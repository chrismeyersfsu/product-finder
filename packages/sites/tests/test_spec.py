"""Registry contract: 21 sites, unique slugs, ordered strategies, complete configs."""

from product_finder_sites.spec import BUILTIN_SITES, JS_HEAVY

SITES = {s["slug"]: s for s in BUILTIN_SITES}


def _strategies(site):
    if site["kind"] == "tiered":
        return site["config"]["strategies"]
    return [{"kind": site["kind"], "config": site["config"]}]


def test_twenty_one_sites_unique_slugs():
    slugs = [s["slug"] for s in BUILTIN_SITES]
    assert len(slugs) == 21 and len(set(slugs)) == 21


def test_css_configs_complete():
    for site in BUILTIN_SITES:
        for strat in _strategies(site):
            if strat["kind"] in ("css", "browser_css", "reddit_json"):
                assert "{query}" in strat["config"]["url"], site["slug"]
            if strat["kind"] in ("css", "browser_css"):
                for key in ("item", "title", "link"):
                    assert strat["config"].get(key), f"{site['slug']} missing {key}"


def test_api_first_ordering():
    for slug, api_kind in (
        ("ebay", "ebay_api"),
        ("bestbuy", "bestbuy_api"),
        ("walmart", "walmart_api"),
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
        assert kinds.index("css") < kinds.index("browser_css")


def test_reddit_is_api_only():
    assert SITES["reddit-hardwareswap"]["kind"] == "reddit_json"
