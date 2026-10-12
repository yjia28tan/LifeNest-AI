"""Confirmed buyer profile — the only input the engine accepts about a person (shape per spec §12)."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Person(_Model):
    age: int = Field(ge=0, le=120)
    dependants: int = Field(default=0, ge=0)
    locale: str = "en"


class Income(_Model):
    gross_monthly: Decimal
    statutory_deductions: Decimal = Field(default=Decimal("0"), ge=0)
    variable_component: Decimal = Field(default=Decimal("0"), ge=0)
    # As printed on the payslip; the engine recomputes net from gross and deductions.
    net_monthly: Decimal | None = None


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
