import json

import pytest

import fc_common
import fc_stories


def _repo(**kw):
    base = {
        "stars": 50,
        "forks": 0,
        "open_issues": 0,
        "created_at": "2026-08-20T00:00:00Z",
        "pushed_at": "2026-08-24T00:00:00Z",
        "archived": False,
        "fork": False,
        "topics": [],
        "description": "d",
        "language": "Py",
    }
    base.update(kw)
    return base


def _cfg(**kw):
    cfg = dict(fc_common.DEFAULT_CONFIG)
    cfg.update(kw)
    return cfg


def _write_snap(date, org_repos):
    d = fc_common.snapshot_dir()
    d.mkdir(parents=True, exist_ok=True)
    snap = {
        "schema_version": 1,
        "date": date,
        "orgs": {o: {"fetched_at": "", "repos": r, "releases": []} for o, r in org_repos.items()},
        "errors": {},
    }
    (d / f"{date}.json").write_text(json.dumps(snap))
    return snap


# --- snapshot loading + baseline -------------------------------------------


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
    # parseable but not an object -> None too (a list can't be a snapshot)
    (d / "2026-08-24.json").write_text("[1, 2]")
    assert fc_stories.load_snapshot("2026-08-24") is None


def test_load_snapshot_refuses_non_date_names():
    # date_iso is interpolated into a path; anything that isn't a snapshot date
    # must not resolve to a file outside (or inside) the snapshot dir.
    d = fc_common.snapshot_dir()
    d.mkdir(parents=True)
    (d.parent / "escaped.json").write_text("{}")
    assert fc_stories.load_snapshot("../escaped") is None
    assert fc_stories.load_snapshot("2026-8-5") is None
    assert fc_stories.load_snapshot(None) is None


def test_org_views_degrades_on_malformed_shapes():
    snap = {
        "orgs": {
            "good": {"repos": {"ok": _repo(), "broken-rec": "not-a-dict"}, "releases": []},
            "no-repos-map": {"repos": "nope"},
            "not-a-view": 7,
        }
    }
    views = fc_stories.org_views(snap)
    assert set(views) == {"good", "no-repos-map"}
    assert set(views["good"]) == {"ok"}
    assert views["no-repos-map"] == {}
    assert fc_stories.org_views({"orgs": "bogus"}) == {}


def test_org_views_coerces_malformed_stars_to_zero():
    # upstream repo_record returns None for a present-but-null stargazers_count;
    # detectors compare stars numerically BEFORE score_story's guard applies, so
    # the coercion has to happen once at the malformed-data gate.
    snap = {
        "orgs": {
            "acme": {
                "repos": {
                    "null-stars": _repo(stars=None),
                    "negative": _repo(stars=-5),
                    "boolean": _repo(stars=True),
                    "fine": _repo(stars=7),
                }
            }
        }
    }
    views = fc_stories.org_views(snap)
    assert views["acme"]["null-stars"]["stars"] == 0
    assert views["acme"]["negative"]["stars"] == 0
    assert views["acme"]["boolean"]["stars"] == 0
    assert views["acme"]["fine"]["stars"] == 7


def test_org_views_drops_names_outside_githubs_alphabet():
    # such names can only come from a damaged snapshot, and they would forge
    # colliding story keys ("a:b" + stage "c" vs "a" + stage "b:c") or URLs
    snap = {
        "orgs": {
            "acme": {"repos": {"fine": _repo(), "evil:name": _repo(), "sl/ash": _repo()}},
            "bad:org": {"repos": {"x": _repo()}},
        }
    }
    views = fc_stories.org_views(snap)
    assert set(views) == {"acme"}
    assert set(views["acme"]) == {"fine"}


# --- detectors: NEW_REPO / NOTABLE_FORK / ARCHIVED --------------------------


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
        "git": _repo(
            fork=True, created_at="2026-08-21T00:00:00Z", pushed_at="2026-08-24T00:00:00Z"
        ),
        "dead": _repo(
            fork=True, created_at="2026-08-21T00:00:00Z", pushed_at="2026-06-01T00:00:00Z"
        ),
    }
    got = fc_stories.detect_notable_forks({"openai": cur}, "2026-08-18", "2026-08-25")
    assert [s["repo"] for s in got] == ["git"]
    assert got[0]["type"] == "NOTABLE_FORK"


def test_new_repo_novelty_is_set_membership_when_baseline_exists():
    # The critic's scenario: the baseline snapshot was taken early on D0 and a
    # repo was created later that same day — absent from base, but its
    # created_at date <= the cutoff date. Set membership must fire it anyway;
    # a repo present in both snapshots must not fire. The no-baseline fallback
    # still honors the date cutoff.
    base = {"acme": {"steady": _repo(created_at="2020-01-01T00:00:00Z")}}
    cur = {
        "acme": {
            "steady": _repo(created_at="2020-01-01T00:00:00Z"),
            "late-arrival": _repo(created_at="2026-08-18T22:00:00Z"),
        }
    }
    got = fc_stories.detect_new_repos(cur, "2026-08-18", base)
    assert [s["key"] for s in got] == ["NEW_REPO:acme/late-arrival:new"]
    # no baseline -> date arithmetic: created ON the cutoff date is excluded
    assert fc_stories.detect_new_repos(cur, "2026-08-18", None) == []


def test_notable_fork_novelty_is_set_membership_when_baseline_exists():
    base = {"acme": {"old-fork": _repo(fork=True, created_at="2020-01-01T00:00:00Z")}}
    cur = {
        "acme": {
            "old-fork": _repo(
                fork=True, created_at="2020-01-01T00:00:00Z", pushed_at="2026-08-24T00:00:00Z"
            ),
            "late-fork": _repo(
                fork=True, created_at="2026-08-18T22:00:00Z", pushed_at="2026-08-24T00:00:00Z"
            ),
        }
    }
    got = fc_stories.detect_notable_forks(cur, "2026-08-18", "2026-08-25", base)
    assert [s["repo"] for s in got] == ["late-fork"]


def test_org_absent_from_baseline_falls_back_to_date_cutoff():
    # build_snapshot OMITS a failed org from snap["orgs"] (it lands in
    # snap["errors"]), and a newly-configured org has no baseline entry at
    # all. Either way the org's baseline is UNKNOWN, not empty — set
    # membership would announce the org's entire catalog as new. The date
    # cutoff governs instead: ancient repos stay silent, repos created inside
    # the window are still announced.
    base = {"other": {"x": _repo()}}  # acme errored or was just added: absent
    cur = {
        "acme": {
            "ancient": _repo(created_at="2020-01-01T00:00:00Z"),
            "fresh": _repo(created_at="2026-08-22T00:00:00Z"),
        }
    }
    got = fc_stories.detect_new_repos(cur, "2026-08-18", base)
    assert [s["key"] for s in got] == ["NEW_REPO:acme/fresh:new"]
    forks = {
        "acme": {
            "ancient-fork": _repo(
                fork=True, created_at="2020-01-01T00:00:00Z", pushed_at="2026-08-24T00:00:00Z"
            )
        }
    }
    assert fc_stories.detect_notable_forks(forks, "2026-08-18", "2026-08-25", base) == []


def test_newly_added_org_announces_fresh_forks_via_date_cutoff():
    # The positive half of the fork fallback: an org absent from the baseline
    # still announces a fork created inside the window (date arm), so the
    # fallback delays nothing that is genuinely recent.
    base = {"other": {"x": _repo()}}
    forks = {
        "acme": {
            "fresh-fork": _repo(
                fork=True, created_at="2026-08-22T00:00:00Z", pushed_at="2026-08-24T00:00:00Z"
            )
        }
    }
    got = fc_stories.detect_notable_forks(forks, "2026-08-18", "2026-08-25", base)
    assert [s["key"] for s in got] == ["NOTABLE_FORK:acme/fresh-fork:new"]


def test_empty_baseline_org_view_falls_back_to_date_cutoff():
    # A present-but-EMPTY baseline org view is ambiguous the same way: the
    # "ai" name filter can legitimately strip an org to zero repos while its
    # real repos are years old. Treat it as unknown too — date cutoff, never
    # "everything is new".
    base = {"acme": {}}
    cur = {
        "acme": {
            "ancient": _repo(created_at="2020-01-01T00:00:00Z"),
            "fresh": _repo(created_at="2026-08-22T00:00:00Z"),
        }
    }
    got = fc_stories.detect_new_repos(cur, "2026-08-18", base)
    assert [s["key"] for s in got] == ["NEW_REPO:acme/fresh:new"]


def test_detect_archived_needs_a_flip_and_a_baseline():
    base = {"acme": {"gym": _repo(archived=False)}}
    cur = {"acme": {"gym": _repo(archived=True), "born-dead": _repo(archived=True)}}
    got = fc_stories.detect_archived(cur, base)
    assert [s["key"] for s in got] == ["ARCHIVED:acme/gym:archived"]
    assert fc_stories.detect_archived(cur, None) == []


# --- detectors: GOING_STALE / STAR_SURGE / RELEASE --------------------------


def test_going_stale_stages_and_floor():
    cur = {
        "acme": {
            "big-stale": _repo(stars=5000, pushed_at="2026-01-01T00:00:00Z"),  # 236 days
            "big-dead": _repo(stars=5000, pushed_at="2024-08-01T00:00:00Z"),  # > 365
            "small-stale": _repo(stars=10, pushed_at="2026-01-01T00:00:00Z"),
            "fresh": _repo(stars=5000, pushed_at="2026-08-20T00:00:00Z"),
            "tombstone": _repo(stars=5000, pushed_at="2026-01-01T00:00:00Z", archived=True),
        }
    }
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
    snap = {
        "orgs": {
            "acme": {
                "repos": {},
                "releases": [
                    {"repo": "big", "tag": "v2", "published_at": "2026-08-22T00:00:00Z"},
                    {"repo": "tiny", "tag": "v1", "published_at": "2026-08-22T00:00:00Z"},
                    {"repo": "listed", "tag": "v3", "published_at": "2026-08-22T00:00:00Z"},
                    {"repo": "old", "tag": "v0", "published_at": "2026-01-01T00:00:00Z"},
                ],
            }
        }
    }
    cur = {
        "acme": {
            "big": _repo(stars=900),
            "tiny": _repo(stars=5),
            "listed": _repo(stars=5),
            "old": _repo(stars=900),
        }
    }
    cfg = _cfg(allowlist={"acme": ["listed"]})
    got = fc_stories.detect_releases(snap, "2026-08-18", cur, cfg)
    assert {s["key"] for s in got} == {"RELEASE:acme/big:v2", "RELEASE:acme/listed:v3"}


def test_release_published_on_the_baseline_date_is_not_permanently_missed():
    # Same class as the created_at boundary fix: a release published late on
    # the baseline day — AFTER that day's snapshot was taken — was skipped by
    # an at-or-before gate on EVERY subsequent run, so at weekly cadence it
    # was never mentioned at all. On the boundary date it must emit; strictly
    # before the cutoff it must still be skipped; and reported.json keeps the
    # mention exactly-once, so the duplicate candidate a boundary day can
    # produce pre-mark never re-emits after marking.
    snap = {
        "orgs": {
            "acme": {
                "repos": {},
                "releases": [
                    {"repo": "big", "tag": "v9", "published_at": "2026-08-18T22:00:00Z"},
                    {"repo": "big", "tag": "v0", "published_at": "2026-08-17T23:59:00Z"},
                ],
            }
        }
    }
    cur = {"acme": {"big": _repo(stars=900)}}
    got = fc_stories.detect_releases(snap, "2026-08-18", cur, _cfg())
    assert [s["key"] for s in got] == ["RELEASE:acme/big:v9"]
    # after marking, the boundary-day release does not re-emit next run
    fc_stories.mark_reported([got[0]["key"]], "2026-08-25", "spotify:episode:e1")
    assert fc_stories.filter_new(got, fc_stories.load_reported()) == []


def test_release_with_malformed_published_at_is_still_skipped():
    # The strict-before gate must not admit garbage: _date_only maps a
    # missing/unparseable published_at to "", which sorts before every real
    # date — so a malformed release stays skipped, exactly as it was under
    # the old at-or-before gate.
    snap = {
        "orgs": {
            "acme": {
                "repos": {},
                "releases": [
                    {"repo": "big", "tag": "v1"},
                    {"repo": "big", "tag": "v2", "published_at": "not-a-date"},
                ],
            }
        }
    }
    cur = {"acme": {"big": _repo(stars=900)}}
    assert fc_stories.detect_releases(snap, "2026-08-18", cur, _cfg()) == []


def test_release_stage_embeds_tag_verbatim_even_with_colon():
    # The key is an opaque exact-match token, never parsed — so a tag with ':'
    # is embedded VERBATIM. Sanitizing instead would collide distinct tags
    # ("v1:beta" vs "v1_beta") into one key and silently skip a mention.
    # (Two repos, one tag each: the per-repo release-train collapse means a
    # single repo only ever emits its newest release per run.)
    snap = {
        "orgs": {
            "acme": {
                "repos": {},
                "releases": [
                    {"repo": "big", "tag": "v1:beta", "published_at": "2026-08-22T00:00:00Z"},
                    {"repo": "second", "tag": "v1_beta", "published_at": "2026-08-22T00:00:00Z"},
                ],
            }
        }
    }
    cur = {"acme": {"big": _repo(stars=900), "second": _repo(stars=900)}}
    got = fc_stories.detect_releases(snap, "2026-08-18", cur, _cfg())
    keys = [s["key"] for s in got]
    assert "RELEASE:acme/big:v1:beta" in keys
    assert "RELEASE:acme/second:v1_beta" in keys
    assert len(set(keys)) == 2  # no collision
    # and the colon key round-trips through the mention-once state
    fc_stories.mark_reported(["RELEASE:acme/big:v1:beta"], "2026-08-25", "spotify:episode:e1")
    left = fc_stories.filter_new(got, fc_stories.load_reported())
    assert [s["key"] for s in left] == ["RELEASE:acme/second:v1_beta"]


def test_release_skips_empty_tags_unsafe_names_and_malformed_entries():
    snap = {
        "orgs": {
            "acme": {
                "repos": {},
                "releases": [
                    {"repo": "big", "tag": "", "published_at": "2026-08-22T00:00:00Z"},
                    {"repo": "evil:name", "tag": "v1", "published_at": "2026-08-22T00:00:00Z"},
                    "not-a-dict",
                    {"repo": "big", "tag": "v2", "published_at": "2026-08-22T00:00:00Z"},
                ],
            },
            "bad": {"releases": "nope"},
        }
    }
    cur = {"acme": {"big": _repo(stars=900)}}
    got = fc_stories.detect_releases(snap, "2026-08-18", cur, _cfg())
    assert [s["key"] for s in got] == ["RELEASE:acme/big:v2"]


# --- mention-once state ------------------------------------------------------


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
    fc_common.reported_path().write_text("[1]")  # parseable but not an object
    assert fc_stories.load_reported() == {}


# --- the same-ISO-week ship guard -------------------------------------------


def test_already_shipped_this_week_spans_the_sunday_monday_boundary():
    """A Sunday cron fire and the Monday six days earlier are ONE ISO week, and
    both mint the identical slug — a raw date-string compare would miss it and
    let the second run republish over the first one's live permalink."""
    url = "https://audio.example/frontier-commits/week-of-august-31-2026.mp3"
    rep = {"NEW_REPO:acme/x:new": {"date": "2026-08-31", "episode_uri": url}}

    assert fc_stories.already_shipped_this_week(rep, "2026-08-31") == url  # the Monday itself
    assert fc_stories.already_shipped_this_week(rep, "2026-09-06") == url  # Sunday, same week
    assert fc_stories.already_shipped_this_week(rep, "2026-09-07") is None  # the next Monday
    assert fc_stories.already_shipped_this_week(rep, "2026-08-30") is None  # the Sunday before


def test_already_shipped_this_week_sees_the_live_september_3_entries():
    """Episode two shipped 2026-09-03; the weekly cron's next fire was 2026-09-06,
    inside that same ISO week. That collision is what this guard exists to stop."""
    url = "https://audio.example/frontier-commits/week-of-august-31-2026.mp3"
    rep = {"RELEASE:openai/x:v1": {"date": "2026-09-03", "episode_uri": url}}

    assert fc_stories.already_shipped_this_week(rep, "2026-09-04") == url
    assert fc_stories.already_shipped_this_week(rep, "2026-09-08") is None  # the following week


def test_already_shipped_this_week_skips_malformed_entries_without_dying():
    """Same no-data-loss posture as covered.json pruning: a damaged entry is
    skipped, never fatal, and never fabricates a week that did not ship."""
    rep = {
        "a": "not-a-dict",
        "b": {"episode_uri": "no date at all"},
        "c": {"date": None, "episode_uri": "u"},
        "d": {"date": "20260903", "episode_uri": "u"},  # compact form: not YYYY-MM-DD
        "e": {"date": "2026-13-45", "episode_uri": "u"},
        "f": {"date": "2026-08-24", "episode_uri": "an earlier week"},
    }
    assert fc_stories.already_shipped_this_week(rep, "2026-09-04") is None
    assert fc_stories.already_shipped_this_week({}, "2026-09-04") is None


def test_already_shipped_this_week_fails_closed_when_the_uri_is_unusable():
    """ "Shipped but unnamed" must not read as "not shipped" — the field stays
    truthy so the weekly run still skips."""
    rep = {"k": {"date": "2026-09-03", "episode_uri": ""}}
    assert fc_stories.already_shipped_this_week(rep, "2026-09-04") == fc_stories.UNKNOWN_EPISODE
    assert fc_stories.UNKNOWN_EPISODE, "the fail-closed sentinel must be truthy"


def test_detect_all_flags_a_week_that_already_shipped():
    """The guard is a pure function of (reported.json, run_date) and is
    independent of mention-once dedup: fresh stories still surface, but the
    field tells the weekly run not to spend TTS on them."""
    url = "https://audio.example/frontier-commits/week-of-august-31-2026.mp3"
    _write_snap("2026-08-28", {"acme": {}})
    _write_snap("2026-09-04", {"acme": {"n": _repo(created_at="2026-09-02T00:00:00Z")}})
    cfg = _cfg(orgs=[{"name": "acme", "filter": "none"}])

    out = fc_stories.detect_all("2026-09-04", cfg)
    assert out["already_shipped_this_week"] is None

    fc_stories.mark_reported(["NEW_REPO:acme/unrelated:new"], "2026-09-03", url)
    again = fc_stories.detect_all("2026-09-04", cfg)
    assert again["already_shipped_this_week"] == url
    assert [s["key"] for s in again["stories"]] == ["NEW_REPO:acme/n:new"]
    assert fc_stories.detect_all("2026-09-07", cfg)["already_shipped_this_week"] is None


def test_mark_reported_round_trips_and_merges():
    fc_stories.mark_reported(["A:o/r:new"], "2026-08-25", "spotify:episode:e1")
    fc_stories.mark_reported(["B:o/r:new"], "2026-09-01", "spotify:episode:e2")
    rep = fc_stories.load_reported()
    assert rep["A:o/r:new"]["episode_uri"] == "spotify:episode:e1"
    assert rep["B:o/r:new"]["date"] == "2026-09-01"


# --- scoring + selection -----------------------------------------------------


def test_scoring_orders_types_then_stars():
    new_small = fc_stories.story("NEW_REPO", "a", "x", "new", {"stars": 0})
    surge_huge = fc_stories.story("STAR_SURGE", "a", "y", "2026-W35", {"stars": 100000})
    assert fc_stories.score_story(new_small) > fc_stories.score_story(surge_huge)


def test_scoring_degrades_on_malformed_stars():
    # a damaged snapshot's stars must degrade the score, never crash log10
    junk = fc_stories.story("NEW_REPO", "a", "x", "new", {"stars": "lots"})
    negative = fc_stories.story("NEW_REPO", "a", "x", "new", {"stars": -5})
    floor = fc_stories.score_story(fc_stories.story("NEW_REPO", "a", "x", "new", {"stars": 0}))
    assert fc_stories.score_story(junk) == floor
    assert fc_stories.score_story(negative) == floor


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


def test_select_stories_breaks_score_ties_by_key():
    a = fc_stories.story("NEW_REPO", "b", "x", "new", {"stars": 5})
    b = fc_stories.story("NEW_REPO", "a", "y", "new", {"stars": 5})
    for s in (a, b):
        s["score"] = fc_stories.score_story(s)
    picked = fc_stories.select_stories([a, b], target=1, per_org_cap=3)
    assert [s["key"] for s in picked] == ["NEW_REPO:a/y:new"]


# --- detect_all + CLI --------------------------------------------------------


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


def test_detect_all_falls_back_to_newest_snapshot_and_logs(capsys):
    _write_snap("2026-08-18", {"acme": {}})
    _write_snap("2026-08-24", {"acme": {"n": _repo(created_at="2026-08-22T00:00:00Z")}})
    cfg = _cfg(orgs=[{"name": "acme", "filter": "none"}])
    out = fc_stories.detect_all("2026-08-25", cfg)
    assert out["baseline_date"] == "2026-08-18"
    assert [s["key"] for s in out["stories"]] == ["NEW_REPO:acme/n:new"]
    assert "warn: no snapshot for 2026-08-25, using 2026-08-24" in capsys.readouterr().out


def test_detect_all_dies_without_any_snapshot():
    with pytest.raises(SystemExit):
        fc_stories.detect_all("2026-08-25", _cfg())


def test_missed_day_never_uses_the_current_snapshot_as_its_own_baseline(monkeypatch, tmp_path):
    # One snapshot dated D-1, run on D: the baseline must resolve to None —
    # never to the current snapshot itself (which would make the cutoff the
    # snapshot's own date and silently empty the episode). The run must detect
    # exactly what the same snapshot detects when dated D.
    repos = {"acme": {"n": _repo(created_at="2026-08-22T00:00:00Z")}}
    cfg = _cfg(orgs=[{"name": "acme", "filter": "none"}])

    monkeypatch.setattr(fc_common, "CONFIG_DIR", tmp_path / "same-day")
    _write_snap("2026-08-25", repos)
    same_day = fc_stories.detect_all("2026-08-25", cfg)

    monkeypatch.setattr(fc_common, "CONFIG_DIR", tmp_path / "missed-day")
    _write_snap("2026-08-24", repos)
    missed = fc_stories.detect_all("2026-08-25", cfg)

    assert same_day["baseline_date"] is None
    assert missed["baseline_date"] is None
    assert [s["key"] for s in same_day["stories"]] == ["NEW_REPO:acme/n:new"]
    assert [s["key"] for s in missed["stories"]] == [s["key"] for s in same_day["stories"]]


def test_calendar_invalid_snapshot_names_are_ignored():
    # 2026-02-30.json passes the filename regex but is not a real date; picked
    # as a baseline it would crash detect_star_surge with an uncaught
    # ValueError. It must be invisible to date listing entirely.
    _write_snap("2026-02-30", {"acme": {"x": _repo(stars=100)}})
    _write_snap("2026-08-25", {"acme": {"n": _repo(created_at="2026-08-22T00:00:00Z")}})
    assert "2026-02-30" not in fc_stories.list_snapshot_dates()
    cfg = _cfg(orgs=[{"name": "acme", "filter": "none"}])
    out = fc_stories.detect_all("2026-08-25", cfg)
    assert out["baseline_date"] is None
    assert [s["key"] for s in out["stories"]] == ["NEW_REPO:acme/n:new"]


def test_stars_none_repo_degrades_instead_of_killing_the_run():
    # One healthy story plus one repo whose stars came back null: the run must
    # complete and the healthy story must survive.
    cur = {
        "acme": {
            "healthy": _repo(created_at="2026-08-22T00:00:00Z"),
            "null-stars": _repo(stars=None, created_at="2020-01-01T00:00:00Z"),
        }
    }
    _write_snap("2026-08-25", cur)
    cfg = _cfg(orgs=[{"name": "acme", "filter": "none"}])
    out = fc_stories.detect_all("2026-08-25", cfg)
    assert "NEW_REPO:acme/healthy:new" in [s["key"] for s in out["stories"]]


def test_detect_all_warns_but_proceeds_inside_the_snapshot_age_ceiling(capsys):
    _write_snap("2026-08-23", {"acme": {"n": _repo(created_at="2026-08-22T00:00:00Z")}})
    cfg = _cfg(orgs=[{"name": "acme", "filter": "none"}])
    out = fc_stories.detect_all("2026-08-25", cfg)  # gap of 2 days
    assert [s["key"] for s in out["stories"]] == ["NEW_REPO:acme/n:new"]
    assert "warn: no snapshot for 2026-08-25, using 2026-08-23" in capsys.readouterr().out


def test_detect_all_dies_when_the_newest_snapshot_exceeds_the_age_ceiling(capsys):
    _write_snap("2026-08-21", {"acme": {"n": _repo(created_at="2026-08-20T00:00:00Z")}})
    cfg = _cfg(orgs=[{"name": "acme", "filter": "none"}])
    with pytest.raises(SystemExit):
        fc_stories.detect_all("2026-08-25", cfg)  # gap of 4 days > MAX_SNAPSHOT_AGE_DAYS (3)
    assert "run fc_snapshot.py first" in capsys.readouterr().out


def test_detect_all_passes_the_baseline_to_the_novelty_detectors():
    # Wiring for the set-membership fix: a repo created ON the baseline date but
    # after that day's snapshot was taken must still surface as NEW_REPO.
    base_repos = {"acme": {"steady": _repo(created_at="2020-01-01T00:00:00Z")}}
    cur_repos = {
        "acme": {
            "steady": _repo(created_at="2020-01-01T00:00:00Z"),
            "late-arrival": _repo(created_at="2026-08-18T22:00:00Z"),
        }
    }
    _write_snap("2026-08-18", base_repos)
    _write_snap("2026-08-25", cur_repos)
    cfg = _cfg(orgs=[{"name": "acme", "filter": "none"}])
    out = fc_stories.detect_all("2026-08-25", cfg)
    assert out["baseline_date"] == "2026-08-18"
    assert "NEW_REPO:acme/late-arrival:new" in [s["key"] for s in out["stories"]]


def test_detect_all_survives_a_baseline_where_the_org_errored():
    # The confirmed incident: the baseline snapshot recorded the org in
    # "errors" (build_snapshot omits it from "orgs" entirely), and the next
    # run announced three repos created in 2020 as NEW_REPO at top priority —
    # filling per_org_story_cap and permanently burning their mention-once
    # keys. With the unknown-org fallback, the ancient catalog stays silent.
    d = fc_common.snapshot_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "2026-08-18.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "date": "2026-08-18",
                "orgs": {},
                "errors": {"acme": "HTTP 500 from the org listing"},
            }
        )
    )
    cur = {"acme": {f"old{i}": _repo(created_at="2020-01-01T00:00:00Z") for i in range(3)}}
    _write_snap("2026-08-25", cur)
    cfg = _cfg(orgs=[{"name": "acme", "filter": "none"}])
    out = fc_stories.detect_all("2026-08-25", cfg)
    assert out["baseline_date"] == "2026-08-18"
    assert [s for s in out["stories"] if s["type"] == "NEW_REPO"] == []


def test_cli_detect_prints_contract_and_mark_round_trips(tmp_path, capsys):
    fc_common.atomic_write_json(
        fc_common.config_path(), {"orgs": [{"name": "acme", "filter": "none"}]}
    )
    _write_snap("2026-08-25", {"acme": {"n": _repo(created_at="2026-08-22T00:00:00Z")}})
    rc = fc_stories.main(["detect", "--date", "2026-08-25"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert set(out) == {
        "run_date",
        "baseline_date",
        "already_shipped_this_week",
        "thin",
        "stories",
    }

    f = tmp_path / "stories.json"
    f.write_text(json.dumps(out))
    rc = fc_stories.main(["mark", "--stories", str(f), "--episode-uri", "spotify:episode:e9"])
    assert rc == 0
    marked = fc_stories.load_reported()[out["stories"][0]["key"]]
    assert marked["episode_uri"] == "spotify:episode:e9"
    assert marked["date"] == "2026-08-25"  # the file's run_date, not a second clock read


def test_mark_records_a_web_only_mp3_url_as_the_episode_identity(tmp_path, capsys):
    """This show ships web-only (#155), so the identity `mark` records is the
    published mp3 URL — the CLI must not describe or constrain it to a Spotify URI."""
    fc_common.atomic_write_json(
        fc_common.config_path(), {"orgs": [{"name": "acme", "filter": "none"}]}
    )
    _write_snap("2026-08-25", {"acme": {"n": _repo(created_at="2026-08-22T00:00:00Z")}})
    fc_stories.main(["detect", "--date", "2026-08-25"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    f = tmp_path / "stories.json"
    f.write_text(json.dumps(out))
    url = "https://audio.example/frontier-commits/daily-digest-august-24-2026.mp3"

    assert fc_stories.main(["mark", "--stories", str(f), "--episode-uri", url]) == 0

    assert fc_stories.load_reported()[out["stories"][0]["key"]]["episode_uri"] == url
    with pytest.raises(SystemExit):
        fc_stories.main(["mark", "--stories", str(f), "--help"])
    help_text = capsys.readouterr().out
    assert "spotify" not in help_text.lower(), (
        "--episode-uri still advertises a Spotify URI; the weekly run passes an mp3 URL"
    )


def test_cli_detect_rejects_bad_date_and_lookback(tmp_path):
    fc_common.atomic_write_json(
        fc_common.config_path(), {"orgs": [{"name": "acme", "filter": "none"}]}
    )
    with pytest.raises(SystemExit):
        fc_stories.main(["detect", "--date", "not-a-date"])
    with pytest.raises(SystemExit):
        fc_stories.main(["detect", "--date", "2026-08-25", "--lookback-days", "0"])


def test_cli_mark_dies_on_malformed_stories_file(tmp_path):
    missing = tmp_path / "nope.json"
    with pytest.raises(SystemExit):
        fc_stories.main(["mark", "--stories", str(missing), "--episode-uri", "u"])
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"stories": [{"no-key": True}], "run_date": "2026-08-25"}))
    with pytest.raises(SystemExit):
        fc_stories.main(["mark", "--stories", str(bad), "--episode-uri", "u"])
    no_date = tmp_path / "no_date.json"
    no_date.write_text(json.dumps({"stories": [{"key": "A:o/r:new"}]}))
    with pytest.raises(SystemExit):
        fc_stories.main(["mark", "--stories", str(no_date), "--episode-uri", "u"])
    assert fc_stories.load_reported() == {}  # nothing was partially marked


def test_release_train_collapses_to_the_newest_tag_per_repo():
    snap = {
        "orgs": {
            "acme": {
                "repos": {},
                "releases": [
                    {"repo": "train", "tag": "v2.1.237", "published_at": "2026-08-21T00:00:00Z"},
                    {"repo": "train", "tag": "v2.1.239", "published_at": "2026-08-22T12:00:00Z"},
                    {"repo": "train", "tag": "v2.1.238", "published_at": "2026-08-22T00:00:00Z"},
                    {"repo": "solo", "tag": "v1", "published_at": "2026-08-22T00:00:00Z"},
                ],
            }
        }
    }
    cur = {"acme": {"train": _repo(stars=900), "solo": _repo(stars=900)}}
    got = fc_stories.detect_releases(snap, "2026-08-18", cur, _cfg())
    assert [s["key"] for s in got] == [
        "RELEASE:acme/solo:v1",
        "RELEASE:acme/train:v2.1.239",
    ]
