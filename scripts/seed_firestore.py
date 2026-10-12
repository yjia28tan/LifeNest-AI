"""Idempotently (re)seed Firestore with property stock and demo profiles; optionally ingest the KB.

Usage: python scripts/seed_firestore.py [--with-kb]
Target is chosen by env: FIRESTORE_EMULATOR_HOST for the emulator, otherwise GOOGLE_CLOUD_PROJECT.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

from app.config import Settings, get_settings
from app.kb.ingest import ingest
from app.storage import build_repository
from app.storage.repository import Repository
from app.storage.seed_data import load_demo_profiles, load_property_stock


async def seed(settings: Settings, repo: Repository, with_kb: bool) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "property_stock": await repo.replace_property_stock(load_property_stock(settings.property_stock_path)),
        "demo_profiles": await repo.replace_demo_profiles(load_demo_profiles(settings.demo_profiles_path)),
    }
    if with_kb:
        summary["kb"] = await ingest(settings, write_firestore=settings.kb_backend == "firestore")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--with-kb", action="store_true", help="also run KB ingestion")
    args = parser.parse_args()
    settings = get_settings()
    if settings.db_backend != "firestore":
        raise SystemExit("seed_firestore.py needs DB_BACKEND=firestore (emulator or real project)")
    repo = build_repository(settings)
    target = os.environ.get("FIRESTORE_EMULATOR_HOST") or f"project {settings.google_cloud_project or '(ADC default)'}"
    print(f"Seeding Firestore at {target}")
    summary = asyncio.run(seed(settings, repo, args.with_kb))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
