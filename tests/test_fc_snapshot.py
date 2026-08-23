import datetime as dt
import json

import pytest

import fc_common
import fc_snapshot


def _raw(name, **kw):
    base = {
        "name": name,
        "stargazers_count": 10,
        "forks_count": 1,
        "open_issues_count": 0,
        "created_at": "2026-08-01T00:00:00Z",
        "pushed_at": "2026-08-20T00:00:00Z",
        "archived": False,
        "fork": False,
        "topics": [],
        "description": "a repo",
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
        "stars": 10,
        "forks": 1,
        "open_issues": 0,
        "created_at": "2026-08-01T00:00:00Z",
        "pushed_at": "2026-08-20T00:00:00Z",
        "archived": False,
        "fork": False,
        "topics": [],
        "description": "",
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
    kept = fc_snapshot.filter_repos([dict(r, name=n) for n, r in names.items()], org, cfg)
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
            stdout = json.dumps(
                [
                    {"tag_name": "v2", "published_at": "2026-08-22T00:00:00Z"},
                    {"tag_name": "v1", "published_at": "2026-01-01T00:00:00Z"},
                ]
            )
            stderr = ""

        if "broken" in cmd[-1]:
            P.returncode, P.stdout, P.stderr = 1, "", "HTTP 404"
        return P()

    out = fc_snapshot.fetch_releases("acme", ["ok", "broken"], 7, NOW, runner=runner)
    assert out == [{"repo": "ok", "tag": "v2", "published_at": "2026-08-22T00:00:00Z"}]
