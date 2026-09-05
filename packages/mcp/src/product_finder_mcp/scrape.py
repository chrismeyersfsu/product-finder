"""Timer-driven scrape, plus the on-demand "scrape now" request queue.

Owns the oneshot entrypoint the product-finder-scrape.timer unit runs
(all products) and the consumer side of the on-demand request queue
the dashboard writes into (one product; see
infra/systemd/product-finder-scrape-now.*). Never speaks MCP and adds
no pipeline logic of its own — both paths drive server.rescore_product
then server.run_search per product so scoring, storage, and price-
history accrual stay in one place. The rescore first is what makes
product edits made elsewhere (the dashboard's Products pages write the
products table directly) reach stored listings within the hour, or
immediately for a queued request.

Request queue layout, rooted at queue_dir() ($PF_SCRAPE_QUEUE, else
<dirname of $PF_DB>/scrape-now, else data/scrape-now when $PF_DB isn't
set) — this is the one place the layout is documented, the UI's writer
side mirrors it exactly:
  queue/<slug>   — empty file the dashboard creates: "please scrape <slug>".
  running/<slug> — created here (renamed from queue/) while that slug is
                   being scraped; removed once that attempt finishes.
  done/<slug>    — overwritten here when a requested scrape finishes: the
                   same per-product line summarize() produces for that
                   product (optionally followed by per-site error detail
                   lines), so the dashboard can show "last on-demand
                   scrape: …". File mtime is the finish time.
A queued slug naming a product that no longer exists is just removed,
with done/<slug> written as "<slug>: no product". Slugs are always
[a-z0-9-]; any other filename (dotfiles included) is left alone.
process_requests() loops until queue/ is empty, oldest request (by
mtime) first, since new requests may land while it runs.

Callers rely on: a per-site summary on stdout (journalctl-friendly),
exit 0 while any site still produces (individual site blocks are
normal here), exit 1 only on total failure — every attempted site
errored and nothing was stored. --requested processes only the queue
(prints a message and exits 0 when it's empty) instead of every
product; the exit-1-on-total-failure rule applies to both modes.
"""

import argparse
import os
import re
import sys
from pathlib import Path

from product_finder_core import storage

from . import server

_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


def summarize(runs: dict[str, dict]) -> tuple[str, bool]:
    """Pure: (report text, total_failure) from {product_slug: run_search result}.

    A site counts as failed only when it errored and produced nothing;
    a site that errored on one query but stored listings from another
    is producing, not failed.
    """
    lines: list[str] = []
    stored_total = 0
    attempted: set[str] = set()
    failed: set[str] = set()
    for slug, res in sorted(runs.items()):
        if "error" in res and "per_site" not in res:
            lines.append(f"{slug}: {res['error']}")
            continue
        per_site = res.get("per_site", {})
        errors = res.get("errors", {})
        stored = res.get("stored", 0)
        stored_total += stored
        attempted |= set(per_site) | set(errors)
        failed |= {s for s in errors if s not in per_site}
        ok = ", ".join(f"{s}:{n}" for s, n in sorted(per_site.items())) or "none"
        rescored = res.get("rescored")
        pre = (
            f"rescored {rescored['rescored']} (dropped {rescored['rejected']}); "
            if rescored
            else ""
        )
        lines.append(f"{slug}: {pre}stored {stored} ({ok}); {len(errors)} site errors")
        lines.extend(f"  {site}: {err}" for site, err in sorted(errors.items()))
    total_failure = bool(attempted) and stored_total == 0 and failed == attempted
    return "\n".join(lines), total_failure


def queue_dir() -> Path:
    """Base dir for the on-demand scrape-request queue.

    $PF_SCRAPE_QUEUE overrides outright; otherwise it's
    <dirname of $PF_DB>/scrape-now, matching where the containers'
    bind-mounted data dir already lives, falling back to
    data/scrape-now when $PF_DB isn't set.
    """
    override = os.environ.get("PF_SCRAPE_QUEUE")
    if override:
        return Path(override)
    pf_db = os.environ.get("PF_DB")
    if pf_db:
        return Path(os.path.dirname(pf_db)) / "scrape-now"
    return Path("data") / "scrape-now"


def process_requests(base: Path | None = None) -> dict[str, dict]:
    """Drain queue/, scraping each requested slug and writing done/<slug>.

    Loops until queue/ is empty (requests can arrive mid-run), oldest
    request (by mtime) first. Returns {slug: run_result} like main()
    builds for the all-products run — run_search()'s dict plus
    "rescored" for a known product, or {"error": "no product"} for a
    slug that no longer names a product.
    """
    base = base or queue_dir()
    queue_path = base / "queue"
    running_path = base / "running"
    done_path = base / "done"
    for d in (queue_path, running_path, done_path):
        d.mkdir(parents=True, exist_ok=True)

    runs: dict[str, dict] = {}
    while True:
        candidates = [
            p
            for p in queue_path.iterdir()
            if p.is_file() and not p.name.startswith(".") and _SLUG_RE.match(p.name)
        ]
        if not candidates:
            break
        candidates.sort(key=lambda p: p.stat().st_mtime)
        slug = candidates[0].name
        running_file = running_path / slug
        candidates[0].rename(running_file)
        try:
            conn = server._connect()
            product = storage.get_product(conn, slug)
            if product is None:
                result = {"error": "no product"}
            else:
                try:
                    rescored = server.rescore_product(slug)
                    result = {**server.run_search(slug), "rescored": rescored}
                except Exception as e:  # one bad request must not strand the rest
                    result = {"error": f"{type(e).__name__}: {e}"}
            runs[slug] = result
            summary, _ = summarize({slug: result})
            (done_path / slug).write_text(summary + "\n")
        finally:
            running_file.unlink(missing_ok=True)
    return runs


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Product Finder scrape run")
    parser.add_argument(
        "--requested",
        action="store_true",
        help="process only the on-demand request queue instead of every product",
    )
    args = parser.parse_args(argv)

    if args.requested:
        runs = process_requests()
        if not runs:
            print("no scrape requests queued")
            return
    else:
        conn = server._connect()
        products = storage.list_products(conn)
        if not products:
            print("no products configured; nothing to scrape")
            return
        runs = {}
        for p in products:
            rescored = server.rescore_product(p["slug"])
            runs[p["slug"]] = {**server.run_search(p["slug"]), "rescored": rescored}

    text, total_failure = summarize(runs)
    print(text)
    if total_failure:
        print("total failure: every attempted site errored", file=sys.stderr)
        sys.exit(1)
