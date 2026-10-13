"""Scripted stand-ins for Gemini so the full flow runs with no API key and no network."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.llm import demo_planner
from app.llm.extractor import ExtractionResult
from app.llm.gateway import LLMResponse, ToolSpec
from app.storage.models import DocumentKind

Script = Callable[[str, list[dict[str, Any]], list[ToolSpec]], LLMResponse]


class FakeLLM:
    """LLMGateway stand-in. Pass a list of responses (played in order) or a function for scripted tests; with no
    script it uses the offline demo planner. Every call is recorded for assertions."""

    name = "fake"

    def __init__(self, script: list[LLMResponse] | Script | None = None) -> None:
        self._queue = list(script) if isinstance(script, list) else None
        self._fn = None if isinstance(script, list) else script
        self.calls: list[dict[str, Any]] = []

    async def complete(self, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> LLMResponse:
        self.calls.append({"system": system, "messages": copy.deepcopy(messages), "tools": [t.name for t in tools]})
        if self._queue is not None:
            return self._queue.pop(0) if self._queue else LLMResponse(text="")
        if self._fn is not None:
            return self._fn(system, messages, tools)
        return demo_planner.plan(messages)


class FakeExtractor:
    """Returns the scripted extraction for a synthetic document, matched by SHA-256 of its bytes.

    Unknown bytes are reported as unreadable rather than guessed at.
    """

    def __init__(self, documents_dir: Path) -> None:
        fixtures = json.loads((documents_dir / "extractions.json").read_text(encoding="utf-8"))["documents"]
        self._by_hash: dict[str, ExtractionResult] = {}
        for name, result in fixtures.items():
            path = documents_dir / name
            if path.exists():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                self._by_hash[digest] = ExtractionResult.model_validate(result)

    async def extract(self, data: bytes, content_type: str, kind_hint: DocumentKind) -> ExtractionResult:
        found = self._by_hash.get(hashlib.sha256(data).hexdigest())
        if found is None:
            return ExtractionResult(kind_detected=kind_hint, readable=False, fields=[])
        return found.model_copy(deep=True)
