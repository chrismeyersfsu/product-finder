"""API-tier fetchers: official site APIs, keyed by env-var credentials.

Owns endpoint URLs, auth flows, and credential lookup for the api-kind
strategies; all HTTP still goes through fetch._get/_post so tests fake
one seam. Never parses response bodies (parse.py owns that). Callers
rely on: each fetcher returns (body, url) or raises FetchError — a
missing credential raises a clear "<ENV_VAR> unset" FetchError so
run.py degrades to the next strategy instead of crashing.

goodwill_api and autolist_api are keyless. autolist_api is Autolist's
public JSON search (autolist.com/search — the endpoint its own SPA
calls; CarGurus-owned, so its rows are CarGurus dealer inventory):
free-text `keywords`, config zip/radius_mi/condition, 50 rows a page.
Verified live from this network on 2026-09-02.

discogs_api is also keyless: Discogs' marketplace *pages* are
Cloudflare-walled, but api.discogs.com is open anonymously. One search
call (GET /database/search, free-text `q=` plus config["format"],
default "vinyl") followed by up to config["max_releases"] (default 8)
GET /releases/{id} lookups — several calls under one seam, like
fetch_kroger_api — paced >= 0.3s apart per Discogs' ~25 req/min
anonymous rate limit; a 429 from either call raises
FetchError("discogs: rate limited"). config["skip_reissues"] (default
True) drops search rows whose format[] carries Reissue/Unofficial
Release/Repress before spending the lookup budget, so it goes to
original pressings first (falls back to the unfiltered list if that
would leave nothing). config["currency"] (default "USD") is passed as
`curr_abbr` on each release call. Returns one JSON body — not a raw
Discogs response — shaped {"query", "releases": [...]}, each entry the
search result merged with a "release" key holding that id's
/releases/{id} body; parse.py's _parse_discogs_api reads this shape,
not Discogs' own. Verified live from this network on 2026-09-04
("beck odelay" surfaces release 235913, the 1996 US Bong Load
pressing, with ~65 copies for sale).
Credentials: EBAY_CLIENT_ID + EBAY_CLIENT_SECRET (eBay Browse API,
client-credentials OAuth), BESTBUY_API_KEY (Best Buy Products API),
WALMART_API_KEY (Walmart affiliate API — best-effort: Walmart's
production affiliate API wants signed headers; a plain key header is
sent here and 401s surface as per-site errors). KROGER_CLIENT_ID +
KROGER_CLIENT_SECRET (Kroger public Products API, which also covers
the Harris Teeter banner) — client-credentials OAuth scoped to
`product.compact`; this scope's token is assumed to also cover the
Location API (both are typically enabled together on a Kroger
developer app). fetch_kroger_api does three calls under one seam: get
a token, resolve the nearest store of `config["chain"]` to
`config["zip"]` via /v1/locations, then search /v1/products filtered
to that store's locationId — response shapes are per Kroger's
published API reference; not independently curl-verified since this
package holds no Kroger credentials.
"""

import base64
import json
import os
import re
import time
import urllib.parse

from . import fetch


def _require_env(*names: str) -> list[str]:
    values = [os.environ.get(n) for n in names]
    missing = [n for n, v in zip(names, values, strict=True) if not v]
    if missing:
        raise fetch.FetchError(f"{'/'.join(missing)} unset")
    return values  # type: ignore[return-value]


def fetch_ebay_api(config: dict, query: str) -> tuple[str, str]:
    cid, secret = _require_env("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET")
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    token_body = fetch._post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        data="grant_type=client_credentials&scope="
        + urllib.parse.quote_plus("https://api.ebay.com/oauth/api_scope"),
        headers={"Authorization": f"Basic {basic}"},
    )
    try:
        token = json.loads(token_body)["access_token"]
    except (ValueError, KeyError) as e:
        raise fetch.FetchError("ebay oauth: no access_token in response") from e
    url = (
        "https://api.ebay.com/buy/browse/v1/item_summary/search?q="
        + urllib.parse.quote_plus(query)
        + "&limit=50"
    )
    return fetch._get(url, headers={"Authorization": f"Bearer {token}"}), url


def fetch_bestbuy_api(config: dict, query: str) -> tuple[str, str]:
    (key,) = _require_env("BESTBUY_API_KEY")
    url = (
        "https://api.bestbuy.com/v1/products(search="
        + urllib.parse.quote_plus(query)
        + f")?apiKey={key}&format=json&pageSize=50&show=name,salePrice,url"
    )
    return fetch._get(url), url


def fetch_walmart_api(config: dict, query: str) -> tuple[str, str]:
    (key,) = _require_env("WALMART_API_KEY")
    url = (
        "https://developer.api.walmart.com/api-proxy/service/affil/product/v2/search?query="
        + urllib.parse.quote_plus(query)
    )
    return fetch._get(url, headers={"WM_SEC.ACCESS_TOKEN": key}), url


_GOODWILL_URL = "https://buyerapi.shopgoodwill.com/api/Search/ItemListing"
# The buyer API wants the full search form; only searchText varies.
_GOODWILL_BODY = {
    "page": 1,
    "pageSize": 10,  # the API 500s above pageSize 10
    "catIds": "-1",
    "categoryId": 0,
    "lowPrice": "0",
    "highPrice": "999999",
    "sortColumn": "1",
    "sortDescending": "false",
    "searchClosedAuctions": False,
    "closedAuctionEndingDate": "1/1/1",
    "closedAuctionDaysBack": "7",
    "searchDescriptions": False,
    "searchPickupOnly": False,
    "searchNoPickupOnly": False,
    "searchOneCentShippingOnly": False,
    "searchBuyNowOnly": "",
    "searchCanadaShipping": False,
    "searchInternationalShippingOnly": False,
    "searchUSOnlyShippingOnly": False,
    "savedSearchId": 0,
    "useBuyerPrefs": False,
    "searchAllSellers": "",
    "isSize": False,
    "isWeddingCatagory": "false",
    "isMultipleCategoryIds": False,
    "isFromHeaderMenuTab": False,
    "layout": "",
    "partNumber": "",
    "categoryLevelNo": "1",
    "categoryLevel": 1,
    "searchAuctionCloseTimeFrom": "",
    "searchAuctionCloseTimeTo": "",
}


def fetch_goodwill_api(config: dict, query: str) -> tuple[str, str]:
    """ShopGoodwill's public buyer API: keyless JSON POST, no credentials."""
    body = json.dumps({**_GOODWILL_BODY, "searchText": query})
    resp = fetch._post(_GOODWILL_URL, body, headers={"Content-Type": "application/json"})
    return resp, _GOODWILL_URL


_AUTOLIST_URL = "https://www.autolist.com/search"


def fetch_autolist_api(config: dict, query: str) -> tuple[str, str]:
    """Autolist's keyless JSON search, scoped to config["radius_mi"]
    miles of config["zip"] and config["condition"] ("used" by default;
    "new" or omit for both)."""
    params = {
        "keywords": query,
        "zip": config["zip"],
        "radius": config.get("radius_mi", 100),
        "limit": config.get("limit", 50),
    }
    if config.get("condition"):
        params["condition"] = config["condition"]
    url = _AUTOLIST_URL + "?" + urllib.parse.urlencode(params)
    return fetch._get(url, headers={"Accept": "application/json"}), url


_KROGER_TOKEN_URL = "https://api.kroger.com/v1/connect/oauth2/token"
_KROGER_LOCATIONS_URL = "https://api.kroger.com/v1/locations"
_KROGER_PRODUCTS_URL = "https://api.kroger.com/v1/products"


def _kroger_token() -> str:
    cid, secret = _require_env("KROGER_CLIENT_ID", "KROGER_CLIENT_SECRET")
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    body = fetch._post(
        _KROGER_TOKEN_URL,
        data="grant_type=client_credentials&scope=product.compact",
        headers={"Authorization": f"Basic {basic}"},
    )
    try:
        return json.loads(body)["access_token"]
    except (ValueError, KeyError) as e:
        raise fetch.FetchError("kroger oauth: no access_token in response") from e


def _kroger_location_id(config: dict, headers: dict) -> str:
    """config["location_id"] pins a store outright; otherwise the nearest
    non-fuel store of config["chain"] (Kroger's banner code, e.g. "HART"
    for Harris Teeter) to config["zip"]."""
    if config.get("location_id"):
        return str(config["location_id"])
    url = (
        _KROGER_LOCATIONS_URL
        + "?filter.zipCode.near="
        + urllib.parse.quote_plus(config["zip"])
        + "&filter.chain="
        + urllib.parse.quote_plus(config["chain"])
        + f"&filter.radiusInMiles={config.get('radius_miles', 15)}&filter.limit=10"
    )
    body = fetch._get(url, headers=headers)
    try:
        stores = [s for s in json.loads(body)["data"] if "fuel" not in s.get("name", "").lower()]
        return stores[0]["locationId"]
    except (ValueError, KeyError, IndexError) as e:
        raise fetch.FetchError(f"kroger: no {config['chain']} location near {config['zip']}") from e


def fetch_kroger_api(config: dict, query: str) -> tuple[str, str]:
    """Kroger Products API, scoped to the store nearest `config['zip']` of
    banner `config['chain']` (e.g. "HART"). Three HTTP calls behind
    one seam: token, then locations (resolves locationId), then products."""
    token = _kroger_token()
    headers = {"Authorization": f"Bearer {token}"}
    location_id = _kroger_location_id(config, headers)
    url = (
        _KROGER_PRODUCTS_URL
        + "?filter.term="
        + urllib.parse.quote_plus(query)
        + f"&filter.locationId={location_id}&filter.limit=10"
    )
    return fetch._get(url, headers=headers), url


_DISCOGS_SEARCH_URL = "https://api.discogs.com/database/search"
_DISCOGS_UA = "product-finder/0.1 +https://github.com/chrismeyersfsu/product-finder"
# Discogs' ~25 req/min unauthenticated limit: keep every call at least
# this far apart, including the gap after the one search call.
_DISCOGS_MIN_SPACING = 0.3
# Search rows carrying any of these in format[] are a reissue/bootleg/
# repress rather than an original pressing.
_DISCOGS_SKIP_FORMATS = {"reissue", "unofficial release", "repress"}
# Trailing words dropped from the free-text query since format=vinyl
# (or whatever config["format"] is) already scopes the search.
_DISCOGS_NOISE_WORDS = {"vinyl", "lp", "record", "records"}
_DISCOGS_ARTIST_DISAMBIG_RE = re.compile(r"\s*\(\d+\)\s*$")


def _discogs_clean_query(query: str) -> str:
    """Strip trailing noise words ("... vinyl", "... lp") one at a time
    so "Beck Odelay vinyl lp" -> "Beck Odelay"; never empties the query."""
    words = query.split()
    while len(words) > 1 and words[-1].strip(",.!").lower() in _DISCOGS_NOISE_WORDS:
        words.pop()
    return " ".join(words) if words else query


def _discogs_is_reissue(formats: list) -> bool:
    descs = {str(f).strip().lower() for f in formats or []}
    return bool(descs & _DISCOGS_SKIP_FORMATS)


def _discogs_get(url: str) -> str:
    try:
        return fetch._get(url, headers={"User-Agent": _DISCOGS_UA})
    except fetch.FetchError as e:
        if "429" in str(e):
            raise fetch.FetchError("discogs: rate limited") from e
        raise


def fetch_discogs_api(config: dict, query: str) -> tuple[str, str]:
    """Discogs' public database API: keyless. One search call, then up
    to config["max_releases"] per-release lookups behind the same seam
    (see the module docstring for the full shape/rate-limit contract).
    Every HTTP call — including the search — is followed by a >= 0.3s
    sleep before the next one, so the whole fetch stays under Discogs'
    ~25/min anonymous limit regardless of how many releases are looked
    up."""
    fmt = config.get("format", "vinyl")
    max_releases = config.get("max_releases", 8)
    skip_reissues = config.get("skip_reissues", True)
    currency = config.get("currency", "USD")

    q = _discogs_clean_query(query)
    search_url = (
        f"{_DISCOGS_SEARCH_URL}?q={urllib.parse.quote_plus(q)}"
        f"&format={urllib.parse.quote_plus(fmt)}&per_page=25"
    )
    search_body = _discogs_get(search_url)
    time.sleep(_DISCOGS_MIN_SPACING)
    try:
        results = json.loads(search_body).get("results") or []
    except ValueError:
        results = []

    if skip_reissues:
        originals = [r for r in results if not _discogs_is_reissue(r.get("format") or [])]
        results = originals or results  # an all-reissue query still gets something

    releases = []
    for i, r in enumerate(results[:max_releases]):
        release_id = r.get("id")
        if not release_id:
            continue
        if i:
            time.sleep(_DISCOGS_MIN_SPACING)
        release_url = f"https://api.discogs.com/releases/{release_id}?curr_abbr={currency}"
        try:
            release_body = _discogs_get(release_url)
        except fetch.FetchError:
            continue  # one bad pressing lookup shouldn't sink the whole search
        try:
            release = json.loads(release_body)
        except ValueError:
            continue
        releases.append({**r, "release": release})

    return json.dumps({"query": q, "releases": releases}), search_url


FETCHERS = {
    "ebay_api": fetch_ebay_api,
    "bestbuy_api": fetch_bestbuy_api,
    "walmart_api": fetch_walmart_api,
    "goodwill_api": fetch_goodwill_api,
    "kroger_api": fetch_kroger_api,
    "autolist_api": fetch_autolist_api,
    "discogs_api": fetch_discogs_api,
}
