"""copart_csv: Copart's member CSV filtered per query -> lot listings.

copart_salesdata.csv is SYNTHETIC — it follows the column names Copart
documents for its "CSV Sales Data" download, but no real file was ever
captured (member-only). These tests pin the parser mechanics: query
word filtering, title/price/condition/location shaping, and the
fetcher's cache-or-download behavior at the fetch._get seam.
"""

from pathlib import Path

import pytest
from product_finder_sites import api, fetch, parse, run
from product_finder_sites.spec import BUILTIN_SITES

FIXTURES = Path(__file__).parent / "fixtures"
COPART = next(s for s in BUILTIN_SITES if s["slug"] == "copart")
CSV = (FIXTURES / "copart_salesdata.csv").read_text()


def test_query_words_filter_rows_and_shape_listings():
    out = parse.parse_listings(COPART, "file:x.csv", CSV, query="Kia EV9")
    assert [li["url"] for li in out] == [
        "https://www.copart.com/lot/80123456",
        "https://www.copart.com/lot/80123457",
    ]
    land, light = out
    assert land["title"] == "2024 KIA EV9 LAND, 12345 mi"
    assert land["price"] == 1250.0  # no buy-it-now: current high bid
    assert land["location"] == "Raleigh, NC"
    assert land["condition"] == (
        "Salvage Certificate; Front End; Runs And Drives; current bid, sale 9/15/2026"
    )
    assert light["price"] == 31000.0  # buy-it-now beats a zero bid
    assert light["condition"] == "Salvage Certificate; Rear End/Side; buy it now"
    assert "salvage" not in land["title"].lower()


def test_trim_word_narrows_and_clean_title_reads_through():
    assert len(parse.parse_listings(COPART, "f", CSV, query="kia ev9 land")) == 1
    (fit,) = parse.parse_listings(COPART, "f", CSV, query="Honda Fit")
    assert fit["title"] == "2019 HONDA FIT LX, 67890 mi"
    assert fit["price"] is None  # no bids yet and no buy-it-now
    assert fit["condition"] == "Clean Title; Normal Wear; Runs And Drives; no bids, sale 9/15/2026"


def test_no_query_returns_every_lot_and_skips_lotless_rows():
    assert len(parse.parse_listings(COPART, "f", CSV)) == 4


def test_unknown_columns_yield_nothing():
    assert parse.parse_listings(COPART, "f", "a,b,c\n1,2,3\n", query="kia") == []


def test_cached_csv_is_used_without_cookies(monkeypatch, tmp_path):
    cache = tmp_path / "copart.csv"
    cache.write_text(CSV)
    monkeypatch.setenv("COPART_CSV", str(cache))
    monkeypatch.delenv("COPART_COOKIES", raising=False)
    monkeypatch.setattr(fetch, "_get", lambda *a, **k: pytest.fail("must not download"))
    result = run.search_site(COPART, "kia ev9")
    assert result["error"] is None and result["strategy"] == "copart_csv"
    assert len(result["listings"]) == 2
    assert result["listings"][0]["site_slug"] == "copart"


def test_no_cache_and_no_cookies_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setenv("COPART_CSV", str(tmp_path / "missing.csv"))
    monkeypatch.delenv("COPART_COOKIES", raising=False)
    result = run.search_site(COPART, "kia ev9")
    assert result["strategy"] is None
    assert result["error"].startswith("csv: COPART_COOKIES unset and no CSV at ")


def test_stale_cache_is_refreshed_with_cookie_header(monkeypatch, tmp_path):
    import os

    cache = tmp_path / "copart.csv"
    cache.write_text("Lot number,Make\n1,OLD\n")
    os.utime(cache, (0, 0))  # ancient
    monkeypatch.setenv("COPART_CSV", str(cache))
    monkeypatch.setenv("COPART_COOKIES", "SESSION=abc")
    seen = {}

    def fake_get(url, headers=None, timeout=25.0):
        seen["url"], seen["headers"] = url, headers
        return CSV

    monkeypatch.setattr(fetch, "_get", fake_get)
    _, url = api.fetch_copart_csv(COPART["config"], "kia")
    assert url == COPART["config"]["url"] and seen["headers"]["Cookie"] == "SESSION=abc"
    assert cache.read_text() == CSV  # cache rewritten
    # fresh now: a second call reads the file, no download
    monkeypatch.setattr(fetch, "_get", lambda *a, **k: pytest.fail("fresh cache"))
    body2, url2 = api.fetch_copart_csv(COPART["config"], "kia")
    assert body2 == CSV and url2.startswith("file:")


def test_expired_cookies_keep_serving_the_stale_cache(monkeypatch, tmp_path):
    import os

    cache = tmp_path / "copart.csv"
    cache.write_text(CSV)
    os.utime(cache, (0, 0))
    monkeypatch.setenv("COPART_CSV", str(cache))
    monkeypatch.setenv("COPART_COOKIES", "SESSION=expired")
    monkeypatch.setattr(fetch, "_get", lambda *a, **k: "<html>Sign in</html>")
    body, _ = api.fetch_copart_csv(COPART["config"], "kia")
    assert body == CSV
    # no cache at all: the failure surfaces
    monkeypatch.setenv("COPART_CSV", str(tmp_path / "none.csv"))
    with pytest.raises(fetch.FetchError, match="not a CSV"):
        api.fetch_copart_csv(COPART["config"], "kia")
