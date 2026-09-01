#!/usr/bin/env bash
# Release from a tag like core-v0.1.0: verify the tag matches the
# package version, run CI, build the wheel, create the GitHub release.
set -euo pipefail
cd "$(dirname "$0")/../.."
tag="${1:?usage: ./packages/core/release.sh core-vX.Y.Z}"
version="${tag#core-v}"
grep -q "^version = \"${version}\"" packages/core/pyproject.toml || {
    echo "tag ${tag} does not match version in packages/core/pyproject.toml" >&2
    exit 1
}
./packages/core/ci.sh
uv build --package myproj-core
gh release create "$tag" dist/*.whl --title "$tag" --generate-notes
