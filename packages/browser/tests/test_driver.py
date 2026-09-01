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
    monkeypatch.setattr(driver, "_render", lambda url, wait, timeout: f"<html>{url}</html>")
    driver.wire()
    assert fetch._get_browser("https://x") == "<html>https://x</html>"


def test_playwright_errors_normalize_to_fetcherror(monkeypatch):
    def boom(url, wait, timeout):
        raise RuntimeError("chromium crashed")

    monkeypatch.setattr(driver, "_render", boom)
    with pytest.raises(fetch.FetchError, match="browser: RuntimeError: chromium crashed"):
        driver.get_page("https://x")
