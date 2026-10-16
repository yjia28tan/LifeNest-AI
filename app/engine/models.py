"""Confirmed buyer profile — the only input the engine accepts about a person (shape per spec §12)."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Where an input figure came from. Only the first three may carry a user's financial figure into a computation.
Provenance = Literal["stated_this_turn", "stated_earlier", "confirmed_from_profile", "default", "inferred"]
TRUSTED_PROVENANCE: frozenset[str] = frozenset({"stated_this_turn", "stated_earlier", "confirmed_from_profile"})


class InputUsed(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: str
    label: str
    value: str
    provenance: Provenance
    source: str


def untrusted_inputs(inputs: list[InputUsed]) -> list[InputUsed]:
    """Inputs the engine must refuse: anything not stated, confirmed, or a rules default."""
    return [i for i in inputs if i.provenance not in TRUSTED_PROVENANCE and i.provenance != "default"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Person(_Model):
    age: int = Field(ge=0, le=120)
    dependants: int = Field(default=0, ge=0)
    locale: str = "en"


class Income(_Model):
    gross_monthly: Decimal | None = None
    # None = not known, so the engine derives it from gross with the rules config (never treated as zero).
    statutory_deductions: Decimal | None = Field(default=None, ge=0)
    variable_component: Decimal = Field(default=Decimal("0"), ge=0)
    # Payslip net, or the user's stated take-home pay. Used only when gross is unknown.
    net_monthly: Decimal | None = None

    @model_validator(mode="after")
    def _gross_or_net(self) -> Income:
        if self.gross_monthly is None and self.net_monthly is None:
            raise ValueError("income needs gross_monthly or net_monthly")
        return self


class Commitment(_Model):
    type: str
    monthly: Decimal = Field(ge=0)
    remaining_months: int | None = Field(default=None, ge=0)


class EPFBalances(_Model):
    akaun_sejahtera: Decimal = Field(default=Decimal("0"), ge=0)


class Employment(_Model):
    type: str = "permanent"
    months_in_service: int | None = Field(default=None, ge=0)


class Profile(_Model):
    person: Person
    income: Income
    commitments: tuple[Commitment, ...] = ()
    epf: EPFBalances = EPFBalances()
    employment: Employment = Employment()
    existing_housing_loans: int = Field(default=0, ge=0)
    # Liquid cash for upfront costs. None = not declared, so the cash constraint is not evaluated.
    savings: Decimal | None = Field(default=None, ge=0)
