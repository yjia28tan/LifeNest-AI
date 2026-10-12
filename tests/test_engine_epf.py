from __future__ import annotations

from decimal import Decimal

from app.engine.epf import epf_withdrawal
from tests.conftest import make_profile

D = Decimal


def test_aina_akaun_sejahtera(rules, aina) -> None:
    r = epf_withdrawal(aina, rules)
    assert r.akaun_sejahtera_balance == D("18400.00")
    assert r.eligible_withdrawal == D("18400.00")
    assert r.withdrawal_source == "akaun_sejahtera"
    assert r.rules_version == rules.version


def test_rules_note_explains_2024_restructure(rules, aina) -> None:
    note = epf_withdrawal(aina, rules).rules_note
    assert "Akaun Sejahtera" in note
    assert "15%" in note and "30%" in note


def test_uses_2024_structure_not_account_2(rules) -> None:
    assert rules.epf.structure == "three_account_2024"
    assert rules.epf.akaun_sejahtera_contribution_share_pct == D("15")


def test_zero_balance(rules) -> None:
    r = epf_withdrawal(make_profile(akaun_sejahtera="0"), rules)
    assert r.eligible_withdrawal == D("0.00")
