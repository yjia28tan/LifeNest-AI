"""Knowledge-base backend selection (KB_BACKEND) and embedder selection (follows LLM_BACKEND)."""

from __future__ import annotations

import json

from app.config import Settings
from app.kb.embeddings import Embedder, HashingEmbedder, VertexEmbedder
from app.kb.memory_store import InMemoryKBStore
from app.kb.store import KBStore, KnowledgeBase


def build_embedder(settings: Settings) -> Embedder:
    if settings.llm_backend == "vertex":
        return VertexEmbedder(
            settings.google_cloud_project, settings.google_cloud_location,
            settings.embedding_model, settings.embedding_dim,
        )
    # The offline embedder's IDF weights are fitted at ingest and shipped inside kb_embeddings.json.
    idf: dict[str, float] = {}
    if settings.kb_embeddings_path.exists():
        idf = json.loads(settings.kb_embeddings_path.read_text(encoding="utf-8")).get("idf", {})
    return HashingEmbedder(settings.embedding_dim, idf)


def build_kb_store(settings: Settings, embedding_model: str) -> KBStore:
    if settings.kb_backend == "firestore":
        from app.kb.firestore_store import FirestoreKBStore

        return FirestoreKBStore(settings.google_cloud_project, settings.firestore_database, embedding_model)
    return InMemoryKBStore.from_file(settings.kb_embeddings_path)


def build_knowledge_base(settings: Settings) -> KnowledgeBase:
    embedder = build_embedder(settings)
    min_score = settings.kb_min_score if settings.kb_min_score is not None else embedder.recommended_min_score
    return KnowledgeBase(embedder, build_kb_store(settings, embedder.model_name), min_score)
