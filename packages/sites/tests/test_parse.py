"""Parsers are pure: fixture file in, listing dicts out, no I/O."""

from pathlib import Path

import pytest
from product_finder_sites import parse
from product_finder_sites.spec import BUILTIN_SITES

FIXTURES = Path(__file__).parent / "fixtures"
SITES = {s["slug"]: s for s in BUILTIN_SITES}


def _strategy(slug, kind):
    site = SITES[slug]
    if site["kind"] != "tiered":
        return site
    return next(s for s in site["config"]["strategies"] if s["kind"] == kind)


def test_parse_ebay_fixture():
    body = (FIXTURES / "ebay.html").read_text()
    url = "https://www.ebay.com/sch/i.html?_nkw=x"
    listings = parse.parse_listings(_strategy("ebay", "browser_css"), url, body)
    assert len(listings) == 2  # placeholder "Shop on eBay" row skipped
    first = listings[0]
    assert first["title"].startswith("Lenovo ThinkPad X1 Carbon Gen 6")
    assert first["price"] == 289.99
    assert first["seller_rating"] == 99.1
    assert first["seller_feedback_count"] == 2394
    assert first["image_url"] is None  # ebay.html carries no <img> markup
    # relative href resolved against the page url
    assert listings[1]["url"] == "https://www.ebay.com/itm/456"
    assert listings[1]["price"] == 1349.00
    assert listings[1]["seller_rating"] is None


def test_parse_reddit_fixture():
    body = (FIXTURES / "hardwareswap.json").read_text()
    listings = parse.parse_listings(SITES["reddit-hardwareswap"], "https://www.reddit.com", body)
    assert len(listings) == 2  # post without permalink skipped
    assert listings[0]["url"].startswith("https://www.reddit.com/r/hardwareswap/")
    assert listings[0]["price"] == 300.0  # pulled from selftext
    # preview.images[0].source.url wins over `thumbnail`, &amp; unescaped
    assert listings[0]["image_url"] == (
        "https://preview.redd.it/abc123thinkpad.jpg?width=640&format=pjpg&auto=webp&s=deadbeef1234"
    )
    assert listings[1]["image_url"] is None  # thumbnail: "self" is a sentinel, not a URL


def test_parse_garbage_html_returns_empty():
    assert (
        parse.parse_listings(_strategy("ebay", "browser_css"), "https://x", "<html>nothing</html>")
        == []
    )


def test_price_helper():
    assert parse._price("$1,234.56") == 1234.56
    assert parse._price("US $89") == 89.0
    assert parse._price(None) is None
    assert parse._price("free") is None


def test_parse_ebay_api_fixture():
    body = (FIXTURES / "ebay_api.json").read_text()
    listings = parse.parse_listings(
        {"kind": "ebay_api", "config": {}}, "https://api.ebay.com", body
    )
    assert len(listings) == 2  # item without url skipped
    assert listings[0]["price"] == 289.99
    assert listings[0]["seller_rating"] == 99.1
    assert listings[0]["seller_feedback_count"] == 2394
    assert listings[1]["seller_rating"] is None
    assert listings[0]["image_url"] == "https://i.ebayimg.com/images/g/8KIAAOSwsalcdef/s-l500.jpg"
    # no `image`, falls back to thumbnailImages[0].imageUrl
    assert listings[1]["image_url"] == "https://i.ebayimg.com/images/g/th456ghi/s-l225.jpg"


def test_parse_bestbuy_api_fixture():
    body = (FIXTURES / "bestbuy_api.json").read_text()
    listings = parse.parse_listings({"kind": "bestbuy_api", "config": {}}, "https://x", body)
    assert len(listings) == 1
    assert listings[0]["price"] == 599.99
    assert listings[0]["image_url"] == (
        "https://pisces.bbystatic.com/image2/BestBuy_US/images/products/6353713/6353713_sd.jpg"
    )


def test_parse_browser_css_uses_css_selectors():
    body = (FIXTURES / "ebay.html").read_text()
    css = _strategy("ebay", "browser_css")
    listings = parse.parse_listings(
        {"kind": "browser_css", "config": css["config"]}, "https://www.ebay.com/sch", body
    )
    assert len(listings) == 2


def test_parse_facebook_marketplace():
    body = (FIXTURES / "facebook.html").read_text()
    items = parse.parse_listings(
        {"kind": "facebook_marketplace", "config": {}},
        "https://www.facebook.com/marketplace/durham/search?query=x",
        body,
    )
    assert len(items) == 3  # duplicate item url collapsed
    first = items[0]
    assert first["title"] == "Lenovo ThinkPad X1 Carbon Gen 6 i7 16GB 512GB NVMe"
    assert first["price"] == 450.0
    assert first["location"] == "Durham, NC"
    assert first["url"] == "https://www.facebook.com/marketplace/item/1112223334445556/"
    assert (
        first["image_url"] == "https://scontent.xx.fbcdn.net/v/t45.5328-4/thinkpad_x1_carbon_g6.jpg"
    )
    assert items[1]["price"] == 1200.0
    assert items[1]["image_url"] is None  # card has no <img>
    assert items[2]["price"] is None  # no-price card still parses


def test_facebook_login_wall_raises():
    body = (FIXTURES / "facebook_login.html").read_text()
    with pytest.raises(parse.LoginWall, match="FB_COOKIES"):
        parse.parse_listings(
            {"kind": "facebook_marketplace", "config": {}}, "https://www.facebook.com/", body
        )


_IMAGE_CARD_CSS = {"item": "div.card", "title": "h3", "price": ".price", "link": "a"}


def test_parse_css_lazy_load_image_fallback():
    # Card 1: src is a base64 data: placeholder, the real photo is
    # lazy-loaded into data-src — the default attr chain must skip the
    # placeholder and pick up data-src instead.
    # Card 2: src is an obvious spacer gif with no lazy-load attr at all —
    # image_url should come back None rather than the spacer.
    body = """
    <div class="card">
      <img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBTAA7"
           data-src="https://cdn.example.com/real-photo.jpg">
      <h3>Widget</h3>
      <span class="price">$9.99</span>
      <a href="/widget">Widget</a>
    </div>
    <div class="card">
      <img src="https://cdn.example.com/img/spacer.gif">
      <h3>Gadget</h3>
      <span class="price">$4.99</span>
      <a href="/gadget">Gadget</a>
    </div>
    """
    listings = parse.parse_listings(
        {"kind": "css", "config": _IMAGE_CARD_CSS}, "https://example.com", body
    )
    assert len(listings) == 2
    assert listings[0]["image_url"] == "https://cdn.example.com/real-photo.jpg"
    assert listings[1]["image_url"] is None


def test_parse_css_image_selector_override():
    # Without an explicit "image" selector, the default "first img in the
    # item" would grab the decorative badge icon instead of the real photo.
    config = {**_IMAGE_CARD_CSS, "image": "img.product-photo", "image_attr": "data-original"}
    body = """
    <div class="card">
      <img class="badge-icon" src="https://cdn.example.com/badge.png">
      <img class="product-photo" src="https://cdn.example.com/placeholder.png"
           data-original="https://cdn.example.com/product-real.jpg">
      <h3>Widget</h3>
      <span class="price">$9.99</span>
      <a href="/widget">Widget</a>
    </div>
    """
    listings = parse.parse_listings({"kind": "css", "config": config}, "https://example.com", body)
    assert listings[0]["image_url"] == "https://cdn.example.com/product-real.jpg"
