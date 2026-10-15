# LifeNest AI

**An AI financial risk advisor for first-time home buyers in Malaysia.** A buyer photographs their payslip and EPF
statement in chat. Gemini reads them multimodally, the buyer checks what was read, deterministic tools compute what a
bank would actually lend, life events stress-test the next decade, and the buyer gets a plain-language verdict — plus
a dated plan to approval when the answer is no. Every figure comes from a tool, never from the model.

Entry for the **Google Cloud AI Builder Cup 2026 · BFSI track (JAPAC)**.

> Indicative only. Not a loan offer, not a lending decision. All documents, profiles and listings in this repository
> are synthetic — no real payslip, person or account appears anywhere. See
> [NOT_FINANCIAL_ADVICE.md](NOT_FINANCIAL_ADVICE.md) and [SYNTHETIC_DATA.md](SYNTHETIC_DATA.md).

---

## Architecture

```
Firebase Hosting (web/: chat UI, review panel, verdict & scenario cards)
   │ HTTPS + Firebase ID token
   ▼
Cloud Run — FastAPI (app/)
   ├─ middleware: request id, structured JSON logs, per-user rate limit
   ├─ auth → RequestContext(user_id from the verified token only)
   ├─ review gate: Gemini extraction → user confirms/corrects → profile CONFIRMED
   ├─ agent loop ──► Vertex AI Gemini (function calling)
   │     └─ tools ──► app/engine/  pure Python over config/rules_my_2026_07.yaml (no LLM, ever)
   │               └─ app/kb/      regulation search (Firestore vector search)
   ├─ grounding check: every RM / % in the reply must trace to a tool, a source or the user
   ├─ Firestore: sessions, profiles, messages, audit_logs, pending_actions, property_stock, kb_chunks
   └─ Cloud Storage: uploaded documents (signed URLs, 24h lifecycle, explicit purge)
            │
        Cloud Logging ◄── JSON from everything above (no document text, no income figures)
```

| Google Cloud service | Role |
|---|---|
| **Vertex AI Gemini** | Multimodal payslip/EPF extraction (as a *proposal*), function-calling orchestration, replies in EN/BM/中文 |
| **Vertex AI text embeddings** | Embeds the regulation knowledge base and each query |
| **Firestore (Native)** | The only database: sessions, profiles with field provenance, chat history, audit trail, review actions, property stock; vector search over `kb_chunks`; TTL policies on `expires_at` |
| **Cloud Storage** | Session-scoped documents via signed PUT URLs; 24-hour lifecycle delete; purged on request |
| **Cloud Run** | The API, scales to zero; signs URLs through IAM with its own identity (no key files) |
| **Firebase Hosting + Auth** | The web client; anonymous sign-in issues the ID token the API verifies |
| **Cloud Logging** | Structured JSON with request ids, tool latency and token counts — never prompts or document content |

## Repository layout

```
app/engine/      deterministic engine: DSR, loan capacity, EPF, upfront costs, scenarios, path to approval, stock
app/agent/       orchestrator loop, tool registry, review gate, grounding check, prompts, cards
app/llm/         Gemini gateway + extractor, FakeLLM / FakeExtractor and the offline demo planner
app/kb/          chunking, embedders, Firestore and in-memory vector stores
app/storage/     repository (authz, expiry, purge, scrubbing), Firestore + memory adapters, document stores
app/routers/     sessions, documents, actions, chat, privacy
config/          rules_my_2026_07.yaml — every financial parameter, versioned
data/            property stock, demo profiles, synthetic documents, KB embeddings
knowledge/       regulation markdown for the knowledge base
scripts/         generate_mock_documents, ingest_kb, seed_firestore, demo_chat
web/             front end (plain ES modules, no build step)
tests/           288 tests; 278 run with no cloud credentials, 10 more against the Firestore emulator
```

Configuration comes from environment variables, `.env` (non-secret) and `secrets.env` (secrets); both files are
gitignored and documented in [.env.example](.env.example). Internal working documents live in a local, untracked
`docs/` folder.

---

## Quickstart — no cloud account

**With Docker** (Firestore emulator + API + web):

```bash
docker compose up --build
# open http://localhost:5000 → "Load demo profile" → ask the suggested questions
```

**Without Docker** (in-memory database; Python 3.11+):

```powershell
python -m venv venv; .\venv\Scripts\Activate.ps1
pip install -e ".[dev,gcp]"
python scripts/generate_mock_documents.py   # synthetic payslips into data/mock_documents (already committed)
python scripts/ingest_kb.py                 # offline KB embeddings (already committed)
pytest                                      # zero cloud credentials
uvicorn app.main:app --reload               # API on :8000, seeds demo data in memory
python -m http.server 5000 --directory web  # UI → open http://localhost:5000
```

Then either click **Load demo profile** (Aina, Kumar or Mei) or upload `data/mock_documents/aina_payslip.png`,
`aina_epf.png` and `aina_loan_statements.png`, review and confirm the fields, and run the journey:
*"I'm looking at a RM750,000 condo in Cheras, can I afford it?"* → *"Why so much lower?"* → *"How much can I take from
EPF?"* → *"What if we have a child in three years?"* → *"What would I need to do to reach RM650,000?"* → *"Show me what I
can actually afford near Kajang."* The same journey runs headless with `python scripts/demo_chat.py`.

With no Gemini key, `LLM_BACKEND=fake` uses `FakeExtractor` (recognises the synthetic documents) and an offline planner
that chooses tools by keyword and writes replies only from tool outputs — so the grounding check applies unchanged.

## Running with real Gemini (Vertex AI) locally

No API key is involved: the app calls Gemini through Vertex AI with a Google identity.

1. **Project.** A Google Cloud project with billing enabled (`lifenest-ai` here). Enable the *Vertex AI API*
   (`aiplatform.googleapis.com`) in *APIs & Services*.
2. **Identity.** Either run `gcloud auth application-default login`, or use a service-account key kept **outside the
   repo** (for example `%USERPROFILE%\.lifenest\gcp-key.json`). Grant that identity the **Vertex AI User**
   (`roles/aiplatform.user`) role in *IAM & Admin → IAM* — newer consoles list it as **Agent Platform User** — plus
   **Cloud Datastore User** (`roles/datastore.user`) if it will use Firestore.
3. **`secrets.env`** (gitignored) — only for a key file:
   ```
   GOOGLE_APPLICATION_CREDENTIALS=C:\Users\<you>\.lifenest\gcp-key.json
   ```
4. **`.env`** (gitignored) — copy [.env.example](.env.example) and set:
   ```
   GOOGLE_CLOUD_PROJECT=lifenest-ai
   GOOGLE_CLOUD_LOCATION=asia-southeast1
   LLM_BACKEND=vertex
   GEMINI_MODEL=<a Gemini model enabled in your region>
   EMBEDDING_MODEL=text-embedding-005
   KB_EMBEDDINGS_PATH=data/kb_embeddings_vertex.json
   GROUNDING_CHECK=enforce
   ```
5. **Embed the knowledge base with Vertex** (writes the gitignored `data/kb_embeddings_vertex.json`; the committed
   offline file is untouched): `python scripts/ingest_kb.py`
6. **Run the journey headless:** `python scripts/demo_chat.py` — every reply is grounding-checked.
7. **Run the UI:** `uvicorn app.main:app --reload` and `python -m http.server 5000 --directory web`, then open
   `http://localhost:5000` and upload `data/mock_documents/aina_payslip.png` — Gemini reads it, you review it.

Switch back to the offline stack at any time with `LLM_BACKEND=fake` and `KB_EMBEDDINGS_PATH=data/kb_embeddings.json`.

## Quickstart — Google Cloud

Prerequisites: a project with billing, `gcloud` and `firebase` CLIs, `gcloud auth login` and
`gcloud auth application-default login`.

```bash
PROJECT_ID=lifenest-ai FIRESTORE_DATABASE=lifenest-db REGION=asia-southeast1 \
   GEMINI_MODEL=gemini-3.5-flash EMBEDDING_MODEL=text-embedding-005 ./deploy.sh
```

[deploy.sh](deploy.sh) is idempotent. It enables the APIs, creates Firestore with TTL policies and indexes (including
the `kb_chunks` vector index), a private bucket with a 24-hour lifecycle and browser CORS, a least-privilege runtime
service account, builds the image, seeds stock, demo profiles and the Vertex-embedded KB, and deploys Cloud Run with
`APP_ENV=prod` — which refuses dev auth and the in-memory database at startup. It then prints the three steps for
Firebase Hosting (enable Anonymous sign-in, set `web/config.js`, `firebase deploy`). The same code runs locally and in
the cloud; only environment variables change ([.env.example](.env.example)).

### Automatic deployment from `main`

`.github/workflows/deploy-main.yml` runs Ruff and pytest, then builds and deploys Cloud Run and Firebase Hosting on
each push to `main`. It uses GitHub OIDC Workload Identity Federation; do not store a service-account key in GitHub.
Create a pool/provider restricted to `yjia28tan/LifeNest-AI` and `refs/heads/main`, create a deployment service
account, grant it Cloud Build Editor, Artifact Registry Writer, Cloud Run Admin, Datastore User, Vertex AI User, and
Firebase Hosting Admin, and grant it Service Account User on `lifenest-api`. Bind the provider's repository principal
to the deployment account with Workload Identity User. The current project is already configured with that restricted
provider and service account; no GitHub Actions secrets are required.

Run the full `deploy.sh` once with an administrator identity to provision APIs, Firestore indexes and TTL, bucket,
runtime service account, and Artifact Registry. The workflow sets `SKIP_INFRA=1` and only rebuilds, seeds, and deploys.

---

## Change the OPR: one line of YAML

Every financial parameter lives in [config/rules_my_2026_07.yaml](config/rules_my_2026_07.yaml); nothing numeric is
hard-coded in the engine, and every computation is stamped with `rules_version`.

```yaml
rates:
  opr_pct: 3.00        # was 2.75
```

Restart the API and every figure moves. Mei (DSR-bound) with the demo profile:

| | OPR 2.75% | OPR 3.00% |
|---|---|---|
| Effective rate | 4.40% | 4.65% |
| Maximum property price | RM852,000 | RM824,000 |
| Maximum loan | RM766,800 | RM741,600 |

`tests/test_engine_rules.py::test_changing_opr_is_one_yaml_edit_and_moves_figures` proves this on every run.

## Grounding guarantee

Gemini never states a figure it computed itself, and this is enforced in code, not just requested in the prompt:

1. **Tools compute, the model narrates.** Nine tools wrap the engine. Their outputs carry 2-dp money strings,
   `currency`, `rules_version` and `inputs_echo`, and each call writes `TOOL_CALL` and `COMPUTATION` audit records.
2. **The UI never reads numbers from prose.** Every figure on screen is rendered from `cards` and `computations`.
3. **Every reply is checked before it leaves the server** ([app/agent/grounding.py](app/agent/grounding.py)). Every
   RM amount and percentage in the reply must match a tool output, a cited regulation source, a value the user typed,
   or their confirmed profile — at the precision displayed (RM520k and RM2,186 are fine; RM900,000 is not).
   `GROUNDING_CHECK=warn` keeps the reply and audits `UNGROUNDED_FIGURE`; `enforce` (production) withholds the text and
   keeps the cards.
4. **No approval promises.** "You will be approved", "pasti diluluskan" and "一定会获得批准" are replaced in every mode
   and audited as `PROHIBITED_CLAIM`.
5. [tests/test_grounding.py](tests/test_grounding.py) defines `assert_grounded`, which runs on every reply of the
   [acceptance journey](tests/test_journey.py).

## Privacy

- **Review before trust.** Extraction is a proposal. Nothing is computed until the user confirms or corrects it, and
  every field records its source (`payslip`, `epf_statement`, `user_corrected`, `user_stated`…). User-asserted figures
  are labelled *unverified* on the verdict.
- **Documents.** Uploaded through short-lived signed URLs into a private bucket with a 24-hour lifecycle; content type
  and file signature are checked; bytes are never copied into Firestore or logs.
- **Never stored.** Raw document text, IC/NRIC numbers, bank account numbers, addresses and full names are dropped at
  the extraction schema and scrubbed again before any write.
- **Purge.** *Delete my data* (`DELETE /sessions/{id}`) removes documents, profile, messages and review records, then
  audits `DOCUMENT_PURGED`. Sessions, documents and review actions also expire through Firestore TTL.
- **Isolation.** `user_id` comes only from the verified Firebase token; another user's session looks exactly like a
  missing one (404) and is audited as `AUTHZ_DENIED`.
- **Context.** Designed with Malaysia's Personal Data Protection Act 2010 principles in mind — purpose limitation,
  minimal retention and the user's ability to delete. All demo data is synthetic.

## Orchestration

The agent is a small explicit async loop ([app/agent/orchestrator.py](app/agent/orchestrator.py)), not a framework:
Gemini proposes tool calls, they run concurrently against the engine, results go back, and this repeats until it
answers or `AGENT_MAX_STEPS`. Profile gating, argument validation, timeouts, audit and grounding are each a few
visible lines, which is what an auditor of a financial tool needs to see. The loop maps directly onto a graph
framework if one is needed later: the LLM call and the tool fan-out become two nodes with a conditional edge on "has
tool calls", and the turn state (messages, outcomes) becomes the graph state.

## Testing

`pytest` runs 278 tests offline: engine worked examples, scenarios, storage authz and purge, the review gate, the
tool registry, grounding, the full spec §15 journey, every spec §16 unexpected input, and static checks on the web
client (no HTML injection sinks, strict CSP, complete translations). Firestore-adapter tests also run against the
emulator when `FIRESTORE_EMULATOR_HOST` is set.

## Limitations

- An indicative model. The parameters in the rules file need verification against Bank Negara Malaysia, KWSP and LHDN
  before anyone relies on them; individual banks apply their own DSR bands, spreads and tenure caps.
- No real lender integration and no credit-bureau data; property stock is synthetic.
- First-home schemes and stamp duty exemptions are described, not applied.
- The offline embedder and demo planner are stand-ins for Vertex AI: English-only retrieval and keyword intent.
- Rate limiting is per Cloud Run instance.

## Roadmap

- **Readiness tracking** — re-check the path to approval monthly as savings and commitments change.
- **Agency / developer view** — qualified, affordability-filtered leads with the buyer's consent.
- **Outcome data** — learn from actual approvals to calibrate the indicative bands.
- **Automotive pack** — the same engine and guardrails for car financing.
