"""End-to-end benchmark: upload -> Gemini extraction -> review -> verdict, in EN, BM and 中文.

Measures, per assessment:
  * latency from the first upload to the verdict reply (median and p95);
  * number provenance - every RM amount and % in each reply checked by the app's own grounding check;
  * language - whether each reply comes back in the language it was asked in;
  * tokens and cost per assessment (local mode only; remote mode cannot see token counts).

Review is simulated: the "buyer" corrects any field Gemini got wrong using the ground truth, instantly, so the
latency excludes human reading time. The number of corrections is reported as its own metric.

Modes:
  local  - runs the FastAPI app in-process with your .env (set LLM_BACKEND=vertex for real Gemini). Memory DB and
           local file storage are forced, so it never touches Firestore or your bucket.
  remote - calls the deployed Cloud Run API like the web app does (Firebase anonymous sign-in, signed GCS upload).
           Latency includes real network and Cloud Run; tokens are not visible.

Usage:
    python -m benchmarks.generate_docs
    python -m benchmarks.bench_e2e --mode local --runs 9
    python -m benchmarks.bench_e2e --mode remote --runs 9
    python -m benchmarks.bench_e2e --mode local --persona-set repo     # offline smoke test with LLM_BACKEND=fake
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.agent.grounding import extract_figures
from app.agent.orchestrator import (
    APPROVAL_REPLY,
    LLM_FAILURE_REPLY,
    STEP_LIMIT_REPLY,
    TIMEOUT_REPLY,
    UNGROUNDED_REPLY,
)
from benchmarks.common import (
    DEFAULT_DATA_DIR,
    ROOT,
    TokenMeter,
    banner,
    cost_usd,
    same_money,
    summarise_seconds,
    to_decimal,
    write_result,
)

FIXED_REPLIES = {APPROVAL_REPLY, LLM_FAILURE_REPLY, STEP_LIMIT_REPLY, TIMEOUT_REPLY, UNGROUNDED_REPLY}
QUESTIONS = {
    "en": ["I'm looking at a RM600,000 condo in Cheras. Can I afford it?",
           "What if we have a child in three years?"],
    "ms": ["Saya sedang melihat kondominium RM600,000 di Cheras. Mampukah saya membelinya?",
           "Bagaimana jika kami dapat anak dalam tiga tahun lagi?"],
    "zh": ["我在看蕉赖（Cheras）一间 RM600,000 的公寓，我买得起吗？",
           "如果我们三年后生孩子会怎样？"],
}
_MS_WORDS = {"anda", "yang", "dan", "untuk", "boleh", "dengan", "tidak", "pinjaman", "bulanan", "harga", "rumah",
             "ialah", "ini", "itu", "akan", "dalam", "jika", "mampu", "bayaran", "pendapatan", "anak"}
_EN_WORDS = {"the", "you", "your", "and", "can", "with", "is", "for", "monthly", "loan", "price", "would", "this",
             "that", "if", "income", "afford", "child"}


def detect_language(text: str) -> str:
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    letters = len(re.findall(r"[A-Za-z\u4e00-\u9fff]", text)) or 1
    if cjk / letters > 0.15:
        return "zh"
    words = re.findall(r"[a-z]+", text.lower())
    ms, en = sum(w in _MS_WORDS for w in words), sum(w in _EN_WORDS for w in words)
    return "ms" if ms > en else "en"


# --- personas -------------------------------------------------------------------------------------------------------

def load_personas(persona_set: str, data_dir: Path) -> list[list[dict[str, Any]]]:
    """Each persona is a list of {path, kind, content_type, fields, commitments} documents."""
    if persona_set == "repo":
        docs_dir = ROOT / "data" / "mock_documents"
        fixtures = json.loads((docs_dir / "extractions.json").read_text(encoding="utf-8"))["documents"]
        persona = []
        for name in ("aina_payslip.png", "aina_epf.png", "aina_loan_statements.png"):
            fx = fixtures[name]
            fields = {f["path"]: f["value"] for f in fx["fields"] if f["path"] != "commitments"}
            loans = next((f["value"] for f in fx["fields"] if f["path"] == "commitments"), [])
            persona.append({"path": docs_dir / name, "kind": fx["kind_detected"], "content_type": "image/png",
                            "fields": fields, "commitments": loans})
        return [persona]
    truth = json.loads((data_dir / "ground_truth.json").read_text(encoding="utf-8"))
    personas = []
    for p in truth["personas"].values():
        personas.append([{"path": data_dir / n, **truth["documents"][n]} for n in p["documents"]])
    return personas


def corrections_for(doc: dict[str, Any], proposed: dict[str, Any]) -> dict[str, Any]:
    """What a careful buyer would fix in the review panel."""
    fixes: dict[str, Any] = {}
    for path, want in doc["fields"].items():
        got = proposed.get(path)
        if path == "person.age":
            if got is None or to_decimal(got) is None or int(to_decimal(got)) != int(want):
                fixes[path] = int(want)
        elif got is None:
            if to_decimal(want) != 0:
                fixes[path] = str(want)
        elif not same_money(got, want):
            fixes[path] = str(want)
    want_loans = doc.get("commitments") or []
    if want_loans:
        got_loans = proposed.get("commitments") or []
        key = lambda c: (str(to_decimal(c.get("monthly"))), c.get("remaining_months"))  # noqa: E731
        if sorted(map(key, got_loans)) != sorted(map(key, want_loans)):
            fixes["commitments"] = [{"type": c["type"], "monthly": str(c["monthly"]),
                                     "remaining_months": c["remaining_months"]} for c in want_loans]
    return fixes


# --- clients --------------------------------------------------------------------------------------------------------

class LocalApi:
    def __init__(self) -> None:
        from fastapi.testclient import TestClient

        from app.config import Settings
        from app.main import create_app

        self.settings = Settings(app_env="dev", log_level="WARNING", auth_backend="dev", db_backend="memory",
                                 storage_backend="local", local_upload_dir=Path(tempfile.mkdtemp()),
                                 rate_limit_per_minute=10_000)
        self.client = TestClient(create_app(self.settings))
        self.client.__enter__()
        self.label = f"local in-process, LLM_BACKEND={self.settings.llm_backend}, model={self.settings.gemini_model}"
        self._n = 0

    def new_user(self) -> dict[str, str]:
        self._n += 1
        return {"Authorization": f"Bearer dev:bench{self._n}"}

    def request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        return self.client.request(method, url, **kw)

    def upload(self, upload_url: str, data: bytes, content_type: str) -> httpx.Response:
        u = urlparse(upload_url)
        return self.client.put(f"{u.path}?{u.query}", content=data, headers={"Content-Type": content_type})

    def close(self) -> None:
        self.client.__exit__(None, None, None)


class RemoteApi:
    def __init__(self, base_url: str, api_key: str, referer: str) -> None:
        self.base = base_url.rstrip("/")
        self.api_key, self.referer = api_key, referer
        self.client = httpx.Client(timeout=180)
        self.label = f"remote {self.base}"

    def new_user(self) -> dict[str, str]:
        r = self.client.post(f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={self.api_key}",
                             json={"returnSecureToken": True}, headers={"Referer": self.referer})
        r.raise_for_status()
        return {"Authorization": f"Bearer {r.json()['idToken']}", "Origin": self.referer.rstrip("/")}

    def request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        return self.client.request(method, self.base + url, **kw)

    def upload(self, upload_url: str, data: bytes, content_type: str) -> httpx.Response:
        return self.client.put(upload_url, content=data, headers={"Content-Type": content_type})

    def close(self) -> None:
        self.client.close()


def web_config() -> tuple[str, str]:
    text = (ROOT / "web" / "config.js").read_text(encoding="utf-8")
    api = re.search(r'"(https://[^"]+run\.app)"', text)
    key = re.search(r'apiKey:\s*"([^"]+)"', text)
    return (api.group(1) if api else ""), (key.group(1) if key else "")


# --- one assessment -------------------------------------------------------------------------------------------------

def run_assessment(api: Any, persona: list[dict[str, Any]], locale: str, turns: int,
                   meter: TokenMeter | None) -> dict[str, Any]:
    headers = api.new_user()
    out: dict[str, Any] = {"locale": locale, "documents": [d["path"].name for d in persona], "corrections": 0,
                           "replies": [], "error": None}
    tokens_before = meter.snapshot() if meter else None

    start = time.perf_counter()
    sid = api.request("POST", "/sessions", headers=headers, json={"locale": locale}).json()["session_id"]
    try:
        for doc in persona:
            t0 = time.perf_counter()
            up = api.request("POST", f"/sessions/{sid}/documents/upload-url", headers=headers,
                             json={"kind": doc["kind"], "content_type": doc["content_type"]})
            up.raise_for_status()
            body = up.json()
            put = api.upload(body["upload_url"], doc["path"].read_bytes(), doc["content_type"])
            if put.status_code >= 300:
                raise RuntimeError(f"upload failed {put.status_code}: {put.text[:120]}")
            ex = api.request("POST", f"/sessions/{sid}/documents/{body['doc_id']}/extract", headers=headers)
            out.setdefault("extract_seconds", []).append(round(time.perf_counter() - t0, 2))
            if ex.status_code != 200:
                raise RuntimeError(f"{doc['path'].name}: extract {ex.status_code} {ex.json().get('error', {})}")
            proposed = {f["path"]: f["value"] for f in ex.json()["fields"]}
            fixes = corrections_for(doc, proposed)
            out["corrections"] += len(fixes)
            conf = api.request("POST", f"/actions/{ex.json()['action_id']}/confirm", headers=headers,
                               json={"corrections": fixes})
            conf.raise_for_status()
        out["profile_status"] = conf.json()["profile_status"]
        out["documents_ready_seconds"] = round(time.perf_counter() - start, 2)
        tokens_docs = meter.snapshot() if meter else None

        for i, question in enumerate(QUESTIONS[locale][:turns]):
            t0 = time.perf_counter()
            r = api.request("POST", f"/sessions/{sid}/messages", headers=headers, json={"message": question})
            r.raise_for_status()
            body = r.json()
            elapsed = time.perf_counter() - t0
            if i == 0:
                out["verdict_turn_seconds"] = round(elapsed, 2)
                out["end_to_end_seconds"] = round(time.perf_counter() - start, 2)
                if meter:
                    out["tokens_assessment"] = TokenMeter.diff(meter.snapshot(), tokens_before)
                    out["tokens_extraction"] = TokenMeter.diff(tokens_docs, tokens_before)
            reply = body["reply"]
            figures = [f.text for f in extract_figures(reply)]
            out["replies"].append({
                "question": question, "seconds": round(elapsed, 2), "reply": reply,
                "grounding": body["grounding"]["status"], "unmatched": body["grounding"]["unmatched"],
                "figures": figures, "fixed_reply": reply in FIXED_REPLIES,
                "language": detect_language(reply), "tools": [t["tool"] for t in body["tool_trace"]],
            })
        if meter:
            out["tokens_total"] = TokenMeter.diff(meter.snapshot(), tokens_before)
    except Exception as exc:  # noqa: BLE001 - recorded and reported, the run continues
        out["error"] = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        api.request("DELETE", f"/sessions/{sid}", headers=headers)
    return out


# --- main -----------------------------------------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["local", "remote"], default="local")
    parser.add_argument("--runs", type=int, default=9, help="assessments to time; locales rotate EN, BM, 中文")
    parser.add_argument("--warmup", type=int, default=1, help="untimed runs first (Cloud Run cold start)")
    parser.add_argument("--turns", type=int, default=2, choices=[1, 2], help="verdict only, or verdict + follow-up")
    parser.add_argument("--persona-set", choices=["benchmark", "repo"], default="benchmark")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--api", help="remote: Cloud Run URL (default: from web/config.js)")
    parser.add_argument("--api-key", help="remote: Firebase web API key (default: from web/config.js)")
    parser.add_argument("--referer", default="https://lifenest-ai.web.app/")
    parser.add_argument("--price-in", type=float, help="USD per 1M input tokens for your GEMINI_MODEL")
    parser.add_argument("--price-out", type=float, help="USD per 1M output tokens (thinking billed as output)")
    parser.add_argument("--usd-myr", type=float, default=4.2)
    args = parser.parse_args()

    meter: TokenMeter | None = None
    if args.mode == "local":
        try:
            meter = TokenMeter().install()
        except ImportError:
            meter = None
        api: Any = LocalApi()
        if api.settings.llm_backend != "vertex":
            print("NOTE: LLM_BACKEND is not 'vertex' - this measures the offline fake, not Gemini. "
                  "Use --persona-set repo for a smoke test, or set LLM_BACKEND=vertex in .env.")
    else:
        default_api, default_key = web_config()
        api = RemoteApi(args.api or default_api, args.api_key or default_key, args.referer)

    personas = load_personas(args.persona_set, args.data)
    locales = ["en", "ms", "zh"]
    results = []
    try:
        for i in range(args.warmup + args.runs):
            locale = locales[i % 3] if i >= args.warmup else "en"
            r = run_assessment(api, personas[i % len(personas)], locale, args.turns, meter)
            tag = "warm-up" if i < args.warmup else f"run {i - args.warmup + 1}/{args.runs}"
            print(f"{tag:<12} {locale}  e2e={r.get('end_to_end_seconds')}s  corrections={r['corrections']}  "
                  f"grounding={[x['grounding'] for x in r['replies']]}  lang={[x['language'] for x in r['replies']]}"
                  + (f"  ERROR {r['error']}" if r["error"] else ""))
            if i >= args.warmup:
                results.append(r)
    finally:
        api.close()

    ok_runs = [r for r in results if not r["error"] and "end_to_end_seconds" in r]
    e2e = summarise_seconds([r["end_to_end_seconds"] for r in ok_runs])
    verdict = summarise_seconds([r["verdict_turn_seconds"] for r in ok_runs])
    docs = summarise_seconds([r["documents_ready_seconds"] for r in ok_runs])

    replies = [x for r in ok_runs for x in r["replies"]]
    all_figures = sum(len(x["figures"]) + (len(x["unmatched"]) if x["grounding"] == "withheld" else 0)
                      for x in replies)
    untraced = sum(len(x["unmatched"]) for x in replies)
    grounded_replies = sum(x["grounding"] == "ok" for x in replies)
    by_locale: dict[str, list[bool]] = {}
    for r in ok_runs:
        for x in r["replies"]:
            if not x["fixed_reply"]:
                by_locale.setdefault(r["locale"], []).append(x["language"] == r["locale"])

    banner(f"End-to-end - {api.label}")
    print(f"assessments   : {len(ok_runs)} completed / {len(results)} timed ({args.warmup} warm-up excluded)")
    print(f"upload->verdict (s): median {e2e.get('median')}  p95 {e2e.get('p95')}  (min {e2e.get('min')}, "
          f"max {e2e.get('max')})")
    print(f"  documents ready (3 extractions + review): median {docs.get('median')}  p95 {docs.get('p95')}")
    print(f"  verdict turn                            : median {verdict.get('median')}  p95 {verdict.get('p95')}")
    print(f"review corrections per assessment: "
          f"{sum(r['corrections'] for r in ok_runs) / max(len(ok_runs), 1):.2f} fields on average")
    print(f"provenance    : {all_figures - untraced} / {all_figures} RM and % figures traced to a tool, source or "
          f"the user; {grounded_replies}/{len(replies)} replies passed the grounding check")
    lang_ok = {k: f"{sum(v)}/{len(v)}" for k, v in by_locale.items()}
    print(f"language      : {lang_ok}  (reply language matches the question's)")

    cost_line = ""
    assess_tokens = [r["tokens_assessment"] for r in ok_runs if r.get("tokens_assessment")]
    if assess_tokens:
        avg = {k: round(sum(t[k] for t in assess_tokens) / len(assess_tokens)) for k in assess_tokens[0]}
        print(f"tokens per assessment (avg, 3 docs + verdict): in {avg['prompt']}, out {avg['output']}, "
              f"thinking {avg['thoughts']}, Gemini calls {avg['calls']}")
        usd = cost_usd(avg, args.price_in, args.price_out)
        if usd is not None:
            cost_line = f"RM {usd * args.usd_myr:.3f} (USD {usd:.4f})"
            print(f"cost per assessment: {cost_line}  (Gemini only; Cloud Run/Firestore at this volume ~RM0)")
        else:
            print("cost: pass --price-in and --price-out (USD per 1M tokens for your model) to convert tokens to RM")
    for r in results:
        if r["error"]:
            print(f"  FAILED run ({r['locale']}, {r['documents'][0]}): {r['error']}")

    path = write_result(f"e2e_{args.mode}", {
        "label": api.label, "runs": results, "end_to_end_s": e2e, "documents_ready_s": docs, "verdict_turn_s": verdict,
        "figures_total": all_figures, "figures_untraced": untraced, "replies": len(replies),
        "replies_grounded": grounded_replies, "language": lang_ok, "cost": cost_line,
    })
    print("\nSlide 9 ->")
    print(f"  End-to-end latency : {e2e.get('median')} s / {e2e.get('p95')} s  (median / p95, n={e2e.get('n')})")
    print(f"  Number provenance  : {100 * (all_figures - untraced) / max(all_figures, 1):.0f}% "
          f"({all_figures - untraced}/{all_figures} figures)")
    print(f"  Language coverage  : {sum(1 for v in by_locale.values() if v and all(v))} / 3 languages, all replies")
    if cost_line:
        print(f"  Cost per assessment: ~{cost_line.split(' (')[0]}")
    print(f"details (includes every reply, for spot-checking): {path}")


if __name__ == "__main__":
    main()
