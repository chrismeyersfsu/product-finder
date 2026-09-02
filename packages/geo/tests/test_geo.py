"""Geo contract: gazetteer-first, Nominatim behind _get, cache once per query."""

import sqlite3
from pathlib import Path

from product_finder_geo import geo

FIXTURES = Path(__file__).parent / "fixtures"
HOME = (35.9940, -78.8986)


def test_haversine_known_pair():
    # Durham -> Raleigh centroids are about 21 miles as the crow flies.
    assert 19 < geo.haversine_mi(HOME, geo.BUILTIN["raleigh, nc"]) < 23
    assert geo.haversine_mi(HOME, HOME) == 0


def test_builtin_gazetteer_needs_no_network(monkeypatch):
    def boom(url, timeout=15.0):
        raise AssertionError("network called")

    monkeypatch.setattr(geo, "_get", boom)
    assert geo.geocode("Chapel Hill, NC") == geo.BUILTIN["chapel hill, nc"]
    assert geo.geocode("  cary,   nc ") == geo.BUILTIN["cary, nc"]


def test_geocode_via_nominatim_fixture(monkeypatch):
    seen = []

    def fake(url, timeout=15.0):
        seen.append(url)
        return (FIXTURES / "nominatim_durham.json").read_text()

    monkeypatch.setattr(geo, "_get", fake)
    lat, lon = geo.geocode("Citrus Heights, CA")
    assert (round(lat, 3), round(lon, 3)) == (35.994, -78.899)
    assert "q=Citrus+Heights%2C+CA" in seen[0] and "format=json" in seen[0]


def test_geocode_empty_and_errors_are_none(monkeypatch):
    monkeypatch.setattr(
        geo, "_get", lambda url, timeout=15.0: (FIXTURES / "nominatim_empty.json").read_text()
    )
    assert geo.geocode("Nowhere, ZZ") is None

    def fail(url, timeout=15.0):
        raise geo.GeoError("HTTP 429")

    monkeypatch.setattr(geo, "_get", fail)
    assert geo.geocode("Nowhere, ZZ") is None
    assert geo.geocode("") is None
    assert geo.parse_nominatim("not json") is None


def test_cache_geocodes_once_and_remembers_misses(monkeypatch):
    calls = []

    def fake(url, timeout=15.0):
        calls.append(url)
        name = "nominatim_durham.json" if "Sacramento" in url else "nominatim_empty.json"
        return (FIXTURES / name).read_text()

    monkeypatch.setattr(geo, "_get", fake)
    cache = geo.GeoCache(sqlite3.connect(":memory:"))
    assert cache.lookup("Sacramento, CA") is not None
    assert cache.lookup("Sacramento, CA") is not None
    assert cache.lookup("Nowhere, ZZ") is None
    assert cache.lookup("nowhere,  zz") is None
    assert len(calls) == 2  # one hit, one miss, both memoized

    assert cache.distance_mi(HOME, "Raleigh, NC") > 20
    assert cache.distance_mi(HOME, None) is None
    assert cache.distance_mi(HOME, "Nowhere, ZZ") is None
    assert len(calls) == 2
