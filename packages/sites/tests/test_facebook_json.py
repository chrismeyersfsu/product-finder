"""facebook_json tier: one paced, retried document GET per query — page 1
is read out of the feed embedded in the document itself, no GraphQL POST
(see api.py/parse.py module docstrings for why: an earlier version of
this port POSTed for page 1 too, but that endpoint started answering
every query with a GraphQL "Rate limit exceeded" soft error in
production). Ported from the resilience-first waterfall bake-off
prototype's strategy 1 (product-finder-fb-requests/experiments/
facebook-requests/report.md, 2026-09-05). Every test here monkeypatches
fetch._get and api._sleep so nothing ever touches the network or
actually sleeps.
"""

from pathlib import Path

import pytest
from product_finder_sites import api, fetch, parse, run
from product_finder_sites.spec import BUILTIN_SITES

FIXTURES = Path(__file__).parent / "fixtures"
SITES = {s["slug"]: s for s in BUILTIN_SITES}


def _strategy(slug, kind):
    site = SITES[slug]
    return next(s for s in site["config"]["strategies"] if s["kind"] == kind)


FB_JSON_CONFIG = _strategy("facebook-marketplace", "facebook_json")["config"]


def _doc():
    return (FIXTURES / "facebook_json_search_doc.html").read_text()


def _empty_doc():
    return (FIXTURES / "facebook_json_search_doc_empty.html").read_text()


def _login_wall():
    return (FIXTURES / "facebook_json_login_wall.html").read_text()


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(api, "_sleep", lambda seconds: None)


def test_happy_path_document_embedded_feed(monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=25.0):
        calls.append(url)
        assert "/marketplace/durham/search" in url
        return _doc()

    monkeypatch.setattr(fetch, "_get", fake_get)

    body, page_url = api.fetch_facebook_json(FB_JSON_CONFIG, "bandaid")
    assert len(calls) == 1  # one GET, no GraphQL POST

    listings = parse.parse_listings({"kind": "facebook_json", "config": {}}, page_url, body)
    assert len(listings) == 3
    first = listings[0]
    assert first["title"] == "Bandaids"
    assert first["price"] == 8.0
    assert first["location"] == "Okeechobee, FL"
    assert first["url"] == "https://www.facebook.com/marketplace/item/917742840657219/"
    assert first["image_url"] == (
        "https://scontent-iad3-1.xx.fbcdn.net/v/t39.84726-6/"
        "759040663_2252138442231337_5310480539420055245_n.jpg?oh=00_FIXTURE&oe=FIXTURE"
    )
    assert first["seller_rating"] is None
    assert first["seller_feedback_count"] is None
    assert listings[1]["location"] == "Concord, NC"
    assert listings[1]["price"] == 1.0
    assert listings[2]["title"] == "Bactine max liquid bandaid"
    assert listings[2]["price"] == 5.0


def test_empty_document_returns_empty_list(monkeypatch):
    monkeypatch.setattr(fetch, "_get", lambda url, headers=None, timeout=25.0: _empty_doc())
    body, page_url = api.fetch_facebook_json(FB_JSON_CONFIG, "x")
    listings = parse.parse_listings({"kind": "facebook_json", "config": {}}, page_url, body)
    assert listings == []


def test_login_wall_raises_before_falling_through(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, headers=None, timeout=25.0):
        calls["n"] += 1
        return _login_wall()

    monkeypatch.setattr(fetch, "_get", fake_get)
    with pytest.raises(fetch.FetchError, match="login wall"):
        api.fetch_facebook_json(FB_JSON_CONFIG, "x")
    assert calls["n"] == 1  # a wall is never retried


def test_search_site_falls_through_to_browser_on_login_wall(monkeypatch):
    monkeypatch.delenv("FB_COOKIES", raising=False)
    monkeypatch.setattr(fetch, "_get", lambda url, headers=None, timeout=25.0: _login_wall())
    monkeypatch.setattr(
        fetch,
        "_get_browser",
        lambda url, wait=None, timeout=30.0, cookies=None: (
            FIXTURES / "facebook_login.html"
        ).read_text(),
    )
    result = run.search_site(SITES["facebook-marketplace"], "x1 carbon")
    assert result["strategy"] is None
    assert result["attempts"][0] == {"strategy": "facebook_json", "error": "login wall"}
    assert "json: login wall" in result["error"]
    assert "login wall — set FB_COOKIES" in result["error"]


def test_schema_drift_raises_for_fallthrough():
    # No marketplace_search blob at all: the feed shape changed, or this
    # isn't a search-results page — must raise, never return [].
    body = "<html><body><script data-sjs>{}</script></body></html>"
    with pytest.raises(ValueError, match="no marketplace_search script"):
        parse.parse_listings({"kind": "facebook_json", "config": {}}, "https://x", body)


def test_schema_drift_blob_without_feed_units_raises():
    body = (
        '<script type="application/json" data-sjs>'
        '{"marketplace_search": {"not_feed_units": true}}'
        "</script>"
    )
    with pytest.raises(ValueError, match="no feed_units"):
        parse.parse_listings({"kind": "facebook_json", "config": {}}, "https://x", body)


def test_search_site_falls_through_to_browser_on_schema_drift(monkeypatch):
    monkeypatch.delenv("FB_COOKIES", raising=False)
    monkeypatch.setattr(
        fetch, "_get", lambda url, headers=None, timeout=25.0: "<html>no script here</html>"
    )
    monkeypatch.setattr(
        fetch,
        "_get_browser",
        lambda url, wait=None, timeout=30.0, cookies=None: (FIXTURES / "facebook.html").read_text(),
    )
    result = run.search_site(SITES["facebook-marketplace"], "thinkpad")
    assert result["strategy"] == "facebook_marketplace"
    assert result["attempts"][0]["strategy"] == "facebook_json"
    assert "no marketplace_search script" in result["attempts"][0]["error"]


def test_transient_429_retries_and_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky_get(url, headers=None, timeout=25.0):
        calls["n"] += 1
        if calls["n"] == 1:
            raise fetch.FetchError("HTTP 429")
        return _doc()

    monkeypatch.setattr(fetch, "_get", flaky_get)
    body, _page_url = api.fetch_facebook_json(FB_JSON_CONFIG, "x")
    assert calls["n"] == 2
    listings = parse.parse_listings({"kind": "facebook_json", "config": {}}, "https://x", body)
    assert len(listings) == 3


def test_5xx_retries_and_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky_get(url, headers=None, timeout=25.0):
        calls["n"] += 1
        if calls["n"] == 1:
            raise fetch.FetchError("HTTP 503")
        return _doc()

    monkeypatch.setattr(fetch, "_get", flaky_get)
    api.fetch_facebook_json(FB_JSON_CONFIG, "x")
    assert calls["n"] == 2


def test_client_error_is_not_retried(monkeypatch):
    calls = {"n": 0}

    def fail_get(url, headers=None, timeout=25.0):
        calls["n"] += 1
        raise fetch.FetchError("HTTP 404")

    monkeypatch.setattr(fetch, "_get", fail_get)
    with pytest.raises(fetch.FetchError, match="404"):
        api.fetch_facebook_json(FB_JSON_CONFIG, "x")
    assert calls["n"] == 1


def test_retries_exhaust_at_four_attempts_then_raise(monkeypatch):
    calls = {"n": 0}

    def always_500(url, headers=None, timeout=25.0):
        calls["n"] += 1
        raise fetch.FetchError("HTTP 500")

    monkeypatch.setattr(fetch, "_get", always_500)
    with pytest.raises(fetch.FetchError, match="500"):
        api.fetch_facebook_json(FB_JSON_CONFIG, "x")
    assert calls["n"] == 4


def test_unexpected_exception_normalized_to_fetch_error(monkeypatch):
    def boom(url, headers=None, timeout=25.0):
        raise RuntimeError("boom")

    monkeypatch.setattr(fetch, "_get", boom)
    with pytest.raises(fetch.FetchError, match="facebook_json: RuntimeError: boom"):
        api.fetch_facebook_json(FB_JSON_CONFIG, "x")


def test_search_site_falls_back_to_browser_tier_when_json_fails(monkeypatch):
    monkeypatch.setenv("FB_COOKIES", "c_user=1; xs=abc")
    monkeypatch.setattr(
        fetch, "_get", lambda url, headers=None, timeout=25.0: "<html>no script here</html>"
    )
    monkeypatch.setattr(
        fetch,
        "_get_browser",
        lambda url, wait=None, timeout=30.0, cookies=None: (FIXTURES / "facebook.html").read_text(),
    )
    result = run.search_site(SITES["facebook-marketplace"], "thinkpad")
    assert result["error"] is None
    assert result["strategy"] == "facebook_marketplace"
    assert result["attempts"][0]["strategy"] == "facebook_json"
    assert len(result["listings"]) == 3


def test_json_tier_label_is_json():
    assert run._label("facebook_json") == "json"


def test_requests_are_paced_to_one_per_second(monkeypatch):
    slept = []
    monkeypatch.setattr(api, "_sleep", slept.append)
    monkeypatch.setattr(api, "_fb_last_request_at", 0.0)
    monkeypatch.setattr(fetch, "_get", lambda url, headers=None, timeout=25.0: _doc())
    api.fetch_facebook_json(FB_JSON_CONFIG, "x")  # one GET
    api.fetch_facebook_json(FB_JSON_CONFIG, "y")  # one more GET, back to back
    # first request is free; the second (only) later one waits out the 1s interval
    assert len(slept) == 1 and 0 < slept[0] <= api._FB_MIN_INTERVAL_S
