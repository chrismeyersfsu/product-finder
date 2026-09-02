"""Search orchestration inside the sites concern: site x query -> listings.

Owns strategy iteration (API -> plain HTML -> browser), URL
construction ({query} is the URL-quoted query, {query_slug} its
hyphenated lowercase form for path-style searches), and error
containment around fetch/api + parse. Never
stores or scores (callers do that with packages/core). Callers rely
on: search_site() returns errors as values, never raises, reports
which strategy actually ran ("strategy") and every attempt
("attempts"); the joined error string labels every tier compactly
(api/css/browser/json) and calls a 200-with-no-items wall a
"challenge page". An empty page falls through to the next strategy.
Category-feed configs (local_filter) drop rows not matching the
query. search_many() dedupes listings by url across queries and keeps
a site's error only if no query ever succeeded there.
"""

import os
import re
import urllib.parse

from . import api, fetch, parse

# Compact tier labels for the per-site error string ("api: KEY unset;
# css: HTTP 403; browser: challenge page") shown verbatim by /sites.
_TIER_LABEL = {"css": "css", "reddit_json": "json"}

# Wall/challenge pages that answer 200 with no items.
_CHALLENGE_RE = re.compile(
    r"captcha|robot or human|just a moment|security measure|access denied"
    r"|pardon our interruption|are you a human|verify you are"
    r"|sorry! something went wrong"  # amazon's bot wall
    r"|enable js and disable any ad blocker",  # foodlion's DataDome wall
    re.IGNORECASE,
)

# Strategy kinds fetched by the browser seam (config may carry a
# cookies_env naming an env var whose cookie header logs the page in).
BROWSER_KINDS = {"browser_css", "facebook_marketplace", "carscom", "carvana"}


def _label(kind: str) -> str:
    if kind in _TIER_LABEL:
        return _TIER_LABEL[kind]
    return "api" if kind.endswith("_api") else "browser"


def _local_filter(listings: list[dict], query: str) -> list[dict]:
    """Keep listings whose title carries a distinctive query token — for
    category-feed sites (woot) that can't search server-side."""
    tokens = [w for w in re.findall(r"\w+", query.lower()) if len(w) >= 6]
    tokens = tokens or [w for w in re.findall(r"\w+", query.lower())]
    return [li for li in listings if any(t in li["title"].lower() for t in tokens)]


def _strategies(site: dict) -> list[dict]:
    if site["kind"] == "tiered":
        return site["config"]["strategies"]
    return [{"kind": site["kind"], "config": site["config"]}]


def _fetch(strategy: dict, query: str) -> tuple[str, str]:
    """Returns (body, page_url) for one strategy attempt."""
    kind = strategy["kind"]
    config = strategy["config"]
    if kind in api.FETCHERS:
        return api.FETCHERS[kind](config, query)
    params = {k: v for k, v in config.items() if isinstance(v, str | int | float)}
    params["query"] = urllib.parse.quote_plus(query)
    # {query_slug}: "kia ev9 land" -> "kia-ev9-land" for path-style searches
    params["query_slug"] = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
    url = config["url"].format(**params)
    if kind in BROWSER_KINDS:
        cookies = os.environ.get(config["cookies_env"]) if config.get("cookies_env") else None
        return fetch._get_browser(url, config.get("wait"), cookies=cookies), url
    return fetch._get(url, headers=config.get("headers")), url


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
        except parse.LoginWall as e:
            attempts.append({"strategy": kind, "error": str(e)})
            continue
        except Exception as e:  # a bad parse must not kill the run
            attempts.append({"strategy": kind, "error": f"parse: {e}"})
            continue
        if listings and strategy["config"].get("local_filter"):
            parsed = len(listings)
            listings = _local_filter(listings, query)
            if not listings:
                attempts.append(
                    {"strategy": kind, "error": f"0 of {parsed} feed items match query"}
                )
                continue
        if not listings:
            reason = "challenge page" if _CHALLENGE_RE.search(body[:200_000]) else "no items parsed"
            attempts.append({"strategy": kind, "error": reason})
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
    error = "; ".join(f"{_label(a['strategy'])}: {a['error']}" for a in attempts) or "no strategies"
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
                if site["slug"] not in strategies:  # never overwrite a success
                    errors[site["slug"]] = result["error"]
                continue
            strategies[site["slug"]] = result["strategy"]
            errors.pop(site["slug"], None)
            for li in result["listings"]:
                seen.setdefault(li["url"], li)
    return {"listings": list(seen.values()), "errors": errors, "strategies": strategies}
