"""Decimal helpers. All engine math is Decimal; rounding happens only when building results."""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from app.engine.rules import Tier

ZERO = Decimal("0")
HUNDRED = Decimal("100")
_CENT = Decimal("0.01")
_THOUSAND = Decimal("1000")


def money(x: Decimal) -> Decimal:
    return x.quantize(_CENT, rounding=ROUND_HALF_UP)


def pct(x: Decimal) -> Decimal:
    return x.quantize(_CENT, rounding=ROUND_HALF_UP)


def floor_thousand(x: Decimal) -> Decimal:
    """Round down to the nearest RM1,000 so a reported price is never optimistic."""
    if x <= ZERO:
        return ZERO.quantize(_CENT)
    return ((x / _THOUSAND).to_integral_value(rounding=ROUND_DOWN) * _THOUSAND).quantize(_CENT)


def tiered_marginal(amount: Decimal, tiers: tuple[Tier, ...]) -> Decimal:
    """Progressive scale: each tier's rate applies only to the slice of `amount` inside it."""
    total = ZERO
    lower = ZERO
    for tier in tiers:
        if amount <= lower:
            break
        upper = amount if tier.up_to is None else min(amount, tier.up_to)
        total += (upper - lower) * tier.rate_pct / HUNDRED
        if tier.up_to is None:
            break
        lower = tier.up_to
    return total
