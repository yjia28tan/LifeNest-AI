from __future__ import annotations

from decimal import Decimal

import pytest

from app.engine.dsr import compute_dsr, dsr_cap_pct
from tests.conftest import make_profile

D = Decimal


def test_aina_worked_example(rules, aina) -> None:
    r = compute_dsr(aina, rules)
    assert r.net_income == D("5298.35")
    assert r.total_commitments == D("1090.00")
    assert r.dsr_pct == D("20.57")            # 1090 / 5298.35
    assert r.max_dsr_pct == D("70.00")        # 5,000 < net <= 10,000
    assert r.headroom_monthly == D("2618.85")  # 5298.35 * 0.70 - 1090 = 2618.845
    assert r.verdict_band == "WITHIN_BAND"
    assert r.rules_version == rules.version


@pytest.mark.parametrize(
    ("net", "expected"),
    [("2500", "60"), ("3000", "60"), ("3000.01", "65"), ("5000", "65"),
     ("10000", "70"), ("10000.01", "75"), ("0", "60"), ("-100", "60")],
)
def test_band_boundaries_are_inclusive_upper(rules, net, expected) -> None:
    assert dsr_cap_pct(D(net), rules) == D(expected)


def test_variable_income_haircut(rules) -> None:
    # Mei: 2,400 of 7,800 is commission; 30% of it is discounted.
    mei = make_profile(age=34, gross="7800", statutory="1000", variable="2400", commitments=())
    assert compute_dsr(mei, rules).net_income == D("6080.00")  # 7800 - 1000 - 720


def test_commitments_exceeding_band(rules) -> None:
    p = make_profile(gross="3500", statutory="500", commitments=(("personal_loan", "2500"),))
    r = compute_dsr(p, rules)
    assert r.dsr_pct == D("83.33")
    assert r.verdict_band == "ABOVE_BAND"
    assert r.headroom_monthly == D("0.00")


@pytest.mark.parametrize(("gross", "statutory"), [("0", "0"), ("500", "900")])
def test_zero_or_negative_net_income(rules, gross, statutory) -> None:
    r = compute_dsr(make_profile(gross=gross, statutory=statutory), rules)
    assert r.dsr_pct is None
    assert r.verdict_band == "ABOVE_BAND"
    assert r.headroom_monthly == D("0.00")
