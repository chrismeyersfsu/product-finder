"""Timer-driven scrape: refresh every product's listings on a schedule.

Owns the oneshot entrypoint the product-finder-scrape.timer unit runs
(see infra/systemd/). Never speaks MCP and adds no pipeline logic of
its own — it drives server.rescore_product then server.run_search per
product so scoring, storage, and price-history accrual stay in one
place. The rescore first is what makes product edits made elsewhere
(the dashboard's Products pages write the products table directly)
reach stored listings within the hour. Callers rely on: a per-site
summary on stdout (journalctl-friendly), exit 0 while any site still
produces (individual site blocks are normal here), exit 1 only on
total failure — every attempted site errored and nothing was stored.
"""

import sys

from product_finder_core import storage

from . import server


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


def main(argv=None) -> None:
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
