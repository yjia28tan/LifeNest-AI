"""Monthly commute cost between a home area and a workplace area."""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.engine.money import HUNDRED, ZERO, money, pct
from app.engine.rules import Area, Rules

TransportMode = Literal["car", "transit"]

_EARTH_RADIUS_KM = Decimal("6371.0088")


class UnknownAreaError(ValueError):
    error_code = "UNKNOWN_AREA"

    def __init__(self, name: str, supported: list[str]) -> None:
        super().__init__(f"unknown area {name!r}; supported: {', '.join(supported)}")
        self.supported = supported


class LocationCostResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    area: str
    workplace_area: str
    transport_mode: TransportMode
    road_distance_km_one_way: Decimal
    monthly_fuel: Decimal
    monthly_tolls: Decimal
    monthly_parking: Decimal
    monthly_transit: Decimal
    total_monthly: Decimal
    annual_hours_commuting: Decimal
    rules_version: str


def _straight_line_km(a: Area, b: Area) -> Decimal:
    lat1, lon1, lat2, lon2 = (math.radians(float(x)) for x in (a.lat, a.lon, b.lat, b.lon))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    # Trig is float-only; round back into Decimal at 3 dp (metre precision) before any money math.
    return (_EARTH_RADIUS_KM * Decimal(str(2 * math.asin(math.sqrt(h))))).quantize(Decimal("0.001"))


def _resolve(name: str, rules: Rules) -> Area:
    area = rules.locations.find(name)
    if area is None:
        raise UnknownAreaError(name, [a.name for a in rules.locations.areas])
    return area


def location_cost(area: str, workplace_area: str, transport_mode: TransportMode, rules: Rules) -> LocationCostResult:
    home, work = _resolve(area, rules), _resolve(workplace_area, rules)
    loc, tr = rules.locations, rules.transport
    days = Decimal(tr.working_days_per_month)
    road_km = _straight_line_km(home, work) * loc.road_distance_factor
    monthly_km = 2 * road_km * days

    fuel = tolls = parking = transit = ZERO
    if transport_mode == "car":
        fuel = monthly_km * tr.avg_consumption_l_per_100km / HUNDRED * tr.fuel_price_per_litre
        tolls = monthly_km * loc.toll_per_km
        parking = tr.parking_monthly_city_centre if work.city_centre else ZERO
        minutes_one_way = road_km / loc.avg_drive_speed_kmh * 60
    else:
        transit = tr.transit_pass_monthly
        minutes_one_way = road_km / loc.avg_transit_speed_kmh * 60 + loc.transit_overhead_minutes

    parts = [money(fuel), money(tolls), money(parking), money(transit)]
    return LocationCostResult(
        area=home.name,
        workplace_area=work.name,
        transport_mode=transport_mode,
        road_distance_km_one_way=pct(road_km),
        monthly_fuel=parts[0],
        monthly_tolls=parts[1],
        monthly_parking=parts[2],
        monthly_transit=parts[3],
        total_monthly=sum(parts, ZERO),
        annual_hours_commuting=pct(2 * minutes_one_way * days * 12 / 60),
        rules_version=rules.version,
    )
