"""Cash needed at completion: down payment, stamp duties, legal fees, valuation, disbursements."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.engine.epf import eligible_withdrawal
from app.engine.models import Profile
from app.engine.money import HUNDRED, ZERO, money, pct, tiered_marginal
from app.engine.rules import Rules


class UpfrontCostsResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    property_price: Decimal
    margin_of_finance_pct: Decimal
    loan_amount: Decimal
    down_payment: Decimal
    stamp_duty_mot: Decimal
    stamp_duty_loan: Decimal
    legal_fees: Decimal
    valuation: Decimal
    disbursements: Decimal
    total_cash_needed: Decimal
    epf_applied: Decimal
    shortfall_after_epf: Decimal
    savings_declared: Decimal | None
    shortfall_after_savings: Decimal | None
    rules_version: str


def _components(price: Decimal, mof_pct: Decimal, rules: Rules) -> dict[str, Decimal]:
    loan = price * mof_pct / HUNDRED
    return {
        "loan_amount": loan,
        "down_payment": price - loan,
        "stamp_duty_mot": tiered_marginal(price, rules.stamp_duty_mot_tiers),
        "stamp_duty_loan": loan * rules.stamp_duty_loan_pct / HUNDRED,
        "legal_fees": tiered_marginal(price, rules.legal_fees_tiers),
        "valuation": price * rules.other_upfront.valuation_pct / HUNDRED,
        "disbursements": rules.other_upfront.disbursements_flat,
    }


def cash_needed(price: Decimal, mof_pct: Decimal, rules: Rules) -> Decimal:
    c = _components(price, mof_pct, rules)
    return sum((v for k, v in c.items() if k != "loan_amount"), ZERO)


def cash_available(profile: Profile, rules: Rules) -> Decimal | None:
    """Savings plus eligible EPF, or None if savings were not declared."""
    if profile.savings is None:
        return None
    return profile.savings + eligible_withdrawal(profile, rules)


def upfront_costs(property_price: Decimal, profile: Profile, rules: Rules) -> UpfrontCostsResult:
    mof_pct = rules.margin_of_finance_pct(profile.existing_housing_loans)
    c = {k: money(v) for k, v in _components(property_price, mof_pct, rules).items()}
    total = sum((v for k, v in c.items() if k != "loan_amount"), ZERO)

    epf = money(eligible_withdrawal(profile, rules))
    after_epf = max(total - epf, ZERO)
    after_savings = None if profile.savings is None else max(after_epf - money(profile.savings), ZERO)

    return UpfrontCostsResult(
        property_price=money(property_price),
        margin_of_finance_pct=pct(mof_pct),
        total_cash_needed=total,
        epf_applied=min(epf, total),
        shortfall_after_epf=after_epf,
        savings_declared=None if profile.savings is None else money(profile.savings),
        shortfall_after_savings=after_savings,
        rules_version=rules.version,
        **c,
    )
