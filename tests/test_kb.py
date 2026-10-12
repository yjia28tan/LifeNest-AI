from __future__ import annotations

import json

import pytest

from app.config import ROOT, Settings
from app.kb import build_knowledge_base
from app.kb.chunking import chunk_document, load_knowledge, parse_markdown
from app.kb.embeddings import HashingEmbedder
from app.kb.ingest import ingest
from app.kb.memory_store import InMemoryKBStore
from app.kb.store import KnowledgeBase

KNOWLEDGE = ROOT / "knowledge"
COMMITTED_JSON = ROOT / "data" / "kb_embeddings.json"


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, llm_backend="fake", kb_backend="memory", **overrides)


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return build_knowledge_base(_settings())


def test_chunks_follow_sections() -> None:
    chunks = load_knowledge(KNOWLEDGE)
    assert len({c.doc_id for c in chunks}) == 7
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    epf = [c for c in chunks if c.doc_id == "epf-akaun-sejahtera"]
    assert [c.section for c in epf][0] == "The 2024 three-account structure"
    assert all(c.category == "EPF" and c.title.startswith("EPF") for c in epf)


def test_long_sections_are_split() -> None:
    meta, body = parse_markdown(KNOWLEDGE / "epf-akaun-sejahtera.md")
    small = chunk_document(meta, body, max_tokens=40)
    assert len(small) > len(chunk_document(meta, body))
    assert any(c.chunk_id.endswith("-2") for c in small)


def test_committed_embeddings_match_knowledge_folder() -> None:
    raw = json.loads(COMMITTED_JSON.read_text(encoding="utf-8"))
    on_disk = {c["chunk_id"]: c["content"] for c in raw["chunks"]}
    assert on_disk == {c.chunk_id: c.content for c in load_knowledge(KNOWLEDGE)}, \
        "data/kb_embeddings.json is stale; run scripts/ingest_kb.py"


async def test_ingest_is_idempotent(tmp_path) -> None:
    settings = _settings(kb_embeddings_path=tmp_path / "kb.json")
    first = await ingest(settings, write_firestore=False)
    content_1 = (tmp_path / "kb.json").read_text(encoding="utf-8")
    second = await ingest(settings, write_firestore=False)
    content_2 = (tmp_path / "kb.json").read_text(encoding="utf-8")

    assert first["chunks"] == second["chunks"] == len(load_knowledge(KNOWLEDGE))
    strip = lambda s: {k: v for k, v in json.loads(s).items() if k != "generated_at"}  # noqa: E731
    assert strip(content_1) == strip(content_2)
    assert json.loads(content_1)["idf"]


@pytest.mark.parametrize(
    ("query", "doc_id"),
    [
        ("how much can I withdraw from EPF for housing", "epf-akaun-sejahtera"),
        ("stamp duty on memorandum of transfer", "stamp-duty-and-legal-fees"),
        ("what margin of finance for a third housing loan", "margin-of-finance"),
        ("maximum loan tenure and age limit", "loan-tenure-and-age"),
        ("how does the OPR affect my instalment", "sbr-and-rate-pricing"),
    ],
)
async def test_offline_retrieval_finds_the_right_document(kb, query, doc_id) -> None:
    out = await kb.search(query, top_k=3)
    assert out["ok"] and out["results"][0]["doc_id"] == doc_id
    first = out["results"][0]
    assert set(first) >= {"doc_id", "title", "section", "content", "score"}
    assert all(r["score"] >= kb.min_score for r in out["results"])


@pytest.mark.parametrize("query", ["does the bank cover crypto income", "best pizza in kuala lumpur"])
async def test_kb_miss_is_honest(kb, query) -> None:
    out = await kb.search(query)
    assert out == {"ok": True, "results": [], "note": "no relevant regulation found"}


async def test_category_filter(kb) -> None:
    out = await kb.search("how does the OPR affect my instalment", categories=["rates"], top_k=4)
    assert out["results"] and {r["category"] for r in out["results"]} == {"RATES"}


@pytest.mark.parametrize("query", ["hi", "x" * 301])
async def test_query_length_validated(kb, query) -> None:
    with pytest.raises(ValueError):
        await kb.search(query)


def test_threshold_defaults_and_override() -> None:
    assert build_knowledge_base(_settings()).min_score == HashingEmbedder.recommended_min_score
    assert build_knowledge_base(_settings(kb_min_score=0.35)).min_score == 0.35


def test_embedding_model_mismatch_fails_fast() -> None:
    store = InMemoryKBStore.from_file(COMMITTED_JSON)
    with pytest.raises(ValueError, match="re-run scripts/ingest_kb.py"):
        KnowledgeBase(HashingEmbedder(384, {}), store, 0.15)
