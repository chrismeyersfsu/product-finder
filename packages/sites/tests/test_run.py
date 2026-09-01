"""search_site/search_many against a monkeypatched fetch._get seam."""

from pathlib import Path

from product_finder_sites import fetch, run
from product_finder_sites.spec import BUILTIN_SITES

FIXTURES = Path(__file__).parent / "fixtures"
EBAY = next(s for s in BUILTIN_SITES if s["slug"] == "ebay")


def test_search_site_ok(monkeypatch):
    seen_urls = []

    def fake_get(url, headers=None, timeout=25.0):
        seen_urls.append(url)
        return (FIXTURES / "ebay.html").read_text()

    monkeypatch.setattr(fetch, "_get", fake_get)
    result = run.search_site(EBAY, "thinkpad x1 carbon")
    assert result["error"] is None
    assert len(result["listings"]) == 2
    assert all(li["site_slug"] == "ebay" for li in result["listings"])
    assert "thinkpad+x1+carbon" in seen_urls[0]


def test_search_site_fetch_error_is_a_value(monkeypatch):
    def fake_get(url, headers=None, timeout=25.0):
        raise fetch.FetchError("HTTP 403")

    monkeypatch.setattr(fetch, "_get", fake_get)
    result = run.search_site(EBAY, "x")
    assert result == {"site": "ebay", "listings": [], "error": "HTTP 403"}


def test_search_many_dedupes_across_queries(monkeypatch):
    monkeypatch.setattr(
        fetch, "_get", lambda url, headers=None, timeout=25.0: (FIXTURES / "ebay.html").read_text()
    )
    out = run.search_many([EBAY], ["query one", "query two"])
    assert len(out["listings"]) == 2  # same fixture twice -> deduped by url
    assert out["errors"] == {}
