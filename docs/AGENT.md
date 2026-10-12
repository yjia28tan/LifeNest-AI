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