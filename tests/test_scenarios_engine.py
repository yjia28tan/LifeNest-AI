from __future__ import annotations

from decimal import Decimal

import pytest

from app.engine.loan_capacity import instalment_for, outstanding_principal
from app.engine.models import Commitment
from app.engine.scenarios import simulate_scenario
from tests.conftest import make_profile

D = Decimal


def _with_ending_commitments(profile, car_months: int, study_months: int):
    return profile.model_copy(update={"commitments": (
        Commitment(type="car_loan", monthly=D("780"), remaining_months=car_months),
        Commitment(type="study_loan", monthly=D("310"), remaining_months=study_months),
    )})


def test_outstanding_principal_endpoints() -> None:
    assert outstanding_principal(D("100000"), D("4.4"), 120, 0) == D("100000")
    assert outstanding_principal(D("100000"), D("4.4"), 120, 120) == 0
    assert abs(outstanding_principal(D("100000"), D("4.4"), 120, 119) - instalment_for(D("100000"), D("4.4"), 10)) < D("5")
    assert outstanding_principal(D("12000"), D("0"), 12, 6) == D("6000")


def test_projection_is_internally_consistent(rules) -> None:
    s = simulate_scenario(make_profile(savings="54790"), rules, "first_child", D("520000"), start_month=36)
    assert len(s.projection) == rules.projection.horizon_months + 1
    assert s.projection[0].outgoings == s.narrative_facts["upfront_cash_needed"]
    for prev, cur in zip(s.projection, s.projection[1:], strict=False):
        assert abs(prev.balance + cur.income - cur.outgoings - cur.balance) <= D("0.02")


def test_aina_first_child_at_computed_price_worked_example(rules) -> None:
    # Cash-bound at RM520k: savings are spent on upfront costs, then +1,200/month and a 12,000 one-off.
    s = simulate_scenario(make_profile(savings="54790"), rules, "first_child", D("520000"), start_month=36)
    f = s.narrative_facts
    assert f["monthly_instalment"] == D("2185.95")
    assert f["baseline_monthly_surplus"] == D("222.40")      # 5298.35 - 2185.95 - 1090 - 1800
    assert f["starting_balance"] == D("0.00")
    assert f["balance_before_event"] == D("7784.09")         # 35 months of the unrounded 222.4026 surplus
    assert s.monthly_shortfall == D("977.60")                # 2185.95 + 1090 + 1800 + 1200 - 5298.35
    assert s.status == "BREAKS" and s.break_month == 36


def test_aina_at_750k_breaks_immediately_on_upfront_cash(rules) -> None:
    s = simulate_scenario(make_profile(savings="54790"), rules, "first_child", D("750000"), start_month=36)
    assert s.status == "BREAKS" and s.break_month == 0
    assert s.narrative_facts["upfront_shortfall"] == D("33810.00")  # 107,000 - 73,190
    assert s.narrative_facts["baseline_monthly_surplus"] < 0
    assert s.monthly_shortfall > D("977.60")


def test_comfortable_household_is_safe(rules) -> None:
    kumar = make_profile(age=41, gross="11500", statutory="2080", commitments=(("car_loan", "1100"),), savings="200000")
    s = simulate_scenario(kumar, rules, "first_child", D("500000"), start_month=36)
    assert s.status == "SAFE" and s.break_month is None
    assert s.monthly_shortfall == D("0.00")


def test_at_risk_when_balance_stays_positive_but_below_buffer(rules) -> None:
    aina = _with_ending_commitments(make_profile(savings="54790"), car_months=24, study_months=30)
    s = simulate_scenario(aina, rules, "first_child", D("520000"), start_month=36)
    assert s.status == "AT_RISK" and s.break_month is None


def test_job_loss_zeroes_income_for_configured_months(rules) -> None:
    s = simulate_scenario(make_profile(savings="200000"), rules, "job_loss", D("400000"), start_month=12)
    months_off = rules.scenarios.job_loss.months_without_income
    incomes = [p.income for p in s.projection]
    assert incomes[11] > 0
    assert all(i == 0 for i in incomes[12:12 + months_off])
    assert incomes[12 + months_off] == incomes[11]


def test_rate_rise_raises_instalment_from_event(rules) -> None:
    s = simulate_scenario(make_profile(savings="200000"), rules, "rate_rise", D("400000"), start_month=24)
    f = s.narrative_facts
    assert f["effective_rate_after_pct"] == rules.effective_rate_pct + rules.scenarios.rate_rise.rate_delta_pct
    assert f["monthly_instalment_after"] > f["monthly_instalment"]
    assert s.projection[23].outgoings < s.projection[24].outgoings


def test_param_override_and_validation(rules, aina) -> None:
    base = simulate_scenario(aina, rules, "car_replacement", D("400000"))
    cheaper = simulate_scenario(aina, rules, "car_replacement", D("400000"), params={"one_off_cost": 0})
    assert cheaper.narrative_facts["lowest_balance"] > base.narrative_facts["lowest_balance"]
    with pytest.raises(ValueError, match="unknown params"):
        simulate_scenario(aina, rules, "car_replacement", D("400000"), params={"rate_delta_pct": 2})
    with pytest.raises(ValueError, match="start_month"):
        simulate_scenario(aina, rules, "marriage", D("400000"), start_month=0)


def test_undeclared_savings_starts_with_zero_balance(rules, aina) -> None:
    s = simulate_scenario(aina, rules, "marriage", D("400000"))
    assert s.narrative_facts["savings_declared"] is False
    assert s.narrative_facts["starting_balance"] == D("0.00")
    assert s.narrative_facts["upfront_shortfall"] is None


def test_facts_are_data_not_prose(rules, aina) -> None:
    s = simulate_scenario(aina, rules, "first_child", D("520000"), start_month=36)
    for key, value in s.narrative_facts.items():
        assert not (isinstance(value, str) and " " in value), key
