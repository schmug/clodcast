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
