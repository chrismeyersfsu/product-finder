#!/usr/bin/env bash
# Release from a tag like backtest-v0.1.0: verify the tag matches the
# package version, run CI, build the wheel, create the GitHub release.
set -euo pipefail
cd "$(dirname "$0")/../.."
tag="${1:?usage: ./packages/backtest/release.sh backtest-vX.Y.Z}"
version="${tag#backtest-v}"
grep -q "^version = \"${version}\"" packages/backtest/pyproject.toml || {
    echo "tag ${tag} does not match version in packages/backtest/pyproject.toml" >&2
    exit 1
}
./packages/backtest/ci.sh
uv build --package product-finder-backtest
gh release create "$tag" dist/*.whl --title "$tag" --generate-notes
