"""Parsers for the two grocery sites (harris-teeter, food-lion).

Unlike test_parse_live_sites.py, the css/browser_css fixtures here are
SYNTHETIC — harristeeter.com and foodlion.com wall this network at
every tier (see spec.py's module docstring), so their real markup was
never captured. These tests only pin down parser mechanics (location/
condition pass-through, skip rules) against spec.py's guessed
selectors. kroger_api.json mirrors Kroger's published Products API
response shape (not independently curl-verified — no credentials).
foodlion_walled.html is a REAL trimmed capture of the DataDome
interstitial a headless browser gets back instead of results.
"""

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


def test_harris_teeter_css():
    out = _parse("harris-teeter", "css", "harristeeter.html", "https://www.harristeeter.com/search")
    assert len(out) == 2  # linkless "coming soon" card skipped
    assert out[0]["title"] == "Kroger Grade A Large Eggs, 12 ct"
    assert out[0]["price"] == 3.49
    assert out[0]["url"] == (
        "https://www.harristeeter.com/p/kroger-grade-a-large-eggs-12-ct/0001111060903"
    )
    assert out[0]["location"] == "Harris Teeter, 2107 Hillsborough Rd, Durham, NC 27705"
    assert out[0]["condition"] == "new"


def test_food_lion_css():
    out = _parse("food-lion", "css", "foodlion.html", "https://www.foodlion.com/shop/search")
    assert len(out) == 2
    assert out[0]["title"] == "Food Lion Large White Eggs, 12 ct"
    assert out[0]["price"] == 2.89
    assert out[0]["location"] == "Food Lion, 3808 Guess Rd, Durham, NC 27705"
    assert out[0]["condition"] == "new"


def test_food_lion_datadome_wall_is_not_mistaken_for_zero_results():
    # A real captured wall page: no product-tile cards to select, so the
    # parser correctly returns [] — run.py (not parse.py) is what labels
    # this a "challenge page" via its body-text regex.
    out = _parse(
        "food-lion", "browser_css", "foodlion_walled.html", "https://www.foodlion.com/shop/search"
    )
    assert out == []


def test_kroger_api_json():
    out = parse.parse_listings(
        _strategy("harris-teeter", "kroger_api"),
        "https://api.kroger.com",
        (FIXTURES / "kroger_api.json").read_text(),
    )
    assert len(out) == 3
    assert out[0]["title"] == "Kroger Grade A Large Eggs, 12 ct"
    assert out[0]["price"] == 3.49  # "regular", not the sometimes-0 "promo"
    assert out[0]["url"] == (
        "https://www.harristeeter.com/search?query=Kroger+Grade+A+Large+Eggs%2C+12+ct"
    )
    assert out[0]["location"] == "Harris Teeter, 2107 Hillsborough Rd, Durham, NC 27705"
    assert out[0]["condition"] == "new"
    assert out[1]["price"] == 5.99
    assert out[2]["price"] is None  # out-of-stock row: no `items` entries
