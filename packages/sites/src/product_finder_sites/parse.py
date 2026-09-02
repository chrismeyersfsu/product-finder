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

css/browser_css configs may carry static `location` and `condition`
strings (grocery sites: a fixed store address and "new") — copied onto
every row verbatim, unlike the per-card `seller`/`date` selectors.
kroger_api rows get the same two fields from strategy config, since a
Kroger product search is already scoped to one resolved store.

copart_csv is the one parser that takes the query: its body is
Copart's whole-inventory sales CSV, so parse_listings(..., query=)
keeps only rows whose year/make/model/trim words contain every query
word. Columns are matched by normalized header name (Copart's
documented "CSV Sales Data" layout: Lot number, Year, Make, Model
Group, Model Detail, Trim, Odometer, Damage Description, Sale Title
Type, Runs/Drives, High Bid, Buy-It-Now Price, Location city/state);
price is the Buy-It-Now price when set, else the current high bid,
else None; the title carries "YEAR MAKE MODEL, <odometer> mi" and the
`condition` string carries title type, damage, runs/drives and
whether the price is a bid — so a product's title-only rules don't
see "salvage", which is most of Copart. Every row is a lot page URL.
"""

import csv
import io
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
            }
        )
    return out


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
        out.append(
            {
                "title": title,
                "price": float(price) if price else None,
                "url": url,
                "seller_rating": float(rating) if rating else None,
                "seller_feedback_count": seller.get("feedbackScore"),
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
        out.append(
            {
                "title": title,
                "price": _price(price_text),
                "url": url,
                "location": location,
                "seller_rating": None,
                "seller_feedback_count": None,
            }
        )
    return out


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
            }
        )
    return out


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
            }
        )
    return out


def _norm_header(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _column(headers: list[str], *candidates: str) -> int | None:
    """Index of the first header equal to, then starting with, a candidate."""
    normed = [_norm_header(h) for h in headers]
    for cand in candidates:
        if cand in normed:
            return normed.index(cand)
    for cand in candidates:
        for i, h in enumerate(normed):
            if h.startswith(cand):
                return i
    return None


def _money(text: str) -> float | None:
    m = _NUM_RE.search(text or "")
    value = float(m.group(1).replace(",", "")) if m else None
    return value if value else None


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _parse_copart_csv(config: dict, body: str, query: str) -> list[dict]:
    """Copart sales-data CSV -> listings for the lots matching `query`."""
    reader = csv.reader(io.StringIO(body.lstrip("\ufeff")))
    try:
        headers = next(reader)
    except StopIteration:
        return []
    col = {
        "lot": _column(headers, "lotnumber", "lotid", "lot"),
        "year": _column(headers, "year"),
        "make": _column(headers, "make"),
        "group": _column(headers, "modelgroup"),
        "detail": _column(headers, "modeldetail", "model"),
        "trim": _column(headers, "trim"),
        "type": _column(headers, "vehicletype"),
        "odometer": _column(headers, "odometer", "mileage"),
        "damage": _column(headers, "damagedescription"),
        "damage2": _column(headers, "secondarydamage"),
        "title_type": _column(headers, "saletitletype"),
        "runs": _column(headers, "runsdrives"),
        "bid": _column(headers, "highbid"),
        "bin": _column(headers, "buyitnowprice"),
        "city": _column(headers, "locationcity"),
        "state": _column(headers, "locationstate"),
        "zip": _column(headers, "locationzip"),
        "sale_date": _column(headers, "saledate"),
        "currency": _column(headers, "currencycode"),
    }
    if col["lot"] is None or col["make"] is None:
        return []
    site_url = config.get("site_url", "https://www.copart.com")
    want = _tokens(query)
    out = []
    for row in reader:
        v = {k: (row[i].strip() if i is not None and i < len(row) else "") for k, i in col.items()}
        lot = v["lot"]
        if not lot:
            continue
        if v["currency"] and v["currency"].upper() != "USD":
            continue
        group, detail, trim = v["group"], v["detail"], v["trim"]
        model = detail if detail.upper().startswith(group.upper()) else f"{group} {detail}"
        if trim and trim.lower() not in model.lower():
            model = f"{model} {trim}"
        vehicle = " ".join(x for x in (v["year"], v["make"], model) if x)
        if not vehicle:
            continue
        if want and not want <= _tokens(f"{vehicle} {v['type']}"):
            continue
        title = vehicle
        odometer = re.sub(r"[^\d]", "", v["odometer"])
        if odometer and int(odometer) > 0:
            title = f"{title}, {int(odometer)} mi"
        buy_now, bid = _money(v["bin"]), _money(v["bid"])
        price = buy_now or bid
        parts = []
        if v["title_type"]:
            parts.append(v["title_type"].title())
        damage = v["damage"]
        if damage and v["damage2"] and v["damage2"].upper() != damage.upper():
            damage = f"{damage}/{v['damage2']}"
        if damage:
            parts.append(damage.title())
        if v["runs"]:
            parts.append(v["runs"].title())
        sale = f", sale {v['sale_date']}" if v["sale_date"] else ""
        if buy_now:
            parts.append("buy it now")
        elif bid:
            parts.append(f"current bid{sale}")
        elif sale:
            parts.append(f"no bids{sale}")
        city, state = v["city"], v["state"]
        location = f"{city.title()}, {state.upper()}" if city and state else (v["zip"] or None)
        out.append(
            {
                "title": title,
                "price": price,
                "url": f"{site_url}/lot/{lot}",
                "location": location,
                "condition": "; ".join(parts) or None,
                "seller_rating": None,
                "seller_feedback_count": None,
            }
        )
    return out


def parse_listings(
    strategy: dict, page_url: str, body: str, query: str | None = None
) -> list[dict]:
    """Dispatch on strategy kind. `strategy` is a {kind, config} dict —
    a flat single-strategy site works too (same shape). `query` only
    matters to whole-inventory feeds (copart_csv) that filter rows."""
    kind = strategy["kind"]
    if kind == "copart_csv":
        return _parse_copart_csv(strategy["config"], body, query or "")
    if kind == "reddit_json":
        return _parse_reddit_json(page_url, body)
    if kind in ("css", "browser_css"):
        return _parse_css(strategy["config"], page_url, body)
    if kind == "facebook_marketplace":
        return _parse_facebook(page_url, body)
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
    raise ValueError(f"unknown strategy kind: {kind}")
