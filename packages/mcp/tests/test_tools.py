"""Tool functions exercised directly against a tmp DB; run via ./packages/mcp/ci.sh.

External HTTP is faked at the sites package's fetch._get seam.
"""

from pathlib import Path

import pytest
from product_finder_core import storage
from product_finder_mcp import server
from product_finder_sites import fetch

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_DB", str(tmp_path / "t.db"))
    for var in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "BESTBUY_API_KEY", "WALMART_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_seed_and_product_crud():
    out = server.seed_defaults()
    assert "thin-client-laptop" in out["products"]
    assert len(out["sites"]) == 28
    assert server.get_product("thin-client-laptop")["criteria"]

    server.add_product(
        "gpu",
        "Used GPU",
        queries=["rtx 3080"],
        criteria=[{"field": "price", "op": "lte", "value": 400}],
    )
    assert {p["slug"] for p in server.list_products()} == {"gpu", "thin-client-laptop"}
    assert server.delete_product("gpu")["deleted"]
    assert "error" in server.get_product("gpu")


def test_site_management():
    server.seed_defaults()
    server.add_site("mysite", "My Site", config={"url": "https://x/?q={query}"})
    assert any(s["slug"] == "mysite" for s in server.list_sites())
    assert server.set_site_enabled("amazon", False)["ok"]
    enabled = {s["slug"]: s["enabled"] for s in server.list_sites()}
    assert enabled["amazon"] is False


def test_run_search_scores_and_stores(monkeypatch):
    server.seed_defaults()
    monkeypatch.setattr(
        fetch,
        "_get_browser",
        lambda url, wait=None, timeout=30.0, cookies=None: (FIXTURES / "ebay.html").read_text(),
    )
    summary = server.run_search("thin-client-laptop", sites=["ebay"], query="x1 carbon")
    assert summary["stored"] == 2 and summary["per_site"] == {"ebay": 2}
    assert summary["strategies"] == {"ebay": "browser_css"}  # api unset, no plain-HTML tier

    rows = server.query_listings("thin-client-laptop")
    assert rows and rows[0]["score"] > 0.8
    assert rows[0]["attrs"]["ram_gb"] == 16

    deals = server.best_deals("thin-client-laptop")
    assert deals["deals"][0]["title"].startswith("Lenovo ThinkPad")
    assert "median_price" in deals["deals"][0]
    assert deals["manual_checks"]


def test_run_search_records_site_errors(monkeypatch):
    server.seed_defaults()

    def fail(url, headers=None, timeout=25.0):
        raise fetch.FetchError("HTTP 403")

    monkeypatch.setattr(fetch, "_get", fail)
    monkeypatch.setattr(
        fetch,
        "_get_browser",
        lambda url, wait=None, timeout=30.0, cookies=None: fetch._browser_unwired(url),
    )
    summary = server.run_search("thin-client-laptop", sites=["amazon"])
    assert summary["stored"] == 0
    assert "css: HTTP 403" in summary["errors"]["amazon"]
    assert "browser:" in summary["errors"]["amazon"]


def test_run_search_honors_product_site_list(monkeypatch):
    server.seed_defaults()
    server.add_product("car", "Car", queries=["honda fit"], sites=["craigslist"])
    assert server.get_product("car")["sites"] == ["craigslist"]
    seen: list[str] = []

    def fail(url, headers=None, timeout=25.0):
        seen.append(url)
        raise fetch.FetchError("HTTP 403")

    monkeypatch.setattr(fetch, "_get", fail)
    monkeypatch.setattr(
        fetch,
        "_get_browser",
        lambda url, wait=None, timeout=30.0, cookies=None: fetch._browser_unwired(url),
    )
    summary = server.run_search("car")
    assert set(summary["errors"]) == {"craigslist"}
    assert all("craigslist" in u for u in seen)
    # An explicit sites= argument still overrides the stored list.
    assert set(server.run_search("car", sites=["amazon"])["errors"]) == {"amazon"}


def test_run_search_missing_product():
    assert "error" in server.run_search("nope")


def test_project_tools_scoped_to_root(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "hello.txt").write_text("hi")
    monkeypatch.setenv("PF_PROJECT_ROOT", str(root))

    assert server.project_list_files("*.txt") == ["hello.txt"]
    assert server.project_read_file("hello.txt") == "hi"
    out = server.project_write_file("sub/new.txt", "content")
    assert out["wrote"] == "sub/new.txt"
    assert (root / "sub/new.txt").read_text() == "content"

    with pytest.raises(ValueError, match="escapes"):
        server.project_read_file("../secret")
    with pytest.raises(ValueError, match="escapes"):
        server.project_write_file("../../evil.txt", "x")
    with pytest.raises(ValueError, match=r"\.git"):
        server.project_write_file(".git/config", "x")


def test_tools_registered():
    assert len(server.TOOLS) == 29


def test_home_and_distances(monkeypatch):
    from product_finder_geo import geo

    # Street addresses are not in the gazetteer; fake the Nominatim seam.
    monkeypatch.setattr(
        geo, "_get", lambda url, timeout=15.0: (FIXTURES / "nominatim_home.json").read_text()
    )
    assert server.get_home() == {}
    assert server.backfill_distances()["error"].startswith("no home")

    home = server.set_home("2409 Tampa Ave, Durham, NC 27705")
    assert round(home["lat"], 2) == 36.02 and round(home["lon"], 2) == -78.94
    assert server.get_home()["address"].endswith("27705")

    server.seed_defaults()
    conn = server._connect()
    base = {"product_slug": "thin-client-laptop", "site_slug": "facebook", "score": 0.9}
    storage.upsert_listing(conn, {**base, "url": "fb://1", "location": "Chapel Hill, NC"})
    storage.upsert_listing(conn, {**base, "url": "fb://2", "location": "Charlotte, NC"})
    storage.upsert_listing(conn, {**base, "url": "fb://3"})
    conn.commit()

    assert server.backfill_distances() == {"updated": 2, "unknown_location": 0}
    near = server.query_listings("thin-client-laptop", max_distance_mi=25)
    assert [r["url"] for r in near] == ["fb://1"]
    assert 5 < near[0]["distance_mi"] < 15
    assert len(server.query_listings("thin-client-laptop")) == 3
    assert len(server.best_deals("thin-client-laptop", max_distance_mi=25)["deals"]) == 1


def test_run_search_stores_distance_when_home_set(monkeypatch):
    from product_finder_geo import geo

    monkeypatch.setattr(
        geo, "_get", lambda url, timeout=15.0: '[{"lat": "36.02", "lon": "-78.94"}]'
    )
    server.set_home("2409 Tampa Ave, Durham, NC 27705")
    server.seed_defaults()
    monkeypatch.setattr(
        fetch,
        "_get_browser",
        lambda url, wait=None, timeout=30.0, cookies=None: (FIXTURES / "ebay.html").read_text(),
    )
    server.run_search("thin-client-laptop", sites=["ebay"], query="x1 carbon")
    rows = server.query_listings("thin-client-laptop")
    # ebay rows carry no city, so distance stays unknown rather than wrong.
    assert all(r["distance_mi"] is None for r in rows)
    assert server.query_listings("thin-client-laptop", max_distance_mi=50) == []


def test_run_search_stores_unit_price_and_backfills(monkeypatch):
    server.seed_defaults()
    server.add_product(slug="shakes", name="Shakes", queries=["shake"])
    body = (FIXTURES / "aldi.html").read_text()
    monkeypatch.setattr(
        fetch, "_get_browser", lambda url, wait=None, timeout=30.0, cookies=None: body
    )
    summary = server.run_search("shakes", sites=["aldi"])
    assert summary["per_site"] == {"aldi": 3}
    rows = {r["title"]: r for r in server.query_listings("shakes")}
    r = rows["Elevation Vanilla Ready to Drink Protein Shake, 4 x 11 fl oz"]
    assert (r["unit_qty"], r["unit"], r["unit_price"]) == (44.0, "oz", 0.1466)

    conn = server._connect()
    conn.execute("UPDATE listings SET unit_price=NULL, unit=NULL, unit_qty=NULL")
    conn.commit()
    assert server.backfill_unit_prices() == {"listings": 3, "with_unit_price": 3}
    assert server.query_listings("shakes")[0]["unit_price"] is not None


def test_hide_listing_drops_it_from_deals_until_unhidden():
    server.seed_defaults()
    conn = server._connect()
    base = {"product_slug": "thin-client-laptop", "site_slug": "ebay", "score": 0.9, "price": 5.0}
    a = storage.upsert_listing(conn, {**base, "url": "e://1", "title": "keep"})
    b = storage.upsert_listing(conn, {**base, "url": "e://2", "title": "junk"})
    assert server.hide_listing(b) == {"id": b, "hidden": True}
    assert [r["id"] for r in server.best_deals("thin-client-laptop")["deals"]] == [a]
    assert [r["id"] for r in server.query_listings("thin-client-laptop")] == [a]
    assert [r["id"] for r in server.query_listings("thin-client-laptop", include_hidden=True)] == [
        a,
        b,
    ]
    assert [r["title"] for r in server.hidden_listings("thin-client-laptop")] == ["junk"]
    assert server.unhide_listing(b)["hidden"] is False
    assert len(server.best_deals("thin-client-laptop")["deals"]) == 2
    assert "error" in server.hide_listing(12345)


def test_new_within_days_filters_on_first_seen():
    server.seed_defaults()
    conn = server._connect()
    base = {"product_slug": "thin-client-laptop", "site_slug": "ebay", "score": 0.9, "price": 5.0}
    storage.upsert_listing(conn, {**base, "url": "e://old"})
    conn.execute("UPDATE listings SET first_seen='2020-01-01T00:00:00+00:00'")
    conn.commit()
    storage.upsert_listing(conn, {**base, "url": "e://new"})
    assert [r["url"] for r in server.query_listings("thin-client-laptop", new_within_days=1)] == [
        "e://new"
    ]
    assert [
        r["url"] for r in server.best_deals("thin-client-laptop", new_within_days=1)["deals"]
    ] == ["e://new"]
    assert len(server.query_listings("thin-client-laptop")) == 2


def test_run_search_fits_market_values_for_car_products(monkeypatch):
    server.seed_defaults()
    conn = server._connect()
    storage.upsert_product(
        conn,
        {
            "slug": "cars",
            "name": "Cars",
            "queries": ["car"],
            "sites": ["ebay"],
            "extractors": {
                "year": {"pattern": r"\b(20\d\d)\b", "type": "int"},
                "mileage": {"pattern": r"(\d+) mi\b", "type": "int"},
            },
        },
    )
    # a dozen priced rows from earlier scrapes: $20k new, cheaper with age and miles
    for i in range(12):
        year, miles = 2014 + i, 10_000 * (12 - i)
        storage.upsert_listing(
            conn,
            {
                "product_slug": "cars",
                "site_slug": "ebay",
                "url": f"e://{i}",
                "title": f"{year} Car, {miles} mi",
                "price": round(20_000 * 0.9 ** (2026 - year) * 0.95 ** (miles / 10_000)),
                "attrs": {"year": year, "mileage": miles},
            },
        )
    monkeypatch.setattr(
        fetch,
        "_get_browser",
        lambda url, wait=None, timeout=30.0, cookies=None: (FIXTURES / "ebay.html").read_text(),
    )
    summary = server.run_search("cars", sites=["ebay"], query="car")
    assert summary["stored"] == 2  # the ebay fixture rows carry no year -> unvalued
    assert summary["valued"] == 12
    deals = server.best_deals("cars", limit=50)["deals"]
    valued = {r["url"]: r for r in deals if r.get("est_value")}
    assert len(valued) == 12
    newest, oldest = valued["e://11"], valued["e://0"]
    assert newest["est_value"] > oldest["est_value"]
    assert abs(newest["pct_vs_est"]) < 5 and abs(oldest["pct_vs_est"]) < 5
    assert all(r.get("est_value") is None for r in deals if r["url"].startswith("http"))
    assert server.backfill_market_values()["cars"] == 12
