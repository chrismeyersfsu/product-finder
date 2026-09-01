"""Parsers are pure: fixture file in, listing dicts out, no I/O."""

from pathlib import Path

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
    listings = parse.parse_listings(_strategy("ebay", "css"), url, body)
    assert len(listings) == 2  # placeholder "Shop on eBay" row skipped
    first = listings[0]
    assert first["title"].startswith("Lenovo ThinkPad X1 Carbon Gen 6")
    assert first["price"] == 289.99
    assert first["seller_rating"] == 99.1
    assert first["seller_feedback_count"] == 2394
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


def test_parse_garbage_html_returns_empty():
    assert parse.parse_listings(_strategy("ebay", "css"), "https://x", "<html>nothing</html>") == []


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


def test_parse_bestbuy_api_fixture():
    body = (FIXTURES / "bestbuy_api.json").read_text()
    listings = parse.parse_listings({"kind": "bestbuy_api", "config": {}}, "https://x", body)
    assert len(listings) == 1
    assert listings[0]["price"] == 599.99


def test_parse_browser_css_uses_css_selectors():
    body = (FIXTURES / "ebay.html").read_text()
    css = _strategy("ebay", "css")
    listings = parse.parse_listings(
        {"kind": "browser_css", "config": css["config"]}, "https://www.ebay.com/sch", body
    )
    assert len(listings) == 2
