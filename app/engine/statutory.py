"""Gross-to-net: employee EPF, SOCSO, EIS and an estimated PCB, all from the rules config."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.engine.models import Profile
from app.engine.money import HUNDRED, ZERO, money, tiered_marginal
from app.engine.rules import Rules

_MONTHS = Decimal(12)
IncomeBasis = Literal["payslip_deductions", "derived_from_gross", "stated_net"]


class IncomeBreakdown(BaseModel):
    model_config = ConfigDict(frozen=True)

    basis: IncomeBasis
    gross_monthly: Decimal | None
    epf_employee: Decimal | None
    socso: Decimal | None
    eis: Decimal | None
    pcb_estimate: Decimal | None
    statutory_deductions: Decimal | None
    net_monthly: Decimal
    rules_version: str


def estimate_deductions(gross: Decimal, rules: Rules) -> dict[str, Decimal]:
    """Employee deductions estimated from gross monthly salary.

    epf   = gross × epf_employee_pct
    socso = min(gross, wage_ceiling) × socso_employee_pct;  eis likewise
    chargeable (annual) = max(12·gross − min(12·epf, epf_relief_cap) − individual_relief, 0)
    tax = tiered(chargeable) − rebate (only if chargeable ≤ rebate limit), floored at 0
    pcb = tax / 12
    """
    sd = rules.statutory_deductions
    gross = max(gross, ZERO)
    insured = min(gross, sd.socso_eis_wage_ceiling)
    epf = gross * sd.epf_employee_pct / HUNDRED
    socso = insured * sd.socso_employee_pct / HUNDRED
    eis = insured * sd.eis_employee_pct / HUNDRED
    pcb_rules = sd.pcb
    chargeable = max(gross * _MONTHS - min(epf * _MONTHS, pcb_rules.epf_relief_cap_annual)
                     - pcb_rules.individual_relief_annual, ZERO)
    tax = tiered_marginal(chargeable, pcb_rules.tax_tiers)
    if chargeable <= pcb_rules.rebate_chargeable_income_up_to:
        tax -= pcb_rules.rebate_annual
    pcb = max(tax, ZERO) / _MONTHS
    return {"epf_employee": epf, "socso": socso, "eis": eis, "pcb_estimate": pcb}


def income_breakdown(profile: Profile, rules: Rules) -> IncomeBreakdown:
    """Net monthly income before any variable-income haircut.

    payslip deductions known → net = gross − deductions
    gross only            → net = gross − estimate_deductions(gross)
    stated net only       → net as stated
    """
    inc = profile.income
    if inc.gross_monthly is None:
        assert inc.net_monthly is not None  # Income validation guarantees one of them
        return IncomeBreakdown(basis="stated_net", gross_monthly=None, epf_employee=None, socso=None, eis=None,
                               pcb_estimate=None, statutory_deductions=None, net_monthly=money(inc.net_monthly),
                               rules_version=rules.version)
    if inc.statutory_deductions is not None:
        return IncomeBreakdown(basis="payslip_deductions", gross_monthly=money(inc.gross_monthly), epf_employee=None,
                               socso=None, eis=None, pcb_estimate=None,
                               statutory_deductions=money(inc.statutory_deductions),
                               net_monthly=money(inc.gross_monthly - inc.statutory_deductions),
                               rules_version=rules.version)
    parts = {k: money(v) for k, v in estimate_deductions(inc.gross_monthly, rules).items()}
    total = sum(parts.values(), ZERO)
    return IncomeBreakdown(basis="derived_from_gross", gross_monthly=money(inc.gross_monthly),
                           statutory_deductions=total, net_monthly=money(inc.gross_monthly - total),
                           rules_version=rules.version, **parts)
