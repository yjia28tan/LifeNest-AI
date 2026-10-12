"""Deterministic financial engine. Pure functions over the versioned rules config; no LLM imports."""

from app.engine.dsr import DSRResult, compute_dsr
from app.engine.epf import EPFResult, epf_withdrawal
from app.engine.loan_capacity import LoanCapacityResult, loan_capacity
from app.engine.location_cost import LocationCostResult, UnknownAreaError, location_cost
from app.engine.models import Commitment, Employment, EPFBalances, Income, Person, Profile
from app.engine.path_to_approval import PathToApprovalResult, PlanAction, plan_path_to_approval
from app.engine.rules import Rules, load_rules
from app.engine.scenarios import ScenarioResult, simulate_scenario
from app.engine.stock_match import ExceedsComputedCapacityError, Listing, match_affordable_stock
from app.engine.upfront_costs import UpfrontCostsResult, upfront_costs

__all__ = [
    "Commitment",
    "DSRResult",
    "EPFBalances",
    "EPFResult",
    "Employment",
    "ExceedsComputedCapacityError",
    "Income",
    "Listing",
    "LoanCapacityResult",
    "LocationCostResult",
    "PathToApprovalResult",
    "Person",
    "PlanAction",
    "Profile",
    "Rules",
    "ScenarioResult",
    "UnknownAreaError",
    "UpfrontCostsResult",
    "compute_dsr",
    "epf_withdrawal",
    "load_rules",
    "loan_capacity",
    "location_cost",
    "match_affordable_stock",
    "plan_path_to_approval",
    "simulate_scenario",
    "upfront_costs",
]
