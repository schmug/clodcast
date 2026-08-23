import datetime as dt
import json
import re
import subprocess

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
    recs = [
        dict(fc_snapshot.repo_record(_raw("a", fork=True)), name="a"),
        dict(fc_snapshot.repo_record(_raw("boilerplate")), name="boilerplate"),
    ]
    assert fc_snapshot.filter_repos(recs, org, _cfg()) == recs
    # the denylist still applies even with the AI filter off
    cfg = _cfg(denylist={"anthropics": ["boilerplate"]})
    assert [r["name"] for r in fc_snapshot.filter_repos(recs, org, cfg)] == ["a"]


def test_topic_matching_is_case_insensitive_on_both_sides():
    org = {"name": "google", "filter": "ai"}
    # repo-side case: topic "Machine-Learning" must hit the lowercase default ai_topics
    mixed = [dict(fc_snapshot.repo_record(_raw("m", topics=["Machine-Learning"])), name="m")]
    kept = fc_snapshot.filter_repos(mixed, org, _cfg(ai_description_patterns=[]))
    assert [r["name"] for r in kept] == ["m"]
    # config-side case: ai_topics=["LLM"] must hit a repo tagged plain "llm"
    lower = [dict(fc_snapshot.repo_record(_raw("l", topics=["llm"])), name="l")]
    kept = fc_snapshot.filter_repos(lower, org, _cfg(ai_topics=["LLM"], ai_description_patterns=[]))
    assert [r["name"] for r in kept] == ["l"]


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


@pytest.mark.parametrize(
    "exc",
    [subprocess.TimeoutExpired("gh", 120), FileNotFoundError("gh: command not found")],
    ids=["timeout", "gh-missing"],
)
def test_build_snapshot_isolates_timeout_and_missing_gh(exc):
    # TimeoutExpired (run_gh's timeout=120) and FileNotFoundError (gh absent
    # under launchd) are not GhError — they must still cost only the one org
    cfg = _cfg(orgs=[{"name": "good", "filter": "none"}, {"name": "bad", "filter": "none"}])

    def runner(cmd, **kw):
        if "orgs/bad" in cmd[-1]:
            raise exc

        class P:
            returncode = 0
            stdout = json.dumps([_raw("r1")])
            stderr = ""

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


def test_write_snapshot_is_atomic_and_named_by_date(monkeypatch):
    snap = {"schema_version": 1, "date": "2026-08-25", "orgs": {}, "errors": {}}
    calls = []
    real_atomic = fc_common.atomic_write_json

    def spy(path, obj):
        calls.append((path, obj))
        real_atomic(path, obj)

    monkeypatch.setattr(fc_common, "atomic_write_json", spy)
    path = fc_snapshot.write_snapshot(snap)
    assert path == fc_common.snapshot_dir() / "2026-08-25.json"
    # atomicity is the atomic_write_json contract — assert the writer goes
    # through it, not around it with a plain write
    assert calls == [(path, snap)]
    assert json.loads(path.read_text())["date"] == "2026-08-25"


@pytest.mark.parametrize("bad", ["../escaped", "2026-8-5", "2026-08-25T06:00:00", "", None])
def test_write_snapshot_rejects_non_canonical_dates(bad):
    snap = {"schema_version": 1, "date": bad, "orgs": {}, "errors": {}}
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        fc_snapshot.write_snapshot(snap)
    assert not fc_common.snapshot_dir().exists()  # nothing written anywhere


def test_writer_accepted_names_are_exactly_pruner_visible_names():
    snap = {"schema_version": 1, "date": "2020-01-01", "orgs": {}, "errors": {}}
    path = fc_snapshot.write_snapshot(snap)
    # both sides share fc_common.SNAPSHOT_RE, so any written file must be
    # visible to retention — prove it end to end by pruning the file just written
    assert re.fullmatch(fc_common.SNAPSHOT_RE, path.name)
    assert fc_snapshot.prune_snapshots(1, today=dt.date(2026, 8, 25)) == [path.name]
    assert not path.exists()


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


def test_release_check_cap_keeps_the_most_recent_pushes():
    # 31 in-window repos; the newest-pushed one sorts alphabetically LAST, so an
    # alphabetical cap would drop exactly the repo most likely to have a release
    repos = {f"a{i:02d}": {"pushed_at": f"2026-08-24T12:{i:02d}:00Z"} for i in range(30)}
    repos["zzz-newest"] = {"pushed_at": "2026-08-25T05:00:00Z"}
    names = fc_snapshot.repos_needing_release_check(repos, NOW, cap=30)
    assert len(names) == 30
    # recency-desc order, and the cap drops the oldest in-window push (a00)
    assert names == ["zzz-newest"] + [f"a{i:02d}" for i in range(29, 0, -1)]


def test_fetch_releases_window_and_error_isolation():
    def runner(cmd, **kw):
        if "hung" in cmd[-1]:
            # not a GhError — the per-repo isolation must still absorb it
            raise subprocess.TimeoutExpired("gh", 120)

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

    out = fc_snapshot.fetch_releases("acme", ["hung", "broken", "ok"], 7, NOW, runner=runner)
    assert out == [{"repo": "ok", "tag": "v2", "published_at": "2026-08-22T00:00:00Z"}]


def test_sweep_org_fetched_at_comes_from_the_injected_clock():
    def runner(cmd, **kw):
        class P:
            returncode = 0
            stdout = "[]"
            stderr = ""

        return P()

    fixed = dt.datetime(2019, 5, 1, 12, 0, tzinfo=dt.timezone.utc)
    snap = fc_snapshot.sweep_org(
        {"name": "acme", "filter": "none"}, _cfg(), runner=runner, now=fixed
    )
    assert snap["fetched_at"] == fixed.isoformat(timespec="seconds")
