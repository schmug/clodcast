"""Property tests for the week-seeded rotation (fc_script_plan).

The 6x6 SHAPE_ORDERS_W table is FIXED data, machine-generated and verified —
these tests are what makes replacing it non-optional to re-verify. The daily
show's stride bug (PR #108) passed a year-long coverage test while pinning
positions to one shape for days at a time; the consecutive-weeks and
non-rotational tests below exist so that class of regression goes red here.

Two modelling rules keep these tests honest:

- Rotation properties are driven off REAL consecutive Mondays through
  week_index(date), never bare integers — production only ever sees dates, and
  a multiplier seed (y*53+w) passes every bare-integer test while stepping by 2
  at 52-week ISO year ends, skipping a rotation row for real weeks.
- The date argument is the ONLY clock: a guard subclass whose today() raises
  proves neither build_plan nor the CLI ever consults the wall clock.
"""

import datetime as dt
import json

import pytest

import fc_script_plan as sp


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


def test_week_index_steps_by_exactly_one_across_real_consecutive_mondays():
    indices = [sp.week_index(m) for m in _MONDAY_SPAN]
    for prev, nxt in zip(indices, indices[1:], strict=False):
        assert nxt - prev == 1  # kills every multiplier formula (y*52+w, y*53+w, ...)


def test_week_index_is_constant_across_one_iso_week():
    monday = dt.date.fromisoformat("2026-08-31")
    expected = sp.week_index("2026-08-31")
    for day in range(7):
        assert sp.week_index((monday + dt.timedelta(days=day)).isoformat()) == expected


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
    assert len(plan["segments"]) == 4
    assert sp.main(["plan", "--date", "2026-08-31", "--stories", "3"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["intro_mode"] in sp.INTRO_MODES_W


def test_cli_requires_an_explicit_date():
    with pytest.raises(SystemExit):
        sp.main(["plan", "--stories", "3"])


def test_square_is_latin_both_ways():
    n = len(sp.SHAPE_ORDERS_W)
    for row in sp.SHAPE_ORDERS_W:
        assert sorted(row) == list(range(n))
    for c in range(n):
        assert sorted(r[c] for r in sp.SHAPE_ORDERS_W) == list(range(n))


def test_no_position_holds_its_shape_two_weeks_running():
    weeks = [sp.week_index(m) for m in _MONDAY_SPAN]
    for prev, nxt in zip(weeks, weeks[1:], strict=False):
        for pos in range(12):  # includes wrap positions
            assert sp.segment_shape(prev, pos) != sp.segment_shape(nxt, pos)


def test_every_position_sees_every_shape_within_one_bank_cycle():
    n = len(sp.STORY_SHAPES_W)
    weeks = [sp.week_index(m) for m in _MONDAY_SPAN]
    for start in range(len(weeks) - n + 1):
        for pos in range(n):
            seen = {sp.segment_shape(weeks[start + k], pos) for k in range(n)}
            assert seen == set(sp.STORY_SHAPES_W)


def test_intro_rotation_covers_the_bank_over_consecutive_mondays():
    n = len(sp.INTRO_MODES_W)
    for start in range(len(_MONDAY_SPAN) - n + 1):
        modes = {sp.build_plan(_MONDAY_SPAN[start + k], 1)["intro_mode"] for k in range(n)}
        assert modes == set(sp.INTRO_MODES_W)


def test_outro_rotation_covers_the_bank_over_consecutive_mondays():
    n = len(sp.OUTRO_MODES_W)
    for start in range(len(_MONDAY_SPAN) - n + 1):
        modes = {sp.build_plan(_MONDAY_SPAN[start + k], 1)["outro_mode"] for k in range(n)}
        assert modes == set(sp.OUTRO_MODES_W)


def test_rows_are_pairwise_non_rotational():
    n = len(sp.SHAPE_ORDERS_W)
    sigs = {tuple((x - row[0]) % n for x in row) for row in sp.SHAPE_ORDERS_W}
    assert len(sigs) == n


def test_transitions_are_not_locked_to_the_shape_rotation():
    n = len(sp.SHAPE_ORDERS_W)
    assert sp.TRANSITION_ROW_OFFSET_W % n != 0
    for monday in _MONDAY_SPAN[:20]:
        wk = sp.week_index(monday)
        # Compare the UNDERLYING rows, not names from two disjoint banks (which
        # could never collide): a zero offset would make these identical.
        shape_row = [sp.SHAPE_ORDERS_W[wk % n][p] for p in range(n)]
        move_row = [sp.SHAPE_ORDERS_W[(wk + sp.TRANSITION_ROW_OFFSET_W) % n][p] for p in range(n)]
        assert shape_row != move_row


def test_build_plan_is_deterministic_and_shaped():
    a = sp.build_plan("2026-08-31", 4)
    b = sp.build_plan("2026-08-31", 4)
    assert a == b
    assert len(a["segments"]) == 4
    assert a["segments"][0]["move"] == "cold"
    band0 = a["segments"][0]["band"]
    assert band0 == list(sp.LEAD_BAND) or band0 == sp.LEAD_BAND
    assert a["intro_mode"] in sp.INTRO_MODES_W and a["outro_mode"] in sp.OUTRO_MODES_W
    assert sp.build_plan("2026-09-07", 4) != a  # next week differs


def test_cli_prints_json_contract(capsys):
    assert sp.main(["plan", "--date", "2026-08-31", "--stories", "3"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert {"week_row", "intro_mode", "intro_text", "outro_mode", "outro_text", "segments"} <= set(
        out
    )
