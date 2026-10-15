"""Chunk and embed knowledge/*.md into data/kb_embeddings.json (and Firestore kb_chunks with --firestore).

Usage: python scripts/ingest_kb.py [--firestore]
Embedder follows LLM_BACKEND: fake -> offline hashing embedder, vertex -> EMBEDDING_MODEL on Vertex AI.
Production vector index (run once):
  gcloud firestore indexes composite create --collection-group=kb_chunks --query-scope=COLLECTION \
    --field-config=field-path=category,order=ASCENDING \
    --field-config='field-path=embedding,vector-config={"dimension":"768","flat":"{}"}'
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.config import get_settings
from app.kb.ingest import ingest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--firestore", action="store_true", help="also upsert into Firestore kb_chunks")
    args = parser.parse_args()
    settings = get_settings()
    settings.export_process_env()
    summary = asyncio.run(ingest(settings, write_firestore=args.firestore or settings.kb_backend == "firestore"))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
