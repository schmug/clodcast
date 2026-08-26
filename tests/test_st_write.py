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


# --- the recorded cast and this show's art (#177) ---------------------------
#
# Phase 1-2 shipped on the four bundled presets so the FORMAT could be judged
# before any assets existed. Phase 3 swaps the values for recorded ref_audio
# clips; what stays put is the persona names, because the roles rotate per scene
# against them.

import render  # noqa: E402  (conftest puts skills/daily-podcast on sys.path)

REFS = pathlib.Path(st_write.__file__).resolve().parent / "refs"


def test_the_cast_keys_stay_the_personas():
    assert list(st_write.cast_map()) == list(st_script_plan.VOICES_ST)


def test_every_persona_maps_to_a_bundled_recorded_clip():
    for persona, entry in st_write.cast_map().items():
        assert set(entry) == set(render.CAST_CLIP_FIELDS), persona
        clip = pathlib.Path(entry["ref_audio"])
        assert clip.is_file(), f"{persona}: {clip} is missing"
        assert clip.parent == REFS, f"{persona}: the clip must ship with the skill"


def test_the_cast_clip_paths_are_absolute():
    # A scheduled run's CWD is arbitrary and CLAUDE_PLUGIN_ROOT is unset under the
    # cron, so a relative clip path resolves against nothing in particular. Same
    # reasoning the sibling show's cover_image carries.
    for persona, entry in st_write.cast_map().items():
        assert pathlib.Path(entry["ref_audio"]).is_absolute(), persona


def test_each_cast_transcript_is_the_clips_own_text():
    # ref_text is what the model believes the clip says. A transcript belonging to
    # a different clip is not an error anywhere — it just makes that voice drift.
    for persona, entry in st_write.cast_map().items():
        sidecar = pathlib.Path(entry["ref_audio"]).with_suffix(".txt")
        assert sidecar.is_file(), f"{persona}: {sidecar} is missing"
        assert entry["ref_text"] == sidecar.read_text().strip(), persona


def test_no_two_personas_share_a_clip():
    clips = [e["ref_audio"] for e in st_write.cast_map().values()]
    assert len(set(clips)) == len(clips), "two personas on one clip is one voice, twice"


def test_the_house_voice_is_not_in_the_cast():
    # It is the daily show's narrator. Making it a panelist here spends the more
    # valuable identity on the newer show.
    house = str(render.BUNDLED_HOUSE_AUDIO)
    assert house not in [e["ref_audio"] for e in st_write.cast_map().values()]


def test_the_bundled_cast_clips_are_usable_reference_audio():
    # Bundled, not fetched: a render must not depend on the network for a local
    # artifact (the house-voice clip's posture). Mono 24k PCM is what the model
    # took to produce them and what house_voice.wav is; ~20-30s is the window
    # docs/durable-voices.md calls the sweet spot for capturing prosody.
    import wave

    for persona, entry in st_write.cast_map().items():
        with wave.open(entry["ref_audio"]) as w:
            assert w.getnchannels() == 1, persona
            assert w.getframerate() == 24000, persona
            assert w.getsampwidth() == 2, persona
            seconds = w.getnframes() / w.getframerate()
        assert 15 <= seconds <= 40, f"{persona}: {seconds:.1f}s is outside the window"


def test_the_manifest_carries_this_shows_own_art():
    m = st_write.assemble_manifest("2026-08-31", "T", "S", [_frame()])
    cover = pathlib.Path(m["cover_image"])
    assert cover.is_absolute()
    assert cover.is_file()
    assert render.check_cover_image(cover)["ok"], render.check_cover_image(cover)["detail"]


def test_the_bundled_show_art_is_a_valid_podcast_cover():
    # Apple Podcasts and Spotify both require square art, 1400-3000px, and a
    # directory rejects the whole FEED over bad art, not just the episode.
    from PIL import Image

    with Image.open(REFS / "cover.jpg") as im:
        assert im.format == "JPEG"
        assert im.width == im.height, f"show art must be square (got {im.size})"
        assert 1400 <= im.width <= 3000, f"show art must be 1400-3000px (got {im.width})"


def test_the_assembled_manifest_survives_the_renderers_validation():
    # The end-to-end pin: st_write builds it, render.py is what refuses it. A cast
    # shape the assembler likes and the renderer rejects fails at the top of a real
    # weekly run, after the gather and every scene has already been paid for.
    m = st_write.assemble_manifest("2026-08-31", "A title", "A hook.", [_frame()])
    render.validate_manifest(m)
    for persona, entry in m["cast"].items():
        spec = render.resolve_cast_voice(persona, entry)
        assert spec["mode"] == "clone", persona
        assert spec["ref_fingerprint"], persona


def _frame() -> dict:
    return {
        "kind": "frame",
        "title": "Cold open",
        "lines": [{"speaker": v, "text": "A turn."} for v in st_script_plan.VOICES_ST],
    }
