"""Strategy iteration against monkeypatched fetch seams: API -> css -> browser."""

from pathlib import Path

import pytest
from product_finder_sites import fetch, run
from product_finder_sites.spec import BUILTIN_SITES

FIXTURES = Path(__file__).parent / "fixtures"
SITES = {s["slug"]: s for s in BUILTIN_SITES}


@pytest.fixture(autouse=True)
def no_api_creds(monkeypatch):
    for var in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "BESTBUY_API_KEY", "WALMART_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_api_unset_falls_through_to_css(monkeypatch):
    monkeypatch.setattr(
        fetch, "_get", lambda url, headers=None, timeout=25.0: (FIXTURES / "ebay.html").read_text()
    )
    result = run.search_site(SITES["ebay"], "thinkpad x1 carbon")
    assert result["error"] is None
    assert result["strategy"] == "css"
    assert result["attempts"] == [
        {"strategy": "ebay_api", "error": "EBAY_CLIENT_ID/EBAY_CLIENT_SECRET unset"}
    ]
    assert len(result["listings"]) == 2
    assert all(li["site_slug"] == "ebay" for li in result["listings"])


def test_api_tier_runs_when_creds_set(monkeypatch):
    monkeypatch.setenv("EBAY_CLIENT_ID", "cid")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "secret")
    monkeypatch.setattr(
        fetch, "_post", lambda url, data, headers=None, timeout=25.0: '{"access_token": "tok"}'
    )

    def fake_get(url, headers=None, timeout=25.0):
        assert headers["Authorization"] == "Bearer tok"
        return (FIXTURES / "ebay_api.json").read_text()

    monkeypatch.setattr(fetch, "_get", fake_get)
    result = run.search_site(SITES["ebay"], "x1 carbon")
    assert result["strategy"] == "ebay_api"
    assert result["listings"][0]["seller_feedback_count"] == 2394


def test_all_tiers_fail_error_is_a_value(monkeypatch):
    def fail(url, headers=None, timeout=25.0):
        raise fetch.FetchError("HTTP 403")

    monkeypatch.setattr(fetch, "_get", fail)
    result = run.search_site(SITES["ebay"], "x")
    assert result["strategy"] is None and result["listings"] == []
    assert "ebay_api: EBAY_CLIENT_ID/EBAY_CLIENT_SECRET unset" in result["error"]
    assert "css: HTTP 403" in result["error"]


def test_empty_page_falls_through_to_browser(monkeypatch):
    monkeypatch.setattr(
        fetch, "_get", lambda url, headers=None, timeout=25.0: "<html>bot wall</html>"
    )
    monkeypatch.setattr(
        fetch,
        "_get_browser",
        lambda url, wait=None, timeout=30.0: (
            (FIXTURES / "ebay.html").read_text().replace("s-item", "x")
        ),
    )
    # amazon: css parses nothing -> browser_css runs (selectors won't match the
    # ebay fixture either, so amazon ends in error; assert the attempt order)
    result = run.search_site(SITES["amazon"], "x")
    kinds = [a["strategy"] for a in result["attempts"]]
    assert kinds == ["css", "browser_css"]


def test_unwired_browser_degrades_to_error(monkeypatch):
    monkeypatch.setattr(
        fetch, "_get", lambda url, headers=None, timeout=25.0: "<html>bot wall</html>"
    )
    result = run.search_site(SITES["target"], "x")
    assert result["strategy"] is None
    assert "browser_css: browser fetching not wired" in result["error"]


def test_search_many_dedupes_and_reports_strategies(monkeypatch):
    monkeypatch.setattr(
        fetch, "_get", lambda url, headers=None, timeout=25.0: (FIXTURES / "ebay.html").read_text()
    )
    out = run.search_many([SITES["ebay"]], ["query one", "query two"])
    assert len(out["listings"]) == 2  # same fixture twice -> deduped by url
    assert out["errors"] == {}
    assert out["strategies"] == {"ebay": "css"}
