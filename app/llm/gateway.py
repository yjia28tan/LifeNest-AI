"""Provider-neutral LLM contract (spec section 6.1).

Conversation messages are plain dicts:
  {"role": "user", "content": str}
  {"role": "assistant", "content": str | None, "tool_calls": [{"id", "name", "arguments"}], "provider_turn": Any}
  {"role": "tool", "tool_call_id": str, "name": str, "result": dict}

`provider_turn` is the gateway's own opaque copy of the model turn (e.g. Gemini content with thought signatures),
replayed verbatim on the next call. Other gateways ignore it.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = {}


class LLMResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    text: str | None = None
    tool_calls: list[ToolCall] = []
    stop_reason: str = "stop"
    usage: dict[str, int] = {}
    provider_turn: Any = None


class LLMError(Exception):
    pass


class LLMGateway(Protocol):
    name: str

    async def complete(self, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> LLMResponse: ...
