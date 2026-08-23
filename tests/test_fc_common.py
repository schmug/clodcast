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
    assert cfg["target_stories_per_episode"] == 4  # file wins
    assert cfg["lookback_days"] == 7  # default fills in
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


def test_run_gh_env_falls_back_to_secrets_gh_token(monkeypatch, tmp_path):
    """Under launchd there is no shell env and gh's keyring can be locked, so a
    GH_TOKEN in secrets.json must reach the child process — but only when the
    env doesn't already carry one (env always wins; see fc_common._gh_env)."""
    monkeypatch.setattr(fc_common, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(fc_common, "DAILY_PODCAST_CONFIG_DIR", tmp_path / "dp")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    (tmp_path / "secrets.json").write_text(json.dumps({"GH_TOKEN": "from-secrets"}))
    seen = {}

    def runner(cmd, **kw):
        seen["env"] = kw.get("env")

        class P:
            returncode = 0
            stdout = "{}"
            stderr = ""

        return P()

    fc_common.run_gh(["api", "orgs/x"], runner=runner)
    assert seen["env"]["GH_TOKEN"] == "from-secrets"


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
