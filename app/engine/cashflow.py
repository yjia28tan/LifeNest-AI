"""Monthly household cashflow pieces shared by scenarios and path-to-approval."""

from __future__ import annotations

from decimal import Decimal

from app.engine.models import Profile
from app.engine.money import ZERO
from app.engine.rules import Rules


def living_costs(profile: Profile, rules: Rules) -> Decimal:
    lc = rules.living_costs
    return lc.base_household_monthly + lc.per_dependant_monthly * profile.person.dependants


def commitments_in_month(profile: Profile, month: int) -> Decimal:
    """Commitments still being paid in `month` (1-based, counted from today)."""
    return sum(
        (c.monthly for c in profile.commitments if c.remaining_months is None or month <= c.remaining_months),
        ZERO,
    )
