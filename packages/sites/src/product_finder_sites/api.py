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

facebook_json is the plain-HTTP replacement for the browser-only
facebook_marketplace tier, ported from the winning prototype of a
2026-09-05 bake-off (product-finder-fb-requests/experiments/
facebook-requests/report.md, "Integration" section; fb.py is
proto_6_waterfall). Facebook server-renders the Marketplace search
document with every token the search itself needs embedded as JSON:
one GET harvests `lsd`/`jazoest`/`__spin_r,b,t`/`__hs`/`__hsi` and the
server-resolved lat/lon/radius (regexes below), then one POST replays
Facebook's own `CometMarketplaceSearchContentPaginationQuery` GraphQL
query (pinned `doc_id`) with `cursor: None` — the prototype's
null-cursor discovery: a null cursor returns the same first page the
document itself would embed, so this fetcher never needs to parse the
document's own feed, only its tokens. This pass fetches page 1 only
(`_FB_PAGE_SIZE` = 24 items); `page_info.end_cursor`/`has_next_page` in
the GraphQL response are there for a later pagination pass and are
otherwise unused. A login wall in the document is checked before any
session harvesting or POST, so a wall costs exactly one request and
raises immediately (never retried — see BROWSER_KINDS/_TIER_LABEL in
run.py for how that degrades to the browser tier). Retries
(_call_with_retries) cover transport failures and 429/5xx with capped
exponential backoff through the module-level `_sleep` seam (tests
monkeypatch it to a no-op); a 4xx other than 429 is never retried, and
the backoff base is 1s so a sustained retry sequence never exceeds
~1 request/second. fetch.py's FetchError carries no structured status
code or Retry-After, only an "HTTP nnn" message, so classification
here is message-based (_fb_status_of/_fb_retryable) rather than
attribute-based the way the prototype's own HTTPStatusError subclasses
were. Any bare exception escaping fetch._get/_post is normalized to
FetchError (the prototype's own author flagged this as his design's
one gap; grafted here from the bake-off's runner-up). GraphQL
soft-error and schema-drift handling (a 200 that isn't a success, a
renamed/missing field, a systemic drift too large to trust) lives in
parse.py's _parse_facebook_json, not here — this module only ever
returns (body, page_url) or raises FetchError.
"""

import base64
import json
import os
import random
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


# --------------------------------------------------------------------------
# facebook_json: see the module docstring for the full flow. Constants and
# regexes below are pinned from the 2026-09-05 capture (report.md/FLOW.md
# in product-finder-fb-requests); doc_id rots when Facebook ships a new JS
# bundle — see _parse_facebook_json_body's stale-doc_id handling in parse.py.
# --------------------------------------------------------------------------

_FB_GRAPHQL_URL = "https://www.facebook.com/api/graphql/"
_FB_DOC_ID = "27212616558440397"
_FB_FRIENDLY_NAME = "CometMarketplaceSearchContentPaginationQuery"
_FB_PAGE_SIZE = 24

# Durham, NC — what an anonymous document resolves to absent an explicit
# lat/lon/radius token (which a live document usually, but not always,
# carries); used only when harvesting can't find one.
_FB_DEFAULT_LAT = 35.9886
_FB_DEFAULT_LON = -78.9072
_FB_DEFAULT_RADIUS_KM = 65.0

_FB_LOGIN_MARKERS = ("login_form", 'action="/login/"', "You must log in")

# Two shapes seen in the wild: a live document's `["LSD",[],{"token":...}]`
# and a sanitized fixture's `["LSD",null,[],[{"token":...}]]` — the `null,`
# and the array-wrapping around the token dict are both optional.
_FB_LSD_RE = re.compile(r'"LSD",\s*(?:null,\s*)?\[\],\s*\[?\{"token":"([^"]+)"')
_FB_SPIN_R_RE = re.compile(r'"__spin_r":(\d+)')
_FB_SPIN_B_RE = re.compile(r'"__spin_b":"(\w+)"')
_FB_SPIN_T_RE = re.compile(r'"__spin_t":(\d+)')
_FB_HS_RE = re.compile(r'"haste_session":"([^"]+)"')
_FB_HSI_RE = re.compile(r'"hsi":"(\d+)"')
_FB_LATLON_RE = re.compile(
    r'"filter_location_latitude":([-\d.]+),"filter_location_longitude":([-\d.]+)'
)
_FB_RADIUS_RE = re.compile(r'"filter_radius_km":([\d.]+)')

# Matches fetch.py's own "HTTP {code}" convention so a bare
# fetch.FetchError("HTTP 429") is classified correctly even though it
# carries no structured .status attribute.
_FB_HTTP_CODE_RE = re.compile(r"\bHTTP (\d{3})\b")

_FB_MAX_ATTEMPTS = 4
_FB_BASE_DELAY_S = 1.0
_FB_MAX_DELAY_S = 8.0


def _sleep(seconds: float) -> None:
    """time.sleep through a seam so tests can monkeypatch retry backoff to
    a no-op instead of actually waiting."""
    time.sleep(seconds)


def _fb_backoff_delay(attempt: int) -> float:
    """attempt 1 -> ~1.0-1.1s, 2 -> ~2.0-2.2s, 3 -> ~4.0-4.4s, capped at
    _FB_MAX_DELAY_S; a 1s base keeps a sustained retry sequence at or
    under ~1 request/second."""
    exp = min(_FB_MAX_DELAY_S, _FB_BASE_DELAY_S * (2 ** (attempt - 1)))
    return exp + exp * 0.1 * random.random()


def _fb_status_of(exc: Exception) -> int | None:
    m = _FB_HTTP_CODE_RE.search(str(exc))
    return int(m.group(1)) if m else None


def _fb_retryable(exc: fetch.FetchError) -> bool:
    status = _fb_status_of(exc)
    if status is None:
        # No recognizable HTTP code in the message: transport-shaped
        # (connection reset, timeout, ...) — treat as retryable.
        return True
    return status == 429 or 500 <= status < 600


# Wall-clock of the last request sent to facebook.com, so consecutive
# queries (two requests each) never exceed ~1 request/second across the
# whole run — a burst of four back-to-back queries earned a GraphQL
# "Rate limit exceeded" soft error in testing.
_FB_MIN_INTERVAL_S = 1.0
_fb_last_request_at = 0.0

# GraphQL answers a rate limit as a 200 with this in errors[].message;
# retried with backoff here rather than handed to the browser tier, which
# would send ~86 more requests to the host that just throttled us.
_FB_RATE_LIMIT_MARK = "Rate limit exceeded"


def _fb_pace() -> None:
    global _fb_last_request_at
    wait = _FB_MIN_INTERVAL_S - (time.monotonic() - _fb_last_request_at)
    if wait > 0:
        _sleep(wait)
    _fb_last_request_at = time.monotonic()


def _fb_post_graphql(form: str, headers: dict) -> str:
    body = fetch._post(_FB_GRAPHQL_URL, data=form, headers=headers, timeout=20.0)
    if _FB_RATE_LIMIT_MARK in body[:2000]:
        raise fetch.FetchError("rate limited")
    return body


def _call_with_retries(fn, *args, max_attempts: int = _FB_MAX_ATTEMPTS, **kwargs):
    """Calls fn(*args, **kwargs), retrying per the policy in the module
    docstring. Every attempt is paced by _fb_pace and every attempt beyond
    the first sleeps via the _sleep seam first. A non-retryable FetchError
    (a 4xx other than 429) or the max_attempts'th failure propagates
    as-is."""
    attempt = 0
    while True:
        attempt += 1
        try:
            _fb_pace()
            return fn(*args, **kwargs)
        except fetch.FetchError as e:
            if not _fb_retryable(e) or attempt >= max_attempts:
                raise
            _sleep(_fb_backoff_delay(attempt))


def _fb_harvest_session(body: str, page_url: str) -> dict | None:
    """Returns None (not an error — the caller decides that's fatal, since
    this port always needs a session to build the GraphQL POST) when the
    document carries no `lsd`."""
    lsd_m = _FB_LSD_RE.search(body)
    if not lsd_m:
        return None
    lsd = lsd_m.group(1)

    def _grp(pattern: re.Pattern, default: str = "") -> str:
        m = pattern.search(body)
        return m.group(1) if m else default

    latlon = _FB_LATLON_RE.search(body)
    lat, lon = (
        (float(latlon.group(1)), float(latlon.group(2)))
        if latlon
        else (_FB_DEFAULT_LAT, _FB_DEFAULT_LON)
    )
    radius_m = _FB_RADIUS_RE.search(body)
    radius = float(radius_m.group(1)) if radius_m else _FB_DEFAULT_RADIUS_KM
    return {
        "lsd": lsd,
        "jazoest": "2" + str(sum(ord(c) for c in lsd)),
        "spin_r": _grp(_FB_SPIN_R_RE),
        "spin_b": _grp(_FB_SPIN_B_RE),
        "spin_t": _grp(_FB_SPIN_T_RE),
        "hs": _grp(_FB_HS_RE),
        "hsi": _grp(_FB_HSI_RE),
        "lat": lat,
        "lon": lon,
        "radius_km": radius,
        "referer": page_url,
    }


def _fb_graphql_variables(query: str, cursor: str | None, session: dict) -> dict:
    return {
        "count": _FB_PAGE_SIZE,
        "cursor": cursor,
        "params": {
            "bqf": {"callsite": "COMMERCE_MKTPLACE_WWW", "query": query},
            "browse_request_params": {
                "commerce_enable_local_pickup": True,
                "commerce_enable_shipping": True,
                "commerce_search_and_rp_available": True,
                "commerce_search_and_rp_category_id": [],
                "commerce_search_and_rp_condition": None,
                "commerce_search_and_rp_ctime_days": None,
                "filter_location_latitude": session["lat"],
                "filter_location_longitude": session["lon"],
                "filter_price_lower_bound": 0,
                "filter_price_upper_bound": 214748364700,
                "filter_radius_km": session["radius_km"],
            },
            "custom_request_params": {
                "browse_context": None,
                "contextual_filters": [],
                "referral_code": None,
                "referral_ui_component": None,
                "saved_search_strid": None,
                "search_vertical": "C2C",
                "seo_url": None,
                "serp_landing_settings": {"virtual_category_id": ""},
                "surface": "SEARCH",
                "virtual_contextual_filters": [],
            },
        },
        "scale": 1,
        "__relay_internal__pv__GHLShouldChangeMarketplaceSponsoredDataFieldNamerelayprovider": False,
    }


def _fb_graphql_form_body(query: str, cursor: str | None, session: dict) -> str:
    form = {
        "av": "0",
        "__user": "0",
        "__a": "1",
        "__comet_req": "15",
        "lsd": session["lsd"],
        "jazoest": session["jazoest"],
        "__hs": session["hs"],
        "__rev": session["spin_r"],
        "__hsi": session["hsi"],
        "__spin_r": session["spin_r"],
        "__spin_b": session["spin_b"],
        "__spin_t": session["spin_t"],
        "__crn": "comet.fbweb.CometMarketplaceSearchRoute",
        "fb_api_caller_class": "RelayModern",
        "fb_api_req_friendly_name": _FB_FRIENDLY_NAME,
        "server_timestamps": "true",
        "doc_id": _FB_DOC_ID,
        "variables": json.dumps(
            _fb_graphql_variables(query, cursor, session), separators=(",", ":")
        ),
    }
    return urllib.parse.urlencode(form)


def _fb_graphql_headers(session: dict) -> dict:
    return {
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": session["referer"],
        "Origin": "https://www.facebook.com",
        "X-FB-LSD": session["lsd"],
        "X-FB-Friendly-Name": _FB_FRIENDLY_NAME,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }


def _fetch_facebook_json(config: dict, query: str) -> tuple[str, str]:
    region = config.get("region", "durham")
    radius_km = config.get("radius_km", 80)
    doc_url = config["url"].format(
        region=region, query=urllib.parse.quote_plus(query), radius_km=radius_km
    )
    doc_body = _call_with_retries(fetch._get, doc_url, headers=config.get("headers"), timeout=20.0)
    if any(mark in doc_body for mark in _FB_LOGIN_MARKERS):
        raise fetch.FetchError("login wall")
    session = _fb_harvest_session(doc_body, doc_url)
    if session is None:
        raise fetch.FetchError("facebook_json: no session token (lsd) in search document")
    form = _fb_graphql_form_body(query, None, session)  # cursor=None: page 0, see module docstring
    headers = _fb_graphql_headers(session)
    graphql_body = _call_with_retries(_fb_post_graphql, form, headers)
    return graphql_body, doc_url


def fetch_facebook_json(config: dict, query: str) -> tuple[str, str]:
    """One GET of the Marketplace search document (harvests the session
    tokens the GraphQL POST needs), then one POST of
    CometMarketplaceSearchContentPaginationQuery with cursor=None for the
    first page of listings — see the module docstring for the full flow
    and the retry/login-wall policy. Never leaks anything but
    fetch.FetchError."""
    try:
        return _fetch_facebook_json(config, query)
    except fetch.FetchError:
        raise
    except Exception as e:  # a bare exception must not crash search_site
        raise fetch.FetchError(f"facebook_json: {type(e).__name__}: {e}") from e


FETCHERS = {
    "ebay_api": fetch_ebay_api,
    "bestbuy_api": fetch_bestbuy_api,
    "walmart_api": fetch_walmart_api,
    "goodwill_api": fetch_goodwill_api,
    "kroger_api": fetch_kroger_api,
    "autolist_api": fetch_autolist_api,
    "discogs_api": fetch_discogs_api,
    "facebook_json": fetch_facebook_json,
}
