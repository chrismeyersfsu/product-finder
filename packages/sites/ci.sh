#!/usr/bin/env bash
# Per-package CI: run locally or from the workflow, identically.
set -euo pipefail
cd "$(dirname "$0")/../.."
uv sync -q
uv run ruff check packages/
uv run ruff format --check packages/
uv sync -q --package product-finder-sites
uv run --package product-finder-sites pytest packages/sites/tests -q
