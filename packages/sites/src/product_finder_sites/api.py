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
sent here and 401s surface as per-site errors).
"""

import base64
import json
import os
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


FETCHERS = {
    "ebay_api": fetch_ebay_api,
    "bestbuy_api": fetch_bestbuy_api,
    "walmart_api": fetch_walmart_api,
    "goodwill_api": fetch_goodwill_api,
}
