"""B&H Photo Video. bhphotovideo.html is a REAL trimmed capture of
bhphotovideo.com/c/search?q=iPad+Air+M4+256GB (2026-09-05): two of the
SSR'd miniProductPage cards (the first, which carries an <img>, and a
later one, which carries none) plus the bh-preloaded-data state blob
cut to four items, the last two edited into a Used-department copy and
a price-withheld copy."""

from pathlib import Path

from product_finder_sites import fetch, parse, run
from product_finder_sites.spec import BUILTIN_SITES

FIXTURES = Path(__file__).parent / "fixtures"
BH = next(s for s in BUILTIN_SITES if s["slug"] == "bhphotovideo")
PLAIN, BROWSER = BH["config"]["strategies"]
PAGE = "https://www.bhphotovideo.com/c/search?q=iPad+Air+M4+256GB"


def test_rows_come_from_the_preloaded_state_not_the_cards():
    body = (FIXTURES / "bhphotovideo.html").read_text()
    out = parse.parse_listings(PLAIN, PAGE, body)
    # four state items, though only two cards were server-rendered
    assert [li["title"] for li in out] == [
        'Apple 11" iPad Air (M4, 256GB, Wi-Fi Only, Space Gray)',
        'Apple 13" iPad Air (M4, 256GB, Wi-Fi Only, Gray)',
        'Apple 13" iPad Air (M4, 256GB, Wi-Fi + Cellular, Purple)',
        'Apple 11" iPad Air (M4, 256GB, Wi-Fi Only, Purple)',
    ]
    first = out[0]
    assert first["price"] == 849.0
    assert first["url"] == (
        "https://www.bhphotovideo.com/c/product/1956579-REG/apple_mh354ll_a_11_ipad_air_m4.html"
    )
    assert first["condition"] is None
    assert first["seller_rating"] is None and first["sold_at"] is None
    # every row has a hotlinkable static.bhphoto.com image, never the
    # cdn-cgi proxy the SSR card's <img src> points at
    for li in out:
        assert li["image_url"].startswith("https://static.bhphoto.com/images/images345x345/")
    assert out[2]["condition"] == "used"
    assert out[2]["url"].endswith("/1956629-USED/apple_mh9l4ll_a_13_ipad_air_m4.html")
    assert out[3]["price"] is None  # showPrice false -> still listed, unpriced
    # the browser tier hands the parser the same document
    assert parse.parse_listings(BROWSER, PAGE, body) == out


def test_page_without_state_blob_parses_to_nothing():
    assert parse.parse_listings(PLAIN, PAGE, "<html><body>Access Denied</body></html>") == []
    broken = '<div class="bh-preloaded-data" data-data="{not json"></div>'
    assert parse.parse_listings(PLAIN, PAGE, broken) == []
    empty = '<div class="bh-preloaded-data" data-data="{&quot;ListingStore&quot;:{}}"></div>'
    assert parse.parse_listings(PLAIN, PAGE, empty) == []


def test_plain_http_first_then_headless_render_of_the_same_page(monkeypatch):
    body = (FIXTURES / "bhphotovideo.html").read_text()
    seen = {}

    def fake_get(url, headers=None, timeout=25.0):
        seen["get"] = url
        return body

    def fake_browser(url, wait=None, timeout=30.0, cookies=None):
        seen["browser"] = (url, wait)
        return body

    monkeypatch.setattr(fetch, "_get", fake_get)
    monkeypatch.setattr(fetch, "_get_browser", fake_browser)
    result = run.search_site(BH, "iPad Air M4 256GB")
    assert seen == {"get": PAGE}  # plain HTTP answered: no render
    assert result["strategy"] == "bhphotovideo" and len(result["listings"]) == 4

    # B&H 403s most plain requests; the browser tier renders the same
    # URL and waits for the state blob, which the parser reads as-is
    def walled_get(url, headers=None, timeout=25.0):
        raise fetch.FetchError("HTTP 403")

    monkeypatch.setattr(fetch, "_get", walled_get)
    result = run.search_site(BH, "iPad Air M4 256GB")
    assert seen["browser"] == (PAGE, "div.bh-preloaded-data")
    assert result["strategy"] == "bhphotovideo_browser" and len(result["listings"]) == 4

    monkeypatch.setattr(
        fetch, "_get_browser", lambda url, wait=None, timeout=30.0, cookies=None: "Access Denied"
    )
    walled = run.search_site(BH, "iPad Air M4 256GB")
    assert walled["listings"] == []
    assert walled["error"] == "json: HTTP 403; browser: challenge page"
