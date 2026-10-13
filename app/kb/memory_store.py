"""Offline KB backend: loads data/kb_embeddings.json and ranks by NumPy cosine similarity."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from app.kb.store import KBChunk, KBResult


class InMemoryKBStore:
    def __init__(self, chunks: list[KBChunk], embeddings: np.ndarray, embedding_model: str) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings length mismatch")
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        self._matrix = embeddings / np.where(norms == 0, 1, norms)
        self._chunks = chunks
        self.embedding_model = embedding_model

    @classmethod
    def from_file(cls, path: Path) -> InMemoryKBStore:
        if not path.exists():
            raise FileNotFoundError(f"{path} not found; run scripts/ingest_kb.py first")
        raw = json.loads(path.read_text(encoding="utf-8"))
        chunks = [KBChunk.model_validate({k: v for k, v in c.items() if k != "embedding"}) for c in raw["chunks"]]
        matrix = np.array([c["embedding"] for c in raw["chunks"]], dtype=np.float64)
        return cls(chunks, matrix, raw["embedding_model"])

    async def search(self, query_vector: list[float], top_k: int, categories: Sequence[str] | None) -> list[KBResult]:
        q = np.asarray(query_vector, dtype=np.float64)
        q = q / (np.linalg.norm(q) or 1.0)
        scores = self._matrix @ q
        allowed = None if not categories else {c.upper() for c in categories}
        order = [i for i in np.argsort(-scores) if allowed is None or self._chunks[i].category in allowed]
        return [
            KBResult(**self._chunks[i].model_dump(exclude={"chunk_id"}), score=round(float(scores[i]), 4))
            for i in order[:top_k]
        ]

    async def ping(self) -> bool:
        return True
