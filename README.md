# python-monorepo-template

A uv workspace monorepo, stubbed and ready for a new Python project:
one lockfile, one virtualenv, small installable packages under
`packages/`, each owning a single concern. Structure follows the
portable patterns in `caseworkflow/docs/patterns/` (workspace layout,
conventions, ci-release); the heavier patterns attach later as the
project earns them.

## Use it

```
gh repo create <name> --private --template chrismeyersfsu/python-monorepo-template --clone
cd <name>
./rename.sh <name>          # replaces the myproj placeholder, locks, hooks, self-deletes
./packages/core/ci.sh       # green from the first commit
```

## What's stubbed

| Piece | Where |
|---|---|
| Workspace root: members, dev group, ruff config | `pyproject.toml` |
| One package, src layout, hatchling, CLI via `[project.scripts]` | `packages/core/` |
| Per-package CI script (sync, ruff over all packages, pytest) | `packages/core/ci.sh` |
| Per-package release script (tag `core-vX.Y.Z` → wheel → GitHub release) | `packages/core/release.sh` |
| Thin path-filtered workflows calling the scripts | `.github/workflows/` |
| Pre-commit hook (fast half of ci.sh) | `.githooks/pre-commit` |
| Test stub showing the HTTP-at-one-seam faking convention | `packages/core/tests/` |
| Changelog (Keep a Changelog) and scoped agent instructions | `CHANGELOG.md`, `CLAUDE.md` |

## Conventions baked in

- Distribution names use dashes (`myproj-core`), import names use
  underscores (`myproj_core`).
- Test-only dependencies go in `[dependency-groups] dev`, never in
  `dependencies` — runtime metadata stays honest.
- CLIs are `[project.scripts]` entries; `uv run <name>` is the only
  invocation anyone types.
- Module docstrings state the module's contract; a docstring edit is a
  docs edit.
- `CHANGELOG.md` entry for every user-facing feature, same commit.

## Growing the project

Add a package per new concern (`packages/<concern>/`, copy `core`'s
shape, add it to the new package's workflow paths). When the import
graph needs enforcing, add a devtools package with import-linter
contracts; when orchestrators need extensions, use entry points; see
the pattern docs for storage (single-file SQLite Store), telemetry
(fail-safe OTel leaf package), and deployment (one image, systemd
quadlets).
