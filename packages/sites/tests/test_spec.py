"""Registry contract: 21 sites, unique slugs, complete css configs."""

from product_finder_sites.spec import BUILTIN_SITES


def test_twenty_one_sites():
    assert len(BUILTIN_SITES) == 21


def test_unique_slugs():
    slugs = [s["slug"] for s in BUILTIN_SITES]
    assert len(slugs) == len(set(slugs))


def test_css_configs_complete():
    for site in BUILTIN_SITES:
        assert "{query}" in site["config"]["url"], site["slug"]
        if site["kind"] == "css":
            for key in ("item", "title", "link"):
                assert site["config"].get(key), f"{site['slug']} missing {key}"
