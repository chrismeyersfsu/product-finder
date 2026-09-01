"""Search orchestration inside the sites concern: site x query -> listings.

Owns strategy iteration (API -> plain HTML -> browser), URL
construction, and error containment around fetch/api + parse. Never
stores or scores (callers do that with packages/core). Callers rely
on: search_site() returns errors as values, never raises, reports
which strategy actually ran ("strategy") and every attempt
("attempts"), and an empty page falls through to the next strategy —
bot walls often 200 with no items. search_many() dedupes listings by
url across queries.
"""

import urllib.parse

from . import api, fetch, parse


def _strategies(site: dict) -> list[dict]:
    if site["kind"] == "tiered":
        return site["config"]["strategies"]
    return [{"kind": site["kind"], "config": site["config"]}]


def _fetch(strategy: dict, query: str) -> tuple[str, str]:
    """Returns (body, page_url) for one strategy attempt."""
    kind = strategy["kind"]
    if kind in api.FETCHERS:
        return api.FETCHERS[kind](strategy["config"], query)
    url = strategy["config"]["url"].format(query=urllib.parse.quote_plus(query))
    if kind == "browser_css":
        return fetch._get_browser(url, strategy["config"].get("wait")), url
    return fetch._get(url), url


def search_site(site: dict, query: str) -> dict:
    attempts = []
    for strategy in _strategies(site):
        kind = strategy["kind"]
        try:
            body, page_url = _fetch(strategy, query)
            listings = parse.parse_listings(strategy, page_url, body)
        except fetch.FetchError as e:
            attempts.append({"strategy": kind, "error": str(e)})
            continue
        except Exception as e:  # a bad parse must not kill the run
            attempts.append({"strategy": kind, "error": f"parse: {e}"})
            continue
        if not listings:
            attempts.append({"strategy": kind, "error": "no items parsed"})
            continue
        for li in listings:
            li["site_slug"] = site["slug"]
        return {
            "site": site["slug"],
            "listings": listings,
            "strategy": kind,
            "attempts": attempts,
            "error": None,
        }
    error = "; ".join(f"{a['strategy']}: {a['error']}" for a in attempts) or "no strategies"
    return {
        "site": site["slug"],
        "listings": [],
        "strategy": None,
        "attempts": attempts,
        "error": error,
    }


def search_many(sites: list[dict], queries: list[str]) -> dict:
    """Run every query against every site. Returns {"listings": [...],
    "errors": {site: msg}, "strategies": {site: kind_that_ran}} with
    url-deduped listings."""
    seen: dict[str, dict] = {}
    errors: dict[str, str] = {}
    strategies: dict[str, str] = {}
    for site in sites:
        for query in queries:
            result = search_site(site, query)
            if result["error"]:
                errors[site["slug"]] = result["error"]
                continue
            strategies[site["slug"]] = result["strategy"]
            for li in result["listings"]:
                seen.setdefault(li["url"], li)
    return {"listings": list(seen.values()), "errors": errors, "strategies": strategies}
