"""Storage CRUD contract: JSON columns round-trip, listings dedupe on url."""

from product_finder_core import storage


def _conn(tmp_path):
    return storage.connect(str(tmp_path / "t.db"))


def test_product_roundtrip(tmp_path):
    conn = _conn(tmp_path)
    prod = storage.upsert_product(
        conn,
        {
            "slug": "widget",
            "name": "Widget",
            "queries": ["blue widget"],
            "criteria": [{"field": "price", "op": "lte", "value": 5}],
        },
    )
    assert prod["queries"] == ["blue widget"]
    assert storage.list_products(conn)[0]["slug"] == "widget"
    prod["name"] = "Widget 2"
    assert storage.upsert_product(conn, prod)["name"] == "Widget 2"
    assert storage.delete_product(conn, "widget")
    assert storage.get_product(conn, "widget") is None


def test_listing_upsert_dedupes_on_url(tmp_path):
    conn = _conn(tmp_path)
    storage.upsert_product(conn, {"slug": "w"})
    li = {
        "product_slug": "w",
        "site_slug": "ebay",
        "url": "http://x/1",
        "title": "a",
        "price": 10.0,
        "score": 0.5,
    }
    storage.upsert_listing(conn, li)
    storage.upsert_listing(conn, {**li, "price": 8.0})
    rows = storage.query_listings(conn, "w")
    assert len(rows) == 1 and rows[0]["price"] == 8.0
    assert rows[0]["first_seen"] <= rows[0]["last_seen"]


def test_query_filters_hard_fails(tmp_path):
    conn = _conn(tmp_path)
    storage.upsert_product(conn, {"slug": "w"})
    storage.upsert_listing(
        conn,
        {
            "product_slug": "w",
            "site_slug": "ebay",
            "url": "http://x/2",
            "score": 0.9,
            "hard_fails": ["8GB RAM"],
        },
    )
    assert storage.query_listings(conn, "w") == []
    assert len(storage.query_listings(conn, "w", include_hard_fails=True)) == 1


def test_sites_table(tmp_path):
    conn = _conn(tmp_path)
    storage.upsert_site(conn, {"slug": "ebay", "name": "eBay", "config": {"url": "u"}})
    assert storage.list_sites(conn)[0]["config"] == {"url": "u"}
    storage.set_site_enabled(conn, "ebay", False)
    assert storage.list_sites(conn, enabled_only=True) == []


def test_settings_roundtrip(tmp_path):
    conn = _conn(tmp_path)
    assert storage.get_setting(conn, "home") is None
    assert storage.get_setting(conn, "home", {}) == {}
    storage.set_setting(conn, "home", {"lat": 1.5, "lon": -2.0})
    storage.set_setting(conn, "home", {"lat": 1.5, "lon": -3.0})
    assert storage.get_setting(conn, "home") == {"lat": 1.5, "lon": -3.0}


def test_distance_filter_excludes_unknown(tmp_path):
    conn = _conn(tmp_path)
    storage.upsert_product(conn, {"slug": "w"})
    base = {"product_slug": "w", "site_slug": "fb", "score": 0.5}
    storage.upsert_listing(conn, {**base, "url": "http://x/near", "distance_mi": 4.2})
    storage.upsert_listing(conn, {**base, "url": "http://x/far", "distance_mi": 140.0})
    storage.upsert_listing(conn, {**base, "url": "http://x/unknown"})
    assert len(storage.query_listings(conn, "w")) == 3
    near = storage.query_listings(conn, "w", max_distance_mi=25)
    assert [r["url"] for r in near] == ["http://x/near"]
    assert near[0]["distance_mi"] == 4.2


def test_migrate_adds_distance_column_to_old_db(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    raw = sqlite3.connect(str(path))
    raw.executescript(
        """CREATE TABLE products (slug TEXT PRIMARY KEY, name TEXT NOT NULL,
             description TEXT NOT NULL DEFAULT '', queries TEXT NOT NULL DEFAULT '[]',
             criteria TEXT NOT NULL DEFAULT '[]', extractors TEXT NOT NULL DEFAULT '{}',
             manual_checks TEXT NOT NULL DEFAULT '[]', max_price REAL,
             created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
           CREATE TABLE listings (id INTEGER PRIMARY KEY, product_slug TEXT NOT NULL,
             site_slug TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
             price REAL, currency TEXT NOT NULL DEFAULT 'USD', condition TEXT, location TEXT,
             seller_rating REAL, seller_feedback_count INTEGER,
             attrs TEXT NOT NULL DEFAULT '{}', score REAL,
             hard_fails TEXT NOT NULL DEFAULT '[]', first_seen TEXT NOT NULL,
             last_seen TEXT NOT NULL, UNIQUE(product_slug, url));"""
    )
    raw.close()
    conn = storage.connect(str(path))
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(listings)")}
    assert "distance_mi" in cols
    assert "sites" in {r["name"] for r in conn.execute("PRAGMA table_info(products)")}
    storage.upsert_product(conn, {"slug": "w"})
    storage.upsert_listing(
        conn, {"product_slug": "w", "site_slug": "fb", "url": "u", "distance_mi": 1}
    )
    assert storage.listings_with_location(conn) == []


def test_listing_unit_price_columns(tmp_path):
    conn = _conn(tmp_path)
    storage.upsert_product(conn, {"slug": "w"})
    li = {"product_slug": "w", "site_slug": "aldi", "url": "http://x/3", "price": 6.45}
    lid = storage.upsert_listing(conn, {**li, "unit_qty": 44.0, "unit": "oz", "unit_price": 0.1466})
    row = storage.query_listings(conn, "w")[0]
    assert (row["unit_qty"], row["unit"], row["unit_price"]) == (44.0, "oz", 0.1466)
    storage.set_listing_units(conn, lid, {"unit_qty": 12.0, "unit": "ct", "unit_price": 0.5375})
    assert storage.query_listings(conn, "w")[0]["unit"] == "ct"
    assert storage.listings_for_units(conn) == [{"id": lid, "title": "", "price": 6.45}]
