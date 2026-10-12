# LifeNest AI

**An AI financial risk advisor for Malaysian first-time home buyers.**
Reads a payslip → computes what a bank would actually lend → prices the
commute cost of the target property → stress-tests the household over a
decade → produces a dated plan to yes when the answer is no.

Hackathon entry: **Google Cloud AI Builder Cup 2026 · BFSI theme**.

---

## The architectural bet

The engine is a pure Python module with **no LLM inside it**. Gemini extracts
fields from documents and narrates verdicts, but never performs arithmetic.
Every currency figure in every user-facing output traces to a logged tool
call — and a guardrail asserts this before the verdict reaches the user.

See [AGENT.md](AGENT.md) for the non-negotiable architecture rules.

---

## Repo layout

```
lifenest-ai/
├── AGENT.md                     ← standing context, paste into every agent session
├── config/                      ← parameters.yaml, credit.yaml, ConfigService
├── engine/                      ← pure Python. no LLM, no I/O, no network.
├── agent/                       ← Gemini orchestration, tool adapters, guardrail
├── api/                         ← FastAPI → Cloud Run
├── web/                         ← Next.js → Firebase Hosting (ticket W-01)
├── jobs/                        ← rate sync (ticket D-04)
├── evals/                       ← golden dataset, synthetic docs, pytest
└── docs/                        ← proposal, build plan, backlog
```

---

## Development

```powershell
# 1. Create the venv (Python 3.13; spec is 3.12 but 3.13 is forward-compatible)
& "C:\Program Files\Python313\python.exe" -m venv venv
.\venv\Scripts\Activate.ps1

# 2. Install
pip install -U pip
pip install -e ".[dev]"

# 3. Test
pytest

# 4. Run the API stub locally
uvicorn api.main:app --reload --port 8080
```

Visit `http://localhost:8080/health` and `http://localhost:8080/engine/demo`.

---

## Config: everything traceable

Financial constants live in `config/*.yaml` with a `source` and `verified_on`
field. Values without a verified Malaysian source are marked
`source: UNVERIFIED`. Ticket **S-03** is the sweep that replaces every
UNVERIFIED entry with a cited value before submission.

Check the current unverified count:

```powershell
python -c "from config.service import ConfigService; print(len(ConfigService.load().unverified_keys()), 'unverified')"
```

Or hit `GET /config/status` on the running API.

---

## What this is not

See [NOT_FINANCIAL_ADVICE.md](NOT_FINANCIAL_ADVICE.md) and
[SYNTHETIC_DATA.md](SYNTHETIC_DATA.md).
