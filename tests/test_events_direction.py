"""Vocal events and per-line voice direction, gated on the engine (#201).

Qwen3 reads `(laugh)` as "Loff" and `(sigh)` as "Sigh" (the 2026-09-04 eval);
Breeze performs them, and can be told HOW to say a line (`instruct` on a clone).
Neither may reach an engine that lacks the capability: events are stripped there
(a stripped marker is the same line), direction is refused (a dropped direction
is a different performance, and nobody would be told). Every measurement of a
script reads the spoken text with the markers gone, or every event would
lengthen the measured script and skew the rate gate (#186).
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

import render

CLIP = {"ref_audio": "/tmp/ryan.wav", "ref_text": "a transcript"}


def _manifest(segments, **over):
    m = {"title": "T", "summary": "S", "cast": {"a": CLIP, "b": CLIP}, "segments": segments}
    m.update(over)
    return m


def _scene(*turns, url="https://example.com/post"):
    lines = []
    for turn in turns:
        speaker, text = turn[0], turn[1]
        line = {"speaker": speaker, "text": text}
        if len(turn) > 2:
            line["instruct"] = turn[2]
        lines.append(line)
    return {"lines": lines, "source_url": url}


# --- the closed marker list and what stripping it means ----------------------


def test_the_marker_list_is_closed_and_is_what_the_eval_measured():
    assert render.EVENT_MARKERS == ("laugh", "sigh")


def test_stripping_removes_every_listed_marker_and_the_space_it_leaves():
    text = "Okay. (laugh) I read it (sigh), twice. (laugh)"
    assert render.strip_event_markers(text) == "Okay. I read it, twice."


def test_stripping_is_the_identity_on_a_marker_free_line():
    for text in ("Plain text.", "two  spaces stay", " untouched ", "(really) an aside"):
        assert render.strip_event_markers(text) == text


def test_stripping_leaves_an_unlisted_parenthetical_alone():
    # `(cough)` is not an event this code knows; it is text, and whether it is
    # spoken is the writer's problem, not a silent edit here.
    assert render.strip_event_markers("Well (cough) then.") == "Well (cough) then."


# --- every measurement reads the spoken text -------------------------------


def test_the_derived_scene_text_has_no_markers_in_it():
    lines = [{"speaker": "a", "text": "One. (laugh)"}, {"speaker": "b", "text": "(sigh) Two."}]
    assert render.lines_text(lines) == "One. Two."


def test_a_scene_full_of_events_measures_the_same_chars_as_the_clean_one(tmp_path, monkeypatch):
    """The rate gate's population, with and without events, must measure alike:
    the markers are performed, not spoken, so they are not script."""
    n = render.MIN_RATE_SAMPLE_SEGMENTS
    clean = [_scene(("a", "x" * 200), ("b", "y" * 200), url=f"https://e.com/{i}") for i in range(n)]
    noisy = [
        _scene(
            ("a", "(laugh) " + "x" * 200 + " (sigh)"),
            ("b", "(sigh) (laugh) " + "y" * 200),
            url=f"https://e.com/{i}",
        )
        for i in range(n)
    ]
    paths = [tmp_path / f"seg_{i + 1:02d}.mp3" for i in range(n)]
    for p in paths:
        p.write_bytes(b"\x00")
    monkeypatch.setattr(render, "mp3_duration_ms", lambda p: 20_000)
    rows = {}
    for label, segments in (("clean", clean), ("noisy", noisy)):
        manifest = _manifest(segments, tts_engine="breeze")
        render.validate_manifest(manifest)
        render.materialize_line_text(manifest)
        rows[label] = render.speech_rate_rows(manifest["segments"], paths)
    assert len(rows["clean"]) == len(rows["noisy"]) == n
    assert [r["chars"] for r in rows["noisy"]] == [r["chars"] for r in rows["clean"]]


def test_a_plain_segment_with_markers_is_measured_stripped_too(tmp_path, monkeypatch):
    # speech_rate_rows reads seg["text"] directly for a plain segment; nothing
    # materializes that, so the strip has to happen at the measurement.
    n = render.MIN_RATE_SAMPLE_SEGMENTS
    segments = [
        {"text": "(laugh) " + "x" * 100, "source_url": f"https://e.com/{i}"} for i in range(n)
    ]
    paths = [tmp_path / f"seg_{i + 1:02d}.mp3" for i in range(n)]
    for p in paths:
        p.write_bytes(b"\x00")
    monkeypatch.setattr(render, "mp3_duration_ms", lambda p: 10_000)
    rows = render.speech_rate_rows(segments, paths)
    assert [r["chars"] for r in rows] == [100] * n


# --- the engine decides whether a marker is heard ---------------------------


class _FakeAudioResult:
    def __init__(self, n: int = 4):
        self.audio = [0.0] * n


@pytest.fixture
def fake_tts(monkeypatch):
    """tests/test_tts_engines.py's fake_tts: the FULL kwargs of every generate
    call, because this file is about which form a take is rendered through."""
    calls: list[dict] = []
    model_loads: list[str] = []

    class FakeModel:
        def generate(self, text, **kw):
            calls.append({"method": "generate", "text": text, **kw})
            return [_FakeAudioResult()]

        def generate_voice_design(self, text, **kw):
            calls.append({"method": "generate_voice_design", "text": text, **kw})
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
    # Breeze declares the derailment detector (#202); these tests are about events
    # and direction, so the transcriber hears every take exactly as written. (The
    # real one would import mlx_whisper — absent on CI — against the fake numpy.)
    monkeypatch.setattr(render, "transcribe_take", lambda path: calls[-1]["text"])
    return types.SimpleNamespace(calls=calls, model_loads=model_loads)


def _cast(tmp_path):
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFF")
    return {
        "a": {"ref_audio": str(ref), "ref_text": "hi"},
        "b": {"ref_audio": str(ref), "ref_text": "hi"},
    }


def test_an_engine_without_events_never_hears_a_marker(tmp_path, fake_tts):
    seg = _scene(("a", "Okay. (laugh) Right."), ("b", "(sigh) Fine."))
    render.render_segments([seg], "Ryan", tmp_path, cast=_cast(tmp_path), engine="qwen3")
    assert [c["text"] for c in fake_tts.calls] == ["Okay. Right.", "Fine."]


def test_an_engine_with_events_hears_the_marker(tmp_path, fake_tts):
    seg = _scene(("a", "Okay. (laugh) Right."), ("b", "(sigh) Fine."))
    render.render_segments([seg], "house", tmp_path, cast=_cast(tmp_path), engine="breeze")
    assert [c["text"] for c in fake_tts.calls] == ["Okay. (laugh) Right.", "(sigh) Fine."]


def test_a_plain_segment_is_stripped_on_an_engine_without_events_too(tmp_path, fake_tts):
    render.render_segments([{"text": "Well (sigh), no."}], "Ryan", tmp_path, engine="qwen3")
    assert [c["text"] for c in fake_tts.calls] == ["Well, no."]


# --- direction: a manifest field on a line, refused where the engine lacks it --


def test_a_directed_line_dies_on_an_engine_without_direction_naming_it(capsys):
    m = _manifest([_scene(("a", "One."), ("b", "Two.", "exasperated"))], tts_engine="qwen3")
    with pytest.raises(SystemExit):
        render.validate_manifest(m)
    err = capsys.readouterr().err
    assert "segment[0] line 1" in err and "instruct" in err and "engine qwen3" in err


def test_a_directed_line_validates_on_an_engine_with_direction():
    m = _manifest([_scene(("a", "One."), ("b", "Two.", "exasperated"))], tts_engine="breeze")
    render.validate_manifest(m)


@pytest.mark.parametrize("bad", ["", "   ", 3, ["exasperated"]])
def test_an_instruct_must_be_a_non_empty_string(bad, capsys):
    m = _manifest([_scene(("a", "One."), ("b", "Two.", bad))], tts_engine="breeze")
    with pytest.raises(SystemExit):
        render.validate_manifest(m)
    assert "segment[0].lines[1]" in capsys.readouterr().err


def test_a_directed_line_needs_a_clone_reference(monkeypatch, capsys):
    # No registered engine has presets AND direction, so the rule is exercised
    # on a synthetic one: direction is `instruct` over a clone, never over a
    # preset speaker tag.
    fake = render.EngineSpec(
        name="fake",
        label="Fake",
        base_model_id="fake/base",
        design_model_id=None,
        capabilities=frozenset({"preset", "clone", "design", "direction"}),
        presets=("Ryan",),
        max_take_chars=None,
        max_tokens=None,
        min_mlx_audio="0.0.0",
        license="none",
    )
    monkeypatch.setitem(render.ENGINES, "fake", fake)
    monkeypatch.setattr(render, "TTS_ENGINES", (*render.TTS_ENGINES, "fake"))
    m = _manifest(
        [_scene(("p", "One.", "weary"))], cast={"p": "Ryan"}, tts_engine="fake", voice="house"
    )
    with pytest.raises(SystemExit):
        render.validate_manifest(m)
    assert "clone" in capsys.readouterr().err


def test_a_directed_take_renders_through_the_clone_plus_instruct_form(tmp_path, fake_tts):
    cast = _cast(tmp_path)
    seg = _scene(("a", "One."), ("b", "Two.", "Plainly exasperated."))
    render.render_segments([seg], "house", tmp_path, cast=cast, engine="breeze")
    assert fake_tts.model_loads == [render.ENGINES["breeze"].base_model_id]
    ref = cast["a"]["ref_audio"]
    assert fake_tts.calls == [
        {
            "method": "generate",
            "text": "One.",
            "ref_audio": ref,
            "ref_text": "hi",
            "max_tokens": 750,
        },
        {
            "method": "generate",
            "text": "Two.",
            "ref_audio": ref,
            "ref_text": "hi",
            "instruct": "Plainly exasperated.",
            "cfg_scale": render.BREEZE_CFG_SCALE,
            "max_tokens": 750,
        },
    ]


def test_the_directed_takes_key_records_its_instruct(tmp_path, fake_tts):
    cast = _cast(tmp_path)

    def _render(instruct):
        seg = _scene(("a", "One."), ("b", "Two.", instruct))
        render.render_segments([seg], "house", tmp_path, cast=cast, engine="breeze")
        return json.loads((tmp_path / "line_01_02.json").read_text())["key"]

    k1 = _render("weary")
    assert len(fake_tts.calls) == 2
    k2 = _render("urgent")
    assert k2 != k1
    assert len(fake_tts.calls) == 3  # only the directed line re-rendered
    _render("urgent")
    assert len(fake_tts.calls) == 3  # same direction, same take: cached


def test_an_undirected_takes_key_is_unchanged_by_the_instruct_field():
    base = dict(text="t", voice_mode="clone", voice="ryan", ref_fingerprint="abc", ref_text="r")
    k = render._segment_cache_key(**base, engine="breeze", model_id="m")
    assert k == render._segment_cache_key(**base, engine="breeze", model_id="m", instruct=None)
    assert k != render._segment_cache_key(**base, engine="breeze", model_id="m", instruct="weary")


def test_a_qwen3_take_with_an_instruct_dies_at_the_seam(tmp_path, fake_tts):
    with pytest.raises(SystemExit):
        render._render_take(
            object(),
            spec=render.ENGINES["qwen3"],
            text="x",
            mode="clone",
            voice="ryan",
            voice_instruct=None,
            ref_audio="/tmp/ryan.wav",
            ref_text="hi",
            instruct="weary",
            mp3=tmp_path / "a.mp3",
        )
    assert fake_tts.calls == []


# --- voice_instruct + cast: per engine now --------------------------------------


def test_breeze_renders_a_designed_episode_voice_and_a_cast_on_one_load(tmp_path, fake_tts):
    cast = _cast(tmp_path)
    segments = [{"text": "Intro."}, _scene(("a", "One."))]
    render.validate_manifest(
        _manifest(segments, cast=cast, tts_engine="breeze", voice="custom", voice_instruct="calm")
    )
    render.render_segments(
        segments, "custom", tmp_path, voice_instruct="calm", cast=cast, engine="breeze"
    )
    assert fake_tts.model_loads == [render.ENGINES["breeze"].base_model_id]
    assert fake_tts.calls[0] == {
        "method": "generate",
        "text": "Intro.",
        "instruct": "calm",
        "cfg_scale": render.BREEZE_CFG_SCALE,
        "max_tokens": 750,
    }
    assert (
        fake_tts.calls[1]["ref_audio"] == cast["a"]["ref_audio"]
        and "instruct" not in fake_tts.calls[1]
    )
