# myproj

uv workspace monorepo; structure follows the patterns in
`caseworkflow/docs/patterns/` (workspace layout, conventions,
ci-release).

- `./packages/core/ci.sh` is the CI entry; run it before pushing.
  Hooks: `git config core.hooksPath .githooks`.
- One concern per package. A new concern gets a new package under
  `packages/`, not a subdirectory of an existing one.
- Tests fake external HTTP at a module's `_get`/`_post` seam against
  fixture files; parsers stay pure functions.
- Module docstrings state each module's contract: what it owns, what it
  never does, what callers rely on.
- `CHANGELOG.md` entry with every user-facing change, same commit.
