"""Grounding guarantee (spec section 14): every RM amount and percentage in a reply must trace to a tool output,
a cited regulation source, or a value the user supplied. Presentation rounding is tolerated, invention is not."""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

_MULTIPLIERS = {"k": Decimal(1000), "mil": Decimal(1_000_000), "million": Decimal(1_000_000),
                "m": Decimal(1_000_000), "juta": Decimal(1_000_000), "ribu": Decimal(1000)}
MONEY = re.compile(r"RM\s?(\d[\d,]*(?:\.\d+)?)(?:\s?(k|mil|million|m|juta|ribu)\b)?", re.IGNORECASE)
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s?%")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


@dataclass(frozen=True)
class Figure:
    text: str
    value: Decimal       # as displayed, before the multiplier
    decimals: int
    multiplier: Decimal


@dataclass(frozen=True)
class GroundingResult:
    ok: bool
    figures: tuple[str, ...]
    unmatched: tuple[str, ...]


def _parse(raw: str) -> tuple[Decimal, int]:
    cleaned = raw.replace(",", "")
    return Decimal(cleaned), len(cleaned.split(".")[1]) if "." in cleaned else 0


def extract_figures(text: str) -> list[Figure]:
    figures = []
    for m in MONEY.finditer(text):
        value, decimals = _parse(m.group(1))
        mult = _MULTIPLIERS.get((m.group(2) or "").lower(), Decimal(1))
        figures.append(Figure(m.group(0), value, decimals, mult))
    for m in PERCENT.finditer(text):
        value, decimals = _parse(m.group(1))
        figures.append(Figure(m.group(0), value, decimals, Decimal(1)))
    return figures


def collect_numbers(*sources: Any) -> set[Decimal]:
    """Every number found in nested dicts/lists, numeric strings and free text."""
    found: set[Decimal] = set()

    def walk(node: Any) -> None:
        if isinstance(node, bool) or node is None:
            return
        if isinstance(node, int | float | Decimal):
            with contextlib.suppress(InvalidOperation):
                found.add(Decimal(str(node)))
        elif isinstance(node, str):
            for raw in _NUMBER.findall(node):
                with contextlib.suppress(InvalidOperation):
                    found.add(Decimal(raw.replace(",", "")))
            # "RM750k" written by the user means 750000.
            for m in MONEY.finditer(node):
                if m.group(2):
                    found.add(_parse(m.group(1))[0] * _MULTIPLIERS[m.group(2).lower()])
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list | tuple | set):
            for v in node:
                walk(v)

    for source in sources:
        walk(source)
    return found


def _matches(fig: Figure, allowed: Iterable[Decimal]) -> bool:
    quantum = Decimal(1).scaleb(-fig.decimals)
    for a in allowed:
        if (a / fig.multiplier).quantize(quantum, rounding=ROUND_HALF_UP) == fig.value:
            return True
    return False


def check_grounding(reply: str, allowed: set[Decimal]) -> GroundingResult:
    figures = extract_figures(reply)
    unmatched = tuple(f.text for f in figures if not _matches(f, allowed))
    return GroundingResult(ok=not unmatched, figures=tuple(f.text for f in figures), unmatched=unmatched)


def allowed_values(computations: list[dict[str, Any]], sources: list[dict[str, Any]],
                   user_texts: list[str], profile: dict[str, Any] | None) -> set[Decimal]:
    # Confirmed profile values were supplied by the user (from their own documents) and reviewed by them.
    return collect_numbers([c.get("outputs", {}) for c in computations],
                           [s.get("content", "") for s in sources], user_texts, profile or {})
