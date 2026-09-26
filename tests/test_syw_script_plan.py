"""Show Your Work plan layer (spec §4.3)."""

from __future__ import annotations

import datetime as dt
import itertools
import json

import pytest

import syw_gather
import syw_script_plan as sp

CONFIG = dict(syw_gather.DEFAULT_CONFIG)
TODAY = "2026-09-27"


def item(url, lab="openai", kind="research", date="2026-09-20", **kw):
    return {
        "url": url, "source": "s", "lab": lab, "kind": kind, "title": url.rsplit("/", 1)[-1],
        "summary": "", "date": date, "date_precision": kw.pop("precision", "day"),
        "role": "lead", "mentions": [], "seen": kw.pop("seen", False),
        "first_observed": kw.pop("first_observed", date), **kw,
    }  # fmt: skip


def plan(lead, check=(), history=(), **cfg):
    return sp.build_plan(
        {"lead": list(lead), "check": list(check)}, list(history), {**CONFIG, **cfg}, TODAY
    )


def test_rotations_do_not_lock():
    """Every pair of banks except the exempt one covers its full product cycle over
    consecutive weeks — no rotation is a function of another (the daily show's
    fourth-wall/intro-mode lesson)."""
    start = dt.date(2026, 9, 28)
    for a, b in itertools.combinations(sp.ROTATION_BANKS, 2):
        if {a, b} == set(sp.EXEMPT_PAIR):
            continue
        n = len(sp.ROTATION_BANKS[a]) * len(sp.ROTATION_BANKS[b])
        pairs = {
            (r[a], r[b])
            for r in (sp.rotation((start + dt.timedelta(weeks=i)).isoformat()) for i in range(n))
        }
        assert len(pairs) == n, f"{a} x {b} locks: {len(pairs)} of {n} combinations"


def test_rotation_is_a_pure_function_of_the_date():
    assert sp.rotation("2026-09-27") == sp.rotation("2026-09-27")
    assert set(sp.rotation("2026-09-27")) == set(sp.ROTATION_BANKS)


def test_month_precision_items_age_from_first_observation():
    late_august = item(
        "https://a.test/aug", lab="anthropic", date="2026-08-01", precision="month",
        first_observed="2026-09-10",
    )  # fmt: skip
    # Aged from its month it would be 57 days old on 2026-09-27; from first sight, 17.
    assert sp.age_days(late_august, dt.date(2026, 9, 27)) == 17
    p = plan([late_august], max_age_days=21)
    assert p["feature"]["url"] == "https://a.test/aug"


def test_a_back_catalogue_month_item_first_observed_today_is_not_in_the_pool():
    """C1: after a partial (or no) seed, a never-seen 2024 alignment.anthropic.com
    post is first observed today. Its first-observed age is 0, but its MONTH ended
    long before any post first seen now could be new."""
    old = item(
        "https://a.test/2024", lab="anthropic", date="2024-12-01", precision="month",
        first_observed=TODAY,
    )  # fmt: skip
    p = plan([old])
    assert p["feature"] is None and p["skip"] == "no new lab items"


def test_a_month_item_whose_month_ends_inside_the_window_stays_in_the_pool():
    this_month = item(
        "https://a.test/sep", lab="anthropic", date="2026-09-01", precision="month",
        first_observed=TODAY,
    )  # fmt: skip
    assert plan([this_month])["feature"]["url"] == "https://a.test/sep"


def test_seen_and_stale_items_are_not_in_the_pool():
    p = plan(
        [item("https://a.test/seen", seen=True), item("https://a.test/old", date="2026-08-01")]
    )
    assert p["feature"] is None and p["skip"] == "no new lab items"


def test_newest_wins_without_penalties():
    p = plan(
        [
            item("https://a.test/old", date="2026-09-15"),
            item("https://a.test/new", date="2026-09-25"),
        ]
    )
    assert p["feature"]["url"] == "https://a.test/new"


def test_lab_penalty_hands_the_slot_to_another_lab():
    history = [{"date": "2026-09-20", "feature_url": "x", "lab": "anthropic", "kind": "research"}]
    p = plan(
        [
            item("https://a.test/anthropic", lab="anthropic", date="2026-09-26"),
            item("https://a.test/openai", lab="openai", date="2026-09-10"),
        ],
        history=history,
    )
    assert p["feature"]["url"] == "https://a.test/openai"


def test_kind_penalty_breaks_a_near_tie():
    history = [{"date": "2026-09-20", "feature_url": "x", "lab": "gdm", "kind": "incident"}]
    p = plan(
        [
            item("https://a.test/inc", kind="incident", date="2026-09-26"),
            item("https://a.test/res", kind="research", date="2026-09-20"),
        ],
        history=history,
        lab_penalty_weeks=0,
    )
    assert p["feature"]["url"] == "https://a.test/res"


def test_notices_are_never_features():
    p = plan([item("https://a.test/n", kind="notice", date="2026-09-26")])
    assert p["feature"] is None


def test_casebook_folds_incidents_before_notices_and_takes_one_slot():
    lead = [item("https://a.test/feature", kind="research", date="2026-09-26")]
    lead += [
        item(f"https://a.test/inc{i}", kind="incident", date="2026-09-2" + str(i)) for i in range(4)
    ]
    lead += [item("https://a.test/notice", kind="notice", date="2026-09-25")]
    lead += [
        item(f"https://a.test/res{i}", kind="research", date="2026-09-1" + str(i)) for i in range(5)
    ]
    p = plan(lead, lab_penalty_weeks=0)
    kinds = [b["kind"] for b in p["briefs"]]
    assert kinds == ["single", "single", "single", "casebook"]
    casebook = p["briefs"][-1]["items"]
    assert len(casebook) == 3 and all(it["kind"] == "incident" for it in casebook)
    assert "https://a.test/notice" in p["leftover"]


def test_checks_attach_by_mention_within_the_lookback_and_cap():
    feature = item("https://a.test/f", date="2026-09-26")
    checks = [
        {
            **item(f"https://metr.test/{i}", date=f"2026-09-{10 + i}"),
            "role": "check",
            "mentions": ["https://a.test/f"],
        }
        for i in range(5)
    ]
    checks.append(
        {
            **item("https://metr.test/old", date="2026-06-01"),
            "role": "check",
            "mentions": ["https://a.test/f"],
        }
    )
    checks.append(
        {**item("https://metr.test/other", date="2026-09-20"), "role": "check", "mentions": []}
    )
    p = plan([feature], check=checks)
    assert [c["url"] for c in p["checks"]] == [
        "https://metr.test/4", "https://metr.test/3", "https://metr.test/2",
    ]  # fmt: skip


def test_feature_override_and_exclude():
    lead = [
        item("https://a.test/new", date="2026-09-26"),
        item("https://a.test/old", date="2026-09-20"),
    ]
    over = sp.build_plan(
        {"lead": lead, "check": []}, [], CONFIG, TODAY, feature_override="https://a.test/old"
    )
    assert over["feature"]["url"] == "https://a.test/old" and over["feature"]["override"] is True
    ex = sp.build_plan(
        {"lead": lead, "check": []}, [], CONFIG, TODAY, exclude=("https://a.test/new",)
    )
    assert ex["feature"]["url"] == "https://a.test/old" and ex["excluded"] == ["https://a.test/new"]
    with pytest.raises(ValueError):
        sp.build_plan(
            {"lead": lead, "check": []}, [], CONFIG, TODAY, feature_override="https://a.test/nope"
        )


def test_cli_reuses_an_existing_plan(tmp_path, capsys):
    syw_gather.config_path().parent.mkdir(parents=True, exist_ok=True)
    syw_gather.config_path().write_text("{}")
    cands = tmp_path / "candidates.json"
    cands.write_text(
        json.dumps({"lead": [item("https://a.test/a", date="2026-09-26")], "check": []})
    )
    out = tmp_path / "plan.json"
    args = ["plan", "--date", TODAY, "--candidates", str(cands), "--out", str(out)]
    assert sp.main(args) == 0
    assert capsys.readouterr().out.strip() == "PLAN ok feature=https://a.test/a briefs=0 checks=0"
    cands.write_text(
        json.dumps({"lead": [item("https://a.test/b", date="2026-09-26")], "check": []})
    )
    assert sp.main(args) == 0
    assert capsys.readouterr().out.strip().startswith("PLAN reused feature=https://a.test/a")
    # --exclude re-plans and accumulates.
    assert sp.main(args + ["--exclude", "https://a.test/a"]) == 0
    assert json.loads(out.read_text())["feature"]["url"] == "https://a.test/b"


def test_cli_a_new_plan_clears_the_old_plans_writes(tmp_path, capsys):
    """I2: an accepted brief from plan 1 must not be assembled into plan 2. Digests
    are keyed by URL and stay valid, so they survive."""
    syw_gather.config_path().parent.mkdir(parents=True, exist_ok=True)
    syw_gather.config_path().write_text("{}")
    cands = tmp_path / "candidates.json"
    lead = [
        item("https://a.test/a", date="2026-09-26"),
        item("https://a.test/b", date="2026-09-25"),
    ]
    cands.write_text(json.dumps({"lead": lead, "check": []}))
    out = tmp_path / "plan.json"
    args = ["plan", "--date", TODAY, "--candidates", str(cands), "--out", str(out)]

    def stale_writes():
        (tmp_path / "writes").mkdir(exist_ok=True)
        (tmp_path / "writes" / "brief_00.json").write_text("{}")
        (tmp_path / "writes" / "terms.json").write_text("[]")

    (tmp_path / "digests").mkdir()
    (tmp_path / "digests" / "abc.json").write_text("{}")
    stale_writes()
    assert sp.main(args) == 0  # a NEW plan
    assert not (tmp_path / "writes").exists()
    assert (tmp_path / "digests" / "abc.json").is_file()
    stale_writes()
    assert sp.main(args) == 0  # the reuse path keeps this plan's writes
    assert (tmp_path / "writes" / "brief_00.json").is_file()
    assert sp.main(args + ["--exclude", "https://a.test/a"]) == 0  # a re-plan
    assert not (tmp_path / "writes").exists()


@pytest.mark.parametrize(
    "candidates, extra",
    [
        (None, []),  # missing file
        ("{not json", []),
        ('["a", "list"]', []),
        (json.dumps({"lead": [item("https://a.test/a")], "check": []}), ["--feature", "https://x"]),
    ],
)
def test_cli_failures_print_one_plan_failed_line(tmp_path, capsys, candidates, extra):
    syw_gather.config_path().parent.mkdir(parents=True, exist_ok=True)
    syw_gather.config_path().write_text("{}")
    cands = tmp_path / "candidates.json"
    if candidates is not None:
        cands.write_text(candidates)
    args = ["plan", "--date", TODAY, "--candidates", str(cands), "--out", str(tmp_path / "p.json")]
    assert sp.main(args + extra) == 1
    assert capsys.readouterr().out.strip().startswith("PLAN FAILED ")
    assert not (tmp_path / "p.json").exists()


def test_a_notice_cannot_be_forced_into_the_feature_slot():
    """M9: spec §4.3 — notices are never features, --feature included."""
    lead = [item("https://a.test/n", kind="notice", date="2026-09-26")]
    with pytest.raises(ValueError, match="notice"):
        sp.build_plan(
            {"lead": lead, "check": []}, [], CONFIG, TODAY, feature_override="https://a.test/n"
        )


def test_cli_reuse_skips_a_plan_whose_feature_already_shipped(tmp_path, capsys):
    """M11: a re-run after commit must not re-publish an immutable-cached slug."""
    syw_gather.config_path().parent.mkdir(parents=True, exist_ok=True)
    syw_gather.config_path().write_text("{}")
    cands = tmp_path / "candidates.json"
    cands.write_text(
        json.dumps({"lead": [item("https://a.test/a", date="2026-09-26")], "check": []})
    )
    args = ["plan", "--date", TODAY, "--candidates", str(cands), "--out", str(tmp_path / "p.json")]
    assert sp.main(args) == 0
    capsys.readouterr()
    syw_gather.seen_path().write_text(
        json.dumps({"https://a.test/a": {"date": TODAY, "role": "feature"}})
    )
    assert sp.main(args) == 0
    assert capsys.readouterr().out.strip() == "PLAN skip already shipped"


# --- #236: undated items never reach an episode; the weekly count is weekly ----


def test_an_undated_lead_item_is_not_in_the_pool():
    """#236 item 6: an item with no date aged from first observation has no bound —
    a never-seen 2024 post whose date the parser lost reads as brand new (the C1
    class). With no date there is no evidence it is new, so it never airs."""
    undated = item("https://a.test/undated", date="", first_observed=TODAY)
    p = plan([undated])
    assert p["feature"] is None and p["skip"] == "no new lab items"
    dated = item("https://a.test/dated", date="2026-09-26")
    p = plan([undated, dated])
    assert p["feature"]["url"] == "https://a.test/dated"
    assert p["briefs"] == [] and p["leftover"] == []


def test_this_week_counts_only_pool_items_from_the_last_seven_days():
    """#236 item 1: the cold open's ledger count is the week's, not the 21-day pool's."""
    p = plan(
        [
            item("https://a.test/new", lab="openai", date="2026-09-24"),
            item("https://a.test/new2", lab="google-deepmind", date="2026-09-21"),
            item("https://a.test/week-old", lab="anthropic", date="2026-09-20"),
            item("https://a.test/seen", lab="anthropic", date="2026-09-25", seen=True),
        ]
    )
    assert p["this_week"] == {"count": 2, "labs": ["google-deepmind", "openai"]}


def test_cli_reports_a_non_object_existing_plan_on_its_line(tmp_path, capsys):
    """#236 acceptance: the reuse path read a previous plan.json holding a list
    and raised AttributeError instead of printing PLAN FAILED."""
    syw_gather.config_path().parent.mkdir(parents=True, exist_ok=True)
    syw_gather.config_path().write_text("{}")
    cands = tmp_path / "candidates.json"
    cands.write_text(json.dumps({"lead": [], "check": []}))
    out = tmp_path / "plan.json"
    out.write_text('["not", "a", "plan"]')
    args = ["plan", "--date", TODAY, "--candidates", str(cands), "--out", str(out)]
    assert sp.main(args) == 1
    last = capsys.readouterr().out.strip()
    assert last.startswith("PLAN FAILED") and "must hold a JSON object" in last
