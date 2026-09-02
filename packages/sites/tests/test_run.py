"""Strategy iteration against monkeypatched fetch seams: API -> css -> browser."""

from pathlib import Path

import pytest
from product_finder_sites import fetch, run
from product_finder_sites.spec import BUILTIN_SITES

FIXTURES = Path(__file__).parent / "fixtures"
SITES = {s["slug"]: s for s in BUILTIN_SITES}


@pytest.fixture(autouse=True)
def no_api_creds(monkeypatch):
    for var in (
        "EBAY_CLIENT_ID",
        "EBAY_CLIENT_SECRET",
        "BESTBUY_API_KEY",
        "WALMART_API_KEY",
        "KROGER_CLIENT_ID",
        "KROGER_CLIENT_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)


def test_api_unset_falls_through_to_browser(monkeypatch):
    monkeypatch.setattr(
        fetch,
        "_get_browser",
        lambda url, wait=None, timeout=30.0, cookies=None: (FIXTURES / "ebay.html").read_text(),
    )
    result = run.search_site(SITES["ebay"], "thinkpad x1 carbon")
    assert result["error"] is None
    assert result["strategy"] == "browser_css"
    assert result["attempts"] == [
        {"strategy": "ebay_api", "error": "EBAY_CLIENT_ID/EBAY_CLIENT_SECRET unset"}
    ]
    assert len(result["listings"]) == 2
    assert all(li["site_slug"] == "ebay" for li in result["listings"])


def test_api_unset_falls_through_to_css(monkeypatch):
    # bestbuy keeps a plain-HTML tier between its API and the browser
    monkeypatch.setattr(
        fetch, "_get", lambda url, headers=None, timeout=25.0: "<html>bot wall</html>"
    )
    result = run.search_site(SITES["bestbuy"], "x")
    kinds = [a["strategy"] for a in result["attempts"]]
    assert kinds == ["bestbuy_api", "css", "browser_css"]
    assert result["attempts"][0]["error"] == "BESTBUY_API_KEY unset"


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
    assert "api: EBAY_CLIENT_ID/EBAY_CLIENT_SECRET unset" in result["error"]
    assert "browser: browser fetching not wired" in result["error"]


def test_empty_page_falls_through_to_browser(monkeypatch):
    monkeypatch.setattr(
        fetch, "_get", lambda url, headers=None, timeout=25.0: "<html>bot wall</html>"
    )
    monkeypatch.setattr(
        fetch,
        "_get_browser",
        lambda url, wait=None, timeout=30.0, cookies=None: (
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
    assert "browser: browser fetching not wired" in result["error"]


def test_search_many_dedupes_and_reports_strategies(monkeypatch):
    monkeypatch.setattr(
        fetch,
        "_get_browser",
        lambda url, wait=None, timeout=30.0, cookies=None: (FIXTURES / "ebay.html").read_text(),
    )
    out = run.search_many([SITES["ebay"]], ["query one", "query two"])
    assert len(out["listings"]) == 2  # same fixture twice -> deduped by url
    assert out["errors"] == {}
    assert out["strategies"] == {"ebay": "browser_css"}


def test_facebook_login_wall_error_is_clear(monkeypatch):
    monkeypatch.delenv("FB_COOKIES", raising=False)
    monkeypatch.setattr(
        fetch,
        "_get_browser",
        lambda url, wait=None, timeout=30.0, cookies=None: (
            FIXTURES / "facebook_login.html"
        ).read_text(),
    )
    result = run.search_site(SITES["facebook-marketplace"], "x1 carbon")
    assert result["strategy"] is None and result["listings"] == []
    assert "login wall — set FB_COOKIES" in result["error"]


def test_facebook_cookies_env_reaches_browser_seam(monkeypatch):
    seen = {}

    def fake_browser(url, wait=None, timeout=30.0, cookies=None):
        seen["url"], seen["cookies"] = url, cookies
        return (FIXTURES / "facebook.html").read_text()

    monkeypatch.setattr(fetch, "_get_browser", fake_browser)
    monkeypatch.setenv("FB_COOKIES", "c_user=1; xs=abc")
    result = run.search_site(SITES["facebook-marketplace"], "thinkpad")
    assert result["error"] is None and result["strategy"] == "facebook_marketplace"
    assert seen["cookies"] == "c_user=1; xs=abc"
    assert "/marketplace/durham/search" in seen["url"] and "radius=80" in seen["url"]
    assert len(result["listings"]) == 3


def test_local_filter_drops_unrelated_feed_items(monkeypatch):
    woot = SITES["woot"]
    monkeypatch.setattr(
        fetch,
        "_get_browser",
        lambda url, wait=None, timeout=30.0, cookies=None: (FIXTURES / "woot.html").read_text(),
    )
    result = run.search_site(woot, "ThinkPad X1 Carbon Gen 6")
    assert result["error"] is None and result["strategy"] == "browser_css"
    assert [li["title"] for li in result["listings"]] == [
        "$ 349 99 Lenovo ThinkPad X1 Carbon G8 16GB Refurb"
    ]
    result = run.search_site(woot, "espresso machine")
    assert result["error"] == "browser: 0 of 3 feed items match query"


def test_challenge_page_labeled(monkeypatch):
    monkeypatch.setattr(
        fetch,
        "_get",
        lambda url, headers=None, timeout=25.0: "<html><title>Robot or human?</title></html>",
    )
    monkeypatch.setattr(
        fetch,
        "_get_browser",
        lambda url, wait=None, timeout=30.0, cookies=None: "<html>Just a moment...</html>",
    )
    result = run.search_site(SITES["target"], "x")
    assert result["error"] == "css: challenge page; browser: challenge page"


def test_goodwill_api_tier_is_keyless(monkeypatch):
    posted = {}

    def fake_post(url, data, headers=None, timeout=25.0):
        posted["url"] = url
        posted["data"] = data
        return (FIXTURES / "goodwill_api.json").read_text()

    monkeypatch.setattr(fetch, "_post", fake_post)
    result = run.search_site(SITES["shopgoodwill"], "x1 carbon")
    assert result["strategy"] == "goodwill_api"
    assert len(result["listings"]) == 2
    assert "x1 carbon" in posted["data"]
    assert posted["url"].startswith("https://buyerapi.shopgoodwill.com")


def test_search_many_keeps_error_only_without_any_success(monkeypatch):
    calls = {"n": 0}

    def flaky(url, headers=None, timeout=25.0):
        calls["n"] += 1
        if calls["n"] == 1:
            raise fetch.FetchError("HTTP 403")
        return (FIXTURES / "ebay.html").read_text()

    monkeypatch.setattr(fetch, "_get", flaky)
    site = {
        "slug": "newegg2",
        "name": "n",
        "kind": "css",
        "config": SITES["newegg"]["config"]
        | {
            "item": "li.s-item",
            "title": ".s-item__title",
            "price": ".s-item__price",
            "link": "a.s-item__link",
        },
    }
    out = run.search_many([site], ["q1", "q2"])
    assert out["errors"] == {}  # q2 succeeded; q1's 403 is not reported
    assert out["strategies"] == {"newegg2": "css"}


def test_kroger_creds_unset_falls_through_to_css(monkeypatch):
    monkeypatch.setattr(
        fetch,
        "_get_browser",
        lambda url, wait=None, timeout=30.0, cookies=None: "<html>no cards here</html>",
    )
    result = run.search_site(SITES["harris-teeter"], "eggs")
    assert result["attempts"][0] == {
        "strategy": "kroger_api",
        "error": "KROGER_CLIENT_ID/KROGER_CLIENT_SECRET unset",
    }
    # css and browser_css both attempted next, against harris-teeter's
    # guessed (unverified) selectors, which the fixture-free bodies here
    # never match — that's fine, this test only checks tier order/labels.
    kinds = [a["strategy"] for a in result["attempts"]]
    assert kinds == ["kroger_api", "css", "browser_css"]


def test_kroger_api_tier_runs_when_creds_set(monkeypatch):
    monkeypatch.setenv("KROGER_CLIENT_ID", "cid")
    monkeypatch.setenv("KROGER_CLIENT_SECRET", "secret")
    calls = []

    def fake_post(url, data, headers=None, timeout=25.0):
        calls.append(("post", url))
        assert url == "https://api.kroger.com/v1/connect/oauth2/token"
        assert headers["Authorization"].startswith("Basic ")
        assert "scope=product.compact" in data
        return '{"access_token": "tok"}'

    def fake_get(url, headers=None, timeout=25.0):
        calls.append(("get", url))
        assert headers["Authorization"] == "Bearer tok"
        assert "locations" not in url  # builtin pins location_id: no lookup call
        assert "filter.locationId=09700394" in url and "filter.term=eggs" in url
        return (FIXTURES / "kroger_api.json").read_text()

    monkeypatch.setattr(fetch, "_post", fake_post)
    monkeypatch.setattr(fetch, "_get", fake_get)
    result = run.search_site(SITES["harris-teeter"], "eggs")
    assert result["error"] is None
    assert result["strategy"] == "kroger_api"
    assert [c[0] for c in calls] == ["post", "get"]
    assert result["listings"][0]["price"] == 3.49
    assert (
        result["listings"][0]["location"] == "Harris Teeter, 2107 Hillsborough Rd, Durham, NC 27705"
    )
    assert result["listings"][0]["condition"] == "new"


def _unpinned_harris_teeter():
    """Builtin spec minus the pinned store, so the zip lookup path runs."""
    import copy

    site = copy.deepcopy(SITES["harris-teeter"])
    for strat in site["config"]["strategies"]:
        strat.get("config", {}).pop("location_id", None)
    site["config"].pop("location_id", None)
    return site


def test_kroger_zip_lookup_skips_fuel_centers(monkeypatch):
    monkeypatch.setenv("KROGER_CLIENT_ID", "cid")
    monkeypatch.setenv("KROGER_CLIENT_SECRET", "secret")
    monkeypatch.setattr(
        fetch, "_post", lambda url, data, headers=None, timeout=25.0: '{"access_token": "tok"}'
    )

    def fake_get(url, headers=None, timeout=25.0):
        if "locations" in url:
            assert "filter.zipCode.near=27705" in url and "filter.chain=HART" in url
            return (
                '{"data": [{"locationId": "09700024", "name": "Harris Teeter Fuel - Hope Valley"},'
                ' {"locationId": "09700394", "name": "Harris Teeter - Shops at Erwin Mill"}]}'
            )
        assert "filter.locationId=09700394" in url
        return (FIXTURES / "kroger_api.json").read_text()

    monkeypatch.setattr(fetch, "_get", fake_get)
    result = run.search_site(_unpinned_harris_teeter(), "eggs")
    assert result["error"] is None and result["strategy"] == "kroger_api"


def test_kroger_no_location_near_zip_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("KROGER_CLIENT_ID", "cid")
    monkeypatch.setenv("KROGER_CLIENT_SECRET", "secret")
    monkeypatch.setattr(
        fetch, "_post", lambda url, data, headers=None, timeout=25.0: '{"access_token": "tok"}'
    )
    monkeypatch.setattr(fetch, "_get", lambda url, headers=None, timeout=25.0: '{"data": []}')
    monkeypatch.setattr(
        fetch, "_get_browser", lambda url, wait=None, timeout=30.0, cookies=None: ""
    )
    result = run.search_site(_unpinned_harris_teeter(), "eggs")
    assert result["attempts"][0]["error"] == "kroger: no HART location near 27705"


def test_food_lion_browser_wall_is_labeled_challenge_page(monkeypatch):
    monkeypatch.setattr(fetch, "_get", lambda url, headers=None, timeout=25.0: "<html>403</html>")
    monkeypatch.setattr(
        fetch,
        "_get_browser",
        lambda url, wait=None, timeout=30.0, cookies=None: (
            FIXTURES / "foodlion_walled.html"
        ).read_text(),
    )
    result = run.search_site(SITES["food-lion"], "eggs")
    assert result["strategy"] is None and result["listings"] == []
    assert "browser: challenge page" in result["error"]
