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


# --- labs.json aggregation + hash-gated publish + CLI ------------------------


class FakeS3:
    def __init__(self):
        self.puts = []

    def put_object(self, **kw):
        self.puts.append(kw)


def _raw_rec(**kw):
    """A STORED record (repo_record output shape) — vs _raw, which builds
    GitHub-API-shaped input."""
    rec = {
        "stars": 10,
        "forks": 1,
        "open_issues": 0,
        "created_at": "2026-08-01T00:00:00Z",
        "pushed_at": "2026-08-20T00:00:00Z",
        "archived": False,
        "fork": False,
        "topics": [],
        "description": "a repo",
        "language": "Python",
    }
    rec.update(kw)
    return rec


def _snap_file(date, org_repos):
    d = fc_common.snapshot_dir()
    d.mkdir(parents=True, exist_ok=True)
    snap = {
        "schema_version": 1,
        "date": date,
        "orgs": {o: {"fetched_at": "", "repos": r, "releases": []} for o, r in org_repos.items()},
        "errors": {},
    }
    (d / f"{date}.json").write_text(json.dumps(snap))


_R2_CREDS = {
    "R2_ACCESS_KEY_ID": "a",
    "R2_SECRET_ACCESS_KEY": "s",
    "R2_ACCOUNT_ID": "id",
}


def test_build_labs_json_movers_and_new_repos():
    old = "2025-01-01T00:00:00Z"
    _snap_file("2026-08-18", {"acme": {"hot": _raw_rec(stars=100, created_at=old)}})
    _snap_file(
        "2026-08-25",
        {
            "acme": {
                "hot": _raw_rec(stars=900, created_at=old),
                "fresh": _raw_rec(created_at="2026-08-20T00:00:00Z"),
            }
        },
    )
    labs = fc_snapshot.build_labs_json(
        _cfg(orgs=[{"name": "acme", "filter": "none"}]), "2026-08-25"
    )
    lab = labs["labs"][0]
    assert labs["schema_version"] == 1
    assert lab["org"] == "acme"
    assert [m["name"] for m in lab["movers"]] == ["hot"]
    assert lab["movers"][0]["delta_7d"] == 800
    assert [n["name"] for n in lab["new_repos"]] == ["fresh"]


def test_labs_totals_and_display_name():
    _snap_file(
        "2026-08-25",
        {
            "anthropics": {
                "active": _raw_rec(stars=100, pushed_at="2026-08-24T00:00:00Z"),
                "dormant": _raw_rec(stars=50, pushed_at="2026-01-01T00:00:00Z"),
            }
        },
    )
    labs = fc_snapshot.build_labs_json(
        _cfg(orgs=[{"name": "anthropics", "filter": "none"}]), "2026-08-25"
    )
    lab = labs["labs"][0]
    assert lab["display"] == "Anthropic"
    assert lab["totals"] == {"repos": 2, "active_30d": 1, "stars": 150}


def test_labs_movers_baseline_boundary_is_six_days_inclusive():
    # a snapshot EXACTLY 6 days older qualifies as the movers baseline
    _snap_file("2026-08-19", {"acme": {"hot": _raw_rec(stars=100)}})
    _snap_file("2026-08-25", {"acme": {"hot": _raw_rec(stars=900)}})
    labs = fc_snapshot.build_labs_json(
        _cfg(orgs=[{"name": "acme", "filter": "none"}]), "2026-08-25"
    )
    assert labs["labs"][0]["movers"][0]["delta_7d"] == round(800 * 7 / 6)


def test_labs_movers_skip_when_newest_older_snapshot_is_under_six_days():
    _snap_file("2026-08-20", {"acme": {"hot": _raw_rec(stars=100)}})
    _snap_file("2026-08-25", {"acme": {"hot": _raw_rec(stars=900)}})
    labs = fc_snapshot.build_labs_json(
        _cfg(orgs=[{"name": "acme", "filter": "none"}]), "2026-08-25"
    )
    assert labs["labs"][0]["movers"] == []


def test_labs_new_repos_window_is_inclusive_at_thirty_days_without_baseline():
    # no older snapshot at all -> the created_at window still decides (it does
    # in every branch — new_repos is a timeline, not a diff), boundary in
    _snap_file(
        "2026-08-25",
        {
            "acme": {
                "edge": _raw_rec(created_at="2026-07-26T12:00:00Z"),
                "older": _raw_rec(created_at="2026-07-25T23:59:59Z"),
            }
        },
    )
    labs = fc_snapshot.build_labs_json(
        _cfg(orgs=[{"name": "acme", "filter": "none"}]), "2026-08-25"
    )
    assert [n["name"] for n in labs["labs"][0]["new_repos"]] == ["edge"]


def test_labs_new_repos_is_a_timeline_not_a_novelty_detector():
    # ORCHESTRATOR RULING: new_repos shows what was CREATED recently — the
    # created_at window applies in every branch, baseline present or not.
    # 'young' (20 days old) is listed even though the baseline already saw it;
    # 'edge' sits exactly ON the 30-day boundary (inclusive) and is listed;
    # 'revealed' (40 days old) is NOT listed despite being absent from the
    # baseline — set-membership novelty is fc_stories' job, not this field's.
    _snap_file(
        "2026-08-18",
        {
            "acme": {
                "young": _raw_rec(created_at="2026-08-05T00:00:00Z"),
                "edge": _raw_rec(created_at="2026-07-26T12:00:00Z"),
            }
        },
    )
    _snap_file(
        "2026-08-25",
        {
            "acme": {
                "young": _raw_rec(created_at="2026-08-05T00:00:00Z"),
                "edge": _raw_rec(created_at="2026-07-26T12:00:00Z"),
                "revealed": _raw_rec(created_at="2026-07-16T00:00:00Z"),
            }
        },
    )
    labs = fc_snapshot.build_labs_json(
        _cfg(orgs=[{"name": "acme", "filter": "none"}]), "2026-08-25"
    )
    assert [n["name"] for n in labs["labs"][0]["new_repos"]] == ["young", "edge"]


def test_labs_movers_baseline_and_span_anchor_to_the_snapshot_date():
    # run_date is 3 days after the newest snapshot. Anchored to the RUN date,
    # the 08-22 snapshot would look 6 days old and become the movers baseline;
    # anchored to the MEASUREMENT date (08-25, where the stars come from) it is
    # only 3 days old, so the baseline must be 08-19 — and the span 6 days
    # (08-19 -> 08-25), never 9 (-> run date).
    old = "2025-01-01T00:00:00Z"
    _snap_file("2026-08-19", {"acme": {"hot": _raw_rec(stars=100, created_at=old)}})
    _snap_file("2026-08-22", {"acme": {"hot": _raw_rec(stars=500, created_at=old)}})
    _snap_file("2026-08-25", {"acme": {"hot": _raw_rec(stars=900, created_at=old)}})
    labs = fc_snapshot.build_labs_json(
        _cfg(orgs=[{"name": "acme", "filter": "none"}]), "2026-08-28"
    )
    assert labs["snapshot_date"] == "2026-08-25"
    assert labs["labs"][0]["movers"][0]["delta_7d"] == round(800 * 7 / 6)


def test_labs_windows_anchor_to_the_snapshot_date_not_the_run_date():
    # Every fixture sits exactly ON its boundary relative to the MEASUREMENT
    # snapshot (2026-08-25), with the run three days later — a run-date anchor
    # flips each one: the 07-26 archived baseline would fall out of the 30-day
    # window, 'edge-new' out of the new_repos window, 'cusp-active' out of
    # active_30d, and s120's days_since_push would read 123.
    old = "2020-01-01T00:00:00Z"
    _snap_file("2026-07-26", {"acme": {"dead": _raw_rec(archived=False, created_at=old)}})
    _snap_file(
        "2026-08-25",
        {
            "acme": {
                "dead": _raw_rec(archived=True, created_at=old),
                "edge-new": _raw_rec(created_at="2026-07-26T12:00:00Z"),
                "cusp-active": _raw_rec(created_at=old, pushed_at="2026-07-26T00:00:00Z"),
                "s120": _raw_rec(stars=5000, created_at=old, pushed_at="2026-04-27T00:00:00Z"),
            }
        },
    )
    labs = fc_snapshot.build_labs_json(
        _cfg(orgs=[{"name": "acme", "filter": "none"}]), "2026-08-28"
    )
    lab = labs["labs"][0]
    assert [n["name"] for n in lab["new_repos"]] == ["edge-new"]
    assert [a["name"] for a in lab["archived_recent"]] == ["dead"]
    assert [(w["name"], w["days_since_push"]) for w in lab["stale_watch"]] == [("s120", 120)]
    assert lab["totals"]["active_30d"] == 3  # dead, edge-new, cusp-active; not s120


def test_labs_absent_org_in_baseline_is_unknown_not_empty():
    # The baseline snapshot never saw acme: movers must skip the comparison —
    # never treat the org's baseline as an empty set (mirror of fc_stories'
    # _appeared posture). new_repos is unaffected either way: the created_at
    # window applies in every branch, so the ancient repo stays out.
    _snap_file("2026-08-18", {"other": {"x": _raw_rec()}})
    _snap_file(
        "2026-08-25",
        {
            "acme": {
                "ancient": _raw_rec(created_at="2020-01-01T00:00:00Z"),
                "recent": _raw_rec(created_at="2026-08-20T00:00:00Z"),
            }
        },
    )
    labs = fc_snapshot.build_labs_json(
        _cfg(orgs=[{"name": "acme", "filter": "none"}]), "2026-08-25"
    )
    lab = labs["labs"][0]
    assert lab["movers"] == []
    assert [n["name"] for n in lab["new_repos"]] == ["recent"]


def test_labs_org_absent_from_current_snapshot_gets_no_entry():
    # build_snapshot omits a failed org entirely — publishing zeros for it would
    # misreport an outage as a dead lab, so the org is skipped (UNKNOWN)
    _snap_file("2026-08-25", {"acme": {"a": _raw_rec()}})
    cfg = _cfg(orgs=[{"name": "acme", "filter": "none"}, {"name": "ghost", "filter": "none"}])
    labs = fc_snapshot.build_labs_json(cfg, "2026-08-25")
    assert [lab["org"] for lab in labs["labs"]] == ["acme"]


def test_labs_stale_watch_boundary_excludes_archived_and_low_stars():
    _snap_file(
        "2026-08-25",
        {
            "acme": {
                "s120": _raw_rec(stars=5000, pushed_at="2026-04-27T00:00:00Z"),
                "s119": _raw_rec(stars=5000, pushed_at="2026-04-28T00:00:00Z"),
                "tomb": _raw_rec(stars=5000, pushed_at="2025-01-01T00:00:00Z", archived=True),
                "dim": _raw_rec(stars=999, pushed_at="2025-01-01T00:00:00Z"),
            }
        },
    )
    labs = fc_snapshot.build_labs_json(
        _cfg(orgs=[{"name": "acme", "filter": "none"}]), "2026-08-25"
    )
    watch = labs["labs"][0]["stale_watch"]
    assert [w["name"] for w in watch] == ["s120"]
    assert watch[0]["days_since_push"] == 120


def test_labs_archived_recent_flips_vs_oldest_snapshot_within_thirty_days():
    # the OLDEST in-window snapshot is the baseline: the flip already visible in
    # the 08-20 snapshot must still be reported (a newest-older baseline would
    # miss it), and the window is inclusive at exactly 30 days
    _snap_file("2026-07-26", {"acme": {"dead": _raw_rec(archived=False)}})
    _snap_file("2026-08-20", {"acme": {"dead": _raw_rec(archived=True)}})
    _snap_file("2026-08-25", {"acme": {"dead": _raw_rec(archived=True)}})
    labs = fc_snapshot.build_labs_json(
        _cfg(orgs=[{"name": "acme", "filter": "none"}]), "2026-08-25"
    )
    assert [a["name"] for a in labs["labs"][0]["archived_recent"]] == ["dead"]


def test_labs_archived_recent_ignores_snapshots_older_than_thirty_days():
    # only baseline is 31 days old -> the flip's age is unknowable -> no report
    _snap_file("2026-07-25", {"acme": {"dead": _raw_rec(archived=False)}})
    _snap_file("2026-08-25", {"acme": {"dead": _raw_rec(archived=True)}})
    labs = fc_snapshot.build_labs_json(
        _cfg(orgs=[{"name": "acme", "filter": "none"}]), "2026-08-25"
    )
    assert labs["labs"][0]["archived_recent"] == []


def test_labs_content_hash_ignores_generated_at_but_not_content():
    a = {"schema_version": 1, "generated_at": "T1", "labs": [{"org": "acme"}]}
    b = dict(a, generated_at="T2")
    c = dict(a, labs=[])
    assert fc_snapshot.labs_content_hash(a) == fc_snapshot.labs_content_hash(b)
    assert fc_snapshot.labs_content_hash(a) != fc_snapshot.labs_content_hash(c)


def test_publish_labs_hash_gate_and_statuses(monkeypatch):
    labs = {"schema_version": 1, "generated_at": "T1", "labs": []}
    cfg = _cfg(r2_bucket="clodcast", r2_public_base_url="https://x")
    monkeypatch.setattr(fc_common, "load_r2_secrets", lambda: dict(_R2_CREDS))
    s3 = FakeS3()
    assert fc_snapshot.publish_labs(labs, cfg, client_factory=lambda c: s3) == "published"
    assert s3.puts[0]["Key"] == "labs.json"
    # the gate record is destination-keyed JSON, not a bare content hash
    assert json.loads(fc_common.labs_hash_path().read_text()) == {
        "bucket": "clodcast",
        "key": "labs.json",
        "sha256": fc_snapshot.labs_content_hash(labs),
    }
    # same content, different generated_at -> unchanged, no second PUT
    labs2 = dict(labs, generated_at="T2")
    assert fc_snapshot.publish_labs(labs2, cfg, client_factory=lambda c: s3) == "unchanged"
    assert len(s3.puts) == 1


def test_publish_labs_republishes_when_the_destination_changes(monkeypatch):
    # The gate is keyed on DESTINATION + content, not content alone: renaming
    # labs_manifest_name or switching r2_bucket with identical content must PUT
    # to the new destination — never report a healthy-looking 'unchanged' while
    # the new key stays unwritten forever.
    labs = {"schema_version": 1, "generated_at": "T1", "labs": []}
    monkeypatch.setattr(fc_common, "load_r2_secrets", lambda: dict(_R2_CREDS))
    s3 = FakeS3()
    cfg = _cfg(r2_bucket="clodcast")
    assert fc_snapshot.publish_labs(labs, cfg, client_factory=lambda c: s3) == "published"
    # same content, new key -> published, PUT lands on the new key
    cfg2 = _cfg(r2_bucket="clodcast", labs_manifest_name="labs-v2.json")
    assert fc_snapshot.publish_labs(labs, cfg2, client_factory=lambda c: s3) == "published"
    assert len(s3.puts) == 2 and s3.puts[-1]["Key"] == "labs-v2.json"
    # same content, new bucket -> published
    cfg3 = _cfg(r2_bucket="clodcast-staging", labs_manifest_name="labs-v2.json")
    assert fc_snapshot.publish_labs(labs, cfg3, client_factory=lambda c: s3) == "published"
    assert len(s3.puts) == 3 and s3.puts[-1]["Bucket"] == "clodcast-staging"
    # same content AND same destination -> unchanged, no PUT
    assert fc_snapshot.publish_labs(labs, cfg3, client_factory=lambda c: s3) == "unchanged"
    assert len(s3.puts) == 3


def test_publish_labs_legacy_plain_sha_record_publishes_and_rewrites(monkeypatch):
    # a pre-destination-keyed gate file holds the bare content sha: treat it as
    # no-gate (publish) and rewrite it as the destination-keyed JSON record
    labs = {"schema_version": 1, "generated_at": "T1", "labs": []}
    cfg = _cfg(r2_bucket="clodcast")
    monkeypatch.setattr(fc_common, "load_r2_secrets", lambda: dict(_R2_CREDS))
    p = fc_common.labs_hash_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(fc_snapshot.labs_content_hash(labs))
    s3 = FakeS3()
    assert fc_snapshot.publish_labs(labs, cfg, client_factory=lambda c: s3) == "published"
    assert len(s3.puts) == 1
    assert json.loads(p.read_text()) == {
        "bucket": "clodcast",
        "key": "labs.json",
        "sha256": fc_snapshot.labs_content_hash(labs),
    }
    # the rewritten record gates the next identical publish
    assert fc_snapshot.publish_labs(labs, cfg, client_factory=lambda c: s3) == "unchanged"
    assert len(s3.puts) == 1


def test_publish_labs_skips_when_unconfigured():
    assert fc_snapshot.publish_labs({"labs": []}, _cfg()) == "skipped"


def test_publish_labs_failed_on_put_error_never_raises(monkeypatch):
    labs = {"schema_version": 1, "generated_at": "T1", "labs": []}
    cfg = _cfg(r2_bucket="clodcast")
    monkeypatch.setattr(fc_common, "load_r2_secrets", lambda: dict(_R2_CREDS))

    class BoomS3:
        def put_object(self, **kw):
            raise RuntimeError("R2 down")

    assert fc_snapshot.publish_labs(labs, cfg, client_factory=lambda c: BoomS3()) == "failed"
    # a publish that never happened must not record its hash…
    assert not fc_common.labs_hash_path().exists()
    # …so the next run with a working client still publishes the same content
    s3 = FakeS3()
    assert fc_snapshot.publish_labs(labs, cfg, client_factory=lambda c: s3) == "published"
    assert len(s3.puts) == 1


def test_publish_labs_fires_hook_only_on_publish(monkeypatch):
    labs = {"schema_version": 1, "generated_at": "T1", "labs": []}
    cfg = _cfg(r2_bucket="clodcast")
    creds = dict(_R2_CREDS, PAGES_DEPLOY_HOOK_URL="https://hook")
    monkeypatch.setattr(fc_common, "load_r2_secrets", lambda: creds)
    fired = []
    monkeypatch.setattr(fc_snapshot, "fire_hook", lambda url: fired.append(url))
    s3 = FakeS3()
    assert fc_snapshot.publish_labs(labs, cfg, client_factory=lambda c: s3) == "published"
    assert fired == ["https://hook"]
    labs2 = dict(labs, generated_at="T2")
    assert fc_snapshot.publish_labs(labs2, cfg, client_factory=lambda c: s3) == "unchanged"
    assert fired == ["https://hook"]  # unchanged never re-fires the hook


def _good_runner(cmd, **kw):
    class P:
        returncode = 0
        stdout = json.dumps([_raw("only")])
        stderr = ""

    return P()


def test_cli_dry_run_touches_no_network(monkeypatch, capsys):
    fc_common.atomic_write_json(
        fc_common.config_path(), {"orgs": [{"name": "acme", "filter": "none"}]}
    )

    def boom(*a, **kw):
        raise AssertionError("network touched in --dry-run")

    monkeypatch.setattr(fc_snapshot, "publish_labs", boom)
    monkeypatch.setattr(fc_snapshot, "fire_hook", boom)
    monkeypatch.setattr(fc_snapshot, "_default_runner", _good_runner)
    rc = fc_snapshot.main(["--date", "2026-08-25", "--dry-run"])
    assert rc == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("SNAPSHOT ok date=2026-08-25")
    assert "labs=dry-run" in last


def test_cli_failed_line_when_every_org_fails(monkeypatch, capsys):
    fc_common.atomic_write_json(
        fc_common.config_path(), {"orgs": [{"name": "acme", "filter": "none"}]}
    )

    def runner(cmd, **kw):
        raise FileNotFoundError("gh: command not found")

    monkeypatch.setattr(fc_snapshot, "_default_runner", runner)
    rc = fc_snapshot.main(["--date", "2026-08-25"])
    assert rc == 1
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("SNAPSHOT FAILED")


def test_cli_failed_line_when_config_is_missing(capsys):
    # the launchd first-run state: no config.json at all. load_config die()s
    # (SystemExit), which used to escape main() with only an `error: …` line —
    # no SNAPSHOT line, invisible to the scheduler contract. The die message
    # must ride the FAILED line, exactly once (one line, no `error:` echo).
    rc = fc_snapshot.main(["--date", "2026-08-25"])
    assert rc == 1
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[-1].startswith("SNAPSHOT FAILED")
    assert "config.json" in lines[-1]
    assert lines == lines[-1:]  # the contract line is the ONLY line — no double print


def test_cli_failed_line_when_config_is_invalid(capsys):
    # every load_config die() site (not just the missing-file one) must end in
    # the parseable FAILED line with exit code 1
    fc_common.atomic_write_json(
        fc_common.config_path(), {"orgs": [{"name": "acme", "filter": "nope"}]}
    )
    rc = fc_snapshot.main(["--date", "2026-08-25"])
    assert rc == 1
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("SNAPSHOT FAILED")
    assert "filter" in last  # the die message, flattened onto the contract line


def test_cli_failed_line_on_oserror_after_sweep(monkeypatch, capsys):
    # the catch is (GhError, SubprocessError, OSError), not SweepFailed alone —
    # a full disk mid-write must still end in the single parseable line
    fc_common.atomic_write_json(
        fc_common.config_path(), {"orgs": [{"name": "acme", "filter": "none"}]}
    )
    monkeypatch.setattr(fc_snapshot, "_default_runner", _good_runner)

    def bad_write(snap):
        raise OSError("disk full")

    monkeypatch.setattr(fc_snapshot, "write_snapshot", bad_write)
    rc = fc_snapshot.main(["--date", "2026-08-25"])
    assert rc == 1
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("SNAPSHOT FAILED")


@pytest.mark.parametrize("bad", ["2026-8-5", "2026-02-30", "../escape", "2026-08-25T06:00:00"])
def test_cli_rejects_non_canonical_dates(bad, capsys):
    rc = fc_snapshot.main(["--date", bad])
    assert rc == 1
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("SNAPSHOT FAILED")


def test_cli_one_resolved_date_drives_prune_and_labs_status(monkeypatch, capsys):
    # 2028-01-01 is in the FUTURE against the wall clock but stale against the
    # resolved --date: pruning must use the resolved date, never a second clock
    # read. The final line's labs=skipped also proves the real publish path ran
    # (R2 unconfigured -> skipped), not the dry-run branch.
    fc_common.atomic_write_json(
        fc_common.config_path(), {"orgs": [{"name": "acme", "filter": "none"}]}
    )
    d = fc_common.snapshot_dir()
    d.mkdir(parents=True)
    (d / "2028-01-01.json").write_text("{}")
    monkeypatch.setattr(fc_snapshot, "_default_runner", _good_runner)
    rc = fc_snapshot.main(["--date", "2030-01-01"])
    assert rc == 0
    assert not (d / "2028-01-01.json").exists()
    assert (d / "2030-01-01.json").exists()
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("SNAPSHOT ok date=2030-01-01 orgs=1/1 repos=1")
    assert "labs=skipped" in last
