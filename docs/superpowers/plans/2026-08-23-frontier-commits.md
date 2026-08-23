# Frontier Commits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Frontier Commits weekly podcast (frontier-lab GitHub activity → speculation-forward Spotify episodes) plus the daily snapshot data layer that powers it and the future /labs/ dashboard.

**Architecture:** A new `skills/frontier-commits/` skill in this repo. A pure-Python daily snapshot collector (launchd, gh CLI, no Claude credential) accumulates per-repo org state; a deterministic story detector diffs snapshots into typed, mention-once story candidates; a weekly SKILL.md-driven Claude routine researches and writes segments and ships through the existing `render.py` (one bounded change: `r2_manifest_name`).

**Tech Stack:** Python 3.10+ (stdlib + boto3, already a dependency), gh CLI 2.95, pytest, ruff 0.14.10, launchd, save-to-spotify CLI.

**Spec:** `docs/superpowers/specs/2026-08-23-frontier-commits-design.md` — read it first; every task below implements a numbered spec section.

## Global Constraints

- Python floor is 3.10 (CI matrix 3.10–3.12); no 3.11+ syntax even though the host python is 3.13.
- Ruff: line-length 100, `select = ["E","W","F","I","UP","B"]`, target py310. CI runs `ruff check .`, `ruff format --check .`, `pytest` — all three must pass after every task.
- No new pip dependencies. boto3 is already declared (lazy-imported); gh CLI is a host requirement like ffmpeg.
- Tests must never touch `~/.config/frontier-commits/` or `~/.config/daily-podcast/` — extend the existing conftest isolation + post-test assertion (Task 1).
- All state writes are atomic (`tempfile.mkstemp` + `os.replace`), all state reads degrade on malformed content (malformed JSON → `{}` / skip), never a hard failure.
- One final stdout line per CLI: `SNAPSHOT ok ...` / `SNAPSHOT FAILED ...` (collector), `SHIPPED ...` / `SKIPPED ...` / `FAILED ...` (weekly run). Schedulers parse these.
- Modules use the `fc_` prefix (`fc_common`, `fc_snapshot`, `fc_stories`, `fc_script_plan`) — tests import them by bare name via a conftest `sys.path` insert alongside the existing daily-podcast one, so names must not collide with `render`/`orchestrate`.
- Sibling modules access paths ONLY through `fc_common` helper functions (`fc_common.snapshot_dir()`, never `from fc_common import CONFIG_DIR`) so monkeypatching `fc_common.CONFIG_DIR` redirects everything.
- Conventional commits (`feat:`/`fix:`/`test:`/`docs:` with scope `frontier-commits` where apt); test + implementation in the same commit; commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- `render.py` stays single-file; the only render.py change in this plan is Task 12.
- The unattended weekly procedure has exactly ONE home: the new SKILL.md's "Unattended weekly run" section. `prompts/weekly.md` is a stub pointing there; a drift test enforces it (Task 10).

> **Post-review corrections (2026-08-23):** during the orchestrated build, adversarial critics on lanes n1 (PR #120) and n2 (PR #122) found defects in this plan's own code excerpts, since folded back into the text so it stays truthful: per-org failure isolation must catch `(GhError, SubprocessError, OSError)`, not `GhError` alone (a gh timeout or missing binary was killing the whole sweep); the release-check cap must sort by recency before truncating; `fetched_at` must use the injected clock; `write_snapshot` must validate the date before building the path; and `load_config` deep-merges `allowlist`/`denylist` per-org, validates the `orgs` container, and constrains org names to `[A-Za-z0-9-]+` (Task 1's excerpts predate those review fixes — the shipped `fc_common.py` on PR #120 is authoritative). The n3 critic round (PR #127) settled three more semantics now reflected in Task 6's contract and authoritative in the shipped `fc_stories.py`: the baseline must exclude the current snapshot date; snapshot staleness has a hard ceiling (`MAX_SNAPSHOT_AGE_DAYS = 3`); and with a baseline present, repo novelty is set-membership against the baseline's repo set, not date arithmetic. Task 4/6 code excerpts predate these. The n3 round-2 re-review settled two more: an org absent from (or empty in) the baseline snapshot is UNKNOWN, not empty — novelty for that org falls back to the date-cutoff arm, since delaying a genuinely-new repo by at most the lookback window is recoverable while flooding ancient repos as "new" burns mention-once keys permanently; and `detect_releases` skips only strictly-before-cutoff releases — a boundary-day release may be nominated twice pre-mark, but `reported.json` makes the mention exactly-once, and permanently missing a release is the real failure mode. The n4 round (PR #136) settled: labs.json's `new_repos` is a pure created-within-30-days timeline (novelty/mention-once is fc_stories' job alone); every dashboard window and span anchors to the measurement snapshot's date, never the CLI run date; the publish gate is keyed on (bucket, key, content); and any `die()` path in the CLI resolves to the `SNAPSHOT FAILED` final line. The n5 round (PR #138) replaced the week seed with the contiguous Monday-ordinal `// 7` counter and re-drove the rotation property tests off real consecutive Mondays — an integer-week model masked a row-skip at 52-week ISO year boundaries.

## File structure

```
skills/frontier-commits/
  SKILL.md                  # skill doc: config, script template, unattended weekly run, setup   (Task 10)
  fc_common.py              # paths, config, atomic writes, gh runner, R2/secrets resolution     (Task 1)
  fc_snapshot.py            # daily collector CLI: sweep → snapshot → labs.json → R2 + hook      (Tasks 2,3,7)
  fc_stories.py             # deterministic story detector CLI: detect / mark                    (Tasks 4,5,6)
  fc_script_plan.py         # week-seeded rotation: intro/outro/shapes/moves/bands               (Task 9)
  prompts/
    weekly.md               # stub → SKILL.md "Unattended weekly run"                            (Task 10)
    write_story.md          # per-story segment prompt (placeholder contract)                    (Task 10)
  launchd/
    com.cortech.frontier-commits-snapshot.plist                                                  (Task 8)
tests/
  conftest.py               # MODIFIED: second sys.path insert + fc isolation + guard            (Task 1)
  test_fc_common.py         # Task 1
  test_fc_snapshot.py       # Tasks 2,3,7
  test_fc_stories.py        # Tasks 4,5,6
  test_fc_script_plan.py    # Task 9
  test_fc_skill_md.py       # Task 10 (drift tests)
  test_r2.py                # MODIFIED: r2_manifest_name tests                                   (Task 12)
skills/daily-podcast/render.py  # MODIFIED: r2_manifest_name (validate_manifest + maybe_publish_r2) (Task 12)
```

Interfaces between phases, fixed here so tasks can be built out of order:

- `fc_stories.py detect` prints ONE JSON object: `{"run_date": str, "baseline_date": str|null, "thin": bool, "stories": [Story,...]}` where `Story = {"key": str, "type": str, "org": str, "repo": str, "url": str, "title": str, "score": float, "facts": dict}`.
- `fc_script_plan.py plan --date D --stories N` prints ONE JSON object: `{"week_row": int, "intro_mode": str, "intro_text": str, "outro_mode": str, "outro_text": str, "segments": [{"pos": int, "shape": str, "shape_text": str, "move": str, "move_text": str, "band": [int, int]}, ...]}`.
- `fc_snapshot.py` writes `snapshots/YYYY-MM-DD.json` (schema in Task 2) and publishes `labs.json` (schema in Task 7).
- The weekly manifest is a standard render.py manifest plus `"show_id"` (frontier show) and `"r2_manifest_name": "manifest-frontier-commits.json"`.

> **P4 note:** the cortech.online `/labs/` dashboard (spec §4.6) is a separate plan in the `schmug/cortech.online` repo, written once ≥1 week of snapshots exists on R2. It consumes `labs.json` + `manifest-frontier-commits.json` and is deliberately NOT in this plan.

---

## Phase P1 — data layer

### Task 1: `fc_common.py` + conftest isolation

**Files:**
- Create: `skills/frontier-commits/fc_common.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_fc_common.py`

**Interfaces:**
- Consumes: nothing (foundation).
- Produces: `CONFIG_DIR: Path` (module attr, monkeypatch target); `config_path() -> Path`; `snapshot_dir() -> Path`; `reported_path() -> Path`; `labs_hash_path() -> Path`; `secrets_path() -> Path`; `DEFAULT_CONFIG: dict`; `load_config() -> dict` (SystemExit if file missing; defaults merged UNDER file values); `atomic_write_json(path: Path, obj) -> None`; `log(msg: str) -> None`; `die(msg: str, code: int = 1) -> NoReturn`; `class GhError(RuntimeError)`; `run_gh(args: list[str], runner=subprocess.run) -> Any` (parsed JSON, raises GhError); `gh_paginate(path: str, runner=subprocess.run, per_page: int = 100, max_pages: int = 40) -> list[dict]`; `load_r2_secrets() -> dict[str, str]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fc_common.py
import json

import pytest

import fc_common


def test_load_config_dies_when_missing():
    with pytest.raises(SystemExit):
        fc_common.load_config()


def test_load_config_merges_defaults_under_file_values(tmp_path, monkeypatch):
    monkeypatch.setattr(fc_common, "CONFIG_DIR", tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"target_stories_per_episode": 4}))
    cfg = fc_common.load_config()
    assert cfg["target_stories_per_episode"] == 4          # file wins
    assert cfg["lookback_days"] == 7                        # default fills in
    assert any(o["name"] == "xai-org" for o in cfg["orgs"])  # default org list present


def test_load_config_rejects_bad_org_filter(tmp_path, monkeypatch):
    monkeypatch.setattr(fc_common, "CONFIG_DIR", tmp_path)
    (tmp_path / "config.json").write_text(
        json.dumps({"orgs": [{"name": "openai", "filter": "everything"}]})
    )
    with pytest.raises(SystemExit):
        fc_common.load_config()


def test_atomic_write_json_replaces_and_leaves_no_tmp(tmp_path):
    target = tmp_path / "state.json"
    target.write_text("old")
    fc_common.atomic_write_json(target, {"a": 1})
    assert json.loads(target.read_text()) == {"a": 1}
    assert list(tmp_path.glob(".state.json.*")) == []


def _fake_runner_pages(pages):
    """Return a runner whose Nth call yields pages[N] as gh stdout."""
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        idx = len(calls) - 1
        body = pages[idx] if idx < len(pages) else []

        class P:
            returncode = 0
            stdout = json.dumps(body)
            stderr = ""

        return P()

    runner.calls = calls
    return runner


def test_gh_paginate_stops_on_short_page():
    full = [{"name": f"r{i}"} for i in range(100)]
    short = [{"name": "last"}]
    runner = _fake_runner_pages([full, short])
    repos = fc_common.gh_paginate("orgs/x/repos", runner=runner)
    assert len(repos) == 101
    assert len(runner.calls) == 2
    assert "page=1" in runner.calls[0][-1] and "page=2" in runner.calls[1][-1]


def test_run_gh_raises_gherror_on_failure():
    def runner(cmd, **kw):
        class P:
            returncode = 1
            stdout = ""
            stderr = "HTTP 401"

        return P()

    with pytest.raises(fc_common.GhError):
        fc_common.run_gh(["api", "orgs/x"], runner=runner)


def test_load_r2_secrets_env_first(monkeypatch, tmp_path):
    monkeypatch.setattr(fc_common, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(fc_common, "DAILY_PODCAST_CONFIG_DIR", tmp_path / "dp")
    for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ACCOUNT_ID"):
        monkeypatch.setenv(k, f"env-{k}")
    assert fc_common.load_r2_secrets()["R2_ACCESS_KEY_ID"] == "env-R2_ACCESS_KEY_ID"


def test_load_r2_secrets_falls_back_to_daily_podcast_file(monkeypatch, tmp_path):
    monkeypatch.setattr(fc_common, "CONFIG_DIR", tmp_path / "fc")
    dp = tmp_path / "dp"
    dp.mkdir()
    monkeypatch.setattr(fc_common, "DAILY_PODCAST_CONFIG_DIR", dp)
    for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ACCOUNT_ID"):
        monkeypatch.delenv(k, raising=False)
    (dp / "secrets.json").write_text(json.dumps({"R2_ACCOUNT_ID": "from-dp"}))
    assert fc_common.load_r2_secrets().get("R2_ACCOUNT_ID") == "from-dp"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fc_common.py -v`
Expected: collection ERROR — `ModuleNotFoundError: No module named 'fc_common'` (conftest not yet extended).

- [ ] **Step 3: Extend `tests/conftest.py`**

Directly under the existing `SKILL_DIR` insert + `import render`, add (keeping the existing lines untouched):

```python
FC_SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "frontier-commits"
sys.path.insert(0, str(FC_SKILL_DIR))

import fc_common  # noqa: E402  (must follow the sys.path insert above)
```

In `pytest_configure`, next to the existing daily-podcast stash, add:

```python
    config._frontier_real_state_dir = Path.home() / ".config" / "frontier-commits"
```

In the `_isolate_user_state` autouse fixture body (before `yield sandbox`), add:

```python
    fc_sandbox = tmp_path_factory.mktemp("frontier-commits-state")
    monkeypatch.setattr(fc_common, "CONFIG_DIR", fc_sandbox)
```

In `_assert_no_real_state_writes`, after the existing render loop, add:

```python
    real_fc = request.config._frontier_real_state_dir
    fc_dir = Path(fc_common.CONFIG_DIR)
    assert real_fc not in fc_dir.parents and fc_dir != real_fc, (
        f"test left fc_common.CONFIG_DIR pointing at real user state: {fc_dir}"
    )
```

- [ ] **Step 4: Write `skills/frontier-commits/fc_common.py`**

```python
"""Shared paths, config, and helpers for the frontier-commits skill.

Sibling modules (fc_snapshot, fc_stories, fc_script_plan) import this module and
reach every path through the helper *functions* below, never by importing the
constants — so tests redirect the whole skill by monkeypatching
fc_common.CONFIG_DIR alone (tests/conftest.py does this for every test).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, NoReturn

CONFIG_DIR = Path.home() / ".config" / "frontier-commits"
# secrets.json has ONE durable home for this host: the daily-podcast dir (0600,
# already populated with R2 + deploy-hook keys). We fall back to it rather than
# duplicating credentials; a frontier-commits secrets.json wins if it ever exists.
DAILY_PODCAST_CONFIG_DIR = Path.home() / ".config" / "daily-podcast"

SNAPSHOT_RE = r"\d{4}-\d{2}-\d{2}\.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "show_name": "Frontier Commits",
    "host_name": "Cory",
    "orgs": [
        {"name": "anthropics", "filter": "none"},
        {"name": "openai", "filter": "none"},
        {"name": "xai-org", "filter": "none"},
        {"name": "google", "filter": "ai"},
        {"name": "google-deepmind", "filter": "none"},
    ],
    "ai_topics": [
        "ai", "artificial-intelligence", "machine-learning", "deep-learning",
        "llm", "language-model", "genai", "generative-ai", "agent", "agents",
        "agentic-ai", "mcp", "model-context-protocol", "gemini", "gemma",
        "neural-network", "reinforcement-learning", "nlp", "transformer",
    ],
    "ai_description_patterns": [
        r"\bAI\b", r"\bML\b", r"machine[ -]learning", r"deep[ -]learning",
        r"language model", r"\bLLM\b", r"\bagent", r"neural", r"transformer",
        r"\bGemini\b", r"\bGemma\b", r"diffusion", r"embedding", r"\bRAG\b",
    ],
    "allowlist": {"google": ["langextract", "adk-python", "sam", "artemis", "mantis"]},
    "denylist": {},
    "lookback_days": 7,
    "min_stories_per_episode": 2,
    "target_stories_per_episode": 6,
    "per_org_story_cap": 3,
    "release_min_stars": 500,
    "stale_min_stars": 1000,
    "stale_stages_days": [180, 365],
    "surge_min_delta_7d": 500,
    "surge_min_ratio": 0.20,
    "snapshot_retention_days": 400,
    "releases_repo_cap_per_org": 30,
    "r2_bucket": None,
    "r2_public_base_url": None,
    "labs_manifest_name": "labs.json",
}

VALID_ORG_FILTERS = ("none", "ai")


def config_path() -> Path:
    return CONFIG_DIR / "config.json"


def snapshot_dir() -> Path:
    return CONFIG_DIR / "snapshots"


def reported_path() -> Path:
    return CONFIG_DIR / "reported.json"


def labs_hash_path() -> Path:
    return CONFIG_DIR / "labs_json.sha256"


def secrets_path() -> Path:
    return CONFIG_DIR / "secrets.json"


def log(msg: str) -> None:
    print(msg, flush=True)


def die(msg: str, code: int = 1) -> NoReturn:
    log(f"error: {msg}")
    sys.exit(code)


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        die(f"missing {path} — see SKILL.md 'Setup' for the schema")
    try:
        file_cfg = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        die(f"unreadable {path}: {e}")
    if not isinstance(file_cfg, dict):
        die(f"{path} must contain a JSON object")
    cfg = {**DEFAULT_CONFIG, **file_cfg}
    for org in cfg["orgs"]:
        if not isinstance(org, dict) or "name" not in org:
            die(f'each org needs a "name": {org!r}')
        if org.get("filter", "none") not in VALID_ORG_FILTERS:
            die(f'org {org["name"]!r} filter must be one of {VALID_ORG_FILTERS}')
    return cfg


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(obj, indent=2, sort_keys=True))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class GhError(RuntimeError):
    pass


def _gh_env() -> dict[str, str]:
    """launchd doesn't inherit the shell env, and gh's keyring can be locked
    pre-login — a GH_TOKEN in secrets.json (either dir) is the deterministic
    fallback. Env var still wins if already set."""
    env = dict(os.environ)
    if "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env:
        for p in (secrets_path(), DAILY_PODCAST_CONFIG_DIR / "secrets.json"):
            try:
                token = json.loads(p.read_text()).get("GH_TOKEN")
            except (OSError, json.JSONDecodeError, AttributeError):
                token = None
            if token:
                env["GH_TOKEN"] = token
                break
    return env


def run_gh(args: list[str], runner=subprocess.run) -> Any:
    cmd = ["gh", *args]
    proc = runner(cmd, capture_output=True, text=True, env=_gh_env(), timeout=120)
    if proc.returncode != 0:
        raise GhError(f"gh {' '.join(args[:2])} exited {proc.returncode}: "
                      f"{(proc.stderr or proc.stdout or '').strip()[:300]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise GhError(f"gh {' '.join(args[:2])}: non-JSON output ({e})") from e


def gh_paginate(path: str, runner=subprocess.run, per_page: int = 100,
                max_pages: int = 40) -> list[dict[str, Any]]:
    """Manual pagination: deterministic, injectable, no dependence on gh flags."""
    sep = "&" if "?" in path else "?"
    out: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        batch = run_gh(["api", f"{path}{sep}per_page={per_page}&page={page}"], runner=runner)
        if not isinstance(batch, list):
            raise GhError(f"gh api {path}: expected a list page, got {type(batch).__name__}")
        out.extend(batch)
        if len(batch) < per_page:
            break
    return out


def load_r2_secrets() -> dict[str, str]:
    """Env first, then frontier secrets.json, then the daily-podcast secrets.json
    (same key names render.py uses)."""
    keys = ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ACCOUNT_ID",
            "PAGES_DEPLOY_HOOK_URL")
    out = {k: os.environ[k] for k in keys if os.environ.get(k)}
    for p in (secrets_path(), DAILY_PODCAST_CONFIG_DIR / "secrets.json"):
        if len(out) == len(keys):
            break
        try:
            data = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            for k in keys:
                if k not in out and data.get(k):
                    out[k] = data[k]
    return out
```

Note on `subprocess` runner injection: every network-touching function takes `runner=subprocess.run`; tests pass fakes and never shell out. Mirror this in Tasks 2–7.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_fc_common.py -v`
Expected: 9 passed. Then run the FULL suite (`pytest`) — the conftest change must not break existing tests. Then `ruff check . && ruff format --check .`.

- [ ] **Step 6: Commit**

```bash
git add skills/frontier-commits/fc_common.py tests/conftest.py tests/test_fc_common.py
git commit -m "feat(frontier-commits): fc_common config/paths/gh helpers + test isolation"
```

### Task 2: `fc_snapshot.py` — org sweep, AI filter, snapshot write

**Files:**
- Create: `skills/frontier-commits/fc_snapshot.py`
- Test: `tests/test_fc_snapshot.py`

**Interfaces:**
- Consumes: `fc_common.gh_paginate`, `fc_common.load_config`, `fc_common.atomic_write_json`, `fc_common.snapshot_dir`, `fc_common.GhError`.
- Produces: `repo_record(raw: dict) -> dict`; `matches_ai(record: dict, config: dict, org: str) -> bool`; `filter_repos(records: list[dict], org_cfg: dict, config: dict) -> list[dict]`; `sweep_org(org_cfg: dict, config: dict, runner) -> dict` (`{"fetched_at": iso, "repos": {name: record}, "releases": []}`); `build_snapshot(config: dict, date_iso: str, runner) -> dict`; `write_snapshot(snap: dict) -> Path`; `prune_snapshots(retention_days: int, today: datetime.date) -> list[str]`.

**Snapshot file schema** (`snapshots/YYYY-MM-DD.json`) — fixed contract for Tasks 4–7 and the future /labs/ plan:

```json
{
  "schema_version": 1,
  "date": "2026-08-25",
  "orgs": {
    "anthropics": {
      "fetched_at": "2026-08-25T06:15:04+00:00",
      "repos": {
        "claude-code": {
          "stars": 142710, "forks": 3100, "open_issues": 210,
          "created_at": "2024-02-01T00:00:00Z", "pushed_at": "2026-08-23T10:00:00Z",
          "archived": false, "fork": false, "topics": ["ai"],
          "description": "", "language": "Python"
        }
      },
      "releases": []
    }
  },
  "errors": {"openai": "gh api orgs/openai exited 1: HTTP 500"}
}
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fc_snapshot.py
import datetime as dt
import json

import pytest

import fc_common
import fc_snapshot


def _raw(name, **kw):
    base = {
        "name": name, "stargazers_count": 10, "forks_count": 1, "open_issues_count": 0,
        "created_at": "2026-08-01T00:00:00Z", "pushed_at": "2026-08-20T00:00:00Z",
        "archived": False, "fork": False, "topics": [], "description": "a repo",
        "language": "Python",
    }
    base.update(kw)
    return base


def _cfg(**kw):
    cfg = dict(fc_common.DEFAULT_CONFIG)
    cfg.update(kw)
    return cfg


def test_repo_record_projects_fields_and_defaults_description():
    rec = fc_snapshot.repo_record(_raw("x", description=None, topics=None))
    assert rec == {
        "stars": 10, "forks": 1, "open_issues": 0,
        "created_at": "2026-08-01T00:00:00Z", "pushed_at": "2026-08-20T00:00:00Z",
        "archived": False, "fork": False, "topics": [], "description": "",
        "language": "Python",
    }


def test_ai_filter_topic_description_allowlist_denylist():
    cfg = _cfg(allowlist={"google": ["sam"]}, denylist={"google": ["guava"]})
    org = {"name": "google", "filter": "ai"}
    by_topic = fc_snapshot.repo_record(_raw("t", topics=["machine-learning"]))
    by_desc = fc_snapshot.repo_record(_raw("d", description="A library for LLM agents"))
    by_allow = fc_snapshot.repo_record(_raw("sam"))
    plumbing = fc_snapshot.repo_record(_raw("guava", topics=["java"], description="collections"))
    denied = fc_snapshot.repo_record(_raw("guava", topics=["machine-learning"]))
    names = {"t": by_topic, "d": by_desc, "sam": by_allow, "gson": plumbing, "guava": denied}
    kept = fc_snapshot.filter_repos(
        [dict(r, name=n) for n, r in names.items()], org, cfg
    )
    kept_names = {r["name"] for r in kept}
    assert kept_names == {"t", "d", "sam"}  # denylist beats topic hit; plumbing dropped


def test_filter_none_keeps_everything_including_forks():
    org = {"name": "anthropics", "filter": "none"}
    recs = [dict(fc_snapshot.repo_record(_raw("a", fork=True)), name="a")]
    assert fc_snapshot.filter_repos(recs, org, _cfg()) == recs


def test_build_snapshot_isolates_per_org_failure():
    cfg = _cfg(orgs=[{"name": "good", "filter": "none"}, {"name": "bad", "filter": "none"}])

    def runner(cmd, **kw):
        class P:
            returncode = 0
            stdout = json.dumps([_raw("r1")])
            stderr = ""

        if "orgs/bad" in cmd[-1]:
            P.returncode, P.stdout, P.stderr = 1, "", "HTTP 500"
        return P()

    snap = fc_snapshot.build_snapshot(cfg, "2026-08-25", runner=runner)
    assert "r1" in snap["orgs"]["good"]["repos"]
    assert "bad" not in snap["orgs"]
    assert "bad" in snap["errors"]


def test_build_snapshot_raises_when_every_org_fails():
    cfg = _cfg(orgs=[{"name": "bad", "filter": "none"}])

    def runner(cmd, **kw):
        class P:
            returncode = 1
            stdout = ""
            stderr = "HTTP 500"

        return P()

    with pytest.raises(fc_snapshot.SweepFailed):
        fc_snapshot.build_snapshot(cfg, "2026-08-25", runner=runner)


def test_write_snapshot_is_atomic_and_named_by_date():
    snap = {"schema_version": 1, "date": "2026-08-25", "orgs": {}, "errors": {}}
    path = fc_snapshot.write_snapshot(snap)
    assert path == fc_common.snapshot_dir() / "2026-08-25.json"
    assert json.loads(path.read_text())["date"] == "2026-08-25"


def test_prune_snapshots_drops_old_keeps_recent_and_foreign_files():
    d = fc_common.snapshot_dir()
    d.mkdir(parents=True)
    (d / "2024-01-01.json").write_text("{}")
    (d / "2026-08-01.json").write_text("{}")
    (d / "notes.txt").write_text("keep me")
    removed = fc_snapshot.prune_snapshots(400, today=dt.date(2026, 8, 25))
    assert removed == ["2024-01-01.json"]
    assert (d / "2026-08-01.json").exists() and (d / "notes.txt").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fc_snapshot.py -v`
Expected: `ModuleNotFoundError: No module named 'fc_snapshot'`.

- [ ] **Step 3: Implement in `skills/frontier-commits/fc_snapshot.py`**

```python
"""Daily org snapshot collector for the frontier-commits skill.

Pure Python, no LLM, no Claude credential — designed to run under launchd with
only the gh CLI. Star history no longer exists upstream (the stargazers
timestamps endpoint 404s), so these daily snapshots ARE the show's and the
/labs/ page's only source of velocity. Losing a day degrades trends; it never
breaks a run — fc_stories diffs against the newest snapshot available.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys

import fc_common


class SweepFailed(RuntimeError):
    pass


def repo_record(raw: dict) -> dict:
    return {
        "stars": raw.get("stargazers_count", 0),
        "forks": raw.get("forks_count", 0),
        "open_issues": raw.get("open_issues_count", 0),
        "created_at": raw.get("created_at", ""),
        "pushed_at": raw.get("pushed_at", ""),
        "archived": bool(raw.get("archived", False)),
        "fork": bool(raw.get("fork", False)),
        "topics": raw.get("topics") or [],
        "description": raw.get("description") or "",
        "language": raw.get("language") or "",
    }


def matches_ai(record: dict, config: dict, org: str) -> bool:
    name = record.get("name", "")
    if name in config.get("denylist", {}).get(org, []):
        return False
    if name in config.get("allowlist", {}).get(org, []):
        return True
    topics = {t.lower() for t in record.get("topics", [])}
    if topics & {t.lower() for t in config["ai_topics"]}:
        return True
    desc = record.get("description", "")
    return any(re.search(p, desc, re.IGNORECASE) for p in config["ai_description_patterns"])


def filter_repos(records: list[dict], org_cfg: dict, config: dict) -> list[dict]:
    if org_cfg.get("filter", "none") == "none":
        # denylist still applies even without the AI filter
        deny = set(config.get("denylist", {}).get(org_cfg["name"], []))
        return [r for r in records if r.get("name") not in deny]
    return [r for r in records if matches_ai(r, config, org_cfg["name"])]


def sweep_org(org_cfg: dict, config: dict, runner=subprocess.run) -> dict:
    raw = fc_common.gh_paginate(f"orgs/{org_cfg['name']}/repos", runner=runner)
    records = []
    for item in raw:
        rec = repo_record(item)
        rec["name"] = item.get("name", "")
        records.append(rec)
    kept = filter_repos(records, org_cfg, config)
    repos = {r.pop("name"): r for r in kept}
    return {
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "repos": repos,
        "releases": [],
    }


def build_snapshot(config: dict, date_iso: str, runner=subprocess.run) -> dict:
    orgs: dict = {}
    errors: dict = {}
    for org_cfg in config["orgs"]:
        try:
            orgs[org_cfg["name"]] = sweep_org(org_cfg, config, runner=runner)
        except (fc_common.GhError, subprocess.SubprocessError, OSError) as e:
            errors[org_cfg["name"]] = str(e)
            fc_common.log(f"warn: org {org_cfg['name']} failed, continuing: {e}")
    if not orgs:
        raise SweepFailed(f"every org failed: {errors}")
    return {"schema_version": 1, "date": date_iso, "orgs": orgs, "errors": errors}


def write_snapshot(snap: dict) -> "Path":
    # The writer and the pruner must agree on what a snapshot filename is —
    # an unvalidated date here ('../escaped', '2026-8-5') either escapes the
    # snapshot dir or writes a file the retention regex can never prune.
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", snap.get("date") or ""):
        raise ValueError(f"snapshot date must be YYYY-MM-DD, got {snap.get('date')!r}")
    path = fc_common.snapshot_dir() / f"{snap['date']}.json"
    fc_common.atomic_write_json(path, snap)
    return path


def prune_snapshots(retention_days: int, today: dt.date) -> list[str]:
    """Only files named YYYY-MM-DD.json are candidates; anything else is kept
    (same no-data-loss posture as covered.json pruning)."""
    removed = []
    d = fc_common.snapshot_dir()
    if not d.is_dir():
        return removed
    cutoff = today - dt.timedelta(days=retention_days)
    for p in sorted(d.iterdir()):
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.json", p.name)
        if not m:
            continue
        try:
            when = dt.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if when < cutoff:
            try:
                p.unlink()
                removed.append(p.name)
            except OSError as e:
                fc_common.log(f"warn: could not prune {p.name}: {e}")
    return removed
```

(`from pathlib import Path` goes at the top with the other imports; the string annotation above is only to keep this excerpt short. The `main()` CLI arrives in Task 7 — this task ships library functions only, so add a temporary `if __name__ == "__main__": sys.exit("CLI arrives with labs.json publishing — import the functions instead")` guard.)

- [ ] **Step 4: Run tests, then the full suite and lint**

Run: `pytest tests/test_fc_snapshot.py -v` → 7 passed; `pytest` → all green; `ruff check . && ruff format --check .`

- [ ] **Step 5: Commit**

```bash
git add skills/frontier-commits/fc_snapshot.py tests/test_fc_snapshot.py
git commit -m "feat(frontier-commits): org sweep, AI filter, atomic snapshot store"
```

### Task 3: `fc_snapshot.py` — targeted release detection

**Files:**
- Modify: `skills/frontier-commits/fc_snapshot.py`
- Test: `tests/test_fc_snapshot.py` (append)

**Interfaces:**
- Consumes: `fc_common.run_gh`, Task 2's `sweep_org`/`build_snapshot`.
- Produces: `repos_needing_release_check(repos: dict[str, dict], now: dt.datetime, hours: int = 48, cap: int = 30) -> list[str]` (recency-sorted before capping); `fetch_releases(org: str, repo_names: list[str], window_days: int, now: dt.datetime, runner) -> list[dict]` (entries `{"repo", "tag", "published_at"}`). `sweep_org` gains a `now` param and fills `"releases"`; the per-org cap `releases_repo_cap_per_org` is enforced with a logged line when it truncates (no silent caps).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_fc_snapshot.py`)

```python
NOW = dt.datetime(2026, 8, 25, 6, 0, tzinfo=dt.timezone.utc)


def test_repos_needing_release_check_selects_recent_pushes_only():
    repos = {
        "fresh": {"pushed_at": "2026-08-24T12:00:00Z"},
        "stale": {"pushed_at": "2026-07-01T00:00:00Z"},
        "nodate": {"pushed_at": ""},
    }
    assert fc_snapshot.repos_needing_release_check(repos, NOW) == ["fresh"]


def test_release_check_cap_is_enforced_and_logged(capsys):
    repos = {f"r{i}": {"pushed_at": "2026-08-24T12:00:00Z"} for i in range(40)}
    names = fc_snapshot.repos_needing_release_check(repos, NOW, cap=30)
    assert len(names) == 30
    assert "capped" in capsys.readouterr().out


def test_fetch_releases_window_and_error_isolation():
    def runner(cmd, **kw):
        class P:
            returncode = 0
            stdout = json.dumps([
                {"tag_name": "v2", "published_at": "2026-08-22T00:00:00Z"},
                {"tag_name": "v1", "published_at": "2026-01-01T00:00:00Z"},
            ])
            stderr = ""

        if "broken" in cmd[-1]:
            P.returncode, P.stdout, P.stderr = 1, "", "HTTP 404"
        return P()

    out = fc_snapshot.fetch_releases("acme", ["ok", "broken"], 7, NOW, runner=runner)
    assert out == [{"repo": "ok", "tag": "v2", "published_at": "2026-08-22T00:00:00Z"}]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_fc_snapshot.py -v` — the three new tests fail with `AttributeError`.

- [ ] **Step 3: Implement**

```python
def _parse_iso(ts: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def repos_needing_release_check(repos: dict, now: dt.datetime,
                                hours: int = 48, cap: int = 30) -> list[str]:
    cutoff = now - dt.timedelta(hours=hours)
    names = [
        n for n, r in repos.items()
        if (ts := _parse_iso(r.get("pushed_at", ""))) and ts >= cutoff
    ]
    # Recency first, THEN cap — an alphabetical cap would keep the 30
    # alphabetically-first repos and drop the most-recently-pushed one.
    names.sort(key=lambda n: (_parse_iso(repos[n]["pushed_at"]), n), reverse=True)
    if len(names) > cap:
        fc_common.log(f"warn: release check capped at {cap} of {len(names)} recently-pushed repos")
        names = names[:cap]
    return names


def fetch_releases(org: str, repo_names: list[str], window_days: int,
                   now: dt.datetime, runner=subprocess.run) -> list[dict]:
    cutoff = now - dt.timedelta(days=window_days)
    out: list[dict] = []
    for name in repo_names:
        try:
            rels = fc_common.run_gh(
                ["api", f"repos/{org}/{name}/releases?per_page=5"], runner=runner
            )
        except (fc_common.GhError, subprocess.SubprocessError, OSError) as e:
            fc_common.log(f"warn: releases for {org}/{name} skipped: {e}")
            continue
        for rel in rels if isinstance(rels, list) else []:
            ts = _parse_iso(rel.get("published_at") or "")
            if ts and ts >= cutoff:
                out.append({"repo": name, "tag": rel.get("tag_name", ""),
                            "published_at": rel.get("published_at", "")})
    return out
```

Wire into `sweep_org` (which gains `now: dt.datetime | None = None`, defaulting to `dt.datetime.now(dt.timezone.utc)`): after building `repos`, set `releases = fetch_releases(org_cfg["name"], repos_needing_release_check(repos, now, cap=config["releases_repo_cap_per_org"]), config["lookback_days"], now, runner=runner)` — and switch `fetched_at` to the SAME resolved clock (`now.isoformat(timespec="seconds")`); a second wall-clock read there silently ignores an injected `now` and leaves the field untestable.

- [ ] **Step 4: Run tests + suite + lint** — `pytest` all green, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add skills/frontier-commits/fc_snapshot.py tests/test_fc_snapshot.py
git commit -m "feat(frontier-commits): targeted release detection with logged cap"
```

### Task 4: `fc_stories.py` — snapshot loading, baseline, NEW_REPO / NOTABLE_FORK / ARCHIVED

**Files:**
- Create: `skills/frontier-commits/fc_stories.py`
- Test: `tests/test_fc_stories.py`

**Interfaces:**
- Consumes: snapshot files (Task 2 schema) via `fc_common.snapshot_dir()`.
- Produces: `list_snapshot_dates() -> list[str]`; `load_snapshot(date_iso: str) -> dict | None` (malformed → None); `pick_baseline(dates: list[str], run_date: str, lookback_days: int) -> str | None`; `story(type_: str, org: str, repo: str, stage: str, facts: dict) -> dict` (builds the Story dict incl. `key` = `f"{type_}:{org}/{repo}:{stage}"`, `url` = `f"https://github.com/{org}/{repo}"`, `title` = `f"{org}/{repo}"`, `score` = 0.0 placeholder until Task 6); `detect_new_repos(cur: dict, cutoff_date: str) -> list[dict]`; `detect_notable_forks(cur: dict, cutoff_date: str, run_date: str) -> list[dict]`; `detect_archived(cur: dict, base: dict | None) -> list[dict]`.

Detector semantics (each takes the per-org view; a top-level `detect_all` arrives in Task 5):
- `cutoff_date` = baseline snapshot date if one exists, else `run_date - lookback_days`. Repos `created_at > cutoff_date` count as "appeared".
- NEW_REPO: appeared AND `fork == False`. Stage `"new"`. Facts: `created_at, stars, description, language, topics`.
- NOTABLE_FORK: appeared AND `fork == True` AND `pushed_at` within 14 days of `run_date` (the actively-pushed arm of the spec's OR — the upstream-stars arm needs a per-fork parent lookup and is deliberately deferred; SKILL.md documents this).
- ARCHIVED: `base` is not None, repo in both, `base.archived == False and cur.archived == True`. Stage `"archived"`. Facts include `pushed_at` and `stars`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fc_stories.py
import json

import fc_common
import fc_stories


def _repo(**kw):
    base = {
        "stars": 50, "forks": 0, "open_issues": 0,
        "created_at": "2026-08-20T00:00:00Z", "pushed_at": "2026-08-24T00:00:00Z",
        "archived": False, "fork": False, "topics": [], "description": "d",
        "language": "Py",
    }
    base.update(kw)
    return base


def _write_snap(date, org_repos):
    d = fc_common.snapshot_dir()
    d.mkdir(parents=True, exist_ok=True)
    snap = {"schema_version": 1, "date": date,
            "orgs": {o: {"fetched_at": "", "repos": r, "releases": []}
                     for o, r in org_repos.items()},
            "errors": {}}
    (d / f"{date}.json").write_text(json.dumps(snap))
    return snap


def test_pick_baseline_prefers_newest_at_or_before_lookback():
    dates = ["2026-08-10", "2026-08-17", "2026-08-18", "2026-08-24"]
    assert fc_stories.pick_baseline(dates, "2026-08-25", 7) == "2026-08-18"


def test_pick_baseline_falls_back_to_oldest_then_none():
    assert fc_stories.pick_baseline(["2026-08-24"], "2026-08-25", 7) == "2026-08-24"
    assert fc_stories.pick_baseline(["2026-08-25"], "2026-08-25", 7) is None
    assert fc_stories.pick_baseline([], "2026-08-25", 7) is None


def test_load_snapshot_degrades_to_none_on_malformed():
    d = fc_common.snapshot_dir()
    d.mkdir(parents=True)
    (d / "2026-08-25.json").write_text("{not json")
    assert fc_stories.load_snapshot("2026-08-25") is None


def test_detect_new_repos_skips_forks_and_old_repos():
    cur = {
        "shiny": _repo(created_at="2026-08-21T00:00:00Z"),
        "forked": _repo(created_at="2026-08-21T00:00:00Z", fork=True),
        "ancient": _repo(created_at="2020-01-01T00:00:00Z"),
    }
    got = fc_stories.detect_new_repos({"acme": cur}, "2026-08-18")
    assert [s["repo"] for s in got] == ["shiny"]
    assert got[0]["key"] == "NEW_REPO:acme/shiny:new"
    assert got[0]["url"] == "https://github.com/acme/shiny"


def test_detect_notable_forks_requires_recent_push():
    cur = {
        "git": _repo(fork=True, created_at="2026-08-21T00:00:00Z",
                     pushed_at="2026-08-24T00:00:00Z"),
        "dead": _repo(fork=True, created_at="2026-08-21T00:00:00Z",
                      pushed_at="2026-06-01T00:00:00Z"),
    }
    got = fc_stories.detect_notable_forks({"openai": cur}, "2026-08-18", "2026-08-25")
    assert [s["repo"] for s in got] == ["git"]
    assert got[0]["type"] == "NOTABLE_FORK"


def test_detect_archived_needs_a_flip_and_a_baseline():
    base = {"acme": {"gym": _repo(archived=False)}}
    cur = {"acme": {"gym": _repo(archived=True), "born-dead": _repo(archived=True)}}
    got = fc_stories.detect_archived(cur, base)
    assert [s["key"] for s in got] == ["ARCHIVED:acme/gym:archived"]
    assert fc_stories.detect_archived(cur, None) == []
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_fc_stories.py -v` → `ModuleNotFoundError: fc_stories`.

- [ ] **Step 3: Implement `skills/frontier-commits/fc_stories.py`**

```python
"""Deterministic story detector for Frontier Commits.

Pure function of (snapshots, reported.json, config): no network, no LLM.
Curation stays metadata-only — the same philosophy as the daily show's
one-body-per-request invariant. The "mention once, not repeatedly" rule is
mechanical: a story key that reaches reported.json never re-emits; a repo
resurfaces only when its STAGE changes (stale-180 → stale-365 → archived).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys

import fc_common

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\.json$")
FORK_ACTIVE_DAYS = 14


def _parse_iso(ts: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _date_only(ts: str) -> str:
    d = _parse_iso(ts)
    return d.date().isoformat() if d else ""


def list_snapshot_dates() -> list[str]:
    d = fc_common.snapshot_dir()
    if not d.is_dir():
        return []
    return sorted(m.group(1) for p in d.iterdir() if (m := DATE_RE.search(p.name)))


def load_snapshot(date_iso: str) -> dict | None:
    path = fc_common.snapshot_dir() / f"{date_iso}.json"
    try:
        snap = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return snap if isinstance(snap, dict) else None


def pick_baseline(dates: list[str], run_date: str, lookback_days: int) -> str | None:
    target = (dt.date.fromisoformat(run_date) - dt.timedelta(days=lookback_days)).isoformat()
    eligible = [d for d in dates if d <= target]
    if eligible:
        return eligible[-1]
    older = [d for d in dates if d < run_date]
    return older[-1] if older else None


def story(type_: str, org: str, repo: str, stage: str, facts: dict) -> dict:
    return {
        "key": f"{type_}:{org}/{repo}:{stage}",
        "type": type_, "org": org, "repo": repo,
        "url": f"https://github.com/{org}/{repo}",
        "title": f"{org}/{repo}",
        "score": 0.0,
        "facts": facts,
    }


def _org_repos(snap_orgs: dict) -> "list[tuple[str, str, dict]]":
    return [(org, name, rec)
            for org, view in snap_orgs.items()
            for name, rec in view.get("repos", {}).items()] \
        if all(isinstance(v, dict) and "repos" in v for v in snap_orgs.values()) \
        else [(org, name, rec)
              for org, repos in snap_orgs.items()
              for name, rec in repos.items()]
```

**Simplification note for the implementer:** the dual-shape `_org_repos` above is ugly — instead, make every detector take the SAME shape: `cur: dict[org, dict[name, record]]` (plain repos maps, no `fetched_at` wrapper), and add one adapter `org_views(snap: dict) -> dict[org, dict[name, record]]` used by `detect_all` in Task 5 to unwrap the snapshot file. The tests above already pass plain repos maps. Write it that way:

```python
def org_views(snap: dict) -> dict:
    return {org: view.get("repos", {}) for org, view in snap.get("orgs", {}).items()}


def detect_new_repos(cur: dict, cutoff_date: str) -> list[dict]:
    out = []
    for org, repos in cur.items():
        for name, r in repos.items():
            if r.get("fork") or _date_only(r.get("created_at", "")) <= cutoff_date:
                continue
            out.append(story("NEW_REPO", org, name, "new", {
                "created_at": r.get("created_at", ""), "stars": r.get("stars", 0),
                "description": r.get("description", ""), "language": r.get("language", ""),
                "topics": r.get("topics", []),
            }))
    return out


def detect_notable_forks(cur: dict, cutoff_date: str, run_date: str) -> list[dict]:
    active_cutoff = dt.date.fromisoformat(run_date) - dt.timedelta(days=FORK_ACTIVE_DAYS)
    out = []
    for org, repos in cur.items():
        for name, r in repos.items():
            if not r.get("fork") or _date_only(r.get("created_at", "")) <= cutoff_date:
                continue
            pushed = _parse_iso(r.get("pushed_at", ""))
            if not pushed or pushed.date() < active_cutoff:
                continue
            out.append(story("NOTABLE_FORK", org, name, "new", {
                "created_at": r.get("created_at", ""), "pushed_at": r.get("pushed_at", ""),
                "stars": r.get("stars", 0), "description": r.get("description", ""),
            }))
    return out


def detect_archived(cur: dict, base: dict | None) -> list[dict]:
    if not base:
        return []
    out = []
    for org, repos in cur.items():
        for name, r in repos.items():
            prev = base.get(org, {}).get(name)
            if prev and not prev.get("archived") and r.get("archived"):
                out.append(story("ARCHIVED", org, name, "archived", {
                    "stars": r.get("stars", 0), "pushed_at": r.get("pushed_at", ""),
                    "description": r.get("description", ""),
                }))
    return out
```

(Delete the `_org_repos` sketch entirely — `org_views` is the adapter. Sort each detector's output by `(org, repo)` for determinism.)

- [ ] **Step 4: Run tests + suite + lint** — 6 new passed, all green, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add skills/frontier-commits/fc_stories.py tests/test_fc_stories.py
git commit -m "feat(frontier-commits): story detector core — new repos, notable forks, archivals"
```

### Task 5: `fc_stories.py` — GOING_STALE stages, STAR_SURGE, RELEASE, mention-once

**Files:**
- Modify: `skills/frontier-commits/fc_stories.py`
- Test: `tests/test_fc_stories.py` (append)

**Interfaces:**
- Consumes: Task 4's `story`, `org_views`, `_parse_iso`.
- Produces: `detect_going_stale(cur: dict, run_date: str, config: dict) -> list[dict]`; `detect_star_surge(cur: dict, base: dict | None, baseline_date: str | None, run_date: str, config: dict) -> list[dict]`; `detect_releases(snap: dict, cutoff_date: str, cur: dict, config: dict) -> list[dict]`; `iso_week(run_date: str) -> str` (e.g. `"2026-W35"`); `load_reported() -> dict`; `filter_new(candidates: list[dict], reported: dict) -> list[dict]`; `mark_reported(keys: list[str], date_iso: str, episode_uri: str) -> None`.

Semantics:
- GOING_STALE: `stars >= config["stale_min_stars"]`, not archived; `days = run_date - pushed_at date`; stage = largest boundary in `config["stale_stages_days"]` with `days >= boundary`, named `f"stale-{boundary}"`; no boundary crossed → no story. No baseline needed.
- STAR_SURGE: needs baseline; `delta7 = (cur.stars - base.stars) * 7 / max(1, days_between(baseline_date, run_date))`; trigger `delta7 >= max(config["surge_min_delta_7d"], config["surge_min_ratio"] * base.stars)`; stage = `iso_week(run_date)` — re-runs the same week rebuild the same episode (idempotent), a surge sustained into the next week may legitimately re-emit and scoring de-prioritizes it.
- RELEASE: reads the CURRENT snapshot's per-org `releases` lists; keep entries with `published_at > cutoff_date` whose repo has `stars >= config["release_min_stars"]` or is allowlisted for that org; stage = tag.
- `load_reported`: malformed/missing → `{}` (worst case a repeated mention one week — mirrors covered.json posture). `mark_reported` merges `{key: {"date": date_iso, "episode_uri": episode_uri}}` and writes atomically via `fc_common.atomic_write_json(fc_common.reported_path(), ...)`.

- [ ] **Step 1: Write the failing tests** (append)

```python
def _cfg(**kw):
    cfg = dict(fc_common.DEFAULT_CONFIG)
    cfg.update(kw)
    return cfg


def test_going_stale_stages_and_floor():
    cur = {"acme": {
        "big-stale": _repo(stars=5000, pushed_at="2026-01-01T00:00:00Z"),   # 236 days
        "big-dead": _repo(stars=5000, pushed_at="2024-08-01T00:00:00Z"),    # > 365
        "small-stale": _repo(stars=10, pushed_at="2026-01-01T00:00:00Z"),
        "fresh": _repo(stars=5000, pushed_at="2026-08-20T00:00:00Z"),
        "tombstone": _repo(stars=5000, pushed_at="2026-01-01T00:00:00Z", archived=True),
    }}
    got = fc_stories.detect_going_stale(cur, "2026-08-25", _cfg())
    assert {s["key"] for s in got} == {
        "GOING_STALE:acme/big-stale:stale-180",
        "GOING_STALE:acme/big-dead:stale-365",
    }


def test_star_surge_normalizes_to_seven_days_and_needs_baseline():
    base = {"acme": {"hot": _repo(stars=1000), "cool": _repo(stars=1000)}}
    cur = {"acme": {"hot": _repo(stars=2200), "cool": _repo(stars=1100)}}
    got = fc_stories.detect_star_surge(cur, base, "2026-08-11", "2026-08-25", _cfg())
    # hot: +1200 over 14d -> +600/7d >= max(500, 200); cool: +50/7d -> no
    assert [s["repo"] for s in got] == ["hot"]
    assert got[0]["key"] == "STAR_SURGE:acme/hot:2026-W35"
    assert fc_stories.detect_star_surge(cur, None, None, "2026-08-25", _cfg()) == []


def test_release_detection_respects_stars_bar_and_allowlist():
    snap = {"orgs": {"acme": {"repos": {}, "releases": [
        {"repo": "big", "tag": "v2", "published_at": "2026-08-22T00:00:00Z"},
        {"repo": "tiny", "tag": "v1", "published_at": "2026-08-22T00:00:00Z"},
        {"repo": "listed", "tag": "v3", "published_at": "2026-08-22T00:00:00Z"},
        {"repo": "old", "tag": "v0", "published_at": "2026-01-01T00:00:00Z"},
    ]}}}
    cur = {"acme": {"big": _repo(stars=900), "tiny": _repo(stars=5),
                    "listed": _repo(stars=5), "old": _repo(stars=900)}}
    cfg = _cfg(allowlist={"acme": ["listed"]})
    got = fc_stories.detect_releases(snap, "2026-08-18", cur, cfg)
    assert {s["key"] for s in got} == {
        "RELEASE:acme/big:v2", "RELEASE:acme/listed:v3",
    }


def test_mention_once_and_stage_readmission():
    s180 = fc_stories.story("GOING_STALE", "acme", "x", "stale-180", {})
    s365 = fc_stories.story("GOING_STALE", "acme", "x", "stale-365", {})
    fc_stories.mark_reported([s180["key"]], "2026-08-25", "spotify:episode:e1")
    reported = fc_stories.load_reported()
    assert fc_stories.filter_new([s180, s365], reported) == [s365]


def test_load_reported_degrades_on_malformed():
    fc_common.reported_path().parent.mkdir(parents=True, exist_ok=True)
    fc_common.reported_path().write_text("{broken")
    assert fc_stories.load_reported() == {}


def test_mark_reported_round_trips_and_merges():
    fc_stories.mark_reported(["A:o/r:new"], "2026-08-25", "spotify:episode:e1")
    fc_stories.mark_reported(["B:o/r:new"], "2026-09-01", "spotify:episode:e2")
    rep = fc_stories.load_reported()
    assert rep["A:o/r:new"]["episode_uri"] == "spotify:episode:e1"
    assert rep["B:o/r:new"]["date"] == "2026-09-01"
```

- [ ] **Step 2: Run to verify failure** — new tests fail with `AttributeError`.

- [ ] **Step 3: Implement**

```python
def iso_week(run_date: str) -> str:
    y, w, _ = dt.date.fromisoformat(run_date).isocalendar()
    return f"{y}-W{w:02d}"


def detect_going_stale(cur: dict, run_date: str, config: dict) -> list[dict]:
    today = dt.date.fromisoformat(run_date)
    out = []
    for org, repos in cur.items():
        for name, r in repos.items():
            if r.get("archived") or r.get("stars", 0) < config["stale_min_stars"]:
                continue
            pushed = _parse_iso(r.get("pushed_at", ""))
            if not pushed:
                continue
            days = (today - pushed.date()).days
            crossed = [b for b in sorted(config["stale_stages_days"]) if days >= b]
            if not crossed:
                continue
            out.append(story("GOING_STALE", org, name, f"stale-{crossed[-1]}", {
                "stars": r.get("stars", 0), "pushed_at": r.get("pushed_at", ""),
                "days_since_push": days, "description": r.get("description", ""),
            }))
    return sorted(out, key=lambda s: (s["org"], s["repo"]))


def detect_star_surge(cur: dict, base: dict | None, baseline_date: str | None,
                      run_date: str, config: dict) -> list[dict]:
    if not base or not baseline_date:
        return []
    span = max(1, (dt.date.fromisoformat(run_date) - dt.date.fromisoformat(baseline_date)).days)
    out = []
    for org, repos in cur.items():
        for name, r in repos.items():
            prev = base.get(org, {}).get(name)
            if not prev:
                continue
            delta7 = (r.get("stars", 0) - prev.get("stars", 0)) * 7 / span
            bar = max(config["surge_min_delta_7d"],
                      config["surge_min_ratio"] * prev.get("stars", 0))
            if delta7 >= bar:
                out.append(story("STAR_SURGE", org, name, iso_week(run_date), {
                    "stars": r.get("stars", 0), "stars_before": prev.get("stars", 0),
                    "delta_per_7d": round(delta7), "span_days": span,
                    "description": r.get("description", ""),
                }))
    return sorted(out, key=lambda s: (s["org"], s["repo"]))


def detect_releases(snap: dict, cutoff_date: str, cur: dict, config: dict) -> list[dict]:
    out = []
    for org, view in snap.get("orgs", {}).items():
        allow = set(config.get("allowlist", {}).get(org, []))
        for rel in view.get("releases", []):
            name = rel.get("repo", "")
            if _date_only(rel.get("published_at", "")) <= cutoff_date:
                continue
            rec = cur.get(org, {}).get(name, {})
            if rec.get("stars", 0) < config["release_min_stars"] and name not in allow:
                continue
            out.append(story("RELEASE", org, name, rel.get("tag", ""), {
                "tag": rel.get("tag", ""), "published_at": rel.get("published_at", ""),
                "stars": rec.get("stars", 0), "description": rec.get("description", ""),
            }))
    return sorted(out, key=lambda s: (s["org"], s["repo"], s["key"]))


def load_reported() -> dict:
    try:
        data = json.loads(fc_common.reported_path().read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def filter_new(candidates: list[dict], reported: dict) -> list[dict]:
    return [c for c in candidates if c["key"] not in reported]


def mark_reported(keys: list[str], date_iso: str, episode_uri: str) -> None:
    rep = load_reported()
    for k in keys:
        rep[k] = {"date": date_iso, "episode_uri": episode_uri}
    fc_common.atomic_write_json(fc_common.reported_path(), rep)
```

- [ ] **Step 4: Run tests + suite + lint** — all green.

- [ ] **Step 5: Commit**

```bash
git add skills/frontier-commits/fc_stories.py tests/test_fc_stories.py
git commit -m "feat(frontier-commits): stale stages, star surge, releases, mention-once state"
```

### Task 6: `fc_stories.py` — scoring, per-org cap, selection, `detect`/`mark` CLI

**Files:**
- Modify: `skills/frontier-commits/fc_stories.py`
- Test: `tests/test_fc_stories.py` (append)

**Interfaces:**
- Consumes: everything above.
- Produces: `TYPE_PRIORITY: dict[str, int]` = `{"NEW_REPO": 100, "ARCHIVED": 90, "NOTABLE_FORK": 80, "RELEASE": 60, "GOING_STALE": 50, "STAR_SURGE": 40}`; `score_story(s: dict) -> float` = `TYPE_PRIORITY[s["type"]] + 10 * math.log10(s["facts"].get("stars", 0) + 10)`; `select_stories(cands: list[dict], target: int, per_org_cap: int) -> list[dict]` (score desc, tie-break by key asc, skip a story once its org has `per_org_cap` picks); `detect_all(run_date: str, config: dict) -> dict` (the full pipeline: load the newest snapshot ≤ run_date, warn on a 1–`MAX_SNAPSHOT_AGE_DAYS` (=3) day gap, die beyond it — gap-tolerant across a missed cron day, but detecting against a stale world is worse than no run; pick baseline WITH THE CURRENT DATE EXCLUDED so the baseline can never be the current snapshot; run all six detectors — when a baseline exists, "new repo" means present-in-cur AND absent-from-baseline (set membership), the created_at cutoff being only the no-baseline fallback; `filter_new`; score; select; return the Task-header JSON contract with `"thin": len(stories) < config["min_stories_per_episode"]`); `main(argv) -> int` — `detect --date D [--lookback-days N]` prints the JSON, `mark --stories FILE --episode-uri URI` reads the detect-output JSON file and marks every `stories[].key`.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_scoring_orders_types_then_stars():
    new_small = fc_stories.story("NEW_REPO", "a", "x", "new", {"stars": 0})
    surge_huge = fc_stories.story("STAR_SURGE", "a", "y", "2026-W35", {"stars": 100000})
    assert fc_stories.score_story(new_small) > fc_stories.score_story(surge_huge)


def test_select_stories_enforces_per_org_cap_and_target():
    cands = []
    for i in range(5):
        s = fc_stories.story("NEW_REPO", "monopoly", f"r{i}", "new", {"stars": 100 - i})
        s["score"] = fc_stories.score_story(s)
        cands.append(s)
    other = fc_stories.story("RELEASE", "indie", "z", "v1", {"stars": 1})
    other["score"] = fc_stories.score_story(other)
    cands.append(other)
    picked = fc_stories.select_stories(cands, target=4, per_org_cap=3)
    assert len(picked) == 4
    assert sum(1 for s in picked if s["org"] == "monopoly") == 3
    assert any(s["org"] == "indie" for s in picked)


def test_detect_all_end_to_end_with_mention_once(capsys):
    base_repos = {"acme": {"steady": _repo(stars=1000, created_at="2020-01-01T00:00:00Z")}}
    cur_repos = {
        "acme": {
            "steady": _repo(stars=1000, created_at="2020-01-01T00:00:00Z"),
            "brand-new": _repo(created_at="2026-08-22T00:00:00Z"),
        }
    }
    _write_snap("2026-08-18", base_repos)
    _write_snap("2026-08-25", cur_repos)
    cfg = _cfg(orgs=[{"name": "acme", "filter": "none"}])

    out = fc_stories.detect_all("2026-08-25", cfg)
    assert out["baseline_date"] == "2026-08-18"
    assert [s["key"] for s in out["stories"]] == ["NEW_REPO:acme/brand-new:new"]
    assert out["thin"] is True  # 1 < min_stories_per_episode (2)

    fc_stories.mark_reported([s["key"] for s in out["stories"]], "2026-08-25", "spotify:episode:e")
    again = fc_stories.detect_all("2026-08-25", cfg)
    assert again["stories"] == []


def test_cli_detect_prints_contract_and_mark_round_trips(tmp_path, capsys):
    fc_common.atomic_write_json(fc_common.config_path(),
                                {"orgs": [{"name": "acme", "filter": "none"}]})
    _write_snap("2026-08-25", {"acme": {"n": _repo(created_at="2026-08-22T00:00:00Z")}})
    rc = fc_stories.main(["detect", "--date", "2026-08-25"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert set(out) == {"run_date", "baseline_date", "thin", "stories"}

    f = tmp_path / "stories.json"
    f.write_text(json.dumps(out))
    rc = fc_stories.main(["mark", "--stories", str(f), "--episode-uri", "spotify:episode:e9"])
    assert rc == 0
    assert fc_stories.load_reported()[out["stories"][0]["key"]]["episode_uri"] == "spotify:episode:e9"
```

- [ ] **Step 2: Run to verify failure**, then **Step 3: implement** exactly the Produces list. `detect_all` core:

```python
def detect_all(run_date: str, config: dict) -> dict:
    dates = list_snapshot_dates()
    usable = [d for d in dates if d <= run_date]
    if not usable:
        fc_common.die(f"no snapshot at or before {run_date} — run fc_snapshot.py first")
    cur_date = usable[-1]
    if cur_date != run_date:
        fc_common.log(f"warn: no snapshot for {run_date}, using {cur_date}")
    snap = load_snapshot(cur_date)
    if snap is None:
        fc_common.die(f"snapshot {cur_date} is unreadable")
    cur = org_views(snap)
    baseline_date = pick_baseline(usable, run_date, config["lookback_days"])
    base_snap = load_snapshot(baseline_date) if baseline_date else None
    base = org_views(base_snap) if base_snap else None
    cutoff = baseline_date or (
        dt.date.fromisoformat(run_date) - dt.timedelta(days=config["lookback_days"])
    ).isoformat()

    cands = [
        *detect_new_repos(cur, cutoff),
        *detect_notable_forks(cur, cutoff, run_date),
        *detect_archived(cur, base),
        *detect_going_stale(cur, run_date, config),
        *detect_star_surge(cur, base, baseline_date, run_date, config),
        *detect_releases(snap, cutoff, cur, config),
    ]
    cands = filter_new(cands, load_reported())
    for s in cands:
        s["score"] = round(score_story(s), 2)
    stories = select_stories(cands, config["target_stories_per_episode"],
                             config["per_org_story_cap"])
    return {"run_date": run_date, "baseline_date": baseline_date,
            "thin": len(stories) < config["min_stories_per_episode"],
            "stories": stories}
```

`main` uses `argparse` subcommands; `detect` calls `fc_common.load_config()` (the CLI test above writes a minimal config first for exactly this reason); `mark` needs no config. Print the JSON with `json.dumps(...)` as the FINAL stdout line. Scoring note: the spec's "recency" term is deliberately omitted from `score_story` — every detector already gates on a recency window, so a score term would double-count it.

- [ ] **Step 4: Run tests + suite + lint** — all green.

- [ ] **Step 5: Commit**

```bash
git add skills/frontier-commits/fc_stories.py tests/test_fc_stories.py
git commit -m "feat(frontier-commits): scoring, per-org cap, detect/mark CLI"
```

### Task 7: `fc_snapshot.py` — labs.json aggregation, hash-gated R2 publish, CLI

**Files:**
- Modify: `skills/frontier-commits/fc_snapshot.py`
- Test: `tests/test_fc_snapshot.py` (append)

**Interfaces:**
- Consumes: Task 2/3 functions; `fc_stories.list_snapshot_dates`, `fc_stories.load_snapshot`, `fc_stories.org_views` (import `fc_stories` — same directory, resolves via the conftest sys.path in tests and via sibling import at runtime).
- Produces: `build_labs_json(config: dict, run_date: str) -> dict` (spec §4.6 schema, `schema_version: 1`); `labs_content_hash(labs: dict) -> str` (sha256 of the canonical dump EXCLUDING `generated_at`); `publish_labs(labs: dict, config: dict, client_factory=None) -> str` returning one of `"published" | "unchanged" | "skipped" | "failed"`; `fire_hook(url: str) -> None` (urllib POST, 10 s timeout, never raises — mirror of render.py's `fire_pages_hook`); `main(argv) -> int` with `--date` and `--dry-run`.

Semantics:
- `build_labs_json` uses today's snapshot plus history: `new_repos` = created within 30 days (top 10 per org by created_at desc); `movers` = stars delta vs the newest snapshot ≥ 6 days older (top 10 by |delta|, only if such a snapshot exists); `stale_watch` = `stars >= stale_min_stars` and `days_since_push >= 120` (top 10 by days desc); `archived_recent` = archived flips vs the oldest snapshot within 30 days; `totals` = `{"repos": n, "active_30d": pushed-within-30d count, "stars": sum}`. `display` name: `{"anthropics": "Anthropic", "openai": "OpenAI", "xai-org": "xAI", "google": "Google", "google-deepmind": "Google DeepMind"}.get(org, org)`.
- `publish_labs`: R2 unconfigured (no bucket or missing creds) → `"skipped"` (the web page is optional — same posture as render.py's R2 three-state `absent`); hash equal to `labs_hash_path()` content → `"unchanged"` (no PUT, no hook); else boto3 PUT of `config["labs_manifest_name"]` with `content_type="application/json", cache_control="no-cache"`, write the hash file, fire the hook (resolved from `fc_common.load_r2_secrets()["PAGES_DEPLOY_HOOK_URL"]` if present), return `"published"`. Any exception → log + `"failed"`, never raises. `client_factory` defaults to a boto3 lazy-import identical to render.py's `r2_client` (copy the endpoint-URL pattern; do NOT import render.py — the skills stay independent).
- `main`: resolve date (`--date` or today); build snapshot (Task 2/3), write it, prune retention; build labs.json; `--dry-run` → skip publish AND hook, print the plan; final line `SNAPSHOT ok date=<d> orgs=<ok>/<total> repos=<n> releases=<n> labs=<status>`; on `SweepFailed`/fatal → `SNAPSHOT FAILED <reason>` and exit 1.

- [ ] **Step 1: Write the failing tests** (append; reuse `_write_snap` from test_fc_stories via a local copy — tests files stay independent, so re-define the small helper here)

```python
class FakeS3:
    def __init__(self):
        self.puts = []

    def put_object(self, **kw):
        self.puts.append(kw)


def _snap_file(date, org_repos):
    d = fc_common.snapshot_dir()
    d.mkdir(parents=True, exist_ok=True)
    snap = {"schema_version": 1, "date": date,
            "orgs": {o: {"fetched_at": "", "repos": r, "releases": []}
                     for o, r in org_repos.items()},
            "errors": {}}
    (d / f"{date}.json").write_text(json.dumps(snap))


def test_build_labs_json_movers_and_new_repos():
    _snap_file("2026-08-18", {"acme": {"hot": _raw_rec(stars=100)}})
    _snap_file("2026-08-25", {"acme": {
        "hot": _raw_rec(stars=900),
        "fresh": _raw_rec(created_at="2026-08-20T00:00:00Z"),
    }})
    labs = fc_snapshot.build_labs_json(_cfg(orgs=[{"name": "acme", "filter": "none"}]),
                                       "2026-08-25")
    lab = labs["labs"][0]
    assert labs["schema_version"] == 1
    assert lab["org"] == "acme"
    assert [m["name"] for m in lab["movers"]] == ["hot"]
    assert lab["movers"][0]["delta_7d"] == 800
    assert [n["name"] for n in lab["new_repos"]] == ["fresh"]


def test_publish_labs_hash_gate_and_statuses(monkeypatch):
    labs = {"schema_version": 1, "generated_at": "T1", "labs": []}
    cfg = _cfg(r2_bucket="clodcast", r2_public_base_url="https://x")
    monkeypatch.setattr(fc_common, "load_r2_secrets", lambda: {
        "R2_ACCESS_KEY_ID": "a", "R2_SECRET_ACCESS_KEY": "s", "R2_ACCOUNT_ID": "id",
    })
    s3 = FakeS3()
    assert fc_snapshot.publish_labs(labs, cfg, client_factory=lambda c: s3) == "published"
    assert s3.puts[0]["Key"] == "labs.json"
    # same content, different generated_at -> unchanged, no second PUT
    labs2 = dict(labs, generated_at="T2")
    assert fc_snapshot.publish_labs(labs2, cfg, client_factory=lambda c: s3) == "unchanged"
    assert len(s3.puts) == 1


def test_publish_labs_skips_when_unconfigured():
    assert fc_snapshot.publish_labs({"labs": []}, _cfg()) == "skipped"


def test_cli_dry_run_touches_no_network(monkeypatch, capsys):
    fc_common.atomic_write_json(fc_common.config_path(),
                                {"orgs": [{"name": "acme", "filter": "none"}]})

    def runner(cmd, **kw):
        class P:
            returncode = 0
            stdout = json.dumps([_raw("only")])
            stderr = ""

        return P()

    def boom(*a, **kw):
        raise AssertionError("network touched in --dry-run")

    monkeypatch.setattr(fc_snapshot, "publish_labs", boom)
    monkeypatch.setattr(fc_snapshot, "fire_hook", boom)
    monkeypatch.setattr(fc_snapshot, "_default_runner", runner)
    rc = fc_snapshot.main(["--date", "2026-08-25", "--dry-run"])
    assert rc == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("SNAPSHOT ok date=2026-08-25")
    assert "labs=dry-run" in last
```

`_raw_rec` builds a STORED record (Task 2 `repo_record` output shape), vs `_raw` which builds GitHub-API-shaped input — define it at the top of the test file:

```python
def _raw_rec(**kw):
    rec = {
        "stars": 10, "forks": 1, "open_issues": 0,
        "created_at": "2026-08-01T00:00:00Z", "pushed_at": "2026-08-20T00:00:00Z",
        "archived": False, "fork": False, "topics": [], "description": "a repo",
        "language": "Python",
    }
    rec.update(kw)
    return rec
```

(`_default_runner` is a module-level alias for `subprocess.run` in `fc_snapshot.py` that `main` uses, so tests can swap it without touching `subprocess`.)

- [ ] **Step 2: Run to verify failure**, **Step 3: implement** per the Produces list. `main` skeleton:

```python
_default_runner = subprocess.run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    date_iso = args.date or dt.date.today().isoformat()
    config = fc_common.load_config()
    try:
        snap = build_snapshot(config, date_iso, runner=_default_runner)
    except SweepFailed as e:
        print(f"SNAPSHOT FAILED {e}")
        return 1
    write_snapshot(snap)
    removed = prune_snapshots(config["snapshot_retention_days"], dt.date.fromisoformat(date_iso))
    if removed:
        fc_common.log(f"pruned {len(removed)} old snapshots")
    labs = build_labs_json(config, date_iso)
    labs_status = "dry-run" if args.dry_run else publish_labs(labs, config)
    n_repos = sum(len(v["repos"]) for v in snap["orgs"].values())
    n_rel = sum(len(v["releases"]) for v in snap["orgs"].values())
    print(f"SNAPSHOT ok date={date_iso} orgs={len(snap['orgs'])}/{len(config['orgs'])} "
          f"repos={n_repos} releases={n_rel} labs={labs_status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests + suite + lint** — all green. **Step 5: Commit**

```bash
git add skills/frontier-commits/fc_snapshot.py tests/test_fc_snapshot.py
git commit -m "feat(frontier-commits): labs.json aggregation, hash-gated R2 publish, snapshot CLI"
```

### Task 8: launchd job + first real sweep (manual verification gate)

**Files:**
- Create: `skills/frontier-commits/launchd/com.cortech.frontier-commits-snapshot.plist`
- Config (host, not committed): `~/.config/frontier-commits/config.json`

**Interfaces:**
- Consumes: Task 7's CLI.
- Produces: real snapshots accumulating daily at `~/.config/frontier-commits/snapshots/` — the long pole for every trend feature, which is why this ships before P2.

No unit tests — this task's verification is real execution. **Note for the executor:** the plist points at the MAIN clone (`/Users/cory/clodcast/...`), not this worktree and not the version-keyed plugin cache (which is wiped on every release — the same trap as the stale-plugin-cache incident). Until P1 merges to main, point it at the worktree path and switch to the main-clone path as a step in Task 15.

- [ ] **Step 1: Write the real host config** `~/.config/frontier-commits/config.json`:

```json
{
  "orgs": [
    {"name": "anthropics", "filter": "none"},
    {"name": "openai", "filter": "none"},
    {"name": "xai-org", "filter": "none"},
    {"name": "google", "filter": "ai"},
    {"name": "google-deepmind", "filter": "none"}
  ],
  "r2_bucket": "clodcast",
  "r2_public_base_url": "https://clodcast.cortech.online"
}
```

(Everything else rides DEFAULT_CONFIG. `show_id` is added in Task 13 — the snapshot path never needs it.)

- [ ] **Step 2: First real sweep, by hand**

Run: `python3 skills/frontier-commits/fc_snapshot.py --dry-run`
Expected: `SNAPSHOT ok date=<today> orgs=5/5 repos=<n> releases=<n> labs=dry-run` with n roughly 700–900 (anthropics ~102 + openai ~268 + xai-org ~9 + google filtered subset + google-deepmind ~400 — sanity-check against the recon numbers in the spec §2 table). Inspect `~/.config/frontier-commits/snapshots/<today>.json`: spot-check that `google` contains `langextract` (allowlist) and does NOT contain `guava` (non-AI). Then run once WITHOUT `--dry-run` and confirm `labs=published` and `https://clodcast.cortech.online/labs.json` serves the file (`curl -s https://clodcast.cortech.online/labs.json | head -c 400`).

- [ ] **Step 3: Write the plist** (committed to the repo as the canonical copy):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.cortech.frontier-commits-snapshot</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/cory/.pyenv/shims/python3</string>
    <string>/Users/cory/clodcast/skills/frontier-commits/fc_snapshot.py</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>6</integer><key>Minute</key><integer>15</integer></dict>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin</string></dict>
  <key>StandardOutPath</key>
  <string>/Users/cory/Library/Logs/frontier-commits/snapshot.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/cory/Library/Logs/frontier-commits/snapshot.err.log</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
```

(pyenv shim = Python 3.13.7 with boto3 importable — verified on this host; `/opt/homebrew/bin` on PATH gives the job `gh` 2.95. launchd runs a missed StartCalendarInterval once on wake, so a sleeping laptop still snapshots.)

- [ ] **Step 4: Install and kickstart**

```bash
mkdir -p ~/Library/Logs/frontier-commits
cp skills/frontier-commits/launchd/com.cortech.frontier-commits-snapshot.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cortech.frontier-commits-snapshot.plist
launchctl kickstart gui/$(id -u)/com.cortech.frontier-commits-snapshot
```

Then check `~/Library/Logs/frontier-commits/snapshot.log` for the `SNAPSHOT ok` line. **The thing actually being verified here is gh keyring auth under launchd** — gh stores its token in the login keychain, which is normally unlocked in a gui session, but if the log shows a gh auth error instead: add `"GH_TOKEN": "<token from gh auth token>"` to `~/.config/frontier-commits/secrets.json` (create it `chmod 600`) — `fc_common._gh_env` already picks it up — and kickstart again.

- [ ] **Step 5: Commit + open the P1 PR**

```bash
git add skills/frontier-commits/launchd/com.cortech.frontier-commits-snapshot.plist
git commit -m "feat(frontier-commits): daily snapshot launchd job"
```

Open the P1 PR (Tasks 1–8) per the repo's issue→PR loop: full `pytest` / `ruff` counts and the real `SNAPSHOT ok` line in the description; squash-merge through the existing main gate once CI is green. From merge onward the launchd job runs against the main clone (flip the plist path now if it still points at the worktree).

---

## Phase P2 — the weekly episode

### Task 9: `fc_script_plan.py` — week-seeded rotation

**Files:**
- Create: `skills/frontier-commits/fc_script_plan.py`
- Test: `tests/test_fc_script_plan.py`

**Interfaces:**
- Consumes: nothing (pure).
- Produces: `week_index(date_iso: str) -> int` — the ISO week's Monday ordinal `// 7`, a CONTIGUOUS counter: consecutive real-world weeks give consecutive integers across every year boundary. (Superseded excerpt: the original `y * 53 + w` formula stepped by 2 at 52-week ISO year ends, skipping a rotation row — caught by the n5 critic's mutation testing); `INTRO_MODES_W: dict[str, str]` (5), `OUTRO_MODES_W: dict[str, str]` (3), `STORY_SHAPES_W: dict[str, str]` (6), `MOVES_W: dict[str, str]` (6, `"cold"` maps to `""`); `SHAPE_ORDERS_W: tuple[tuple[int, ...], ...]` (6×6, below); `TRANSITION_ROW_OFFSET_W = 3`; `segment_shape(week: int, pos: int) -> str`; `segment_transition(week: int, junction: int) -> str`; `LEAD_BAND = (1100, 1500)`, `BODY_BAND = (700, 1100)`, `TREND_BAND = (450, 700)`; `band_for(pos: int, n_stories: int) -> tuple[int, int]` (pos 0 → LEAD, else BODY; the trend-watch close is not a story position and always uses TREND_BAND); `build_plan(date_iso: str, n_stories: int) -> dict` (the Task-header JSON contract); `main(argv) -> int` (`plan --date D --stories N` prints the JSON).

**The Latin square is fixed — do NOT regenerate or "improve" it.** It was machine-generated and verified for: Latin rows AND columns, cyclic consecutive-row disagreement in every column, and 6 pairwise-distinct rotation signatures (so adjacencies genuinely vary — the property the daily show's stride bug faked). This repo's history (CLAUDE.md, PR #108) is exactly why this table is data, not arithmetic:

```python
SHAPE_ORDERS_W = (
    (3, 1, 2, 4, 0, 5),
    (4, 3, 0, 1, 5, 2),
    (1, 0, 5, 2, 3, 4),
    (0, 4, 1, 5, 2, 3),
    (5, 2, 4, 3, 1, 0),
    (2, 5, 3, 0, 4, 1),
)
```

Bank contents (verbatim — these strings are the product; SKILL.md re-renders them as tables and the Task 10 drift tests tie the two):

```python
INTRO_MODES_W = {
    "ledger": "Open with the week's ledger: \"Week of <date>. <N> stories across "
              "<the labs involved>.\" Then the single most telling number of the week.",
    "headline": "Open cold on the week's biggest story in one sentence, then pull back: "
                "date, story count, rundown.",
    "question": "Open with the question this week's activity raises, then the date and "
                "the rundown.",
    "pattern": "Open by naming a pattern that honestly connects two or more of this "
               "week's stories, then the rundown. No honest pattern? Use the ledger "
               "opening instead — never manufacture one.",
    "time-capsule": "Open by contrasting this week with a specific earlier state "
                    "(\"A month ago this org was quiet - this week...\"), then the rundown.",
}

OUTRO_MODES_W = {
    "plain": "Plain sign-off: a simple thanks. No new content.",
    "watchlist": "Name one or two repos to watch next week and the observable that "
                 "would settle the question. Then sign off.",
    "callback": "Close by paying off the cold open in one line, then sign off. No new facts.",
}

STORY_SHAPES_W = {
    "artifact-first": "Open with the concrete thing that appeared - the repo, what is "
                      "actually in it - then what it might mean.",
    "question-first": "Open with the question this repo's existence raises, then walk "
                      "the evidence toward the best available answer.",
    "timeline": "Walk the observable sequence in order - created, pushed, released, "
                "went quiet - then read the trajectory.",
    "cross-lab": "Open by placing this against another lab's position in the same space, "
                 "then the specifics. Only when the comparison is real.",
    "numbers-first": "Open with the most telling number - stars, days since a push, "
                     "repo counts - then the story behind it.",
    "zoom-out": "Open one level up - what this kind of repo says about where the lab "
                "is headed - then drop into the specifics.",
}

MOVES_W = {
    "cold": "",
    "pivot": "Name the change of subject in a few words, then go.",
    "echo": "Mark that this story rhymes with the previous one: same pattern, new lab.",
    "contrast": "Mark the opposition to the previous story in one clause, then go.",
    "escalate": "Frame this story as raising the stakes of the previous one.",
    "zoom": "Shift altitude from the previous story - from one repo to the big picture, "
            "or back down.",
}
```

Lookup mirrors orchestrate.py's `_latin_pick`: `order = SHAPE_ORDERS_W[(week + row_offset) % 6]; names[order[pos % 6]]` with `row_offset=0` for shapes and `TRANSITION_ROW_OFFSET_W` for moves (junction j is the gap BEFORE story j, j ≥ 1; `build_plan` sets segment 0's move to `"cold"` always).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fc_script_plan.py
import json

import fc_script_plan as sp


def test_square_is_latin_both_ways():
    n = len(sp.SHAPE_ORDERS_W)
    for row in sp.SHAPE_ORDERS_W:
        assert sorted(row) == list(range(n))
    for c in range(n):
        assert sorted(r[c] for r in sp.SHAPE_ORDERS_W) == list(range(n))


def test_no_position_holds_its_shape_two_weeks_running():
    for week in range(1, 320):
        for pos in range(12):  # includes wrap positions
            assert sp.segment_shape(week, pos) != sp.segment_shape(week + 1, pos)


def test_every_position_sees_every_shape_within_one_bank_cycle():
    n = len(sp.STORY_SHAPES_W)
    for start in range(1, 30):
        for pos in range(n):
            seen = {sp.segment_shape(start + k, pos) for k in range(n)}
            assert seen == set(sp.STORY_SHAPES_W)


def test_rows_are_pairwise_non_rotational():
    n = len(sp.SHAPE_ORDERS_W)
    sigs = {tuple((x - row[0]) % n for x in row) for row in sp.SHAPE_ORDERS_W}
    assert len(sigs) == n


def test_transitions_are_not_locked_to_the_shape_rotation():
    # Compare the UNDERLYING square rows, not the rendered names — shape and
    # move banks are disjoint strings, so comparing names can never fail.
    for week in range(1, 20):
        shape_idx = [sp.SHAPE_ORDERS_W[week % 6][p] for p in range(6)]
        move_idx = [sp.SHAPE_ORDERS_W[(week + sp.TRANSITION_ROW_OFFSET_W) % 6][p] for p in range(6)]
        assert shape_idx != move_idx
    assert sp.TRANSITION_ROW_OFFSET_W % len(sp.SHAPE_ORDERS_W) != 0


def test_build_plan_is_deterministic_and_shaped():
    a = sp.build_plan("2026-08-31", 4)
    b = sp.build_plan("2026-08-31", 4)
    assert a == b
    assert len(a["segments"]) == 4
    assert a["segments"][0]["move"] == "cold"
    assert a["segments"][0]["band"] == list(sp.LEAD_BAND) or a["segments"][0]["band"] == sp.LEAD_BAND
    assert a["intro_mode"] in sp.INTRO_MODES_W and a["outro_mode"] in sp.OUTRO_MODES_W
    assert sp.build_plan("2026-09-07", 4) != a  # next week differs


def test_cli_prints_json_contract(capsys):
    assert sp.main(["plan", "--date", "2026-08-31", "--stories", "3"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert {"week_row", "intro_mode", "intro_text", "outro_mode", "outro_text",
            "segments"} <= set(out)
```

- [ ] **Step 2: Run to verify failure** → `ModuleNotFoundError: fc_script_plan`.

- [ ] **Step 3: Implement** — banks + square verbatim from above; `build_plan` computes `week = week_index(date_iso)` and returns `{"week_row": week % 6, "intro_mode": ..., "intro_text": ..., "outro_mode": ..., "outro_text": ..., "segments": [...]}` where `intro = list(INTRO_MODES_W)[week % 5]`, `outro = list(OUTRO_MODES_W)[week % 3]`, and each segment dict is `{"pos": i, "shape": ..., "shape_text": STORY_SHAPES_W[shape], "move": "cold" if i == 0 else segment_transition(week, i), "move_text": MOVES_W[move], "band": list(band_for(i, n))}`. Dict iteration order is insertion order (guaranteed) — the banks above are ordered deliberately; do not sort them.

- [ ] **Step 4: Run tests + suite + lint.** **Step 5: Commit**

```bash
git add skills/frontier-commits/fc_script_plan.py tests/test_fc_script_plan.py
git commit -m "feat(frontier-commits): week-seeded rotation with verified 6x6 Latin square"
```

### Task 10: SKILL.md, prompts, drift tests

**Files:**
- Create: `skills/frontier-commits/SKILL.md`
- Create: `skills/frontier-commits/prompts/weekly.md`
- Create: `skills/frontier-commits/prompts/write_story.md`
- Test: `tests/test_fc_skill_md.py`

**Interfaces:**
- Consumes: every CLI and bank above. Plugin discovery is directory convention (`skills/<name>/SKILL.md` + frontmatter) — NO plugin.json change is needed for the skill to exist.
- Produces: the canonical "Unattended weekly run" procedure (single home), the script template Claude follows, and the placeholder contract for per-story prompts.

**SKILL.md frontmatter** (mirror daily-podcast's):

```yaml
---
id: frontier-commits
name: frontier-commits
description: Use when the user asks to ship the Frontier Commits weekly podcast — turns the frontier labs' GitHub org activity (new repos, releases, archivals, star trends) into a speculation-forward Spotify episode via the daily snapshot store and render.py. Skips the standard production interview because defaults are pre-set.
enabled: true
---
```

**SKILL.md required sections** (in order): `# Frontier Commits` (what the show is; the speculation register with the openai/git example) · `## Layout` (file map) · `## Data layer` (snapshot store, launchd job, fc_snapshot/fc_stories CLIs and their stdout contracts) · `## Story types` (a table naming all six TYPE names with trigger + stage semantics, incl. the NOTABLE_FORK actively-pushed simplification) · `## Script template` (below) · `## Speculation rules` (the three hard rules from spec §4.4 verbatim, plus: never manufacture a connection; segments end on substance, never a pointer to the source URL) · `## Manifest` (a Form-2-style JSON example — standard render.py manifest plus `"show_id"` from `~/.config/frontier-commits/config.json` and `"r2_manifest_name": "manifest-frontier-commits.json"`; title format `Frontier Commits — Week of <Month D, YYYY>`; strict 1:1 segment↔source_url mapping; voice defaults to house) · `## Show + state config` (config.json keys, reported.json semantics, shared covered.json note) · `## Unattended weekly run` (below) · `## Setup` (show creation, launchd install from Task 8, routine prompt from Task 15).

**Script template section** must present the banks as markdown tables in EXACTLY the daily SKILL.md's format (backticked cells) so the drift test can parse them, including the shape table with header row `| week % 6 | pos 0 | pos 1 | pos 2 | pos 3 | pos 4 | pos 5 |` — the test anchors on `| pos 0 |`. Seed framing line: ``**Seed:** `week` = ISO-week index of the run date (`isocalendar`), so a re-run of the same week rebuilds the same episode. Every index below is `week` modulo the bank size.`` Render the 6 rows from `SHAPE_ORDERS_W` by hand, then double-check against the code — the drift test will catch any transcription slip. Trend-watch close: a fixed final NON-story segment (TREND_BAND length) reading 2–3 numbers from today's `labs.json` (biggest mover, longest quiet streak on the stale watch); it has `"source_url": null`.

**Unattended weekly run** — numbered procedure (the single home; schedulers carry only a trigger):

1. Resolve paths: `${CLAUDE_PLUGIN_ROOT}/skills/frontier-commits/` when set; when unset (known to happen under scheduled tasks) fall back to the path this SKILL.md was loaded from. Workdir: `$TMPDIR/frontier-commits-<date>/`.
2. `python3 fc_snapshot.py` — ensure today's snapshot exists (run it; if the sweep fails but a snapshot ≤ 2 days old exists, log and continue with it; otherwise print `FAILED no usable snapshot` and stop).
3. `python3 fc_stories.py detect --date <today>` → save stdout JSON to `<workdir>/stories.json`. If `"thin": true` → print `SKIPPED thin-week (<n> stories)` and exit 0. **No filler episodes.**
4. `python3 fc_script_plan.py plan --date <today> --stories <n>` → `<workdir>/plan.json`.
5. Per story, in its own subagent context (one story's material per context — the weekly analogue of the one-body-per-request invariant): read `prompts/write_story.md`, fill `<<TYPE>>/<<TITLE>>/<<URL>>/<<FACTS>>/<<SHAPE>>/<<MIN_CHARS>>/<<MAX_CHARS>>` from stories.json + plan.json, research the repo first (README via `gh api repos/<org>/<repo>/readme`, recent commits/releases), write the segment, return the JSON contract. A refused/failed story is dropped and logged; if survivors < `min_stories_per_episode` → `SKIPPED thin-week after drops`.
6. Write intro (assigned mode), segues (assigned moves — `cold` means no segue text), trend-watch close (from labs.json), outro (assigned mode).
7. Assemble `<workdir>/manifest.json`; render in the BACKGROUND (`render.py --manifest ... --workdir <workdir>` — the 10-minute foreground Bash cap SIGTERMs a poll; monitor the log instead).
8. On render success (READY): `python3 fc_stories.py mark --stories <workdir>/stories.json --episode-uri <uri>`.
9. Final line: `SHIPPED <uri> - <title> - <n> chapters - <dur>s - r2=<ok|skipped|FAILED>` / `SKIPPED <reason>` / `FAILED <reason>`.

**prompts/weekly.md** — a stub in the exact daily.md mold: points at SKILL.md "Unattended weekly run", explains the one-home rule, gives the scheduler trigger snippet, < 40 lines.

**prompts/write_story.md** — full content:

```markdown
You are writing ONE segment of "Frontier Commits", a weekly podcast reading the
frontier AI labs' public GitHub activity. Spoken audio: no headings, no lists,
no URLs read aloud.

STORY
- type: <<TYPE>>
- repo: <<TITLE>>
- url: <<URL>>
- observable facts (JSON): <<FACTS>>

RESEARCH FIRST: read the repo's README (`gh api repos/<<TITLE>>/readme`) and its
recent commit/release activity before writing. Everything you assert as fact
must come from FACTS or what you just read.

SHAPE — this segment's assigned opening. Other segments this week were assigned
different ones; follow yours:
<<SHAPE>>

RULES
- <<MIN_CHARS>> to <<MAX_CHARS>> characters, one paragraph, spoken style. Stay
  inside the band — it is this segment's slot in the episode's pacing.
- Speculation is the genre, and it is governed: label speculation as speculation
  ("reads like", "the obvious guess is", "if this is X, then..."), anchor every
  speculative claim to at least one observable (creation date, fork parent,
  commit cadence, description, star trajectory), and never state a guess as
  confirmed fact.
- The actor is the lab, never a named individual's motives.
- Never manufacture a connection to another story.
- End on substance — never with a pointer to the source or "check it out".

OUTPUT: print exactly ONE JSON object as your final output and nothing after it:
{"ok": true, "segment": "<the spoken segment>", "source_url": "<<URL>>"}
If you genuinely cannot write this story, print instead:
{"ok": false, "reason": "<short reason>"}
```

- [ ] **Step 1: Write the failing drift tests**

```python
# tests/test_fc_skill_md.py
from pathlib import Path

import fc_script_plan as sp
import fc_stories

FC_DIR = Path(__file__).resolve().parent.parent / "skills" / "frontier-commits"


def _skill_text():
    return (FC_DIR / "SKILL.md").read_text()


def test_skill_md_shape_table_matches_the_code():
    names = list(sp.STORY_SHAPES_W)
    lines = _skill_text().splitlines()
    header = next((i for i, ln in enumerate(lines) if "| pos 0 |" in ln), None)
    assert header is not None, "SKILL.md lost the per-position shape table"
    body = lines[header + 2 : header + 2 + len(sp.SHAPE_ORDERS_W)]
    for row, (order, line) in enumerate(zip(sp.SHAPE_ORDERS_W, body, strict=True)):
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        assert cells[0] == str(row)
        assert cells[1:] == [names[i] for i in order]


def test_skill_md_documents_every_shape_mode_move_and_story_type():
    skill = _skill_text()
    for name in (*sp.STORY_SHAPES_W, *sp.INTRO_MODES_W, *sp.OUTRO_MODES_W, *sp.MOVES_W):
        assert name in skill, f"SKILL.md never mentions {name!r}"
    for t in fc_stories.TYPE_PRIORITY:
        assert t in skill, f"SKILL.md never mentions story type {t!r}"


def test_weekly_prompt_stays_a_stub():
    stub = (FC_DIR / "prompts" / "weekly.md").read_text()
    assert "SKILL.md" in stub
    for marker in ("fc_stories.py detect", "Assemble", "trend-watch"):
        assert marker not in stub, f"prompts/weekly.md re-inlined the procedure: {marker!r}"
    assert len(stub.splitlines()) < 40


def test_write_story_prompt_declares_the_placeholders():
    text = (FC_DIR / "prompts" / "write_story.md").read_text()
    for ph in ("<<TYPE>>", "<<TITLE>>", "<<URL>>", "<<FACTS>>",
               "<<SHAPE>>", "<<MIN_CHARS>>", "<<MAX_CHARS>>"):
        assert ph in text


def test_skill_md_pins_the_frontier_manifest_name():
    assert "manifest-frontier-commits.json" in _skill_text()
```

- [ ] **Step 2: Run to verify failure** (missing files). **Step 3:** write the three files per the outlines above. **Step 4:** tests + suite + lint green. **Step 5: Commit**

```bash
git add skills/frontier-commits/SKILL.md skills/frontier-commits/prompts/ tests/test_fc_skill_md.py
git commit -m "feat(frontier-commits): SKILL.md, weekly stub, write_story prompt, drift tests"
```

### Task 11: Dry-run rehearsal (manual P2 gate)

**Files:** none committed (workdir artifacts only).

Follow SKILL.md's "Unattended weekly run" end to end IN THIS SESSION with today's real snapshots, but pass `--dry-run` to render.py at step 7 (and skip step 8 — no episode URI exists). If real story candidates are thin this early (likely: ARCHIVED/STAR_SURGE need a week of history), run `fc_stories.py detect` with `--lookback-days 14` for rehearsal purposes only.

Acceptance (all required):
- [ ] `stories.json` contains ≥ 2 plausible stories with correct keys and facts.
- [ ] Every segment respects its assigned shape and band, labels speculation as speculation, anchors it to an observable, and ends on substance.
- [ ] `render.py --dry-run` produces mp3 + cover + timeline.json; listen to at least two segments (house voice, no truncation, segues land).
- [ ] The manifest carries `show_id` (placeholder OK pre-Task-13) and `r2_manifest_name: "manifest-frontier-commits.json"` and passes `validate_manifest` (unknown key ignored today; validated after Task 12).
- [ ] Report the rehearsal artifacts' paths + a verdict to Cory before starting P3.

Open the P2 PR (Tasks 9–11) with test counts + rehearsal evidence.

---

## Phase P3 — render change, show, first ship, routine

### Task 12: `render.py` — `r2_manifest_name`

**Files:**
- Modify: `skills/daily-podcast/render.py` (`validate_manifest` ~L613 block end; `maybe_publish_r2` ~L2100)
- Test: `tests/test_r2.py` + `tests/test_render.py` (append)

**Interfaces:**
- Consumes: existing `validate_manifest`, `maybe_publish_r2`, `_r2_get_manifest`.
- Produces: optional manifest key `r2_manifest_name` — default behavior byte-identical (`"manifest.json"`).

- [ ] **Step 1: Write the failing tests.** In `tests/test_render.py` (validation) and `tests/test_r2.py` (publish path — reuse the existing `FakeS3` + `maybe_publish_r2` test scaffolding in that file; copy the setup from the nearest existing `maybe_publish_r2` test):

```python
# tests/test_render.py additions
import pytest


def _minimal_manifest(**kw):
    m = {"title": "T", "summary": "S", "segments": [{"text": "x" * 40, "source_url": None}]}
    m.update(kw)
    return m


def test_r2_manifest_name_accepts_bare_json_filename():
    render.validate_manifest(_minimal_manifest(r2_manifest_name="manifest-frontier-commits.json"))


@pytest.mark.parametrize("bad", [
    "../evil.json", "a/b.json", "manifest.txt", "", 7, "no-extension",
])
def test_r2_manifest_name_rejects_paths_and_non_json(bad):
    with pytest.raises(SystemExit):
        render.validate_manifest(_minimal_manifest(r2_manifest_name=bad))
```

```python
# tests/test_r2.py additions — self-contained: fakes the client and the manifest
# GET so no FakeS3 get_object semantics are assumed.
class _RecordingS3:
    def __init__(self):
        self.puts = []

    def put_object(self, **kw):
        self.puts.append(kw)


def _publish(monkeypatch, tmp_path, manifest):
    _clear_r2_env(monkeypatch)
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    s3 = _RecordingS3()
    monkeypatch.setattr(render, "r2_client", lambda cfg: s3)
    monkeypatch.setattr(render, "_r2_get_manifest", lambda client, bucket, key="manifest.json": [])
    monkeypatch.setattr(render, "mp3_duration_ms", lambda p: 123_000)
    monkeypatch.setattr(render, "resolve_pages_hook_url", lambda config: None)
    mp3 = tmp_path / "e.mp3"
    mp3.write_bytes(b"x" * 100)
    status = render.maybe_publish_r2(
        {"r2_bucket": "clodcast", "r2_public_base_url": "https://audio.example"},
        episode_mp3=mp3, cover=None, timeline={"items": []},
        manifest=manifest, description="<p>d</p>", episode_uri="spotify:episode:x",
    )
    return status, s3


def test_publish_uses_default_manifest_key_when_absent(monkeypatch, tmp_path):
    status, s3 = _publish(monkeypatch, tmp_path,
                          {"title": "T", "summary": "S", "date": "2026-08-25"})
    assert status == render.R2_PUBLISHED
    json_keys = [p["Key"] for p in s3.puts if p["Key"].endswith(".json")]
    assert json_keys == ["manifest.json"]


def test_publish_routes_to_named_manifest_and_leaves_default_alone(monkeypatch, tmp_path):
    status, s3 = _publish(monkeypatch, tmp_path, {
        "title": "T", "summary": "S", "date": "2026-08-25",
        "r2_manifest_name": "manifest-frontier-commits.json",
    })
    assert status == render.R2_PUBLISHED
    keys = [p["Key"] for p in s3.puts]
    assert "manifest-frontier-commits.json" in keys
    assert "manifest.json" not in keys
    assert any(k.endswith(".mp3") for k in keys)  # episode object key unaffected
```

(If `maybe_publish_r2`'s GET path calls `_r2_get_manifest` with the key positionally rather than by keyword, adjust the lambda's parameters to match the real call site — the implementation step threads `manifest_key` through it either way.)

- [ ] **Step 2: Run to verify failure** — validation tests fail (no such check yet); publish tests fail (key is hardcoded).

- [ ] **Step 3: Implement.** In `validate_manifest`, after the `voice_instruct`/`show_id` string checks (the `for field in ("voice_instruct", "show_id")` loop at ~L658):

```python
    # A second show publishes into the same R2 bucket; a bare-filename key keeps
    # its web feed out of the Field Notes manifest.json without touching paths.
    r2_name = manifest.get("r2_manifest_name")
    if r2_name is not None and (
        not isinstance(r2_name, str) or not re.fullmatch(r"[A-Za-z0-9._-]+\.json", r2_name)
    ):
        die('manifest "r2_manifest_name" must be a bare filename ending in .json')
```

In `maybe_publish_r2`, near the top: `manifest_key = manifest.get("r2_manifest_name") or "manifest.json"`; replace the literal `"manifest.json"` in the `_r2_get_manifest(...)` call and the manifest `_r2_put(...)` call with `manifest_key`. (`re` is already imported. The resume path needs no change — `_resume` passes the same manifest dict through.) Also check the dry-run R2 preview block at ~L3252–3264: if it prints the manifest URL/key, thread `manifest_key` there too.

- [ ] **Step 4: Full suite + lint** — `pytest` (report the full count) and ruff clean. The default-key test proves the daily show's behavior is untouched.

- [ ] **Step 5: Commit**

```bash
git add skills/daily-podcast/render.py tests/test_render.py tests/test_r2.py
git commit -m "feat(render): optional r2_manifest_name manifest key for second-show web feeds"
```

### Task 13: Create the show + finalize host config

**Files:** host config only (`~/.config/frontier-commits/config.json`).

- [ ] **Step 1:** Discover the exact show-creation syntax: `save-to-spotify shows --help` (and `--json shows` to list existing). Do not assume flags — the CLI has positional/flag quirks (see CLAUDE.md's 0.2.0 notes). If show creation requires a cover image, generate a one-off 3000×3000 JPEG with Pillow in the scratchpad (reuse `build_cover`'s Futura + palette approach, title "Frontier Commits").
- [ ] **Step 2:** Create the show named **Frontier Commits**. Record the returned `spotify:show:...` URI. (Fresh show = fresh 60-episode cap; weekly cadence = over a year of runway. Per-show cap means this never touches Field Notes' capacity.)
- [ ] **Step 3:** Add `"show_id": "spotify:show:<new>"` and `"show_name": "Frontier Commits"` to `~/.config/frontier-commits/config.json`.
- [ ] **Step 4:** Verify: `save-to-spotify --json episodes --show-id <new-id>` returns an empty list, not an error.

### Task 14: First real ship (manual gate)

**Files:** none committed.

- [ ] **Step 1:** Confirm P1+P2+Task 12 are merged and at least ~7 days of snapshots exist (the launchd job has been running since Task 8). If story volume is real, proceed; else wait for the natural Monday.
- [ ] **Step 2:** Follow SKILL.md "Unattended weekly run" end to end in-session — real render (background, monitor the log), real upload.
- [ ] **Step 3:** Verify ALL of: final `SHIPPED spotify:episode:... r2=ok` line · episode READY in `save-to-spotify --json episodes --show-id <frontier-id>` · `https://clodcast.cortech.online/manifest-frontier-commits.json` serves one entry · the Field Notes `manifest.json` is unchanged (fetch before/after, byte-compare) · `reported.json` now contains every shipped story key · `runs.jsonl` gained one `ready` record. Report each check's actual output.

### Task 15: Weekly routine + docs

**Files:**
- Modify: `README.md` (short "Frontier Commits" subsection: what it is, weekly cadence, /labs/ pointer)
- Modify: `.claude-plugin/plugin.json` (description now mentions both skills)

- [ ] **Step 1:** Flip the launchd plist's script path to the main clone (`/Users/cory/clodcast/skills/frontier-commits/fc_snapshot.py`) if Task 8 pointed it at the worktree; `launchctl bootout` + `bootstrap` to reload; kickstart once and confirm the log line.
- [ ] **Step 2:** Create the weekly scheduled task (Mondays 07:30 local — after the 06:15 snapshot) with this trigger-only prompt, verbatim:

```
You are an unattended invocation. Invoke the `frontier-commits` skill via the
Skill tool, then follow its "Unattended weekly run" section exactly, end to end.
Report its single SHIPPED/SKIPPED/FAILED line to stdout and exit.
```

- [ ] **Step 3:** README + plugin.json edits; run the drift tests (`pytest tests/test_fc_skill_md.py tests/test_reliability.py -k stub`) to confirm nothing re-inlined a procedure.
- [ ] **Step 4:** Commit + open the P3 PR (Task 12 code + Task 15 docs; Tasks 13–14 evidence in the PR body):

```bash
git add README.md .claude-plugin/plugin.json
git commit -m "docs(frontier-commits): README section + plugin description for the second show"
```

---

## Execution notes

- Branch: `claude/frontier-labs-podcast-080363` (this worktree). Three PRs — P1 (Tasks 1–8), P2 (9–11), P3 (12+15, with 13–14 evidence) — each merged through the existing main gate with test counts in the description.
- Tasks 8, 11, 13, 14 are manual verification gates: their "tests" are real executions with the expected outputs written above. Everything else is strict TDD.
- If any interface here fights reality (a gh field missing, a save-to-spotify flag renamed), fix the plan file in the same commit as the code so the plan stays truthful.
- **P4 (`/labs/` dashboard)** gets its own plan in `schmug/cortech.online` (fresh clone — the local `/Users/cory/portfolio` checkout is stale) once this plan's P1 has ≥ 1 week of snapshots and `labs.json` is live. Spec §4.6 is its input.
