"""Load and validate the versioned rules config. Every financial parameter lives in YAML."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "rules_my_2026_07.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Rates(_Strict):
    opr_pct: Decimal
    sbr_pct: Decimal
    spread_strong_applicant_pct: Decimal
    spread_standard_applicant_pct: Decimal
    stress_test_uplift_pct: Decimal


class DSRBand(_Strict):
    up_to_net_monthly: Decimal | None
    max_dsr_pct: Decimal


class DSRRules(_Strict):
    max_dsr_pct_by_income_band: tuple[DSRBand, ...]
    income_basis: str
    variable_income_haircut_pct: Decimal

    @model_validator(mode="after")
    def _bands_ascending_open_ended(self) -> DSRRules:
        _check_tiers([b.up_to_net_monthly for b in self.max_dsr_pct_by_income_band], "dsr bands")
        return self


class Tenure(_Strict):
    max_years: int
    max_age_at_maturity: int


class MarginOfFinance(_Strict):
    first_property_pct: Decimal
    second_property_pct: Decimal
    third_and_subsequent_pct: Decimal


class EPFRules(_Strict):
    structure: str
    akaun_sejahtera_contribution_share_pct: Decimal
    housing_withdrawal_source: str
    note: str


class Tier(_Strict):
    up_to: Decimal | None
    rate_pct: Decimal


class OtherUpfront(_Strict):
    valuation_pct: Decimal
    disbursements_flat: Decimal


class LivingCosts(_Strict):
    base_household_monthly: Decimal
    per_dependant_monthly: Decimal
    recommended_buffer_months: int


class Transport(_Strict):
    fuel_price_per_litre: Decimal
    avg_consumption_l_per_100km: Decimal
    working_days_per_month: int
    parking_monthly_city_centre: Decimal
    transit_pass_monthly: Decimal


class LifeEventScenario(_Strict):
    one_off_cost: Decimal
    monthly_cost_delta: Decimal
    income_delta_pct: Decimal


class JobLossScenario(_Strict):
    months_without_income: int
    income_delta_pct: Decimal


class RateRiseScenario(_Strict):
    rate_delta_pct: Decimal


class CarReplacementScenario(_Strict):
    one_off_cost: Decimal
    monthly_cost_delta: Decimal


class Projection(_Strict):
    horizon_months: int


class Area(_Strict):
    name: str
    lat: Decimal
    lon: Decimal
    city_centre: bool


class Locations(_Strict):
    road_distance_factor: Decimal
    avg_drive_speed_kmh: Decimal
    avg_transit_speed_kmh: Decimal
    transit_overhead_minutes: Decimal
    toll_per_km: Decimal
    areas: tuple[Area, ...]

    def find(self, name: str) -> Area | None:
        key = name.strip().casefold()
        return next((a for a in self.areas if a.name.casefold() == key), None)


class Scenarios(_Strict):
    first_child: LifeEventScenario
    marriage: LifeEventScenario
    job_loss: JobLossScenario
    rate_rise: RateRiseScenario
    car_replacement: CarReplacementScenario


class Rules(_Strict):
    version: str
    currency: str
    effective_from: date
    source_note: str
    rates: Rates
    dsr: DSRRules
    tenure: Tenure
    margin_of_finance: MarginOfFinance
    epf: EPFRules
    stamp_duty_mot_tiers: tuple[Tier, ...]
    stamp_duty_loan_pct: Decimal
    legal_fees_tiers: tuple[Tier, ...]
    other_upfront: OtherUpfront
    living_costs: LivingCosts
    transport: Transport
    projection: Projection
    locations: Locations
    scenarios: Scenarios

    @model_validator(mode="after")
    def _tiers_ascending_open_ended(self) -> Rules:
        _check_tiers([t.up_to for t in self.stamp_duty_mot_tiers], "stamp_duty_mot_tiers")
        _check_tiers([t.up_to for t in self.legal_fees_tiers], "legal_fees_tiers")
        return self

    @property
    def base_rate_pct(self) -> Decimal:
        # SBR is pegged to the OPR, so the OPR is the single driver of pricing.
        return self.rates.opr_pct

    @property
    def effective_rate_pct(self) -> Decimal:
        return self.base_rate_pct + self.rates.spread_standard_applicant_pct

    def margin_of_finance_pct(self, existing_housing_loans: int) -> Decimal:
        mof = self.margin_of_finance
        if existing_housing_loans <= 0:
            return mof.first_property_pct
        if existing_housing_loans == 1:
            return mof.second_property_pct
        return mof.third_and_subsequent_pct


def _check_tiers(bounds: list[Decimal | None], name: str) -> None:
    if not bounds or bounds[-1] is not None:
        raise ValueError(f"{name}: last tier must be open-ended (null)")
    finite = bounds[:-1]
    if any(b is None for b in finite):
        raise ValueError(f"{name}: only the last tier may be open-ended")
    if finite != sorted(finite) or len(set(finite)) != len(finite):
        raise ValueError(f"{name}: tier bounds must be strictly ascending")


def _floats_to_decimal(node: Any) -> Any:
    # Go through str() so 2.75 becomes Decimal("2.75"), not its binary float expansion.
    if isinstance(node, float):
        return Decimal(str(node))
    if isinstance(node, dict):
        return {k: _floats_to_decimal(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_floats_to_decimal(v) for v in node]
    return node


def parse_rules(raw: dict[str, Any]) -> Rules:
    return Rules.model_validate(_floats_to_decimal(raw))


def load_rules(path: str | Path = DEFAULT_RULES_PATH) -> Rules:
    with Path(path).open(encoding="utf-8") as fh:
        return parse_rules(yaml.safe_load(fh))
