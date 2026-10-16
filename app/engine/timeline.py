"""'When can I afford it?': earliest month each scenario reaches a target price over the projection horizon."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.engine.cashflow import commitments_in_month, living_costs
from app.engine.dsr import dsr_cap_pct, net_income
from app.engine.loan_capacity import LimitingFactor, loan_capacity
from app.engine.models import Commitment, Income, Profile
from app.engine.money import HUNDRED, ZERO, money, pct
from app.engine.rules import PriceBand, Rules
from app.engine.statutory import IncomeBreakdown, income_breakdown

TimelineScenario = Literal["baseline", "clear_commitments", "joint_applicant", "cheaper_area"]
TimelineStatus = Literal["AFFORDABLE", "NOT_WITHIN_HORIZON", "NEEDS_INPUTS", "NOT_APPLICABLE"]
PriceSource = Literal["stated", "price_band"]


class TimelineRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario: TimelineScenario
    area: str | None
    target_price: Decimal | None
    status: TimelineStatus
    earliest_month: int | None
    earliest_date: str | None
    key_lever: str
    best_max_property_price: Decimal | None = None
    payoff_amount: Decimal | None = None
    monthly_freed: Decimal | None = None
    inputs_needed: tuple[str, ...] = ()


class TimelineResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_price: Decimal
    target_area: str | None
    target_price_source: PriceSource
    start_date: date
    horizon_months: int
    horizon_years: int
    income_growth_pct_per_year: Decimal
    income: IncomeBreakdown
    current_net_income: Decimal
    dsr_cap_pct_at_current_income: Decimal
    current_max_property_price: Decimal
    price_by_dsr_today: Decimal
    binding_constraint: LimitingFactor
    savings_declared: bool
    tenure_years: int
    tenure_reason: str
    rows: tuple[TimelineRow, ...]
    assumptions: tuple[str, ...]
    rules_version: str


def month_label(start: date, months: int) -> str:
    total = start.year * 12 + start.month - 1 + months
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _scaled_income(income: Income, factor: Decimal) -> Income:
    def scale(v: Decimal | None) -> Decimal | None:
        return None if v is None else v * factor

    return income.model_copy(update={
        "gross_monthly": scale(income.gross_monthly), "statutory_deductions": scale(income.statutory_deductions),
        "variable_component": income.variable_component * factor, "net_monthly": scale(income.net_monthly),
    })


def _earliest(profile: Profile, rules: Rules, target: Decimal, *, clear_debts: bool) -> tuple[int | None, Decimal]:
    """Month-by-month: income grows yearly, the surplus is saved, debts run off; first month capacity >= target.

    income(m)  = income(0) × (1 + g)^(m // 12)
    savings(m) = savings(m−1) + net(m) − living − commitments(m)
    affordable when loan_capacity(snapshot(m)).max_property_price >= target (DSR, tenure and cash all checked)
    """
    growth = 1 + rules.projection.income_growth_pct_per_year / HUNDRED
    living = living_costs(profile, rules)
    savings = profile.savings or ZERO
    debts = list(profile.commitments)
    best = ZERO
    for m in range(rules.projection.horizon_months + 1):
        snap = profile.model_copy(update={
            "income": _scaled_income(profile.income, growth ** (m // 12)),
            "person": profile.person.model_copy(update={"age": profile.person.age + m // 12}),
            "commitments": tuple(debts),
            "savings": max(savings, ZERO),
        })
        if m > 0:
            savings += net_income(snap, rules) - living - commitments_in_month(snap, 1)
            debts = [Commitment(type=c.type, monthly=c.monthly,
                                remaining_months=None if c.remaining_months is None else c.remaining_months - 1)
                     for c in debts if c.remaining_months is None or c.remaining_months > 1]
        payoff = sum((c.monthly * (c.remaining_months or 0) for c in debts), ZERO)
        if clear_debts and debts and savings >= payoff:
            savings -= payoff
            debts = []
        snap = snap.model_copy(update={"commitments": tuple(debts), "savings": max(savings, ZERO)})
        price = loan_capacity(snap, rules).max_property_price
        best = max(best, price)
        if price >= target:
            return m, best
    return None, best


def _row(scenario: TimelineScenario, area: str | None, target: Decimal, start: date, found: tuple[int | None, Decimal],
         lever: str, **extra: object) -> TimelineRow:
    month, best = found
    return TimelineRow(
        scenario=scenario, area=area, target_price=money(target),
        status="AFFORDABLE" if month is not None else "NOT_WITHIN_HORIZON",
        earliest_month=month, earliest_date=None if month is None else month_label(start, month),
        key_lever=lever, best_max_property_price=best, **extra,  # type: ignore[arg-type]
    )


def affordability_timeline(
    profile: Profile,
    rules: Rules,
    target_price: Decimal,
    start: date,
    *,
    target_price_source: PriceSource = "stated",
    area: str | None = None,
    cheaper_area: PriceBand | None = None,
    commitments_confirmed: bool = True,
) -> TimelineResult:
    g = rules.projection.income_growth_pct_per_year
    savings0 = profile.savings or ZERO
    today = loan_capacity(profile.model_copy(update={"savings": savings0}), rules)
    net = net_income(profile, rules)

    rows = [_row("baseline", area, target_price, start, _earliest(profile, rules, target_price, clear_debts=False),
                 f"Income grows {g}% a year and the monthly surplus is saved for the deposit and fees.")]

    if not profile.commitments or not commitments_confirmed:
        rows.append(TimelineRow(scenario="clear_commitments", area=area, target_price=money(target_price),
                                status="NOT_APPLICABLE", earliest_month=None, earliest_date=None,
                                key_lever="No confirmed commitments to clear."))
    else:
        unknown = [c.type for c in profile.commitments if c.remaining_months is None]
        freed = money(sum((c.monthly for c in profile.commitments), ZERO))
        if unknown:
            rows.append(TimelineRow(
                scenario="clear_commitments", area=area, target_price=money(target_price), status="NEEDS_INPUTS",
                earliest_month=None, earliest_date=None, monthly_freed=freed,
                key_lever="Settle or reduce existing commitments; the outstanding balance is needed to date this.",
                inputs_needed=tuple(f"outstanding balance or months remaining on {t}" for t in unknown)))
        else:
            payoff = money(sum((c.monthly * (c.remaining_months or 0) for c in profile.commitments), ZERO))
            rows.append(_row("clear_commitments", area, target_price, start,
                             _earliest(profile, rules, target_price, clear_debts=True),
                             "Settle existing commitments from savings as soon as savings cover them.",
                             payoff_amount=payoff, monthly_freed=freed))

    rows.append(TimelineRow(
        scenario="joint_applicant", area=area, target_price=money(target_price), status="NEEDS_INPUTS",
        earliest_month=None, earliest_date=None,
        key_lever="Apply with a co-applicant so both incomes count towards the debt service ratio.",
        inputs_needed=("co-applicant net monthly income", "co-applicant monthly commitments")))

    if cheaper_area is None:
        rows.append(TimelineRow(scenario="cheaper_area", area=None, target_price=None, status="NOT_APPLICABLE",
                                earliest_month=None, earliest_date=None,
                                key_lever="No cheaper area is configured for this location."))
    else:
        alt = cheaper_area.typical_entry_price
        rows.append(_row("cheaper_area", cheaper_area.name, alt, start,
                         _earliest(profile, rules, alt, clear_debts=False),
                         f"Buy in {cheaper_area.name} at its typical entry price instead."))

    assumptions = [
        f"Income grows {g}% a year from the configured planning assumption; living costs stay flat.",
        "Every month's surplus after living costs and commitments is saved.",
        "Affordable means the debt service ratio, loan tenure and upfront cash all allow the target price.",
    ]
    if profile.savings is None:
        assumptions.append("Savings were not declared, so they start at zero.")
    if target_price_source == "price_band":
        assumptions.append("The target price is a typical entry price for the area from the rules config, "
                           "not a listing.")

    return TimelineResult(
        target_price=money(target_price), target_area=area, target_price_source=target_price_source, start_date=start,
        horizon_months=rules.projection.horizon_months, horizon_years=rules.projection.horizon_months // 12, income_growth_pct_per_year=pct(g),
        income=income_breakdown(profile, rules),
        current_net_income=money(net), dsr_cap_pct_at_current_income=pct(dsr_cap_pct(net, rules)),
        current_max_property_price=today.max_property_price, price_by_dsr_today=today.price_by_dsr,
        binding_constraint=today.limiting_factor, savings_declared=profile.savings is not None,
        tenure_years=today.tenure_years, tenure_reason=today.tenure_reason, rows=tuple(rows),
        assumptions=tuple(assumptions), rules_version=rules.version,
    )
