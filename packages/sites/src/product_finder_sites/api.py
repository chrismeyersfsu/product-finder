"""API-tier fetchers: official site APIs, keyed by env-var credentials.

Owns endpoint URLs, auth flows, and credential lookup for the api-kind
strategies; all HTTP still goes through fetch._get/_post so tests fake
one seam. Never parses response bodies (parse.py owns that). Callers
rely on: each fetcher returns (body, url) or raises FetchError — a
missing credential raises a clear "<ENV_VAR> unset" FetchError so
run.py degrades to the next strategy instead of crashing.

goodwill_api is keyless (ShopGoodwill's public buyer API).
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

copart_csv is not a search API at all: Copart publishes its whole
auction inventory as one member-only CSV ("Download Sales Data" on
copart.com — the site itself is Incapsula-walled at every tier, and
its Angular search never hydrates headless). fetch_copart_csv keeps a
local copy at COPART_CSV (default: copart_salesdata.csv beside PF_DB,
else data/copart_salesdata.csv), re-downloads it with the COPART_COOKIES
cookie header (a logged-in Copart member session) once it is older
than config["max_age_hours"], and otherwise returns the cached file —
so a CSV saved by hand from the member page works with no cookies at
all. A stale copy is still served when a refresh fails (cookies expire).
The download URL in config is unverified against a member session;
the CSV's column names are documented by Copart's "CSV Sales Data"
page and parse.py matches them by normalized name.
"""

import base64
import json
import os
import time
import urllib.parse
from pathlib import Path

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


def _copart_cache_path() -> Path:
    if os.environ.get("COPART_CSV"):
        return Path(os.environ["COPART_CSV"])
    if os.environ.get("PF_DB"):
        return Path(os.environ["PF_DB"]).parent / "copart_salesdata.csv"
    return Path("data") / "copart_salesdata.csv"


def _looks_like_csv(body: str) -> bool:
    head = body.lstrip()[:2000].splitlines()
    return bool(head) and head[0].count(",") >= 10 and "<html" not in head[0].lower()


def fetch_copart_csv(config: dict, query: str) -> tuple[str, str]:
    """Copart's member CSV of every lot on sale, cached at COPART_CSV and
    refreshed with the COPART_COOKIES cookie header when older than
    config["max_age_hours"] (default 6). `query` is unused here — the
    parser filters rows, since the file is the whole inventory."""
    path = _copart_cache_path()
    max_age = float(config.get("max_age_hours", 6)) * 3600
    cached = path.exists()
    fresh = cached and time.time() - path.stat().st_mtime < max_age
    cookies = os.environ.get(config.get("cookies_env", "COPART_COOKIES"))
    if not fresh and cookies:
        try:
            body = fetch._get(
                config["url"],
                headers={"Cookie": cookies, "Accept": "text/csv,*/*;q=0.8"},
                timeout=float(config.get("timeout", 120)),
            )
            if not _looks_like_csv(body):
                raise fetch.FetchError("copart: response is not a CSV (COPART_COOKIES expired?)")
        except fetch.FetchError:
            if not cached:
                raise
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
            return body, config["url"]
    if not cached:
        raise fetch.FetchError(f"COPART_COOKIES unset and no CSV at {path}")
    return path.read_text(), path.as_uri() if path.is_absolute() else f"file:{path}"


FETCHERS = {
    "copart_csv": fetch_copart_csv,
    "ebay_api": fetch_ebay_api,
    "bestbuy_api": fetch_bestbuy_api,
    "walmart_api": fetch_walmart_api,
    "goodwill_api": fetch_goodwill_api,
    "kroger_api": fetch_kroger_api,
}
