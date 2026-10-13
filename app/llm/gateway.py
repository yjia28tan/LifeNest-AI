"""Provider-neutral LLM contract (spec section 6.1).

Conversation messages are plain dicts:
  {"role": "user", "content": str}
  {"role": "assistant", "content": str | None, "tool_calls": [{"id", "name", "arguments"}]}
  {"role": "tool", "tool_call_id": str, "name": str, "result": dict}
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = {}


class LLMResponse(BaseModel):
    text: str | None = None
    tool_calls: list[ToolCall] = []
    stop_reason: str = "stop"
    usage: dict[str, int] = {}


class LLMError(Exception):
    pass


class LLMGateway(Protocol):
    name: str

    async def complete(self, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> LLMResponse: ...
