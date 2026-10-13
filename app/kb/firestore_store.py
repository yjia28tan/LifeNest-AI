"""Production KB backend: Firestore vector search (find_nearest) over kb_chunks. Not supported by the emulator."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from google.cloud.firestore_v1.vector import Vector

from app.kb.store import KBChunk, KBResult

COLLECTION = "kb_chunks"
_DISTANCE_FIELD = "vector_distance"


class FirestoreKBStore:
    def __init__(self, project: str | None, database: str, embedding_model: str) -> None:
        self.client = firestore.AsyncClient(project=project or None, database=database)
        self.embedding_model = embedding_model

    async def search(self, query_vector: list[float], top_k: int, categories: Sequence[str] | None) -> list[KBResult]:
        query: Any = self.client.collection(COLLECTION)
        if categories:
            query = query.where(filter=FieldFilter("category", "in", [c.upper() for c in categories]))
        vector_query = query.find_nearest(
            vector_field="embedding",
            query_vector=Vector(query_vector),
            distance_measure=DistanceMeasure.COSINE,
            limit=top_k,
            distance_result_field=_DISTANCE_FIELD,
        )
        results = []
        async for snap in vector_query.stream():
            doc = snap.to_dict()
            results.append(KBResult(
                doc_id=doc["doc_id"], title=doc["title"], section=doc["section"], category=doc["category"],
                content=doc["content"], score=round(1.0 - float(doc[_DISTANCE_FIELD]), 4),
            ))
        return results

    async def upsert(self, chunks: list[KBChunk], embeddings: list[list[float]]) -> dict[str, int]:
        """Idempotent on chunk_id (doc_id + section); removes stale chunks of re-ingested documents."""
        coll = self.client.collection(COLLECTION)
        keep = {c.chunk_id for c in chunks}
        stale = 0
        for doc_id in {c.doc_id for c in chunks}:
            async for snap in coll.where(filter=FieldFilter("doc_id", "==", doc_id)).stream():
                if snap.id not in keep:
                    await snap.reference.delete()
                    stale += 1
        for chunk, emb in zip(chunks, embeddings, strict=True):
            await coll.document(chunk.chunk_id).set({
                **chunk.model_dump(), "embedding": Vector(emb), "embedding_model": self.embedding_model,
            })
        return {"written": len(chunks), "stale_deleted": stale}

    async def ping(self) -> bool:
        try:
            await asyncio.wait_for(self.client.collection(COLLECTION).limit(1).get(), timeout=3)
            return True
        except Exception:
            return False
