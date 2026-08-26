"""The Surface Tension write layer's deterministic half (#176).

`prompts/write_scene.md` is prose and cannot be tested; the seams around it can.
This file covers the two the daily show already proved worth pinning — the
placeholder contract between a template and its filler, and the outcome mapping
that decides whether a subprocess's output becomes a chapter — plus the manifest
assembly the content rules in test_switchboard.py are asserted against.
"""

import json
import pathlib
import re

import orchestrate
import st_script_plan
import st_write


def _out(obj) -> str:
    return json.dumps(obj)


def _lines(n: int, chars: int) -> list[dict]:
    return [{"speaker": "Ryan", "text": "x" * chars} for _ in range(n)]


def test_a_valid_scene_returns_its_lines():
    got = st_write.classify_scene(_out({"ok": True, "lines": _lines(4, 200)}), "", 0)
    assert got["outcome"] == "OK"
    assert [ln["speaker"] for ln in got["lines"]] == ["Ryan"] * 4


def test_a_scene_under_the_floor_is_refused():
    lines = _lines(2, 50)
    got = st_write.classify_scene(_out({"ok": True, "lines": lines}), "", 0)
    assert got["outcome"] == "REFUSED"
    assert got["lines"] is None
    assert str(len(st_write.scene_text(lines))) in got["detail"]


def test_the_floor_measures_the_summed_line_texts_not_one_line():
    # Eight 90-char turns is a real scene; no single line clears the 500 floor.
    lines = _lines(8, 90)
    assert all(len(ln["text"]) < orchestrate.MIN_SEGMENT_CHARS for ln in lines)
    got = st_write.classify_scene(_out({"ok": True, "lines": lines}), "", 0)
    assert got["outcome"] == "OK", "the floor must measure the scene, not one turn"


def test_an_explicit_refusal_carries_its_reason():
    got = st_write.classify_scene(_out({"ok": False, "reason": "paywalled"}), "", 0)
    assert got["outcome"] == "REFUSED"
    assert got["detail"] == "paywalled"


def test_an_auth_failure_is_its_own_outcome():
    got = st_write.classify_scene("", "API error: 401 unauthorized", 1)
    assert got["outcome"] == "AUTH"


def test_a_policy_block_is_its_own_outcome():
    got = st_write.classify_scene("I am unable to respond to that", "", 0)
    assert got["outcome"] == "BLOCKED"


def test_anything_else_is_an_error():
    got = st_write.classify_scene("not json at all", "", 1)
    assert got["outcome"] == "ERROR"


def test_a_line_missing_its_speaker_is_refused_not_shipped():
    # render.validate_manifest would die on this, taking the whole run with it.
    # One malformed scene must drop one scene.
    bad = [{"text": "x" * 600}]
    got = st_write.classify_scene(_out({"ok": True, "lines": bad}), "", 0)
    assert got["outcome"] == "REFUSED"


def test_a_line_naming_an_unknown_speaker_is_refused():
    bad = [{"speaker": "Gandalf", "text": "x" * 600}]
    got = st_write.classify_scene(_out({"ok": True, "lines": bad}), "", 0)
    assert got["outcome"] == "REFUSED"
    assert "Gandalf" in got["detail"]


# --- the prompt and its filler (the <<PLACEHOLDER>> contract) ---------------

ST_DIR = pathlib.Path(__file__).resolve().parent.parent / "skills" / "surface-tension"


def _template() -> str:
    return (ST_DIR / "prompts" / "write_scene.md").read_text()


def _post(**over) -> dict:
    post = {
        "title": "Stop Trying to Distinguish AI-Generated Writing",
        "url": "https://example.com/stop-trying",
        "summary": "A blogger argues the tell-tale signs are folklore.",
        "votes": 12,
        "comment_count": 2,
        "domain": "example.com",
    }
    post.update(over)
    return post


def _scene(pos: int = 0) -> dict:
    return st_script_plan.build_scene(week=3, pos=pos)


def test_the_template_and_the_filler_declare_the_same_placeholders():
    found = set(re.findall(r"<<[A-Z_]+>>", _template()))
    assert found == set(st_write.PLACEHOLDERS), (
        "prompts/write_scene.md and st_write.PLACEHOLDERS have drifted"
    )


def test_filling_the_prompt_leaves_no_placeholder_behind():
    filled = st_write.fill_scene_prompt(_template(), _post(), _scene())
    assert "<<" not in filled


def test_the_length_band_reaches_the_writer():
    filled = st_write.fill_scene_prompt(_template(), _post(), _scene(), band=(940, 1460))
    assert "940" in filled and "1460" in filled


def test_the_rundown_names_the_assigned_voice_for_every_speaking_role():
    scene = _scene()
    filled = st_write.fill_scene_prompt(_template(), _post(), scene)
    for role in scene["turn_order"]:
        assert role["voice"] in filled
        assert role["role"] in filled
    assert scene["sits_out"] not in [r["role"] for r in scene["turn_order"]]


def test_a_post_with_no_comments_gets_no_switchboard_turn():
    # The plan may assign the slot; the data vetoes it. Pick the scene position
    # where the switchboard is actually cast, so the test proves the veto rather
    # than an accident of the rotation.
    scene = next(
        s
        for s in (_scene(p) for p in range(5))
        if "switchboard" in [r["role"] for r in s["turn_order"]]
    )
    filled = st_write.fill_scene_prompt(_template(), _post(comment_count=0), scene)
    rundown = filled.split("RUNDOWN")[1].split("STANCES")[0]
    assert "switchboard" not in rundown, "a post with no calls must get no switchboard turn"


def test_a_post_with_comments_keeps_its_switchboard_turn():
    scene = next(
        s
        for s in (_scene(p) for p in range(5))
        if "switchboard" in [r["role"] for r in s["turn_order"]]
    )
    filled = st_write.fill_scene_prompt(_template(), _post(comment_count=3), scene)
    rundown = filled.split("RUNDOWN")[1].split("STANCES")[0]
    assert "switchboard" in rundown


def test_the_prompt_gives_the_writer_a_refusal_path_for_an_unarguable_post():
    # The pool is vote-ranked over PERSONAL blogs, so it surfaces grief, illness
    # and crisis posts on their merits — "My Mom Has Cancer" ranked 6th in the
    # first live gather. Every main post gets an assigned for/against stance, so
    # without an explicit refusal path the format will eventually assign someone
    # to argue AGAINST a stranger's cancer diagnosis.
    text = _template()
    assert "not every post is arguable" in text.lower()
    assert '{"ok": false' in text, "the refusal contract is the writer's only way out"


def _scene_where_a_stance_lands_on_the_desk():
    """A scene whose for- or against-voice is the one working the board.

    The two axes are independent by design (coprime squares), so this collides
    regularly — and when it does, one voice is handed both "argue this side" and
    "report the board, never an opinion".
    """
    for week in range(20):
        for pos in range(5):
            scene = st_script_plan.build_scene(week, pos)
            desk = scene["roles"].get("switchboard")
            if desk and desk in (scene["stance"]["for"], scene["stance"]["against"]):
                return scene
    raise AssertionError("the two squares never collide, which cannot be right")


def test_the_desk_is_never_told_to_argue_a_side():
    scene = _scene_where_a_stance_lands_on_the_desk()
    desk = scene["roles"]["switchboard"]
    filled = st_write.fill_scene_prompt(_template(), _post(comment_count=2), scene)
    stances = filled.split("STANCES")[1].split("THE BOARD")[0]
    assert f"{desk} argues" not in stances, (
        f"{desk} works the board this scene and must not also be told to argue"
    )


def test_a_side_the_desk_would_have_carried_is_named_as_unassigned():
    # Silently dropping it would read as "no side to argue"; the panel should
    # know the post goes unopposed rather than quietly agreeing with it.
    scene = _scene_where_a_stance_lands_on_the_desk()
    filled = st_write.fill_scene_prompt(_template(), _post(comment_count=2), scene)
    stances = filled.split("STANCES")[1].split("THE BOARD")[0]
    assert "unassigned" in stances.lower()


def test_a_scene_with_no_stance_collision_still_names_both_sides():
    scene = next(
        st_script_plan.build_scene(3, p)
        for p in range(5)
        if st_script_plan.build_scene(3, p)["roles"].get("switchboard")
        not in (
            st_script_plan.build_scene(3, p)["stance"]["for"],
            st_script_plan.build_scene(3, p)["stance"]["against"],
        )
    )
    filled = st_write.fill_scene_prompt(_template(), _post(comment_count=1), scene)
    stances = filled.split("STANCES")[1].split("THE BOARD")[0]
    assert f"{scene['stance']['for']} argues FOR" in stances
    assert f"{scene['stance']['against']} argues AGAINST" in stances
