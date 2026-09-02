"""Built-in site registry: 28 marketplaces as pure data.

Owns the default site specs — nothing else. A site is
{slug, name, kind, config}. kind "tiered" holds an ordered
config["strategies"] list of {kind, config} tried best-first:
official API (ebay_api/bestbuy_api/walmart_api/kroger_api/reddit_json,
keyed by env vars — see api.py), then plain-HTML "css", then
"browser_css" (same selectors, page fetched by a real browser) for
JS-heavy sites. A flat kind ("css", "reddit_json") is a
single-strategy site. css/browser_css config: a search `url` with a
{query} placeholder plus CSS selectors (item/title/price/link,
optional link_attr and seller). Never fetches, parses, or stores;
callers copy these into the sites table and may override any config
there.

Selectors are best-effort snapshots of each site's public search page
and will rot as sites redesign; the css/browser_css sets below were
re-derived from live pages on 2026-09-01. JS-heavy or bot-blocking
sites get a browser_css fallback tier; without API keys or a wired
browser those tiers degrade to per-site errors. Live status from this
network as of 2026-09-01: target/bestbuy work on the browser tier,
officedepot/swappa on plain css, shopgoodwill on its keyless buyer
API (goodwill_api), woot parses its computers feed with a local
keyword filter (feed rarely carries a given product). Still walled
regardless of tier: ebay/walmart (API tiers exist — add keys),
amazon, adorama, microcenter, mercari, backmarket (selectors are
live-verified but its wall is intermittent), offerup (geo-locates
anonymous searches by IP and returns nothing), govdeals, staples
(resets even real-browser connections), reddit (403s datacenter IPs
on .json). Craigslist needs a region subdomain in its url.

facebook-marketplace is browser-only (kind "facebook_marketplace":
fully JS-rendered, no public API, usually login-walled) with its own
parser in parse.py. config keys: region (a Marketplace location slug,
default "durham"), radius_km (default 80 ~ 50 miles around the region
— the URL takes no ZIP anonymously), and cookies_env naming the env
var (FB_COOKIES) whose cookie-header value, when set, is injected into
the browser context for a logged-in search.

harris-teeter and food-lion are the two local grocery sites (added for
a Durham 27705 shopper), each scoped to one physical store via a
static `location` string and `condition: "new"` in config (see
parse.py's css/kroger_api handling of those two keys) rather than a
per-card selector — a store's shelf listings have one fixed address,
unlike a marketplace card. harris-teeter is a Kroger banner: its api
tier (kroger_api, KROGER_CLIENT_ID/KROGER_CLIENT_SECRET) resolves the
config["location_id"] store (else nearest HART store to config["zip"]) and is the only
tier expected to work — harristeeter.com itself resets *both* plain
HTTP and a real headless-browser connection from this network
(ERR_HTTP2_PROTOCOL_ERROR, the same Akamai wall as staples), so its
css/browser_css selectors below are an unverified best-effort guess,
never seen against a real response. food-lion has no official API
(Ahold Delhaize runs no public developer portal for it, unlike Kroger)
and foodlion.com is walled by DataDome at every tier from this network
— plain HTTP 403s outright and a real headless browser is served a
"please enable JS" captcha interstitial instead of results — so all of
food-lion's tiers are expected to degrade to a per-site error today;
its css/browser_css selectors are likewise an unverified guess, kept
so the strategy list is ready the day the wall lifts or a JSON search
endpoint is found. aldi is the third grocery site: aldi.us's storefront
is an Instacart-powered SPA (plain HTTP to the search path 404s), so
only its browser_css tier is expected to work; its selectors WERE
captured from a real headless render (tests/fixtures/aldi.html) — the
visible price is split across spans, so the price selector targets the
screen-reader-only "Current price: $6.45" span instead.

Three dealer used-car sites, probed 2026-09-02. autolist (kind
"autolist_api", flat) is autolist.com's own keyless JSON search — CarGurus's
dealer inventory (Autolist is CarGurus-owned) — the one used-car search
reachable without a browser; config: zip, radius_mi (100), condition
("used"; drop it for new+used). cars-com (kind "carscom", browser-only:
Cloudflare on plain HTTP, renders headless) takes a free-text `keyword`
plus zip/maximum_distance; its parser reads each card's
data-vehicle-details JSON. carvana (kind "carvana", browser-only,
server-rendered) has no free-text search: its URL is a
make-model[-trim] path built from {query_slug}, so a query that isn't
a slug Carvana knows ("EV9 AWD") lands on a generic page with no
vehicle JSON-LD and reports "no items parsed"; Carvana delivers
nationwide, so its rows carry no location. Still walled even in a
headless browser: autotrader, cargurus, carfax, truecar (captcha),
carmax, edmunds, kbb (Akamai "Access Denied"); hemmings renders but is
a classic-car site.
"""


def _css(slug, name, url, item, title, price, link, link_attr="href", seller=None, **extra):
    config = {
        "url": url,
        "item": item,
        "title": title,
        "price": price,
        "link": link,
        "link_attr": link_attr,
        **extra,
    }
    if seller:
        config["seller"] = seller
    return {"slug": slug, "name": name, "kind": "css", "config": config}


# Not part of BUILTIN_SITES: eBay *sold/completed* listings, used to
# backfill real historical sale prices (eBay exposes roughly the last
# 90 days). Same css selectors, but fetched by the browser tier —
# eBay 403s plain HTTP; the "date" selector yields sold_at.
EBAY_SOLD = {
    "slug": "ebay-sold",
    "name": "eBay sold listings",
    "kind": "browser_css",
    "config": {
        "url": "https://www.ebay.com/sch/i.html?_nkw={query}&LH_Sold=1&LH_Complete=1&_sop=13",
        "item": "li.s-item",
        "title": ".s-item__title",
        "price": ".s-item__price",
        "link": "a.s-item__link",
        "link_attr": "href",
        "seller": ".s-item__seller-info-text",
        "date": ".s-item__caption",
    },
}

_FLAT_SITES = [
    _css(
        "ebay",
        "eBay",
        "https://www.ebay.com/sch/i.html?_nkw={query}&_sop=15",
        "li.s-item",
        ".s-item__title",
        ".s-item__price",
        "a.s-item__link",
        seller=".s-item__seller-info-text",
    ),
    _css(
        "craigslist",
        "Craigslist (set your region in config.url)",
        "https://sfbay.craigslist.org/search/sss?query={query}",
        "li.cl-static-search-result",
        "div.title",
        "div.price",
        "a",
    ),
    _css(
        "amazon",
        "Amazon",
        "https://www.amazon.com/s?k={query}",
        "div[data-component-type='s-search-result']",
        "h2 a span",
        "span.a-offscreen",
        "h2 a",
    ),
    _css(
        "newegg",
        "Newegg",
        "https://www.newegg.com/p/pl?d={query}",
        "div.item-cell",
        "a.item-title",
        "li.price-current",
        "a.item-title",
    ),
    _css(
        "bestbuy",
        "Best Buy",
        "https://www.bestbuy.com/site/searchpage.jsp?st={query}",
        "div.product-list-item",
        "a.product-list-item-link",
        "div.price-block-customer-price",
        "a.product-list-item-link",
        wait="div.product-list-item",
    ),
    _css(
        "walmart",
        "Walmart",
        "https://www.walmart.com/search?q={query}",
        "div[data-item-id]",
        "span[data-automation-id='product-title']",
        "div[data-automation-id='product-price']",
        "a[link-identifier]",
    ),
    _css(
        "target",
        "Target",
        "https://www.target.com/s?searchTerm={query}",
        "[data-test='ListingPageProductListing']",
        "a[href*='/p/'][aria-label]",
        "a[href*='/p/'][aria-label]",
        "a[href*='/p/'][aria-label]",
        title_attr="aria-label",
        wait="[data-test='ListingPageProductListing']",
    ),
    # Staples resets plain HTTP *and* real-browser connections from
    # datacenter IPs (ERR_HTTP2_PROTOCOL_ERROR); selectors unverifiable.
    _css(
        "staples",
        "Staples",
        "https://www.staples.com/search?query={query}",
        "div.standard-type__product_card",
        "a.standard-type__product_title",
        "div.standard-type__price",
        "a.standard-type__product_title",
    ),
    _css(
        "officedepot",
        "Office Depot",
        "https://www.officedepot.com/a/search/?q={query}",
        "div.od-product-card",
        "a.od-product-card-description",
        "div.od-graphql-price",
        "a.od-product-card-description",
    ),
    _css(
        "adorama",
        "Adorama",
        "https://www.adorama.com/l/?searchinfo={query}",
        "div.item",
        "div.item-details a",
        "div.prices span.your-price",
        "div.item-details a",
    ),
    _css(
        "bhphotovideo",
        "B&H Photo Video",
        "https://www.bhphotovideo.com/c/search?q={query}",
        "div[data-selenium='miniProductPage']",
        "span[data-selenium='miniProductPageProductName']",
        "span[data-selenium='uppedDecimalPrice']",
        "a[data-selenium='miniProductPageProductNameLink']",
    ),
    _css(
        "microcenter",
        "Micro Center",
        "https://www.microcenter.com/search/search_results.aspx?Ntt={query}",
        "li.product_wrapper",
        "div.pDescription a",
        "span.price",
        "div.pDescription a",
    ),
    _css(
        "backmarket",
        "Back Market",
        "https://www.backmarket.com/en-us/search?q={query}",
        "div[data-qa='productCard']",
        "&",
        "&",
        "a[href*='/p/']",
        title_attr="data-cnstrc-item-name",
        price_attr="data-cnstrc-item-price",
        wait="div[data-qa='productCard']",
    ),
    _css(
        "swappa",
        "Swappa",
        "https://swappa.com/search?q={query}",
        "div.cell_product",
        "a.title",
        "a.price",
        "a.title",
    ),
    _css(
        "mercari",
        "Mercari",
        "https://www.mercari.com/search/?keyword={query}",
        "div[data-testid='ItemCell']",
        "div[data-testid='ItemName']",
        "div[data-testid='ItemPrice']",
        "a",
    ),
    _css(
        "offerup",
        "OfferUp",
        "https://offerup.com/search?q={query}",
        "a[title]",
        "span.MuiTypography-subtitle1",
        "span.MuiTypography-body2",
        "a[title]",
        link_attr="href",
    ),
    _css(
        "bonanza",
        "Bonanza",
        "https://www.bonanza.com/items/search?q%5Bsearch_term%5D={query}",
        "div.search_result_item",
        "div.item_title a",
        "a.item_price",
        "div.item_title a",
    ),
    # Woot removed site search; scrape the computers category feed and
    # keyword-filter locally. Card anchors mix price into the title text.
    _css(
        "woot",
        "Woot (computers feed)",
        "https://www.woot.com/category/computers",
        "a[href*='/offers/']",
        "&",
        "&",
        "&",
        local_filter=True,
        wait="a[href*='/offers/']",
    ),
    _css(
        "shopgoodwill",
        "ShopGoodwill",
        "https://shopgoodwill.com/categories/listing?st={query}",
        "div.feat-item",
        "div.feat-item_name",
        "div.feat-item_price",
        "a",
    ),
    _css(
        "govdeals",
        "GovDeals",
        "https://www.govdeals.com/search?kWord={query}",
        "div.asset-card",
        "a.asset-title",
        "span.asset-price",
        "a.asset-title",
    ),
    # Harris Teeter (Kroger banner): api tier does the real work (see
    # api.py's fetch_kroger_api). harristeeter.com itself resets both
    # plain HTTP and a real headless browser from this network
    # (ERR_HTTP2_PROTOCOL_ERROR) — these css selectors are an
    # unverified best-effort guess, never seen against a live response.
    _css(
        "harris-teeter",
        "Harris Teeter",
        "https://www.harristeeter.com/search?query={query}",
        "div.ProductCard",
        "span.ProductCard-title",
        "span.ProductCard-price",
        "a.ProductCard-link",
        location="Harris Teeter, 2107 Hillsborough Rd, Durham, NC 27705",
        condition="new",
        zip="27705",
        chain="HART",  # Kroger banner code for Harris Teeter (/v1/chains)
        location_id="09700394",  # Shops at Erwin Mill, 2107 Hillsborough Rd
        site_url="https://www.harristeeter.com",
        wait="div.ProductCard",
    ),
    # Food Lion (Ahold Delhaize): no public developer API. foodlion.com
    # is DataDome-walled from this network at every tier — plain HTTP
    # 403s outright, a real headless browser gets served a "please
    # enable JS" captcha interstitial — so these css selectors are an
    # unverified best-effort guess, kept for the day the wall lifts or
    # a JSON search endpoint (the SPA must call one) is found.
    _css(
        "food-lion",
        "Food Lion",
        "https://www.foodlion.com/shop/search?q={query}",
        "div.product-tile",
        "span.product-tile__title",
        "span.product-tile__price",
        "a.product-tile__link",
        location="Food Lion, 3808 Guess Rd, Durham, NC 27705",
        condition="new",
        wait="div.product-tile",
    ),
    # Aldi: the storefront at aldi.us/store/aldi is Instacart's SPA and
    # renders nothing without JS (the plain css tier 404s); the browser
    # tier renders the Durham store's shelf prices. Card class names are
    # hashed (e-xxxx) so selectors lean on aria/testid/href instead.
    _css(
        "aldi",
        "Aldi",
        "https://www.aldi.us/store/aldi/s?k={query}",
        "div[aria-label='Product']",
        "h3",
        "span.screen-reader-only",
        "a[href*='/store/aldi/products/']",
        location="Aldi, 3600 N Duke St, Durham, NC 27704",
        condition="new",
        subtitle="h3 + div",  # pack size ("4 x 11 fl oz") sits right after the name
        site_url="https://www.aldi.us",
        wait="div[aria-label='Product']",
    ),
    {
        "slug": "facebook-marketplace",
        "name": "Facebook Marketplace",
        "kind": "facebook_marketplace",
        "config": {
            "url": "https://www.facebook.com/marketplace/{region}/search"
            "?query={query}&radius={radius_km}",
            "region": "durham",
            "radius_km": 80,
            "cookies_env": "FB_COOKIES",
        },
    },
    {
        "slug": "autolist",
        "name": "Autolist",
        "kind": "autolist_api",
        "config": {"zip": "27705", "radius_mi": 100, "condition": "used"},
    },
    {
        "slug": "cars-com",
        "name": "Cars.com",
        "kind": "carscom",
        "config": {
            "url": "https://www.cars.com/shopping/results/?keyword={query}&zip={zip}"
            "&maximum_distance={radius_mi}&stock_type=used&page_size=50",
            "zip": "27705",
            "radius_mi": 100,
            "wait": "fuse-card[data-vehicle-details]",
        },
    },
    {
        "slug": "carvana",
        "name": "Carvana",
        "kind": "carvana",
        "config": {
            "url": "https://www.carvana.com/cars/{query_slug}",
            "wait": "script[data-testid=vehicle-ld]",
        },
    },
    {
        "slug": "reddit-hardwareswap",
        "name": "r/hardwareswap",
        "kind": "reddit_json",
        "config": {
            "url": "https://www.reddit.com/r/hardwareswap/search.json"
            "?q={query}&restrict_sr=on&sort=new&limit=50",
            # Reddit's API rules want a descriptive UA (they block the
            # browser UA harder); this IP range may still get 403s.
            "headers": {"User-Agent": "linux:product-finder:v0.4 (personal price tracker)"},
        },
    },
]


# Sites that hard-block plain HTTP entirely (eBay 403s /sch/i.html even
# with a browser UA): no css tier at all — API first, then browser.
NO_PLAIN_HTML = {"ebay", "woot"}

# Sites whose search pages are JS-rendered or bot-block plain HTTP:
# they get a browser_css fallback tier after plain css.
JS_HEAVY = {
    "woot",
    "staples",
    "adorama",
    "microcenter",
    "amazon",
    "walmart",
    "target",
    "bestbuy",
    "backmarket",
    "mercari",
    "offerup",
    "shopgoodwill",
    "govdeals",
    "harris-teeter",
    "food-lion",
    "aldi",
}

# API-first tiers, prepended where an official API exists.
_API_FIRST = {
    "ebay": "ebay_api",
    "bestbuy": "bestbuy_api",
    "walmart": "walmart_api",
    "shopgoodwill": "goodwill_api",  # keyless public buyer API
    "harris-teeter": "kroger_api",
}

# api-tier strategies that need config (zip/chain/location/...) rather
# than the env-only fetchers, which take an empty config.
_API_NEEDS_CONFIG = {"kroger_api"}


def _tiered(site: dict) -> dict:
    """Wrap a flat css site in ordered strategies: api? -> css -> browser?"""
    strategies = []
    if site["slug"] in _API_FIRST:
        api_kind = _API_FIRST[site["slug"]]
        api_config = site["config"] if api_kind in _API_NEEDS_CONFIG else {}
        strategies.append({"kind": api_kind, "config": api_config})
    if site["slug"] not in NO_PLAIN_HTML:
        strategies.append({"kind": "css", "config": site["config"]})
    if site["slug"] in JS_HEAVY | NO_PLAIN_HTML:
        strategies.append({"kind": "browser_css", "config": site["config"]})
    if len(strategies) == 1 and strategies[0]["kind"] == site["kind"]:
        return site  # plain css stays flat
    return {
        "slug": site["slug"],
        "name": site["name"],
        "kind": "tiered",
        "config": {"strategies": strategies},
    }


BUILTIN_SITES = [_tiered(s) if s["kind"] == "css" else s for s in _FLAT_SITES]
