"""Property tests for the week-seeded assignment layer (st_script_plan).

Both squares are FIXED data, machine-searched and verified — these tests are
what makes replacing either one non-optional to re-verify. The daily show's
stride bug (PR #108) passed a year-long coverage test while pinning positions
4/9/14 to one shape for four days at a time, because `(1 + p) % 5 == 0`
cancelled the day-varying term; the consecutive-weeks and non-rotational tests
below exist so that class of regression goes red here rather than on air.

Three modelling rules keep these tests honest, inherited from
`test_fc_script_plan.py`:

- Rotation properties are driven off REAL consecutive Mondays through
  week_index(date), never bare integers — production only ever sees dates, and
  a multiplier seed (y*53+w) passes every bare-integer test while stepping by 2
  at 52-week ISO year ends, skipping a rotation row for real weeks.
- The date argument is the ONLY clock: a guard subclass whose today() raises
  proves neither build_plan nor the CLI ever consults the wall clock.
- Independence between two axes is asserted on the UNDERLYING rows, not on the
  names they resolve to. Two disjoint banks can never collide, so comparing
  names would pass even with the axes locked to a single row.
"""

import datetime as dt
import json

import pytest

import fc_script_plan as fc
import st_script_plan as sp


def _mondays(start_iso: str, count: int) -> list[str]:
    """`count` consecutive real-world Mondays starting at `start_iso` (a Monday)."""
    d = dt.date.fromisoformat(start_iso)
    assert d.isoweekday() == 1, "test helper must start on a Monday"
    return [(d + dt.timedelta(weeks=k)).isoformat() for k in range(count)]


# ~120 Mondays from 2026-01-05 span BOTH kinds of ISO year boundary: 2026 is a
# 53-week ISO year (2026-12-28 starts W53) and 2027 is a 52-week one. Every
# rotation property below walks this span so a seed that misbehaves at either
# boundary goes red.
_MONDAY_SPAN = _mondays("2026-01-05", 120)
_WEEKS = [sp.week_index(m) for m in _MONDAY_SPAN]

_N_ROLES = len(sp.ROLE_ORDERS_ST)
_N_VOICES = len(sp.STANCE_ORDERS_ST)


# --------------------------------------------------------------------------
# The seed
# --------------------------------------------------------------------------


def test_week_index_is_reused_from_frontier_commits_not_re_derived():
    # A second copy of this function is a second chance to reintroduce the
    # year*53+week trap. Identity, not equality of results on a sample.
    assert sp.week_index is fc.week_index


def test_week_index_steps_by_exactly_one_across_real_consecutive_mondays():
    for prev, nxt in zip(_WEEKS, _WEEKS[1:], strict=False):
        assert nxt - prev == 1  # kills every multiplier formula (y*52+w, y*53+w, ...)


def test_week_index_is_contiguous_across_both_year_boundary_kinds():
    # 2026 is a 53-week ISO year; 2027 is a 52-week one. Both boundaries step by 1.
    assert sp.week_index("2027-01-04") - sp.week_index("2026-12-28") == 1
    assert sp.week_index("2028-01-03") - sp.week_index("2027-12-27") == 1


def test_the_date_argument_is_the_only_clock(monkeypatch, capsys):
    class NoTodayDate(dt.date):
        # Only today() is overridden — fromisoformat/arithmetic must keep working.
        @classmethod
        def today(cls):
            raise AssertionError("wall clock consulted — the --date argument is the only clock")

    monkeypatch.setattr(sp.dt, "date", NoTodayDate)
    plan = sp.build_plan("2026-08-31", 4)
    assert len(plan["scenes"]) == 4
    assert sp.main(["plan", "--date", "2026-08-31", "--posts", "3"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["bits"]["vote_desk"]["voice"] in sp.VOICES_ST


def test_a_plan_is_identical_for_every_day_of_one_iso_week():
    monday = dt.date.fromisoformat("2026-08-31")
    expected = json.dumps(sp.build_plan("2026-08-31", 4), sort_keys=True)
    for day in range(7):
        iso = (monday + dt.timedelta(days=day)).isoformat()
        assert json.dumps(sp.build_plan(iso, 4), sort_keys=True) == expected


def test_the_same_week_rebuilds_a_byte_identical_plan_across_both_year_boundaries():
    # Idempotence is what makes a resumed or repeated run rebuild the same
    # episode; the boundary weeks are where a bad seed would drift.
    for monday in ("2026-12-28", "2027-01-04", "2027-12-27", "2028-01-03"):
        first = json.dumps(sp.build_plan(monday, 4), sort_keys=True)
        second = json.dumps(sp.build_plan(monday, 4), sort_keys=True)
        assert first == second


# --------------------------------------------------------------------------
# The tables — fixed data, verified both ways
# --------------------------------------------------------------------------


@pytest.mark.parametrize("square", [sp.ROLE_ORDERS_ST, sp.STANCE_ORDERS_ST])
def test_squares_are_latin_both_ways(square):
    n = len(square)
    for row in square:
        assert sorted(row) == list(range(n))  # every row a permutation of the bank
    for c in range(n):
        assert sorted(r[c] for r in square) == list(range(n))  # every column too


@pytest.mark.parametrize("square", [sp.ROLE_ORDERS_ST, sp.STANCE_ORDERS_ST])
def test_square_rows_are_pairwise_non_rotational(square):
    # Mutual rotations would make one row's adjacencies every row's adjacencies
    # — the property the stride bug faked while passing a coverage test.
    n = len(square)
    sigs = {tuple((x - row[0]) % n for x in row) for row in square}
    assert len(sigs) == n


def test_the_role_square_is_sized_for_one_role_more_than_the_cast():
    # Five roles over four voices: the fifth chair is the one that sits out, and
    # that is a rotation axis rather than an accident.
    assert len(sp.ROLES_ST) == len(sp.VOICES_ST) + 1
    assert len(sp.ROLE_ORDERS_ST) == len(sp.ROLES_ST)
    assert len(sp.STANCE_ORDERS_ST) == len(sp.VOICES_ST)


# --------------------------------------------------------------------------
# Role -> voice rotation
# --------------------------------------------------------------------------


def test_no_voice_holds_the_same_role_two_weeks_running():
    for prev, nxt in zip(_WEEKS, _WEEKS[1:], strict=False):
        for scene in range(9):  # includes wrap positions past the bank size
            before = sp.scene_roles(prev, scene)
            after = sp.scene_roles(nxt, scene)
            for role in sp.ROLES_ST:
                assert before[role] != after[role], (role, scene)


def test_every_voice_sees_every_role_within_one_bank_cycle():
    for start in range(len(_WEEKS) - _N_ROLES + 1):
        for scene in range(_N_ROLES):
            for voice in sp.VOICES_ST:
                seen = set()
                for k in range(_N_ROLES):
                    roles = sp.scene_roles(_WEEKS[start + k], scene)
                    seen |= {r for r, v in roles.items() if v == voice}
                assert seen == set(sp.ROLES_ST), (voice, scene)


def test_exactly_one_role_sits_out_each_scene_and_every_voice_is_cast():
    for week in _WEEKS[:20]:
        for scene in range(9):
            roles = sp.scene_roles(week, scene)
            assert set(roles) == set(sp.ROLES_ST)
            vacant = [r for r, v in roles.items() if v is None]
            assert vacant == [sp.sits_out(week, scene)]
            cast = [v for v in roles.values() if v is not None]
            assert sorted(cast) == sorted(sp.VOICES_ST)  # no voice doubled, none idle


def test_the_sat_out_role_rotates_and_covers_the_bank():
    for start in range(len(_WEEKS) - _N_ROLES + 1):
        for scene in range(_N_ROLES):
            out = [sp.sits_out(_WEEKS[start + k], scene) for k in range(_N_ROLES)]
            assert sorted(out) == sorted(sp.ROLES_ST)  # covers, and never repeats


def test_the_scene_axis_reshuffles_roles_within_one_episode():
    # Every scene of a five-post episode gets a different panel, so the anchor
    # of scene 0 is not the anchor of every scene.
    for week in _WEEKS[:20]:
        maps = [tuple(sorted(sp.scene_roles(week, s).items())) for s in range(_N_ROLES)]
        assert len(set(maps)) == _N_ROLES


# --------------------------------------------------------------------------
# Stances — independent of the role rotation
# --------------------------------------------------------------------------


def test_stance_sides_never_collide():
    for week in _WEEKS[:40]:
        for pos in range(9):
            stance = sp.scene_stance(week, pos)
            assert stance["for"] != stance["against"]
            assert stance["for"] in sp.VOICES_ST and stance["against"] in sp.VOICES_ST


def test_every_voice_takes_each_side_once_per_bank_cycle():
    for week in _WEEKS[:20]:
        fors = [sp.scene_stance(week, p)["for"] for p in range(_N_VOICES)]
        againsts = [sp.scene_stance(week, p)["against"] for p in range(_N_VOICES)]
        assert sorted(fors) == sorted(sp.VOICES_ST)
        assert sorted(againsts) == sorted(sp.VOICES_ST)


def test_stance_is_not_locked_to_the_role_rotation():
    # The TRANSITION_ROW_OFFSET_W lesson: two axes sharing one row collapse into
    # one. Compare the UNDERLYING rows, not names from two disjoint banks.
    assert sp.STANCE_SIDE_OFFSET_ST % _N_VOICES != 0
    pairings = {
        (sp._role_row(week, 0), sp._stance_row(week)) for week in _WEEKS[: _N_ROLES * _N_VOICES]
    }
    # 5-cycle x 4-cycle: every combination appears over one lcm span, so a given
    # panel never carries the same stance table twice in a row.
    assert len(pairings) == _N_ROLES * _N_VOICES


# --------------------------------------------------------------------------
# Turn order, the last word, and the conditional switchboard
# --------------------------------------------------------------------------


def test_turn_order_is_every_present_role_exactly_once():
    for week in _WEEKS[:20]:
        for scene in range(9):
            order = sp.scene_turn_order(week, scene)
            present = set(sp.ROLES_ST) - {sp.sits_out(week, scene)}
            assert sorted(order) == sorted(present)


def test_turn_order_is_not_locked_to_the_role_rotation():
    assert sp.TURN_ROW_OFFSET_ST % _N_ROLES != 0
    for week in _WEEKS[:20]:
        for scene in range(_N_ROLES):
            role_row = sp.ROLE_ORDERS_ST[sp._role_row(week, scene)]
            turn_row = sp.ROLE_ORDERS_ST[sp._turn_row(week, scene)]
            assert role_row != turn_row  # a zero offset would make these identical


def test_switchboard_is_the_only_conditional_role():
    assert tuple(sp.CONDITIONAL_ROLES_ST) == ("switchboard",)
    plan = sp.build_plan("2026-08-31", 4)
    for scene in plan["scenes"]:
        for turn in scene["turn_order"]:
            assert turn["conditional"] == (turn["role"] in sp.CONDITIONAL_ROLES_ST)
            assert ("condition" in turn) == turn["conditional"]


def test_a_scene_with_no_discussion_renders_no_switchboard_turn():
    # The plan proposes, the data disposes. The no-discussion ordering is precomputed
    # so a writer handed an empty comment list is never left resolving it — and
    # so nothing is tempted to invent a call to fill an assigned slot.
    for monday in _MONDAY_SPAN[:30]:
        for scene in sp.build_plan(monday, 5)["scenes"]:
            full = [t["role"] for t in scene["turn_order"]]
            fallback = scene["no_discussion"]["turn_order"]
            assert fallback == [r for r in full if r not in sp.CONDITIONAL_ROLES_ST]
            assert fallback  # never empties the scene
            assert scene["no_discussion"]["opens"] == fallback[0]
            assert scene["no_discussion"]["last_word"] == fallback[-1]
            assert scene["opens"] == full[0]
            assert scene["last_word"] == full[-1]


def test_no_discussion_fallback_never_hands_a_turn_to_the_switchboard():
    for monday in _MONDAY_SPAN[:30]:
        for scene in sp.build_plan(monday, 5)["scenes"]:
            assert scene["no_discussion"]["opens"] not in sp.CONDITIONAL_ROLES_ST
            assert scene["no_discussion"]["last_word"] not in sp.CONDITIONAL_ROLES_ST


def test_the_switchboard_takes_the_opening_or_closing_slot_sometimes():
    # If it never could, the conditional modelling above would be dead code.
    slots = set()
    for monday in _MONDAY_SPAN[:40]:
        for scene in sp.build_plan(monday, 5)["scenes"]:
            slots |= {scene["opens"], scene["last_word"]}
    assert "switchboard" in slots


def test_every_role_opens_a_scene_within_one_bank_cycle():
    # Paired with the last-word test below, this is what pins TURN_ROW_OFFSET_ST
    # to a value rather than merely to "non-zero": the sat-out role is dropped
    # from the ordering, so the offset decides which roles can reach its ends.
    for start in range(len(_WEEKS) - _N_ROLES + 1):
        for scene in range(_N_ROLES):
            seen = {sp.scene_turn_order(_WEEKS[start + k], scene)[0] for k in range(_N_ROLES)}
            assert len(seen) == _N_ROLES


def test_every_role_gets_the_last_word_within_one_bank_cycle():
    for start in range(len(_WEEKS) - _N_ROLES + 1):
        for scene in range(_N_ROLES):
            seen = {sp.scene_turn_order(_WEEKS[start + k], scene)[-1] for k in range(_N_ROLES)}
            assert len(seen) == _N_ROLES


# --------------------------------------------------------------------------
# Bit ownership
# --------------------------------------------------------------------------


def test_the_two_bits_never_land_on_one_voice():
    for week in _WEEKS[:40]:
        owners = sp.bit_owners(week)
        assert set(owners) == set(sp.BITS_ST)
        assert len(set(owners.values())) == len(sp.BITS_ST)
        assert set(owners.values()) <= set(sp.VOICES_ST)


def test_every_voice_runs_every_bit_within_one_cast_cycle():
    for start in range(len(_WEEKS) - _N_VOICES + 1):
        for bit in sp.BITS_ST:
            seen = {sp.bit_owners(_WEEKS[start + k])[bit] for k in range(_N_VOICES)}
            assert seen == set(sp.VOICES_ST)


def test_no_voice_runs_the_same_bit_two_weeks_running():
    for prev, nxt in zip(_WEEKS, _WEEKS[1:], strict=False):
        for bit in sp.BITS_ST:
            assert sp.bit_owners(prev)[bit] != sp.bit_owners(nxt)[bit]


# --------------------------------------------------------------------------
# The JSON contract and the CLI
# --------------------------------------------------------------------------


def test_build_plan_is_deterministic_and_shaped():
    a = sp.build_plan("2026-08-31", 4)
    b = sp.build_plan("2026-08-31", 4)
    assert a == b
    assert len(a["scenes"]) == 4
    assert a["cast"] == list(sp.VOICES_ST)
    assert a["week_row"] == sp.week_index("2026-08-31") % _N_ROLES
    assert sp.build_plan("2026-09-07", 4) != a  # next week differs


def test_scene_entries_carry_the_full_writer_contract():
    plan = sp.build_plan("2026-08-31", 4)
    for i, scene in enumerate(plan["scenes"]):
        assert scene["pos"] == i
        assert {
            "pos",
            "roles",
            "sits_out",
            "stance",
            "turn_order",
            "opens",
            "last_word",
            "no_discussion",
        } <= set(scene)
        for turn in scene["turn_order"]:
            assert turn["voice"] == scene["roles"][turn["role"]]
            assert turn["does"] == sp.ROLES_ST[turn["role"]]["does"]
            assert turn["turns"] == list(sp.ROLES_ST[turn["role"]]["turns"])


def test_cli_prints_one_json_object_as_its_final_line(capsys):
    assert sp.main(["plan", "--date", "2026-08-31", "--posts", "3"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert {"week_row", "cast", "bits", "scenes"} <= set(out)
    assert len(out["scenes"]) == 3


def test_cli_requires_an_explicit_date():
    with pytest.raises(SystemExit):
        sp.main(["plan", "--posts", "3"])


@pytest.mark.parametrize("bad", ["20260831", "2026-8-31", "2026-13-01", "tomorrow"])
def test_cli_refuses_a_date_that_is_not_a_real_iso_day(bad):
    # 3.11+ fromisoformat also accepts compact forms, which would vary the plan
    # by Python version — the regex gate is what keeps 3.10-3.12 in agreement.
    with pytest.raises(SystemExit):
        sp.main(["plan", "--date", bad, "--posts", "3"])


def test_cli_refuses_a_post_count_below_one():
    with pytest.raises(SystemExit):
        sp.main(["plan", "--date", "2026-08-31", "--posts", "0"])


# ---------------------------------------------------------------------------
# The plan must never instruct the writer to quote a caller (#173 fallout)
# ---------------------------------------------------------------------------


def test_the_plan_never_instructs_the_writer_to_quote_a_comment():
    """A plan that says "quote verbatim" is an instruction to fabricate.

    `/feed/comments` carries NO comment bodies (#173, spec section 2.3, pinned by
    tests/test_bubbles_fixtures.py) — an entry is navigation links only. So there
    is nothing to quote, and any quotable text a writer produces is invented.

    This is sharper than it sounds because the switchboard gate fires on the
    comment COUNT, which is real and non-zero: `slash:comments` was > 0 on 17 of
    17 live `/feed/hot` entries, and scene 6 is sourced from `/feed/hot`. So the
    condition goes TRUE on essentially every scene-6 post while the bodies remain
    unavailable — the worst possible combination for a model told a real quote
    exists.

    Asserted on the SERIALIZED plan rather than on the constants, because the
    JSON is what actually reaches the writer.
    """
    blob = json.dumps(sp.build_plan("2026-08-30", 4)).lower()
    for banned in ("quot", "verbatim", "caller", "takes the calls"):
        assert banned not in blob, f"the emitted plan tells the writer to {banned!r}"
