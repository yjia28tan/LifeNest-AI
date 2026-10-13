from __future__ import annotations

from decimal import Decimal

import pytest

from app.engine.location_cost import UnknownAreaError, location_cost

D = Decimal


def test_car_to_city_centre_includes_parking(rules) -> None:
    r = location_cost("Kajang", "KL City Centre", "car", rules)
    tr = rules.transport
    monthly_km = 2 * r.road_distance_km_one_way * tr.working_days_per_month
    assert abs(r.monthly_fuel - monthly_km * tr.avg_consumption_l_per_100km / 100 * tr.fuel_price_per_litre) < D("0.5")
    assert r.monthly_parking == tr.parking_monthly_city_centre
    assert r.monthly_tolls > 0 and r.monthly_transit == 0
    assert r.total_monthly == r.monthly_fuel + r.monthly_tolls + r.monthly_parking
    assert r.rules_version == rules.version


def test_transit_uses_pass_only(rules) -> None:
    r = location_cost("Kajang", "KL City Centre", "transit", rules)
    assert r.total_monthly == r.monthly_transit == rules.transport.transit_pass_monthly
    assert r.monthly_fuel == r.monthly_tolls == r.monthly_parking == 0
    assert r.annual_hours_commuting > location_cost("Kajang", "KL City Centre", "car", rules).annual_hours_commuting


def test_no_parking_outside_city_centre(rules) -> None:
    assert location_cost("Puchong", "Cyberjaya", "car", rules).monthly_parking == 0


def test_farther_home_costs_more(rules) -> None:
    near = location_cost("Cheras", "KL City Centre", "car", rules)
    far = location_cost("Semenyih", "KL City Centre", "car", rules)
    assert far.total_monthly > near.total_monthly
    assert far.annual_hours_commuting > near.annual_hours_commuting


def test_symmetric_and_case_insensitive(rules) -> None:
    a = location_cost("shah alam", "mont kiara", "car", rules)
    b = location_cost("Mont Kiara", "Shah Alam", "car", rules)
    assert a.road_distance_km_one_way == b.road_distance_km_one_way
    assert a.area == "Shah Alam"


def test_same_area_has_no_fuel(rules) -> None:
    r = location_cost("Cyberjaya", "Cyberjaya", "car", rules)
    assert r.monthly_fuel == 0 and r.annual_hours_commuting == 0


def test_unknown_area(rules) -> None:
    with pytest.raises(UnknownAreaError) as exc:
        location_cost("Atlantis", "KL City Centre", "car", rules)
    assert exc.value.error_code == "UNKNOWN_AREA"
    assert "Kajang" in exc.value.supported
