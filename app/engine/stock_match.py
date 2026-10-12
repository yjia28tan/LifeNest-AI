"""Filter property stock by the computed affordable price — never the aspirational one."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

DEFAULT_LIMIT = 6


class ExceedsComputedCapacityError(ValueError):
    error_code = "EXCEEDS_COMPUTED_CAPACITY"


class Listing(BaseModel):
    model_config = ConfigDict(frozen=True)

    listing_id: str
    title: str
    area: str
    state: str
    price: Decimal
    bedrooms: int
    bathrooms: int
    sqft: int
    tenure: Literal["freehold", "leasehold"]
    property_type: str
    completion_status: str
    distance_to_kl_city_km: Decimal
    nearest_transit: str | None = None
    maintenance_monthly: Decimal


def match_affordable_stock(
    listings: list[Listing],
    max_price: Decimal,
    computed_max_property_price: Decimal,
    area: str | None = None,
    min_bedrooms: int | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[Listing]:
    if max_price > computed_max_property_price:
        raise ExceedsComputedCapacityError(
            "max_price is above the computed affordable price; search at or below the computed figure"
        )
    area_key = area.strip().casefold() if area else None
    matches = [
        x for x in listings
        if x.price <= max_price
        and (area_key is None or x.area.casefold() == area_key)
        and (min_bedrooms is None or x.bedrooms >= min_bedrooms)
    ]
    # Closest to the budget first: the most home the buyer can genuinely afford.
    matches.sort(key=lambda x: (-x.price, x.listing_id))
    return matches[:limit]
