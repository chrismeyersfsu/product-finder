"""SQLite persistence for products, sites, and listings.

Owns the schema, connections, and all SQL. Never does network I/O and
never interprets criteria (scoring.py owns that). Callers rely on:
JSON-typed columns are always valid JSON, `connect()` returns rows as
sqlite3.Row, and upsert_listing() keys on (product_slug, url) so
re-running a search refreshes price/last_seen instead of duplicating.
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
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  UNIQUE(product_slug, url)
);
CREATE TABLE IF NOT EXISTS search_runs (
  id INTEGER PRIMARY KEY,
  product_slug TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  site_results TEXT NOT NULL DEFAULT '{}'
);
"""

_JSON_PRODUCT_FIELDS = ("queries", "criteria", "extractors", "manual_checks")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DEFAULT_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    return conn


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
                                 manual_checks, max_price, created_at, updated_at)
           VALUES (:slug, :name, :description, :queries, :criteria, :extractors,
                   :manual_checks, :max_price, :now, :now)
           ON CONFLICT(slug) DO UPDATE SET
             name=excluded.name, description=excluded.description, queries=excluded.queries,
             criteria=excluded.criteria, extractors=excluded.extractors,
             manual_checks=excluded.manual_checks, max_price=excluded.max_price,
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
        "now": now,
    }
    cur = conn.execute(
        """INSERT INTO listings (product_slug, site_slug, url, title, price, currency,
                                 condition, location, seller_rating, seller_feedback_count,
                                 attrs, score, hard_fails, first_seen, last_seen)
           VALUES (:product_slug, :site_slug, :url, :title, :price, :currency,
                   :condition, :location, :seller_rating, :seller_feedback_count,
                   :attrs, :score, :hard_fails, :now, :now)
           ON CONFLICT(product_slug, url) DO UPDATE SET
             title=excluded.title, price=excluded.price, condition=excluded.condition,
             seller_rating=excluded.seller_rating,
             seller_feedback_count=excluded.seller_feedback_count,
             attrs=excluded.attrs, score=excluded.score, hard_fails=excluded.hard_fails,
             last_seen=excluded.last_seen""",
        row,
    )
    conn.commit()
    return cur.lastrowid


def query_listings(
    conn: sqlite3.Connection,
    product_slug: str,
    min_score: float | None = None,
    max_price: float | None = None,
    site_slug: str | None = None,
    include_hard_fails: bool = False,
    limit: int = 50,
) -> list[dict]:
    sql = "SELECT * FROM listings WHERE product_slug=?"
    args: list = [product_slug]
    if min_score is not None:
        sql += " AND score >= ?"
        args.append(min_score)
    if max_price is not None:
        sql += " AND price IS NOT NULL AND price <= ?"
        args.append(max_price)
    if site_slug:
        sql += " AND site_slug=?"
        args.append(site_slug)
    if not include_hard_fails:
        sql += " AND hard_fails = '[]'"
    sql += " ORDER BY score DESC NULLS LAST, price ASC NULLS LAST LIMIT ?"
    args.append(limit)
    out = []
    for row in conn.execute(sql, args).fetchall():
        d = dict(row)
        d["attrs"] = json.loads(d["attrs"])
        d["hard_fails"] = json.loads(d["hard_fails"])
        out.append(d)
    return out


def record_search_run(conn: sqlite3.Connection, product_slug: str, site_results: dict) -> int:
    now = _now()
    cur = conn.execute(
        "INSERT INTO search_runs (product_slug, started_at, finished_at, site_results)"
        " VALUES (?, ?, ?, ?)",
        (product_slug, now, now, json.dumps(site_results)),
    )
    conn.commit()
    return cur.lastrowid
