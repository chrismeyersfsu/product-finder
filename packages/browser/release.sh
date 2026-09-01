#!/usr/bin/env bash
# Release from a tag like browser-v0.1.0: verify the tag matches the
# package version, run CI, build the wheel, create the GitHub release.
set -euo pipefail
cd "$(dirname "$0")/../.."
tag="${1:?usage: ./packages/browser/release.sh browser-vX.Y.Z}"
version="${tag#browser-v}"
grep -q "^version = \"${version}\"" packages/browser/pyproject.toml || {
    echo "tag ${tag} does not match version in packages/browser/pyproject.toml" >&2
    exit 1
}
./packages/browser/ci.sh
uv build --package product-finder-browser
gh release create "$tag" dist/*.whl --title "$tag" --generate-notes
