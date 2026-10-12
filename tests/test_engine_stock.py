from __future__ import annotations

from decimal import Decimal

import pytest

from app.engine.stock_match import ExceedsComputedCapacityError, Listing, match_affordable_stock

D = Decimal


def _listing(i: int, price: int, area: str = "Kajang", bedrooms: int = 2) -> Listing:
    return Listing(
        listing_id=f"L{i:04d}", title=f"{bedrooms}-bed unit", area=area, state="Selangor",
        price=D(price), bedrooms=bedrooms, bathrooms=2, sqft=850, tenure="leasehold",
        property_type="serviced_apartment", completion_status="completed",
        distance_to_kl_city_km=D("24"), nearest_transit="MRT Kajang", maintenance_monthly=D("210"),
    )


STOCK = [
    _listing(1, 380_000), _listing(2, 450_000, bedrooms=3), _listing(3, 505_000),
    _listing(4, 520_000), _listing(5, 521_000), _listing(6, 690_000),
    _listing(7, 410_000, area="Cheras"), _listing(8, 470_000, area="Cheras", bedrooms=3),
    _listing(9, 360_000), _listing(10, 395_000), _listing(11, 430_000), _listing(12, 440_000),
]
CAPACITY = D("520000")


def test_rejects_max_price_above_computed_capacity() -> None:
    with pytest.raises(ExceedsComputedCapacityError) as exc:
        match_affordable_stock(STOCK, D("750000"), CAPACITY)
    assert exc.value.error_code == "EXCEEDS_COMPUTED_CAPACITY"


def test_never_returns_above_capacity_and_caps_at_six() -> None:
    out = match_affordable_stock(STOCK, CAPACITY, CAPACITY)
    assert len(out) == 6
    assert all(x.price <= CAPACITY for x in out)
    assert [x.price for x in out] == sorted((x.price for x in out), reverse=True)
    assert out[0].listing_id == "L0004"


def test_area_and_bedroom_filters() -> None:
    out = match_affordable_stock(STOCK, CAPACITY, CAPACITY, area="cheras", min_bedrooms=3)
    assert [x.listing_id for x in out] == ["L0008"]


def test_lower_budget_than_capacity_is_allowed() -> None:
    out = match_affordable_stock(STOCK, D("400000"), CAPACITY, area="Kajang")
    assert [x.listing_id for x in out] == ["L0010", "L0001", "L0009"]
