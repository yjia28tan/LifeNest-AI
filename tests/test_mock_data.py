from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from app.engine.dsr import compute_dsr
from app.engine.loan_capacity import loan_capacity
from app.engine.models import Profile
from app.engine.path_to_approval import plan_path_to_approval
from app.engine.scenarios import simulate_scenario
from app.engine.stock_match import Listing, match_affordable_stock

ROOT = Path(__file__).resolve().parents[1]
D = Decimal

KB_DOCS = {
    "epf-akaun-sejahtera", "dsr-responsible-lending", "margin-of-finance", "stamp-duty-and-legal-fees",
    "loan-tenure-and-age", "sbr-and-rate-pricing", "first-home-schemes",
}
KB_CATEGORIES = {"EPF", "LENDING", "COSTS", "TENURE", "RATES", "SCHEMES"}
KB_FOOTER = "Indicative guidance. Verify current terms with the relevant authority. Not a lending decision."


@pytest.fixture(scope="module")
def listings() -> list[Listing]:
    raw = json.loads((ROOT / "data" / "property_stock.json").read_text(encoding="utf-8"))
    return [Listing.model_validate(x) for x in raw["listings"]]


@pytest.fixture(scope="module")
def demo() -> dict[str, dict]:
    raw = json.loads((ROOT / "data" / "demo_profiles.json").read_text(encoding="utf-8"))
    return {p["demo_id"]: p for p in raw["profiles"]}


def _profile(demo: dict, demo_id: str) -> Profile:
    return Profile.model_validate(demo[demo_id]["profile"])


# --- property stock -------------------------------------------------------------------------

def test_stock_shape(listings, rules) -> None:
    assert 70 <= len(listings) <= 90
    assert len({x.listing_id for x in listings}) == len(listings)
    assert {x.area for x in listings} <= {a.name for a in rules.locations.areas}
    assert len({x.area for x in listings}) == 10
    assert {x.completion_status for x in listings} == {"completed", "under_construction"}


def test_stock_clusters_in_target_band(listings) -> None:
    in_band = [x for x in listings if D("350000") <= x.price <= D("750000")]
    assert len(in_band) / len(listings) >= 0.7


def test_stock_straddles_aina_capacity(listings, demo, rules) -> None:
    capacity = loan_capacity(_profile(demo, "aina"), rules).max_property_price
    assert sum(x.price <= capacity for x in listings) >= 20
    assert sum(x.price > capacity for x in listings) >= 20
    kajang = match_affordable_stock(listings, capacity, capacity, area="Kajang")
    assert kajang and all(x.price <= capacity for x in kajang)


# --- demo profiles --------------------------------------------------------------------------

def test_demo_profiles_parse_and_are_internally_consistent(demo) -> None:
    assert set(demo) == {"aina", "kumar", "mei"}
    for d in demo.values():
        p = Profile.model_validate(d["profile"])
        assert p.income.net_monthly == p.income.gross_monthly - p.income.statutory_deductions
        assert set(d["field_provenance"]) >= {"income.gross_monthly", "savings"}
        assert d["field_provenance"]["savings"]["source"] == "user_stated"


def test_aina_verdict_is_cash_bound_near_520k(demo, rules) -> None:
    cap = loan_capacity(_profile(demo, "aina"), rules)
    assert cap.limiting_factor == "CASH_UPFRONT"
    assert D("500000") <= cap.max_property_price <= D("540000")
    assert cap.price_by_dsr > cap.max_property_price


def test_aina_honest_stress_story(demo, rules) -> None:
    aina = _profile(demo, "aina")
    capacity = loan_capacity(aina, rules).max_property_price
    at_capacity = simulate_scenario(aina, rules, "first_child", capacity, start_month=36)
    at_dream = simulate_scenario(aina, rules, "first_child", D("750000"), start_month=36)
    assert at_capacity.status != "SAFE"
    assert at_dream.status == "BREAKS" and at_dream.break_month is not None
    assert at_dream.monthly_shortfall > at_capacity.monthly_shortfall


def test_aina_path_to_650k(demo, rules) -> None:
    plan = plan_path_to_approval(_profile(demo, "aina"), rules, D("650000"))
    assert plan.eligible_from_month is not None
    assert plan.actions[0].action == "CLEAR_COMMITMENT"
    assert plan.actions[0].commitment_type == "study_loan"


def test_kumar_approves_comfortably(demo, rules) -> None:
    kumar = _profile(demo, "kumar")
    assert loan_capacity(kumar, rules).max_property_price >= D("750000")
    assert simulate_scenario(kumar, rules, "first_child", D("750000"), start_month=36).status == "SAFE"


def test_mei_variable_income_haircut_binds(demo, rules) -> None:
    mei = _profile(demo, "mei")
    haircut = mei.income.variable_component * rules.dsr.variable_income_haircut_pct / 100
    assert compute_dsr(mei, rules).net_income == mei.income.net_monthly - haircut
    assert loan_capacity(mei, rules).limiting_factor == "DSR"


# --- synthetic documents --------------------------------------------------------------------

def test_generated_documents_match_demo_profiles(tmp_path, demo) -> None:
    pytest.importorskip("PIL")
    from PIL import Image

    from scripts.generate_mock_documents import AINA, KUMAR, MEI, generate

    paths = generate(tmp_path)
    names = {p.name for p in paths}
    assert names >= {
        "aina_payslip.png", "aina_epf.png", "aina_loan_statements.png", "kumar_payslip.png",
        "mei_payslip.png", "low_quality_payslip.png", "not_a_payslip.png", "injection_payslip.png",
    }
    for p in paths:
        with Image.open(p) as img:
            assert img.format == "PNG" and img.width >= 400

    for slip, demo_id in ((AINA, "aina"), (KUMAR, "kumar"), (MEI, "mei")):
        inc = demo[demo_id]["profile"]["income"]
        assert D(str(slip.gross)) == D(inc["gross_monthly"])
        assert D(str(slip.deductions)) == D(inc["statutory_deductions"])
        assert D(str(slip.net)) == D(inc["net_monthly"])
        assert slip.age == demo[demo_id]["profile"]["person"]["age"]


# --- knowledge base -------------------------------------------------------------------------

def _split_front_matter(text: str) -> tuple[dict, str]:
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    assert match, "missing YAML front matter"
    return yaml.safe_load(match.group(1)), match.group(2)


def test_knowledge_documents() -> None:
    files = sorted((ROOT / "knowledge").glob("*.md"))
    assert {f.stem for f in files} == KB_DOCS
    for f in files:
        meta, body = _split_front_matter(f.read_text(encoding="utf-8"))
        assert meta["doc_id"] == f.stem
        assert meta["category"] in KB_CATEGORIES
        assert meta["title"] and meta["source_uri"].startswith("https://")
        assert str(meta["version"]) == "2026-07"
        assert len(re.findall(r"^## ", body, re.MULTILINE)) >= 3, f.name
        words = len(body.split())
        assert 300 <= words <= 700, f"{f.name}: {words} words"
        assert body.strip().strip("*").endswith(KB_FOOTER), f.name
