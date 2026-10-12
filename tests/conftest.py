from __future__ import annotations

from decimal import Decimal

import pytest

from app.engine.models import Commitment, EPFBalances, Income, Person, Profile
from app.engine.rules import Rules, load_rules

D = Decimal


@pytest.fixture(scope="session")
def rules() -> Rules:
    return load_rules()


def make_profile(
    *,
    age: int = 29,
    gross: str = "6200",
    statutory: str = "901.65",
    variable: str = "0",
    commitments: tuple[tuple[str, str], ...] = (("car_loan", "780"), ("study_loan", "310")),
    akaun_sejahtera: str = "18400",
    savings: str | None = None,
    existing_housing_loans: int = 0,
    dependants: int = 0,
) -> Profile:
    return Profile(
        person=Person(age=age, dependants=dependants),
        income=Income(
            gross_monthly=D(gross),
            statutory_deductions=D(statutory),
            variable_component=D(variable),
        ),
        commitments=tuple(Commitment(type=t, monthly=D(m)) for t, m in commitments),
        epf=EPFBalances(akaun_sejahtera=D(akaun_sejahtera)),
        existing_housing_loans=existing_housing_loans,
        savings=None if savings is None else D(savings),
    )


@pytest.fixture
def aina() -> Profile:
    """Aina, 29: gross 6,200; EPF 682 + SOCSO 24.75 + EIS 9.90 + PCB 185 = 901.65; net 5,298.35."""
    return make_profile()
