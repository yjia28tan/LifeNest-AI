# LifeNest AI — benchmarks

Scripts that produce the numbers on the performance slide. Every input is synthetic.

| Slide row | Script | Needs Gemini? |
|---|---|---|
| Calculation correctness | `bench_engine.py` | No |
| Document extraction accuracy | `bench_extraction.py` | Yes (Vertex AI) |
| End-to-end latency, number provenance, language coverage, cost | `bench_e2e.py` | Yes |

Run everything from the repo root, with the venv active and `.env` / `secrets.env` set up as in the main README
("Running with real Gemini (Vertex AI) locally").

## 1. Test data

```powershell
python -m benchmarks.generate_docs          # 20 personas -> 60 documents + ground_truth.json in benchmarks/data
```

Each persona has a payslip, an EPF statement (three balances, only Akaun Sejahtera is scored) and a loan statement
with 1–3 loans. About 30% are saved as mildly degraded "phone photo" JPEGs. Every printed value is recorded in
`benchmarks/data/ground_truth.json`.

## 2. Calculation correctness (offline)

```powershell
python -m benchmarks.bench_engine
```

Compares the engine with an independent re-implementation of the same rules (plain floats, reads the YAML directly,
shares no code with `app/engine`) on 50 random profiles plus the worked figures published in the main README. Checks
net income, DSR, tenure, max price, max loan, instalment, limiting factor and every upfront-cost line, to RM0.01.
Writes `results/engine_cases.csv` for hand-checking a sample in a spreadsheet. Also runs in CI as
`tests/test_benchmark_engine.py`.

## 3. Extraction accuracy (Gemini)

```powershell
python -m benchmarks.bench_extraction --limit 6        # quick trial first
python -m benchmarks.bench_extraction                  # full set
```

Calls the app's own `GeminiExtractor` (same prompt, schema and model) and scores every printed field. Also runs three
safety checks on the repo's documents: the prompt-injection payslip, the restaurant receipt and the unreadable photo.
Wrong fields are listed in `results/extraction_mismatches.csv`. Use `--concurrency 1` if you hit 429 quota errors.

## 4. End-to-end (Gemini)

```powershell
# in-process with your .env (LLM_BACKEND=vertex): gives tokens and cost
python -m benchmarks.bench_e2e --mode local --runs 9 --price-in <USD per 1M input> --price-out <USD per 1M output>

# against the deployed Cloud Run API, like the web app: gives real-world latency
python -m benchmarks.bench_e2e --mode remote --runs 9
```

Each run creates a session, uploads the three documents, extracts, confirms (correcting any wrong field from the
ground truth, as a buyer would), asks for a verdict and one follow-up. Languages rotate EN → BM → 中文.
Latency is first upload → verdict reply, excluding human review time; one warm-up run is excluded. Provenance uses the
app's own grounding check on every RM amount and percentage in every reply. Take the prices for your `GEMINI_MODEL`
from the Vertex AI pricing page; thinking tokens are billed as output.

Offline smoke test (no Gemini): `python -m benchmarks.bench_e2e --mode local --persona-set repo --runs 3 --warmup 0`
with `LLM_BACKEND=fake`. Its numbers measure the fake, not Gemini, so don't report them.

Results are written to `benchmarks/results/` (gitignored); each script ends with a "Slide 9 ->" summary.
