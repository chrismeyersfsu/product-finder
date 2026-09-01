"""Chromium page fetching behind the sites fetch seam.

Owns browser lifecycle (launch, navigate, wait, teardown) and error
normalization to FetchError. Never parses HTML and never selects
sites. Callers rely on: get_page(url, wait_selector, timeout) returns
the rendered page's HTML; wire() is idempotent and is the only
supported way to enable the browser_css strategy tier. CI never
launches a real browser — tests monkeypatch _render.
"""

from product_finder_sites import fetch


def _render(url: str, wait_selector: str | None, timeout: float) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=fetch.UA)
            page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
            if wait_selector:
                page.wait_for_selector(wait_selector, timeout=10_000)
            return page.content()
        finally:
            browser.close()


def get_page(url: str, wait_selector: str | None = None, timeout: float = 30.0) -> str:
    try:
        return _render(url, wait_selector, timeout)
    except fetch.FetchError:
        raise
    except Exception as e:  # normalize playwright errors to the seam's contract
        raise fetch.FetchError(f"browser: {e.__class__.__name__}: {e}") from e


def wire() -> None:
    """Plug this driver into the sites package's browser seam."""
    fetch._get_browser = get_page
