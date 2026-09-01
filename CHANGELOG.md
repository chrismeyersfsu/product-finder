# Changelog

## 0.10.0 — 2026-09-01

- Products carry an optional `sites` list (`add_product(sites=[...])`);
  `run_search` and the hourly scrape search only those sites, so local
  goods like cars aren't queried against Newegg. Empty keeps the old
  every-enabled-site behaviour; existing databases migrate in place.
- Four products added via `add_product` (no code): Honda Fit, Toyota
  Prius, Tesla Model Y, Guardian 24" kids bike — Craigslist + Facebook
  Marketplace only, with model-name, parts/accessory, salvage-title,
  price-floor, and balance-bike reject rules. Definitions kept in
  `docs/add_products_example.py`.

## 0.9.0 — 2026-09-01

- Scraper repair from live pages: Office Depot (new /a/search URL +
  od-product-card selectors), Swappa (cell_product), Target (browser
  tier, aria-label titles), Best Buy (browser tier,
  product-list-item), Back Market (card-attribute titles/prices),
  Woot (search is gone — computers-feed scrape + local keyword
  filter), ShopGoodwill (new keyless goodwill_api buyer-API tier).
- Selector configs learned attribute sources (title_attr/price_attr),
  "&" self-selectors, per-site headers, split "$ 27 99" prices, and
  browser waits; the browser driver scroll-nudges lazy grids.
- Per-site errors now label every tier compactly (api/css/browser/
  json), call bot walls "challenge page", and a site that succeeded
  for any query no longer reports a stale error.
- Still walled from this network: amazon, adorama, microcenter,
  mercari, backmarket (intermittent), offerup (geo-empty), govdeals,
  staples, reddit, and ebay/walmart without API keys.

## 0.8.2

- Laptop seed: under-16GB RAM listings are now rejected at ingest, not
  stored flagged; /sites now parses run results correctly (it read a
  demo-data shape and showed every site as never searched) and a site
  that produced listings reads working even when another query errored.

## 0.8.1

- Criteria rules gained a `reject` flag: a violated reject rule discards
  the row at ingest (never stored in listings or price_history) instead
  of storing it flagged. The laptop seed marks parts/accessory and
  spec-less-title rules as reject; existing junk rows are purged.

## 0.8.0

- UI: sortable table headers on the deals table, history table view,
  sites view, and backtests list — click or Enter/Space toggles
  asc/desc with an arrow + `aria-sort` indicator; numeric columns sort
  numerically with blanks last; one shared snippet in the layout.
- UI: multi-select site filter on Deals and History (checkbox dropdown
  with "All sites" reset and count badge). Empty selection = all
  sites; selection round-trips through a comma-separated `sites`
  query param so filtered views are shareable. Entity colors are
  unaffected by filtering.

## 0.7.0

- Hourly scheduled scrape: `product-finder-scrape.timer` runs a oneshot
  container over every product (`product-finder-scrape` entrypoint);
  per-site summary in the journal, non-zero exit only on total failure.
- SQLite `busy_timeout=10000` so the scrape oneshot and the MCP service
  can share the database.

## 0.6.2

- Laptop seed: parts/accessory/broken listings (motherboards, screens,
  palmrests, chargers, "for parts") now hard-fail via a required
  `is_parts` rule; broad "ThinkPad X1 Carbon" query added to the seed.

## 0.6.1

- Fixed: install.sh now pins the DNS route to the product-finder tunnel
  (explicit --config + UUID) and fails loudly when the record targets a
  different tunnel, instead of printing success over a wrong binding.

## 0.6.0

- Deployment: `infra/systemd/` with podman quadlets (mcp, ui), a
  Cloudflare tunnel unit, and an idempotent `install.sh` deploy
  command, per the caseworkflow deployment pattern. The db moves to
  `data/` (same-path bind mount); the installer migrates an existing
  repo-root db.

## 0.5.1

- Fixed: newly added built-in sites now seed into existing databases
  (previously only an empty sites table was seeded).

## 0.5.0 — 2026-08-31

- Facebook Marketplace as the 22nd built-in site: browser-only (fully
  JS-rendered, no public API), card parser with price/title/location
  heuristics, login-wall detection surfaced as a clear per-site error
  ("login wall — set FB_COOKIES"), and optional logged-in searching by
  injecting the `FB_COOKIES` cookie header into the browser context
  (never persisted). Region and radius are plain site config, seeded
  for Durham NC (`region=durham`, `radius_km=80`). Browser page waits
  are now best-effort — a wall page returns its content for parsing
  instead of dying on a selector timeout.

## 0.4.0 — 2026-08-31

- Dashboard UI (`ui/`, Astro SSR): deals dashboard with filters and
  KPI tiles, price-history explorer, backtest visualizations (best
  price by window, savings vs baseline with 95% CIs, per-site win
  rates), and a site-health view. Reads the SQLite db read-only;
  `docker compose up ui` serves it on :4321 from the shared volume.

## 0.3.1 — 2026-08-31

- eBay dropped its plain-HTML tier (live 403s regardless of user
  agent): Browse API first, else browser; the sold-listings backfill
  spec is browser-fetched too.

## 0.3.0 — 2026-08-31

- Deal backtesting: new `packages/backtest` engine samples pivot dates
  from the past year and evaluates the best deal found over 3d/1w/2w/
  4w/8w/16w lookback windows, with paired bootstrap CIs, per-site win
  rates, and plain-English verdicts; results stored in SQLite.
- Price history: `price_history` table fed by every search
  (`kind='seen'`), by `backfill_ebay_sold` (real sale dates,
  `kind='sold'`), and by `add_price_observation`.
- New MCP tools: `run_backtest`, `get_backtest`, `list_backtests`,
  `backfill_ebay_sold`, `price_history_stats`, `add_price_observation`.
- eBay sold-listings parsing (`sold_at` from "Sold <date>" captions).

## 0.2.0 — 2026-08-31

- Tiered fetching per site, tried best-first and recorded per run:
  official API (eBay Browse, Best Buy, Walmart best-effort, Reddit
  JSON; env-var credentials, graceful "<VAR> unset" degradation) ->
  plain HTML -> Playwright browser for the nine JS-heavy sites.
- New `packages/browser` (product-finder-browser): Chromium fetching
  behind the sites seam; mcp extra `browser` enables it.
- `run_search` now reports which strategy ran per site
  (`strategies`), every failed attempt, and `browser_wired`.
- Docker: two targets — slim `mcp` and browser-capable `browser`
  (compose default) with Chromium installed.

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
