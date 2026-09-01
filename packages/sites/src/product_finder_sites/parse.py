"""Pure parsers: search-page body -> listing dicts. No I/O ever.

Owns HTML/JSON interpretation only; fetch.py owns the network,
spec.py owns which selectors to use. Callers rely on:
parse_listings() never raises on weird markup — it returns whatever it
could parse — and every returned dict has a non-empty title and an
absolute url; price/seller fields are None when absent.
"""

import json
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

_PRICE_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")
_NUM_RE = re.compile(r"([\d,]+(?:\.\d{1,2})?)")
# eBay-style "seller_name (2,394) 99.1%"
_SELLER_RE = re.compile(r"\(([\d,]+)\)\s*([\d.]+)%")


def _price(text: str | None) -> float | None:
    if not text:
        return None
    m = _PRICE_RE.search(text) or _NUM_RE.search(text)
    return float(m.group(1).replace(",", "")) if m else None


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
        title_node = item.select_one(config["title"])
        link_node = item.select_one(config["link"])
        title = title_node.get_text(" ", strip=True) if title_node else ""
        href = link_node.get(config.get("link_attr", "href")) if link_node else None
        if not title or not href or title.lower() == "shop on ebay":
            continue
        price_node = item.select_one(config["price"]) if config.get("price") else None
        rating = count = None
        if config.get("seller"):
            seller_node = item.select_one(config["seller"])
            rating, count = _seller(seller_node.get_text(" ", strip=True) if seller_node else None)
        out.append(
            {
                "title": title,
                "price": _price(price_node.get_text(" ", strip=True) if price_node else None),
                "url": urljoin(page_url, str(href)),
                "seller_rating": rating,
                "seller_feedback_count": count,
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


def parse_listings(site: dict, page_url: str, body: str) -> list[dict]:
    if site["kind"] == "reddit_json":
        return _parse_reddit_json(page_url, body)
    if site["kind"] == "css":
        return _parse_css(site["config"], page_url, body)
    raise ValueError(f"unknown site kind: {site['kind']}")
