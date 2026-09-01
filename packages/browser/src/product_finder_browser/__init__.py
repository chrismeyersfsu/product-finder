"""product-finder browser: the browser-driven fetch tier.

Owns Playwright and nothing else — this package exists so heavy
browser deps never leak into packages/sites or plain-MCP installs.
Never parses (parsers stay in packages/sites) and never runs unless
wired. Callers rely on: wire() plugs driver.get_page into
product_finder_sites.fetch._get_browser, and get_page raises the
sites package's FetchError on any browser failure so browser-tier
errors stay per-site values.
"""

from .driver import wire

__all__ = ["wire"]
