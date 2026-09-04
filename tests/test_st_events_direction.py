"""Surface Tension's half of #201: what the writer may ask of the engine.

render.py decides what an engine can do; this layer decides what the SHOW lets a
writer ask for, and proves it in code the way test_switchboard.py proves the
desk's rules. The direction vocabulary is a closed list of pace and affect words
(free text bends identity: "exasperated" moved Ethan from 93 Hz to 183 Hz in the
2026-09-04 eval), the budget is one directed line per scene until that drift has
been listened to, and a scene is gated through the engine it will render on
before it becomes a manifest: events stripped where the engine cannot perform
them, direction refused where it cannot render them.
"""

import pathlib

import pytest

import render
import st_script_plan
import st_write

ST_DIR = pathlib.Path(__file__).resolve().parent.parent / "skills" / "surface-tension"
V = st_script_plan.VOICES_ST


def _template() -> str:
    return (ST_DIR / "prompts" / "write_scene.md").read_text()


def _post(**over) -> dict:
    post = {
        "title": "A post",
        "url": "https://example.com/a-post",
        "summary": "A summary.",
        "comment_count": 0,
        "domain": "example.com",
    }
    post.update(over)
    return post


def _scene() -> dict:
    return st_script_plan.build_scene(week=3, pos=0)


def _lines(*turns) -> list[dict]:
    """(speaker, text[, instruct]) tuples; the filler clears MIN_SEGMENT_CHARS."""
    filler = "The post argues its case at length and the panel takes it seriously. " * 4
    out = [{"speaker": V[0], "text": filler}]
    for turn in turns:
        line = {"speaker": turn[0], "text": turn[1]}
        if len(turn) > 2:
            line["instruct"] = turn[2]
        out.append(line)
    out.append({"speaker": V[1], "text": filler})
    return out


def _ok(lines) -> str:
    import json

    return json.dumps({"ok": True, "lines": lines})


# --- the vocabulary and the budget are the show's, not the writer's -----------


def test_the_direction_vocabulary_is_a_closed_list_of_single_words():
    assert st_write.DIRECTIONS, "the vocabulary must not be empty"
    for word, instruct in st_write.DIRECTIONS.items():
        assert word.isalpha() and word.islower(), word
        assert isinstance(instruct, str) and instruct.strip(), word
    assert st_write.MAX_DIRECTED_LINES_PER_SCENE == 1


def test_a_directed_line_in_the_vocabulary_is_a_valid_scene():
    word = next(iter(st_write.DIRECTIONS))
    got = st_write.classify_scene(_ok(_lines((V[2], "Then say so.", word))), "", 0)
    assert got["outcome"] == "OK"
    assert got["lines"][1]["instruct"] == word


def test_an_instruct_outside_the_vocabulary_is_refused_naming_it():
    got = st_write.classify_scene(_ok(_lines((V[2], "Then say so.", "furious"))), "", 0)
    assert got["outcome"] == "REFUSED"
    assert "furious" in got["detail"]


@pytest.mark.parametrize("bad", ["", 3, ["weary"]])
def test_a_non_string_or_empty_instruct_is_refused(bad):
    got = st_write.classify_scene(_ok(_lines((V[2], "Then say so.", bad))), "", 0)
    assert got["outcome"] == "REFUSED"


def test_a_second_directed_line_in_one_scene_is_refused():
    w1, w2 = list(st_write.DIRECTIONS)[:2]
    lines = _lines((V[2], "One.", w1), (V[3], "Two.", w2))
    got = st_write.classify_scene(_ok(lines), "", 0)
    assert got["outcome"] == "REFUSED"
    assert str(st_write.MAX_DIRECTED_LINES_PER_SCENE) in got["detail"]


def test_a_marker_no_engine_performs_is_refused_naming_it():
    got = st_write.classify_scene(_ok(_lines((V[2], "Well (cough) then."))), "", 0)
    assert got["outcome"] == "REFUSED"
    assert "cough" in got["detail"]


def test_a_line_that_is_only_a_marker_is_refused():
    got = st_write.classify_scene(_ok(_lines((V[2], "(laugh)"))), "", 0)
    assert got["outcome"] == "REFUSED"


def test_the_scene_floor_measures_the_spoken_text_not_the_markers():
    # Enough markers to clear the floor on their own, around too little speech.
    padded = [{"speaker": V[0], "text": "(laugh) " * 80 + "Short."}]
    assert len(" ".join(ln["text"] for ln in padded)) > 500
    got = st_write.classify_scene(_ok(padded), "", 0)
    assert got["outcome"] == "REFUSED"
    assert "short" in got["detail"]


# --- the engine gate: strip what it cannot perform, refuse what it cannot render


def test_direction_is_refused_on_an_engine_without_it_naming_the_engine():
    word = next(iter(st_write.DIRECTIONS))
    lines = _lines((V[2], "Then say so.", word))
    got = st_write.engine_violations(lines, "qwen3")
    assert got and all("qwen3" in p for p in got)
    assert any("line 1" in p for p in got)


def test_direction_passes_on_an_engine_with_it():
    word = next(iter(st_write.DIRECTIONS))
    assert st_write.engine_violations(_lines((V[2], "Then say so.", word)), "breeze") == []


def test_events_are_stripped_for_an_engine_without_them():
    lines = _lines((V[2], "Okay. (laugh) Right (sigh), fine."))
    got = st_write.lines_for_engine(lines, "qwen3")
    assert got[1]["text"] == "Okay. Right, fine."
    assert lines[1]["text"].count("(") == 2, "the caller's lines are not mutated"


def test_events_survive_and_direction_expands_for_an_engine_with_them():
    word = next(iter(st_write.DIRECTIONS))
    lines = _lines((V[2], "Okay. (laugh) Right.", word))
    got = st_write.lines_for_engine(lines, "breeze")
    assert got[1]["text"] == "Okay. (laugh) Right."
    # The writer emits the WORD; the engine hears the instruct it maps to, so the
    # phrasing can be tuned after a listen without touching the writer contract.
    assert got[1]["instruct"] == st_write.DIRECTIONS[word]
    assert "instruct" not in got[0]


def _items(scene_lines, frame_lines):
    return [
        {"kind": "frame", "title": "Cold open", "lines": frame_lines},
        {"kind": "scene", "post": _post(), "plan": _scene(), "lines": scene_lines},
    ]


def test_assemble_manifest_gates_every_scene_and_frame_through_the_engine():
    scene = _lines((V[2], "One. (laugh)"))
    frame = [{"speaker": V[0], "text": "(sigh) Morning."}]
    m = st_write.assemble_manifest("2026-08-31", "T", "S.", _items(scene, frame), engine="qwen3")
    texts = [ln["text"] for seg in m["segments"] for ln in seg["lines"]]
    assert not any("(" in t for t in texts)
    assert "Morning." in texts and "One." in texts


def test_a_directed_frame_line_is_refused_on_an_engine_without_direction():
    word = next(iter(st_write.DIRECTIONS))
    frame = [{"speaker": V[0], "text": "Morning.", "instruct": word}]
    with pytest.raises(SystemExit):
        st_write.assemble_manifest("2026-08-31", "T", "S.", _items(_lines(), frame), engine="qwen3")


@pytest.mark.parametrize("engine", [None, "qwen3", "breeze"])
def test_the_assembled_manifest_validates_under_the_engine_it_was_gated_for(engine):
    word = next(iter(st_write.DIRECTIONS))
    scene = _lines((V[2], "One. (laugh)", word))
    frame = [{"speaker": V[0], "text": "(sigh) Morning."}]
    if engine != "breeze":
        # No direction on qwen3: gate refuses it, so hand it an undirected scene.
        scene = _lines((V[2], "One. (laugh)"))
    m = st_write.assemble_manifest("2026-08-31", "T", "S.", _items(scene, frame), engine=engine)
    render.validate_manifest(m)
    if engine is None:
        assert "tts_engine" not in m, "an unset engine keeps the key absent (SKILL.md)"
    else:
        assert m["tts_engine"] == engine
    spec = render.ENGINES[engine or render.TTS_ENGINE_QWEN3]
    assert m["voice"] == (V[0] if spec.has("preset") else "house")


def test_an_unknown_engine_dies_at_assembly():
    with pytest.raises(SystemExit):
        st_write.assemble_manifest(
            "2026-08-31", "T", "S.", _items(_lines(), _lines()), engine="Breeze"
        )


# --- the writer is told exactly what it may ask for ---------------------------


def test_the_prompt_offers_markers_and_direction_only_where_the_engine_has_them():
    breeze = st_write.fill_scene_prompt(_template(), _post(), _scene(), engine="breeze")
    for marker in render.EVENT_MARKERS:
        assert f"({marker})" in breeze
    for word in st_write.DIRECTIONS:
        assert word in breeze
    assert '"instruct"' in breeze
    assert str(st_write.MAX_DIRECTED_LINES_PER_SCENE) in breeze or "ONE" in breeze

    qwen3 = st_write.fill_scene_prompt(_template(), _post(), _scene(), engine="qwen3")
    assert "instruct" in qwen3 and "refused" in qwen3
    assert not any(word in qwen3 for word in st_write.DIRECTIONS)
    assert "(sigh)" not in qwen3


def test_the_prompt_defaults_to_the_renderers_default_engine():
    default = st_write.fill_scene_prompt(_template(), _post(), _scene())
    qwen3 = st_write.fill_scene_prompt(_template(), _post(), _scene(), engine="qwen3")
    assert default == qwen3


def test_the_performance_block_is_a_declared_placeholder():
    assert "<<PERFORMANCE>>" in st_write.PLACEHOLDERS
    assert "<<PERFORMANCE>>" in _template()
