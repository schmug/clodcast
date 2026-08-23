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


def test_load_config_deep_merges_allowlist_per_org(tmp_path, monkeypatch):
    """A file allowlist overrides per org, never wholesale: setting openai's list
    must not wipe the default google allowlist — that list is the only mechanism
    rescuing non-AI-named google repos past the org's "ai" filter."""
    monkeypatch.setattr(fc_common, "CONFIG_DIR", tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"allowlist": {"openai": ["whisper"]}}))
    cfg = fc_common.load_config()
    assert cfg["allowlist"]["openai"] == ["whisper"]  # file entry wins for its org
    assert "langextract" in cfg["allowlist"]["google"]  # default org survives


def test_load_config_allowlist_override_replaces_only_that_org(tmp_path, monkeypatch):
    monkeypatch.setattr(fc_common, "CONFIG_DIR", tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"allowlist": {"google": ["custom-repo"]}}))
    cfg = fc_common.load_config()
    assert cfg["allowlist"]["google"] == ["custom-repo"]  # per-org replace, not append
    assert cfg["denylist"] == {}  # sibling key untouched


def test_load_config_null_allowlist_degrades_to_defaults(tmp_path, monkeypatch):
    """A file value of null must behave like an absent key ({}), not TypeError."""
    monkeypatch.setattr(fc_common, "CONFIG_DIR", tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"allowlist": None, "denylist": None}))
    cfg = fc_common.load_config()
    assert "langextract" in cfg["allowlist"]["google"]
    assert cfg["denylist"] == {}


@pytest.mark.parametrize("orgs", [None, 5])
def test_load_config_rejects_non_list_orgs(tmp_path, monkeypatch, orgs):
    """A malformed orgs container must reach die()'s schema pointer, never leak a
    raw TypeError traceback from the validation loop."""
    monkeypatch.setattr(fc_common, "CONFIG_DIR", tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"orgs": orgs}))
    with pytest.raises(SystemExit):
        fc_common.load_config()


def test_load_config_rejects_org_name_unsafe_for_api_paths(tmp_path, monkeypatch):
    """Org names are interpolated into gh api paths (gh_paginate); '/' or '?'
    would rewrite the request path, so anything outside [A-Za-z0-9-] dies."""
    monkeypatch.setattr(fc_common, "CONFIG_DIR", tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"orgs": [{"name": "a/b", "filter": "none"}]}))
    with pytest.raises(SystemExit):
        fc_common.load_config()


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


def test_fallback_tier_is_sandboxed_by_default(monkeypatch):
    """The autouse fixture must redirect BOTH user-state roots. With no env vars
    and no per-test patches, the secrets fallback tier finds nothing — proving a
    test can never read the developer's real ~/.config/daily-podcast/secrets.json."""
    for k in (
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_ACCOUNT_ID",
        "PAGES_DEPLOY_HOOK_URL",
    ):
        monkeypatch.delenv(k, raising=False)
    assert fc_common.load_r2_secrets() == {}
    assert "GH_TOKEN" not in fc_common._gh_env()


def test_load_r2_secrets_falls_back_to_daily_podcast_file(monkeypatch, tmp_path):
    monkeypatch.setattr(fc_common, "CONFIG_DIR", tmp_path / "fc")
    dp = tmp_path / "dp"
    dp.mkdir()
    monkeypatch.setattr(fc_common, "DAILY_PODCAST_CONFIG_DIR", dp)
    for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ACCOUNT_ID"):
        monkeypatch.delenv(k, raising=False)
    (dp / "secrets.json").write_text(json.dumps({"R2_ACCOUNT_ID": "from-dp"}))
    assert fc_common.load_r2_secrets().get("R2_ACCOUNT_ID") == "from-dp"
