# Automated Releases (release-please) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace clodcast's manual release workflow with `googleapis/release-please-action@v4` so versions + changelog are derived automatically from conventional commits and a release is cut by merging a bot-maintained release PR.

**Architecture:** A `push: main` workflow runs release-please in manifest mode. It maintains one standing "release PR" (version bump to `plugin.json` + `pyproject.toml` and a `CHANGELOG.md` entry, version computed from conventional commits, staying in 0.x). Merging that PR creates the `vX.Y.Z` tag + GitHub Release. The old manual `release.yml` + `cliff.toml` are deleted; release-please becomes the sole release path and owns `CHANGELOG.md`.

**Tech Stack:** GitHub Actions, `googleapis/release-please-action@v4`, JSON config (`release-please-config.json`, `.release-please-manifest.json`).

**Source spec:** `docs/superpowers/specs/2026-06-03-automated-releases-design.md`

**Branch:** `ci/release-please` (off `origin/main` @ `e069baf`). Deliver as ONE review-only PR — do NOT enable auto-merge.

**Known behavior to accept (not a bug):** a release PR opened by the default `GITHUB_TOKEN` does **not** trigger the `push`/`pull_request` CI workflows (GitHub loop-prevention). That's fine here — `main` branch protection requires **no** status checks, and release commits touch only JSON/TOML/Markdown (the `ruff format` gate is irrelevant to them). If auto-CI on release PRs is ever wanted, swap in a PAT/GitHub-App token (out of scope).

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `.github/workflows/release-please.yml` | create | The `push: main` trigger + the single release-please step + least-privilege perms |
| `release-please-config.json` | create | Release behavior: `simple` type, stay-in-0.x bump flags, version-file updaters, bootstrap-sha |
| `.release-please-manifest.json` | create | The current-version baseline (`0.1.0`) release-please reads/writes |
| `.github/workflows/release.yml` | delete | Old manual workflow (superseded) |
| `cliff.toml` | delete | Old git-cliff config (superseded) |
| `README.md` | modify | "### Releases" section (lines 24-30) → describe the release-please flow |
| `CONTRIBUTING.md` | modify | Changelog-generator reference (line ~91-92) git-cliff → release-please |

`CHANGELOG.md`, `.claude-plugin/plugin.json`, `pyproject.toml` are **kept unchanged** in this PR (release-please edits them later, in its own release PRs).

---

## Task 1: Add release-please config + manifest

**Files:**
- Create: `release-please-config.json`
- Create: `.release-please-manifest.json`

- [ ] **Step 1: Create `.release-please-manifest.json`**

```json
{
  ".": "0.1.0"
}
```

- [ ] **Step 2: Create `release-please-config.json`**

```json
{
  "$schema": "https://raw.githubusercontent.com/googleapis/release-please/main/schemas/config.json",
  "bootstrap-sha": "e069baf1d0de0e1002ded5a6897fc3868445317b",
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

- [ ] **Step 3: Validate both files are valid JSON**

Run:
```bash
python3 -c "import json; json.load(open('release-please-config.json')); json.load(open('.release-please-manifest.json')); print('JSON OK')"
```
Expected: `JSON OK`

- [ ] **Step 4: Verify the `extra-files` jsonpaths resolve against the real version files**

Run:
```bash
python3 -c "import json; assert json.load(open('.claude-plugin/plugin.json'))['version']=='0.1.0'; print('plugin \$.version OK')"
python3 -c "import tomllib; assert tomllib.load(open('pyproject.toml','rb'))['project']['version']=='0.1.0'; print('pyproject \$.project.version OK')"
```
Expected: `plugin $.version OK` then `pyproject $.project.version OK`

- [ ] **Step 5: Verify the `extra-files` updater spelling against the release-please v4 schema**

The `json`/`toml` updaters with a `jsonpath` query are the documented generic mechanism. Confirm the field names against the schema referenced in `$schema` (key names: `type`, `path`, `jsonpath`). If the schema names differ for the TOML updater, adjust to match — the intent is "bump `$.version` in plugin.json and `project.version` in pyproject.toml". This cannot be fully exercised locally (release-please needs GitHub context); the real test is the post-merge release PR (Task 5, Step 4).

- [ ] **Step 6: Commit**

```bash
git add release-please-config.json .release-please-manifest.json
git commit -m "ci: add release-please config + manifest (stay in 0.x)"
```

---

## Task 2: Add the release-please workflow

**Files:**
- Create: `.github/workflows/release-please.yml`

- [ ] **Step 1: Create `.github/workflows/release-please.yml`**

```yaml
name: release-please

# Automated releases. On every push to main, release-please opens/updates a
# single "release PR" with the next version (derived from conventional commits)
# + the CHANGELOG entry. Merging that PR cuts the vX.Y.Z tag + GitHub Release.
# This replaces the old manual release.yml — never push to main directly.
on:
  push:
    branches: [main]

# Least privilege: open/update the release PR (pull-requests) and create the
# tag + Release and commit to the PR branch (contents). Nothing else.
permissions:
  contents: write
  pull-requests: write

concurrency:
  group: release-please
  cancel-in-progress: false

jobs:
  release-please:
    runs-on: ubuntu-latest
    steps:
      - uses: googleapis/release-please-action@v4
        with:
          token: ${{ secrets.GITHUB_TOKEN }}
```

- [ ] **Step 2: Validate the workflow is valid YAML**

Run:
```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release-please.yml')); print('YAML OK')"
```
Expected: `YAML OK`

- [ ] **Step 3: Confirm the action is pinned to a major tag (repo convention)**

Run:
```bash
grep -n "googleapis/release-please-action@v4" .github/workflows/release-please.yml
```
Expected: one match on the `uses:` line.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/release-please.yml
git commit -m "ci: add release-please workflow on push to main"
```

---

## Task 3: Remove the manual release workflow + git-cliff config

**Files:**
- Delete: `.github/workflows/release.yml`
- Delete: `cliff.toml`

- [ ] **Step 1: Delete both files**

Run:
```bash
git rm .github/workflows/release.yml cliff.toml
```
Expected: `rm '.github/workflows/release.yml'` and `rm 'cliff.toml'`

- [ ] **Step 2: Confirm no workflow or config still references git-cliff/cliff.toml**

Run:
```bash
grep -rniE "git-cliff|cliff\.toml|orhun/git-cliff" .github/ pyproject.toml .pre-commit-config.yaml 2>/dev/null; echo "exit: $?"
```
Expected: no matches (grep prints nothing; `exit: 1`).

- [ ] **Step 3: Commit**

```bash
git commit -m "ci: remove manual release workflow + git-cliff config (superseded by release-please)"
```

---

## Task 4: Update docs to the release-please flow

**Files:**
- Modify: `README.md` (the "### Releases" section, lines 24-30)
- Modify: `CONTRIBUTING.md` (changelog-generator reference, ~line 91-92)

- [ ] **Step 1: Replace the README "### Releases" section**

Find this exact block:
```markdown
Versions follow [semver](https://semver.org/) and are tagged `vX.Y.Z`. See the
[**Releases**](https://github.com/schmug/clodcast/releases) page for tagged
versions and the [**CHANGELOG**](CHANGELOG.md) for what changed in each. Both are
generated from conventional commits by [git-cliff](https://git-cliff.org); a
release is cut manually via the [release workflow](.github/workflows/release.yml).
```
Replace with:
```markdown
Versions follow [semver](https://semver.org/) and are tagged `vX.Y.Z`. See the
[**Releases**](https://github.com/schmug/clodcast/releases) page for tagged
versions and the [**CHANGELOG**](CHANGELOG.md) for what changed in each. Both are
maintained automatically by [release-please](https://github.com/googleapis/release-please)
from conventional commits: every push to `main` updates a standing "release PR" with
the next version and changelog; **merging that PR** cuts the tag and GitHub Release.
```

- [ ] **Step 2: Update the CONTRIBUTING changelog-generator reference**

Find this exact text:
```markdown
  `security:`. The CHANGELOG is generated from these by
  [git-cliff](https://git-cliff.org), so the prefix is load-bearing.
```
Replace with:
```markdown
  `security:`. The CHANGELOG and the next version number are generated from these by
  [release-please](https://github.com/googleapis/release-please), so the prefix is load-bearing.
```

- [ ] **Step 3: Confirm no docs still reference the deleted workflow or git-cliff**

Run:
```bash
grep -rniE "release\.yml|git-cliff|cliff\.toml|cut manually" README.md CONTRIBUTING.md CLAUDE.md 2>/dev/null; echo "exit: $?"
```
Expected: no matches (`exit: 1`). (The spec/plan files under `docs/superpowers/` legitimately mention git-cliff as historical context — do not edit those.)

- [ ] **Step 4: Commit**

```bash
git add README.md CONTRIBUTING.md
git commit -m "docs: point release docs at release-please instead of git-cliff/manual workflow"
```

---

## Task 5: Final verification + open the review-only PR

**Files:** none (verification + PR)

- [ ] **Step 1: Confirm the full change set is exactly what's intended**

Run:
```bash
git diff --stat origin/main...HEAD
```
Expected: adds `release-please-config.json`, `.release-please-manifest.json`, `.github/workflows/release-please.yml`, the spec + plan docs; deletes `.github/workflows/release.yml`, `cliff.toml`; modifies `README.md`, `CONTRIBUTING.md`. No changes to `render.py`, `plugin.json`, `pyproject.toml`, or `CHANGELOG.md`.

- [ ] **Step 2: Re-validate all machine-readable files parse**

Run:
```bash
python3 -c "import json,yaml; json.load(open('release-please-config.json')); json.load(open('.release-please-manifest.json')); yaml.safe_load(open('.github/workflows/release-please.yml')); print('all config OK')"
```
Expected: `all config OK`

- [ ] **Step 3: Sanity-check the existing gate is unaffected (this PR touches no `.py`)**

Run:
```bash
ruff check . && ruff format --check . && python3 -m pytest -q 2>&1 | tail -1
```
Expected: `All checks passed!`, `N files already formatted`, and `158 passed` (unchanged baseline).

- [ ] **Step 4: Push and open the PR (base `main`, review-only — NO auto-merge)**

Run:
```bash
git push -u origin ci/release-please
gh pr create --base main --head ci/release-please \
  --title "ci: automate releases with release-please (supersedes #16)" \
  --body "$(cat <<'BODY'
Automates releases via \`googleapis/release-please-action@v4\`, replacing the manual workflow from #16.

## What changed
- **Add** \`.github/workflows/release-please.yml\` (push:main; least-privilege \`contents:write\` + \`pull-requests:write\`).
- **Add** \`release-please-config.json\` (\`simple\` type; stay-in-0.x bump flags; \`extra-files\` bump \`plugin.json\` \`$.version\` + \`pyproject.toml\` \`$.project.version\`; \`bootstrap-sha\` pins the start point).
- **Add** \`.release-please-manifest.json\` (\`{".":"0.1.0"}\`).
- **Delete** the manual \`release.yml\` + \`cliff.toml\` — release-please is now the sole release path and owns \`CHANGELOG.md\`.
- **Docs**: README + CONTRIBUTING point at release-please.

## How releases work now
Merge feature PRs as usual (conventional commits) → release-please keeps one "release PR" updated with the next version + changelog → **merge that PR** to cut the \`vX.Y.Z\` tag + GitHub Release. Still PR-only; no auto-merge of feature branches.

## Notes
- Release PRs opened by the default \`GITHUB_TOKEN\` don't auto-trigger CI (GitHub loop-prevention); acceptable — \`main\` requires no status checks and release commits are JSON/TOML/MD only.
- Spec: \`docs/superpowers/specs/2026-06-03-automated-releases-design.md\`.

## Post-merge verification
After this lands, the next \`feat\`/\`fix\` commit on \`main\` should make release-please open a release PR bumping both version files; merging it should produce the tag + Release.
BODY
)"
```
Expected: a PR URL. **Do not** run `gh pr merge --auto` or enable auto-merge.

- [ ] **Step 5: Report the PR URL and the post-merge verification steps to the maintainer.**

---

## Self-Review (completed by plan author)

- **Spec coverage:** every spec item maps to a task — workflow (T2), config+manifest+bootstrap-sha+0.x flags+extra-files (T1), delete release.yml/cliff.toml (T3), keep CHANGELOG/version fields (untouched by design), docs (T4), security/least-privilege (T2), verification (T1/T5). ✔
- **Placeholder scan:** no TBD/TODO; all file contents and commands are concrete. The one "verify the updater spelling" step (T1.S5) is a real validation action with a defined intent + fallback, not a placeholder. ✔
- **Consistency:** version files & jsonpaths (`$.version`, `$.project.version`) confirmed against the actual files; bootstrap-sha is the real `origin/main` HEAD `e069baf`; baseline test count `158` matches current `main`. ✔
