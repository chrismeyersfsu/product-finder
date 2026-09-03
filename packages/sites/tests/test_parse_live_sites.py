"""Parsers for the 2026-09 live-rederived selector sets, on fixtures."""

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


def _parse(slug, kind, fixture, url):
    body = (FIXTURES / fixture).read_text()
    return parse.parse_listings(_strategy(slug, kind), url, body)


def test_officedepot_css():
    out = _parse("officedepot", "css", "officedepot.html", "https://www.officedepot.com/a/search/")
    assert len(out) == 2  # card with no description link skipped
    assert out[0]["title"].startswith("ThinkPad T14s")
    assert out[0]["price"] == 1149.99
    assert out[0]["url"].startswith("https://www.officedepot.com/a/products/6790912")


def test_swappa_css():
    out = _parse("swappa", "css", "swappa.html", "https://swappa.com/search?q=x")
    assert len(out) == 2
    assert out[1]["title"] == "Lenovo ThinkPad X1 Carbon (Gen 6)"
    assert out[1]["price"] == 189.0
    assert out[1]["url"] == "https://swappa.com/listings/lenovo-x1-carbon-gen-6"


def test_target_browser_title_from_aria_label():
    out = _parse("target", "browser_css", "target.html", "https://www.target.com/s?searchTerm=x")
    assert len(out) == 2
    assert out[0]["title"].startswith("Refurbished: Lenovo Thinkpad X1 Carbon G10")
    assert out[0]["price"] == 999.99  # current price, not the reg price
    assert out[0]["url"].startswith("https://www.target.com/p/")


def test_bestbuy_browser():
    out = _parse(
        "bestbuy", "browser_css", "bestbuy.html", "https://www.bestbuy.com/site/searchpage.jsp"
    )
    assert len(out) == 2
    assert out[0]["title"].startswith("Lenovo - ThinkPad X1 Carbon Gen 12")
    assert out[0]["price"] == 2499.99


def test_backmarket_attrs_on_card():
    out = _parse(
        "backmarket", "browser_css", "backmarket.html", "https://www.backmarket.com/en-us/search"
    )
    assert len(out) == 2
    assert out[0]["title"] == 'Lenovo ThinkPad X1 Carbon G9 14"'
    assert out[0]["price"] == 463.0
    assert out[0]["url"] == "https://www.backmarket.com/en-us/p/thinkpad-x1-carbon-g9/47f02719"


def test_woot_feed_split_prices():
    out = _parse("woot", "browser_css", "woot.html", "https://www.woot.com/category/computers")
    assert len(out) == 3  # unfiltered here; run.py applies the local filter
    assert out[0]["price"] == 349.99
    assert "ThinkPad X1 Carbon G8" in out[0]["title"]
    assert out[1]["price"] == 27.99


def test_goodwill_api_json():
    out = _parse(
        "shopgoodwill", "goodwill_api", "goodwill_api.json", "https://buyerapi.shopgoodwill.com"
    )
    assert len(out) == 2  # null-id row skipped
    assert out[0]["price"] == 54.0
    assert out[0]["url"] == "https://shopgoodwill.com/item/275108890"
    assert out[1]["price"] == 129.99  # buyNowPrice fallback when currentPrice 0
    assert out[0]["image_url"] is None  # live-rederived capture carries no image field


def test_bonanza_css_split_price_spans():
    out = _parse("bonanza", "css", "bonanza.html", "https://www.bonanza.com/items/search")
    assert len(out) == 3
    assert out[0]["title"].startswith("Love Beauty and Planet Bath Bombs")
    assert out[0]["price"] == 15.83  # $ / whole / . / cents are separate spans
    assert out[2]["price"] == 4.95
    assert out[0]["url"].startswith("https://www.bonanza.com/listings/Love-Beauty")
    # spec.py's explicit "image" selector skips the top-seller badge icon
    # (also an <img>, earlier in the card) and picks the real photo
    assert out[0]["image_url"] == (
        "https://images-bucket.bonanzastatic.com/afu/images/94c2/7149/4366_16176832346"
        "/s-l1600_thumb200.jpg"
    )
