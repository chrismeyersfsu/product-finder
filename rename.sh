#!/usr/bin/env bash
# One-shot bootstrap: replace the `myproj` placeholder with your project
# name, regenerate the lockfile, activate git hooks, then delete itself.
#
#   ./rename.sh acmeleads
#
# Use dashes in the name; underscores are derived for import paths
# (acme-leads -> acme_leads_core).
set -euo pipefail
new="${1:?usage: ./rename.sh <project-name-with-dashes>}"
new_us="${new//-/_}"

# Replace inside files (underscore form first so myproj_core -> newname_core)
grep -rl 'myproj' --exclude-dir=.git --exclude-dir=.venv --exclude=rename.sh --exclude=uv.lock . |
    while IFS= read -r f; do
        sed -i "s/myproj_/${new_us}_/g; s/myproj/${new}/g" "$f"
    done

# Rename import directories
for d in packages/*/src/myproj_*; do
    [ -e "$d" ] && mv "$d" "$(dirname "$d")/${new_us}_$(basename "$d" | cut -d_ -f2-)"
done

rm -f uv.lock
uv lock -q
git config core.hooksPath .githooks

rm -- "$0"
echo "Renamed to ${new}. Verify with ./packages/core/ci.sh, then commit."
