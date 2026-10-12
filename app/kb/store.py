"""KBStore port and the search service used by the `search_regulation` tool (spec section 7.3)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from app.kb.embeddings import Embedder

QUERY_MIN_CHARS, QUERY_MAX_CHARS = 3, 300
MAX_TOP_K = 10


class KBChunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: str
    doc_id: str
    title: str
    section: str
    category: str
    content: str

    def embedding_text(self) -> str:
        return f"{self.title}. {self.section}. {self.content}"


class KBResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    doc_id: str
    title: str
    section: str
    category: str
    content: str
    score: float


class KBStore(Protocol):
    embedding_model: str

    async def search(self, query_vector: list[float], top_k: int, categories: Sequence[str] | None) -> list[KBResult]: ...
    async def ping(self) -> bool: ...


class KnowledgeBase:
    def __init__(self, embedder: Embedder, store: KBStore, min_score: float) -> None:
        if store.embedding_model != embedder.model_name:
            raise ValueError(
                f"KB was embedded with {store.embedding_model!r} but queries use {embedder.model_name!r}; "
                "re-run scripts/ingest_kb.py with the current settings"
            )
        self.embedder = embedder
        self.store = store
        self.min_score = min_score

    async def search(self, query: str, categories: Sequence[str] | None = None, top_k: int = 4) -> dict[str, Any]:
        query = query.strip()
        if not QUERY_MIN_CHARS <= len(query) <= QUERY_MAX_CHARS:
            raise ValueError(f"query must be {QUERY_MIN_CHARS}-{QUERY_MAX_CHARS} characters")
        top_k = max(1, min(top_k, MAX_TOP_K))
        vector = await self.embedder.embed_query(query)
        hits = [h for h in await self.store.search(vector, top_k, categories) if h.score >= self.min_score]
        if not hits:
            return {"ok": True, "results": [], "note": "no relevant regulation found"}
        return {"ok": True, "results": [h.model_dump() for h in hits]}
