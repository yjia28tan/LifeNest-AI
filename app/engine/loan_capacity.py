"""Maximum loan and property price, and which constraint binds first."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.engine.dsr import available_monthly
from app.engine.models import Profile
from app.engine.money import HUNDRED, ZERO, floor_thousand, money, pct
from app.engine.rules import Rules
from app.engine.upfront_costs import cash_available, cash_needed

LimitingFactor = Literal["DSR", "TENURE_AGE", "MARGIN_OF_FINANCE", "CASH_UPFRONT"]

_THOUSAND = Decimal("1000")
_SEARCH_CEILING_THOUSANDS = 10_000_000  # RM10bn: bisection bound, not a financial parameter


class LoanCapacityResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_loan: Decimal
    max_property_price: Decimal
    monthly_instalment: Decimal
    tenure_years: int
    tenure_capped_by_age: bool
    effective_rate_pct: Decimal
    margin_of_finance_pct: Decimal
    down_payment_pct: Decimal
    limiting_factor: LimitingFactor
    available_monthly: Decimal
    price_by_dsr: Decimal
    price_by_cash: Decimal | None
    rules_version: str


def resolve_tenure(age: int, rules: Rules, requested_years: int | None = None) -> tuple[int, bool]:
    """Return (tenure_years, capped_by_age)."""
    wanted = rules.tenure.max_years if requested_years is None else min(requested_years, rules.tenure.max_years)
    wanted = max(wanted, 0)
    age_cap = max(rules.tenure.max_age_at_maturity - age, 0)
    return min(wanted, age_cap), age_cap < wanted


def annuity_factor(rate_pct: Decimal, months: int) -> Decimal:
    if months <= 0:
        return ZERO
    r = rate_pct / HUNDRED / 12
    if r == ZERO:
        return Decimal(months)
    return (1 - (1 + r) ** -months) / r


def principal_from_instalment(instalment: Decimal, rate_pct: Decimal, years: int) -> Decimal:
    return instalment * annuity_factor(rate_pct, years * 12)


def instalment_for_months(principal: Decimal, rate_pct: Decimal, months: int) -> Decimal:
    factor = annuity_factor(rate_pct, months)
    return ZERO if factor == ZERO else principal / factor


def instalment_for(principal: Decimal, rate_pct: Decimal, years: int) -> Decimal:
    return instalment_for_months(principal, rate_pct, years * 12)


def outstanding_principal(principal: Decimal, rate_pct: Decimal, months_total: int, months_paid: int) -> Decimal:
    if months_paid >= months_total:
        return ZERO
    r = rate_pct / HUNDRED / 12
    instalment = instalment_for_months(principal, rate_pct, months_total)
    if r == ZERO:
        return principal - instalment * months_paid
    growth = (1 + r) ** months_paid
    return principal * growth - instalment * (growth - 1) / r


def max_price_by_cash(cash: Decimal, mof_pct: Decimal, rules: Rules) -> Decimal:
    """Highest whole-RM1,000 price whose total upfront cash fits within `cash`."""
    def fits(k: int) -> bool:
        return cash_needed(Decimal(k) * _THOUSAND, mof_pct, rules) <= cash

    if not fits(0):
        return ZERO
    lo, hi = 0, 1
    while fits(hi) and hi < _SEARCH_CEILING_THOUSANDS:
        lo, hi = hi, hi * 2
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if fits(mid) else (lo, mid)
    return Decimal(lo) * _THOUSAND


def loan_capacity(
    profile: Profile,
    rules: Rules,
    tenure_years: int | None = None,
    rate_override_pct: Decimal | None = None,
) -> LoanCapacityResult:
    rate = rules.effective_rate_pct if rate_override_pct is None else rate_override_pct
    tenure, capped_by_age = resolve_tenure(profile.person.age, rules, tenure_years)
    mof = rules.margin_of_finance_pct(profile.existing_housing_loans)

    avail = max(available_monthly(profile, rules), ZERO)
    price_by_dsr = principal_from_instalment(avail, rate, tenure) / (mof / HUNDRED)

    cash = cash_available(profile, rules)
    price_by_cash = None if cash is None else max_price_by_cash(cash, mof, rules)

    factor: LimitingFactor
    if price_by_cash is not None and price_by_cash < price_by_dsr:
        raw_price = price_by_cash
        # A reduced margin is what inflates the cash requirement, so name it as the cause.
        factor = "MARGIN_OF_FINANCE" if mof < rules.margin_of_finance.first_property_pct else "CASH_UPFRONT"
    else:
        raw_price = price_by_dsr
        factor = "TENURE_AGE" if capped_by_age else "DSR"

    max_price = floor_thousand(raw_price)
    max_loan = money(max_price * mof / HUNDRED)

    return LoanCapacityResult(
        max_loan=max_loan,
        max_property_price=max_price,
        monthly_instalment=money(instalment_for(max_loan, rate, tenure)),
        tenure_years=tenure,
        tenure_capped_by_age=capped_by_age,
        effective_rate_pct=pct(rate),
        margin_of_finance_pct=pct(mof),
        down_payment_pct=pct(HUNDRED - mof),
        limiting_factor=factor,
        available_monthly=money(avail),
        price_by_dsr=floor_thousand(price_by_dsr),
        price_by_cash=None if price_by_cash is None else money(price_by_cash),
        rules_version=rules.version,
    )
