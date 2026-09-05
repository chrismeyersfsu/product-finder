"""SQLite persistence for products, sites, and listings.

Owns the schema, connections, and all SQL — including the key/value
settings table (home location). Never does network I/O and
never interprets criteria (scoring.py owns that). Callers rely on:
JSON-typed columns are always valid JSON, `connect()` returns rows as
sqlite3.Row, and upsert_listing() keys on (product_slug, url) so
re-running a search refreshes price/last_seen instead of duplicating —
first_seen, hidden_at, and pinned_at survive that refresh, so "new
since", hide-from-deals state, and pin-to-top state persist across
scrapes, and a scrape that finds no image keeps the image_url an
earlier one stored. query_listings() omits hidden rows unless asked
for them (a pinned row is still hidden if hidden_at is set). est_value
(the fitted market value, scoring.py's math) is written only by
set_est_values(), which callers run over a whole product after a
scrape.
"""

import json
import os
import sqlite3
from datetime import UTC, datetime

DEFAULT_DB = os.environ.get("PF_DB", "product_finder.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
  slug TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  queries TEXT NOT NULL DEFAULT '[]',
  criteria TEXT NOT NULL DEFAULT '[]',
  extractors TEXT NOT NULL DEFAULT '{}',
  manual_checks TEXT NOT NULL DEFAULT '[]',
  sites TEXT NOT NULL DEFAULT '[]',
  max_price REAL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sites (
  slug TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'css',
  config TEXT NOT NULL DEFAULT '{}',
  enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS listings (
  id INTEGER PRIMARY KEY,
  product_slug TEXT NOT NULL REFERENCES products(slug),
  site_slug TEXT NOT NULL,
  url TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  price REAL,
  currency TEXT NOT NULL DEFAULT 'USD',
  condition TEXT,
  location TEXT,
  seller_rating REAL,
  seller_feedback_count INTEGER,
  attrs TEXT NOT NULL DEFAULT '{}',
  score REAL,
  hard_fails TEXT NOT NULL DEFAULT '[]',
  distance_mi REAL,
  unit_qty REAL,
  unit TEXT,
  unit_price REAL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  hidden_at TEXT,
  pinned_at TEXT,
  image_url TEXT,
  est_value REAL,
  flags TEXT NOT NULL DEFAULT '[]',
  UNIQUE(product_slug, url)
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS search_runs (
  id INTEGER PRIMARY KEY,
  product_slug TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  site_results TEXT NOT NULL DEFAULT '{}'
);
"""

_JSON_PRODUCT_FIELDS = ("queries", "criteria", "extractors", "manual_checks", "sites")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DEFAULT_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a database was first created."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(products)")}
    if "sites" not in cols:
        conn.execute("ALTER TABLE products ADD COLUMN sites TEXT NOT NULL DEFAULT '[]'")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(listings)")}
    if "distance_mi" not in cols:
        conn.execute("ALTER TABLE listings ADD COLUMN distance_mi REAL")
    if "unit_price" not in cols:
        conn.execute("ALTER TABLE listings ADD COLUMN unit_qty REAL")
        conn.execute("ALTER TABLE listings ADD COLUMN unit TEXT")
        conn.execute("ALTER TABLE listings ADD COLUMN unit_price REAL")
    if "hidden_at" not in cols:
        conn.execute("ALTER TABLE listings ADD COLUMN hidden_at TEXT")
    if "image_url" not in cols:
        conn.execute("ALTER TABLE listings ADD COLUMN image_url TEXT")
    if "est_value" not in cols:
        conn.execute("ALTER TABLE listings ADD COLUMN est_value REAL")
    if "flags" not in cols:
        conn.execute("ALTER TABLE listings ADD COLUMN flags TEXT NOT NULL DEFAULT '[]'")
    if "pinned_at" not in cols:
        conn.execute("ALTER TABLE listings ADD COLUMN pinned_at TEXT")
    conn.execute("DROP INDEX IF EXISTS idx_price_history_lookup")
    conn.execute("DROP TABLE IF EXISTS price_history")
    conn.execute("DROP TABLE IF EXISTS backtests")
    conn.commit()


def get_setting(conn: sqlite3.Connection, key: str, default=None):
    """JSON-typed key/value settings (home location, etc.)."""
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )
    conn.commit()


def _row_to_product(row: sqlite3.Row) -> dict:
    d = dict(row)
    for f in _JSON_PRODUCT_FIELDS:
        d[f] = json.loads(d[f])
    return d


def upsert_product(conn: sqlite3.Connection, product: dict) -> dict:
    now = _now()
    p = {
        "slug": product["slug"],
        "name": product.get("name", product["slug"]),
        "description": product.get("description", ""),
        "max_price": product.get("max_price"),
    }
    for f in _JSON_PRODUCT_FIELDS:
        p[f] = json.dumps(product.get(f, [] if f != "extractors" else {}))
    conn.execute(
        """INSERT INTO products (slug, name, description, queries, criteria, extractors,
                                 manual_checks, sites, max_price, created_at, updated_at)
           VALUES (:slug, :name, :description, :queries, :criteria, :extractors,
                   :manual_checks, :sites, :max_price, :now, :now)
           ON CONFLICT(slug) DO UPDATE SET
             name=excluded.name, description=excluded.description, queries=excluded.queries,
             criteria=excluded.criteria, extractors=excluded.extractors,
             manual_checks=excluded.manual_checks, sites=excluded.sites,
             max_price=excluded.max_price,
             updated_at=excluded.updated_at""",
        {**p, "now": now},
    )
    conn.commit()
    return get_product(conn, product["slug"])


def get_product(conn: sqlite3.Connection, slug: str) -> dict | None:
    row = conn.execute("SELECT * FROM products WHERE slug=?", (slug,)).fetchone()
    return _row_to_product(row) if row else None


def list_products(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM products ORDER BY slug").fetchall()
    return [_row_to_product(r) for r in rows]


def delete_product(conn: sqlite3.Connection, slug: str) -> bool:
    conn.execute("DELETE FROM listings WHERE product_slug=?", (slug,))
    cur = conn.execute("DELETE FROM products WHERE slug=?", (slug,))
    conn.commit()
    return cur.rowcount > 0


def upsert_site(conn: sqlite3.Connection, site: dict) -> None:
    conn.execute(
        """INSERT INTO sites (slug, name, kind, config, enabled)
           VALUES (:slug, :name, :kind, :config, :enabled)
           ON CONFLICT(slug) DO UPDATE SET
             name=excluded.name, kind=excluded.kind, config=excluded.config,
             enabled=excluded.enabled""",
        {
            "slug": site["slug"],
            "name": site.get("name", site["slug"]),
            "kind": site.get("kind", "css"),
            "config": json.dumps(site.get("config", {})),
            "enabled": int(site.get("enabled", True)),
        },
    )
    conn.commit()


def list_sites(conn: sqlite3.Connection, enabled_only: bool = False) -> list[dict]:
    q = "SELECT * FROM sites" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY slug"
    out = []
    for row in conn.execute(q).fetchall():
        d = dict(row)
        d["config"] = json.loads(d["config"])
        d["enabled"] = bool(d["enabled"])
        out.append(d)
    return out


def set_site_enabled(conn: sqlite3.Connection, slug: str, enabled: bool) -> bool:
    cur = conn.execute("UPDATE sites SET enabled=? WHERE slug=?", (int(enabled), slug))
    conn.commit()
    return cur.rowcount > 0


def upsert_listing(conn: sqlite3.Connection, listing: dict) -> int:
    now = _now()
    row = {
        "product_slug": listing["product_slug"],
        "site_slug": listing["site_slug"],
        "url": listing["url"],
        "title": listing.get("title", ""),
        "price": listing.get("price"),
        "currency": listing.get("currency", "USD"),
        "condition": listing.get("condition"),
        "location": listing.get("location"),
        "seller_rating": listing.get("seller_rating"),
        "seller_feedback_count": listing.get("seller_feedback_count"),
        "attrs": json.dumps(listing.get("attrs", {})),
        "score": listing.get("score"),
        "hard_fails": json.dumps(listing.get("hard_fails", [])),
        "flags": json.dumps(listing.get("flags", [])),
        "distance_mi": listing.get("distance_mi"),
        "unit_qty": listing.get("unit_qty"),
        "unit": listing.get("unit"),
        "unit_price": listing.get("unit_price"),
        "image_url": listing.get("image_url"),
        "now": now,
    }
    cur = conn.execute(
        """INSERT INTO listings (product_slug, site_slug, url, title, price, currency,
                                 condition, location, seller_rating, seller_feedback_count,
                                 attrs, score, hard_fails, flags, distance_mi,
                                 unit_qty, unit, unit_price, image_url, first_seen, last_seen)
           VALUES (:product_slug, :site_slug, :url, :title, :price, :currency,
                   :condition, :location, :seller_rating, :seller_feedback_count,
                   :attrs, :score, :hard_fails, :flags, :distance_mi,
                   :unit_qty, :unit, :unit_price, :image_url, :now, :now)
           ON CONFLICT(product_slug, url) DO UPDATE SET
             title=excluded.title, price=excluded.price, condition=excluded.condition,
             location=excluded.location, seller_rating=excluded.seller_rating,
             seller_feedback_count=excluded.seller_feedback_count,
             attrs=excluded.attrs, score=excluded.score, hard_fails=excluded.hard_fails,
             flags=excluded.flags, distance_mi=excluded.distance_mi, unit_qty=excluded.unit_qty,
             unit=excluded.unit, unit_price=excluded.unit_price,
             image_url=COALESCE(excluded.image_url, listings.image_url),
             last_seen=excluded.last_seen""",
        row,
    )
    conn.commit()
    return cur.lastrowid


def set_listing_distance(conn: sqlite3.Connection, listing_id: int, distance_mi: float | None):
    conn.execute("UPDATE listings SET distance_mi=? WHERE id=?", (distance_mi, listing_id))


def set_listing_units(conn: sqlite3.Connection, listing_id: int, units: dict) -> None:
    """Store a units.unit_price() result ({unit_qty, unit, unit_price})."""
    conn.execute(
        "UPDATE listings SET unit_qty=?, unit=?, unit_price=? WHERE id=?",
        (units.get("unit_qty"), units.get("unit"), units.get("unit_price"), listing_id),
    )


def set_listing_scoring(
    conn: sqlite3.Connection,
    listing_id: int,
    attrs: dict,
    score: float,
    hard_fails: list,
    flags: list,
) -> None:
    """Rewrite one row's extracted attrs and everything scored from them."""
    conn.execute(
        "UPDATE listings SET attrs=?, score=?, hard_fails=?, flags=? WHERE id=?",
        (json.dumps(attrs), score, json.dumps(hard_fails), json.dumps(flags), listing_id),
    )


def delete_listing(conn: sqlite3.Connection, listing_id: int) -> None:
    conn.execute("DELETE FROM listings WHERE id=?", (listing_id,))


def set_listing_hidden(conn: sqlite3.Connection, listing_id: int, hidden: bool) -> bool:
    """Hide a listing from deals (or unhide it). Returns False for an
    unknown id. Hiding is idempotent: re-hiding keeps the original stamp."""
    if hidden:
        sql = "UPDATE listings SET hidden_at=COALESCE(hidden_at, ?) WHERE id=?"
        cur = conn.execute(sql, (_now(), listing_id))
    else:
        cur = conn.execute("UPDATE listings SET hidden_at=NULL WHERE id=?", (listing_id,))
    conn.commit()
    return cur.rowcount > 0


def set_listing_pinned(conn: sqlite3.Connection, listing_id: int, pinned: bool) -> bool:
    """Pin a listing to the top of deals (or unpin it). Returns False for
    an unknown id. Pinning is idempotent: re-pinning keeps the original
    stamp. A hidden listing stays hidden regardless of pin state."""
    if pinned:
        sql = "UPDATE listings SET pinned_at=COALESCE(pinned_at, ?) WHERE id=?"
        cur = conn.execute(sql, (_now(), listing_id))
    else:
        cur = conn.execute("UPDATE listings SET pinned_at=NULL WHERE id=?", (listing_id,))
    conn.commit()
    return cur.rowcount > 0


def product_listings(conn: sqlite3.Connection, product_slug: str) -> list[dict]:
    """Every row for a product, hidden ones included (market data is
    market data), for the value fit."""
    rows = conn.execute("SELECT * FROM listings WHERE product_slug=?", (product_slug,))
    return [_row_to_listing(r) for r in rows]


def set_est_values(
    conn: sqlite3.Connection, product_slug: str, values: dict[int, float | None]
) -> None:
    """Rewrite est_value for a product: ids in `values` get theirs, every
    other row of the product is cleared, so a shrinking fit can't leave
    stale estimates behind."""
    conn.execute("UPDATE listings SET est_value=NULL WHERE product_slug=?", (product_slug,))
    conn.executemany(
        "UPDATE listings SET est_value=? WHERE id=? AND product_slug=?",
        [(v, i, product_slug) for i, v in values.items() if v is not None],
    )
    conn.commit()


def hidden_listings(conn: sqlite3.Connection, product_slug: str | None = None) -> list[dict]:
    """Every hidden listing (optionally one product's), most recently hidden first."""
    sql = "SELECT * FROM listings WHERE hidden_at IS NOT NULL"
    args: list = []
    if product_slug:
        sql += " AND product_slug=?"
        args.append(product_slug)
    sql += " ORDER BY hidden_at DESC, id DESC"
    return [_row_to_listing(r) for r in conn.execute(sql, args).fetchall()]


def _row_to_listing(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["attrs"] = json.loads(d["attrs"])
    d["hard_fails"] = json.loads(d["hard_fails"])
    d["flags"] = json.loads(d.get("flags") or "[]")
    return d


def listings_for_units(conn: sqlite3.Connection) -> list[dict]:
    """(id, title, price) for every listing — the unit-price backfill set."""
    return [dict(r) for r in conn.execute("SELECT id, title, price FROM listings")]


def listings_with_location(conn: sqlite3.Connection) -> list[dict]:
    """(id, location) for every listing that has a location string — the
    backfill set for distance recomputation."""
    rows = conn.execute(
        "SELECT id, location FROM listings WHERE location IS NOT NULL AND location != ''"
    ).fetchall()
    return [dict(r) for r in rows]


def query_listings(
    conn: sqlite3.Connection,
    product_slug: str,
    min_score: float | None = None,
    max_price: float | None = None,
    site_slug: str | None = None,
    include_hard_fails: bool = False,
    limit: int = 50,
    max_distance_mi: float | None = None,
    include_hidden: bool = False,
    first_seen_since: str | None = None,
) -> list[dict]:
    """max_distance_mi drops rows with unknown distance: a listing with no
    location can't claim to be nearby (they still show when no cap is set).
    Hidden rows are omitted unless include_hidden; first_seen_since (ISO
    timestamp) keeps only listings first seen at or after it."""
    sql = "SELECT * FROM listings WHERE product_slug=?"
    args: list = [product_slug]
    if not include_hidden:
        sql += " AND hidden_at IS NULL"
    if first_seen_since:
        sql += " AND first_seen >= ?"
        args.append(first_seen_since)
    if min_score is not None:
        sql += " AND score >= ?"
        args.append(min_score)
    if max_price is not None:
        sql += " AND price IS NOT NULL AND price <= ?"
        args.append(max_price)
    if site_slug:
        sql += " AND site_slug=?"
        args.append(site_slug)
    if max_distance_mi is not None:
        sql += " AND distance_mi IS NOT NULL AND distance_mi <= ?"
        args.append(max_distance_mi)
    if not include_hard_fails:
        sql += " AND hard_fails = '[]'"
    sql += " ORDER BY score DESC NULLS LAST, price ASC NULLS LAST LIMIT ?"
    args.append(limit)
    return [_row_to_listing(r) for r in conn.execute(sql, args).fetchall()]


def record_search_run(conn: sqlite3.Connection, product_slug: str, site_results: dict) -> int:
    now = _now()
    cur = conn.execute(
        "INSERT INTO search_runs (product_slug, started_at, finished_at, site_results)"
        " VALUES (?, ?, ?, ?)",
        (product_slug, now, now, json.dumps(site_results)),
    )
    conn.commit()
    return cur.lastrowid
