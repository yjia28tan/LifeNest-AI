# LifeNest AI — Build Backlog and Agent Prompts

Feed `AGENTS.md` (Section 2) to your coding agent once as standing context, then give
it one ticket prompt per session. One ticket per session — agents degrade when you
chain unrelated work into a single run.

---

## 1. Repo structure: one repo, not two

**Use a monorepo.** Three reasons, in order of weight:

1. **The submission takes one repository link.** Two repos means a judge follows one
   of them and forms an opinion from half your work. Not worth it for any
   architectural benefit.
2. **The `engine/` and `agent/` separation is your technical argument.** Splitting
   frontend out doesn't hurt that, but splitting the repo means the first thing a
   judge sees is a Next.js app, not a tested domain engine with no LLM in it.
3. **One schema, two consumers.** `ProfileInput` drives the extraction contract, the
   confirm-fields UI and the manual form. Keep it in one place and generate the
   TypeScript types from it.

The counter-argument — different runtimes, different CI — is a build-config problem,
not a repo problem. Cloud Build can deploy `api/` to Cloud Run and `web/` to Firebase
Hosting from a single trigger with path filters.

```
lifenest-ai/                     ← the one public repo
├── AGENTS.md                    ← standing context for your coding agent
├── engine/                      ← pure Python, no LLM, no I/O
├── agent/                       ← Gemini orchestration, tool adapters
├── api/                         ← FastAPI → Cloud Run
├── web/                         ← Next.js → Firebase Hosting
├── jobs/                        ← rate sync
├── config/                      ← parameters.yaml, credit.yaml
├── evals/                       ← golden dataset, synthetic documents
└── cloudbuild.yaml
```

---

## 2. AGENTS.md — paste this at the top of every session

```markdown
# LifeNest AI — standing context

## What this is
An AI financial risk advisor for Malaysian first-time home buyers. It reads a
payslip and computes what a bank would actually lend, prices the commute cost of
where they want to buy, stress-tests the household over a decade, and — when the
answer is no — produces a dated plan to yes.

Hackathon entry: Google Cloud AI Builder Cup 2026, BFSI theme. Public repo.

## Non-negotiable architecture rules
1. `engine/` contains NO LLM calls, NO network I/O, NO file access. Pure functions
   only. If you need to add a calculation, it goes here and it gets a test.
2. Gemini NEVER performs arithmetic. It extracts, decides which tool to call, and
   narrates results. Every currency figure in any user-facing output must trace to
   a logged tool call.
3. NEVER invent a financial constant. Rates, duty scales, DSR ceilings, EPF rules
   and fee tables live in `config/*.yaml`, each with a `source` and a `verified_on`
   field. If a value is missing, add it to config with `source: UNVERIFIED` and
   raise it in your summary — do not guess it inline.
4. Tool adapters in `agent/tools.py` are thin: validate, call engine, log, return.
   No business logic.
5. `AdvisorAgent` is registered with zero tools. This is deliberate — it structurally
   cannot emit an uncomputed number. Do not give it tools.

## Stack
Python 3.12, FastAPI, Google ADK + Gemini via Vertex AI, Firestore, Cloud Storage,
Cloud Run, Next.js + Tailwind on Firebase Hosting, pytest.
Region: asia-southeast1.

## Conventions
- Type hints everywhere. Dataclasses over dicts for domain objects.
- Money as float, rounded only at the presentation boundary.
- Every engine function gets a docstring containing its formula.
- Tests live in `evals/` and run in CI. A red test fails the build.
- No `localStorage`/`sessionStorage` anywhere.
- Malaysian English in user-facing copy. Currency as `RM1,234`.

## Privacy rules — these are architectural, not policy
- Payslips and credit reports are session-scoped. Never write document contents to
  logs. Log the tool call, never the document.
- Never persist a credit report field beyond the session.
- Never attempt to fetch CCRIS or CTOS data. The buyer uploads their own report.
  We are not a credit reporting agency subscriber.

## Before you finish any ticket
- Run `pytest`. Green or explain why not.
- List any value you had to assume, and flag it as UNVERIFIED.
- Do not mark a ticket done if the acceptance criteria are partially met.
```

---

## 3. Backlog

Priority: **P0** the demo dies without it · **P1** materially affects scoring ·
**P2** only if time remains.

### Epic 0 — Foundation

| ID | Ticket | Pri | Depends |
|---|---|---|---|
| F-01 | Repo scaffold, tooling, pre-commit, pytest wiring | P0 | — |
| F-02 | GCP project, APIs enabled, **billing budget alert** | P0 | — |
| F-03 | `config/parameters.yaml` + `credit.yaml` with `source` and `verified_on` | P0 | F-01 |
| F-04 | ConfigService: YAML load, Firestore override merge, version stamp | P0 | F-03 |

F-02 is yours, not the agent's. Do it first.

### Epic 1 — Domain engine

| ID | Ticket | Pri | Depends |
|---|---|---|---|
| E-01 | Port `lifenest_engine.py` into `engine/` modules, config-driven | P0 | F-04 |
| E-02 | Golden dataset: 25 profiles, hand-computed in a spreadsheet first | P0 | E-01 |
| E-03 | Port `lifenest_credit.py` into `engine/credit.py` | P1 | E-01 |
| E-04 | Split `engine/core/` vs `engine/property/` for the vertical story | P2 | E-01 |

### Epic 2 — Intake

| ID | Ticket | Pri | Depends |
|---|---|---|---|
| I-01 | Synthetic payslip generator — 5 layouts, BM and EN, PDF + PNG | P0 | F-01 |
| I-02 | Format normalisation: HEIC→JPEG, DOCX/XLSX→PDF, magic-byte sniffing | P0 | F-01 |
| I-03 | Gemini extraction → `PayslipFields` with per-field confidence | P0 | I-01, I-02 |
| I-04 | Port `lifenest_intake.py`: schema, validation, evidence tier | P0 | I-03 |
| I-05 | Extraction accuracy eval against the labelled synthetic set | P1 | I-03 |
| I-06 | Credit report extraction (CCRIS/CTOS) reusing the same path | P1 | I-03, E-03 |

I-01 first. You cannot build or test extraction without documents to extract from.

### Epic 3 — Agent

| ID | Ticket | Pri | Depends |
|---|---|---|---|
| A-01 | Tool adapters for all 8 tools + audit logger wrapper | P0 | E-01 |
| A-02 | AnalysisAgent: ADK orchestration, function calling | P0 | A-01 |
| A-03 | AdvisorAgent: narration, BM/EN/ZH, **zero tools registered** | P0 | A-02 |
| A-04 | Guardrail: parse figures, assert traced, regenerate once, fallback card | P0 | A-03 |
| A-05 | Guardrail test in CI — adversarial prompts, target zero violations | P1 | A-04 |

### Epic 4 — API and state

| ID | Ticket | Pri | Depends |
|---|---|---|---|
| P-01 | FastAPI: `/session`, `/document`, `/profile`, `/message`, `/scenario`, `/audit` | P0 | A-02 |
| P-02 | Firestore session store + **TTL policy on `expiresAt`** | P0 | P-01 |
| P-03 | GCS signed-URL upload + **lifecycle purge rule** | P0 | P-01 |
| P-04 | Routes API integration with caching and a manual-distance fallback | P1 | E-01 |

### Epic 5 — Web

| ID | Ticket | Pri | Depends |
|---|---|---|---|
| W-01 | Next.js shell, Firebase anonymous auth, chat stream, camera upload | P0 | P-01 |
| W-02 | Confirm-fields screen — doubles as the manual entry form | P0 | I-04, W-01 |
| W-03 | Verdict card + scenario chart with the break month marked | P0 | W-01 |
| W-04 | **"Show the working" audit panel** | P0 | P-01, W-01 |
| W-05 | Evidence tier banner on every verdict | P1 | I-04, W-03 |
| W-06 | i18n: BM / EN / 中文 | P1 | W-03 |
| W-07 | Voice input in Bahasa Malaysia | P1 | W-01 |

W-04 is not optional. It converts your strongest claim into something clickable.

### Epic 6 — Deploy

| ID | Ticket | Pri | Depends |
|---|---|---|---|
| D-01 | Dockerfile + Cloud Run deploy, **do this on day one of the API work** | P0 | P-01 |
| D-02 | Firebase Hosting deploy | P0 | W-01 |
| D-03 | Cloud Build CI: pytest + guardrail test gate deploys | P1 | D-01 |
| D-04 | Rate sync job: Cloud Scheduler → Cloud Run job | P1 | F-04 |
| D-05 | **Min instances = 1 before submission** | P0 | D-01 |

### Epic 7 — Submission

| ID | Ticket | Pri | Depends |
|---|---|---|---|
| S-01 | Eval report: extraction accuracy, computation pass rate, guardrail violations | P1 | I-05, A-05 |
| S-02 | README, SYNTHETIC_DATA.md, NOT_FINANCIAL_ADVICE.md | P0 | — |
| S-03 | **Parameter verification pass** — every value, with dates | P0 | F-03 |
| S-04 | Calibrate the demo profile to match the deck's RM520,000 | P0 | E-01 |
| S-05 | 3-minute video: 20s problem, 100s unbroken live demo, 40s architecture | P0 | all P0 |
| S-06 | Deck → PDF | P0 | — |

---

## 4. Ticket prompts

Paste `AGENTS.md` first, then one of these.

### E-01 — Port the engine

```
Port the attached lifenest_engine.py into a proper package under engine/.

Split into: core.py (monthly_instalment, principal_from_instalment,
outstanding_balance, _tiered), dsr.py, capacity.py, epf.py, costs.py
(stamp duty, legal fees, upfront), location.py, scenarios.py, readiness.py.

Replace the DEFAULT_CONFIG dict with a load from config/parameters.yaml via
ConfigService. Every config key keeps its source and verified_on metadata, and
ConfigService exposes a version string that gets stamped onto every result.

Keep every function pure — no I/O, no network, no LLM. Preserve the formula
docstrings exactly; they are the spec.

Add engine/__init__.py exporting the public surface.

Acceptance:
- python -c "from engine import max_price" works
- every function has a type-hinted signature and its formula in the docstring
- grep -r "import requests\|vertexai\|google.cloud" engine/ returns nothing
- the worked example from the original file reproduces identical numbers
```

### E-02 — Golden dataset

```
Build evals/golden_profiles.json: 25 borrower profiles with hand-computed
expected outputs.

Coverage: net income across all four DSR bands; ages 25/35/45/55 (the maturity
cap must bite on at least two); zero, one and three commitments; single and
joint applicants; one case with no DSR headroom; one cash-constrained and one
income-constrained; commute costs from zero to RM990/month.

Each record: inputs, expected_net_income, expected_dsr, expected_max_instalment,
expected_max_loan, expected_max_price, expected_constrained_by, and a `working`
string showing the arithmetic.

IMPORTANT: compute the expected values independently — from the formulas in the
docstrings — not by running the code under test. If you cannot verify a value by
hand, mark it `"verified": false` and list it in your summary.

Then write evals/test_engine.py asserting each case within 0.5% tolerance.

Acceptance: pytest evals/test_engine.py green; failures name the profile and
the field.
```

### I-01 — Synthetic payslips

```
Build evals/generate_payslips.py producing synthetic Malaysian payslips.

Five visually distinct layouts: a GLC style with a logo header, an SME single-
table style, a statutory-body style with a dense deduction block, a
payroll-software export (think a clean grid), and a handwritten-style scanned
look. At least two in Bahasa Malaysia with BM field labels (Gaji Pokok,
Elaun, Potongan KWSP, PERKESO, SIP, PCB, Gaji Bersih).

Vary: field ordering, fonts, whether allowances are itemised or lumped,
presence of a staff-loan or zakat deduction line, decimal formatting.

Output each as both PDF and PNG, plus a ground-truth JSON with every field.
Generate 40 documents across the five layouts.

Use reportlab or WeasyPrint. All names, employers and figures fabricated — add
a visible "SPECIMEN" watermark and a note in evals/README.md.

Acceptance: 40 PDF + 40 PNG + 40 JSON; opening any three shows obviously
different layouts.
```

### I-03 — Extraction

```
Build agent/intake.py: Gemini multimodal extraction into PayslipFields.

Contract: takes a normalised file (image or PDF bytes + mime), returns
PayslipFields with per-field confidence 0-1. Gemini must return JSON only,
against a fixed schema — no prose, no markdown fences. Parse defensively.

The prompt must handle Bahasa Malaysia labels, itemised vs lumped allowances,
and deductions we do not model (staff loan, zakat, co-op) — those go into an
`unmodelled_deductions` list rather than being silently dropped.

Never guess a field. Missing means None with confidence 0, not an estimate.

Use Vertex AI (not an AI Studio API key). Model via config, default Gemini
Flash.

Acceptance: runs against 5 sample payslips from evals/, prints extracted fields
with confidences; a deliberately blurred input returns low confidence rather
than confident nonsense.
```

### A-01 — Tools and audit

```
Build agent/tools.py and agent/audit.py.

tools.py: one thin adapter per engine function — compute_dsr, loan_capacity,
epf_withdrawal, location_cost, assess_credit_standing, simulate_scenario,
plan_path_to_approval, match_affordable_stock. Each validates its input,
calls the engine, returns a plain dict. No calculation in this file.

audit.py: a decorator wrapping every adapter. Writes to
sessions/{sessionId}/toolCalls/{callId} in Firestore AND emits structured JSON
to Cloud Logging: tool, arguments, result, engineVersion, configVersion,
timestamp, durationMs. Never log document contents or credit report fields.

Also export each tool's Gemini function-declaration schema.

Acceptance: calling any tool writes exactly one Firestore doc and one log line;
a unit test asserts the audit record matches the returned result.
```

### A-04 — Guardrail

```
Build agent/guardrail.py.

check(verdict_text, session_id) -> GuardrailResult

Extract every currency figure from the verdict (RM1,234, RM1,234.56, "RM1.2
million", and bare numbers adjacent to RM). For each, assert a matching value
appears in that session's toolCalls results, within rounding tolerance.

On failure: regenerate the verdict once with an explicit instruction naming the
untraced figures. If it fails again, return a structured fallback card built
directly from tool output with no generated prose. NEVER return an unverified
figure to the user.

Log every violation with the offending figure and the verdict text.

Acceptance: a fabricated verdict containing RM999,999 is caught; a legitimate
verdict passes; both covered by tests.
```

### W-02 — Confirm and manual entry

```
Build the confirm-fields screen in web/. It serves two purposes with one
component: reviewing extracted fields, and manual entry from scratch.

Extracted fields render pre-filled with a provenance chip (extracted /
corrected / declared / derived). Confidence below 0.85 renders highlighted and
focused first. Editing a field flips its source to "corrected".

Manual entry is the same component with an empty state, using the MANUAL_FORM
spec from lifenest_intake.py. Do NOT ask for EPF, SOCSO or EIS — they are
derived from gross and shown read-only with a "calculated" chip.

Commitments are a repeating row group with the select options from the spec.

Render validate_profile() errors as blocking, warnings as inline amber, and
reconfirm fields as highlighted.

Mobile first — a buyer is doing this on a phone. No localStorage; React state
only.

Acceptance: a user can complete a profile with zero uploads; a user can upload
and correct one field and see its chip change; the empty-commitments warning
appears.
```

### W-04 — Show the working

```
Build the audit panel in web/.

A collapsible panel on every verdict listing each tool call in order: tool name,
input arguments, returned values, duration. Currency figures in the verdict text
link to the call that produced them.

Header line: "Every figure below came from a computation, not from the model."

This is the highest-value UI element in the product — it turns the guardrail
from a claim into something a judge can click. Make it look deliberate, not
like a debug dump: monospace for values, readable labels for tool names, a
timestamp and the config version at the foot.

Acceptance: a verdict renders with a working panel; every RM figure in the
verdict maps to a listed call; the panel is legible on a phone.
```

### S-04 — Calibrate the demo

```
The deck claims a bank would approve RM520,000 for the demo buyer. Running the
current sample profile returns a different figure.

Build evals/calibrate_demo.py: solve for a demo profile (basic, allowances,
commitments, savings, EPF balance, commute distance) that produces a max price
within RM5,000 of RM520,000 while staying realistic for a 29-year-old Malaysian
professional — no contrived figures.

Also produce the aspirational RM750,000 comparison and the five scenario
outcomes, so slide 9 matches what the demo actually prints.

Output the final profile as evals/demo_profile.json and print a table of every
figure that appears in the deck alongside what the engine returns.

Acceptance: the printed table shows deck figure vs engine figure with a
matched/mismatched flag, and everything matches.
```

---

## 5. Working rules for agent sessions

**One ticket per session.** Fresh context each time, with `AGENTS.md` re-pasted.

**Never let the agent invent a financial constant.** This is the single highest-risk
failure mode: an agent that helpfully hard-codes a plausible stamp duty rate produces
code that runs, passes tests, and is wrong. The `source: UNVERIFIED` convention exists
so these surface instead of hiding.

**Engine tickets get tests before features.** E-02 is written by hand, from the
formulas, not by running the code under test. An eval harness generated from the
implementation proves nothing.

**Deploy on day one of API work**, not at the end. D-01 before P-02.

**Cut P2 first, then P1, never P0.** If you are behind, drop the automotive split
(E-04), the credit extraction (I-06) and voice (W-07) before you touch the audit
panel or the guardrail.
