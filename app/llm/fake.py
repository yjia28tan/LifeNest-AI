"""Scripted stand-ins for Gemini so the full flow runs with no API key and no network."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.llm.extractor import ExtractionResult
from app.storage.models import DocumentKind


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
