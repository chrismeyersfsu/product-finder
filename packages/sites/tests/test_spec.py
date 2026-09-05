"""Registry contract: 29 sites, unique slugs, ordered strategies, complete configs."""

from product_finder_sites.spec import BUILTIN_SITES, JS_HEAVY, NO_PLAIN_HTML

SITES = {s["slug"]: s for s in BUILTIN_SITES}


def _strategies(site):
    if site["kind"] == "tiered":
        return site["config"]["strategies"]
    return [{"kind": site["kind"], "config": site["config"]}]


def test_twenty_four_sites_unique_slugs():
    slugs = [s["slug"] for s in BUILTIN_SITES]
    assert len(slugs) == 29 and len(set(slugs)) == 29


def test_facebook_marketplace_spec():
    fb = SITES["facebook-marketplace"]
    assert fb["kind"] == "tiered"
    kinds = [s["kind"] for s in _strategies(fb)]
    assert kinds == ["facebook_json", "facebook_marketplace"]
    fb_json = next(s for s in _strategies(fb) if s["kind"] == "facebook_json")
    browser = next(s for s in _strategies(fb) if s["kind"] == "facebook_marketplace")
    for strat in (fb_json, browser):
        assert strat["config"]["region"] == "durham"
        assert strat["config"]["radius_km"] == 80
        assert "{region}" in strat["config"]["url"] and "{query}" in strat["config"]["url"]
    assert browser["config"]["cookies_env"] == "FB_COOKIES"
    assert "cookies_env" not in fb_json["config"]  # facebook_json is always anonymous


def test_css_configs_complete():
    for site in BUILTIN_SITES:
        for strat in _strategies(site):
            if strat["kind"] in ("css", "browser_css", "reddit_json"):
                # category feeds (local_filter) have a fixed url instead
                if not strat["config"].get("local_filter"):
                    assert "{query}" in strat["config"]["url"], site["slug"]
            if strat["kind"] in ("css", "browser_css"):
                for key in ("item", "title", "link"):
                    assert strat["config"].get(key), f"{site['slug']} missing {key}"


def test_api_first_ordering():
    for slug, api_kind in (
        ("ebay", "ebay_api"),
        ("bestbuy", "bestbuy_api"),
        ("walmart", "walmart_api"),
        ("harris-teeter", "kroger_api"),
    ):
        kinds = [s["kind"] for s in _strategies(SITES[slug])]
        assert kinds[0] == api_kind, slug


def test_ebay_never_uses_plain_html():
    # eBay hard-blocks plain HTTP (403 on /sch/i.html): API then browser only.
    assert [s["kind"] for s in _strategies(SITES["ebay"])] == ["ebay_api", "browser_css"]


def test_js_heavy_sites_have_browser_fallback_last():
    for slug in JS_HEAVY:
        kinds = [s["kind"] for s in _strategies(SITES[slug])]
        assert kinds[-1] == "browser_css", slug
        if slug not in NO_PLAIN_HTML:
            assert kinds.index("css") < kinds.index("browser_css")


def test_reddit_is_api_only():
    assert SITES["reddit-hardwareswap"]["kind"] == "reddit_json"


def test_harris_teeter_kroger_config():
    ht = SITES["harris-teeter"]
    kroger = next(s for s in _strategies(ht) if s["kind"] == "kroger_api")
    assert kroger["config"]["zip"] == "27705"
    assert kroger["config"]["chain"] == "HART"
    assert kroger["config"]["location_id"] == "09700394"
    assert kroger["config"]["condition"] == "new"
    assert "Durham, NC" in kroger["config"]["location"]


def test_grocery_sites_carry_static_location_and_condition():
    for slug in ("harris-teeter", "food-lion"):
        for strat in _strategies(SITES[slug]):
            if strat["kind"] in ("css", "browser_css"):
                assert strat["config"]["condition"] == "new", slug
                assert "27705" in strat["config"]["location"], slug


def test_discogs_spec():
    d = SITES["discogs"]
    assert d["kind"] == "discogs_api"
    assert d["config"] == {
        "format": "vinyl",
        "max_releases": 8,
        "currency": "USD",
        "skip_reissues": True,
    }
