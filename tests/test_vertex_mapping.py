"""Gemini message mapping found during the first real Vertex run. Offline: builds request contents, never calls Vertex."""

from __future__ import annotations

import pytest

types = pytest.importorskip("google.genai.types")

from app.llm.vertex_gemini import _LOCAL_ID, VertexGeminiGateway  # noqa: E402

contents = VertexGeminiGateway._contents


def test_model_turn_is_replayed_verbatim_with_thought_signature() -> None:
    original = types.Content(role="model", parts=[types.Part(
        function_call=types.FunctionCall(name="compute_dsr", args={}), thought_signature=b"sig-123")])
    out = contents([
        {"role": "user", "content": "What is my DSR?"},
        {"role": "assistant", "content": None, "provider_turn": original,
         "tool_calls": [{"id": f"{_LOCAL_ID}abc", "name": "compute_dsr", "arguments": {}}]},
        {"role": "tool", "tool_call_id": f"{_LOCAL_ID}abc", "name": "compute_dsr", "result": {"ok": True}},
    ])
    assert out[1] is original and out[1].parts[0].thought_signature == b"sig-123"


def test_parallel_tool_results_share_one_user_turn() -> None:
    out = contents([
        {"role": "user", "content": "Can I afford RM750k?"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "a", "name": "compute_dsr", "arguments": {}},
            {"id": "b", "name": "loan_capacity", "arguments": {}}]},
        {"role": "tool", "tool_call_id": "a", "name": "compute_dsr", "result": {"ok": True}},
        {"role": "tool", "tool_call_id": "b", "name": "loan_capacity", "result": {"ok": True}},
        {"role": "user", "content": "And EPF?"},
    ])
    assert [c.role for c in out] == ["user", "model", "user", "user"]
    assert [p.function_response.name for p in out[2].parts] == ["compute_dsr", "loan_capacity"]
    assert out[3].parts[0].text == "And EPF?"


def test_locally_minted_call_ids_are_not_sent_to_gemini() -> None:
    out = contents([
        {"role": "tool", "tool_call_id": f"{_LOCAL_ID}xyz", "name": "compute_dsr", "result": {"ok": True}},
        {"role": "tool", "tool_call_id": "gemini-id-1", "name": "epf_withdrawal", "result": {"ok": True}},
    ])
    ids = [p.function_response.id for p in out[0].parts]
    assert ids == [None, "gemini-id-1"]
