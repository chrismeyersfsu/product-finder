"""Geocoding and distance: turn a listing's "City, ST" into miles from home.

Owns the Nominatim lookup (behind the `_get` seam), the great-circle
formula, and a small built-in gazetteer of Triangle-area cities so the
feature works before any network call. Never touches listings or
products and never decides what "near" means — callers pass a
threshold. Callers rely on: `geocode()` returns (lat, lon) or None and
never raises on network failure; `GeoCache` geocodes each distinct
query once per database (misses are cached too, so an unresolvable
city costs one request, not one per scrape); live calls are spaced
>= 1 s apart to honor Nominatim's usage policy; `haversine_mi` is pure.
"""

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = "product-finder/0.1 (+https://github.com/chrismeyersfsu/product-finder; personal deal tracker)"
_MIN_INTERVAL = 1.0
_last_call = 0.0

# City centroids (lat, lon), keyed by "city, st" lower-cased. Enough to
# rank Facebook/Craigslist listings around Durham without a network.
BUILTIN: dict[str, tuple[float, float]] = {
    "durham, nc": (35.9940, -78.8986),
    "raleigh, nc": (35.7796, -78.6382),
    "chapel hill, nc": (35.9132, -79.0558),
    "cary, nc": (35.7915, -78.7811),
    "apex, nc": (35.7327, -78.8503),
    "morrisville, nc": (35.8235, -78.8256),
    "wake forest, nc": (35.9799, -78.5097),
    "hillsborough, nc": (36.0754, -79.0997),
    "carrboro, nc": (35.9101, -79.0753),
    "garner, nc": (35.7113, -78.6142),
    "fuquay-varina, nc": (35.5843, -78.8000),
    "holly springs, nc": (35.6513, -78.8336),
    "knightdale, nc": (35.7876, -78.4806),
    "clayton, nc": (35.6507, -78.4564),
    "burlington, nc": (36.0957, -79.4378),
    "graham, nc": (36.0690, -79.4006),
    "mebane, nc": (36.0960, -79.2670),
    "greensboro, nc": (36.0726, -79.7920),
    "high point, nc": (35.9557, -80.0053),
    "kernersville, nc": (36.1199, -80.0737),
    "winston-salem, nc": (36.0999, -80.2442),
    "charlotte, nc": (35.2271, -80.8431),
    "fayetteville, nc": (35.0527, -78.8784),
    "wilmington, nc": (34.2257, -77.9447),
    "asheville, nc": (35.5951, -82.5515),
    "greenville, nc": (35.6127, -77.3664),
}


class GeoError(Exception):
    pass


def _get(url: str, timeout: float = 15.0) -> str:
    """The one network seam; tests monkeypatch this with fixture text."""
    global _last_call
    wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise GeoError(f"HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise GeoError(str(getattr(e, "reason", e))) from e
    finally:
        _last_call = time.monotonic()


def normalize(query: str) -> str:
    return " ".join(query.split()).strip().lower()


def parse_nominatim(body: str) -> tuple[float, float] | None:
    """Pure: first hit's (lat, lon) from a Nominatim JSON response."""
    try:
        hits = json.loads(body)
    except ValueError:
        return None
    if not isinstance(hits, list) or not hits:
        return None
    try:
        return float(hits[0]["lat"]), float(hits[0]["lon"])
    except (KeyError, TypeError, ValueError):
        return None


def geocode(query: str) -> tuple[float, float] | None:
    """(lat, lon) for a free-text place; built-in gazetteer first, then
    Nominatim. None when unknown or the network fails."""
    if not query or not query.strip():
        return None
    key = normalize(query)
    if key in BUILTIN:
        return BUILTIN[key]
    url = f"{NOMINATIM}?{urllib.parse.urlencode({'q': query, 'format': 'json', 'limit': 1})}"
    try:
        body = _get(url)
    except GeoError:
        return None
    return parse_nominatim(body)


def haversine_mi(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle miles between two (lat, lon) pairs."""
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 3958.7613 * 2 * math.asin(math.sqrt(h))


_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS geocache (
  query TEXT PRIMARY KEY,
  lat REAL,
  lon REAL,
  fetched_at TEXT NOT NULL
);
"""


class GeoCache:
    """Memoizes geocode() in a `geocache` table on any sqlite3 connection
    (misses are stored as NULL lat/lon so they are not retried each run)."""

    def __init__(self, conn):
        self.conn = conn
        conn.executescript(_CACHE_SCHEMA)

    def lookup(self, query: str) -> tuple[float, float] | None:
        key = normalize(query)
        if not key:
            return None
        row = self.conn.execute("SELECT lat, lon FROM geocache WHERE query=?", (key,)).fetchone()
        if row is not None:
            lat, lon = row[0], row[1]
            return (lat, lon) if lat is not None and lon is not None else None
        hit = geocode(query)
        self.conn.execute(
            "INSERT OR REPLACE INTO geocache (query, lat, lon, fetched_at) VALUES (?, ?, ?, ?)",
            (
                key,
                hit[0] if hit else None,
                hit[1] if hit else None,
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )
        self.conn.commit()
        return hit

    def distance_mi(self, home: tuple[float, float], query: str | None) -> float | None:
        """Miles from home to a listing location string; None when unknown."""
        if not query:
            return None
        there = self.lookup(query)
        return round(haversine_mi(home, there), 1) if there else None
