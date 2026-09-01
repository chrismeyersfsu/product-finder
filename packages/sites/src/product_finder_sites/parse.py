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
"""

import json
import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

_PRICE_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")
# Woot-style split price markup: "$ 27 99" meaning $27.99
_PRICE_SPLIT_RE = re.compile(r"\$\s*(\d{1,4})\s+(\d{2})(?!\d)")
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
    if kind == "ebay_api":
        return _parse_ebay_api(body)
    if kind == "bestbuy_api":
        return _parse_bestbuy_api(body)
    if kind == "walmart_api":
        return _parse_walmart_api(page_url, body)
    if kind == "goodwill_api":
        return _parse_goodwill_api(body)
    raise ValueError(f"unknown strategy kind: {kind}")
