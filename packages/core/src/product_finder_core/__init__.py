"""product-finder core: generic product-search domain.

Owns the product/criteria data model, SQLite persistence, and scoring.
Never fetches from the network (packages/sites owns that) and never
speaks MCP (packages/mcp owns that). Callers rely on products being
fully data-driven: a product is a slug + search queries + regex
extractors + weighted criteria rules, so new products need no code.
"""
