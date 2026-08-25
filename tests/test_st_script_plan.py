"""Property tests for Surface Tension's week-seeded assignment layer.

Both fixed tables — ROLE_ORDERS_W (5x5, roles) and VOICE_ORDERS_W (4x4, voices)
— are machine-generated and machine-verified; these tests are what makes
replacing either one non-optional to re-verify. The daily show's stride bug
(PR #108) passed a year-long coverage test while pinning positions to one shape
for days at a time, so coverage alone is not evidence: the consecutive-weeks and
non-rotational tests below are the ones that go red on that class of regression.

Two modelling rules carried over from tests/test_fc_script_plan.py:

- Rotation properties are driven off REAL consecutive Mondays through
  week_index(date), never bare integers — production only ever sees dates, and a
  multiplier seed (y*53+w) passes every bare-integer test while stepping by 2 at
  52-week ISO year ends, skipping a rotation row for real weeks.
- The date argument is the ONLY clock: a guard subclass whose today() raises
  proves neither build_plan nor the CLI ever consults the wall clock.
"""

import datetime as dt
import json

import pytest

import fc_script_plan
import st_script_plan as sp


def _mondays(start_iso: str, count: int) -> list[str]:
    """`count` consecutive real-world Mondays starting at `start_iso` (a Monday)."""
    d = dt.date.fromisoformat(start_iso)
    assert d.isoweekday() == 1, "test helper must start on a Monday"
    return [(d + dt.timedelta(weeks=k)).isoformat() for k in range(count)]


# ~140 Mondays from 2026-01-05 span BOTH kinds of ISO year boundary: 2026 is a
# 53-week ISO year (2026-12-28 starts W53) and 2027 is a 52-week one. Every
# rotation property below walks this span so a seed that misbehaves at either
# boundary goes red.
_MONDAY_SPAN = _mondays("2026-01-05", 140)
_WEEKS = [sp.week_index(m) for m in _MONDAY_SPAN]

_N_ROLES = len(sp.ROLES)
_N_VOICES = len(sp.CAST)


def _role_by_voice(scene: dict) -> dict[str, str]:
    return {t["voice"]: t["role"] for t in scene["turns"]}


def _post_scenes(plan: dict) -> list[dict]:
    return [s for s in plan["scenes"] if s["post_index"] is not None]


# --- the seed -------------------------------------------------------------


def test_week_index_is_reused_from_frontier_commits_not_re_derived():
    # The y*53+w trap is solved once, in fc_script_plan. Re-deriving it here is
    # how it comes back: this asserts the SAME function object, not a copy.
    assert sp.week_index is fc_script_plan.week_index


def test_the_date_argument_is_the_only_clock(monkeypatch, capsys):
    class NoTodayDate(dt.date):
        # Only today() is overridden — fromisoformat/arithmetic must keep working.
        @classmethod
        def today(cls):
            raise AssertionError("wall clock consulted — the --date argument is the only clock")

    # sp.dt and fc_script_plan.dt are the same stdlib module object, so this one
    # patch covers the borrowed week_index too.
    monkeypatch.setattr(sp.dt, "date", NoTodayDate)
    plan = sp.build_plan("2026-08-31", 4)
    assert len(_post_scenes(plan)) == 4
    assert sp.main(["plan", "--date", "2026-08-31", "--posts", "3"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["date"] == "2026-08-31"


def test_cli_requires_an_explicit_date():
    with pytest.raises(SystemExit):
        sp.main(["plan", "--posts", "4"])


def test_cli_rejects_a_non_iso_date_and_a_useless_post_count():
    for argv in (
        ["plan", "--date", "20260831", "--posts", "4"],  # 3.11+ fromisoformat accepts this
        ["plan", "--date", "2026-02-30", "--posts", "4"],
        ["plan", "--date", "2026-08-31", "--posts", "0"],
    ):
        with pytest.raises(SystemExit):
            sp.main(argv)


# --- the tables -----------------------------------------------------------


@pytest.mark.parametrize(
    ("square", "n"),
    [(sp.ROLE_ORDERS_W, _N_ROLES), (sp.VOICE_ORDERS_W, _N_VOICES)],
)
def test_squares_are_latin_both_ways(square, n):
    assert len(square) == n
    for row in square:
        assert sorted(row) == list(range(n))
    for c in range(n):
        assert sorted(r[c] for r in square) == list(range(n))


@pytest.mark.parametrize(
    ("square", "n"),
    [(sp.ROLE_ORDERS_W, _N_ROLES), (sp.VOICE_ORDERS_W, _N_VOICES)],
)
def test_square_rows_are_pairwise_non_rotational(square, n):
    # Value-space rotations: a fully cyclic square collapses to one signature and
    # every row then carries the same adjacencies forever.
    sigs = {tuple((x - row[0]) % n for x in row) for row in square}
    assert len(sigs) == n


@pytest.mark.parametrize("square", [sp.ROLE_ORDERS_W, sp.VOICE_ORDERS_W], ids=["roles", "voices"])
def test_square_rows_are_not_positional_rotations_of_each_other(square):
    # The other half of the stride lesson: a square whose rows are left-shifts of
    # one another preserves every adjacency, which is exactly what the daily
    # show's stride formula did while passing its coverage test.
    for i, a in enumerate(square):
        n = len(a)
        shifts = {tuple(a[(j + k) % n] for j in range(n)) for k in range(n)}
        for b in square[i + 1 :]:
            assert tuple(b) not in shifts


@pytest.mark.parametrize("square", [sp.ROLE_ORDERS_W, sp.VOICE_ORDERS_W], ids=["roles", "voices"])
def test_no_column_repeats_between_consecutive_rows(square):
    rows = list(square) + [square[0]]  # cyclic: the wrap from last row to first counts
    for prev, nxt in zip(rows, rows[1:], strict=False):
        for c in range(len(prev)):
            assert prev[c] != nxt[c]


# --- roles rotate; voices don't -------------------------------------------


def test_the_cast_is_fixed_and_excludes_the_daily_show_house_voice():
    assert len(sp.CAST) == _N_VOICES == 4
    assert len(set(sp.CAST)) == 4
    # docs/durable-voices.md + spec 4.8: reusing the daily narrator here would
    # make the digest's host a panelist on another show.
    assert "house" not in {v.lower() for v in sp.CAST}


def test_no_voice_holds_the_same_role_two_weeks_running():
    for post in range(_N_ROLES * 2):  # includes wrap positions past the bank
        for prev, nxt in zip(_WEEKS, _WEEKS[1:], strict=False):
            before = sp.seat_roles(prev, post)
            after = sp.seat_roles(nxt, post)
            for voice in sp.CAST:
                assert before[voice] != after[voice]


def test_every_voice_sees_every_role_within_one_bank_cycle():
    for post in range(_N_ROLES):
        for start in range(len(_WEEKS) - _N_ROLES + 1):
            for voice in sp.CAST:
                seen = {sp.seat_roles(_WEEKS[start + k], post)[voice] for k in range(_N_ROLES)}
                assert seen == set(sp.ROLES)


def test_exactly_one_role_sits_out_each_scene_and_the_bench_itself_rotates():
    for post in range(_N_ROLES):
        benched = [sp.benched_role(w, post) for w in _WEEKS]
        for prev, nxt in zip(benched, benched[1:], strict=False):
            assert prev != nxt  # the bench is a rotation axis, not an accident
        for start in range(len(benched) - _N_ROLES + 1):
            assert set(benched[start : start + _N_ROLES]) == set(sp.ROLES)
        for w in _WEEKS[:20]:
            seated = sp.seat_roles(w, post)
            assert len(seated) == 4
            assert set(seated.values()) == set(sp.ROLES) - {sp.benched_role(w, post)}


# --- stances --------------------------------------------------------------


def test_stance_is_not_locked_to_the_role_rotation():
    # The tempting simplification is "advocate argues for, skeptic argues
    # against", which welds the two axes together. Across the span the for-voice
    # must be seen holding every seated role, not just the advocate's.
    for post in range(4):
        for_roles = set()
        against_roles = set()
        for w in _WEEKS:
            scene = sp.post_scene(w, post, index=post + 3, kind="post")
            roles = _role_by_voice(scene)
            for_roles.add(roles[scene["stances"]["for"]])
            against_roles.add(roles[scene["stances"]["against"]])
        # switchboard is never a stance holder (see the test below), so four
        # roles minus the conditional one is full coverage.
        assert for_roles == set(sp.ROLES) - {"switchboard"}
        assert against_roles == set(sp.ROLES) - {"switchboard"}


def test_role_and_stance_rows_walk_independently_across_the_week_span():
    # 5 role rows x 4 stance rows: locked axes would show far fewer than 20
    # distinct pairings over 20 consecutive weeks.
    combos = {(sp.role_row_index(w, 0), sp.stance_row_index(w, 0)) for w in _WEEKS[:20]}
    assert len(combos) == _N_ROLES * _N_VOICES


def test_both_aligned_and_crossed_stance_scenes_occur():
    notes = {
        sp.post_scene(w, p, index=p + 3, kind="post")["stance_note"]
        for w in _WEEKS[:20]
        for p in range(4)
    }
    assert {"aligned", "crossed"} <= notes


def test_a_stance_is_never_handed_to_the_conditional_switchboard():
    # A stance on a turn that may not render is a scene whose argument can vanish.
    for w in _WEEKS:
        for p in range(_N_ROLES):
            scene = sp.post_scene(w, p, index=p + 3, kind="post")
            roles = _role_by_voice(scene)
            stances = scene["stances"]
            assert stances["for"] != stances["against"]
            assert roles[stances["for"]] != "switchboard"
            assert roles[stances["against"]] != "switchboard"


# --- turn order, last word, and the conditional slot ----------------------


def test_the_last_word_is_never_a_conditional_turn():
    for w in _WEEKS:
        for p in range(_N_ROLES):
            for kind in ("post", "open-lines"):
                scene = sp.post_scene(w, p, index=p + 3, kind=kind)
                closer = scene["turns"][-1]
                assert closer["voice"] == scene["last_word"]
                assert not closer["conditional"]
                assert not scene["turns"][0]["conditional"]
                assert scene["turns"][0]["voice"] == scene["opener"]


def test_every_voice_opens_and_closes_a_post_scene_over_the_span():
    for p in range(4):
        openers = set()
        closers = set()
        for w in _WEEKS:
            scene = sp.post_scene(w, p, index=p + 3, kind="post")
            openers.add(scene["opener"])
            closers.add(scene["last_word"])
        assert openers == set(sp.CAST)
        assert closers == set(sp.CAST)


def test_turn_order_seats_every_voice_exactly_once():
    for w in _WEEKS[:40]:
        for p in range(_N_ROLES):
            scene = sp.post_scene(w, p, index=p + 3, kind="post")
            voices = [t["voice"] for t in scene["turns"]]
            assert sorted(voices) == sorted(sp.CAST)
            assert [t["order"] for t in scene["turns"]] == list(range(4))


def test_the_switchboard_turn_is_marked_conditional_and_carries_its_drop_rule():
    seen_conditional = False
    for w in _WEEKS[:20]:
        for p in range(_N_ROLES):
            scene = sp.post_scene(w, p, index=p + 3, kind="post")
            for turn in scene["turns"]:
                if turn["role"] == "switchboard":
                    seen_conditional = True
                    assert turn["conditional"] is True
                    assert turn["condition"]  # non-empty: what must be true to render
                    assert turn["if_absent"]  # non-empty: what to do when it isn't
                    assert "invent" in turn["if_absent"]
                else:
                    assert turn["conditional"] is False
                    assert turn["condition"] is None
                    assert turn["if_absent"] is None
    assert seen_conditional


def test_the_open_lines_scene_always_seats_the_discussion_desk():
    # Spec 5 scene 6 is built around the board. A week that benched the
    # switchboard there would render the show's signature scene desk-less, so
    # the plan moves the open-lines slot rather than fabricating a report.
    for w in _WEEKS:
        for posts in (2, 3, 4, 6):
            plan = sp.build_plan_for_week(w, posts)
            scene = next(s for s in plan["scenes"] if s["kind"] == "open-lines")
            assert "switchboard" in set(_role_by_voice(scene).values())
            assert plan["open_lines_post_index"] == scene["post_index"]


def test_the_open_lines_slot_is_not_always_the_last_post():
    slots = {sp.build_plan_for_week(w, 4)["open_lines_post_index"] for w in _WEEKS[:20]}
    assert len(slots) > 1


# --- bits -----------------------------------------------------------------


def test_bit_ownership_rotates_across_the_whole_cast():
    for bit in ("vote_desk", "vote_desk_foil", "rapid_fire"):
        for start in range(len(_WEEKS) - _N_VOICES + 1):
            owners = {
                sp.build_plan_for_week(_WEEKS[start + k], 4)["bits"][bit] for k in range(_N_VOICES)
            }
            assert owners == set(sp.CAST)


def test_the_weeks_bits_never_collide_on_one_voice():
    for w in _WEEKS[:40]:
        bits = sp.build_plan_for_week(w, 4)["bits"]
        assert len(set(bits.values())) == len(bits)


# --- episode shape and the JSON contract ----------------------------------


def test_scene_running_order_matches_the_design_spec():
    plan = sp.build_plan("2026-08-31", 4)
    kinds = [s["kind"] for s in plan["scenes"]]
    assert len(kinds) == 8  # spec 5: 4 posts -> 8 chapters
    assert kinds[0] == "cold-open"
    assert kinds[1] == "vote-desk"
    assert kinds[-2] == "rapid-fire"
    assert kinds[-1] == "sign-off"
    assert kinds.count("open-lines") == 1
    assert [s["index"] for s in plan["scenes"]] == list(range(1, 9))
    for scene in plan["scenes"]:
        if scene["kind"] in ("post", "open-lines"):
            assert scene["post_index"] is not None
            assert scene["bench"] in sp.ROLES
        else:
            # spec 5: the frames carry no source and no role rotation.
            assert scene["post_index"] is None
            assert scene["bench"] is None
            assert scene["stances"] is None
    assert [s["post_index"] for s in _post_scenes(plan)] == [0, 1, 2, 3]


def test_a_single_post_episode_still_produces_a_whole_show():
    plan = sp.build_plan("2026-08-31", 1)
    assert [s["kind"] for s in plan["scenes"]] == [
        "cold-open",
        "vote-desk",
        "open-lines",
        "rapid-fire",
        "sign-off",
    ]


def test_every_turn_carries_the_full_key_set():
    plan = sp.build_plan("2026-08-31", 4)
    for scene in plan["scenes"]:
        assert set(scene) == set(sp.SCENE_FIELDS)
        for turn in scene["turns"]:
            assert set(turn) == set(sp.TURN_FIELDS)  # nulls, never absent keys
            assert turn["voice"] in sp.CAST
            assert list(turn["turns"]) == sorted(turn["turns"])
            assert turn["turns"][0] >= 0


def test_no_plan_ever_names_a_caller_or_a_voice_outside_the_cast():
    # Spec 4.4 / 9: there is no `caller` SPEAKER, permanently. The word survives
    # in exactly one place - the switchboard's drop rule, which forbids inventing
    # one - so this checks the speaker banks, not the prose.
    assert "caller" not in sp.ROLES
    assert "caller" not in sp.FRAME_PARTS
    assert "caller" in sp.SWITCHBOARD_IF_ABSENT  # the prohibition itself must stay
    for w in _WEEKS[:20]:
        for scene in sp.build_plan_for_week(w, 4)["scenes"]:
            for turn in scene["turns"]:
                assert turn["voice"] in sp.CAST
                assert turn["role"] in (None, *sp.ROLES)
                assert turn["part"] in {*sp.ROLES, *sp.FRAME_PARTS}


def test_turn_budgets_come_from_the_roles_bank():
    plan = sp.build_plan("2026-08-31", 4)
    for scene in _post_scenes(plan):
        for turn in scene["turns"]:
            assert tuple(turn["turns"]) == tuple(sp.ROLES[turn["role"]]["turns"])
    # spec 4.3: the conditional desk is the only role whose floor is zero.
    assert sp.ROLES["switchboard"]["turns"][0] == 0
    for name, role in sp.ROLES.items():
        if name != "switchboard":
            assert role["turns"][0] >= 1
            assert role["conditional"] is False


# --- determinism ----------------------------------------------------------


def test_same_date_and_post_count_rebuild_a_byte_identical_plan():
    for date in ("2026-08-31", "2026-12-28", "2027-01-04", "2027-12-27", "2028-01-03"):
        first = json.dumps(sp.build_plan(date, 4), sort_keys=True)
        second = json.dumps(sp.build_plan(date, 4), sort_keys=True)
        assert first == second


def test_the_plan_is_constant_within_one_iso_week_and_moves_at_its_boundary():
    monday = dt.date.fromisoformat("2026-08-31")
    baseline = json.dumps(sp.build_plan("2026-08-31", 4), sort_keys=True)
    for day in range(1, 7):
        same_week = (monday + dt.timedelta(days=day)).isoformat()
        plan = sp.build_plan(same_week, 4)
        plan["date"] = "2026-08-31"  # the only field that tracks the day, not the week
        assert json.dumps(plan, sort_keys=True) == baseline
    assert json.dumps(sp.build_plan("2026-09-07", 4), sort_keys=True) != baseline


def test_the_rotation_steps_by_one_across_both_kinds_of_iso_year_boundary():
    # 2026 is a 53-week ISO year; 2027 is a 52-week one. A y*53+w seed skips a
    # row at the 52-week end and would show a step of 2 here.
    for earlier, later in (("2026-12-28", "2027-01-04"), ("2027-12-27", "2028-01-03")):
        before = sp.build_plan(earlier, 4)
        after = sp.build_plan(later, 4)
        assert (before["week_row"] + 1) % _N_ROLES == after["week_row"]
        assert before["week"] + 1 == after["week"]


def test_cli_prints_one_json_object_as_its_final_stdout_line(capsys):
    assert sp.main(["plan", "--date", "2026-08-31", "--posts", "4"]) == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    out = json.loads(last)
    assert {
        "date",
        "week",
        "week_row",
        "cast",
        "roles",
        "bits",
        "open_lines_post_index",
        "scenes",
    } <= set(out)
    assert out == sp.build_plan("2026-08-31", 4)
