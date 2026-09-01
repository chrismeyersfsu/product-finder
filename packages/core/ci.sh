#!/usr/bin/env bash
# Per-package CI: run locally or from the workflow, identically.
# The ruff half runs over ALL packages so a cross-cutting break can't
# hide behind CI path filters; add `uv run lint-imports` here once the
# repo has a devtools package with architecture contracts.
set -euo pipefail
cd "$(dirname "$0")/../.."
uv sync -q
uv run ruff check packages/
uv run ruff format --check packages/
uv sync -q --package myproj-core
uv run --package myproj-core pytest packages/core/tests -q
