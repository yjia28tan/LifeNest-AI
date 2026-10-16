"""Debt service ratio on net income after statutory deductions."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.engine.models import Profile
from app.engine.money import HUNDRED, ZERO, money, pct
from app.engine.rules import Rules
from app.engine.statutory import IncomeBreakdown, income_breakdown

VerdictBand = Literal["WITHIN_BAND", "ABOVE_BAND"]


class DSRResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    income: IncomeBreakdown
    net_income: Decimal
    total_commitments: Decimal
    dsr_pct: Decimal | None  # None when net income is zero or negative
    max_dsr_pct: Decimal
    headroom_monthly: Decimal
    verdict_band: VerdictBand
    rules_version: str


def net_income(profile: Profile, rules: Rules) -> Decimal:
    """net = income_breakdown(profile).net_monthly − variable_component × haircut_pct."""
    haircut = profile.income.variable_component * rules.dsr.variable_income_haircut_pct / HUNDRED
    return income_breakdown(profile, rules).net_monthly - haircut


def total_commitments(profile: Profile) -> Decimal:
    return sum((c.monthly for c in profile.commitments), ZERO)


def dsr_cap_pct(net: Decimal, rules: Rules) -> Decimal:
    """THE DSR ceiling for a net monthly income (band upper bounds inclusive). Every module must call this."""
    for band in rules.dsr.max_dsr_pct_by_income_band:
        if band.up_to_net_monthly is None or net <= band.up_to_net_monthly:
            return band.max_dsr_pct
    raise AssertionError("rules validation guarantees an open-ended final band")


def available_monthly(profile: Profile, rules: Rules) -> Decimal:
    """Monthly amount left for a new instalment under the DSR ceiling. May be negative."""
    net = net_income(profile, rules)
    return net * dsr_cap_pct(net, rules) / HUNDRED - total_commitments(profile)


def compute_dsr(profile: Profile, rules: Rules) -> DSRResult:
    net = net_income(profile, rules)
    commitments = total_commitments(profile)
    max_pct = dsr_cap_pct(net, rules)
    headroom = available_monthly(profile, rules)

    if net > ZERO:
        dsr = commitments / net * HUNDRED
        band: VerdictBand = "WITHIN_BAND" if dsr <= max_pct else "ABOVE_BAND"
        dsr_out: Decimal | None = pct(dsr)
    else:
        band, dsr_out = "ABOVE_BAND", None

    return DSRResult(
        income=income_breakdown(profile, rules),
        net_income=money(net),
        total_commitments=money(commitments),
        dsr_pct=dsr_out,
        max_dsr_pct=pct(max_pct),
        headroom_monthly=money(max(headroom, ZERO)),
        verdict_band=band,
        rules_version=rules.version,
    )
