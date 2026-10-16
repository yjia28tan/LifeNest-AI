"""Spec section 15 acceptance journey, end to end through the HTTP API with FakeExtractor + the offline FakeLLM.

Asserts on tool calls, arguments, gating and payloads — never on exact wording. Expected figures come from the
engine itself, so the test proves the API reproduces the engine rather than hard-coding a number.

Step 6 follows the agreed "honest engine" story: at the computed price a first child BREAKS for Aina (the spec draft
said SAFE; the spec's own parameters leave her RM222/month of headroom).
"""

from __future__ import annotations

import asyncio
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from app.config import ROOT
from app.engine.loan_capacity import loan_capacity
from app.engine.models import Profile
from app.engine.path_to_approval import plan_path_to_approval
from app.engine.rules import load_rules
from app.engine.scenarios import simulate_scenario
from app.llm.fake import FakeLLM
from app.llm.gateway import LLMResponse, ToolCall
from app.main import create_app
from tests.test_api_skeleton import make_settings
from tests.test_grounding import assert_grounded

DOCS = ROOT / "data" / "mock_documents"
AINA = {"Authorization": "Bearer dev:aina"}
DREAM = Decimal("750000")


class Journey:
    def __init__(self, client: TestClient) -> None:
        self.c = client
        self.sid = ""
        self.profile: Profile | None = None
        self.profile_json: dict[str, Any] | None = None
        self.asked: list[str] = []
        self.doc_ids: list[str] = []
        self.step3: dict[str, Any] = {}

    @property
    def services(self) -> Any:
        return self.c.app.state.services

    def say(self, message: str) -> dict[str, Any]:
        self.asked.append(message)
        resp = self.c.post(f"/sessions/{self.sid}/messages", headers=AINA, json={"message": message})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["disclaimer"] == "Indicative only. Not a lending decision."
        assert_grounded(body, self.asked, self.profile_json)
        return body

    def outputs(self, body: dict[str, Any], tool: str) -> list[dict[str, Any]]:
        return [c["outputs"] for c in body["computations"] if c["tool"] == tool]

    def audit(self, event: str) -> list[Any]:
        return asyncio.run(self.services.repo.list_audit(session_id=self.sid, event=event))


@pytest.fixture(scope="module")
def journey(tmp_path_factory):
    settings = make_settings(tmp_path_factory.mktemp("journey"), grounding_check="enforce")
    with TestClient(create_app(settings)) as client:
        j = Journey(client)
        j.sid = client.post("/sessions", headers=AINA).json()["session_id"]
        yield j


def tools(body: dict[str, Any]) -> list[str]:
    return [t["tool"] for t in body["tool_trace"]]


class TestJourney:
    rules = load_rules()

    def test_1_upload_and_extract_proposes_without_computing(self, journey: Journey) -> None:
        c, sid = journey.c, journey.sid
        expected = {"aina_payslip.png": "payslip", "aina_epf.png": "epf_statement",
                    "aina_loan_statements.png": "loan_statement"}
        proposed: dict[str, Any] = {}
        for filename, kind in expected.items():
            up = c.post(f"/sessions/{sid}/documents/upload-url", headers=AINA,
                        json={"kind": kind, "content_type": "image/png"}).json()
            url = urlparse(up["upload_url"])
            assert c.put(f"{url.path}?{url.query}", content=(DOCS / filename).read_bytes(),
                         headers={"Content-Type": "image/png"}).status_code == 204
            body = c.post(f"/sessions/{sid}/documents/{up['doc_id']}/extract", headers=AINA).json()
            assert body["kind_detected"] == kind
            proposed.update({f["path"]: f["value"] for f in body["fields"]})
            journey.doc_ids.append(up["doc_id"])

        assert proposed["income.gross_monthly"] == "6200.00"
        assert proposed["income.net_monthly"] == "5298.35"
        assert proposed["epf.akaun_sejahtera"] == "18400.00"
        assert proposed["person.age"] == 29
        assert [x["monthly"] for x in proposed["commitments"]] == ["780.00", "310.00"]
        assert c.get(f"/sessions/{sid}", headers=AINA).json()["profile_status"] == "EXTRACTED"
        assert journey.audit("COMPUTATION") == []

    def test_2_review_gate_blocks_computation_until_confirmed(self, journey: Journey) -> None:
        c, sid = journey.c, journey.sid
        default_llm = journey.services.llm
        scripted = FakeLLM([LLMResponse(tool_calls=[ToolCall(id="c1", name="loan_capacity")]),
                            LLMResponse(text="Please review the fields I extracted first.")])
        journey.services.llm = scripted
        try:
            body = journey.say("What's my maximum loan?")
        finally:
            journey.services.llm = default_llm
        assert body["tool_trace"] == [{"tool": "loan_capacity", "status": "error", "latency_ms": body["tool_trace"][0]["latency_ms"],
                                       "error_code": "PROFILE_NOT_CONFIRMED"}]
        assert body["computations"] == []

        pending = asyncio.run(journey.services.repo.list_pending_actions(sid))
        assert len(pending) == 3
        for action in sorted(pending, key=lambda a: a.created_at):
            corrections = {"savings": 54790} if action.proposed_fields["kind"] == "loan_statement" else {}
            resp = c.post(f"/actions/{action.action_id}/confirm", headers=AINA, json={"corrections": corrections})
            assert resp.status_code == 200, resp.text
        final = resp.json()
        assert final["profile_status"] == "CONFIRMED" and final["missing_fields"] == []
        assert final["field_provenance"]["income.gross_monthly"]["corrected_by_user"] is False
        assert final["field_provenance"]["savings"]["source"] == "user_stated"
        journey.profile_json = final["profile"]
        journey.profile = Profile.model_validate(final["profile"])

    def test_3_the_honest_number(self, journey: Journey) -> None:
        body = journey.say("I'm looking at a RM750,000 condo in Cheras, can I afford it?")
        assert tools(body) == ["compute_dsr", "loan_capacity", "upfront_costs", "plan_path_to_approval"]
        engine = loan_capacity(journey.profile, self.rules)
        [cap] = journey.outputs(body, "loan_capacity")
        [costs] = journey.outputs(body, "upfront_costs")
        assert Decimal(cap["max_property_price"]) == engine.max_property_price < DREAM
        assert cap["limiting_factor"] == engine.limiting_factor == "CASH_UPFRONT"
        assert costs["inputs_echo"] == {"property_price": 750000.0}
        assert Decimal(costs["shortfall_after_epf"]) > 0

        verdict = next(card for card in body["cards"] if card["type"] == "affordability_verdict")["data"]
        assert verdict["max_property_price"] == cap["max_property_price"] and verdict["named_price"] == "750000"
        assert body["reply"].startswith("The cash needed upfront is the binding limit")
        assert "cannot afford" not in body["reply"].lower()
        assert f"RM{engine.max_property_price:,.0f}" in body["reply"] and "cash needed upfront" in body["reply"]
        assert "Tenure: 35 years is the maximum tenure" in body["reply"]
        journey.step3 = {"loan_capacity": cap, "compute_dsr": journey.outputs(body, "compute_dsr")[0]}

    def test_4_why_cites_regulations(self, journey: Journey) -> None:
        body = journey.say("Why so much lower than I thought?")
        assert tools(body) == ["search_regulation", "search_regulation"]
        assert {s["doc_id"] for s in body["sources"]} >= {"dsr-responsible-lending", "epf-akaun-sejahtera"}
        citations = [c["data"] for c in body["cards"] if c["type"] == "regulation_citation"]
        assert citations and all(c["title"] and c["section"] for c in citations)
        assert "Debt service ratio and responsible lending" in body["reply"]

    def test_5_epf_reality(self, journey: Journey) -> None:
        body = journey.say("How much can I take from EPF?")
        assert tools(body) == ["epf_withdrawal"]
        [epf] = journey.outputs(body, "epf_withdrawal")
        assert epf["akaun_sejahtera_balance"] == epf["eligible_withdrawal"] == "18400.00"
        assert "May 2024" in epf["rules_note"] and "15%" in epf["rules_note"]
        assert "RM18,400" in body["reply"]

    def test_6_stress_test_a_child_in_three_years(self, journey: Journey) -> None:
        body = journey.say("What if we have a child in three years?")
        assert tools(body) == ["loan_capacity", "simulate_scenario", "simulate_scenario"]
        capacity = loan_capacity(journey.profile, self.rules).max_property_price
        at_capacity, at_dream = journey.outputs(body, "simulate_scenario")
        assert at_capacity["inputs_echo"] == {"scenario": "first_child", "property_price": float(capacity),
                                              "start_month": 36, "params": {}}
        assert at_dream["inputs_echo"]["property_price"] == 750000.0

        engine = simulate_scenario(journey.profile, self.rules, "first_child", capacity, start_month=36)
        assert (at_capacity["status"], at_capacity["break_month"]) == (engine.status, engine.break_month)
        assert at_capacity["status"] != "SAFE"  # honest-engine story, see module docstring
        assert at_dream["status"] == "BREAKS" and at_dream["break_month"] is not None
        assert Decimal(at_dream["monthly_shortfall"]) > Decimal(at_capacity["monthly_shortfall"]) > 0

        assert f"month {at_capacity['break_month']}" in body["reply"]
        shortfall = (Decimal(at_capacity["monthly_shortfall"]) / 100).quantize(Decimal(1), ROUND_HALF_UP) * 100
        assert f"RM{shortfall:,.0f}" in body["reply"]  # narrative rounds to RM100; the card keeps the exact value
        charts = [c["data"]["projection"] for c in body["cards"] if c["type"] == "scenario_result"]
        assert len(charts) == 2 and all(len(p) == self.rules.projection.horizon_months + 1 for p in charts)

    def test_7_path_to_yes(self, journey: Journey) -> None:
        body = journey.say("What would I need to do to reach RM650,000?")
        assert tools(body) == ["plan_path_to_approval"]
        [plan] = journey.outputs(body, "plan_path_to_approval")
        engine = plan_path_to_approval(journey.profile, self.rules, Decimal("650000"))
        assert plan["eligible_from_month"] == engine.eligible_from_month is not None
        assert [(a["action"], a["by_month"]) for a in plan["actions"]] == [(a.action, a.by_month) for a in engine.actions]
        assert plan["actions"][0]["commitment_type"] == "study_loan"
        assert all(isinstance(a["by_month"], int) for a in plan["actions"])
        assert f"month {engine.eligible_from_month}" in body["reply"]

    def test_8_stock_is_filtered_by_the_affordable_price(self, journey: Journey) -> None:
        capacity = loan_capacity(journey.profile, self.rules).max_property_price
        body = journey.say("Show me what I can actually afford near Kajang.")
        assert "match_affordable_stock" in tools(body)
        [stock] = journey.outputs(body, "match_affordable_stock")
        assert Decimal(stock["inputs_echo"]["max_price"]) <= capacity and stock["area"] == "Kajang"
        assert stock["listings"] and all(Decimal(x["price"]) <= capacity for x in stock["listings"])

        default_llm = journey.services.llm
        scripted = FakeLLM([LLMResponse(tool_calls=[ToolCall(id="c1", name="match_affordable_stock",
                                                              arguments={"max_price": 750000})]),
                            LLMResponse(text="I can only show homes at or below your computed price.")])
        journey.services.llm = scripted
        try:
            rejected = journey.say("Show me RM750,000 condos instead.")
        finally:
            journey.services.llm = default_llm
        assert rejected["tool_trace"][0]["error_code"] == "EXCEEDS_COMPUTED_CAPACITY"
        assert rejected["computations"] == []

    def test_9_same_question_in_bahasa_melayu(self, journey: Journey) -> None:
        body = journey.say("Saya tengok kondo RM750,000 di Cheras, boleh saya mampu beli?")
        assert tools(body) == ["compute_dsr", "loan_capacity", "upfront_costs", "plan_path_to_approval"]
        assert body["reply"].startswith("Wang tunai pendahuluan") and "harga maksimum" in body["reply"]
        same = {k: v for k, v in journey.outputs(body, "loan_capacity")[0].items() if k != "inputs_echo"}
        before = {k: v for k, v in journey.step3["loan_capacity"].items() if k != "inputs_echo"}
        assert same == before
        assert journey.outputs(body, "compute_dsr")[0] == journey.step3["compute_dsr"]

    def test_10_purge_removes_everything(self, journey: Journey, tmp_path_factory) -> None:
        c, sid = journey.c, journey.sid
        upload_dir = journey.services.settings.local_upload_dir / sid
        assert upload_dir.exists() and len(list(upload_dir.iterdir())) == 3

        resp = c.delete(f"/sessions/{sid}", headers=AINA)
        assert resp.status_code == 200, resp.text
        # pending_actions counts every review record (applied ones too): they hold the proposed field values.
        assert resp.json() == {"session_id": sid, "status": "purged", "files_deleted": 3, "documents": 3,
                               "messages": len(journey.asked) * 2, "profile": 1, "pending_actions": 3}

        assert not upload_dir.exists()
        assert c.get(f"/sessions/{sid}", headers=AINA).status_code == 404
        assert c.get(f"/sessions/{sid}/messages", headers=AINA).status_code == 404
        assert c.delete(f"/sessions/{sid}", headers=AINA).status_code == 404
        repo = journey.services.repo
        assert asyncio.run(repo.get_profile(sid, "aina")) is None
        assert asyncio.run(repo.list_messages(sid)) == []
        purged = journey.audit("DOCUMENT_PURGED")
        assert sorted(e.outputs["doc_id"] for e in purged) == sorted(journey.doc_ids)
        assert journey.audit("SESSION_PURGED")


def test_another_user_cannot_purge_someone_elses_session(tmp_path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as c:
        sid = c.post("/sessions", headers=AINA).json()["session_id"]
        resp = c.delete(f"/sessions/{sid}", headers={"Authorization": "Bearer dev:mallory"})
        assert resp.status_code == 404 and resp.json()["error"]["code"] == "SESSION_NOT_FOUND"
        assert c.get(f"/sessions/{sid}", headers=AINA).status_code == 200
