from __future__ import annotations

from decimal import Decimal

import pytest

from app.engine.money import tiered_marginal
from app.engine.upfront_costs import upfront_costs
from tests.conftest import make_profile

D = Decimal


def test_520k_first_property_worked_example(rules, aina) -> None:
    r = upfront_costs(D("520000"), aina, rules)
    assert r.margin_of_finance_pct == D("90.00")
    assert r.loan_amount == D("468000.00")
    assert r.down_payment == D("52000.00")
    assert r.stamp_duty_mot == D("9600.00")    # 1%*100k + 2%*400k + 3%*20k
    assert r.stamp_duty_loan == D("2340.00")   # 0.5% * 468k
    assert r.legal_fees == D("6450.00")        # 1.25%*500k + 1%*20k
    assert r.valuation == D("1300.00")         # 0.25% * 520k
    assert r.disbursements == D("1500.00")
    assert r.total_cash_needed == D("73190.00")
    assert r.epf_applied == D("18400.00")
    assert r.shortfall_after_epf == D("54790.00")
    assert r.savings_declared is None and r.shortfall_after_savings is None


def test_750k_worked_example(rules, aina) -> None:
    r = upfront_costs(D("750000"), aina, rules)
    assert r.stamp_duty_mot == D("16500.00")
    assert r.stamp_duty_loan == D("3375.00")
    assert r.legal_fees == D("8750.00")
    assert r.total_cash_needed == D("107000.00")


def test_total_equals_sum_of_components(rules, aina) -> None:
    r = upfront_costs(D("612345.67"), aina, rules)
    parts = (r.down_payment + r.stamp_duty_mot + r.stamp_duty_loan + r.legal_fees
             + r.valuation + r.disbursements)
    assert r.total_cash_needed == parts


@pytest.mark.parametrize(
    ("price", "mot", "legal"),
    [("0", "0", "0"), ("100000", "1000", "1250"), ("500000", "9000", "6250"),
     ("1200000", "32000", "12650")],
)
def test_tier_boundaries(rules, price, mot, legal) -> None:
    assert tiered_marginal(D(price), rules.stamp_duty_mot_tiers) == D(mot)
    assert tiered_marginal(D(price), rules.legal_fees_tiers) == D(legal)


def test_savings_cover_shortfall(rules) -> None:
    r = upfront_costs(D("520000"), make_profile(savings="60000"), rules)
    assert r.shortfall_after_epf == D("54790.00")
    assert r.shortfall_after_savings == D("0.00")


def test_third_property_uses_70pct_margin(rules) -> None:
    r = upfront_costs(D("520000"), make_profile(existing_housing_loans=2), rules)
    assert r.margin_of_finance_pct == D("70.00")
    assert r.loan_amount == D("364000.00")
    assert r.down_payment == D("156000.00")
