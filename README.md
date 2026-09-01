# product-finder

Generic product-search engine: define a product as data (search
queries + regex extractors + weighted criteria), search 21
marketplaces, score every listing against the criteria, store it all
in SQLite, and drive the whole thing over MCP. Ships seeded with its
founding use case: a thin-client laptop (ThinkPad X1 Carbon Gen 6+,
16GB, NVMe, FHD IPS) for RDP work.

## Quickstart

```sh
uv sync
uv run product-finder init          # create the SQLite db (or set PF_DB)
uv run product-finder seed          # insert the laptop product
uv run product-finder products
```

Run the MCP server (stdio) and wire it into Claude Code:

```sh
claude mcp add product-finder -- uv --directory /path/to/product-finder run product-finder-mcp
```

Or in a container (streamable-http on :8848, db persisted in a volume):

```sh
docker compose up --build
claude mcp add --transport http product-finder http://localhost:8848/mcp
```

## MCP tools

- `seed_defaults` — seed the laptop product + the 21 built-in sites
- `add_product` / `list_products` / `get_product` / `delete_product` —
  introduce any new product as pure data, no code
- `add_site` / `list_sites` / `set_site_enabled` — manage marketplaces
- `run_search` — fetch every enabled site for a product's queries,
  extract attributes from titles, score, and store
- `query_listings` — filter stored listings (score, price, site)
- `best_deals` — top-scored listings with price-vs-median context plus
  the product's manual checks (battery health, keyboard wear, ...)
- `project_list_files` / `project_read_file` / `project_write_file` /
  `project_run_ci` — modify this project itself over MCP, scoped to
  the repo root

## Fetching strategies

Each site declares an ordered list of strategies, tried best-first;
`run_search` reports which one actually ran per site (`strategies`)
and why the others didn't (`errors`):

1. **Official API** — `ebay_api` (Browse API, `EBAY_CLIENT_ID` +
   `EBAY_CLIENT_SECRET`), `bestbuy_api` (`BESTBUY_API_KEY`),
   `walmart_api` (`WALMART_API_KEY`, best-effort), and Reddit's public
   JSON. Missing credentials degrade gracefully: the site records a
   clear "<VAR> unset" error and falls to the next tier.
2. **Plain HTML** (`css`) — one urllib seam, pure bs4 parsers.
3. **Browser** (`browser_css`) — Playwright/Chromium renders the page,
   same CSS selectors; the fallback tier for the JS-heavy sites
   (amazon, walmart, target, bestbuy, backmarket, mercari, offerup,
   shopgoodwill, govdeals). eBay hard-blocks plain HTTP (403 on its
   search page regardless of user agent), so eBay — including the
   sold-listings backfill — has no plain-HTML tier at all: Browse API
   if `EBAY_CLIENT_ID`/`EBAY_CLIENT_SECRET` are set, else browser. Lives in `packages/browser` so Playwright
   never bloats plain installs; enable with the mcp `browser` extra
   (`uv sync --package product-finder-mcp --extra browser`) — the
   server wires it automatically when importable.

An empty result page falls through to the next tier too (bot walls
often answer 200 with no items).

## Container layout

The Dockerfile has two targets:

- `mcp` — slim image: API + plain-HTML tiers only; browser_css tiers
  degrade to per-site errors.
- `browser` (compose default) — `mcp` plus Playwright and Chromium
  (~600MB extra) so every tier runs.

```sh
docker build --target mcp -t product-finder:slim .
docker compose up --build            # browser-capable
```

## Defining a product

A product is four pieces of data (see the worked example in
`packages/core/src/product_finder_core/seed.py`):

- **queries** — strings sent to each site's search page
- **extractors** — `field -> {pattern, type}` regexes pulled from
  listing titles (`int`, `float`, `str`, `bool`, `size_gb`)
- **criteria** — weighted rules `{field, op, value, weight, required}`
  with ops `gte/lte/eq/contains/one_of/matches/exists`; score is
  earned-weight / total-weight, and a *present* value contradicting a
  `required` rule hard-fails the listing (missing = unknown, not fail)
- **manual_checks** — things only a human can verify before buying

## Packages

- `packages/core` — data model, SQLite storage, scoring (no network)
- `packages/sites` — 21 site adapters: tiered strategies, one I/O
  seam (`_get`/`_post`/`_get_browser`), pure parsers
- `packages/browser` — the Playwright tier, wired into the sites seam
- `packages/mcp` — the MCP server and search pipeline glue

## Backtesting deals

Does waiting longer actually get a better price — and which site wins?
`run_backtest` samples random **pivot dates** from the past year of
observed history (seeded, so results are reproducible) and asks, for
each pivot: what was the best qualifying deal in the trailing 3 days,
1, 2, 4, 8, and 16 weeks? It then compares each longer window against
the 3-day baseline with paired differences and a 95% bootstrap CI, and
reports per-site win rates. Results are stored in SQLite
(`backtests` table) — interact with them via `get_backtest` /
`list_backtests`.

The honest part: **backtests only see prices this database has
observed.** There is no way to scrape a year of history on demand.
History accrues three ways:

- every `run_search` appends `kind='seen'` observations to
  `price_history`;
- `backfill_ebay_sold` pulls real eBay sold/completed listings with
  their sale dates (`kind='sold'`) — eBay exposes roughly the last 90
  days;
- `add_price_observation` records points by hand (or from an import).

Until the span covers a window, that window is dropped and listed in
`coverage.dropped_windows`; windows with fewer than 30 data-bearing
pivots are flagged `insufficient_data` instead of being interpreted.
Every result carries a `caveats` list (notably: overlapping windows
share observations, so CIs are optimistic). Read the `verdict` block
first — it says, in plain English, whether waiting helps, by how many
dollars, whether that difference is statistically distinguishable from
zero, and which site supplies the winning deal most often.

Example, over MCP: `run_backtest("thin-client-laptop")`, later
`list_backtests()` and `get_backtest(id)`.

## Caveats — read before trusting results

- Scraping selectors rot. The built-in specs are best-effort snapshots;
  when a site redesigns, update its row in the `sites` table (or
  `spec.py`) — no code changes needed for selector fixes.
- JS-heavy / bot-blocking sites can still block the browser tier;
  every failed tier is recorded per site in `run_search`'s `errors`
  field and the run keeps going.
- `walmart_api` is best-effort: Walmart's production affiliate API
  wants signed headers; a plain key header is sent and 401s surface as
  per-site errors (the HTML/browser tiers then take over).
- Craigslist searches one region; put your region's subdomain in its
  `config.url` (default is sfbay).
- eBay is the only built-in that yields seller rating/feedback counts
  (via API or HTML), so seller-quality criteria only score there.
