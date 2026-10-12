from __future__ import annotations

import math
from decimal import Decimal

import pytest

from app.engine.loan_capacity import loan_capacity, resolve_tenure
from tests.conftest import make_profile

D = Decimal


def _float_price(available: float, rate_pct: float, years: int, mof_pct: float) -> float:
    r, n = rate_pct / 100 / 12, years * 12
    return available * (1 - (1 + r) ** -n) / r / (mof_pct / 100)


def test_aina_dsr_bound_without_savings(rules, aina) -> None:
    r = loan_capacity(aina, rules)
    assert r.tenure_years == 35 and not r.tenure_capped_by_age
    assert r.effective_rate_pct == D("4.40")
    assert r.margin_of_finance_pct == D("90.00")
    assert r.limiting_factor == "DSR"
    assert r.price_by_cash is None
    assert r.available_monthly == D("2618.85")

    expected = math.floor(_float_price(2618.845, 4.40, 35, 90) / 1000) * 1000
    assert r.max_property_price == D(expected)
    assert r.max_property_price % 1000 == 0
    assert r.max_loan == r.max_property_price * D("0.9")
    assert r.monthly_instalment <= r.available_monthly
    assert r.rules_version == rules.version


def test_aina_cash_bound_at_520k(rules) -> None:
    # 54,790 savings + 18,400 EPF = 73,190 = exactly the cash needed at RM520,000.
    r = loan_capacity(make_profile(savings="54790"), rules)
    assert r.price_by_cash == D("520000.00")
    assert r.max_property_price == D("520000.00")
    assert r.limiting_factor == "CASH_UPFRONT"
    assert r.max_loan == D("468000.00")

    one_ringgit_less = loan_capacity(make_profile(savings="54789"), rules)
    assert one_ringgit_less.max_property_price == D("519000.00")


def test_ample_savings_leaves_dsr_binding(rules, aina) -> None:
    r = loan_capacity(make_profile(savings="500000"), rules)
    assert r.limiting_factor == "DSR"
    assert r.max_property_price == loan_capacity(aina, rules).max_property_price


@pytest.mark.parametrize(
    ("age", "years", "capped"),
    [(29, 35, False), (35, 35, False), (40, 30, True), (68, 2, True), (70, 0, True), (75, 0, True)],
)
def test_tenure_cap_by_age(rules, age, years, capped) -> None:
    assert resolve_tenure(age, rules) == (years, capped)


def test_age_68_limited_by_tenure_age(rules) -> None:
    r = loan_capacity(make_profile(age=68), rules)
    assert r.tenure_years == 2
    assert r.limiting_factor == "TENURE_AGE"
    assert r.max_property_price < loan_capacity(make_profile(age=29), rules).max_property_price


def test_age_at_maturity_ceiling_gives_zero(rules) -> None:
    r = loan_capacity(make_profile(age=70), rules)
    assert r.tenure_years == 0
    assert r.max_loan == D("0.00") and r.monthly_instalment == D("0.00")
    assert r.limiting_factor == "TENURE_AGE"


def test_requested_shorter_tenure_lowers_capacity(rules, aina) -> None:
    r = loan_capacity(aina, rules, tenure_years=25)
    assert r.tenure_years == 25 and r.limiting_factor == "DSR"
    assert r.max_property_price < loan_capacity(aina, rules).max_property_price


def test_requested_tenure_above_max_is_capped(rules, aina) -> None:
    assert loan_capacity(aina, rules, tenure_years=50).tenure_years == 35


def test_rate_override(rules, aina) -> None:
    r = loan_capacity(aina, rules, rate_override_pct=D("5.40"))
    assert r.effective_rate_pct == D("5.40")
    assert r.max_property_price < loan_capacity(aina, rules).max_property_price


def test_second_property_keeps_90pct(rules) -> None:
    assert loan_capacity(make_profile(existing_housing_loans=1), rules).margin_of_finance_pct == D("90.00")


def test_third_property_70pct_margin(rules) -> None:
    no_savings = loan_capacity(make_profile(existing_housing_loans=2), rules)
    assert no_savings.margin_of_finance_pct == D("70.00")

    with_savings = loan_capacity(make_profile(existing_housing_loans=2, savings="54790"), rules)
    assert with_savings.limiting_factor == "MARGIN_OF_FINANCE"
    assert with_savings.max_property_price < D("520000")


def test_commitments_exceeding_income_gives_zero(rules) -> None:
    p = make_profile(gross="3000", statutory="400", commitments=(("personal_loan", "3000"),))
    r = loan_capacity(p, rules)
    assert r.max_loan == D("0.00") and r.max_property_price == D("0.00")
    assert r.limiting_factor == "DSR"


def test_zero_income_gives_zero(rules) -> None:
    r = loan_capacity(make_profile(gross="0", statutory="0", commitments=()), rules)
    assert r.max_property_price == D("0.00")


def test_kumar_approves_comfortably(rules) -> None:
    kumar = make_profile(age=41, gross="11500", statutory="2080", commitments=(("car_loan", "1100"),))
    r = loan_capacity(kumar, rules)
    assert r.tenure_years == 29 and r.limiting_factor == "TENURE_AGE"
    assert r.max_property_price > D("750000")
