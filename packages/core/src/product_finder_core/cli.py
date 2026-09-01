"""Command-line entrypoint, wired via [project.scripts] in pyproject.toml.

`uv run product-finder <cmd>` covers local db chores: init, seed, and
read-only queries. Orchestration and file I/O stay here; the importable
modules stay pure. Searching lives behind the MCP server (packages/mcp),
which owns network orchestration.
"""

import argparse
import json

from . import seed as seed_mod
from . import storage


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="product-finder", description=__doc__.splitlines()[0])
    p.add_argument("--db", default=None, help="SQLite path (default: $PF_DB or product_finder.db)")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("init", help="create the database schema")
    sub.add_parser("seed", help="insert the seed product(s)")
    sub.add_parser("products", help="list products")
    lp = sub.add_parser("listings", help="query stored listings for a product")
    lp.add_argument("product_slug")
    lp.add_argument("--min-score", type=float, default=None)
    lp.add_argument("--max-price", type=float, default=None)
    lp.add_argument("--limit", type=int, default=20)
    args = p.parse_args(argv)

    if not args.cmd:
        p.print_help()
        return
    conn = storage.connect(args.db)
    if args.cmd == "init":
        print("initialized")
    elif args.cmd == "seed":
        print("seeded: " + ", ".join(seed_mod.seed(conn)))
    elif args.cmd == "products":
        for prod in storage.list_products(conn):
            print(f"{prod['slug']}: {prod['name']} ({len(prod['criteria'])} criteria)")
    elif args.cmd == "listings":
        rows = storage.query_listings(
            conn,
            args.product_slug,
            min_score=args.min_score,
            max_price=args.max_price,
            limit=args.limit,
        )
        print(json.dumps(rows, indent=2))
