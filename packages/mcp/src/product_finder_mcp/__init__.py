"""product-finder mcp: the MCP server surface.

Owns tool definitions, transport wiring, and the search pipeline glue
(fetch -> extract -> score -> store). Never parses HTML itself and
never defines schema — packages/sites and packages/core own those.
Callers rely on: every tool returns JSON-serializable dicts, errors are
returned as {"error": ...} values, and project_* tools never touch
paths outside the project root.
"""
