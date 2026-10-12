"""The headline guardrail (spec section 14): no figure in a reply that a tool, a cited source or the user did not supply."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.grounding import allowed_values, check_grounding, collect_numbers, extract_figures
from app.llm.fake import FakeLLM
from app.llm.gateway import LLMResponse, ToolCall
from app.main import create_app
from scripts.demo_chat import QUESTIONS
from tests.test_api_skeleton import make_settings

ALICE = {"Authorization": "Bearer dev:alice"}


def assert_grounded(response: dict[str, Any], user_texts: list[str] = (), profile: dict | None = None) -> None:
    """Every RM amount and percentage in `reply` must match a value from computations, sources or the user."""
    allowed = allowed_values(response["computations"], response["sources"], list(user_texts), profile)
    result = check_grounding(response["reply"], allowed)
    assert result.ok, f"ungrounded figures {result.unmatched} in reply: {response['reply']!r}"


# --- unit: figure extraction and matching -------------------------------------------------------------

def test_extracts_money_and_percentages() -> None:
    figs = {f.text: (f.value, f.decimals, f.multiplier) for f in extract_figures(
        "Max RM520,000 (RM 2,185.95/month), RM520k or RM1.2 million; DSR 20.57% vs 70 %.")}
    assert figs["RM520,000"] == (Decimal("520000"), 0, 1)
    assert figs["RM 2,185.95"] == (Decimal("2185.95"), 2, 1)
    assert figs["RM520k"] == (Decimal("520"), 0, 1000)
    assert figs["RM1.2 million"] == (Decimal("1.2"), 1, 1_000_000)
    assert figs["20.57%"][0] == Decimal("20.57") and figs["70 %"][0] == Decimal("70")


@pytest.mark.parametrize("reply", [
    "Your maximum is RM520,000.", "Your maximum is RM520k.", "Instalment about RM2,186 a month.",
    "Instalment RM2,185.95.", "DSR 20.57%.", "DSR 21%.", "Kumar could reach RM1.2 million.",
])
def test_presentation_rounding_is_tolerated(reply) -> None:
    allowed = collect_numbers({"max": "520000.00", "inst": "2185.95", "dsr": "20.57", "k": "1199000.00"})
    assert check_grounding(reply, allowed).ok


@pytest.mark.parametrize("reply", [
    "You can borrow RM900,000.", "Your maximum is RM521,000.", "Instalment RM2,190.", "DSR 25%.",
])
def test_invented_figures_are_caught(reply) -> None:
    allowed = collect_numbers({"max": "520000.00", "inst": "2185.95", "dsr": "20.57"})
    result = check_grounding(reply, allowed)
    assert not result.ok and result.unmatched


def test_user_supplied_and_source_values_count() -> None:
    allowed = allowed_values([], [{"content": "1% on the first RM100,000 and 0.5% of the loan"}],
                             ["I'm looking at a RM750k condo"], None)
    assert check_grounding("RM750,000 vs RM100,000 at 1% and 0.5%", allowed).ok


# --- runtime check behind GROUNDING_CHECK ---------------------------------------------------------------

def _hallucinating_llm() -> FakeLLM:
    return FakeLLM([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="loan_capacity")]),
        LLMResponse(text="Great news, you can borrow up to RM900,000 at 4.40%."),
    ])


def _client(tmp_path, **settings) -> TestClient:
    return TestClient(create_app(make_settings(tmp_path, **settings)))


def _confirmed_session(c: TestClient) -> str:
    sid = c.post("/sessions", headers=ALICE).json()["session_id"]
    c.post(f"/sessions/{sid}/demo-profile", headers=ALICE, json={"demo_id": "aina"})
    return sid


def test_warn_mode_keeps_reply_but_audits(tmp_path) -> None:
    with _client(tmp_path, grounding_check="warn") as c:
        c.app.state.services.llm = _hallucinating_llm()
        sid = _confirmed_session(c)
        r = c.post(f"/sessions/{sid}/messages", headers=ALICE, json={"message": "how much can I borrow?"}).json()
        assert "RM900,000" in r["reply"]
        assert r["grounding"] == {"status": "flagged", "unmatched": ["RM900,000"]}
        events = asyncio.run(c.app.state.services.repo.list_audit(session_id=sid, event="UNGROUNDED_FIGURE"))
        assert events and events[0].outputs == {"figures": ["RM900,000"]}


def test_enforce_mode_withholds_the_reply(tmp_path) -> None:
    with _client(tmp_path, grounding_check="enforce") as c:
        c.app.state.services.llm = _hallucinating_llm()
        sid = _confirmed_session(c)
        r = c.post(f"/sessions/{sid}/messages", headers=ALICE, json={"message": "how much can I borrow?"}).json()
        assert "900" not in r["reply"] and r["grounding"]["status"] == "withheld"
        assert r["cards"][0]["type"] == "affordability_verdict"
        assert r["cards"][0]["data"]["max_property_price"] == "520000.00"


def test_offline_demo_agent_is_grounded_on_every_journey_question(tmp_path) -> None:
    with _client(tmp_path, grounding_check="enforce") as c:
        sid = _confirmed_session(c)
        asked: list[str] = []
        for q in QUESTIONS:
            asked.append(q)
            r = c.post(f"/sessions/{sid}/messages", headers=ALICE, json={"message": q}).json()
            assert r["grounding"]["status"] == "ok", (q, r["grounding"])
            assert_grounded(r, asked)
