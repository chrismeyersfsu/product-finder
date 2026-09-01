"""Chromium page fetching behind the sites fetch seam.

Owns browser lifecycle (launch, navigate, scroll-nudge, wait, teardown), cookie
injection, and error normalization to FetchError. Never parses HTML
and never selects sites. Callers rely on: get_page(url, wait_selector,
timeout, cookies) returns the rendered page's HTML; wait_selector is
best-effort (a wall page that never shows it still returns its
content, so parsers — not timeouts — decide what the page was);
cookies is an optional "k=v; k2=v2" header string injected into the
browser context for the page's registrable domain and never persisted
anywhere; wire() is idempotent and is the only supported way to enable
the browser strategy tiers. CI never launches a real browser — tests
monkeypatch _render.
"""

from urllib.parse import urlparse

from product_finder_sites import fetch


def _parse_cookie_header(header: str, domain: str) -> list[dict]:
    """Cookie header string -> playwright cookie dicts for `domain`."""
    out = []
    for part in header.split(";"):
        name, _, value = part.strip().partition("=")
        if name and value:
            out.append(
                {"name": name.strip(), "value": value.strip(), "domain": domain, "path": "/"}
            )
    return out


def _render(url: str, wait_selector: str | None, timeout: float, cookies: str | None = None) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(user_agent=fetch.UA)
            if cookies:
                host = urlparse(url).hostname or ""
                context.add_cookies(_parse_cookie_header(cookies, "." + host.removeprefix("www.")))
            page = context.new_page()
            page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
            try:  # nudge lazy-loading result grids into hydrating
                page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
                page.wait_for_timeout(1500)
            except Exception:
                pass
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=10_000)
                except Exception:
                    pass  # best-effort: return whatever rendered
            return page.content()
        finally:
            browser.close()


def get_page(
    url: str, wait_selector: str | None = None, timeout: float = 45.0, cookies: str | None = None
) -> str:
    try:
        return _render(url, wait_selector, timeout, cookies)
    except fetch.FetchError:
        raise
    except Exception as e:  # normalize playwright errors to the seam's contract
        raise fetch.FetchError(f"browser: {e.__class__.__name__}: {e}") from e


def wire() -> None:
    """Plug this driver into the sites package's browser seam."""
    fetch._get_browser = get_page
