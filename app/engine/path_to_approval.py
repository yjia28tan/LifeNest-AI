"""Dated, deterministic plan from today's profile to a target property price."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.engine.cashflow import commitments_in_month, living_costs
from app.engine.dsr import net_income
from app.engine.loan_capacity import (
    LoanCapacityResult,
    annuity_factor,
    loan_capacity,
    resolve_tenure,
)
from app.engine.models import Commitment, Profile
from app.engine.money import HUNDRED, ZERO, money
from app.engine.rules import Rules
from app.engine.upfront_costs import cash_needed

ActionType = Literal[
    "CLEAR_COMMITMENT",
    "COMMITMENT_ENDS",
    "SAVE_UPFRONT_CASH",
    "INCREASE_NET_INCOME",
    "REDUCE_TARGET_PRICE",
]


class PlanAction(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: ActionType
    commitment_type: str | None = None
    amount: Decimal
    monthly_saving: Decimal
    by_month: int


class PathToApprovalResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_property_price: Decimal
    eligible_from_month: int | None
    current_max_property_price: Decimal
    best_max_property_price: Decimal
    best_max_property_price_month: int
    actions: tuple[PlanAction, ...]
    assumptions: tuple[str, ...]
    rules_version: str


@dataclass
class _Debt:
    type: str
    monthly: Decimal
    remaining: int | None

    @property
    def outstanding(self) -> Decimal | None:
        # Upper bound: ignores interest rebates on early settlement.
        return None if self.remaining is None else self.monthly * self.remaining


def required_net_income(available_needed: Decimal, commitments: Decimal, rules: Rules) -> Decimal:
    """Smallest net monthly income whose DSR band leaves `available_needed` after commitments."""
    floor = ZERO
    for band in rules.dsr.max_dsr_pct_by_income_band:
        net = (available_needed + commitments) * HUNDRED / band.max_dsr_pct
        if band.up_to_net_monthly is None or net <= band.up_to_net_monthly:
            return max(net, floor)
        # Band upper bounds are inclusive, so the next band starts one sen above.
        floor = band.up_to_net_monthly + Decimal("0.01")
    raise AssertionError("rules validation guarantees an open-ended final band")


def plan_path_to_approval(profile: Profile, rules: Rules, target_property_price: Decimal) -> PathToApprovalResult:
    horizon = rules.projection.horizon_months
    target = target_property_price
    mof = rules.margin_of_finance_pct(profile.existing_housing_loans)
    net = net_income(profile, rules)
    living = living_costs(profile, rules)

    assumptions = [
        "Net income held at today's level; no pay rises assumed.",
        "EPF Akaun Sejahtera held at today's balance; new contributions not counted.",
        "Living costs from the rules config; rent is assumed to sit within them.",
        "Settling a commitment early costs its monthly amount times the months remaining.",
    ]
    if profile.savings is None:
        assumptions.append("Savings were not declared, so they are taken as zero.")
    savings = profile.savings or ZERO
    debts = [_Debt(c.type, c.monthly, c.remaining_months) for c in profile.commitments]
    actions: list[PlanAction] = []

    def snapshot(month: int) -> Profile:
        return profile.model_copy(update={
            "person": profile.person.model_copy(update={"age": profile.person.age + month // 12}),
            "commitments": tuple(Commitment(type=d.type, monthly=d.monthly, remaining_months=d.remaining) for d in debts),
            "savings": max(savings, ZERO),
        })

    def available_needed(month: int) -> Decimal | None:
        tenure, _ = resolve_tenure(profile.person.age + month // 12, rules)
        factor = annuity_factor(rules.effective_rate_pct, tenure * 12)
        return None if factor == ZERO else target * mof / HUNDRED / factor

    def try_clear_one(month: int, cap: LoanCapacityResult) -> LoanCapacityResult:
        nonlocal savings
        needed = available_needed(month)
        if needed is None or cap.price_by_dsr >= target:
            return cap
        gap = needed - cap.available_monthly
        payable = [d for d in debts if d.outstanding is not None and d.outstanding <= savings]
        if not payable:
            return cap
        closers = [d for d in payable if d.monthly >= gap]
        pick = min(closers, key=lambda d: d.outstanding or ZERO) if closers else min(payable, key=lambda d: d.remaining or 0)
        assert pick.outstanding is not None
        savings -= pick.outstanding
        debts.remove(pick)
        actions.append(PlanAction(
            action="CLEAR_COMMITMENT", commitment_type=pick.type, amount=money(pick.outstanding),
            monthly_saving=money(pick.monthly), by_month=month,
        ))
        return loan_capacity(snapshot(month), rules)

    start_cap = loan_capacity(snapshot(0), rules)
    best_price, best_month = start_cap.max_property_price, 0
    cash_was_short = start_cap.price_by_cash is not None and start_cap.price_by_cash < target

    for month in range(horizon + 1):
        if month > 0:
            savings += net - living - sum((d.monthly for d in debts), ZERO)
            for d in list(debts):
                if d.remaining is None:
                    continue
                d.remaining -= 1
                if d.remaining == 0:
                    debts.remove(d)
                    actions.append(PlanAction(
                        action="COMMITMENT_ENDS", commitment_type=d.type, amount=money(ZERO),
                        monthly_saving=money(d.monthly), by_month=month,
                    ))

        cap = try_clear_one(month, loan_capacity(snapshot(month), rules))
        if cap.max_property_price > best_price:
            best_price, best_month = cap.max_property_price, month

        if cap.max_property_price >= target:
            if cash_was_short:
                surplus = net - living - sum((d.monthly for d in debts), ZERO)
                actions.append(PlanAction(
                    action="SAVE_UPFRONT_CASH", amount=money(cash_needed(target, mof, rules)),
                    monthly_saving=money(max(surplus, ZERO)), by_month=month,
                ))
            return _result(target, month, start_cap, best_price, best_month, actions, assumptions, rules)

    final_cap = loan_capacity(snapshot(horizon), rules)
    needed_now = available_needed(0)
    if final_cap.price_by_dsr < target and needed_now is not None:
        required = required_net_income(needed_now, commitments_in_month(profile, 1), rules)
        actions.append(PlanAction(
            action="INCREASE_NET_INCOME", amount=money(required),
            monthly_saving=money(max(required - net, ZERO)), by_month=0,
        ))
    actions.append(PlanAction(
        action="REDUCE_TARGET_PRICE", amount=best_price, monthly_saving=money(ZERO), by_month=best_month,
    ))
    return _result(target, None, start_cap, best_price, best_month, actions, assumptions, rules)


def _result(
    target: Decimal,
    eligible: int | None,
    start_cap: LoanCapacityResult,
    best_price: Decimal,
    best_month: int,
    actions: list[PlanAction],
    assumptions: list[str],
    rules: Rules,
) -> PathToApprovalResult:
    return PathToApprovalResult(
        target_property_price=money(target),
        eligible_from_month=eligible,
        current_max_property_price=start_cap.max_property_price,
        best_max_property_price=best_price,
        best_max_property_price_month=best_month,
        actions=tuple(actions),
        assumptions=tuple(assumptions),
        rules_version=rules.version,
    )
