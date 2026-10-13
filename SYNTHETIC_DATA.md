# Synthetic data

Every document, dataset, name, employer, salary figure and identifier in this
repository is **fabricated**. None of it derives from a real Malaysian
individual, employer or institution.

- `evals/payslips/` (generated in ticket I-01) contains synthetic payslips
  produced by `evals/generate_payslips.py`. Each PDF/PNG carries a visible
  `SPECIMEN` watermark.
- `evals/golden_profiles.json` (generated in ticket E-02) contains 25
  hand-computed borrower profiles used to regression-test the engine.
- The demo profile in `evals/demo_profile.json` (generated in ticket S-04)
  is calibrated to match the pitch deck's headline figures.

LifeNest AI **never** ingests real credit reports, real payslips or real
CCRIS/CTOS data during development. The buyer-facing product processes their
own documents in a session-scoped context, never persisted beyond the session
— see [AGENT.md](AGENT.md) for the architectural privacy rules.
