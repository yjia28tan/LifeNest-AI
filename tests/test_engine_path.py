from __future__ import annotations

from decimal import Decimal

from app.engine.loan_capacity import loan_capacity
from app.engine.models import Commitment
from app.engine.path_to_approval import plan_path_to_approval, required_net_income
from tests.conftest import make_profile

D = Decimal


def _aina_with_end_dates(savings: str = "54790"):
    return make_profile(savings=savings).model_copy(update={"commitments": (
        Commitment(type="car_loan", monthly=D("780"), remaining_months=36),
        Commitment(type="study_loan", monthly=D("310"), remaining_months=48),
    )})


def test_aina_to_650k_clears_study_loan_then_saves(rules) -> None:
    plan = plan_path_to_approval(_aina_with_end_dates(), rules, D("650000"))
    first, last = plan.actions[0], plan.actions[-1]
    assert (first.action, first.commitment_type, first.amount, first.by_month) == (
        "CLEAR_COMMITMENT", "study_loan", D("14880.00"), 0)  # 310 * 48; smallest payoff that closes the DSR gap
    assert last.action == "SAVE_UPFRONT_CASH"
    assert last.amount == D("92300.00")                       # cash needed at RM650k
    assert last.monthly_saving == D("2718.35")                # 5298.35 - 1800 - 780
    assert plan.eligible_from_month == last.by_month == 13    # (73,900 - 39,910) / 2,718.35 -> 12.5
    assert plan.current_max_property_price == D("520000.00")


def test_plan_end_state_actually_meets_target(rules) -> None:
    plan = plan_path_to_approval(_aina_with_end_dates(), rules, D("650000"))
    after = make_profile(savings="73900").model_copy(update={"commitments": (
        Commitment(type="car_loan", monthly=D("780"), remaining_months=36 - 13),
    )})
    assert loan_capacity(after, rules).max_property_price >= plan.target_property_price


def test_already_affordable_returns_month_zero(rules) -> None:
    plan = plan_path_to_approval(make_profile(savings="54790"), rules, D("500000"))
    assert plan.eligible_from_month == 0 and plan.actions == ()


def test_unreachable_with_open_ended_commitments(rules) -> None:
    plan = plan_path_to_approval(make_profile(savings="54790"), rules, D("650000"))
    assert plan.eligible_from_month is None
    kinds = [a.action for a in plan.actions]
    assert kinds == ["INCREASE_NET_INCOME", "REDUCE_TARGET_PRICE"]
    assert plan.actions[0].amount == D("5460.62")
    assert plan.actions[1].amount == D("622000.00")       # DSR ceiling once cash catches up
    assert plan.best_max_property_price == D("622000.00")


def test_natural_commitment_end_is_recorded(rules) -> None:
    # DSR is not binding at RM500k, so nothing is settled early; the car loan simply runs out.
    p = make_profile(savings="0").model_copy(update={"commitments": (
        Commitment(type="car_loan", monthly=D("780"), remaining_months=3),
    )})
    plan = plan_path_to_approval(p, rules, D("500000"))
    ends = [a for a in plan.actions if a.action == "COMMITMENT_ENDS"]
    assert [(a.commitment_type, a.by_month) for a in ends] == [("car_loan", 3)]
    assert plan.actions[-1].action == "SAVE_UPFRONT_CASH"
    assert plan.eligible_from_month is not None and plan.eligible_from_month > 3


def test_zero_income_still_returns_useful_plan(rules) -> None:
    plan = plan_path_to_approval(make_profile(gross="0", statutory="0", commitments=()), rules, D("400000"))
    assert plan.eligible_from_month is None
    assert {a.action for a in plan.actions} == {"INCREASE_NET_INCOME", "REDUCE_TARGET_PRICE"}
    assert any("not declared" in s for s in plan.assumptions)


def test_required_net_income_respects_inclusive_band_edge(rules) -> None:
    # 60% band would need 3,166.67 (> 3,000), so the 65% band applies from 3,000.01.
    assert required_net_income(D("1000"), D("900"), rules) == D("3000.01")
    assert required_net_income(D("1000"), D("200"), rules) == D("2000")
