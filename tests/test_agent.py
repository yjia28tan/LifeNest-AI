from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.agent.orchestrator import LLM_FAILURE_REPLY, STEP_LIMIT_REPLY
from app.agent.prompts import render_system_prompt
from app.agent.tools import ToolRegistry
from app.llm.fake import FakeLLM
from app.llm.gateway import LLMError, LLMResponse, ToolCall
from app.main import create_app
from tests.test_api_skeleton import make_settings

ALICE = {"Authorization": "Bearer dev:alice"}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as c:
        yield c


def _session(c, demo: str | None = "aina") -> str:
    sid = c.post("/sessions", headers=ALICE).json()["session_id"]
    if demo:
        c.post(f"/sessions/{sid}/demo-profile", headers=ALICE, json={"demo_id": demo})
    return sid


def _script(c, *responses: LLMResponse) -> FakeLLM:
    llm = FakeLLM(list(responses))
    c.app.state.services.llm = llm
    return llm


def _say(c, sid: str, message: str = "go") -> dict:
    resp = c.post(f"/sessions/{sid}/messages", headers=ALICE, json={"message": message})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _calls(*calls: tuple[str, dict]) -> LLMResponse:
    return LLMResponse(tool_calls=[ToolCall(id=f"c{i}", name=n, arguments=a) for i, (n, a) in enumerate(calls)])


def _results(llm: FakeLLM) -> dict[str, dict]:
    return {m["name"]: m["result"] for call in llm.calls for m in call["messages"] if m["role"] == "tool"}


# --- gating -------------------------------------------------------------------------------------

def test_computation_tools_are_gated_until_confirmed(client) -> None:
    sid = _session(client, demo=None)
    llm = _script(client, _calls(("loan_capacity", {}), ("search_regulation", {"query": "debt service ratio"})),
                  LLMResponse(text="Please upload your payslip and EPF statement."))
    r = _say(client, sid)
    results = _results(llm)
    assert results["loan_capacity"] == {"ok": False, "error_code": "PROFILE_NOT_CONFIRMED",
                                        "message": results["loan_capacity"]["message"]}
    assert results["search_regulation"]["ok"] is True
    assert r["computations"] == [] and r["profile_status"] == "EMPTY"
    assert [t["error_code"] for t in r["tool_trace"]] == ["PROFILE_NOT_CONFIRMED", None]


def test_extracted_but_unreviewed_profile_is_still_gated(client) -> None:
    sid = _session(client, demo=None)
    repo = client.app.state.services.repo
    session = asyncio.run(repo.get_session(sid, "alice"))
    asyncio.run(repo.update_session(session, profile_status="EXTRACTED"))
    llm = _script(client, _calls(("compute_dsr", {})), LLMResponse(text="ok"))
    _say(client, sid)
    assert _results(llm)["compute_dsr"]["error_code"] == "PROFILE_NOT_CONFIRMED"


# --- tool contract ----------------------------------------------------------------------------------

def test_tool_outputs_follow_the_contract(client) -> None:
    sid = _session(client)
    _script(client, _calls(("loan_capacity", {}), ("upfront_costs", {"property_price": 750000})),
            LLMResponse(text="done"))
    r = _say(client, sid)
    cap, costs = (c["outputs"] for c in r["computations"])
    assert cap["ok"] and cap["currency"] == "MYR" and cap["rules_version"] == "my-2026-07"
    assert cap["max_property_price"] == "520000.00" and cap["limiting_factor"] == "CASH_UPFRONT"
    assert costs["inputs_echo"] == {"property_price": 750000.0}
    assert costs["total_cash_needed"] == "107000.00"
    assert cap["unverified_inputs"] == ["savings"]  # demo savings are user-stated

    events = asyncio.run(client.app.state.services.repo.list_audit(session_id=sid))
    assert [e.event for e in events].count("TOOL_CALL") == 2
    computed = [e for e in events if e.event == "COMPUTATION"]
    assert {e.tool for e in computed} == {"loan_capacity", "upfront_costs"}
    assert all(e.rules_version == "my-2026-07" for e in computed)


@pytest.mark.parametrize(("name", "args", "code"), [
    ("upfront_costs", {"property_price": -5}, "VALIDATION_ERROR"),
    ("upfront_costs", {}, "VALIDATION_ERROR"),
    ("loan_capacity", {"user_id": "bob"}, "VALIDATION_ERROR"),
    ("simulate_scenario", {"scenario": "lottery_win", "property_price": 500000}, "VALIDATION_ERROR"),
    ("simulate_scenario", {"scenario": "first_child", "property_price": 500000, "params": {"bogus": 1}},
     "VALIDATION_ERROR"),
    ("location_cost", {"area": "Atlantis", "workplace_area": "Kajang", "transport_mode": "car"}, "UNKNOWN_AREA"),
    ("approve_loan", {}, "UNKNOWN_TOOL"),
])
def test_bad_calls_return_errors_the_model_can_act_on(client, name, args, code) -> None:
    sid = _session(client)
    llm = _script(client, _calls((name, args)), LLMResponse(text="Sorry, could you clarify?"))
    r = _say(client, sid)
    result = _results(llm)[name]
    assert result["ok"] is False and result["error_code"] == code and result["message"]
    assert r["reply"] == "Sorry, could you clarify?"


def test_stock_can_never_exceed_computed_capacity(client) -> None:
    sid = _session(client)
    llm = _script(client, _calls(("match_affordable_stock", {"max_price": 750000, "area": "Kajang"})),
                  _calls(("match_affordable_stock", {"max_price": 520000, "area": "kajang"})),
                  LLMResponse(text="Here are some homes."))
    r = _say(client, sid)
    first = llm.calls[1]["messages"][-1]["result"]
    assert first["error_code"] == "EXCEEDS_COMPUTED_CAPACITY" and first["computed_max_property_price"] == "520000.00"
    listings = r["computations"][0]["outputs"]["listings"]
    assert listings and all(float(x["price"]) <= 520000 and x["area"] == "Kajang" for x in listings)


def test_scenario_projection_goes_to_ui_not_to_the_model(client) -> None:
    sid = _session(client)
    llm = _script(client, _calls(("simulate_scenario", {"scenario": "first_child", "property_price": 520000,
                                                        "start_month": 36})), LLMResponse(text="ok"))
    r = _say(client, sid)
    to_model = _results(llm)["simulate_scenario"]
    assert "projection" not in to_model and to_model["projection_points"] == 121
    assert to_model["status"] == "BREAKS" and to_model["break_month"] == 36
    card = next(c for c in r["cards"] if c["type"] == "scenario_result")
    assert len(card["data"]["projection"]) == 121


def test_tool_calls_in_one_step_run_concurrently(client, monkeypatch) -> None:
    import app.agent.tools as tools

    def slow_dsr(profile, rules):
        time.sleep(0.3)
        return original(profile, rules)

    original = tools.compute_dsr
    monkeypatch.setattr(tools, "compute_dsr", slow_dsr)
    sid = _session(client)
    _script(client, _calls(*[("compute_dsr", {})] * 3), LLMResponse(text="ok"))
    start = time.perf_counter()
    r = _say(client, sid)
    assert len(r["computations"]) == 3
    assert time.perf_counter() - start < 0.8


def test_tool_timeout_is_contained(tmp_path, monkeypatch) -> None:
    import app.agent.tools as tools

    monkeypatch.setattr(tools, "compute_dsr", lambda profile, rules: time.sleep(0.5))
    with TestClient(create_app(make_settings(tmp_path, tool_timeout_seconds=0.05))) as c:
        sid = _session(c)
        _script(c, _calls(("compute_dsr", {})), LLMResponse(text="I couldn't compute that."))
        r = _say(c, sid)
    assert r["tool_trace"][0]["error_code"] == "TOOL_TIMEOUT" and r["computations"] == []


# --- loop behaviour -------------------------------------------------------------------------------------

def test_step_limit_gives_a_friendly_fallback(client) -> None:
    sid = _session(client)
    llm = _script(client, *[_calls(("compute_dsr", {}))] * 10)
    r = _say(client, sid)
    assert r["reply"] == STEP_LIMIT_REPLY
    max_steps = client.app.state.services.settings.agent_max_steps
    assert len(r["tool_trace"]) == max_steps - 1
    # The final step offers no tools, so a real model has to answer from what it already has.
    assert llm.calls[-1]["tools"] == [] and all(c["tools"] for c in llm.calls[:-1])


def test_llm_failure_gives_a_friendly_fallback(client) -> None:
    def broken(system, messages, tools):
        raise LLMError("ServiceUnavailable")

    client.app.state.services.llm = FakeLLM(broken)
    sid = _session(client)
    r = _say(client, sid)
    assert r["reply"] == LLM_FAILURE_REPLY
    events = asyncio.run(client.app.state.services.repo.list_audit(session_id=sid, event="LLM_ERROR"))
    assert events and events[0].outcome == "ServiceUnavailable"


def test_turn_is_persisted_with_trace(client) -> None:
    sid = _session(client)
    _script(client, _calls(("compute_dsr", {})), LLMResponse(text="Your DSR is 20.57%."))
    r = _say(client, sid, "what is my DSR?")
    assert r["disclaimer"] == "Indicative only. Not a lending decision."
    history = client.get(f"/sessions/{sid}/messages", headers=ALICE).json()["messages"]
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[1]["tool_trace"][0]["tool"] == "compute_dsr"
    assert history[1]["computations"][0]["outputs"]["dsr_pct"] == "20.57"
    assert history[1]["message_id"] == r["message_id"]


def test_history_is_sent_to_the_model_on_the_next_turn(client) -> None:
    sid = _session(client)
    llm = _script(client, LLMResponse(text="first answer"), LLMResponse(text="second answer"))
    _say(client, sid, "first question")
    _say(client, sid, "second question")
    sent = [m["content"] for m in llm.calls[1]["messages"]]
    assert sent == ["first question", "first answer", "second question"]


@pytest.mark.parametrize("message", ["", "x" * 2001])
def test_message_length_validated(client, message) -> None:
    sid = _session(client)
    resp = client.post(f"/sessions/{sid}/messages", headers=ALICE, json={"message": message})
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_other_users_cannot_chat_in_a_session(client) -> None:
    sid = _session(client)
    resp = client.post(f"/sessions/{sid}/messages", headers={"Authorization": "Bearer dev:bob"},
                       json={"message": "hi"})
    assert resp.status_code == 404


# --- prompt and schemas ---------------------------------------------------------------------------------

def test_system_prompt_carries_state_but_never_the_profile() -> None:
    prompt = render_system_prompt(timezone="Asia/Kuala_Lumpur", profile_status="EXTRACTED",
                                  rules_version="my-2026-07")
    assert "Profile status: EXTRACTED" in prompt and "Confirmed profile" not in prompt
    assert "Rules version: my-2026-07" in prompt and "Only state numbers that appear in a tool result" in prompt
    assert "needs_confirmation" in prompt and "affordability_timeline" in prompt


def test_tool_schemas_are_function_calling_friendly() -> None:
    specs = {s.name: s.input_schema for s in ToolRegistry().specs()}
    assert set(specs) == {"compute_dsr", "loan_capacity", "epf_withdrawal", "upfront_costs", "location_cost",
                          "simulate_scenario", "plan_path_to_approval", "match_affordable_stock",
                          "search_regulation", "record_stated_figures", "affordability_timeline",
                          "confirm_stored_figures"}
    text = repr(specs)
    assert "anyOf" not in text and "'title'" not in text and "user_id" not in text
    assert specs["upfront_costs"]["required"] == ["property_price"]
    assert specs["loan_capacity"]["properties"]["tenure_years"]["type"] == "integer"
