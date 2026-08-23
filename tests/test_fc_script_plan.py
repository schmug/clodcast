"""Property tests for the week-seeded rotation (fc_script_plan).

The 6x6 SHAPE_ORDERS_W table is FIXED data, machine-generated and verified —
these tests are what makes replacing it non-optional to re-verify. The daily
show's stride bug (PR #108) passed a year-long coverage test while pinning
positions to one shape for days at a time; the consecutive-weeks and
non-rotational tests below exist so that class of regression goes red here.
"""

import json

import fc_script_plan as sp


def test_square_is_latin_both_ways():
    n = len(sp.SHAPE_ORDERS_W)
    for row in sp.SHAPE_ORDERS_W:
        assert sorted(row) == list(range(n))
    for c in range(n):
        assert sorted(r[c] for r in sp.SHAPE_ORDERS_W) == list(range(n))


def test_no_position_holds_its_shape_two_weeks_running():
    for week in range(1, 320):
        for pos in range(12):  # includes wrap positions
            assert sp.segment_shape(week, pos) != sp.segment_shape(week + 1, pos)


def test_every_position_sees_every_shape_within_one_bank_cycle():
    n = len(sp.STORY_SHAPES_W)
    for start in range(1, 30):
        for pos in range(n):
            seen = {sp.segment_shape(start + k, pos) for k in range(n)}
            assert seen == set(sp.STORY_SHAPES_W)


def test_rows_are_pairwise_non_rotational():
    n = len(sp.SHAPE_ORDERS_W)
    sigs = {tuple((x - row[0]) % n for x in row) for row in sp.SHAPE_ORDERS_W}
    assert len(sigs) == n


def test_transitions_are_not_locked_to_the_shape_rotation():
    for week in range(1, 20):
        shape_row = [sp.segment_shape(week, p) for p in range(6)]
        move_row = [sp.segment_transition(week, j) for j in range(1, 7)]
        assert shape_row != move_row  # different banks anyway, but offsets must differ too
    assert sp.TRANSITION_ROW_OFFSET_W % len(sp.SHAPE_ORDERS_W) != 0


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
