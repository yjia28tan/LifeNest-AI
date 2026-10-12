"""Static guarantees for the web client: no HTML injection sinks, no number parsing from reply text, complete i18n."""

from __future__ import annotations

import json
import re

from app.config import ROOT

WEB = ROOT / "web"


def _js() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in WEB.glob("*.js"))


def test_no_html_injection_sinks() -> None:
    js = _js()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert sink not in js, sink


def test_reply_text_is_never_parsed_for_numbers() -> None:
    js = _js()
    assert not re.search(r"reply\s*\.\s*(match|replace|split|exec)", js)
    assert not re.search(r"(parseFloat|Number|parseInt)\(\s*[\w.]*reply", js)


def test_csp_is_strict() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    csp = re.search(r'Content-Security-Policy"\s+content="([^"]+)"', html).group(1)
    assert "default-src 'self'" in csp and "object-src 'none'" in csp and "frame-src 'none'" in csp
    assert "'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp
    assert "<script>" not in html and "style=" not in html


def test_disclaimer_is_always_on_screen() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert '<footer class="disclaimer" data-i18n="disclaimer">' in html
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    assert re.search(r"\.disclaimer\s*{[^}]*position:\s*fixed", css)


def test_every_language_has_every_string() -> None:
    strings = json.loads((WEB / "i18n.json").read_text(encoding="utf-8"))
    assert set(strings) == {"en", "ms", "zh"}
    keys = set(strings["en"])
    for lang, table in strings.items():
        assert set(table) == keys, (lang, keys ^ set(table))
        assert all(v.strip() for v in table.values()), lang
    assert "lending decision" in strings["en"]["disclaimer"]


def test_ui_keys_used_in_code_exist() -> None:
    strings = json.loads((WEB / "i18n.json").read_text(encoding="utf-8"))["en"]
    html = (WEB / "index.html").read_text(encoding="utf-8")
    used = set(re.findall(r'data-i18n="([\w.]+)"', html)) | set(re.findall(r'\bt\("([\w.]+)"', _js()))
    assert used - set(strings) == set()


def test_card_and_field_vocabularies_are_translated() -> None:
    strings = json.loads((WEB / "i18n.json").read_text(encoding="utf-8"))["en"]
    from app.agent.state import FIELD_VALIDATORS
    from app.engine.path_to_approval import PlanAction
    from app.engine.scenarios import ScenarioName

    assert {f"field_{p}" for p in FIELD_VALIDATORS} <= set(strings)
    assert {f"sc_{s}" for s in ScenarioName.__args__} <= set(strings)
    actions = PlanAction.model_fields["action"].annotation.__args__
    assert {f"act_{a}" for a in actions} <= set(strings)
    assert {f"lf_{f}" for f in ("DSR", "TENURE_AGE", "CASH_UPFRONT", "MARGIN_OF_FINANCE")} <= set(strings)
