from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.engine.loan_capacity import loan_capacity
from app.engine.rules import DEFAULT_RULES_PATH, Rules, parse_rules

ENGINE_DIR = Path(__file__).resolve().parents[1] / "app" / "engine"
FORBIDDEN_IMPORT_PREFIXES = (
    "app.llm", "app.agent", "app.kb", "app.storage", "app.routers",
    "google", "vertexai", "fastapi", "httpx", "requests",
)


def _raw() -> dict:
    return yaml.safe_load(DEFAULT_RULES_PATH.read_text(encoding="utf-8"))


def test_rules_load_with_version(rules: Rules) -> None:
    assert rules.version == "my-2026-07"
    assert rules.currency == "MYR"


def test_numeric_params_are_exact_decimals(rules: Rules) -> None:
    assert rules.rates.opr_pct == Decimal("2.75")
    assert rules.dsr.variable_income_haircut_pct == Decimal("30")
    assert isinstance(rules.transport.fuel_price_per_litre, Decimal)


def test_effective_rate_is_opr_plus_standard_spread(rules: Rules) -> None:
    assert rules.effective_rate_pct == Decimal("4.40")


def test_missing_field_fails_fast() -> None:
    raw = _raw()
    del raw["tenure"]["max_age_at_maturity"]
    with pytest.raises(ValidationError):
        parse_rules(raw)


def test_unknown_field_rejected() -> None:
    raw = _raw()
    raw["rates"]["opr_pct_typo"] = 3.0
    with pytest.raises(ValidationError):
        parse_rules(raw)


def test_tiers_must_be_ascending_and_open_ended() -> None:
    raw = _raw()
    raw["stamp_duty_mot_tiers"][-1]["up_to"] = 2_000_000
    with pytest.raises(ValidationError):
        parse_rules(raw)
    raw = _raw()
    raw["legal_fees_tiers"][0]["up_to"] = 2_000_000
    with pytest.raises(ValidationError):
        parse_rules(raw)


def test_changing_opr_is_one_yaml_edit_and_moves_figures(rules: Rules, aina) -> None:
    raw = _raw()
    raw["rates"]["opr_pct"] = 3.00
    raised = parse_rules(raw)
    assert raised.effective_rate_pct == Decimal("4.65")
    assert loan_capacity(aina, raised).max_property_price < loan_capacity(aina, rules).max_property_price


def test_engine_never_imports_llm_or_io_layers() -> None:
    offenders = []
    for path in ENGINE_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders += [f"{path.name}: {n}" for n in names if n.startswith(FORBIDDEN_IMPORT_PREFIXES)]
    assert offenders == []
