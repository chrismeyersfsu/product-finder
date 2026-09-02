#!/usr/bin/env bash
# Release from a tag like geo-v0.1.0: verify the tag matches the
# package version, run CI, build the wheel, create the GitHub release.
set -euo pipefail
cd "$(dirname "$0")/../.."
tag="${1:?usage: ./packages/geo/release.sh geo-vX.Y.Z}"
version="${tag#geo-v}"
grep -q "^version = \"${version}\"" packages/geo/pyproject.toml || {
    echo "tag ${tag} does not match version in packages/geo/pyproject.toml" >&2
    exit 1
}
./packages/geo/ci.sh
uv build --package product-finder-geo
gh release create "$tag" dist/*.whl --title "$tag" --generate-notes
