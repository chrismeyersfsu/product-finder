"""Contract tests without a real browser: wiring and error normalization."""

import pytest
from product_finder_browser import driver
from product_finder_sites import fetch


@pytest.fixture(autouse=True)
def unwire():
    yield
    fetch._get_browser = fetch._browser_unwired


def test_unwired_seam_raises_fetcherror():
    with pytest.raises(fetch.FetchError, match="not wired"):
        fetch._get_browser("https://x")


def test_wire_plugs_driver_into_seam(monkeypatch):
    monkeypatch.setattr(
        driver, "_render", lambda url, wait, timeout, cookies=None: f"<html>{url}</html>"
    )
    driver.wire()
    assert fetch._get_browser("https://x") == "<html>https://x</html>"


def test_playwright_errors_normalize_to_fetcherror(monkeypatch):
    def boom(url, wait, timeout, cookies=None):
        raise RuntimeError("chromium crashed")

    monkeypatch.setattr(driver, "_render", boom)
    with pytest.raises(fetch.FetchError, match="browser: RuntimeError: chromium crashed"):
        driver.get_page("https://x")


def test_cookie_header_parses_to_playwright_cookies():
    cookies = driver._parse_cookie_header("c_user=123; xs=a=b; ; junk", ".facebook.com")
    assert {"name": "c_user", "value": "123", "domain": ".facebook.com", "path": "/"} in cookies
    assert {"name": "xs", "value": "a=b", "domain": ".facebook.com", "path": "/"} in cookies
    assert all(c["name"] != "junk" for c in cookies)


def test_render_waits_for_attached_not_visible(monkeypatch):
    """The parsers read the DOM, so the wait must fire for elements that
    exist but never become visible (a <script> marker) — playwright's
    default state is "visible", which made those waits always time out."""
    import sys
    import types

    calls = {}

    class _Page:
        def goto(self, url, timeout, wait_until):
            calls["goto"] = (url, wait_until)

        def evaluate(self, js):
            pass

        def wait_for_timeout(self, ms):
            pass

        def wait_for_selector(self, selector, timeout, state=None):
            calls["wait"] = (selector, state)

        def content(self):
            return "<html>rendered</html>"

    class _Context:
        def add_cookies(self, cookies):
            calls["cookies"] = cookies

        def new_page(self):
            return _Page()

    class _Browser:
        def new_context(self, user_agent):
            return _Context()

        def close(self):
            calls["closed"] = True

    class _Chromium:
        def launch(self, headless):
            return _Browser()

    class _PW:
        chromium = _Chromium()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    fake = types.ModuleType("playwright.sync_api")
    fake.sync_playwright = lambda: _PW()
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake)

    html = driver._render("https://www.carvana.com/cars/x", "script[data-testid=vehicle-ld]", 45.0)

    assert html == "<html>rendered</html>"
    assert calls["wait"] == ("script[data-testid=vehicle-ld]", "attached")
    assert calls["closed"] is True
