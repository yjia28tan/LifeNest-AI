"""EPF housing withdrawal under the 2024 three-account structure (Akaun Sejahtera)."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.engine.models import Profile
from app.engine.money import money
from app.engine.rules import Rules


class EPFResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    akaun_sejahtera_balance: Decimal
    eligible_withdrawal: Decimal
    withdrawal_source: str
    rules_note: str
    rules_version: str


def eligible_withdrawal(profile: Profile, rules: Rules) -> Decimal:
    return profile.epf.akaun_sejahtera


def epf_withdrawal(profile: Profile, rules: Rules) -> EPFResult:
    return EPFResult(
        akaun_sejahtera_balance=money(profile.epf.akaun_sejahtera),
        eligible_withdrawal=money(eligible_withdrawal(profile, rules)),
        withdrawal_source=rules.epf.housing_withdrawal_source,
        rules_note=rules.epf.note.strip(),
        rules_version=rules.version,
    )
