"""product-finder sites: fetching and parsing marketplace listings.

Owns the built-in site registry, the single HTTP seam (fetch._get), and
the pure parsers that turn a search page into listing dicts. Never
touches the database and never scores (packages/core owns those).
Callers rely on: a site is pure data (slug/kind/config), parsers raise
nothing on weird markup — they return what they could parse — and
search_site() reports errors as values, not exceptions.
"""
