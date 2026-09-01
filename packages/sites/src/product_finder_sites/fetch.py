"""The one I/O seam: everything network or browser goes through here.

Owns request headers, timeouts, error normalization, and the browser
hook. Never parses. Tests monkeypatch _get/_post/_get_browser with
fixture data; nothing else in this package may import urllib.
Callers rely on: all three seams return decoded text or raise
FetchError with a short human-readable reason. _get_browser is a hook:
it raises FetchError until a browser driver wires it (see
product-finder-browser's wire()), so browser-tier strategies degrade
to a per-site error value instead of an import crash.
"""

import gzip
import urllib.error
import urllib.request

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class FetchError(Exception):
    pass


def _read(resp) -> str:
    body = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip":
        body = gzip.decompress(body)
    return body.decode(resp.headers.get_content_charset() or "utf-8", "replace")


def _request(req, timeout: float) -> str:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _read(resp)
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise FetchError(str(getattr(e, "reason", e))) from e


def _get(url: str, headers: dict | None = None, timeout: float = 25.0) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            **(headers or {}),
        },
    )
    return _request(req, timeout)


def _post(url: str, data: str, headers: dict | None = None, timeout: float = 25.0) -> str:
    req = urllib.request.Request(
        url,
        data=data.encode(),
        method="POST",
        headers={
            "User-Agent": UA,
            "Content-Type": "application/x-www-form-urlencoded",
            **(headers or {}),
        },
    )
    return _request(req, timeout)


def _browser_unwired(
    url: str, wait_selector: str | None = None, timeout: float = 30.0, cookies: str | None = None
) -> str:
    raise FetchError(
        "browser fetching not wired: install product-finder-browser "
        "(mcp extra 'browser') and call its wire()"
    )


# Hook: product_finder_browser.wire() replaces this with a real driver.
_get_browser = _browser_unwired
