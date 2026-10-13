"""Load the seed JSON files into validated models."""

from __future__ import annotations

import json
from pathlib import Path

from app.engine.stock_match import Listing
from app.storage.models import DemoProfile


def load_property_stock(path: Path) -> list[Listing]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Listing.model_validate(x) for x in raw["listings"]]


def load_demo_profiles(path: Path) -> list[DemoProfile]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [DemoProfile.model_validate(x) for x in raw["profiles"]]
