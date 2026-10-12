"""Vertex AI Gemini gateway: function calling via google-genai. Logs metadata only, never prompt or response text."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from app.llm.gateway import LLMError, LLMResponse, ToolCall, ToolSpec
from app.observability import get_logger

log = get_logger("lifenest.llm")
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 2


class VertexGeminiGateway:
    name = "vertex-gemini"

    def __init__(self, project: str, location: str, model: str, timeout_seconds: float) -> None:
        if not model:
            raise ValueError("GEMINI_MODEL must be set when LLM_BACKEND=vertex")
        from google import genai

        self._client = genai.Client(vertexai=True, project=project or None, location=location)
        self.model = model
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _contents(messages: list[dict[str, Any]]) -> list[Any]:
        from google.genai import types

        contents: list[Any] = []
        for m in messages:
            if m["role"] == "user":
                contents.append(types.Content(role="user", parts=[types.Part.from_text(text=m["content"])]))
            elif m["role"] == "assistant":
                parts = [types.Part.from_text(text=m["content"])] if m.get("content") else []
                parts += [types.Part(function_call=types.FunctionCall(id=c["id"], name=c["name"], args=c["arguments"]))
                          for c in m.get("tool_calls", [])]
                if parts:
                    contents.append(types.Content(role="model", parts=parts))
            elif m["role"] == "tool":
                part = types.Part(function_response=types.FunctionResponse(
                    id=m["tool_call_id"], name=m["name"], response=m["result"]))
                contents.append(types.Content(role="user", parts=[part]))
        return contents

    async def _call(self, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> Any:
        from google.genai import types

        declarations = [types.FunctionDeclaration(name=t.name, description=t.description,
                                                  parameters_json_schema=t.input_schema) for t in tools]
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=declarations)] if declarations else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.2,
        )
        return await asyncio.wait_for(
            self._client.aio.models.generate_content(model=self.model, contents=self._contents(messages),
                                                     config=config),
            timeout=self.timeout_seconds,
        )

    async def complete(self, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> LLMResponse:
        from google.genai import errors

        start = time.perf_counter()
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await self._call(system, messages, tools)
                break
            except (TimeoutError, errors.APIError) as exc:
                status = getattr(exc, "code", None)
                transient = isinstance(exc, TimeoutError) or status in _RETRYABLE_STATUS
                log.warning("llm_error", provider=self.name, model=self.model, attempt=attempt,
                            error_type=type(exc).__name__, status=status)
                if not transient or attempt == _MAX_RETRIES:
                    raise LLMError(type(exc).__name__) from exc
                await asyncio.sleep(0.5 * 2**attempt)

        calls = [ToolCall(id=fc.id or f"call_{uuid.uuid4().hex[:8]}", name=fc.name or "", arguments=dict(fc.args or {}))
                 for fc in (resp.function_calls or [])]
        candidate = (resp.candidates or [None])[0]
        stop = str(getattr(candidate, "finish_reason", None) or "STOP")
        meta = resp.usage_metadata
        usage = {"prompt_tokens": getattr(meta, "prompt_token_count", 0) or 0,
                 "output_tokens": getattr(meta, "candidates_token_count", 0) or 0}
        log.info("llm_call", provider=self.name, model=self.model, stop_reason=stop, tool_calls=len(calls),
                 latency_ms=round((time.perf_counter() - start) * 1000), **usage)
        text = None if calls else (resp.text or None)
        return LLMResponse(text=text, tool_calls=calls, stop_reason=stop, usage=usage)
