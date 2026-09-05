"""facebook_json tier: one document GET (session harvest) + one GraphQL
POST per query, ported from the resilience-first waterfall bake-off
prototype (product-finder-fb-requests/experiments/facebook-requests/
report.md, 2026-09-05). Every test here monkeypatches fetch._get/_post
and api._sleep so nothing ever touches the network or actually sleeps.
"""

import json
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


def _graphql():
    return (FIXTURES / "facebook_json_graphql.json").read_text()


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(api, "_sleep", lambda seconds: None)


def test_happy_path_document_then_graphql(monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=25.0):
        calls.append(("get", url))
        assert "/marketplace/durham/search" in url
        return _doc()

    def fake_post(url, data, headers=None, timeout=25.0):
        calls.append(("post", url))
        assert url == "https://www.facebook.com/api/graphql/"
        assert "lsd=FIXTUREfakeLSDtoken0000000" in data
        assert "doc_id=27212616558440397" in data
        assert headers["X-FB-LSD"] == "FIXTUREfakeLSDtoken0000000"
        return _graphql()

    monkeypatch.setattr(fetch, "_get", fake_get)
    monkeypatch.setattr(fetch, "_post", fake_post)

    body, page_url = api.fetch_facebook_json(FB_JSON_CONFIG, "bandaid")
    assert [c[0] for c in calls] == ["get", "post"]

    listings = parse.parse_listings({"kind": "facebook_json", "config": {}}, page_url, body)
    assert len(listings) == 3
    first = listings[0]
    assert first["title"] == "Banda de capitán"
    assert first["price"] == 12.0
    assert first["location"] == "Durham, NC"
    assert first["url"] == "https://www.facebook.com/marketplace/item/980731561597880/"
    assert first["image_url"] == (
        "https://scontent-iad3-2.xx.fbcdn.net/v/t39.84726-6/"
        "714852647_940142469076201_4344016647644230777_n.jpg?oh=00_FIXTURE&oe=FIXTURE"
    )
    assert first["seller_rating"] is None
    assert first["seller_feedback_count"] is None
    assert listings[1]["location"] == "Apex, NC"
    assert listings[2]["price"] == 5.0


def test_empty_page_returns_empty_list():
    body = json.dumps(
        {
            "data": {
                "marketplace_search": {
                    "feed_units": {
                        "edges": [],
                        "page_info": {"end_cursor": None, "has_next_page": False},
                    }
                }
            }
        }
    )
    listings = parse.parse_listings({"kind": "facebook_json", "config": {}}, "https://x", body)
    assert listings == []


def test_login_wall_raises_before_any_harvest_or_post(monkeypatch):
    calls = {"get": 0, "post": 0}

    def fake_get(url, headers=None, timeout=25.0):
        calls["get"] += 1
        return (FIXTURES / "facebook_json_login_wall.html").read_text()

    def fake_post(url, data, headers=None, timeout=25.0):
        calls["post"] += 1
        raise AssertionError("login wall must short-circuit before any POST")

    monkeypatch.setattr(fetch, "_get", fake_get)
    monkeypatch.setattr(fetch, "_post", fake_post)
    with pytest.raises(fetch.FetchError, match="login wall"):
        api.fetch_facebook_json(FB_JSON_CONFIG, "x")
    assert calls == {"get": 1, "post": 0}


def test_search_site_falls_through_to_browser_on_login_wall(monkeypatch):
    monkeypatch.delenv("FB_COOKIES", raising=False)
    monkeypatch.setattr(
        fetch,
        "_get",
        lambda url, headers=None, timeout=25.0: (
            FIXTURES / "facebook_json_login_wall.html"
        ).read_text(),
    )
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


def test_graphql_null_soft_error_raises_for_fallthrough():
    body = (FIXTURES / "facebook_json_graphql_null.json").read_text()
    with pytest.raises(Exception):  # noqa: B017 - any exception must fall through, not return []
        parse.parse_listings({"kind": "facebook_json", "config": {}}, "https://x", body)


def test_graphql_stale_doc_id_raises_for_fallthrough():
    body = (FIXTURES / "facebook_json_graphql_error.json").read_text()
    with pytest.raises(Exception):  # noqa: B017
        parse.parse_listings({"kind": "facebook_json", "config": {}}, "https://x", body)


def test_search_site_falls_through_to_browser_on_graphql_error(monkeypatch):
    monkeypatch.delenv("FB_COOKIES", raising=False)
    monkeypatch.setattr(fetch, "_get", lambda url, headers=None, timeout=25.0: _doc())
    monkeypatch.setattr(
        fetch,
        "_post",
        lambda url, data, headers=None, timeout=25.0: (
            FIXTURES / "facebook_json_graphql_error.json"
        ).read_text(),
    )
    monkeypatch.setattr(
        fetch,
        "_get_browser",
        lambda url, wait=None, timeout=30.0, cookies=None: (FIXTURES / "facebook.html").read_text(),
    )
    result = run.search_site(SITES["facebook-marketplace"], "thinkpad")
    assert result["strategy"] == "facebook_marketplace"
    assert result["attempts"][0]["strategy"] == "facebook_json"
    assert "doc_id rotted" in result["attempts"][0]["error"]


def test_transient_429_retries_and_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky_get(url, headers=None, timeout=25.0):
        calls["n"] += 1
        if calls["n"] == 1:
            raise fetch.FetchError("HTTP 429")
        return _doc()

    monkeypatch.setattr(fetch, "_get", flaky_get)
    monkeypatch.setattr(fetch, "_post", lambda url, data, headers=None, timeout=25.0: _graphql())
    body, _page_url = api.fetch_facebook_json(FB_JSON_CONFIG, "x")
    assert calls["n"] == 2
    assert json.loads(body)["data"]["marketplace_search"]["feed_units"]["edges"]


def test_5xx_retries_and_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky_post(url, data, headers=None, timeout=25.0):
        calls["n"] += 1
        if calls["n"] == 1:
            raise fetch.FetchError("HTTP 503")
        return _graphql()

    monkeypatch.setattr(fetch, "_get", lambda url, headers=None, timeout=25.0: _doc())
    monkeypatch.setattr(fetch, "_post", flaky_post)
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


def test_missing_lsd_raises_fetch_error(monkeypatch):
    monkeypatch.setattr(
        fetch, "_get", lambda url, headers=None, timeout=25.0: "<html>no tokens here</html>"
    )
    with pytest.raises(fetch.FetchError, match="lsd"):
        api.fetch_facebook_json(FB_JSON_CONFIG, "x")


def test_unexpected_exception_normalized_to_fetch_error(monkeypatch):
    def boom(url, headers=None, timeout=25.0):
        raise RuntimeError("boom")

    monkeypatch.setattr(fetch, "_get", boom)
    with pytest.raises(fetch.FetchError, match="facebook_json: RuntimeError: boom"):
        api.fetch_facebook_json(FB_JSON_CONFIG, "x")


def test_search_site_falls_back_to_browser_tier_when_json_fails(monkeypatch):
    monkeypatch.setenv("FB_COOKIES", "c_user=1; xs=abc")
    monkeypatch.setattr(
        fetch, "_get", lambda url, headers=None, timeout=25.0: "<html>no tokens here</html>"
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


def test_graphql_rate_limit_soft_error_is_retried(monkeypatch):
    calls = {"n": 0}

    def throttled_post(url, data, headers=None, timeout=25.0):
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"errors":[{"message":"Rate limit exceeded"}],"data":null}'
        return _graphql()

    monkeypatch.setattr(fetch, "_get", lambda url, headers=None, timeout=25.0: _doc())
    monkeypatch.setattr(fetch, "_post", throttled_post)
    body, _page_url = api.fetch_facebook_json(FB_JSON_CONFIG, "x")
    assert calls["n"] == 2
    assert json.loads(body)["data"]["marketplace_search"]["feed_units"]["edges"]


def test_requests_are_paced_to_one_per_second(monkeypatch):
    slept = []
    monkeypatch.setattr(api, "_sleep", slept.append)
    monkeypatch.setattr(api, "_fb_last_request_at", 0.0)
    monkeypatch.setattr(fetch, "_get", lambda url, headers=None, timeout=25.0: _doc())
    monkeypatch.setattr(fetch, "_post", lambda url, data, headers=None, timeout=25.0: _graphql())
    api.fetch_facebook_json(FB_JSON_CONFIG, "x")  # GET then POST, back to back
    api.fetch_facebook_json(FB_JSON_CONFIG, "y")
    # first request is free; every later one waits out the 1s interval
    assert len(slept) == 3 and all(0 < s <= api._FB_MIN_INTERVAL_S for s in slept)
