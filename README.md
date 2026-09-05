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

- `seed_defaults` — seed the laptop product + the 22 built-in sites
- `add_product` / `list_products` / `get_product` / `delete_product` —
  introduce any new product as pure data, no code
- `add_site` / `list_sites` / `set_site_enabled` — manage marketplaces
- `run_search` — fetch every enabled site for a product's queries,
  extract attributes from titles, score, and store
- `query_listings` — filter stored listings (score, price, site)
- `best_deals` — top-scored listings with price-vs-median context plus
  the product's manual checks (battery health, keyboard wear, ...)
- `backfill_market_values` — refit every product's market-value model
  (see *Used-car dealer sites*); `run_search` does this per product
- `rescore_product` — re-run a product's extractors and criteria over
  its stored listings after editing them
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
   `kroger_api` (`KROGER_CLIENT_ID` + `KROGER_CLIENT_SECRET`) serves
   Harris Teeter. `discogs_api` is keyless — Discogs' marketplace pages
   are Cloudflare-walled, but api.discogs.com answers anonymous
   searches, so `discogs` (vinyl/record listings) needs no credentials
   at all. Deployed containers read all of these from
   `~/.config/product-finder/secrets.env` (mode 600, created by
   `infra/systemd/install.sh`, never committed).
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

### Facebook Marketplace

`facebook-marketplace` is browser-only: Marketplace is fully
JS-rendered, has no public API, and usually answers anonymous
visitors with a login wall (surfaced as the per-site error
`login wall — set FB_COOKIES`, not a silent empty result). Location
is plain config: `region` (a Marketplace location slug, default
`durham`) and `radius_km` (default 80, ~50 miles — covers the
Triangle; the anonymous URL takes no ZIP code). To search logged in,
set `FB_COOKIES` to the Cookie header from your **own** logged-in
browser session (DevTools → Network → any facebook.com request →
Cookie). The cookies are injected into the throwaway browser context
for that fetch only — never written to the database, config, or logs.
Use your own account at your own risk: Facebook rate-limits and may
challenge automated sessions.

### Used-car dealer sites

`autolist`, `cars-com` and `carvana` search dealer inventory (the
Craigslist/Facebook sites cover private sellers). `autolist` is a
keyless JSON API (CarGurus's inventory); the other two need the
browser tier. All three fold the odometer into the title ("Used 2019
Honda Fit EX, 87024 mi") so a car product's mileage extractor works
unchanged, and put new/used plus the dealer name in the condition
column. Carvana has no free-text search — its URL is the query
slugified into a make-model[-trim] path — and delivers nationwide, so
its rows have no location and drop out under a distance cap.

**Market value.** KBB, Edmunds, TrueCar and the rest sit behind bot
walls, so there is no book value to fetch. Instead, after every scrape
of a product whose listings carry `year` and `mileage`, the finder fits
`ln(price) ~ age + miles` over that product's own listings (all sites;
salvage, parts and placeholder prices excluded; residual outliers
trimmed) and stores each row's fitted price as `est_value`. `best_deals`
and the Deals page report `pct_vs_est` / **vs est.** against it. It is
what that year and mileage is *asking* in your market, not KBB's
transaction data; it needs at least 10 usable listings.

### Discogs (vinyl/records)

`discogs` is a keyless site: Discogs' own marketplace pages
(discogs.com/sell) are Cloudflare-walled, but its public database
JSON API (api.discogs.com) answers anonymous requests fine. One
`database/search` call (free text plus `config["format"]`, default
`"vinyl"`) is followed by up to `config["max_releases"]` (default 8)
per-release lookups, paced to stay under Discogs' ~25 requests/min
anonymous limit; `config["skip_reissues"]` (default on) spends that
lookup budget on original pressings before reissues/bootlegs. Each
pressing with copies currently for sale becomes one listing — title
"Artist – Album (year, label, country) — N for sale", priced at that
pressing's cheapest copy — linking to the Discogs page where that
pressing's copies are actually listed, not its general info page.

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
  listing titles (`int`, `float`, `str`, `bool`, `size_gb`); add
  `fields: ["title", "condition"]` to search other listing fields too
  (the car products catch a known salvage dealer in the seller name
  that way)
- **criteria** — weighted rules `{field, op, value, weight, required}`
  with ops `gte/lte/eq/contains/one_of/matches/exists`; score is
  earned-weight / total-weight, and a *present* value contradicting a
  `required` rule hard-fails the listing (missing = unknown, not fail).
  `reject: true` drops non-product rows at ingest; `flag: true` keeps
  the row but surfaces the note on it (a salvage title is a flag, not
  a hard fail — you want to see the car and the discount)
- **manual_checks** — things only a human can verify before buying

After editing a product, `rescore_product(slug)` re-applies its
extractors and criteria to every stored listing; the hourly scrape
does this for every product before searching, so edits made from the
dashboard's Products pages reach stored listings within the hour.

## Packages

- `packages/core` — data model, SQLite storage, scoring (no network)
- `packages/sites` — 22 site adapters: tiered strategies, one I/O
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

## Dashboard UI

An Astro (SSR) dashboard in `ui/` reads the same SQLite db (its
writes are the Hide button, which stamps `listings.hidden_at`, the
Products pages, which edit the `products` table, and the scrape-now
queue files described under Deployment):

- **Deals** (`/`) — filterable best-deals table with KPI tiles and the
  product's verify-by-hand checklist, each listing with its photo. Car
  products show **Est. value** / **vs est.** (the fitted market value
  above) in place of **vs median**, and a ⚠ line for anything a `flag`
  criterion caught (salvage / rebuilt title). A
  **First seen** column and a
  `new` badge (first seen in the last 2 days) surface fresh listings;
  "New within N days" keeps only those. **Hide** drops a listing from
  deals for good (it keeps refreshing on scrapes, so it never returns
  as new); `hide_listing` / `unhide_listing` do the same over MCP.
- **Hidden** (`/hidden`) — every hidden listing, newest first, with an
  Unhide button.
- On a phone the Deals and Hidden tables become cards (no sideways
  scrolling) with Hide/Unhide pinned top-right and a Sort select in
  place of the column headers.
- **History** (`/history`) — observed prices over time; filled dots are
  real sold prices, rings are asking prices.
- **Backtests** (`/backtests`) — every stored backtest, visualized:
  best price by lookback window, savings vs the 3-day baseline with
  95% CIs, and per-site win rates. Caveats are always shown.
- **Products** (`/products`) — every product with its listing counts.
  **New product** needs only a name: the slug and the search query are
  generated from it, the first scrape is queued immediately, and
  everything else (sites, criteria, extractors, manual checks) waits
  under *Advanced* on the product page for when you want it. Edit and
  delete there too; **Scrape now** queues an immediate scrape of that
  one product instead of waiting for the hourly run. Same data the
  `add_product` MCP tool writes.
- **Sites** (`/sites`) — which scrapers are actually working, with the
  strategy that ran and the last error.
- **Monitor** (`/monitor`) — the hourly sync's live progress (a
  progress bar and a per-product table: done / running / pending, with
  each product's stored/error counts and timing) plus the on-demand
  "scrape now" queue as one ordered list (running, then queued in
  order, then recently finished). `?product=<slug>` highlights that
  product's row and estimates how many minutes out its turn is. Only
  auto-refreshes while a run or a queued request is actually active.

Run it locally (`cd ui && npm install && npm run dev`, then
http://localhost:4321, `PF_DB` to point at a db elsewhere) or in the
container (`docker compose up ui`, port 4321, shares the `/data`
volume with the MCP service). Charts follow a validated colorblind-safe
palette; every chart has a table view, tooltips, and a dark mode.

## Deployment

`./infra/systemd/install.sh` is the one deploy command — idempotent,
rerun it after changing units or app code. It follows the
caseworkflow deployment pattern (`caseworkflow/docs/patterns/
deployment.md`): rootless podman quadlets for the MCP server and the
dashboard UI, a plain systemd user unit for the Cloudflare tunnel, and
the gate + image builds + service restarts all inside the installer so
nothing broken reaches the running services.

What it sets up:

- `product-finder-mcp.container` — MCP server (browser image,
  streamable-http on 127.0.0.1:8848). The db lives in `data/`,
  bind-mounted at the same absolute path inside and out; the repo is
  bind-mounted too so the `project_*` MCP tools edit the real working
  tree.
- `product-finder-ui.container` — dashboard (127.0.0.1:4321), same db
  bind mount, read-only access.
- `product-finder-scrape.timer` + `.container` — hourly oneshot from
  the same browser image scraping every product (missed ticks fire on
  wake; `journalctl --user -u product-finder-scrape` shows the per-site
  summary; the run exits non-zero only when every site errored). Live
  progress (planned order, which product is running now, per-product
  results as they land) is written to `data/scrape-now/state/hourly.json`
  after every product, which the dashboard's **Monitor** page reads.
- `product-finder-scrape-now.path` + `.container` — on-demand scrapes.
  The dashboard's **Scrape now** button (and every newly created
  product) drops an empty file `data/scrape-now/queue/<slug>`; the
  path unit starts `product-finder-scrape --requested`, which moves it
  to `running/<slug>` while it scrapes just that product and leaves a
  one-line summary in `done/<slug>` when finished (the product page
  shows all three states). `touch data/scrape-now/queue/<slug>` does
  the same from a shell. This mode's own live progress is written to
  `data/scrape-now/state/requested.json`, same shape as the hourly
  state file (see `scrape.py`'s module docstring for the exact JSON).
- `product-finder-tunnel.service` — cloudflared serving
  https://product-finder.judicialschedule.com (config in
  `~/.cloudflared/product-finder.yml`; the installer writes it and the
  DNS route if missing).

The installer migrates a pre-existing repo-root `product_finder.db`
into `data/` once, refuses to run while an ad-hoc dev server holds
:4321 (and prints the command to stop it), and skips image builds and
restarts when nothing changed.

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
