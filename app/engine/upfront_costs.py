"""Cash needed at completion: down payment, stamp duties, legal fees, valuation, disbursements."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.engine.cashflow import living_costs
from app.engine.epf import eligible_withdrawal
from app.engine.models import Profile
from app.engine.money import HUNDRED, ZERO, money, pct, tiered_marginal
from app.engine.rules import Rules


class UpfrontCostsResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    property_price: Decimal
    margin_of_finance_pct: Decimal
    down_payment_pct: Decimal
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
    first_home_incentives_eligible: bool
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
        down_payment_pct=pct(HUNDRED - mof_pct),
        total_cash_needed=total,
        epf_applied=min(epf, total),
        shortfall_after_epf=after_epf,
        savings_declared=None if profile.savings is None else money(profile.savings),
        shortfall_after_savings=after_savings,
        first_home_incentives_eligible=rules.first_home_incentives_eligible(profile.existing_housing_loans),
        rules_version=rules.version,
        **c,
    )


class SavingsTarget(BaseModel):
    model_config = ConfigDict(frozen=True)

    property_price: Decimal
    down_payment: Decimal
    legal_fees: Decimal
    stamp_duty: Decimal
    valuation: Decimal
    disbursements: Decimal
    buffer: Decimal
    buffer_months: int
    total_needed: Decimal
    current_savings: Decimal
    savings_declared: bool
    epf_applied: Decimal
    gap: Decimal
    months_to_target: int
    monthly_savings_target: Decimal
    rules_version: str


def savings_target(price: Decimal, profile: Profile, rules: Rules, months_to_target: int) -> SavingsTarget:
    """Monthly saving needed to hold the purchase cash by the target date.

    total  = down_payment + legal_fees + stamp_duty (MOT + loan) + valuation + disbursements + buffer
    buffer = monthly living costs × recommended_buffer_months
    gap    = max(total − current_savings − eligible EPF, 0)
    monthly_savings_target = gap / months_to_target
    """
    mof_pct = rules.margin_of_finance_pct(profile.existing_housing_loans)
    c = {k: money(v) for k, v in _components(price, mof_pct, rules).items()}
    months = max(months_to_target, 1)
    buffer_months = rules.living_costs.recommended_buffer_months
    buffer = money(living_costs(profile, rules) * buffer_months)
    stamp = c["stamp_duty_mot"] + c["stamp_duty_loan"]
    total = c["down_payment"] + c["legal_fees"] + stamp + c["valuation"] + c["disbursements"] + buffer
    savings = money(profile.savings) if profile.savings is not None else money(ZERO)
    epf = min(money(eligible_withdrawal(profile, rules)), total)
    gap = max(total - savings - epf, ZERO)
    return SavingsTarget(
        property_price=money(price), down_payment=c["down_payment"], legal_fees=c["legal_fees"], stamp_duty=stamp,
        valuation=c["valuation"], disbursements=c["disbursements"], buffer=buffer, buffer_months=buffer_months,
        total_needed=total, current_savings=savings, savings_declared=profile.savings is not None, epf_applied=epf,
        gap=gap, months_to_target=months, monthly_savings_target=money(gap / months), rules_version=rules.version,
    )
