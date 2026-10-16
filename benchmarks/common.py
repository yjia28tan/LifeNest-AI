"""Shared helpers for the benchmark scripts: token metering, timing statistics, result files."""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = BENCH_DIR / "data"
RESULTS_DIR = BENCH_DIR / "results"


# --- token metering ------------------------------------------------------------------------------------------------

@dataclass
class TokenMeter:
    """Counts Gemini tokens by wrapping google.genai's async generate_content (used by both the extractor and the
    chat gateway). Thinking tokens are billed as output tokens, so they are tracked and added to output."""

    calls: int = 0
    prompt: int = 0
    output: int = 0
    thoughts: int = 0
    _installed: bool = field(default=False, repr=False)

    def install(self) -> TokenMeter:
        if self._installed:
            return self
        from google.genai import models

        original = models.AsyncModels.generate_content
        meter = self

        async def metered(self_: Any, *args: Any, **kwargs: Any) -> Any:
            resp = await original(self_, *args, **kwargs)
            meta = getattr(resp, "usage_metadata", None)
            meter.calls += 1
            meter.prompt += getattr(meta, "prompt_token_count", 0) or 0
            meter.output += getattr(meta, "candidates_token_count", 0) or 0
            meter.thoughts += getattr(meta, "thoughts_token_count", 0) or 0
            return resp

        models.AsyncModels.generate_content = metered  # type: ignore[method-assign]
        self._installed = True
        return self

    def snapshot(self) -> dict[str, int]:
        return {"calls": self.calls, "prompt": self.prompt, "output": self.output, "thoughts": self.thoughts}

    @staticmethod
    def diff(after: dict[str, int], before: dict[str, int]) -> dict[str, int]:
        return {k: after[k] - before[k] for k in after}


def cost_usd(tokens: dict[str, int], price_in_per_m: float | None, price_out_per_m: float | None) -> float | None:
    """Cost from token counts and per-million-token prices (take both from the Vertex AI pricing page)."""
    if price_in_per_m is None or price_out_per_m is None:
        return None
    billed_out = tokens["output"] + tokens["thoughts"]
    return tokens["prompt"] / 1e6 * price_in_per_m + billed_out / 1e6 * price_out_per_m


# --- statistics ----------------------------------------------------------------------------------------------------

def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (no interpolation), so the result is always a value that was actually observed."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def summarise_seconds(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "median": round(statistics.median(values), 2),
        "p95": round(percentile(values, 95), 2),
        "mean": round(statistics.fmean(values), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
    }


# --- values --------------------------------------------------------------------------------------------------------

def to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).replace("RM", "").replace(",", "").strip()
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def same_money(a: Any, b: Any, tolerance: str = "0.005") -> bool:
    da, db = to_decimal(a), to_decimal(b)
    return da is not None and db is not None and abs(da - db) <= Decimal(tolerance)


# --- output --------------------------------------------------------------------------------------------------------

def write_result(name: str, payload: dict[str, Any]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RESULTS_DIR / f"{name}_{stamp}.json"
    path.write_text(json.dumps(payload, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    return path


def banner(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
