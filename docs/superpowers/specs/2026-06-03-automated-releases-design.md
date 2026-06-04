# Automated releases via release-please — design

**Status:** approved (brainstorm 2026-06-03)
**Supersedes:** the manual `workflow_dispatch` release workflow from #16 / PR #51
**Target repo:** `schmug/clodcast`

## Problem

Releases today are fully manual (#16): a maintainer triggers `release.yml` with a
hand-typed `X.Y.Z`, which validates the version, bumps `plugin.json` + `pyproject.toml`,
regenerates `CHANGELOG.md` via git-cliff, pushes the tag, cuts a GitHub Release, and
opens a PR to land the file changes. The maintainer wants the version-decision and
changelog work automated, while keeping the "never push to `main` directly" convention
and **not** introducing risky auto-merge of feature branches (global CLAUDE.md rule).

## Goals

- The next semantic version is **derived automatically** from conventional commits.
- The changelog is **generated automatically**.
- Cutting a release is a single deliberate human action (no auto-fire on every merge).
- Stays PR-based; no direct pushes to `main`; no auto-merge of feature branches.
- One release mechanism and one changelog engine — no duplication.

## Non-goals (out of scope)

- Publishing to any package registry (PyPI / npm).
- Changing the plugin marketplace manifest (`.claude-plugin/marketplace.json`, if any).
- Signing releases / provenance attestation.
- CI changes beyond adding the release workflow.

## Decisions (resolved during brainstorming)

1. **Model — release-please PR.** A bot maintains one standing "release PR" that
   accumulates pending changes, the computed next version, and the changelog. Merging
   that PR cuts the release. Version is automatic; release *timing* is the maintainer's
   (the merge). Reconciles automation with PR-only + the no-risky-auto-merge rule.
2. **Engine — `googleapis/release-please-action@v4`** (manifest mode). Chosen over a
   git-cliff-based custom workflow for maturity / low maintenance.
3. **Fate of #16 — replace.** Delete the manual `release.yml` and `cliff.toml`;
   release-please becomes the sole release path and owns `CHANGELOG.md` going forward.
   The valuable parts of #16 stay: the `version` fields in `plugin.json`/`pyproject.toml`,
   the conventional-commit discipline, and the existing `CHANGELOG.md` content.
4. **Versioning — stay in 0.x.** `bump-minor-pre-major: true` and
   `bump-patch-for-minor-pre-major: true`: `fix` → patch (`0.1.0`→`0.1.1`), `feat` →
   patch while pre-1.0, breaking change → minor (`0.1.x`→`0.2.0`). Never auto-jump to
   `1.0.0`; going 1.0 is a deliberate future decision.
5. **Bootstrap — `bootstrap-sha`.** Seed the manifest at `0.1.0` and pin
   `bootstrap-sha` to the adoption-time `main` HEAD so the first release PR only covers
   commits made *after* adoption (avoids a first PR that rakes in all history). No
   formal `v0.1.0` GitHub Release is cut; the first bot release will be the next bump.

## File-level design

### Add `.github/workflows/release-please.yml`

```yaml
name: release-please
on:
  push:
    branches: [main]
permissions:
  contents: write        # commit to the release PR branch; create tag + Release
  pull-requests: write   # open / update the release PR
concurrency:
  group: release-please
  cancel-in-progress: false
jobs:
  release-please:
    runs-on: ubuntu-latest
    steps:
      - uses: googleapis/release-please-action@v4
        with:
          # config + manifest are read from the repo root by default
          token: ${{ secrets.GITHUB_TOKEN }}
```

### Add `release-please-config.json` (repo root)

```jsonc
{
  "$schema": "https://raw.githubusercontent.com/googleapis/release-please/main/schemas/config.json",
  "bump-minor-pre-major": true,
  "bump-patch-for-minor-pre-major": true,
  "packages": {
    ".": {
      "release-type": "simple",
      "extra-files": [
        { "type": "json", "path": ".claude-plugin/plugin.json", "jsonpath": "$.version" },
        { "type": "toml", "path": "pyproject.toml", "jsonpath": "$.project.version" }
      ]
    }
  }
}
```

> Implementation note: confirm the exact `extra-files` updater spelling against the
> release-please v4 schema (the `json`/`toml` updaters with `jsonpath` are the
> documented mechanism; verify `$.project.version` resolves for our `pyproject.toml`
> layout). `release-type: simple` updates version files + `CHANGELOG.md` + tag without
> any language-specific packaging.

### Add `.release-please-manifest.json` (repo root)

```json
{ ".": "0.1.0" }
```

### Delete

- `.github/workflows/release.yml` (the manual workflow)
- `cliff.toml` (git-cliff config)

### Keep / unchanged

- `CHANGELOG.md` — release-please prepends new sections; existing v0.1.0-era content stays.
- `.claude-plugin/plugin.json` and `pyproject.toml` `version` fields (currently `0.1.0`).
- Conventional-commit prefixes (already required; CONTRIBUTING.md documents them).

## Release lifecycle (data flow)

1. Feature PRs merge to `main` as usual, using conventional commits.
2. Each push to `main` runs release-please, which opens/updates **one** release PR
   (e.g. `chore(main): release 0.1.1`) containing the version bump (both files) + the
   regenerated `CHANGELOG.md` entry.
3. To ship, the maintainer **merges the release PR**.
4. release-please detects the merged release commit and creates the `vX.Y.Z` tag + a
   GitHub Release with the changelog notes.

The only thing automated is version/changelog computation; the release trigger remains
a human merge of a reviewable PR.

## Security & conventions

- Least-privilege token: `contents: write` + `pull-requests: write` only.
- Action pinned to `@v4` (major tag), matching the repo's existing pin convention.
- The release PR runs the normal CI (ruff / `ruff format --check` / pytest). Release
  commits touch only JSON/TOML/Markdown, so the format gate is unaffected.
- No secrets beyond the default `GITHUB_TOKEN`.

## Edge cases

- A `main` push with only `chore`/`docs`/`ci`/`test`/`style` commits → no version bump;
  the release PR is created/updated only once releasable (`feat`/`fix`/breaking) commits
  exist.
- First run after adoption: `bootstrap-sha` scopes the changelog to post-adoption commits.
- Two pushes in quick succession: `concurrency` serializes release-please runs.

## Testing / verification

- No pytest coverage (config + workflow only).
- Pre-merge: validate `release-please.yml` is valid YAML; `release-please-config.json`
  and `.release-please-manifest.json` are valid JSON; the `extra-files` paths/jsonpaths
  resolve against the real `plugin.json` and `pyproject.toml`.
- Post-merge: confirm release-please opens a correct release PR after the next
  releasable commit lands on `main`; confirm merging it produces the tag + Release with
  both version files bumped.

## Rollback

Revert the PR: deleting `release-please.yml` + the two config files removes the
automation. (The manual `release.yml`/`cliff.toml` would need to be restored from
history if a manual path is wanted again.)
