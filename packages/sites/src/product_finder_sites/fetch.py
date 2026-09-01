"""The one HTTP seam: everything network goes through _get.

Owns request headers, timeouts, and error normalization. Never parses.
Tests monkeypatch _get with fixture files; nothing else in this package
may import urllib. Callers rely on _get returning decoded text or
raising FetchError with a short human-readable reason.
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
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return body.decode(resp.headers.get_content_charset() or "utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise FetchError(str(getattr(e, "reason", e))) from e
