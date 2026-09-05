"""Timer-driven scrape, plus the on-demand "scrape now" request queue.

Owns the oneshot entrypoint the product-finder-scrape.timer unit runs
(all products) and the consumer side of the on-demand request queue
the dashboard writes into (one product; see
infra/systemd/product-finder-scrape-now.*). Never speaks MCP and adds
no pipeline logic of its own — both paths drive server.rescore_product
then server.run_search per product (through the shared _scrape_product
helper, so the two paths can't drift) so scoring and storage stay in
one place. The rescore first is what makes
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

state/<mode>.json (mode is "hourly" for main()'s all-products run,
"requested" for process_requests()) records that run's live progress
for the dashboard — this is the one place its shape is documented,
ui/src/lib/monitor.ts mirrors it exactly:
  {
    "mode": "hourly" | "requested",
    "started_at": "<UTC ISO, +00:00>",
    "finished_at": null | "<UTC ISO>",
    "products": ["slug", ...],       // planned order: hourly writes every
                                      // product up front; requested grows
                                      // one slug at a time as it's picked
                                      // off the queue
    "current": "<slug>" | null,      // being scraped right now
    "current_started_at": "<UTC ISO>" | null,
    "results": {                     // one entry per finished product
      "<slug>": {
        "line": "<slug>: stored 12 (...); 3 site errors",  // summarize()'s
                                                            // per-product
                                                            // line, first
                                                            // line only
        "stored": 12,
        "errors": 3,
        "seconds": 41.2,
        "finished_at": "<UTC ISO>"
      }
    },
    "exit": null | 0 | 1             // set together with finished_at;
                                      // 1 iff summarize()'s total-failure
                                      // rule applies to this run
  }
Every write is atomic (temp file in state/, then os.replace), at run
start, before and after every product, and at the end — so a reader
never sees a torn file, and a crash mid-run leaves "current" set with
"finished_at" still null (the dashboard treats a state file whose
started_at is more than 60 minutes old and never finished as "died").

Callers rely on: a per-site summary on stdout (journalctl-friendly),
exit 0 while any site still produces (individual site blocks are
normal here), exit 1 only on total failure — every attempted site
errored and nothing was stored. --requested processes only the queue
(prints a message and exits 0 when it's empty) instead of every
product; the exit-1-on-total-failure rule applies to both modes.
"""

import argparse
import json
import os
import re
import sys
import tempfile
import time
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


def _result_stats(result: dict) -> tuple[int, int]:
    """(stored, site-error count) from a run_search-shaped result; 0, 0
    for an error-only result (unknown product, or a crashed attempt)."""
    if "error" in result and "per_site" not in result:
        return 0, 0
    return result.get("stored", 0), len(result.get("errors", {}))


def _scrape_product(slug: str) -> tuple[dict, float]:
    """Rescore slug then run_search it, once, timed.

    The single step both the hourly and on-demand paths use, so they
    can't drift apart.
    """
    start = time.monotonic()
    rescored = server.rescore_product(slug)
    result = {**server.run_search(slug), "rescored": rescored}
    return result, time.monotonic() - start


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write data as JSON to path atomically: a temp file in the same
    directory, then os.replace, so a concurrent reader never sees a
    torn file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class _RunState:
    """One scrape run's progress, persisted to state/<mode>.json after
    every change (see the module docstring for the file's shape)."""

    def __init__(self, base: Path, mode: str, products: list[str] | None = None):
        self.path = base / "state" / f"{mode}.json"
        self.data = {
            "mode": mode,
            "started_at": storage._now(),
            "finished_at": None,
            "products": list(products or []),
            "current": None,
            "current_started_at": None,
            "results": {},
            "exit": None,
        }
        self._save()

    def _save(self) -> None:
        _atomic_write_json(self.path, self.data)

    def add_product(self, slug: str) -> None:
        self.data["products"].append(slug)
        self._save()

    def start(self, slug: str) -> None:
        self.data["current"] = slug
        self.data["current_started_at"] = storage._now()
        self._save()

    def record(self, slug: str, result: dict, seconds: float) -> None:
        line = summarize({slug: result})[0].split("\n", 1)[0]
        stored, errors = _result_stats(result)
        self.data["results"][slug] = {
            "line": line,
            "stored": stored,
            "errors": errors,
            "seconds": round(seconds, 1),
            "finished_at": storage._now(),
        }
        self.data["current"] = None
        self.data["current_started_at"] = None
        self._save()

    def finish(self, total_failure: bool) -> None:
        self.data["finished_at"] = storage._now()
        self.data["exit"] = 1 if total_failure else 0
        self._save()


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
    slug that no longer names a product. Also maintains
    state/requested.json throughout (see the module docstring).
    """
    base = base or queue_dir()
    queue_path = base / "queue"
    running_path = base / "running"
    done_path = base / "done"
    for d in (queue_path, running_path, done_path):
        d.mkdir(parents=True, exist_ok=True)

    state = _RunState(base, "requested")
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
        state.add_product(slug)
        state.start(slug)
        product_start = time.monotonic()
        try:
            conn = server._connect()
            product = storage.get_product(conn, slug)
            if product is None:
                result = {"error": "no product"}
            else:
                try:
                    result, _ = _scrape_product(slug)
                except Exception as e:  # one bad request must not strand the rest
                    result = {"error": f"{type(e).__name__}: {e}"}
            runs[slug] = result
            state.record(slug, result, time.monotonic() - product_start)
            summary, _ = summarize({slug: result})
            (done_path / slug).write_text(summary + "\n")
        finally:
            running_file.unlink(missing_ok=True)
    _, total_failure = summarize(runs)
    state.finish(total_failure)
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
        slugs = [p["slug"] for p in products]
        state = _RunState(queue_dir(), "hourly", slugs)
        runs = {}
        for slug in slugs:
            state.start(slug)
            result, seconds = _scrape_product(slug)
            runs[slug] = result
            state.record(slug, result, seconds)
        _, total_failure = summarize(runs)
        state.finish(total_failure)

    text, total_failure = summarize(runs)
    print(text)
    if total_failure:
        print("total failure: every attempted site errored", file=sys.stderr)
        sys.exit(1)
