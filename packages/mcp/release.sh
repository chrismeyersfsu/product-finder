#!/usr/bin/env bash
# Release from a tag like mcp-v0.1.0: verify the tag matches the
# package version, run CI, build the wheel, create the GitHub release.
set -euo pipefail
cd "$(dirname "$0")/../.."
tag="${1:?usage: ./packages/mcp/release.sh mcp-vX.Y.Z}"
version="${tag#mcp-v}"
grep -q "^version = \"${version}\"" packages/mcp/pyproject.toml || {
    echo "tag ${tag} does not match version in packages/mcp/pyproject.toml" >&2
    exit 1
}
./packages/mcp/ci.sh
uv build --package product-finder-mcp
gh release create "$tag" dist/*.whl --title "$tag" --generate-notes
