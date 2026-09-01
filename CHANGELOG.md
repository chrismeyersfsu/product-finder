# Changelog

## 0.1.0 — 2026-08-31

- Generic product-finder: products are data (queries + regex
  extractors + weighted criteria), scored listings stored in SQLite.
- 21 built-in marketplace adapters (eBay, Craigslist, Newegg, B&H,
  r/hardwareswap, ...) behind one HTTP seam with pure parsers.
- MCP server (`product-finder-mcp`): product/site management,
  `run_search`, `query_listings`, `best_deals`, and project
  self-modification tools (`project_read_file` / `project_write_file` /
  `project_run_ci`) scoped to the repo root.
- Container: Dockerfile + docker-compose running streamable-http on
  :8848 with the db on a volume.
- Seed product: thin-client laptop (ThinkPad X1 Carbon Gen 6+, 16GB,
  NVMe, FHD IPS, Gen 6+ weight, seller-quality rules) with manual
  checks for battery health, keyboard wear, screen defects, and ports.
