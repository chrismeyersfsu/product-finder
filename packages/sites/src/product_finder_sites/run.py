"""Search orchestration inside the sites concern: site x query -> listings.

Owns URL construction and error containment around fetch + parse.
Never stores or scores (callers do that with packages/core). Callers
rely on: search_site() returns errors as values, never raises, and
search_many() dedupes listings by url across queries.
"""

import urllib.parse

from . import fetch, parse


def search_site(site: dict, query: str) -> dict:
    url = site["config"]["url"].format(query=urllib.parse.quote_plus(query))
    try:
        body = fetch._get(url)
        listings = parse.parse_listings(site, url, body)
    except fetch.FetchError as e:
        return {"site": site["slug"], "listings": [], "error": str(e)}
    except Exception as e:  # a bad parse must not kill the run
        return {"site": site["slug"], "listings": [], "error": f"parse: {e}"}
    for li in listings:
        li["site_slug"] = site["slug"]
    return {"site": site["slug"], "listings": listings, "error": None}


def search_many(sites: list[dict], queries: list[str]) -> dict:
    """Run every query against every site. Returns
    {"listings": [...], "errors": {site_slug: error}} with url-deduped listings."""
    seen: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for site in sites:
        for query in queries:
            result = search_site(site, query)
            if result["error"]:
                errors[site["slug"]] = result["error"]
                continue
            for li in result["listings"]:
                seen.setdefault(li["url"], li)
    return {"listings": list(seen.values()), "errors": errors}
