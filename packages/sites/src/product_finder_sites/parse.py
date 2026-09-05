"""Pure parsers: search-page body -> listing dicts. No I/O ever.

Owns HTML/JSON interpretation only — search pages and API response
bodies alike; fetch.py/api.py own I/O, spec.py owns which selectors
and strategies to use. Callers rely on:
parse_listings() never raises on weird markup — it returns whatever it
could parse — and every returned dict has a non-empty title and an
absolute url; price/seller/sold_at fields are None when absent (a
"date" selector in a css config turns eBay "Sold <Mon D, YYYY>"
captions into an ISO sold_at date). One deliberate exception to
never-raises: the facebook_marketplace parser raises LoginWall when
Facebook serves a login page instead of results, so run.py can report
"login wall" rather than "no items parsed".

Every returned dict also carries "image_url": an absolute URL for the
listing's thumbnail/primary photo, or None when the markup/JSON has
none — the key is always present, never omitted. css/browser_css
configs may carry an "image" selector (relative to the item; "&" means
the item itself) and "image_attr"; absent an "image" selector, the
first `img` inside the item is used, and absent "image_attr" the
attribute chosen is the first of src/data-src/data-lazy-src/
data-original/first-srcset-url that isn't empty, a `data:` URI, or an
obvious 1x1/spacer/placeholder image — real cards often lazy-load the
real photo into a data-* attribute behind a tiny placeholder in `src`.

css/browser_css configs may carry static `location` and `condition`
strings (grocery sites: a fixed store address and "new") — copied onto
every row verbatim, unlike the per-card `seller`/`date` selectors.
kroger_api rows get the same two fields from strategy config, since a
Kroger product search is already scoped to one resolved store.

discogs_api rows are one per pressing with copies currently for sale
(zero-for-sale releases produce no row), title
"{artist} - {album} ({year}, {label}, {country}) - {N} for sale" (an
en dash and an em dash in the real rendered title, plain hyphens here
only to keep this docstring's characters unambiguous) with
the artist/album split off the search result's "{artist} - {album}"
title on the first " - " and Discogs' "*"/"(2)"-style disambiguation
suffixes stripped from the artist; price is the release's lowest_price
(None when absent or 0 — a free/unset price, not a real $0 listing);
url is the pressing's Discogs "sell" page (`/sell/release/{id}`, where
that pressing's copies are actually listed) rather than its info page;
condition is always "used" (Discogs marketplace copies are all
secondhand); location is always None (sellers ship from all over); a
"year" field carries the release year as an int (None if absent/not
numeric) alongside the usual keys, for a car/game-style extractor that
wants it structured rather than title-mined.

facebook_json rows come from the Marketplace search document api.py's
fetch_facebook_json returns (see that module's docstring for the fetch
side) — ported from the resilience-first waterfall bake-off prototype's
strategy 1 (product-finder-fb-requests/experiments/facebook-requests/
report.md; fb.py's `_find_feed_units`). Page 1 of results is embedded in
the document as a `<script type="application/json" data-sjs>` blob
mentioning `marketplace_search`; `_parse_facebook_json` finds that
blob, `json.loads`s it, and recursively locates the first dict carrying
a `feed_units` key (never path-walks the `require`/`__bbox` nesting —
that rots more often than the payload it wraps). It raises (never
returns `[]`) on every schema-drift shape — no such blob found, a blob
found but no `feed_units` anywhere inside it, invalid/truncated JSON,
or every item in a non-empty edge batch failing to parse — so
run.search_site's generic "a bad parse must not kill the run" handling
falls through to the browser tier instead of reporting a false "no
items parsed"; a genuinely empty `edges: []` still returns `[]` (that
line is drawn on total edge count, not on output count). Rows carry the
same keys as the DOM-based facebook_marketplace parser (title, price,
url, location, seller_rating/seller_feedback_count always None,
image_url) since the storage contract is unchanged; location is
"City, ST" from the node's reverse_geocode, or just the city, or None.

Dealer used-car rows (autolist_api, carscom, carvana) are titled
"[Used] YEAR Make Model Trim, <odometer> mi" so the car products'
year/mileage extractors read them like a Craigslist title; condition
is the site's new/used/certified plus the dealer name; location is the
dealer's "City, ST" where the page carries one (carvana is a
nationwide delivery site — its rows have no location, so a distance
cap hides them). carscom reads each fuse-card's data-vehicle-details
JSON attribute rather than the visible spans; carvana reads the
per-vehicle schema.org Vehicle JSON-LD scripts on its results page.

bhphotovideo reads the search page's `div.bh-preloaded-data` blob (the
React store state, HTML-escaped JSON in its data-data attribute) at
ListingStore.state.response.data.items rather than the SSR'd
miniProductPage cards: B&H server-renders an <img> for only the first
two cards and hydrates the rest client-side, so a css parse of the
cards sees no image on ~95% of rows, and the two it does see point at
B&H's cdn-cgi/image proxy, which 403s hotlinks. Each item's
core.shortDescription/detailsUrl, priceInfo.price (None when the price
is withheld) and mainImage.default.url (static.bhphoto.com, hotlinkable)
become the row; core.isUsed sets condition "used". No blob, or a blob
without that items list, parses to [] so run.search_site reports the
page as a challenge/no-items error rather than raising.
"""

import json
import re
from datetime import datetime
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

_PRICE_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")
# Split price markup: Woot "$ 27 99", Bonanza "$ 15 . 83" — both $27.99-style
_PRICE_SPLIT_RE = re.compile(r"\$\s*(\d{1,4})\s*(?:\.\s*|\s+)(\d{2})(?!\d)")
_NUM_RE = re.compile(r"([\d,]+(?:\.\d{1,2})?)")
# eBay-style "seller_name (2,394) 99.1%"
_SELLER_RE = re.compile(r"\(([\d,]+)\)\s*([\d.]+)%")
# eBay sold-listings caption, e.g. "Sold Oct 15, 2025" / "Sold  Oct 5, 2025"
_SOLD_RE = re.compile(r"sold\s+(\w{3})\.?\s+(\d{1,2}),\s+(\d{4})", re.IGNORECASE)
# "Durham, NC" — a short place-name span on a Facebook Marketplace card
_LOCATION_RE = re.compile(r"^[\w .'\u2019-]+,\s*[A-Z]{2}$")
# Filename/id fragments that mark a decorative or placeholder image
# rather than a real product photo (badge icons aren't caught by this --
# those need an explicit "image" selector instead).
_PLACEHOLDER_IMAGE_RE = re.compile(
    r"(?:^|[/_.-])(?:1x1|blank|spacer|placeholder|transparent|no[_-]?image)(?:[/_.-]|$)",
    re.IGNORECASE,
)
# Attributes tried in order when a css config has no explicit image_attr --
# real markup lazy-loads the true photo into one of the data-* attributes
# behind a tiny placeholder in src.
_IMAGE_SRC_ATTRS = ("src", "data-src", "data-lazy-src", "data-original")


class LoginWall(Exception):
    """A site answered with a login page instead of search results."""


def _price(text: str | None) -> float | None:
    if not text:
        return None
    split = _PRICE_SPLIT_RE.search(text)
    m = _PRICE_RE.search(text) or _NUM_RE.search(text)
    if split and (not m or split.start() <= m.start()):
        return float(f"{split.group(1)}.{split.group(2)}")
    return float(m.group(1).replace(",", "")) if m else None


def _sel(item, selector: str):
    """Resolve a config selector against a card node; "&" is the node itself."""
    return item if selector == "&" else item.select_one(selector)


def _first_srcset_url(srcset) -> str | None:
    """Srcset "url1 1x, url2 2x" -> "url1" (the first candidate, descriptor dropped)."""
    if not srcset:
        return None
    first = str(srcset).strip().split(",")[0].strip()
    return first.split()[0] if first else None


def _clean_image_value(value) -> str | None:
    """None out empty strings, base64 `data:` placeholders, and filenames
    that look like a 1x1/spacer/placeholder gif rather than a real photo."""
    text = str(value).strip() if value else ""
    if not text or text.startswith("data:"):
        return None
    if _PLACEHOLDER_IMAGE_RE.search(text):
        return None
    return text


def _image_from_node(node, image_attr: str | None) -> str | None:
    """Pull a real photo URL off an <img> node, lazy-load aware."""
    if node is None:
        return None
    if image_attr:
        value = node.get(image_attr)
        if image_attr == "srcset":
            value = _first_srcset_url(value)
        return _clean_image_value(value)
    for attr in _IMAGE_SRC_ATTRS:
        cleaned = _clean_image_value(node.get(attr))
        if cleaned:
            return cleaned
    return _clean_image_value(_first_srcset_url(node.get("srcset")))


def _css_image_url(item, config: dict, page_url: str) -> str | None:
    selector = config.get("image")
    node = _sel(item, selector) if selector else item.find("img")
    value = _image_from_node(node, config.get("image_attr"))
    return urljoin(page_url, value) if value else None


def _sold_date(text: str | None) -> str | None:
    if not text:
        return None
    m = _SOLD_RE.search(text)
    if not m:
        return None
    try:
        dt = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%b %d %Y")
    except ValueError:
        return None
    return dt.date().isoformat()


def _seller(text: str | None) -> tuple[float | None, int | None]:
    if not text:
        return None, None
    m = _SELLER_RE.search(text)
    if not m:
        return None, None
    return float(m.group(2)), int(m.group(1).replace(",", ""))


def _parse_css(config: dict, page_url: str, body: str) -> list[dict]:
    soup = BeautifulSoup(body, "html.parser")
    out = []
    for item in soup.select(config["item"]):
        title_node = _sel(item, config["title"])
        link_node = _sel(item, config["link"])
        if title_node is not None and config.get("title_attr"):
            title = str(title_node.get(config["title_attr"]) or "").strip()
        else:
            title = title_node.get_text(" ", strip=True) if title_node else ""
        href = link_node.get(config.get("link_attr", "href")) if link_node else None
        if not title or not href or title.lower() == "shop on ebay":
            continue
        # Optional "subtitle" selector (pack size, variant) folded into the
        # title so extractors see it — grocery cards keep size off the name.
        sub_node = _sel(item, config["subtitle"]) if config.get("subtitle") else None
        subtitle = sub_node.get_text(" ", strip=True) if sub_node else ""
        if subtitle and subtitle.lower() not in title.lower():
            title = f"{title}, {subtitle}"
        price_node = _sel(item, config["price"]) if config.get("price") else None
        if price_node is not None and config.get("price_attr"):
            price_node_text = str(price_node.get(config["price_attr"]) or "")
        else:
            price_node_text = price_node.get_text(" ", strip=True) if price_node else None
        rating = count = None
        if config.get("seller"):
            seller_node = item.select_one(config["seller"])
            rating, count = _seller(seller_node.get_text(" ", strip=True) if seller_node else None)
        sold_at = None
        if config.get("date"):
            date_node = item.select_one(config["date"])
            sold_at = _sold_date(date_node.get_text(" ", strip=True) if date_node else None)
        out.append(
            {
                "title": title,
                "price": _price(price_node_text),
                "url": urljoin(page_url, str(href)),
                "seller_rating": rating,
                "seller_feedback_count": count,
                "sold_at": sold_at,
                "location": config.get("location"),
                "condition": config.get("condition"),
                "image_url": _css_image_url(item, config, page_url),
            }
        )
    return out


def _reddit_image_url(data: dict) -> str | None:
    """preview.images[0].source.url (HTML-unescaped) beats `thumbnail`,
    which reddit sets to "self"/"default"/"nsfw" sentinels rather than a
    real URL for text posts / posts with no preview yet."""
    preview = data.get("preview") or {}
    images = preview.get("images") or []
    if images:
        source = (images[0] or {}).get("source") or {}
        url = source.get("url")
        if url:
            return str(url).replace("&amp;", "&")
    thumbnail = data.get("thumbnail")
    if isinstance(thumbnail, str) and thumbnail.startswith(("http://", "https://")):
        return thumbnail
    return None


def _parse_reddit_json(page_url: str, body: str) -> list[dict]:
    try:
        payload = json.loads(body)
        children = payload["data"]["children"]
    except (ValueError, KeyError, TypeError):
        return []
    out = []
    for child in children:
        data = child.get("data", {})
        title = data.get("title", "")
        permalink = data.get("permalink")
        if not title or not permalink:
            continue
        out.append(
            {
                "title": title,
                "price": _price(title + " " + data.get("selftext", "")[:500]),
                "url": urljoin("https://www.reddit.com", permalink),
                "seller_rating": None,
                "seller_feedback_count": None,
                "image_url": _reddit_image_url(data),
            }
        )
    return out


def _json_items(body: str, *path: str) -> list:
    try:
        node = json.loads(body)
        for key in path:
            node = node[key]
        return node if isinstance(node, list) else []
    except (ValueError, KeyError, TypeError):
        return []


def _parse_ebay_api(body: str) -> list[dict]:
    out = []
    for item in _json_items(body, "itemSummaries"):
        title, url = item.get("title", ""), item.get("itemWebUrl")
        if not title or not url:
            continue
        price = item.get("price", {}).get("value")
        seller = item.get("seller", {})
        rating = seller.get("feedbackPercentage")
        image_url = (item.get("image") or {}).get("imageUrl")
        if not image_url:
            thumbs = item.get("thumbnailImages") or []
            image_url = (thumbs[0] or {}).get("imageUrl") if thumbs else None
        out.append(
            {
                "title": title,
                "price": float(price) if price else None,
                "url": url,
                "seller_rating": float(rating) if rating else None,
                "seller_feedback_count": seller.get("feedbackScore"),
                "image_url": image_url,
            }
        )
    return out


def _parse_bestbuy_api(body: str) -> list[dict]:
    return [
        {
            "title": p["name"],
            "price": p.get("salePrice"),
            "url": p.get("url"),
            "seller_rating": None,
            "seller_feedback_count": None,
            "image_url": p.get("image") or p.get("thumbnailImage"),
        }
        for p in _json_items(body, "products")
        if p.get("name") and p.get("url")
    ]


def _parse_walmart_api(page_url: str, body: str) -> list[dict]:
    out = []
    for item in _json_items(body, "items"):
        title, url = item.get("name", ""), item.get("productUrl")
        if not title or not url:
            continue
        out.append(
            {
                "title": title,
                "price": item.get("salePrice"),
                "url": urljoin("https://www.walmart.com", url),
                "seller_rating": None,
                "seller_feedback_count": None,
                # No walmart_api fixture exists to confirm the real field
                # name against; "image"/"thumbnailImage" mirror the shape
                # used by the other product-search APIs above.
                "image_url": item.get("image") or item.get("thumbnailImage"),
            }
        )
    return out


def _parse_facebook(page_url: str, body: str) -> list[dict]:
    """Marketplace cards: item links with obfuscated span classes, so
    fields come from leaf-span heuristics (price = first $-span, title
    = longest remaining text, location = "City, ST" span)."""
    soup = BeautifulSoup(body, "html.parser")
    anchors = [a for a in soup.select("a[href*='/marketplace/item/']") if a.get("href")]
    if not anchors:
        wall_marks = ("login_form", 'action="/login/"', "You must log in")
        if any(mark in body for mark in wall_marks):
            raise LoginWall(
                "login wall — set FB_COOKIES (cookie header from your own logged-in browser)"
            )
        return []
    out, seen = [], set()
    for anchor in anchors:
        url = urljoin(page_url, str(anchor["href"]).split("?")[0])
        if url in seen:
            continue
        texts = [
            s.get_text(" ", strip=True)
            for s in anchor.find_all("span")
            if not s.find("span") and s.get_text(strip=True)
        ]
        price_text = next((t for t in texts if _PRICE_RE.search(t)), None)
        rest = [t for t in texts if t != price_text]
        location = next((t for t in rest if _LOCATION_RE.match(t)), None)
        title = max((t for t in rest if t != location), key=len, default="")
        if not title:
            continue
        seen.add(url)
        image_url = _image_from_node(anchor.find("img"), None)
        out.append(
            {
                "title": title,
                "price": _price(price_text),
                "url": url,
                "location": location,
                "seller_rating": None,
                "seller_feedback_count": None,
                "image_url": urljoin(page_url, image_url) if image_url else None,
            }
        )
    return out


# facebook_json: type="application/json" and data-sjs can appear in
# either attribute order, with extra attributes (data-content-len=...)
# between them, so match on the tag's raw attribute text rather than a
# fixed sequence.
_FB_SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.DOTALL | re.IGNORECASE)


def _fb_find_first_with_key(obj, key: str):
    """Recursively find the first dict anywhere in `obj` that has `key`,
    and return its value. Deliberately doesn't path-walk the
    require/__bbox nesting Facebook's own bundle rots — it changes shape
    more often than the payload it wraps does."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _fb_find_first_with_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _fb_find_first_with_key(v, key)
            if found is not None:
                return found
    return None


def _fb_find_feed_units(body: str) -> dict:
    """Find the `<script data-sjs>` blob that mentions `marketplace_search`,
    json.loads it, and recursively locate the first `feed_units` dict
    inside. Raises on every shape that isn't a legitimate (possibly
    empty) result set, so `_parse_facebook_json` never returns `[]` for a
    schema-drift failure. Login walls are checked by api.py's
    fetch_facebook_json before this ever runs — not this function's
    concern."""
    saw_marketplace_search_blob = False
    for m in _FB_SCRIPT_RE.finditer(body):
        attrs, content = m.group(1), m.group(2)
        if "data-sjs" not in attrs or "marketplace_search" not in content:
            continue
        saw_marketplace_search_blob = True
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise ValueError(f"embedded marketplace_search JSON is truncated/invalid: {e}") from e
        feed_units = _fb_find_first_with_key(data, "feed_units")
        if feed_units is not None:
            return feed_units
    if saw_marketplace_search_blob:
        raise ValueError(
            "marketplace_search blob found but no feed_units anywhere in it — feed shape changed"
        )
    raise ValueError(
        "no marketplace_search script found in the document "
        "— feed shape changed, or this isn't a search results page"
    )


def _fb_price_and_currency(price_info: dict) -> tuple[float | None, str | None]:
    amount = price_info.get("amount")
    price = None
    if amount is not None:
        try:
            price = float(amount)
        except (TypeError, ValueError):
            price = None
    formatted = price_info.get("formatted_amount") or ""
    currency = "USD" if "$" in formatted else None
    return price, currency


def _fb_location(loc: dict) -> str | None:
    geo = loc.get("reverse_geocode") or {}
    city, state = geo.get("city"), geo.get("state")
    if city and state:
        return f"{city}, {state}"
    return city or None


def _parse_facebook_json_listing(listing: dict) -> dict:
    """Raises KeyError/TypeError/ValueError when a required field is
    missing or the wrong shape; the caller (_parse_facebook_json_edges)
    turns that into a per-item skip, not a crash."""
    listing_id = listing["id"]
    if not listing_id:
        raise ValueError("empty listing id")
    title = listing.get("custom_title") or listing["marketplace_listing_title"]
    if not title:
        raise ValueError("empty title")
    price, _currency = _fb_price_and_currency(listing.get("listing_price") or {})
    location = _fb_location(listing.get("location") or {})
    photo = listing.get("primary_listing_photo") or {}
    image_url = (photo.get("image") or {}).get("uri")
    return {
        "title": title,
        "price": price,
        "url": f"https://www.facebook.com/marketplace/item/{listing_id}/",
        "location": location,
        "seller_rating": None,
        "seller_feedback_count": None,
        "image_url": image_url,
    }


def _parse_facebook_json_edges(edges: list) -> list[dict]:
    """Per-item schema-drift tolerance: a renamed/missing field on one
    item skips that item silently — it must not kill the batch. But if
    *every* item in a non-empty batch fails, that's systemic drift, not
    noise: raise instead of quietly returning `[]` (a legitimately empty
    batch, `edges == []`, still returns `[]` — the line is drawn on
    `total > 0`, not on `out == []`)."""
    out = []
    total = len(edges)
    for edge in edges:
        node = edge.get("node") or {}
        if node.get("__typename") != "MarketplaceFeedListingStoryObject":
            continue  # ad/upsell unit, not a listing — expected, not an error
        listing = node.get("listing")
        if not isinstance(listing, dict):
            continue
        try:
            out.append(_parse_facebook_json_listing(listing))
        except (KeyError, TypeError, ValueError):
            continue
    if total > 0 and not out:
        raise ValueError(
            f"all {total} feed items failed to parse — systemic schema drift, not per-item noise"
        )
    return out


def _parse_facebook_json(page_url: str, body: str) -> list[dict]:
    feed_units = _fb_find_feed_units(body)
    return _parse_facebook_json_edges(feed_units.get("edges") or [])


def _parse_goodwill_api(body: str) -> list[dict]:
    out = []
    for item in _json_items(body, "searchResults", "items"):
        title, item_id = item.get("title", ""), item.get("itemId")
        if not title or not item_id:
            continue
        price = (
            item.get("currentPrice") or item.get("discountedBuyNowPrice") or item.get("buyNowPrice")
        )
        out.append(
            {
                "title": title,
                "price": float(price) if price else None,
                "url": f"https://shopgoodwill.com/item/{item_id}",
                "seller_rating": None,
                "seller_feedback_count": None,
                # goodwill_api.json is a live-rederived capture that
                # carries no image field on any row; "imageURL" is the
                # field name shopgoodwill's buyer API is documented to
                # use elsewhere, kept here for the day a fixture shows it.
                "image_url": item.get("imageURL"),
            }
        )
    return out


def _kroger_image_url(item: dict) -> str | None:
    """Kroger Products API `images`: a list of {perspective, sizes: [{size,
    url}, ...]} entries. Prefer the "front" perspective's medium/large
    size (the ones actually big enough to be useful); fall back to
    whatever the first available image/size offers."""

    def _size_url(entry: dict) -> str | None:
        sizes = entry.get("sizes") or []
        for wanted in ("medium", "large"):
            for s in sizes:
                if s.get("size") == wanted and s.get("url"):
                    return s["url"]
        for s in sizes:
            if s.get("url"):
                return s["url"]
        return None

    images = item.get("images") or []
    front = next((im for im in images if im.get("perspective") == "front"), None)
    if front:
        url = _size_url(front)
        if url:
            return url
    for im in images:
        url = _size_url(im)
        if url:
            return url
    return None


def _parse_kroger_api(config: dict, body: str) -> list[dict]:
    """Kroger Products API response -> listings. There's no product-page
    URL in the payload, so `url` is a deep link into the banner's own
    search (config["site_url"], e.g. harristeeter.com) for that item's
    description — not a canonical product page."""
    site_url = config.get("site_url", "https://www.harristeeter.com")
    out = []
    for item in _json_items(body, "data"):
        title, product_id = item.get("description", ""), item.get("productId")
        if not title or not product_id:
            continue
        entries = item.get("items") or []
        # "regular", not "promo" — Kroger's API returns promo: 0 for "no
        # active promotion" rather than omitting the field, which would
        # otherwise misread as a free item.
        price = entries[0].get("price", {}).get("regular") if entries else None
        # Real Kroger descriptions omit pack size ("Premier Protein Vanilla
        # Protein Shake" is a 12-pack at $33.99); the item's `size` ("12 ct /
        # 11 fl oz") carries it, so fold it into the title for extractors.
        size = str(entries[0].get("size") or "").strip() if entries else ""
        if size and size.lower() not in title.lower():
            title = f"{title}, {size}"
        out.append(
            {
                "title": title,
                "price": float(price) if price else None,
                "url": f"{site_url}/search?query={quote_plus(title)}",
                "location": config.get("location"),
                "condition": config.get("condition", "new"),
                "seller_rating": None,
                "seller_feedback_count": None,
                "image_url": _kroger_image_url(item),
            }
        )
    return out


def _parse_autolist_api(config: dict, body: str) -> list[dict]:
    site_url = config.get("site_url", "https://www.autolist.com")
    out = []
    for r in _json_items(body, "records"):
        href = r.get("href_target")
        vehicle = " ".join(
            str(x) for x in (r.get("year"), r.get("make"), r.get("model"), r.get("trim")) if x
        )
        if not href or not vehicle:
            continue
        miles = r.get("mileage_unformatted")
        title = f"{vehicle}, {int(miles)} mi" if miles else vehicle
        price = r.get("price_unformatted")
        city, state = r.get("city"), r.get("state")
        condition = str(r.get("condition") or "used")
        if r.get("dealer_name"):
            condition = f"{condition}; {r['dealer_name']}"
        # Live /search records carry "primary_photo_url" (a CarGurus CDN
        # jpeg) plus a "photo_urls" list; the primary is the card thumbnail.
        photo = r.get("primary_photo_url")
        out.append(
            {
                "title": title,
                "price": float(price) if price else None,
                "url": urljoin(site_url, href),
                "location": f"{city}, {state}" if city and state else None,
                "condition": condition,
                "seller_rating": None,
                "seller_feedback_count": None,
                "image_url": urljoin(site_url, photo) if photo else None,
            }
        )
    return out


# Discogs disambiguates same-named artists with a trailing "*" (credit
# text differs slightly from the canonical profile name) or "(N)" (the
# Nth artist with this exact name) — strip both off the split artist.
_DISCOGS_ARTIST_DISAMBIG_RE = re.compile(r"\s*\(\d+\)\s*$")


def _discogs_clean_artist(name: str) -> str:
    return _DISCOGS_ARTIST_DISAMBIG_RE.sub("", name.strip()).rstrip("*").strip()


def _discogs_label(release: dict, entry: dict) -> str:
    labels = release.get("labels") or []
    if labels and labels[0].get("name"):
        return str(labels[0]["name"])
    entry_labels = entry.get("label") or []
    return str(entry_labels[0]) if entry_labels else ""


def _discogs_release_image(release: dict) -> str | None:
    """Cover art from a /releases/{id} payload: `thumb` (150px) when set,
    else the primary image's 150px variant, else its full-size `uri`."""
    if release.get("thumb"):
        return release["thumb"]
    images = release.get("images") or []
    primary = next((i for i in images if i.get("type") == "primary"), images[0] if images else {})
    return primary.get("uri150") or primary.get("uri") or None


def _parse_discogs_api(body: str) -> list[dict]:
    """`body` is fetch_discogs_api's own combined JSON, not a raw
    Discogs response — see api.py's fetch_discogs_api docstring for the
    {"query", "releases": [{...search fields, "release": {...}}]} shape."""
    try:
        payload = json.loads(body)
    except ValueError:
        return []
    out = []
    for entry in payload.get("releases") or []:
        release_id = entry.get("id")
        release = entry.get("release") or {}
        num_for_sale = release.get("num_for_sale") or 0
        if not release_id or num_for_sale <= 0:
            continue
        raw_title = str(entry.get("title") or "")
        if " - " in raw_title:
            artist, _, album = raw_title.partition(" - ")
            artist = _discogs_clean_artist(artist)
        else:
            artist, album = "", raw_title
        year = release.get("year") or entry.get("year")
        country = release.get("country") or entry.get("country") or ""
        label = _discogs_label(release, entry)
        title = (
            f"{artist} – {album} ({year or ''}, {label}, {country})"  # noqa: RUF001
            f" — {num_for_sale} for sale"
        )
        lowest_price = release.get("lowest_price")
        # Unauthenticated search rows carry empty thumb/cover_image; the
        # release we fetch anyway has the cover under thumb/images[].
        image_url = (
            entry.get("thumb") or entry.get("cover_image") or _discogs_release_image(release)
        )
        out.append(
            {
                "title": title,
                "price": float(lowest_price) if lowest_price else None,
                "url": f"https://www.discogs.com/sell/release/{release_id}",
                "location": None,
                "condition": "used",
                "seller_rating": None,
                "seller_feedback_count": None,
                "image_url": image_url,
                "year": int(year) if str(year).isdigit() else None,
            }
        )
    return out


# "Greensboro, NC (48 mi)" — the dealer line on a cars.com card; the
# city is a run of capitalized words so the dealer name before it
# ("Toyota of Greensboro 4.4") is not swallowed.
_CARSCOM_LOCATION_RE = re.compile(
    r"((?:[A-Z][\w.'\u2019-]*\s)*[A-Z][\w.'\u2019-]*,\s*[A-Z]{2})\s*\(\d[\d,]*\s*mi\)"
)
# "Used 2015 Toyota Prius Two with 67298 miles - $18,990" — carvana JSON-LD description
_CARVANA_DESC_RE = re.compile(r"^(?:\w+\s+)?(\d{4}\s.+?)\s+with\s+[\d,]+\s+miles", re.IGNORECASE)


def _parse_carscom(page_url: str, body: str) -> list[dict]:
    soup = BeautifulSoup(body, "html.parser")
    out = []
    for card in soup.select("fuse-card[data-vehicle-details]"):
        try:
            d = json.loads(card["data-vehicle-details"])
        except ValueError:
            continue
        link = card.select_one("a[data-card-link], card-gallery[data-card-href]")
        href = link.get("href") or link.get("data-card-href") if link else None
        vehicle = " ".join(str(d.get(k) or "") for k in ("year", "make", "model", "trim")).split()
        if not href or not vehicle:
            continue
        stock = str(d.get("stockType") or "").strip()
        title = " ".join(([stock] if stock else []) + vehicle)
        miles = str(d.get("mileage") or "").replace(",", "")
        if miles.isdigit() and int(miles) > 0:
            title = f"{title}, {int(miles)} mi"
        seller = d.get("seller") or {}
        m = _CARSCOM_LOCATION_RE.search(card.get_text(" ", strip=True))
        condition = stock.lower() or "used"
        if seller.get("dealerName"):
            condition = f"{condition}; {seller['dealerName']}"
        # Real cards.com markup renders the photo as an <img> in the
        # card-gallery (lazy-loaded, so data-src over src); this trimmed
        # fixture has that gallery stripped, so fall back to the same
        # "primaryThumbnail" URL the JSON already carries for the <img>.
        image_node = card.find("img")
        image_url = _image_from_node(image_node, None) or d.get("primaryThumbnail")
        out.append(
            {
                "title": title,
                "price": _price(f"${d['price']}") if d.get("price") else None,
                "url": urljoin(page_url, href.split("?")[0]),
                "location": m.group(1) if m else (seller.get("zip") or None),
                "condition": condition,
                "seller_rating": None,
                "seller_feedback_count": None,
                "image_url": urljoin(page_url, image_url) if image_url else None,
            }
        )
    return out


def _parse_carvana(page_url: str, body: str) -> list[dict]:
    soup = BeautifulSoup(body, "html.parser")
    out = []
    for node in soup.select("script[data-testid=vehicle-ld]"):
        try:
            d = json.loads(node.string or "")
        except ValueError:
            continue
        if d.get("@type") != "Vehicle":
            continue
        offer = d.get("offers") or {}
        url = offer.get("url")
        m = _CARVANA_DESC_RE.match(d.get("description") or "")
        vehicle = m.group(1) if m else d.get("name")
        if not url or not vehicle:
            continue
        cond = str(d.get("itemCondition") or "Used")
        title = f"{cond} {vehicle}"
        miles = d.get("mileageFromOdometer")
        if miles:
            title = f"{title}, {int(miles)} mi"
        price = offer.get("price")
        # schema.org Vehicle.image may be a single URL string or a list of
        # them (multiple photos) — take the first string either way.
        raw_image = d.get("image")
        if isinstance(raw_image, list):
            image_url = next((v for v in raw_image if isinstance(v, str) and v), None)
        elif isinstance(raw_image, str) and raw_image:
            image_url = raw_image
        else:
            image_url = None
        out.append(
            {
                "title": title,
                "price": float(price) if price else None,
                "url": urljoin(page_url, url),
                "location": None,
                "condition": f"{cond.lower()}; Carvana (delivery)",
                "seller_rating": None,
                "seller_feedback_count": None,
                "image_url": image_url,
            }
        )
    return out


def _parse_bhphotovideo(page_url: str, body: str) -> list[dict]:
    soup = BeautifulSoup(body, "html.parser")
    node = soup.select_one("div.bh-preloaded-data")
    if node is None:
        return []
    try:
        state = json.loads(node.get("data-data") or "")
    except ValueError:
        return []
    items = state
    for key in ("ListingStore", "state", "response", "data", "items"):
        items = items.get(key) if isinstance(items, dict) else None
        if items is None:
            return []
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        core = item.get("core") or {}
        title = str(core.get("shortDescription") or "").strip()
        href = core.get("detailsUrl")
        if not title or not href:
            continue
        price = (item.get("priceInfo") or {}).get("price")
        image_url = ((item.get("mainImage") or {}).get("default") or {}).get("url")
        out.append(
            {
                "title": title,
                "price": float(price) if price else None,
                "url": urljoin(page_url, str(href)),
                "seller_rating": None,
                "seller_feedback_count": None,
                "sold_at": None,
                "location": None,
                "condition": "used" if core.get("isUsed") else None,
                "image_url": image_url or None,
            }
        )
    return out


def parse_listings(strategy: dict, page_url: str, body: str) -> list[dict]:
    """Dispatch on strategy kind. `strategy` is a {kind, config} dict —
    a flat single-strategy site works too (same shape)."""
    kind = strategy["kind"]
    if kind == "reddit_json":
        return _parse_reddit_json(page_url, body)
    if kind in ("css", "browser_css"):
        return _parse_css(strategy["config"], page_url, body)
    if kind == "facebook_marketplace":
        return _parse_facebook(page_url, body)
    if kind == "facebook_json":
        return _parse_facebook_json(page_url, body)
    if kind == "ebay_api":
        return _parse_ebay_api(body)
    if kind == "bestbuy_api":
        return _parse_bestbuy_api(body)
    if kind == "walmart_api":
        return _parse_walmart_api(page_url, body)
    if kind == "goodwill_api":
        return _parse_goodwill_api(body)
    if kind == "kroger_api":
        return _parse_kroger_api(strategy["config"], body)
    if kind == "autolist_api":
        return _parse_autolist_api(strategy["config"], body)
    if kind == "discogs_api":
        return _parse_discogs_api(body)
    if kind == "carscom":
        return _parse_carscom(page_url, body)
    if kind == "carvana":
        return _parse_carvana(page_url, body)
    if kind == "bhphotovideo":
        return _parse_bhphotovideo(page_url, body)
    raise ValueError(f"unknown strategy kind: {kind}")
