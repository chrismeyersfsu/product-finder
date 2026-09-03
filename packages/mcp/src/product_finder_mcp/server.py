"""MCP server: products, sites, searches, deals, backtests, and project self-modification.

Owns the MCP app (MCPServer), tool functions, and orchestration of
core (storage/scoring) + sites (fetch/parse) + geo (distance from
home). Never bypasses the fetch._get / geo._get seams and never writes
outside the project root. Callers
rely on: tools are plain functions (testable without a transport),
$PF_DB picks the database at call time, and $PF_PROJECT_ROOT scopes
the project_* tools.
"""

import argparse
import fnmatch
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from product_finder_backtest import engine
from product_finder_core import scoring, storage, units
from product_finder_core import seed as seed_mod
from product_finder_geo import geo
from product_finder_sites import run as run_mod
from product_finder_sites.spec import BUILTIN_SITES, EBAY_SOLD

try:  # optional browser tier: mcp extra "browser" (heavy Playwright deps)
    from product_finder_browser import wire as _wire_browser

    _wire_browser()
    BROWSER_WIRED = True
except ImportError:
    BROWSER_WIRED = False

app = MCPServer("product-finder")

_REPO_ROOT = Path(__file__).resolve().parents[4]


def _connect():
    return storage.connect(os.environ.get("PF_DB"))


def _root() -> Path:
    return Path(os.environ.get("PF_PROJECT_ROOT", _REPO_ROOT)).resolve()


def _resolve_in_root(path: str) -> Path:
    p = (_root() / path).resolve()
    if not p.is_relative_to(_root()):
        raise ValueError(f"path escapes project root: {path}")
    return p


def add_product(
    slug: str,
    name: str,
    description: str = "",
    queries: list[str] | None = None,
    criteria: list[dict] | None = None,
    extractors: dict | None = None,
    manual_checks: list[str] | None = None,
    max_price: float | None = None,
    sites: list[str] | None = None,
) -> dict:
    """Create or update a product to search for.

    queries: search strings sent to each site. extractors: field ->
    {pattern, type} regexes pulled from listing titles. criteria:
    weighted rules {field, op, value, weight, required, note} with ops
    gte/lte/eq/contains/one_of/matches/exists. See the seeded
    thin-client-laptop product for a full example. sites: site slugs
    this product is searched on; empty means every enabled site.
    """
    conn = _connect()
    return storage.upsert_product(
        conn,
        {
            "slug": slug,
            "name": name,
            "description": description,
            "queries": queries or [],
            "criteria": criteria or [],
            "extractors": extractors or {},
            "manual_checks": manual_checks or [],
            "sites": sites or [],
            "max_price": max_price,
        },
    )


def list_products() -> list[dict]:
    """List all products with their criteria."""
    return storage.list_products(_connect())


def get_product(slug: str) -> dict:
    """Fetch one product by slug."""
    product = storage.get_product(_connect(), slug)
    return product or {"error": f"no product: {slug}"}


def delete_product(slug: str) -> dict:
    """Delete a product and its stored listings."""
    return {"deleted": storage.delete_product(_connect(), slug)}


def add_site(slug: str, name: str, kind: str = "css", config: dict | None = None) -> dict:
    """Add or update a marketplace site. kind "css" config needs url
    (with {query}) plus item/title/price/link CSS selectors."""
    conn = _connect()
    storage.upsert_site(conn, {"slug": slug, "name": name, "kind": kind, "config": config or {}})
    return {"ok": True, "slug": slug}


def list_sites() -> list[dict]:
    """List configured sites (seeds the 22 built-ins on first call)."""
    conn = _connect()
    _ensure_sites(conn)
    return storage.list_sites(conn)


def set_site_enabled(slug: str, enabled: bool) -> dict:
    """Enable or disable a site for future searches."""
    return {"ok": storage.set_site_enabled(_connect(), slug, enabled)}


def _ensure_sites(conn) -> None:
    have = {s["slug"] for s in storage.list_sites(conn)}
    for site in BUILTIN_SITES:
        if site["slug"] not in have:
            storage.upsert_site(conn, site)


def _home(conn) -> tuple[float, float] | None:
    home = storage.get_setting(conn, "home")
    if home and home.get("lat") is not None and home.get("lon") is not None:
        return (home["lat"], home["lon"])
    return None


def set_home(address: str) -> dict:
    """Set the home address distances are measured from. Geocodes it
    (Nominatim) and stores address + lat/lon; run backfill_distances
    afterwards to fill in existing listings."""
    hit = geo.geocode(address)
    if not hit:
        return {"error": f"could not geocode: {address!r}"}
    conn = _connect()
    home = {"address": address, "lat": hit[0], "lon": hit[1]}
    storage.set_setting(conn, "home", home)
    return home


def get_home() -> dict:
    """The stored home address and coordinates, or {} if unset."""
    return storage.get_setting(_connect(), "home", {}) or {}


def backfill_distances() -> dict:
    """Recompute distance_mi for every listing that has a location
    (city centroid to home, great-circle). Needs set_home first."""
    conn = _connect()
    home = _home(conn)
    if not home:
        return {"error": "no home set; call set_home(address) first"}
    cache = geo.GeoCache(conn)
    updated = unknown = 0
    for row in storage.listings_with_location(conn):
        d = cache.distance_mi(home, row["location"])
        storage.set_listing_distance(conn, row["id"], d)
        updated += d is not None
        unknown += d is None
    conn.commit()
    return {"updated": updated, "unknown_location": unknown}


def backfill_unit_prices() -> dict:
    """Recompute {unit_qty, unit, unit_price} for every stored listing from
    its title and price — for listings ingested before unit pricing
    existed, or after units.py's parser improves."""
    conn = _connect()
    n = priced = 0
    for row in storage.listings_for_units(conn):
        u = units.unit_price(row["price"], row["title"])
        storage.set_listing_units(conn, row["id"], u)
        n += 1
        priced += u["unit_price"] is not None
    conn.commit()
    return {"listings": n, "with_unit_price": priced}


def run_search(product_slug: str, sites: list[str] | None = None, query: str | None = None) -> dict:
    """Search enabled sites for a product's queries; score and store results.

    sites: optional list of site slugs to restrict to; defaults to the
    product's own site list, then every enabled site. query: optional
    one-off query overriding the product's stored queries.
    """
    conn = _connect()
    product = storage.get_product(conn, product_slug)
    if not product:
        return {"error": f"no product: {product_slug}"}
    _ensure_sites(conn)
    site_rows = storage.list_sites(conn, enabled_only=True)
    sites = sites or product.get("sites") or None
    if sites:
        site_rows = [s for s in site_rows if s["slug"] in sites]
    queries = [query] if query else product["queries"]
    if not queries:
        return {"error": "product has no queries; pass query= or update the product"}

    result = run_mod.search_many(site_rows, queries)
    home = _home(conn)
    cache = geo.GeoCache(conn) if home else None
    counts: dict[str, int] = {}
    rejected = 0
    for li in result["listings"]:
        attrs = scoring.extract_attrs(li["title"], product["extractors"])
        merged = {**{k: v for k, v in li.items() if k not in ("attrs",)}, **attrs}
        if scoring.rejected(merged, product["criteria"]):
            rejected += 1
            continue
        score, hard_fails = scoring.score_listing(merged, product["criteria"])
        storage.upsert_listing(
            conn,
            {
                **li,
                "product_slug": product_slug,
                "attrs": attrs,
                "score": round(score, 3),
                "hard_fails": hard_fails,
                "distance_mi": cache.distance_mi(home, li.get("location")) if cache else None,
                **units.unit_price(li.get("price"), li["title"]),
            },
        )
        if li.get("price"):
            storage.append_observation(
                conn,
                {
                    **li,
                    "product_slug": product_slug,
                    "score": round(score, 3),
                    "hard_fails": hard_fails,
                    "kind": "seen",
                },
            )
        counts[li["site_slug"]] = counts.get(li["site_slug"], 0) + 1
    summary = {
        "stored": len(result["listings"]) - rejected,
        "rejected_non_product": rejected,
        "per_site": counts,
        "strategies": result["strategies"],
        "errors": result["errors"],
        "browser_wired": BROWSER_WIRED,
    }
    storage.record_search_run(conn, product_slug, summary)
    return summary


def query_listings(
    product_slug: str,
    min_score: float | None = None,
    max_price: float | None = None,
    site: str | None = None,
    limit: int = 25,
    include_hard_fails: bool = False,
    max_distance_mi: float | None = None,
    new_within_days: float | None = None,
    include_hidden: bool = False,
) -> list[dict]:
    """Query stored listings, best score first then cheapest.
    max_distance_mi keeps only listings within that many miles of home
    (listings with no known location are excluded when it is set).
    new_within_days keeps only listings first seen that recently; rows
    hidden with hide_listing() are omitted unless include_hidden."""
    return storage.query_listings(
        _connect(),
        product_slug,
        min_score=min_score,
        max_price=max_price,
        site_slug=site,
        include_hard_fails=include_hard_fails,
        limit=limit,
        max_distance_mi=max_distance_mi,
        include_hidden=include_hidden,
        first_seen_since=_since(new_within_days),
    )


def _since(days: float | None) -> str | None:
    if days is None:
        return None
    return (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")


def hide_listing(listing_id: int) -> dict:
    """Hide a listing from query_listings/best_deals and the Deals page.
    The row stays (and keeps refreshing on scrapes) so it never comes back
    as "new"; unhide_listing() reverses it. Also on the dashboard's Hidden page."""
    ok = storage.set_listing_hidden(_connect(), listing_id, True)
    return {"id": listing_id, "hidden": True} if ok else {"error": f"no listing: {listing_id}"}


def unhide_listing(listing_id: int) -> dict:
    """Put a hidden listing back into deals."""
    ok = storage.set_listing_hidden(_connect(), listing_id, False)
    return {"id": listing_id, "hidden": False} if ok else {"error": f"no listing: {listing_id}"}


def hidden_listings(product_slug: str | None = None) -> list[dict]:
    """Every hidden listing (optionally one product's), most recently hidden first."""
    return storage.hidden_listings(_connect(), product_slug)


def best_deals(
    product_slug: str,
    limit: int = 10,
    max_distance_mi: float | None = None,
    new_within_days: float | None = None,
) -> dict:
    """Top-scored listings with price-vs-median context, plus the
    product's manual checks to verify before buying. max_distance_mi
    restricts to listings within that many miles of home;
    new_within_days to listings first seen that recently (the median is
    still taken over that subset). Hidden listings never appear."""
    conn = _connect()
    product = storage.get_product(conn, product_slug)
    if not product:
        return {"error": f"no product: {product_slug}"}
    rows = storage.query_listings(
        conn,
        product_slug,
        limit=200,
        max_distance_mi=max_distance_mi,
        first_seen_since=_since(new_within_days),
    )
    scoring.annotate_deals(rows)
    return {"deals": rows[:limit], "manual_checks": product["manual_checks"]}


def seed_defaults() -> dict:
    """Seed the example product (thin-client laptop) and the 22 built-in sites."""
    conn = _connect()
    slugs = seed_mod.seed(conn)
    _ensure_sites(conn)
    return {"products": slugs, "sites": [s["slug"] for s in storage.list_sites(conn)]}


def _score_for(product: dict, title: str) -> tuple[float, list[str], dict]:
    attrs = scoring.extract_attrs(title, product["extractors"])
    score, hard_fails = scoring.score_listing(attrs, product["criteria"])
    return round(score, 3), hard_fails, attrs


def backfill_ebay_sold(product_slug: str, query: str | None = None) -> dict:
    """Backfill real historical sale prices from eBay sold/completed
    listings (eBay exposes roughly the last 90 days). Observations are
    scored like live search results and stored as kind='sold'."""
    conn = _connect()
    product = storage.get_product(conn, product_slug)
    if not product:
        return {"error": f"no product: {product_slug}"}
    queries = [query] if query else product["queries"]
    added = skipped = 0
    errors = []
    for q in queries:
        result = run_mod.search_site(EBAY_SOLD, q)
        if result["error"]:
            errors.append(f"{q}: {result['error']}")
            continue
        for li in result["listings"]:
            if not li.get("price") or not li.get("sold_at"):
                skipped += 1
                continue
            if scoring.rejected(
                scoring.extract_attrs(li["title"], product["extractors"]), product["criteria"]
            ):
                skipped += 1
                continue
            score, hard_fails, _ = _score_for(product, li["title"])
            fresh = storage.append_observation(
                conn,
                {
                    "product_slug": product_slug,
                    "site_slug": "ebay",
                    "url": li["url"],
                    "title": li["title"],
                    "price": li["price"],
                    "score": score,
                    "hard_fails": hard_fails,
                    "kind": "sold",
                    "observed_at": li["sold_at"],
                },
            )
            added += 1 if fresh else 0
    return {"added": added, "skipped_no_price_or_date": skipped, "errors": errors}


def add_price_observation(
    product_slug: str,
    site_slug: str,
    price: float,
    observed_at: str,
    title: str = "",
    url: str = "",
    kind: str = "seen",
) -> dict:
    """Manually record one historical price point (ISO observed_at).
    If a title is given it is scored against the product's criteria."""
    conn = _connect()
    product = storage.get_product(conn, product_slug)
    if not product:
        return {"error": f"no product: {product_slug}"}
    if title:
        note = scoring.rejected(
            scoring.extract_attrs(title, product["extractors"]), product["criteria"]
        )
        if note:
            return {"rejected": note}
    score, hard_fails, _ = _score_for(product, title) if title else (None, [], {})
    added = storage.append_observation(
        conn,
        {
            "product_slug": product_slug,
            "site_slug": site_slug,
            "url": url,
            "title": title,
            "price": price,
            "score": score,
            "hard_fails": hard_fails,
            "kind": kind,
            "observed_at": observed_at,
        },
    )
    return {"added": added}


def price_history_stats(product_slug: str) -> dict:
    """Observation counts, span, and per-site/per-kind mix for a product."""
    return storage.history_stats(_connect(), product_slug)


def run_backtest(
    product_slug: str,
    windows: list[int] | None = None,
    n_pivots: int = 200,
    min_score: float = 0.5,
    seed: int = 0,
    kinds: list[str] | None = None,
) -> dict:
    """Backtest 'best deal found' over lookback windows (default 3d,
    1/2/4/8/16 weeks) from n_pivots random pivot dates within the past
    year of observed history. Answers: does waiting longer get a
    better deal, and which site wins? Result is stored; interact with
    it later via get_backtest/list_backtests."""
    conn = _connect()
    if not storage.get_product(conn, product_slug):
        return {"error": f"no product: {product_slug}"}
    observations = storage.query_observations(conn, product_slug, kinds=kinds)
    results = engine.run(
        observations, windows=windows, n_pivots=n_pivots, min_score=min_score, seed=seed
    )
    backtest_id = storage.insert_backtest(conn, product_slug, results["params"], results)
    return {"backtest_id": backtest_id, **results}


def get_backtest(backtest_id: int) -> dict:
    """Fetch one stored backtest result by id."""
    row = storage.get_backtest(_connect(), backtest_id)
    return row or {"error": f"no backtest: {backtest_id}"}


def list_backtests(product_slug: str | None = None) -> list[dict]:
    """List stored backtests (id, product, params, created_at), newest first."""
    return storage.list_backtests(_connect(), product_slug)


def project_list_files(pattern: str = "*") -> list[str]:
    """List project files matching a glob pattern (relative to project root)."""
    root = _root()
    out = []
    for p in root.rglob("*"):
        if p.is_dir() or any(part in (".git", ".venv") for part in p.parts):
            continue
        rel = str(p.relative_to(root))
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(p.name, pattern):
            out.append(rel)
    return sorted(out)[:500]


def project_read_file(path: str) -> str:
    """Read a file inside the project root."""
    return _resolve_in_root(path).read_text()


def project_write_file(path: str, content: str) -> dict:
    """Write a file inside the project root (refuses .git). This is how
    the project modifies itself over MCP; run project_run_ci after."""
    p = _resolve_in_root(path)
    if ".git" in p.relative_to(_root()).parts:
        raise ValueError("refusing to write into .git")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return {"wrote": str(p.relative_to(_root())), "bytes": len(content)}


def project_run_ci() -> dict:
    """Run every package's ci.sh; returns per-package exit codes and output tails."""
    root = _root()
    results = {}
    for ci in sorted(root.glob("packages/*/ci.sh")):
        name = ci.parent.name
        try:
            proc = subprocess.run([str(ci)], capture_output=True, text=True, timeout=600, cwd=root)
            results[name] = {"exit": proc.returncode, "output": (proc.stdout + proc.stderr)[-4000:]}
        except subprocess.TimeoutExpired:
            results[name] = {"exit": -1, "output": "timeout after 600s"}
    return results


TOOLS = [
    add_product,
    list_products,
    get_product,
    delete_product,
    add_site,
    list_sites,
    set_site_enabled,
    run_search,
    query_listings,
    best_deals,
    hide_listing,
    unhide_listing,
    hidden_listings,
    set_home,
    get_home,
    backfill_distances,
    backfill_unit_prices,
    seed_defaults,
    backfill_ebay_sold,
    add_price_observation,
    price_history_stats,
    run_backtest,
    get_backtest,
    list_backtests,
    project_list_files,
    project_read_file,
    project_write_file,
    project_run_ci,
]
for _fn in TOOLS:
    app.tool()(_fn)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="product-finder-mcp", description=__doc__.splitlines()[0])
    p.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8848)
    args = p.parse_args(argv)
    if args.transport == "streamable-http":
        app.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        app.run(transport="stdio")
