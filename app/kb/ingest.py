"""Chunk + embed knowledge/*.md, write data/kb_embeddings.json and optionally Firestore kb_chunks. Idempotent."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.config import Settings
from app.kb import build_embedder
from app.kb.chunking import load_knowledge
from app.kb.embeddings import HashingEmbedder

_PRECISION = 6


async def ingest(settings: Settings, write_firestore: bool) -> dict[str, Any]:
    chunks = load_knowledge(settings.knowledge_dir)
    texts = [c.embedding_text() for c in chunks]
    embedder = build_embedder(settings)
    if isinstance(embedder, HashingEmbedder):
        embedder = HashingEmbedder.fit(texts, settings.embedding_dim)
    embeddings = await embedder.embed_documents(texts)
    if any(len(e) != embedder.dim for e in embeddings):
        raise ValueError(f"embedding size does not match EMBEDDING_DIM={embedder.dim}")

    payload = {
        "embedding_model": embedder.model_name,
        "embedding_dim": embedder.dim,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "chunks": [
            {**c.model_dump(), "embedding": [round(v, _PRECISION) for v in e]}
            for c, e in zip(chunks, embeddings, strict=True)
        ],
    }
    if isinstance(embedder, HashingEmbedder):
        payload["idf"] = embedder.idf
    settings.kb_embeddings_path.parent.mkdir(parents=True, exist_ok=True)
    settings.kb_embeddings_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    summary: dict[str, Any] = {
        "documents": len({c.doc_id for c in chunks}),
        "chunks": len(chunks),
        "embedding_model": embedder.model_name,
        "json_path": str(settings.kb_embeddings_path),
    }
    if write_firestore:
        from app.kb.firestore_store import FirestoreKBStore

        store = FirestoreKBStore(settings.google_cloud_project, settings.firestore_database, embedder.model_name)
        summary["firestore"] = await store.upsert(chunks, embeddings)
    return summary
