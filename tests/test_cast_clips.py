"""
Recorded cast clips: a `lines` cast member rendered as an `ref_audio` clone (#177).

The `lines` layer shipped preset-only (#172): every cast value had to be one of the
four bundled presets, and `render_segments` hardcoded `"mode": "preset"` with a
cache key carrying no ref-audio fields. Surface Tension's Phase 3 needs recorded
clips, so a cast value may now also be `{"ref_audio", "ref_text"}`.

The tests here are organised around what a careless version of that change breaks:
  1. the manifest whitelist, which is the only thing standing between a typo and a
     scene rendered in the wrong voice;
  2. the per-line cache key, which without the clip's FINGERPRINT would serve one
     voice's banked audio under another voice's name — silently, with no error;
  3. the one-model-load property, which is what keeps `speaker` a role rather than
     a fifth voice mode.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "daily-podcast"))

import render  # noqa: E402


class _FakeAudioResult:
    def __init__(self, n: int = 4):
        self.audio = [0.0] * n


@pytest.fixture
def fake_tts(monkeypatch):
    """Same seam as tests/test_lines.py, plus `ref_text`: a clone take is only
    correct if BOTH halves of the reference reach the model, and a fixture that
    records the clip but not the transcript cannot tell a half-wired clone from a
    whole one."""
    calls: list[dict] = []
    model_loads: list[str] = []

    class FakeModel:
        def generate(self, text, **kw):
            calls.append(
                {
                    "text": text,
                    "voice": kw.get("voice"),
                    "ref_audio": kw.get("ref_audio"),
                    "ref_text": kw.get("ref_text"),
                }
            )
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
        Path(cmd[-1]).write_bytes(b"\x00")  # ffmpeg writes its output last
        return None

    monkeypatch.setattr(render, "run", fake_run)
    return types.SimpleNamespace(calls=calls, model_loads=model_loads)


def _clip(tmp_path: Path, name: str, body: bytes) -> str:
    p = tmp_path / f"{name}.wav"
    p.write_bytes(body)
    return str(p)


def _entry(path: str, transcript: str = "A reference transcript.") -> dict:
    return {"ref_audio": path, "ref_text": transcript}


def _scene(*turns) -> dict:
    return {
        "lines": [{"speaker": s, "text": t} for s, t in turns],
        "source_url": "https://example.com/post",
    }


def _manifest(cast: dict, segments: list[dict]) -> dict:
    return {
        "title": "Surface Tension - test episode",
        "summary": "A hook.",
        "cast": cast,
        "segments": segments,
    }


# --- the manifest whitelist ------------------------------------------------


def test_validate_manifest_accepts_a_recorded_clip_cast_entry(tmp_path):
    """The Phase 3 shape: a cast member is a clip plus its transcript."""
    cast = {"Nora": _entry(_clip(tmp_path, "nora", b"NORA"))}
    render.validate_manifest(_manifest(cast, [_scene(("Nora", "A line."))]))


def test_validate_manifest_still_accepts_a_bundled_preset_cast_entry(tmp_path):
    """A preset string keeps working: the two shapes coexist in one cast."""
    cast = {"Nora": _entry(_clip(tmp_path, "nora", b"NORA")), "Ada": "Ryan"}
    render.validate_manifest(_manifest(cast, [_scene(("Nora", "A."), ("Ada", "B."))]))


@pytest.mark.parametrize(
    "bad, needle",
    [
        ({"ref_audio": "/x/nora.wav"}, "ref_text"),
        ({"ref_text": "A transcript."}, "ref_audio"),
        ({"ref_audio": "/x/nora.wav", "ref_text": "  "}, "ref_text"),
        ({"ref_audio": "", "ref_text": "A transcript."}, "ref_audio"),
        ({"ref_audio": "/x/nora.wav", "ref_text": "T.", "voice": "Ryan"}, "voice"),
    ],
)
def test_validate_manifest_rejects_a_malformed_cast_clip(bad, needle, capsys):
    """A half-written clip entry, or one carrying a stray key, dies naming the field.

    The stray-key case is the typo guard the preset whitelist used to provide on its
    own: `{"ref_audio": ..., "ref_text": ..., "voice": "Ryan"}` reads like it selects
    a preset and does nothing at all."""
    with pytest.raises(SystemExit):
        render.validate_manifest(_manifest({"Nora": bad}, [_scene(("Nora", "A line."))]))
    assert needle in capsys.readouterr().err


def test_validate_manifest_names_the_speaker_of_a_malformed_clip(capsys):
    with pytest.raises(SystemExit):
        render.validate_manifest(
            _manifest({"Nora": {"ref_audio": "/x/n.wav"}}, [_scene(("Nora", "A."))])
        )
    assert "Nora" in capsys.readouterr().err


def test_validate_manifest_still_rejects_an_unknown_preset_name():
    """Loosening the whitelist must not let a mistyped preset through."""
    with pytest.raises(SystemExit):
        render.validate_manifest(_manifest({"Nora": "Rian"}, [_scene(("Nora", "A."))]))


# --- rendering -------------------------------------------------------------


def test_a_cast_clip_line_renders_as_a_clone(tmp_path, fake_tts):
    """The line reaches the model through the clone path, with BOTH reference halves."""
    clip = _clip(tmp_path, "nora", b"NORA")
    cast = {"Nora": _entry(clip, "Nora reading a paragraph.")}
    render.render_segments([_scene(("Nora", "A line."))], "Ryan", tmp_path, cast=cast)

    (call,) = fake_tts.calls
    assert call["ref_audio"] == clip
    assert call["ref_text"] == "Nora reading a paragraph."
    assert call["voice"] is None


def test_a_mixed_cast_renders_each_member_its_own_way(tmp_path, fake_tts):
    cast = {"Nora": _entry(_clip(tmp_path, "nora", b"NORA")), "Ada": "Aiden"}
    render.render_segments(
        [_scene(("Nora", "Clone line."), ("Ada", "Preset line."))], "Ryan", tmp_path, cast=cast
    )

    by_text = {c["text"]: c for c in fake_tts.calls}
    assert by_text["Clone line."]["ref_audio"] is not None
    assert by_text["Clone line."]["voice"] is None
    assert by_text["Preset line."]["voice"] == "Aiden"
    assert by_text["Preset line."]["ref_audio"] is None


def test_a_mixed_cast_still_pays_exactly_one_model_load(tmp_path, fake_tts):
    """A clone cast member runs on the same base MODEL_ID a preset does, which is
    what keeps `speaker` a role rather than a fifth voice mode."""
    cast = {"Nora": _entry(_clip(tmp_path, "nora", b"NORA")), "Ada": "Aiden"}
    render.render_segments([_scene(("Nora", "A."), ("Ada", "B."))], "Ryan", tmp_path, cast=cast)
    assert fake_tts.model_loads == [render.MODEL_ID]


def test_a_cast_clip_does_not_move_the_episode_voice(tmp_path, fake_tts):
    """A plain-text segment keeps rendering in the EPISODE voice while the scene
    beside it clones — the four-mode precedence is untouched."""
    cast = {"Nora": _entry(_clip(tmp_path, "nora", b"NORA"))}
    segments = [{"text": "A plain segment.", "source_url": None}, _scene(("Nora", "A line."))]
    render.render_segments(segments, "Ryan", tmp_path, cast=cast)

    by_text = {c["text"]: c for c in fake_tts.calls}
    assert by_text["A plain segment."]["voice"] == "Ryan"
    assert by_text["A plain segment."]["ref_audio"] is None


def test_a_missing_cast_clip_dies_before_the_model_load(tmp_path, fake_tts):
    """Naming a clip that isn't there must be a named failure, not a traceback out of
    the hashing — and must cost nothing, the same contract validate_manifest has."""
    cast = {"Nora": _entry(str(tmp_path / "absent.wav"))}
    with pytest.raises(SystemExit):
        render.render_segments([_scene(("Nora", "A line."))], "Ryan", tmp_path, cast=cast)
    assert fake_tts.model_loads == []


# --- the cache: one member's clip invalidates only that member --------------


def test_repointing_a_cast_member_at_another_clip_re_renders_that_member(tmp_path, fake_tts):
    """THE hazard. With `mode: "preset"` and a key carrying no ref fields, swapping
    which clip a persona points at leaves the key unchanged — so the run 'succeeds'
    and replays the OLD voice's banked audio under the new one's name, with no error
    anywhere. The clip's identity has to be in the key."""
    a = _clip(tmp_path, "a", b"VOICE-A")
    b = _clip(tmp_path, "b", b"VOICE-B")
    scene = [_scene(("Nora", "A line."))]

    render.render_segments(scene, "Ryan", tmp_path, cast={"Nora": _entry(a)})
    fake_tts.calls.clear()
    render.render_segments(scene, "Ryan", tmp_path, cast={"Nora": _entry(b)})

    assert [c["ref_audio"] for c in fake_tts.calls] == [b], (
        "re-pointing a cast member at a different clip must re-render its lines"
    )


def test_re_recording_one_cast_clip_invalidates_only_that_members_lines(tmp_path, fake_tts):
    """Clips are hashed by BYTES, so re-recording in place (same path) is caught, and
    the other cast members' takes survive."""
    a = _clip(tmp_path, "a", b"VOICE-A")
    b = _clip(tmp_path, "b", b"VOICE-B")
    cast = {"Nora": _entry(a), "Ada": _entry(b)}
    scene = [_scene(("Nora", "Nora line."), ("Ada", "Ada line."))]

    render.render_segments(scene, "Ryan", tmp_path, cast=cast)
    Path(a).write_bytes(b"VOICE-A-TAKE-TWO")
    fake_tts.calls.clear()
    render.render_segments(scene, "Ryan", tmp_path, cast=cast)

    assert [c["text"] for c in fake_tts.calls] == ["Nora line."]


def test_changing_a_cast_transcript_re_renders_that_member(tmp_path, fake_tts):
    """The transcript is half of the reference; the model hears a different clone
    when it moves, so it belongs in the key alongside the bytes."""
    clip = _clip(tmp_path, "nora", b"NORA")
    scene = [_scene(("Nora", "A line."))]

    render.render_segments(scene, "Ryan", tmp_path, cast={"Nora": _entry(clip, "First.")})
    fake_tts.calls.clear()
    render.render_segments(scene, "Ryan", tmp_path, cast={"Nora": _entry(clip, "Second.")})

    assert [c["text"] for c in fake_tts.calls] == ["A line."]


def test_an_unchanged_cast_clip_still_caches(tmp_path, fake_tts):
    """The point of the fingerprint is invalidation, not thrash: a re-run with the
    same clips must skip the model load entirely."""
    cast = {"Nora": _entry(_clip(tmp_path, "nora", b"NORA")), "Ada": "Aiden"}
    scene = [_scene(("Nora", "A."), ("Ada", "B."))]

    render.render_segments(scene, "Ryan", tmp_path, cast=cast)
    fake_tts.model_loads.clear()
    fake_tts.calls.clear()
    render.render_segments(scene, "Ryan", tmp_path, cast=cast)

    assert fake_tts.calls == []
    assert fake_tts.model_loads == []


def test_a_preset_cast_members_key_is_unchanged_by_the_clone_path(tmp_path, fake_tts):
    """A preset line's cache key must not move: the daily show and every existing
    workdir bank takes under the pre-#177 key, and a gratuitous change re-renders
    every scene on the next resume."""
    text = "A preset line."
    prepped = render._prep_segment_text(text, False)
    expected = render._segment_cache_key(prepped, "preset", "Aiden", None, None)

    render.render_segments([_scene(("Ada", text))], "Ryan", tmp_path, cast={"Ada": "Aiden"})

    sidecar = json.loads((tmp_path / "line_01_01.json").read_text())
    assert sidecar["key"] == expected
