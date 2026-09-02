"""Dealer used-car sites. autolist_api.json is a REAL trimmed capture of
autolist.com/search (2026-09-02) for "kia ev9 land" near 27705, plus
two synthetic rows: an "accepting offers" row with no price and a row
with no listing href (skipped)."""

from pathlib import Path
from urllib.parse import parse_qs, urlparse

from product_finder_sites import api, fetch, parse, run
from product_finder_sites.spec import BUILTIN_SITES

FIXTURES = Path(__file__).parent / "fixtures"
AUTOLIST = next(s for s in BUILTIN_SITES if s["slug"] == "autolist")


def test_autolist_rows_become_car_listings():
    body = (FIXTURES / "autolist_api.json").read_text()
    out = parse.parse_listings(AUTOLIST, "https://www.autolist.com/search", body)
    assert len(out) == 4  # the href-less row is dropped
    first = out[0]
    assert first["title"] == "2024 Kia EV9 Land, 20540 mi"
    assert first["price"] == 45298.0
    assert first["url"] == "https://www.autolist.com/listings/KNDADFS58R6034010"
    assert first["location"] == "Raleigh, NC"
    assert first["condition"] == "used; Westgate Chrysler Jeep Dodge Ram"
    assert out[3]["price"] is None  # accepting offers


def test_autolist_url_carries_query_zip_radius_condition(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, timeout=25.0):
        seen["url"] = url
        return (FIXTURES / "autolist_api.json").read_text()

    monkeypatch.setattr(fetch, "_get", fake_get)
    result = run.search_site(AUTOLIST, "kia ev9 land")
    assert result["error"] is None and result["strategy"] == "autolist_api"
    assert seen["url"].startswith("https://www.autolist.com/search?")
    assert "keywords=kia+ev9+land" in seen["url"]
    assert "zip=27705" in seen["url"] and "radius=100" in seen["url"]
    assert "condition=used" in seen["url"] and "limit=50" in seen["url"]
    assert result["listings"][0]["site_slug"] == "autolist"


def test_autolist_condition_is_optional(monkeypatch):
    calls = []
    monkeypatch.setattr(
        fetch, "_get", lambda url, headers=None, timeout=25.0: calls.append(url) or "{}"
    )
    api.fetch_autolist_api({"zip": "27705"}, "honda fit")
    q = parse_qs(urlparse(calls[0]).query)
    assert "condition" not in q and q["radius"] == ["100"]  # new + used, default radius


CARSCOM = next(s for s in BUILTIN_SITES if s["slug"] == "cars-com")
CARVANA = next(s for s in BUILTIN_SITES if s["slug"] == "carvana")


def test_carscom_cards_from_vehicle_details_json():
    body = (FIXTURES / "carscom.html").read_text()
    out = parse.parse_listings(CARSCOM, "https://www.cars.com/shopping/results/", body)
    assert [li["title"] for li in out] == [
        "Used 2019 Toyota Prius LE, 220371 mi",
        "Used 2014 Toyota Prius v Five, 118490 mi",
        "Used 2026 Toyota Prius XLE, 1861 mi",
    ]
    first = out[0]
    assert first["price"] == 12798.0
    assert (
        first["url"] == "https://www.cars.com/vehicledetail/f944d191-4082-473d-8c74-7a394c5fda7d/"
    )
    assert first["location"] == "Greensboro, NC"
    assert first["condition"] == "used; Toyota of Greensboro"
    assert out[1]["location"] == "Sanford, NC"


def test_carscom_is_browser_only_with_keyword_zip_radius(monkeypatch):
    seen = {}

    def fake_browser(url, wait=None, timeout=30.0, cookies=None):
        seen["url"], seen["wait"] = url, wait
        return (FIXTURES / "carscom.html").read_text()

    monkeypatch.setattr(fetch, "_get", lambda *a, **k: pytest_fail("plain HTTP must not run"))
    monkeypatch.setattr(fetch, "_get_browser", fake_browser)
    result = run.search_site(CARSCOM, "toyota prius")
    assert result["error"] is None and result["strategy"] == "carscom"
    assert "keyword=toyota+prius" in seen["url"] and "zip=27705" in seen["url"]
    assert (
        "maximum_distance=100" in seen["url"] and seen["wait"] == "fuse-card[data-vehicle-details]"
    )
    assert len(result["listings"]) == 3


def test_carvana_vehicle_jsonld_and_slug_url(monkeypatch):
    body = (FIXTURES / "carvana.html").read_text()
    out = parse.parse_listings(CARVANA, "https://www.carvana.com/cars/toyota-prius", body)
    assert [li["title"] for li in out] == [
        "Used 2015 Toyota Prius Two, 67298 mi",
        "Used 2026 Toyota Prius Plug-in Hybrid SE, 7826 mi",
        "Used 2023 Toyota Prius Prime SE, 25334 mi",
    ]
    assert out[0]["price"] == 18990.0
    assert out[0]["url"] == "https://www.carvana.com/vehicle/4590522"
    assert out[0]["location"] is None
    assert out[0]["condition"] == "used; Carvana (delivery)"

    seen = {}

    def fake_browser(url, wait=None, timeout=30.0, cookies=None):
        seen["url"] = url
        return body

    monkeypatch.setattr(fetch, "_get_browser", fake_browser)
    result = run.search_site(CARVANA, "Kia EV9 Land")
    assert seen["url"] == "https://www.carvana.com/cars/kia-ev9-land"
    assert result["strategy"] == "carvana" and len(result["listings"]) == 3


def test_carvana_unknown_slug_page_has_no_vehicles(monkeypatch):
    monkeypatch.setattr(
        fetch,
        "_get_browser",
        lambda url, wait=None, timeout=30.0, cookies=None: "<html><body>Carvana</body></html>",
    )
    result = run.search_site(CARVANA, "EV9 AWD")
    assert result["strategy"] is None and result["error"] == "browser: no items parsed"


def pytest_fail(msg):
    raise AssertionError(msg)
