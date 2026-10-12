"""Month-by-month household cashflow projection under a life event. Returns facts, not prose."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.engine.cashflow import commitments_in_month, living_costs
from app.engine.dsr import net_income
from app.engine.loan_capacity import (
    instalment_for,
    instalment_for_months,
    outstanding_principal,
    resolve_tenure,
)
from app.engine.models import Profile
from app.engine.money import HUNDRED, ZERO, money, pct
from app.engine.rules import Rules
from app.engine.upfront_costs import cash_available, cash_needed

ScenarioName = Literal["first_child", "marriage", "job_loss", "rate_rise", "car_replacement"]
ScenarioStatus = Literal["SAFE", "AT_RISK", "BREAKS"]


class ProjectionPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    month: int
    income: Decimal
    outgoings: Decimal
    balance: Decimal


class ScenarioResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario: ScenarioName
    property_price: Decimal
    status: ScenarioStatus
    break_month: int | None
    monthly_shortfall: Decimal
    buffer_months: Decimal
    projection: tuple[ProjectionPoint, ...]
    narrative_facts: dict[str, Any]
    rules_version: str


def _event_params(scenario: ScenarioName, rules: Rules, overrides: dict[str, Any]) -> dict[str, Any]:
    base: dict[str, Any] = getattr(rules.scenarios, scenario).model_dump()
    unknown = set(overrides) - set(base)
    if unknown:
        raise ValueError(f"unknown params for {scenario}: {sorted(unknown)}; allowed: {sorted(base)}")
    for key, value in overrides.items():
        base[key] = int(value) if isinstance(base[key], int) else Decimal(str(value))
    return base


def simulate_scenario(
    profile: Profile,
    rules: Rules,
    scenario: ScenarioName,
    property_price: Decimal,
    start_month: int = 12,
    params: dict[str, Any] | None = None,
) -> ScenarioResult:
    horizon = rules.projection.horizon_months
    if not 1 <= start_month <= horizon:
        raise ValueError(f"start_month must be between 1 and {horizon}")
    p = _event_params(scenario, rules, params or {})

    one_off = p.get("one_off_cost", ZERO)
    monthly_delta = p.get("monthly_cost_delta", ZERO)
    income_factor = 1 + p.get("income_delta_pct", ZERO) / HUNDRED
    jobless_months = p.get("months_without_income", 0)
    rate_delta = p.get("rate_delta_pct", ZERO)

    net = net_income(profile, rules)
    living = living_costs(profile, rules)
    rate = rules.effective_rate_pct
    mof = rules.margin_of_finance_pct(profile.existing_housing_loans)
    loan = property_price * mof / HUNDRED
    tenure, _ = resolve_tenure(profile.person.age, rules)
    term_months = tenure * 12
    base_instalment = instalment_for(loan, rate, tenure)

    upfront = cash_needed(property_price, mof, rules)
    cash = cash_available(profile, rules)
    # Undeclared savings: assume upfront cash is covered and the household starts with no buffer.
    start_balance = ZERO if cash is None else cash - upfront
    upfront_shortfall = None if cash is None else max(upfront - cash, ZERO)

    def event_income(m: int) -> Decimal:
        if scenario == "job_loss":
            return net * income_factor if start_month <= m < start_month + jobless_months else net
        return net * income_factor if m >= start_month else net

    balance = start_balance
    instalment = base_instalment
    break_month = 0 if start_balance < ZERO else None
    worst_shortfall = ZERO
    lowest_balance: Decimal | None = None
    lowest_month = start_month
    balance_before_event = start_balance
    recurring_at_event = ZERO
    last_surplus = ZERO
    points = [ProjectionPoint(month=0, income=money(ZERO), outgoings=money(upfront), balance=money(start_balance))]

    for m in range(1, horizon + 1):
        active = m >= start_month
        if m == start_month:
            balance_before_event = balance
            if scenario == "rate_rise" and m <= term_months:
                remaining = outstanding_principal(loan, rate, term_months, m - 1)
                instalment = instalment_for_months(remaining, rate + rate_delta, term_months - (m - 1))

        income = event_income(m)
        recurring = (
            (instalment if m <= term_months else ZERO)
            + commitments_in_month(profile, m)
            + living
            + (monthly_delta if active else ZERO)
        )
        outgoings = recurring + (one_off if m == start_month else ZERO)
        balance += income - outgoings
        last_surplus = income - recurring

        if m == start_month:
            recurring_at_event = recurring
        if active:
            worst_shortfall = max(worst_shortfall, recurring - income)
            if lowest_balance is None or balance < lowest_balance:
                lowest_balance, lowest_month = balance, m
        if break_month is None and balance < ZERO:
            break_month = m
        points.append(ProjectionPoint(month=m, income=money(income), outgoings=money(outgoings), balance=money(balance)))

    assert lowest_balance is not None  # start_month <= horizon guarantees one active month
    buffer_threshold = recurring_at_event * rules.living_costs.recommended_buffer_months
    status: ScenarioStatus
    if break_month is not None:
        status = "BREAKS"
    elif lowest_balance < buffer_threshold or last_surplus < ZERO:
        status = "AT_RISK"
    else:
        status = "SAFE"

    buffer_months = max(balance_before_event, ZERO) / recurring_at_event if recurring_at_event > ZERO else ZERO
    baseline_surplus = net - (base_instalment + commitments_in_month(profile, 1) + living)

    facts: dict[str, Any] = {
        "scenario": scenario,
        "event_month": start_month,
        "property_price": money(property_price),
        "loan_amount": money(loan),
        "effective_rate_pct": pct(rate),
        "monthly_instalment": money(base_instalment),
        "monthly_net_income": money(net),
        "monthly_living_costs": money(living),
        "baseline_monthly_surplus": money(baseline_surplus),
        "upfront_cash_needed": money(upfront),
        "upfront_shortfall": None if upfront_shortfall is None else money(upfront_shortfall),
        "savings_declared": profile.savings is not None,
        "starting_balance": money(start_balance),
        "balance_before_event": money(balance_before_event),
        "lowest_balance": money(lowest_balance),
        "lowest_balance_month": lowest_month,
        "break_month": break_month,
        "monthly_shortfall": money(worst_shortfall),
        "buffer_months": pct(buffer_months),
        "event_params": {k: (money(v) if isinstance(v, Decimal) else v) for k, v in p.items()},
    }
    if scenario == "rate_rise":
        facts["effective_rate_after_pct"] = pct(rate + rate_delta)
        facts["monthly_instalment_after"] = money(instalment)

    return ScenarioResult(
        scenario=scenario,
        property_price=money(property_price),
        status=status,
        break_month=break_month,
        monthly_shortfall=money(worst_shortfall),
        buffer_months=pct(buffer_months),
        projection=tuple(points),
        narrative_facts=facts,
        rules_version=rules.version,
    )
