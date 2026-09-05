"""Discogs (keyless api.discogs.com). discogs_search.json is a REAL
trimmed capture of GET /database/search?q=beck+odelay&format=vinyl
(2026-09-04) — thumb/cover_image come back empty on every row from
this network unauthenticated, so the 235913 row's thumb is a synthetic
non-empty value to exercise the image_url path. discogs_release_*.json
are REAL trimmed captures of GET /releases/{id} for two of that
search's original (non-reissue) pressings. discogs_api.json is
fetch_discogs_api's own combined output (not a raw Discogs shape) for
those same two real releases, plus two hand-authored synthetic entries
covering a zero-for-sale pressing (dropped) and a for-sale pressing
with no price set and a "(2)"-disambiguated artist name.
"""

import time
from pathlib import Path

from product_finder_sites import api, fetch, parse, run
from product_finder_sites.spec import BUILTIN_SITES

FIXTURES = Path(__file__).parent / "fixtures"
DISCOGS = next(s for s in BUILTIN_SITES if s["slug"] == "discogs")


def _fake_get(search_fixture="discogs_search.json", releases=None):
    releases = releases or {}
    calls = []

    def fake_get(url, headers=None, timeout=25.0):
        calls.append(url)
        if "database/search" in url:
            return (FIXTURES / search_fixture).read_text()
        for id_str, fixture in releases.items():
            if f"/releases/{id_str}" in url:
                return (FIXTURES / fixture).read_text()
        raise AssertionError(f"unexpected url: {url}")

    return fake_get, calls


# ---- fetcher ----------------------------------------------------------


def test_fetch_discogs_searches_then_looks_up_originals_only(monkeypatch):
    fake_get, calls = _fake_get(
        releases={
            "235913": "discogs_release_235913.json",
            "22884872": "discogs_release_22884872.json",
        }
    )
    monkeypatch.setattr(fetch, "_get", fake_get)
    monkeypatch.setattr(time, "sleep", lambda *_: None)

    body, url = api.fetch_discogs_api({}, "beck odelay")

    assert url == "https://api.discogs.com/database/search?q=beck+odelay&format=vinyl&per_page=25"
    # Only the two non-reissue/non-unofficial rows get a release lookup.
    assert len(calls) == 3  # search + 2 release lookups
    assert calls[1] == "https://api.discogs.com/releases/235913?curr_abbr=USD"
    assert calls[2] == "https://api.discogs.com/releases/22884872?curr_abbr=USD"
    import json

    payload = json.loads(body)
    assert payload["query"] == "beck odelay"
    ids = [r["id"] for r in payload["releases"]]
    assert ids == [235913, 22884872]
    assert payload["releases"][0]["release"]["num_for_sale"] == 65


def test_fetch_discogs_strips_trailing_noise_words(monkeypatch):
    fake_get, _calls = _fake_get()
    monkeypatch.setattr(fetch, "_get", fake_get)
    monkeypatch.setattr(time, "sleep", lambda *_: None)

    _, url = api.fetch_discogs_api({"max_releases": 0}, "beck odelay vinyl lp record")
    assert "q=beck+odelay" in url
    assert "vinyl+lp" not in url


def test_fetch_discogs_respects_max_releases(monkeypatch):
    fake_get, calls = _fake_get(
        releases={
            "235913": "discogs_release_235913.json",
            "22884872": "discogs_release_22884872.json",
        }
    )
    monkeypatch.setattr(fetch, "_get", fake_get)
    monkeypatch.setattr(time, "sleep", lambda *_: None)

    body, _ = api.fetch_discogs_api({"max_releases": 1}, "beck odelay")
    assert len(calls) == 2  # search + exactly one release lookup
    import json

    assert len(json.loads(body)["releases"]) == 1


def test_fetch_discogs_skip_reissues_false_keeps_everything(monkeypatch):
    # Every id in the fixture resolves to the same release body here --
    # this test only checks which ids get looked up, not their content.
    def fake_get(url, headers=None, timeout=25.0):
        if "database/search" in url:
            return (FIXTURES / "discogs_search.json").read_text()
        return (FIXTURES / "discogs_release_235913.json").read_text()

    monkeypatch.setattr(fetch, "_get", fake_get)
    monkeypatch.setattr(time, "sleep", lambda *_: None)

    body, _ = api.fetch_discogs_api({"skip_reissues": False, "max_releases": 8}, "beck odelay")
    import json

    payload = json.loads(body)
    assert len(payload["releases"]) == 6  # all six search fixture rows, reissues included


def test_fetch_discogs_paces_release_lookups(monkeypatch):
    fake_get, _ = _fake_get(
        releases={
            "235913": "discogs_release_235913.json",
            "22884872": "discogs_release_22884872.json",
        }
    )
    monkeypatch.setattr(fetch, "_get", fake_get)
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

    api.fetch_discogs_api({}, "beck odelay")
    # search -> release1 gap, release1 -> release2 gap: at least 2 sleeps,
    # every one honoring the >= 0.3s floor.
    assert len(sleeps) >= 2
    assert all(s >= 0.3 for s in sleeps)


def test_fetch_discogs_429_is_a_clear_rate_limit_error(monkeypatch):
    def fake_get(url, headers=None, timeout=25.0):
        raise fetch.FetchError("HTTP 429")

    monkeypatch.setattr(fetch, "_get", fake_get)
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    try:
        api.fetch_discogs_api({}, "beck odelay")
        raise AssertionError("expected FetchError")
    except fetch.FetchError as e:
        assert str(e) == "discogs: rate limited"


def test_fetch_discogs_sends_descriptive_user_agent(monkeypatch):
    seen_headers = {}

    def fake_get(url, headers=None, timeout=25.0):
        if "database/search" in url:
            seen_headers.update(headers or {})
            return (FIXTURES / "discogs_search.json").read_text()
        return (FIXTURES / "discogs_release_235913.json").read_text()

    monkeypatch.setattr(fetch, "_get", fake_get)
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    api.fetch_discogs_api({"max_releases": 0}, "beck odelay")
    assert seen_headers["User-Agent"] == (
        "product-finder/0.1 +https://github.com/chrismeyersfsu/product-finder"
    )


def test_search_site_runs_discogs_end_to_end(monkeypatch):
    fake_get, _ = _fake_get(
        releases={
            "235913": "discogs_release_235913.json",
            "22884872": "discogs_release_22884872.json",
        }
    )
    monkeypatch.setattr(fetch, "_get", fake_get)
    monkeypatch.setattr(time, "sleep", lambda *_: None)

    result = run.search_site(DISCOGS, "beck odelay")
    assert result["error"] is None
    assert result["strategy"] == "discogs_api"
    assert len(result["listings"]) == 2
    assert all(li["site_slug"] == "discogs" for li in result["listings"])


# ---- parser -------------------------------------------------------------


def test_discogs_rows_become_listings():
    body = (FIXTURES / "discogs_api.json").read_text()
    out = parse.parse_listings(DISCOGS, "https://api.discogs.com/database/search", body)

    assert len(out) == 3  # the zero-for-sale synthetic row is dropped

    original = out[0]
    expected_title = "Beck! – Odelay (1996, Bong Load Custom Records, US) — 65 for sale"  # noqa: RUF001
    assert original["title"] == expected_title
    assert original["price"] == 0.41
    assert original["url"] == "https://www.discogs.com/sell/release/235913"
    assert original["location"] is None
    assert original["condition"] == "used"
    assert original["image_url"] == "https://i.discogs.com/fake-thumb-235913.jpeg"
    assert original["year"] == 1996

    second = out[1]
    expected_title = "Beck! – Odelay (1996, Bong Load Custom Records, US) — 1 for sale"  # noqa: RUF001
    assert second["title"] == expected_title
    assert second["price"] == 84.55
    assert second["image_url"] is None  # both thumb and cover_image are empty


def test_discogs_disambiguated_artist_and_missing_price():
    body = (FIXTURES / "discogs_api.json").read_text()
    out = parse.parse_listings(DISCOGS, "https://api.discogs.com/database/search", body)

    row = out[2]
    assert row["title"] == "Beck – Odelay (2005, Geffen Records, Japan) — 2 for sale"  # noqa: RUF001
    assert row["price"] is None  # lowest_price is 0 -- unset, not a real free copy
    assert row["url"] == "https://www.discogs.com/sell/release/88888888"
    assert row["image_url"] == "https://i.discogs.com/fake-cover-88888888.jpeg"
    assert row["year"] == 2005


def test_discogs_zero_for_sale_dropped():
    body = (FIXTURES / "discogs_api.json").read_text()
    out = parse.parse_listings(DISCOGS, "https://api.discogs.com/database/search", body)
    assert not any(li["url"].endswith("/99999999") for li in out)


def test_discogs_malformed_body_returns_no_listings():
    assert parse.parse_listings(DISCOGS, "https://api.discogs.com", "not json") == []
