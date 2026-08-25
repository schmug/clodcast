"""
Multi-voice scenes: the `lines` layer inside a segment (#172).

A segment may carry `lines: [{speaker, text}, ...]` instead of `text`. Each line
renders in its own cast voice and the takes join into the same seg_NN.mp3 the rest
of the pipeline already expects — so one scene stays one chapter with one
source_url, and nothing below seg_NN.mp3 changes.

The tests here are organised around the four things that can go wrong:
  1. the speech-rate gate silently switching itself off (the trap);
  2. the scene leaking out of its one chapter;
  3. the turn gap being confused with the chapter gap;
  4. the cache re-rendering a whole scene for a one-line rewrite.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "daily-podcast"))

import render  # noqa: E402

# A four-person cast on the four bundled presets, the Phase 1 shape from the
# Surface Tension spec (§4.8): roles map to voices, so Phase 3 can swap recorded
# clips in without the scene texts moving.
CAST = {"anchor": "Ryan", "advocate": "Aiden", "skeptic": "Ethan", "switchboard": "Chelsie"}


class _FakeAudioResult:
    """Mimics one mlx-audio generate() result: a `.audio` array-like."""

    def __init__(self, n: int = 4):
        self.audio = [0.0] * n


@pytest.fixture
def fake_tts(monkeypatch):
    """Install fake numpy / soundfile / mlx_audio modules and stub ffmpeg so
    render_segments runs without MLX, Metal, or a real encoder.

    Same seam as tests/test_render.py's fixture, with one addition: every generate()
    call records the VOICE as well as the text, because a multi-voice scene's whole
    point is which voice said which line. `commands` keeps the ffmpeg argv so the
    per-line join can be asserted without a real encoder.
    """
    calls: list[dict] = []
    model_loads: list[str] = []
    commands: list[list[str]] = []

    class FakeModel:
        def generate(self, text, **kw):
            calls.append({"text": text, "voice": kw.get("voice"), "ref_audio": kw.get("ref_audio")})
            return [_FakeAudioResult()]

        def generate_voice_design(self, text, **kw):
            calls.append({"text": text, "voice": None, "instruct": kw.get("instruct")})
            return [_FakeAudioResult()]

    fake_np = types.ModuleType("numpy")
    fake_np.concatenate = lambda arrs: [x for a in arrs for x in a]
    fake_np.array = lambda x: list(x)
    monkeypatch.setitem(sys.modules, "numpy", fake_np)

    fake_sf = types.ModuleType("soundfile")
    fake_sf.write = lambda path, audio, sr: Path(path).write_bytes(b"\x00")
    monkeypatch.setitem(sys.modules, "soundfile", fake_sf)

    mlx_audio = types.ModuleType("mlx_audio")
    mlx_tts = types.ModuleType("mlx_audio.tts")
    mlx_utils = types.ModuleType("mlx_audio.tts.utils")

    def _load_model(model_id):
        model_loads.append(model_id)
        return FakeModel()

    mlx_utils.load_model = _load_model
    monkeypatch.setitem(sys.modules, "mlx_audio", mlx_audio)
    monkeypatch.setitem(sys.modules, "mlx_audio.tts", mlx_tts)
    monkeypatch.setitem(sys.modules, "mlx_audio.tts.utils", mlx_utils)

    def fake_run(cmd, **kw):
        commands.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"\x00")  # ffmpeg writes its output last
        return None

    monkeypatch.setattr(render, "run", fake_run)

    return types.SimpleNamespace(calls=calls, model_loads=model_loads, commands=commands)


def _scene(*turns, url: str | None = "https://example.com/post") -> dict:
    """One scene segment: (speaker, text) pairs, no author-written `text`."""
    return {
        "lines": [{"speaker": s, "text": t} for s, t in turns],
        "source_url": url,
        "source_title": "A post",
    }


def _manifest(segments: list[dict], **extra) -> dict:
    return {
        "title": "Surface Tension - test episode",
        "summary": "A hook.",
        "cast": dict(CAST),
        "segments": segments,
        **extra,
    }


def _long_scene(n: int, chars: int = 400) -> dict:
    """A scene whose derived text is long enough to measure a speech rate from."""
    half = chars // 2
    return _scene(
        ("anchor", "a" * half),
        ("skeptic", "b" * half),
        url=f"https://example.com/{n}",
    )


# --- the trap: a lines-only episode must keep feeding the speech-rate gate ---


def test_a_scene_yields_a_speech_rate_row(tmp_path, monkeypatch):
    """THE test in #172.

    speech_rate_rows measures len(seg["text"]) and treats zero chars as
    *unmeasurable*. A lines-only segment measures zero, every scene is skipped, the
    population falls under MIN_RATE_SAMPLE_SEGMENTS and the function returns [] —
    which its own docstring defines as "no evidence of a defect". The gate and the
    bloopers bin would both switch themselves off for the whole show and say nothing.

    Deriving `text` from the lines is what keeps the population alive. This runs the
    two calls _render makes, in _render's order.
    """
    manifest = _manifest([_long_scene(i) for i in range(render.MIN_RATE_SAMPLE_SEGMENTS)])
    render.validate_manifest(manifest)
    render.materialize_line_text(manifest)

    segments = manifest["segments"]
    seg_paths = [tmp_path / f"seg_{i + 1:02d}.mp3" for i in range(len(segments))]
    for p in seg_paths:
        p.write_bytes(b"\x00")
    monkeypatch.setattr(render, "mp3_duration_ms", lambda p: 20_000)

    rows = render.speech_rate_rows(segments, seg_paths)

    assert len(rows) == len(segments)  # every scene measured, none skipped
    assert all(row["chars"] > 0 for row in rows)
    assert all(row["rate"] > 0 for row in rows)


def test_without_the_derived_text_the_gate_measures_nothing(tmp_path, monkeypatch):
    """The control for the test above: this is precisely the silent failure.

    Kept as a test rather than a comment because it is the only thing that proves the
    test above is not vacuous — strip the mitigation and the population is empty, and
    an empty list is indistinguishable from a clean episode.
    """
    manifest = _manifest([_long_scene(i) for i in range(render.MIN_RATE_SAMPLE_SEGMENTS)])
    render.validate_manifest(manifest)
    # deliberately NOT materialized

    segments = manifest["segments"]
    seg_paths = [tmp_path / f"seg_{i + 1:02d}.mp3" for i in range(len(segments))]
    for p in seg_paths:
        p.write_bytes(b"\x00")
    monkeypatch.setattr(render, "mp3_duration_ms", lambda p: 20_000)

    assert render.speech_rate_rows(segments, seg_paths) == []


def test_the_derived_text_is_the_line_texts_in_order():
    manifest = _manifest([_scene(("anchor", "First turn."), ("skeptic", "Second turn."))])
    render.materialize_line_text(manifest)
    assert manifest["segments"][0]["text"] == "First turn. Second turn."


def test_materializing_is_idempotent_and_leaves_plain_segments_alone():
    manifest = _manifest(
        [
            {"text": "A single-voice segment.", "source_url": None},
            _scene(("anchor", "One."), ("advocate", "Two.")),
        ]
    )
    render.materialize_line_text(manifest)
    render.materialize_line_text(manifest)
    assert manifest["segments"][0]["text"] == "A single-voice segment."
    assert manifest["segments"][1]["text"] == "One. Two."


def test_the_renderer_derives_the_text_before_the_gate_sees_it(tmp_path, monkeypatch):
    """Wiring, not just the helper: drive main() --dry-run over a lines manifest and
    assert the segments the bloopers bin was handed still measure a rate.

    capture_rate_bloopers is the first consumer of the measurement and runs before
    verify_artifact, so what it sees is what the gate sees.
    """
    n = render.MIN_RATE_SAMPLE_SEGMENTS
    manifest = _manifest([_long_scene(i) for i in range(n)])
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    seg_paths = [tmp_path / f"seg_{i + 1:02d}.mp3" for i in range(n)]
    for p in seg_paths:
        p.write_bytes(b"\x00")

    seen: dict[str, list] = {}
    real_capture = render.capture_rate_bloopers

    def spy(segments, paths, **kw):
        seen["rows"] = render.speech_rate_rows(segments, paths)
        return real_capture(segments, paths, **kw)

    monkeypatch.setattr(render, "capture_rate_bloopers", spy)
    monkeypatch.setattr(render, "load_config", lambda: {"show_id": "spotify:show:1"})
    monkeypatch.setattr(render, "render_segments", lambda *a, **k: seg_paths)
    monkeypatch.setattr(render, "plan_silences", lambda paths: [0] * len(paths))
    monkeypatch.setattr(
        render, "concat_and_normalize", lambda *a, **k: (tmp_path / "episode.mp3", None)
    )
    monkeypatch.setattr(render, "build_cover", lambda *a, **k: None)
    monkeypatch.setattr(
        render,
        "build_timeline_and_description",
        lambda *a, **k: ({"items": [{"chapter": {"title": "A", "start_time_ms": 0}}]}, "<p>d</p>"),
    )
    monkeypatch.setattr(render, "mp3_duration_ms", lambda p: 20_000)
    monkeypatch.setattr(render, "preflight", lambda *a, **k: (True, []))
    monkeypatch.setattr(render, "verify_artifact", lambda *a, **k: [])
    monkeypatch.setattr(render, "probe_audio_profile", lambda p: {})
    monkeypatch.setattr(
        sys,
        "argv",
        ["render.py", "--manifest", str(manifest_path), "--workdir", str(tmp_path), "--dry-run"],
    )

    assert render.main() == 0
    assert len(seen["rows"]) == n  # the gate is still armed on a four-voice show


def test_the_failed_run_sweep_recovers_a_scene_script(tmp_path, monkeypatch):
    """A swept clip without its script is a sound with no story — and a scene's script
    lives in its `lines`, not in a `text` the author never wrote."""
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "seg_01.mp3").write_bytes(b"ID3scene")
    (wd / "manifest.json").write_text(
        json.dumps(_manifest([_scene(("anchor", "Line one."), ("skeptic", "Line two."))]))
    )
    monkeypatch.setattr(render, "BLOOPER_DIR", tmp_path / "bin")
    monkeypatch.setattr(render, "mp3_duration_ms", lambda p: 4200)

    banked = render.capture_workdir_segments(wd)

    assert len(banked) == 1
    assert banked[0]["text"] == "Line one. Line two."
    assert banked[0]["chars"] == len("Line one. Line two.")


# --- one scene stays one chapter -------------------------------------------


def test_a_scene_renders_exactly_one_segment_mp3(tmp_path, fake_tts):
    segments = [_scene(("anchor", "Opening turn."), ("skeptic", "Rebuttal."))]
    paths = render.render_segments(segments, "Ryan", tmp_path, cast=CAST)

    assert [p.name for p in paths] == ["seg_01.mp3"]
    assert paths[0].exists()
    assert sorted(p.name for p in tmp_path.glob("seg_*.mp3")) == ["seg_01.mp3"]


def test_a_scene_keeps_its_single_chapter_and_source_url(tmp_path, monkeypatch, fake_tts):
    """The 1:1 segment <-> chapter <-> source_url mapping is what the lines layer
    exists to preserve; one utterance per segment would have broken all three."""
    manifest = _manifest(
        [
            _scene(("anchor", "Turn one."), ("advocate", "Turn two."), ("skeptic", "Turn three.")),
            {"text": "Sign-off.", "source_url": None},
        ]
    )
    render.validate_manifest(manifest)
    render.materialize_line_text(manifest)
    segments = manifest["segments"]
    seg_paths = render.render_segments(segments, "Ryan", tmp_path, cast=CAST)
    monkeypatch.setattr(
        render, "mp3_duration_ms", lambda p: 300_000 if p.name == "episode.mp3" else 30_000
    )

    timeline, _description = render.build_timeline_and_description(
        segments, seg_paths, [800, 0], "hook", tmp_path / "episode.mp3"
    )

    chapters = [it for it in timeline["items"] if "chapter" in it]
    links = [it for it in timeline["items"] if "link" in it]
    assert len(chapters) == 2  # one per SCENE, not one per turn
    assert len(links) == 1
    assert links[0]["link"]["url"] == "https://example.com/post"


# --- the cast: four voices, one model load ---------------------------------


def test_each_line_is_rendered_in_its_cast_voice(tmp_path, fake_tts):
    segments = [
        _scene(
            ("anchor", "Anchor speaks."),
            ("advocate", "Advocate speaks."),
            ("skeptic", "Skeptic speaks."),
            ("switchboard", "Switchboard speaks."),
        )
    ]
    render.render_segments(segments, "Ryan", tmp_path, cast=CAST)

    assert [(c["text"], c["voice"]) for c in fake_tts.calls] == [
        ("Anchor speaks.", "Ryan"),
        ("Advocate speaks.", "Aiden"),
        ("Skeptic speaks.", "Ethan"),
        ("Switchboard speaks.", "Chelsie"),
    ]


def test_a_four_voice_episode_loads_the_model_once(tmp_path, fake_tts):
    """Acceptance criterion, asserted mechanically rather than by wall-clock: the
    whole cast runs on the same base MODEL_ID, so extra voices cost no extra load."""
    segments = [
        _scene(("anchor", "One."), ("advocate", "Two.")),
        _scene(("skeptic", "Three."), ("switchboard", "Four.")),
    ]
    render.render_segments(segments, "Ryan", tmp_path, cast=CAST)

    assert fake_tts.model_loads == [render.MODEL_ID]
    assert len(fake_tts.calls) == 4


def test_a_cast_and_voice_design_cannot_share_an_episode(capsys):
    """VoiceDesign is a SECOND model (~doubling the ~15s load) and it drifts, while a
    cast is presets on the base model. An episode cannot be rendered from both, so the
    combination dies naming both fields instead of rendering a cast off the wrong
    model — which would sound wrong and be invisible in the run log."""
    manifest = _manifest([_scene(("anchor", "One."))], voice_instruct="a warm voice")
    with pytest.raises(SystemExit):
        render.validate_manifest(manifest)
    err = capsys.readouterr().err
    assert "voice_instruct" in err and "lines" in err


def test_render_segments_refuses_a_cast_on_the_design_model(tmp_path, fake_tts):
    """The same refusal one level down, so a direct caller cannot bypass it."""
    with pytest.raises(SystemExit):
        render.render_segments(
            [_scene(("anchor", "One."))],
            "custom",
            tmp_path,
            voice_instruct="a warm voice",
            cast=CAST,
        )
    assert fake_tts.model_loads == []  # dies before the load, like every other check


def test_a_clone_voice_episode_can_still_carry_scenes(tmp_path, fake_tts):
    """Clone mode shares the base MODEL_ID, so a house-voice narrator and a preset
    cast coexist on ONE load — the mixture the four-mode precedence has to survive."""
    ref = tmp_path / "house.wav"
    ref.write_bytes(b"RIFF-house")
    segments = [
        {"text": "Narration in the house voice.", "source_url": None},
        _scene(("anchor", "One."), ("advocate", "Two.")),
    ]
    render.render_segments(
        segments, "house", tmp_path, ref_audio=str(ref), ref_text="ref", cast=CAST
    )

    assert fake_tts.model_loads == [render.MODEL_ID]
    assert [(c["text"], c["voice"]) for c in fake_tts.calls] == [
        ("Narration in the house voice.", None),  # clone mode passes ref_audio, not voice
        ("One.", "Ryan"),
        ("Two.", "Aiden"),
    ]
    assert fake_tts.calls[0]["ref_audio"] == str(ref)


# --- per-line cache --------------------------------------------------------


def test_rewriting_one_line_re_renders_only_that_line(tmp_path, fake_tts):
    """Per-line caching is strictly better than per-segment: a one-word fix in turn
    three costs one take, not the whole scene."""
    segments = [_scene(("anchor", "One."), ("advocate", "Two."), ("skeptic", "Three."))]
    render.render_segments(segments, "Ryan", tmp_path, cast=CAST)

    fake_tts.calls.clear()
    fake_tts.model_loads.clear()
    revised = [_scene(("anchor", "One."), ("advocate", "Two, revised."), ("skeptic", "Three."))]
    render.render_segments(revised, "Ryan", tmp_path, cast=CAST)

    assert [c["text"] for c in fake_tts.calls] == ["Two, revised."]
    assert len(fake_tts.model_loads) == 1  # a miss still loads the model exactly once


def test_a_fully_cached_scene_skips_the_model_load(tmp_path, fake_tts):
    segments = [_scene(("anchor", "One."), ("advocate", "Two."))]
    render.render_segments(segments, "Ryan", tmp_path, cast=CAST)
    assert len(fake_tts.model_loads) == 1

    fake_tts.calls.clear()
    fake_tts.model_loads.clear()
    paths = render.render_segments(segments, "Ryan", tmp_path, cast=CAST)

    assert [p.name for p in paths] == ["seg_01.mp3"]
    assert fake_tts.calls == []
    assert fake_tts.model_loads == []


def test_recasting_a_speaker_re_renders_only_that_speakers_lines(tmp_path, fake_tts):
    segments = [_scene(("anchor", "One."), ("advocate", "Two."))]
    render.render_segments(segments, "Ryan", tmp_path, cast=CAST)

    fake_tts.calls.clear()
    recast = {**CAST, "advocate": "Chelsie"}
    render.render_segments(segments, "Ryan", tmp_path, cast=recast)

    assert [(c["text"], c["voice"]) for c in fake_tts.calls] == [("Two.", "Chelsie")]


def test_a_missing_scene_mp3_with_cached_takes_only_re_joins(tmp_path, fake_tts):
    """The line takes are the expensive artifact. If only the joined scene is gone,
    re-joining is an ffmpeg call, not a model load."""
    segments = [_scene(("anchor", "One."), ("advocate", "Two."))]
    render.render_segments(segments, "Ryan", tmp_path, cast=CAST)

    (tmp_path / "seg_01.mp3").unlink()
    (tmp_path / "seg_01.json").unlink()
    fake_tts.calls.clear()
    fake_tts.model_loads.clear()
    paths = render.render_segments(segments, "Ryan", tmp_path, cast=CAST)

    assert fake_tts.calls == []
    assert fake_tts.model_loads == []
    assert paths[0].exists()


def test_line_takes_are_cached_beside_their_scene(tmp_path, fake_tts):
    segments = [_scene(("anchor", "One."), ("advocate", "Two."))]
    render.render_segments(segments, "Ryan", tmp_path, cast=CAST)

    assert sorted(p.name for p in tmp_path.glob("line_*.mp3")) == [
        "line_01_01.mp3",
        "line_01_02.mp3",
    ]
    for name in ("line_01_01.json", "line_01_02.json"):
        meta = json.loads((tmp_path / name).read_text())
        assert len(meta["key"]) == 64
    assert len(json.loads((tmp_path / "seg_01.json").read_text())["key"]) == 64


# --- the turn gap is not the chapter gap -----------------------------------


def test_the_turn_gap_is_dialogue_sized_and_distinct_from_the_chapter_constants():
    """DEFAULT_SILENCE_MS = 800 is the beat between CHAPTERS; between two speakers
    mid-scene it sounds like a hostage negotiation. MIN_CHAPTER_GAP_MS is not a
    silence at all — it governs chapter start-time spacing."""
    assert 150 <= render.TURN_GAP_MS <= 350
    assert render.TURN_GAP_MS != render.DEFAULT_SILENCE_MS
    assert render.TURN_GAP_MS != render.MIN_CHAPTER_GAP_MS


def test_line_takes_join_at_the_turn_gap_in_mono_44_1k(tmp_path, fake_tts):
    """The join is one more place the mono-44.1k invariant can be broken, and the
    silence between takes must be the turn gap, not the chapter silence."""
    segments = [_scene(("anchor", "One."), ("advocate", "Two."), ("skeptic", "Three."))]
    render.render_segments(segments, "Ryan", tmp_path, cast=CAST)

    joins = [c for c in fake_tts.commands if "concat" in c and c[-1].endswith("seg_01.mp3")]
    assert len(joins) == 1
    cmd = joins[0]
    assert cmd[cmd.index("-ar") + 1] == str(render.AUDIO_SAMPLE_RATE)
    assert cmd[cmd.index("-ac") + 1] == str(render.AUDIO_CHANNELS)

    concat_list = Path(cmd[cmd.index("-i") + 1]).read_text()
    entries = [line.split("'")[1] for line in concat_list.splitlines() if line.strip()]
    gap = str(tmp_path / f"silence_{render.TURN_GAP_MS}ms.mp3")
    assert entries == [
        str(tmp_path / "line_01_01.mp3"),
        gap,
        str(tmp_path / "line_01_02.mp3"),
        gap,
        str(tmp_path / "line_01_03.mp3"),
    ]


def test_plan_silences_is_unchanged_by_a_lines_episode(tmp_path, monkeypatch):
    """Nothing below seg_NN.mp3 may notice that a segment was a scene: a lines
    episode and the equivalent single-voice one get identical chapter silences."""
    paths = [tmp_path / f"seg_{i:02d}.mp3" for i in (1, 2, 3)]
    monkeypatch.setattr(render, "mp3_duration_ms", lambda p: 30_000)
    assert render.plan_silences(paths) == [
        render.DEFAULT_SILENCE_MS,
        render.DEFAULT_SILENCE_MS,
        render.LAST_SILENCE_MS,
    ]


# --- validation ------------------------------------------------------------


def test_a_segment_with_both_text_and_lines_dies_naming_the_field():
    manifest = _manifest([_scene(("anchor", "One."))])
    manifest["segments"][0]["text"] = "An author-written script."

    with pytest.raises(SystemExit):
        render.validate_manifest(manifest)


def test_the_both_text_and_lines_message_names_both_fields(capsys):
    manifest = _manifest([_scene(("anchor", "One."))])
    manifest["segments"][0]["text"] = "An author-written script."

    with pytest.raises(SystemExit):
        render.validate_manifest(manifest)
    err = capsys.readouterr().err
    assert "'text'" in err and "'lines'" in err and "segment[0]" in err


def test_a_lines_only_segment_validates_without_a_text():
    """The whole point of the schema: a scene carries no author-written `text`, and
    validate_manifest must accept the manifest as hand-authored, not only after it
    has been materialized."""
    render.validate_manifest(_manifest([_scene(("anchor", "One."), ("skeptic", "Two."))]))


def test_a_speaker_outside_the_cast_dies(capsys):
    manifest = _manifest([_scene(("narrator", "Who am I?"))])
    with pytest.raises(SystemExit):
        render.validate_manifest(manifest)
    assert "narrator" in capsys.readouterr().err


def test_a_scene_without_a_cast_dies(capsys):
    manifest = _manifest([_scene(("anchor", "One."))])
    del manifest["cast"]
    with pytest.raises(SystemExit):
        render.validate_manifest(manifest)
    assert "cast" in capsys.readouterr().err


def test_the_cast_must_be_bundled_presets(capsys):
    """Phase 1 ships on the four presets. `house` is the daily show's narrator and
    recorded cast clips are Phase 3 — both must die naming what is allowed rather
    than silently rendering the wrong voice."""
    manifest = _manifest([_scene(("anchor", "One."))], cast={"anchor": "house"})
    with pytest.raises(SystemExit):
        render.validate_manifest(manifest)
    assert "house" in capsys.readouterr().err


@pytest.mark.parametrize(
    "lines",
    [
        [],
        "not a list",
        [{"speaker": "anchor"}],
        [{"speaker": "anchor", "text": "   "}],
        [{"text": "no speaker"}],
        ["not an object"],
    ],
)
def test_a_malformed_lines_array_dies(lines):
    manifest = _manifest([{"lines": lines, "source_url": None}])
    with pytest.raises(SystemExit):
        render.validate_manifest(manifest)


def test_a_plain_manifest_is_unaffected_by_the_lines_schema():
    """Regression fence: the single-voice manifest that ships every day still
    validates with no cast and no lines."""
    render.validate_manifest(
        {
            "title": "A daily episode",
            "summary": "hook",
            "segments": [{"text": "A story.", "source_url": "https://example.com/a"}],
        }
    )


def test_a_plain_segment_missing_text_still_dies_with_the_same_message(capsys):
    manifest = {"title": "t", "summary": "s", "segments": [{"source_url": None}]}
    with pytest.raises(SystemExit):
        render.validate_manifest(manifest)
    assert "missing required field 'text'" in capsys.readouterr().err


# --- the shipped example ---------------------------------------------------


def test_the_example_lines_manifest_validates_and_materializes():
    """tests/data/lines_manifest.json is the hand-written manifest #172 is exercised
    by (and the one a dry run on an Apple Silicon box should use). Pinning it here
    means the documented example cannot drift from the schema."""
    path = Path(__file__).resolve().parent / "data" / "lines_manifest.json"
    manifest = json.loads(path.read_text())

    render.validate_manifest(manifest)
    render.materialize_line_text(manifest)

    scenes = [s for s in manifest["segments"] if s.get("lines")]
    assert len(scenes) == 2
    assert set(manifest["cast"].values()) == set(render.VOICES)  # all four presets
    for scene in scenes:
        assert scene["text"] == " ".join(ln["text"].strip() for ln in scene["lines"])
