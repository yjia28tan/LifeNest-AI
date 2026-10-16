"""Regressions for the "when can I afford a home in Penang Island with 4500 salary" answer.

Bugs: phantom commitment, gross treated as net, DSR cap mismatch, mislabelled outputs, verdict instead of timeline,
property-count rules, presentation. Each test names the bug it guards.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import pytest

from app.agent.grounding import collect_numbers
from app.engine.cashflow import living_costs
from app.engine.dsr import compute_dsr, dsr_cap_pct
from app.engine.epf import eligible_withdrawal
from app.engine.models import Commitment, Income, Person, Profile
from app.engine.money import money
from app.engine.path_to_approval import plan_path_to_approval
from app.engine.rules import parse_rules
from app.engine.statutory import income_breakdown
from app.engine.timeline import affordability_timeline
from app.engine.upfront_costs import upfront_costs
from app.llm.fake import FakeLLM
from app.llm.gateway import LLMResponse, ToolCall
from app.storage.models import FieldProvenance, ProfileRecord
from tests.test_unexpected_inputs import USER, client, confirmed_payslip, new_session, outputs, say  # noqa: F401

D = Decimal
QUESTION = "so when can i afford a home in penang island with 4500 salary"
INVENTED_DEBTS = ("home loan", "car loan", "study loan", "personal loan", "3,410", "3410")
TABLE_HEADER = "| Scenario | Target price | Earliest date | Key lever |"


def _repo(c):
    return c.app.state.services.repo


def _record(c, sid: str) -> ProfileRecord:
    rec = asyncio.run(_repo(c).get_profile(sid, "buyer"))
    assert rec is not None
    return rec


def _codes(body: dict[str, Any]) -> dict[str, str | None]:
    return {t["tool"]: t["error_code"] for t in body["tool_trace"]}


def _gross_profile(gross: str, age: int = 30, **extra: Any) -> Profile:
    return Profile(person=Person(age=age), income=Income(gross_monthly=D(gross)), **extra)


def _numbers_not_in_engine_json(reply: str, computations: list[dict[str, Any]]) -> list[str]:
    """Every number in the reply must be in the engine JSON; RM amounts may be rounded to the nearest RM100."""
    allowed = collect_numbers([c["outputs"] for c in computations])
    bad = []
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", reply):
        value = D(m.group(0).replace(",", "").rstrip("."))
        if value in allowed:
            continue
        is_rm = reply[max(0, m.start() - 2):m.start()] == "RM"
        if is_rm and any((a / 100).quantize(D(1), ROUND_HALF_UP) * 100 == value for a in allowed):
            continue
        bad.append(m.group(0))
    return bad


# --- 1. phantom inputs ---------------------------------------------------------------------------------

def test_salary_only_has_no_commitments_derives_net_and_invents_no_debts(client) -> None:
    sid = new_session(client)
    first = say(client, sid, QUESTION)
    assert _codes(first)["record_stated_figures"] is None and first["computations"] == []
    assert "How old are you?" in first["reply"]  # missing input is asked for, never assumed
    record = _record(client, sid)
    assert record.confirmed_fields == {"income.gross_monthly": "4500.00"}  # no deductions=0, no commitments

    body = say(client, sid, "I'm 30")
    tl = outputs(body, "affordability_timeline")
    used = {i["field"]: i for i in tl["inputs_used"]}
    assert not any(f.startswith("commitments") for f in used)
    assert used["income.gross_monthly"]["provenance"] == "stated_earlier"
    assert used["person.age"]["provenance"] == "stated_this_turn"
    assert used["income.net_monthly"]["provenance"] == used["income.statutory_deductions"]["provenance"] == "default"

    engine = income_breakdown(_gross_profile("4500"), client.app.state.services.rules)
    assert tl["income"] == engine.model_dump(mode="json")
    assert engine.basis == "derived_from_gross" and engine.net_monthly < engine.gross_monthly
    assert (engine.epf_employee, engine.net_monthly) == (D("495.00"), D("3893.50"))

    reply = body["reply"]
    assert "RM4,500 gross salary" in reply and "RM3,900 a month" in reply  # both figures, rounded
    assert not [w for w in INVENTED_DEBTS if w in reply.lower()]
    assert "gross monthly salary RM4,500 (you stated earlier)" in reply  # footer from provenance


def test_llm_slot_filling_is_rejected(client) -> None:
    client.app.state.services.llm = FakeLLM([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="record_stated_figures", arguments={"figures": {
            "income.gross_monthly": 4500, "income.statutory_deductions": 0,
            "commitments": [{"type": "home_loan", "monthly": 3410}]}})]),
        LLMResponse(text="Could you tell me your age?"),
    ])
    sid = new_session(client)
    body = say(client, sid, QUESTION)
    assert _codes(body)["record_stated_figures"] == "VALIDATION_ERROR"
    assert asyncio.run(_repo(client).get_profile(sid, "buyer")) is None  # nothing invented was stored


def test_stored_value_not_confirmed_this_session_is_asked_before_computing(client) -> None:
    sid = new_session(client)
    repo = _repo(client)
    fields = {"person.age": 30, "income.gross_monthly": "4500.00",
              "commitments": [{"type": "car_loan", "monthly": "600.00", "remaining_months": 24}]}
    legacy = {p: FieldProvenance(source="user_stated", confidence=1.0) for p in fields}  # session_confirmed=False
    session = asyncio.run(repo.get_session(sid, "buyer"))
    from app.agent.state import build_profile
    asyncio.run(repo.save_profile(ProfileRecord(session_id=sid, user_id="buyer", status="CONFIRMED",
                                                confirmed_fields=fields, profile=build_profile(fields)[0],
                                                field_provenance=legacy)))
    asyncio.run(repo.update_session(session, profile_status="CONFIRMED"))

    asked = say(client, sid, "Can I afford a RM300,000 home?")
    assert _codes(asked)["loan_capacity"] == "NEEDS_CONFIRMATION" and asked["computations"] == []
    assert "Is that still correct?" in asked["reply"] and "RM600.00" in asked["reply"]

    answered = say(client, sid, "yes, still correct")
    assert _codes(answered)["confirm_stored_figures"] is None
    used = {i["field"]: i["provenance"] for i in outputs(answered, "loan_capacity")["inputs_used"]}
    assert used["commitments[0]"] == "stated_earlier"
    assert "car loan RM600/month" in answered["reply"]  # the footer lists commitments too


def test_net_and_gross_wording(client) -> None:
    sid = new_session(client)
    say(client, sid, "my net salary is 4500, I'm 30")
    fields = _record(client, sid).confirmed_fields
    assert "income.net_monthly" in fields and "income.gross_monthly" not in fields

    client.app.state.services.llm = FakeLLM([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="record_stated_figures",
                                         arguments={"figures": {"income.gross_monthly": 4500}})]),
        LLMResponse(text="Is that gross or net?"),
        LLMResponse(tool_calls=[ToolCall(id="c2", name="record_stated_figures",
                                         arguments={"figures": {"income.gross_monthly": 4500}})]),
        LLMResponse(text="Noted."),
    ])
    sid = new_session(client)
    first = say(client, sid, "I get 4500 a month")
    assert asyncio.run(_repo(client).get_profile(sid, "buyer")).confirmed_fields == {}  # ambiguous: asked, not stored
    assert first["reply"] == "Is that gross or net?"
    say(client, sid, "4500 a month")  # asked once already: falls back to the gross convention
    assert _record(client, sid).confirmed_fields == {"income.gross_monthly": "4500.00"}


# --- 3. one DSR cap ------------------------------------------------------------------------------------

@pytest.mark.parametrize("gross", ["2800", "4500", "7600", "15000"])
def test_dsr_cap_is_the_same_in_eligibility_route_and_timeline(rules, gross) -> None:
    profile = _gross_profile(gross, commitments=(Commitment(type="home_loan", monthly=D("3410")),))
    eligibility = compute_dsr(profile, rules)
    route = plan_path_to_approval(profile, rules, D("300000"))
    timeline = affordability_timeline(profile, rules, D("300000"), date(2026, 10, 16))
    assert eligibility.max_dsr_pct == route.dsr_cap_pct_at_current_income == timeline.dsr_cap_pct_at_current_income
    if route.required_net_income is not None:
        # The route's cap at the required income is what eligibility would report at that income.
        at_required = Profile(person=Person(age=30), income=Income(net_monthly=route.required_net_income),
                              commitments=profile.commitments)
        assert compute_dsr(at_required, rules).max_dsr_pct == route.dsr_cap_pct_at_required_income


def test_only_dsr_module_reads_the_income_bands() -> None:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    readers = [p.name for p in app_dir.rglob("*.py")
               if "max_dsr_pct_by_income_band" in p.read_text(encoding="utf-8")]
    assert sorted(readers) == ["dsr.py", "rules.py"]


# --- 4. labels and savings target ------------------------------------------------------------------------

def test_savings_target_is_deposit_plus_costs_not_the_income_gap(rules) -> None:
    profile = _gross_profile("4500", commitments=(Commitment(type="home_loan", monthly=D("3410")),),
                             existing_housing_loans=1, savings=D("10000"))
    plan = plan_path_to_approval(profile, rules, D("300000"))
    reach = next(a for a in plan.actions if a.action == "REACH_NET_INCOME")
    assert reach.monthly_saving == 0 and reach.income_gap == money(plan.required_net_income - plan.current_net_income)

    st = plan.savings_target
    c = upfront_costs(D("300000"), profile, rules)
    buffer = living_costs(profile, rules) * rules.living_costs.recommended_buffer_months
    total = (c.down_payment + c.legal_fees + c.stamp_duty_mot + c.stamp_duty_loan + c.valuation + c.disbursements
             + buffer)
    gap = max(total - profile.savings - min(eligible_withdrawal(profile, rules), total), D(0))
    assert st.total_needed == money(total) and st.gap == money(gap)
    assert st.monthly_savings_target == money(gap / st.months_to_target)
    assert st.monthly_savings_target != reach.income_gap


# --- 5. "when" gets a dated timeline -----------------------------------------------------------------------

def test_when_question_returns_a_dated_scenario_table(client) -> None:
    body = say(client, new_session(client), QUESTION + ", I'm 30")
    assert [t["tool"] for t in body["tool_trace"]] == ["record_stated_figures", "affordability_timeline"]
    lines = body["reply"].splitlines()
    assert lines[1] == "When you could afford it, by scenario:" and lines[2] == TABLE_HEADER  # leads with the table
    rows = [ln for ln in lines if ln.startswith("| ") and ln != TABLE_HEADER]
    assert {r.split("|")[1].strip() for r in rows} == {
        "Baseline", "Clear commitments", "Joint applicant", "Cheaper area (Seberang Perai)"}
    assert any(re.search(r"\| \d{4}-\d{2} \|", r) for r in rows)
    assert "cannot afford" not in body["reply"].lower()
    assert "the age-70 limit" in body["reply"]  # why tenure is what it is
    tl = outputs(body, "affordability_timeline")
    assert tl["target_price_source"] == "price_band" and tl["target_area"] == "Penang Island"
    assert any(c["type"] == "affordability_timeline" for c in body["cards"])


def test_timeline_baseline_grows_income(rules) -> None:
    raw = rules.model_dump(mode="json")
    raw["projection"]["income_growth_pct_per_year"] = 0
    frozen = parse_rules(raw)
    profile = _gross_profile("4500", savings=D("20000"))
    grown = affordability_timeline(profile, rules, D("600000"), date(2026, 10, 16)).rows[0]
    flat = affordability_timeline(profile, frozen, D("600000"), date(2026, 10, 16)).rows[0]
    assert grown.status == "AFFORDABLE" and (flat.earliest_month is None or grown.earliest_month < flat.earliest_month)


# --- 6. property count -------------------------------------------------------------------------------------

@pytest.mark.parametrize(("answer", "mof", "number"), [("It would be my 3rd property", "70.00", 3),
                                                        ("this is my second property", "90.00", 2)])
def test_existing_home_loan_asks_property_count_and_applies_its_margin(client, answer, mof, number) -> None:
    sid = confirmed_payslip(client, {"commitments": [{"type": "home_loan", "monthly": 1200}]})
    asked = say(client, sid, "Can I afford a RM300,000 home?")
    assert _codes(asked)["loan_capacity"] == "NEEDS_CONFIRMATION" and asked["computations"] == []
    assert "2nd property, or your 3rd or later" in asked["reply"]

    body = say(client, sid, answer)
    cap = outputs(body, "loan_capacity")
    assert cap["margin_of_finance_pct"] == mof and cap["property_number"] == number
    assert cap["first_home_incentives_eligible"] is False


# --- 7. narrative numbers all come from the engine JSON ----------------------------------------------------

@pytest.mark.parametrize("messages", [
    [QUESTION + ", I'm 30"],
    [QUESTION, "I'm 30"],
])
def test_narrative_has_no_number_outside_the_engine_json(client, messages) -> None:
    sid = new_session(client)
    for m in messages:
        body = say(client, sid, m)
    assert body["computations"]
    assert _numbers_not_in_engine_json(body["reply"], body["computations"]) == []


def test_narrative_numbers_for_an_affordability_answer(client) -> None:
    body = say(client, new_session(client, "aina"), "Can I afford a RM750,000 condo?")
    assert _numbers_not_in_engine_json(body["reply"], body["computations"]) == []
