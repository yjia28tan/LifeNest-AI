# LifeNest AI — Prototype Build Plan

**Google Cloud AI Builder Cup 2026 · BFSI track**

---

## 0. What the rules actually require

Read these as hard gates. Failing any one of them costs more than any feature gains.

- A **working prototype**, not a mockup. The rules say this explicitly.
- Must use **Google AI models** (Gemini or Gemma) **or** an agentic platform (Agent
  Platform, Antigravity, AI Studio). Doing both is stronger than doing one.
- Must be **deployed on Google Cloud via Cloud Run or Firebase**. A localhost demo is
  a disqualification risk.
- **Public YouTube / Vimeo / Drive video, 3 minutes.**
- **Proposal deck as PDF.**
- Everything in **English**.
- Public repo — which is why this stays separate from your commercial CRM work.

Scoring weights, so you know where to spend the hours: Technical Merit & Gen AI **40%**,
Problem Alignment & Impact **25%**, Innovation & Creativity **25%**, UX & Design **10%**.

---

## 1. Architecture

```
                        ┌──────────────────────────────┐
  Buyer (mobile web)    │  Next.js chat UI             │
  payslip photo ───────▶│  Firebase Hosting + Auth     │
  EPF statement         └───────────┬──────────────────┘
  BM / EN / 中文                    │ HTTPS
                                    ▼
                        ┌──────────────────────────────┐
                        │  FastAPI orchestrator        │
                        │  Cloud Run (scale to zero)   │
                        └───────────┬──────────────────┘
                                    │
                 ┌──────────────────┼──────────────────┐
                 ▼                  ▼                  ▼
        ┌────────────────┐  ┌───────────────┐  ┌──────────────┐
        │ Gemini         │  │ Rules engine  │  │ Firestore    │
        │ (Vertex AI)    │  │ 7 pure funcs  │  │ session +    │
        │ multimodal     │◀▶│ deterministic │  │ audit trail  │
        │ + function     │  │ + config file │  └──────────────┘
        │   calling      │  └───────┬───────┘
        └────────────────┘          │
                                    ▼
                        ┌──────────────────────────────┐
                        │ Routes API (commute cost)    │
                        │ Cloud Storage (docs, purged) │
                        │ Cloud Scheduler (rate sync)  │
                        └──────────────────────────────┘
```

The important structural property: **the rules engine is a plain Python module with no
LLM inside it.** It can be unit-tested, and its outputs are what the model is allowed
to speak. Build it first, before you touch Gemini.

---

## 2. Tech stack

| Layer | Choice | Why this one |
|---|---|---|
| LLM | **Gemini 2.x Flash** via **Vertex AI** | Multimodal document reading, function calling, multilingual output. Flash is fast and cheap enough to iterate; use Pro only if extraction accuracy demands it. Vertex (not the AI Studio key) because "deployed on Google Cloud" is a judging gate. |
| Agent orchestration | **Google ADK** (Agent Development Kit) or Vertex AI Agent Builder | Ticks the "agentic platform" clause explicitly. Gives you named sub-agents (intake, computation, advisor) instead of one function-calling loop, which reads as more deliberate. Only use it if it's genuinely working — a broken agent framework scores worse than a clean function-calling loop. |
| Backend | **Python 3.12 + FastAPI**, containerised | You already work in backend. Deploys to Cloud Run in one command. |
| Rules engine | Plain Python module, pure functions, YAML config | Testable, auditable, and the thing that makes your guardrail claim true. |
| Frontend | **Next.js** on **Firebase Hosting** (or Firebase App Hosting) | Chat UI, camera upload, mobile-first. Malaysians will use this on a phone. |
| Auth | **Firebase Auth**, anonymous sign-in | Zero-friction demo; still gives you a session identity for Firestore. |
| Database | **Firestore** | Session state, extracted profile, scenario results, tool-call audit trail. |
| File storage | **Cloud Storage** with a lifecycle rule | Uploaded documents deleted automatically. Your PDPA claim needs to be architecturally true, not a promise. |
| Location | **Google Maps Platform Routes API** (or Distance Matrix) | Origin → workplace distance and drive time, which feeds `location_cost()`. |
| Rate sync | **Cloud Scheduler → Cloud Run job** | Refreshes the OPR/SBR config. Turns "parameters are configuration" from a claim into a demo. |
| Logging | **Cloud Logging**, structured JSON | Every tool invocation logged with inputs and outputs. This is your audit trail. |
| Tests | pytest + a golden dataset in the repo | Section 10 of the proposal doc. |
| Repo | **GitHub, public** | Required. Keep commercial work out of it. |

**Do not add:** a vector database, RAG, or a fine-tuned model. None of them solve a
problem you have, and each is a thing that can break on demo day. If a judge asks why
there's no RAG, the answer is that your knowledge is a rules table, not a corpus —
and that's the correct answer.

---

## 3. Accounts and subscriptions

| Item | Needed for | Cost note |
|---|---|---|
| **Google Cloud account with billing enabled** | Everything | New accounts normally come with free credits — check the current offer when you sign up. **Ask the organisers first whether GCP credits are provided to participants**; hackathons of this kind often issue them, and it would be silly to pay. |
| **Vertex AI API** enabled | Gemini | Per-token. Flash-tier pricing on a hackathon workload is small, but set a **budget alert on day one** — this is the one line that can surprise you. |
| **Google Maps Platform** enabled + API key | Routes / Distance Matrix | Billed per request, with a monthly free allowance. Cache aggressively: your demo hits maybe a dozen distinct routes. Restrict the key by API and referrer before you push anything public. |
| **Firebase project** (same GCP project) | Hosting, Auth, Firestore | Spark tier is free; you may need Blaze (pay-as-you-go) for some services. Blaze still has free monthly quotas. |
| **GitHub account** | Public repo | Free. |
| **YouTube account** | 3-minute demo video, public or unlisted per the rules | Free. |
| Screen recorder | Demo capture | OBS Studio (free) or macOS built-in. Record at 1080p. |
| Custom domain | Optional | Skip it. `*.web.app` is fine and one less thing to fail. |

**Verify all pricing at signup rather than trusting any figure here — Google Cloud
pricing and free-tier terms change, and this document is not a price list.**

---

## 4. Build plan

Work backwards from your submission deadline. The phases below are sized for roughly
four weeks of part-time work; compress by cutting Phase 6, never Phase 1 or 2.

### Phase 0 — Foundation (half a day)

1. Create the GCP project. Enable: Vertex AI, Cloud Run, Cloud Build, Firestore,
   Cloud Storage, Cloud Scheduler, Maps Platform.
2. **Set a billing budget alert.** Do this before you write any code.
3. Create the public GitHub repo. Add the licence, a README stub, and a `.gitignore`
   that excludes credentials.
4. Add `SYNTHETIC_DATA.md` stating that every document in the repo is fabricated.

### Phase 1 — The rules engine, alone (3–4 days) · *do this first*

No LLM. No frontend. Just Python and tests.

1. `config/parameters.yaml` — every value from Section 5.1 of the proposal doc, each
   with a `source` and `verified_on` field.
2. Implement, as pure functions:
   `compute_dsr`, `loan_capacity`, `epf_withdrawal`, `location_cost`,
   `simulate_scenario`, `plan_path_to_approval`, `match_affordable_stock`.
3. Build the golden dataset: 20–30 borrower profiles with hand-computed expected
   outputs. **Compute these by hand in a spreadsheet, not with the code you're
   testing.**
4. `pytest` green across the whole set.

You now have the thing that makes your central claim true. Everything after this is
interface.

### Phase 2 — Document extraction (2–3 days)

1. Generate synthetic payslips in at least four visually distinct layouts, plus EPF
   statements. Vary fonts, logos, field ordering, and language.
2. Prompt Gemini for **structured JSON output only** against a fixed schema.
3. Hand-label ground truth. Measure field-level accuracy. Iterate the prompt until
   the number stops improving.
4. Build the **confirmation step**: show extracted fields to the user, let them
   correct. This is both an accuracy backstop and a UX point.

### Phase 3 — Orchestration and the guardrail (3–4 days)

1. Register the seven tools with Gemini function calling (or as ADK agent tools).
2. **Write the audit logger before the conversation logic.** Every tool call emits a
   structured record: tool name, arguments, return value, timestamp, session ID.
3. Add the guardrail test: parse the generated verdict for currency figures, assert
   each one appears in the session's tool-call log. Run it in CI.
4. Verdict generation prompt, in BM / EN / 中文.

### Phase 4 — Frontend and deployment (3–4 days)

1. Next.js chat: camera upload, message stream, verdict card, scenario chart.
2. **Build the "show the working" panel.** A collapsible list of every tool call with
   its inputs and outputs. This is your highest-value UI element — it converts your
   strongest technical claim from an assertion into something a judge can see and
   click. Do not skip it under time pressure.
3. Containerise the API, deploy to Cloud Run. Deploy the frontend to Firebase Hosting.
4. **Get the public URL working early.** Deploy on day one of this phase, not the
   last. Deployment problems always take longer than expected.
5. Cloud Storage lifecycle rule: purge uploads after the session.

### Phase 5 — The things that win points (2–3 days)

In rough order of return:

1. **Voice input in Bahasa Malaysia.** Gemini handles audio natively. A few hours of
   work; owns the 10% UX and accessibility criterion outright.
2. **Rate-sync job.** Cloud Scheduler triggers a Cloud Run job that updates the
   OPR/SBR config. Demo it by changing the rate and showing every downstream number
   move.
3. **Practitioner validation.** Get two or three sentences on camera from the property
   agents you consult for: how many weeks they lose per failed application, how often
   a buyer's stated budget turns out to be fiction. Thirty seconds of a real
   practitioner beats another statistic, and almost no competing team will have one.
4. **Eval results on a slide.** Extraction accuracy, computation pass rate, guardrail
   violations (zero).

### Phase 6 — Polish, only if time remains

- Overhang-by-state risk annotation from NAPIC data.
- Joint applicant modelling in `plan_path_to_approval()` — high real-world value, and
  a good answer to "what if the buyer can't fix their DSR alone?"
- Agency-side view.

### Phase 7 — Submission (2 days, and do not compress this)

1. **Video, 3 minutes.** Structure: 20s problem with one statistic · **100s unbroken
   live screen recording of the deployed URL** · 40s architecture, guardrail, and
   scalability. Real recording only — no slideware, no narrated mockup. Judges can
   tell.
2. **Time the demo against the "ninety seconds" claim on slide 6.** If the real flow
   takes four minutes, change the slide, not the truth.
3. Export the deck to PDF.
4. README: problem, architecture diagram, how to run, the synthetic-data statement,
   the not-financial-advice statement, and the parameter verification dates.
5. **Re-verify every financial parameter and every statistic**, and stamp the
   verification date on the relevant slides.
6. Test the deployed URL from a phone, on mobile data, from a cold start. Cloud Run
   scale-to-zero means the first request after idle is slow — either accept it and
   warm it before the demo, or set minimum instances to 1 for judging week.

---

## 5. Repository structure

```
lifenest-ai/
├── README.md
├── SYNTHETIC_DATA.md
├── config/
│   └── parameters.yaml          # every rate, rule and fee, with sources + dates
├── engine/                      # NO LLM CODE IN HERE
│   ├── dsr.py
│   ├── capacity.py
│   ├── epf.py
│   ├── location.py
│   ├── scenarios.py
│   └── readiness.py
├── agent/
│   ├── extract.py               # Gemini multimodal → schema
│   ├── tools.py                 # tool registration
│   ├── orchestrator.py
│   └── audit.py                 # tool-call logging
├── api/                         # FastAPI → Cloud Run
├── web/                         # Next.js → Firebase Hosting
├── evals/
│   ├── golden_profiles.json     # 20–30 hand-computed cases
│   ├── payslips/                # synthetic, 4+ layouts
│   └── test_*.py
└── docs/
    └── architecture.md
```

The `engine/` and `agent/` split is not cosmetic. It is the physical form of your
central claim, and a judge who opens the repo should be able to see it in five
seconds.

---

## 6. Rules for yourself while building

**Ship the readiness plan in the prototype, not the roadmap.** It is your most
memorable feature. Most teams put their best idea in the "next 6 months" column and
lose the point entirely.

**The deck currently promises more than an unfinished build will show.** That gap is
the main risk you're carrying. Every hour spent making the demo match the deck is
worth more than an hour spent improving the deck.

**Location stays out of the UI as a map.** It enters as a number in the cashflow. The
moment there's a map with pins, you look like a portal and you've thrown away the
differentiation on slide 10.

**Cut features before you cut the deployment buffer.** A brilliant prototype that
404s during judging scores zero on a 40% criterion.
