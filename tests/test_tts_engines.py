"""The TTS engine registry (spec docs/superpowers/specs/2026-09-04-tts-engine-registry-design.md).

Engine choice is a manifest property with a closed whitelist, the ship_mode posture;
each engine declares what it can do and render.py refuses the rest before any model
load. Qwen3's path is byte-identical to the single-engine renderer it replaces."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

import render


def _manifest(**over):
    m = {"title": "T", "summary": "S", "segments": [{"text": "hello there"}]}
    m.update(over)
    return m


def test_tts_engine_defaults_to_qwen3_when_the_key_is_absent():
    assert render.resolve_tts_engine(_manifest()) == "qwen3"
    assert render.engine_spec(_manifest()) is render.ENGINES["qwen3"]


@pytest.mark.parametrize("name", ["qwen3", "breeze"])
def test_tts_engine_accepts_both_documented_engines(name):
    render.validate_manifest(_manifest(tts_engine=name))
    assert render.resolve_tts_engine(_manifest(tts_engine=name)) == name


@pytest.mark.parametrize("bad", ["Breeze", "qwen", "", "mlx-community/Breeze-TTS-2-mlx-8bit"])
def test_validate_manifest_rejects_an_unknown_engine(bad):
    with pytest.raises(SystemExit):
        render.validate_manifest(_manifest(tts_engine=bad))


def test_the_engine_table_is_internally_consistent():
    assert tuple(render.ENGINES) == render.TTS_ENGINES == ("qwen3", "breeze")
    for name, spec in render.ENGINES.items():
        assert spec.name == name
        assert spec.capabilities <= render.ENGINE_CAPABILITIES
        assert bool(spec.presets) == spec.has("preset")
        assert spec.has("clone") and spec.has("design")
    breeze = render.ENGINES["breeze"]
    assert breeze.design_model_id is None
    assert breeze.max_take_chars == 500 and breeze.max_tokens == 750
    assert breeze.min_mlx_audio == "0.5.1"
    assert breeze.has("events") and breeze.has("direction")


def test_the_daily_shows_constants_alias_the_qwen3_entry():
    q = render.ENGINES["qwen3"]
    assert render.MODEL_ID == q.base_model_id == "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"
    assert render.VOICE_DESIGN_MODEL_ID == q.design_model_id
    assert render.VOICES == list(q.presets) == ["Ryan", "Aiden", "Ethan", "Chelsie"]
    assert q.max_take_chars is None and q.max_tokens is None


# --- capabilities gate the voice modes; the ceiling gates the text (spec §3) ------


@pytest.mark.parametrize("voice", ["random", "Ryan", "Chelsie"])
def test_breeze_refuses_a_preset_episode_voice(voice, capsys):
    with pytest.raises(SystemExit):
        render.validate_manifest(_manifest(tts_engine="breeze", voice=voice))
    assert "breeze has no presets" in capsys.readouterr().err


def test_breeze_refuses_a_preset_cast_entry(capsys):
    m = _manifest(
        tts_engine="breeze",
        cast={"anchor": "Ryan"},
        segments=[{"lines": [{"speaker": "anchor", "text": "hi"}]}],
    )
    with pytest.raises(SystemExit):
        render.validate_manifest(m)
    assert "cast['anchor']" in capsys.readouterr().err


def test_breeze_accepts_clones_and_a_designed_voice():
    clip = {"ref_audio": "/tmp/ryan.wav", "ref_text": "a transcript"}
    render.validate_manifest(_manifest(tts_engine="breeze"))  # voice defaults to "house"
    render.validate_manifest(
        _manifest(
            tts_engine="breeze",
            cast={"a": clip},
            segments=[{"lines": [{"speaker": "a", "text": "hi"}]}],
        )
    )
    render.validate_manifest(
        _manifest(tts_engine="breeze", voice="custom", voice_instruct="a calm man")
    )


@pytest.mark.parametrize("engine", ["qwen3", "breeze"])
def test_voice_instruct_with_a_cast_still_dies_on_every_engine(engine):
    clip = {"ref_audio": "/tmp/ryan.wav", "ref_text": "a transcript"}
    m = _manifest(
        tts_engine=engine,
        voice_instruct="a calm man",
        cast={"a": clip},
        segments=[{"lines": [{"speaker": "a", "text": "hi"}]}],
    )
    with pytest.raises(SystemExit):
        render.validate_manifest(m)


def test_qwen3_still_accepts_random_and_presets():
    for voice in ("random", "Ryan", "house"):
        render.validate_manifest(_manifest(voice=voice))


def test_breeze_refuses_a_segment_over_its_take_ceiling(capsys):
    with pytest.raises(SystemExit):
        render.validate_manifest(_manifest(tts_engine="breeze", segments=[{"text": "x" * 501}]))
    assert "renders at most 500 per take" in capsys.readouterr().err
    render.validate_manifest(_manifest(tts_engine="breeze", segments=[{"text": "x" * 500}]))


def test_breeze_refuses_a_scene_line_over_its_take_ceiling(capsys):
    clip = {"ref_audio": "/tmp/ryan.wav", "ref_text": "a transcript"}
    m = _manifest(
        tts_engine="breeze",
        cast={"a": clip},
        segments=[{"lines": [{"speaker": "a", "text": "ok"}, {"speaker": "a", "text": "y" * 501}]}],
    )
    with pytest.raises(SystemExit):
        render.validate_manifest(m)
    assert "segment[0] line 1 is 501 chars" in capsys.readouterr().err


def test_qwen3_has_no_take_ceiling():
    render.validate_manifest(_manifest(segments=[{"text": "x" * 3000}]))


# --- cache key (spec §5) -------------------------------------------------------


def test_cache_key_changes_with_the_engine_and_the_model_id():
    base = dict(text="t", voice_mode="clone", voice="house", ref_fingerprint="abc", ref_text="r")
    k = render._segment_cache_key(**base, engine="qwen3", model_id="m1")
    assert k == render._segment_cache_key(**base, engine="qwen3", model_id="m1")
    assert k != render._segment_cache_key(**base, engine="breeze", model_id="m1")
    assert k != render._segment_cache_key(**base, engine="qwen3", model_id="m2")
    # Still sensitive to everything it was sensitive to before.
    assert k != render._segment_cache_key(**{**base, "text": "u"}, engine="qwen3", model_id="m1")


# --- dispatch (spec §4) --------------------------------------------------------


class _FakeAudioResult:
    def __init__(self, n: int = 4):
        self.audio = [0.0] * n


@pytest.fixture
def fake_tts(monkeypatch):
    """tests/test_lines.py's fake_tts, recording the FULL kwargs and the method
    name, because this file is about the kwargs each engine receives."""
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
    return types.SimpleNamespace(calls=calls, model_loads=model_loads)


def _clip(tmp_path):
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFF")
    return str(ref)


def test_qwen3_clone_kwargs_are_byte_identical_to_the_single_engine_renderer(tmp_path, fake_tts):
    ref = _clip(tmp_path)
    render.render_segments(
        [{"text": "hello there"}], "house", tmp_path, ref_audio=ref, ref_text="hi", engine="qwen3"
    )
    assert fake_tts.model_loads == [render.MODEL_ID]
    assert fake_tts.calls == [
        {
            "method": "generate",
            "text": "hello there",
            "language": "English",
            "ref_audio": ref,
            "ref_text": "hi",
        }
    ]


def test_qwen3_preset_kwargs_are_unchanged(tmp_path, fake_tts):
    render.render_segments([{"text": "hello there"}], "Ryan", tmp_path, engine="qwen3")
    assert fake_tts.calls == [
        {"method": "generate", "text": "hello there", "voice": "Ryan", "language": "English"}
    ]


def test_qwen3_design_still_loads_the_second_model(tmp_path, fake_tts):
    render.render_segments(
        [{"text": "hello there"}], "custom", tmp_path, voice_instruct="a calm man", engine="qwen3"
    )
    assert fake_tts.model_loads == [render.VOICE_DESIGN_MODEL_ID]
    assert fake_tts.calls == [
        {
            "method": "generate_voice_design",
            "text": "hello there",
            "language": "English",
            "instruct": "a calm man",
        }
    ]


def test_breeze_clone_passes_the_cap_and_no_language(tmp_path, fake_tts):
    ref = _clip(tmp_path)
    render.render_segments(
        [{"text": "hello there"}], "house", tmp_path, ref_audio=ref, ref_text="hi", engine="breeze"
    )
    assert fake_tts.model_loads == [render.ENGINES["breeze"].base_model_id]
    assert fake_tts.calls == [
        {
            "method": "generate",
            "text": "hello there",
            "ref_audio": ref,
            "ref_text": "hi",
            "max_tokens": 750,
        }
    ]


def test_breeze_design_runs_on_the_base_model(tmp_path, fake_tts):
    render.render_segments(
        [{"text": "hello there"}], "custom", tmp_path, voice_instruct="a calm man", engine="breeze"
    )
    assert fake_tts.model_loads == [render.ENGINES["breeze"].base_model_id]
    assert fake_tts.calls == [
        {
            "method": "generate",
            "text": "hello there",
            "instruct": "a calm man",
            "cfg_scale": 4.0,
            "max_tokens": 750,
        }
    ]


def test_breeze_cast_clip_lines_render_through_the_clone_form(tmp_path, fake_tts):
    ref = _clip(tmp_path)
    seg = {"lines": [{"speaker": "a", "text": "one"}, {"speaker": "a", "text": "two"}]}
    render.render_segments(
        [seg], "Ryan", tmp_path, cast={"a": {"ref_audio": ref, "ref_text": "hi"}}, engine="breeze"
    )
    assert fake_tts.model_loads == [render.ENGINES["breeze"].base_model_id]
    assert [c["text"] for c in fake_tts.calls] == ["one", "two"]
    assert all(c["max_tokens"] == 750 and "language" not in c for c in fake_tts.calls)


def test_a_breeze_preset_take_dies_rather_than_rendering_a_stranger(tmp_path):
    with pytest.raises(SystemExit):
        render._render_take(
            object(),
            spec=render.ENGINES["breeze"],
            text="x",
            mode="preset",
            voice="Ryan",
            voice_instruct=None,
            ref_audio=None,
            ref_text=None,
            mp3=tmp_path / "a.mp3",
        )


def test_the_take_key_records_the_engine_that_rendered_it(tmp_path, fake_tts):
    ref = _clip(tmp_path)
    render.render_segments(
        [{"text": "hello there"}], "house", tmp_path, ref_audio=ref, ref_text="hi", engine="qwen3"
    )
    k_qwen = json.loads((tmp_path / "seg_01.json").read_text())["key"]
    render.render_segments(
        [{"text": "hello there"}], "house", tmp_path, ref_audio=ref, ref_text="hi", engine="breeze"
    )
    k_breeze = json.loads((tmp_path / "seg_01.json").read_text())["key"]
    assert k_qwen != k_breeze
    assert len(fake_tts.calls) == 2  # the second engine did not reuse the first's take


# --- pre-flight (spec §6) ------------------------------------------------------


def test_engine_check_fails_when_mlx_audio_is_too_old(monkeypatch):
    monkeypatch.setattr(render, "_installed_mlx_audio_version", lambda: "0.4.9")
    c = render._tts_engine_check(render.ENGINES["breeze"])
    assert c["name"] == "tts-engine" and c["ok"] is False
    assert "needs mlx-audio >= 0.5.1" in c["detail"] and "0.4.9" in c["detail"]


@pytest.mark.parametrize("installed", ["0.5.1", "0.5.10", "1.0.0"])
def test_engine_check_passes_at_or_above_the_floor_and_names_the_license(monkeypatch, installed):
    monkeypatch.setattr(render, "_installed_mlx_audio_version", lambda: installed)
    c = render._tts_engine_check(render.ENGINES["breeze"])
    assert c["ok"] is True
    assert "BreezeBlue Research and Non-Commercial" in c["detail"]
    assert "Breeze-TTS-2 3B" in c["detail"]


def test_engine_check_leaves_an_absent_package_to_the_module_check(monkeypatch):
    monkeypatch.setattr(render, "_installed_mlx_audio_version", lambda: None)
    c = render._tts_engine_check(render.ENGINES["qwen3"])
    assert c["ok"] is True and "tts-module" in c["detail"]


def test_version_tuple_compares_dotted_strings_numerically():
    assert render._version_tuple("0.5.10") > render._version_tuple("0.5.9")
    assert render._version_tuple("0.4.3") == (0, 4, 3)
    assert render._version_tuple("1.2.3.dev4") == (1, 2, 3)


def test_preflight_runs_the_engine_check_under_dry_run(monkeypatch):
    monkeypatch.setattr(render.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(
        render, "_tts_module_check", lambda: render._check("tts-module", True, "stub")
    )
    monkeypatch.setattr(
        render, "check_r2_credentials", lambda cfg, required=False: {"ok": True, "detail": "stub"}
    )
    monkeypatch.setattr(render, "_installed_mlx_audio_version", lambda: "0.4.3")
    ok, checks = render.preflight({}, show_id="spotify:show:x", dry_run=True, engine="breeze")
    names = [c["name"] for c in checks]
    assert "tts-engine" in names and names.index("tts-engine") == names.index("tts-module") + 1
    assert ok is False
    assert next(c for c in checks if c["name"] == "tts-engine")["ok"] is False


# --- run log, bloopers, payloads (spec §7) -------------------------------------


def test_tts_engine_is_appended_last_to_both_field_sets():
    assert render.RUN_LOG_FIELDS[-1] == "tts_engine"
    assert render.RUN_LOG_FIELDS[-2] == "bloopers_captured"  # nothing reordered
    assert render.BLOOPER_FIELDS[-1] == "tts_engine"
    assert render.BLOOPER_FIELDS[-2] == "workdir"
    assert render._new_run_record()["tts_engine"] is None
