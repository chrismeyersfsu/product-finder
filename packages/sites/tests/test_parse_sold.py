"""Sold-listings parsing: the "date" selector yields ISO sold_at dates."""

from pathlib import Path

from product_finder_sites import parse
from product_finder_sites.spec import BUILTIN_SITES, EBAY_SOLD

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_sold_dates():
    body = (FIXTURES / "ebay_sold.html").read_text()
    out = parse.parse_listings(EBAY_SOLD, EBAY_SOLD["config"]["url"], body)
    by_url = {li["url"]: li for li in out}
    assert by_url["https://www.ebay.com/itm/777"]["sold_at"] == "2025-10-15"
    assert by_url["https://www.ebay.com/itm/777"]["price"] == 254.00
    assert by_url["https://www.ebay.com/itm/777"]["seller_rating"] == 99.4
    assert by_url["https://www.ebay.com/itm/888"]["sold_at"] == "2025-11-02"
    assert by_url["https://www.ebay.com/itm/999"]["sold_at"] is None  # no Sold caption


def test_ebay_sold_not_in_builtin_roster():
    assert all(site["slug"] != "ebay-sold" for site in BUILTIN_SITES)


def test_regular_sites_get_no_sold_at_lookup():
    body = (FIXTURES / "ebay.html").read_text()
    ebay = next(s for s in BUILTIN_SITES if s["slug"] == "ebay")
    css = next(st for st in ebay["config"]["strategies"] if st["kind"] == "css")
    out = parse.parse_listings(css, "https://www.ebay.com/sch/", body)
    assert all(li["sold_at"] is None for li in out)
