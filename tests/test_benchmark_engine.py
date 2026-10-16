"""Runs the calculation-correctness benchmark on every push: engine vs. the independent reference in benchmarks/."""

from __future__ import annotations

from app.engine import load_rules
from benchmarks.bench_engine import RULES_PATH, Reference, anchor_cases, check_case, random_cases


def test_engine_matches_independent_reference_on_all_cases() -> None:
    rules, ref = load_rules(RULES_PATH), Reference(RULES_PATH)
    cases = anchor_cases() + random_cases(50, seed=2026)
    failures = [(r["case"], r["failures"]) for r in (check_case(c, ref, rules) for c in cases) if r["failures"]]
    assert failures == []
