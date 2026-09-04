"""The TTS engine registry (spec docs/superpowers/specs/2026-09-04-tts-engine-registry-design.md).

Engine choice is a manifest property with a closed whitelist, the ship_mode posture;
each engine declares what it can do and render.py refuses the rest before any model
load. Qwen3's path is byte-identical to the single-engine renderer it replaces."""

from __future__ import annotations

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
