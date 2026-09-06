import pytest

from leadradar.sources.geocode import haversine_distance_km


def test_haversine_distance_same_point_is_zero():
    assert haversine_distance_km(37.0, 127.0, 37.0, 127.0) == 0.0


def test_haversine_distance_one_degree_latitude_is_about_111km():
    distance = haversine_distance_km(0.0, 0.0, 1.0, 0.0)
    assert distance == pytest.approx(111.2, abs=0.5)
