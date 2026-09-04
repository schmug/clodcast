"""Chunked rendering and the derailment detector (#202).

A short-take engine (Breeze: 500 chars, measured) could not render a narrator
show at all: every daily-show band exceeds its ceiling. Two mechanisms close that.
A plain-text segment over the ceiling is split at SENTENCE boundaries into balanced
chunks that join into the same seg_NN.mp3 a scene's line takes do; and on an engine
that declares it, every rendered take is transcribed and a derailed one is banked,
then re-rolled once, before the artifact gate rejects a second failure.

Organised around what can go wrong:
  1. a chunk boundary lands mid-sentence, or a chunk exceeds the ceiling;
  2. the chunker starves one chunk (a 3-word tail is where WER is coarsest);
  3. the composed key re-renders a whole segment for one bad chunk;
  4. Qwen3's path changes at all;
  5. the detector fires on the wrong engine, banks nothing, or re-rolls forever.
"""

from __future__ import annotations

import pytest

import render

# --- 1. sentence boundaries --------------------------------------------------


def test_split_sentences_breaks_on_terminal_punctuation():
    assert render.split_sentences("One thing. Two things! Three things?") == [
        "One thing.",
        "Two things!",
        "Three things?",
    ]


def test_split_sentences_keeps_closing_quotes_with_their_sentence():
    text = 'He said "go now." Then he left (quietly.) Done.'
    assert render.split_sentences(text) == ['He said "go now."', "Then he left (quietly.)", "Done."]


def test_split_sentences_does_not_break_on_abbreviations_or_initials():
    text = "The U.S. Senate met Dr. Smith at 3 p.m. today. J. K. Rowling was not there."
    assert render.split_sentences(text) == [
        "The U.S. Senate met Dr. Smith at 3 p.m. today.",
        "J. K. Rowling was not there.",
    ]


def test_split_sentences_does_not_break_on_decimals_or_lowercase_continuations():
    text = "Version 3.5 shipped... and then it broke. It works now."
    assert render.split_sentences(text) == [
        "Version 3.5 shipped... and then it broke.",
        "It works now.",
    ]


def test_split_sentences_treats_unpunctuated_text_as_one_sentence():
    assert render.split_sentences("no punctuation at all") == ["no punctuation at all"]


# --- 2. chunks: never mid-sentence, never over the cap, never starved ---------


def _prose(n_sentences: int) -> str:
    """Sentences of 104 chars each, numbered so one chunk can be told from another.
    Four fit a 500 cap (419 chars) and five do not (524), so ten of them is the
    shape first-fit packs as 4 + 4 + 2 and leaves a 209-char tail."""
    return " ".join(
        f"Sentence {i:02d} says one thing and then another thing and then a third thing "
        "before it finally ends at last."
        for i in range(n_sentences)
    )


def test_chunk_text_returns_the_text_itself_when_it_fits():
    text = "Short. Enough."
    assert render.chunk_text(text, 500) == [text]
    assert render.chunk_text("x" * 500, 500) == ["x" * 500]


def test_an_1100_char_segment_chunks_at_sentence_boundaries_under_the_cap():
    text = _prose(11)
    assert 1100 <= len(text) <= 1200, len(text)
    chunks = render.chunk_text(text, 500)
    assert len(chunks) >= 3
    assert all(len(c) <= 500 for c in chunks)
    assert all(c.endswith(".") for c in chunks), "a chunk boundary landed mid-sentence"
    assert " ".join(chunks) == text


def test_chunk_text_balances_chunks_rather_than_starving_the_tail():
    """Ten 104-char sentences under a 500 cap. Greedy first-fit packs 4+4+2 and
    leaves a 209-char tail; the tail is exactly where the transcript check is
    coarsest (WER's granularity is one word), so the packer aims every chunk at
    an equal share instead (3+4+3 here: 314, 419, 314)."""
    text = _prose(10)
    chunks = render.chunk_text(text, 500)
    assert len(chunks) == 3
    assert min(len(c) for c in chunks) >= 300, [len(c) for c in chunks]
    assert " ".join(chunks) == text


def test_chunk_text_refuses_a_single_sentence_over_the_cap():
    with pytest.raises(ValueError, match="sentence"):
        render.chunk_text("a" * 501 + ". Short.", 500)


def test_chunk_text_is_deterministic():
    text = _prose(9)
    assert render.chunk_text(text, 500) == render.chunk_text(text, 500)


# --- validation: the ceiling now bounds a SENTENCE, not a segment -------------


def _breeze(segments, **over):
    m = {"title": "T", "summary": "S", "tts_engine": "breeze", "segments": segments}
    m.update(over)
    return m


def test_breeze_accepts_an_1100_char_multi_sentence_segment():
    render.validate_manifest(_breeze([{"text": _prose(11)}]))


def test_breeze_refuses_a_single_sentence_over_the_ceiling(capsys):
    one_sentence = "Word " + "word " * 119 + "end."  # ~600 chars, one terminator
    with pytest.raises(SystemExit):
        render.validate_manifest(_breeze([{"text": "Fine. " + one_sentence}]))
    err = capsys.readouterr().err
    assert "segment[0]" in err and "sentence" in err and "renders at most 500 per take" in err


def test_validation_chunks_the_same_prepped_text_the_render_will():
    """normalize_for_tts strips a URL before the model sees the text, so the ceiling
    is judged on what will actually be spoken — and raw_text, which skips that
    normalization, is judged on the raw text."""
    url = "https://example.com/" + "a" * 200
    text = "word " * 88 + f"see {url} end."  # ~660 raw; ~440 once the URL is stripped
    render.validate_manifest(_breeze([{"text": text}]))
    with pytest.raises(SystemExit):
        render.validate_manifest(_breeze([{"text": text}], raw_text=True))


# --- the render loop: chunk takes join into ONE seg_NN.mp3 ---------------------

import json  # noqa: E402
import sys  # noqa: E402
import types  # noqa: E402
from pathlib import Path  # noqa: E402


class _FakeAudioResult:
    def __init__(self, n: int = 4):
        self.audio = [0.0] * n


@pytest.fixture
def fake_tts(monkeypatch):
    """tests/test_lines.py's seam: fake numpy / soundfile / mlx_audio, ffmpeg stubbed
    to touch its output. Records generate() texts, model loads, and every ffmpeg
    argv so the join can be asserted without an encoder."""
    calls: list[dict] = []
    model_loads: list[str] = []
    commands: list[list[str]] = []

    class FakeModel:
        # Every take's "audio" is one byte longer than the last, so two renders of
        # the same text produce different bytes — a re-roll can be told from the
        # take it replaced, which is what the bank-before-rerender test needs.
        def generate(self, text, **kw):
            calls.append({"text": text, **kw})
            return [_FakeAudioResult(len(calls))]

        def generate_voice_design(self, text, **kw):
            calls.append({"text": text, **kw})
            return [_FakeAudioResult(len(calls))]

    fake_np = types.ModuleType("numpy")
    fake_np.concatenate = lambda arrs: [x for a in arrs for x in a]
    fake_np.array = lambda x: list(x)
    monkeypatch.setitem(sys.modules, "numpy", fake_np)
    fake_sf = types.ModuleType("soundfile")
    fake_sf.write = lambda path, audio, sr: Path(path).write_bytes(b"\x01" * len(audio))
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
        # ffmpeg writes its output last; a wav -> mp3 encode "carries" the take's
        # bytes so the mp3 is as distinguishable as the audio it came from.
        src = Path(cmd[cmd.index("-i") + 1]) if "-i" in cmd else None
        data = src.read_bytes() if src and src.suffix == ".wav" and src.exists() else b"\x00"
        Path(cmd[-1]).write_bytes(data)
        return None

    monkeypatch.setattr(render, "run", fake_run)
    return types.SimpleNamespace(calls=calls, model_loads=model_loads, commands=commands)


def _clip(tmp_path):
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFF")
    return str(ref)


def _render(tmp_path, segments, engine="breeze", **kw):
    ref = _clip(tmp_path)
    return render.render_segments(
        segments, "house", tmp_path, ref_audio=ref, ref_text="hi", engine=engine, **kw
    )


def _concat_lists(tmp_path):
    return {p.name: p.read_text() for p in tmp_path.glob("*.txt")}


def test_a_long_segment_renders_as_chunk_takes_joined_into_one_seg(
    tmp_path, fake_tts, fake_transcribe
):
    text = _prose(11)
    paths = _render(tmp_path, [{"text": text, "source_url": "https://x/1"}])
    chunks = render.chunk_text(text, 500)
    assert len(chunks) == 3
    assert [c["text"] for c in fake_tts.calls] == chunks
    assert all(c["max_tokens"] == 750 for c in fake_tts.calls)
    # One chapter's worth of output, with the takes beside it.
    assert paths == [tmp_path / "seg_01.mp3"]
    assert sorted(p.name for p in tmp_path.glob("chunk_*.mp3")) == [
        "chunk_01_01.mp3",
        "chunk_01_02.mp3",
        "chunk_01_03.mp3",
    ]
    # Joined with the CHUNK gap, not the turn gap and not the chapter silence.
    lists = _concat_lists(tmp_path)
    assert "seg_01_chunks.txt" in lists
    assert lists["seg_01_chunks.txt"].count(f"silence_{render.CHUNK_GAP_MS}ms.mp3") == 2
    assert render.CHUNK_GAP_MS not in (render.TURN_GAP_MS, render.DEFAULT_SILENCE_MS)


def test_a_chunked_segment_keys_like_a_scene_composes_its_takes(
    tmp_path, fake_tts, fake_transcribe
):
    text = _prose(11)
    _render(tmp_path, [{"text": text}])
    take_keys = [
        json.loads((tmp_path / f"chunk_01_{j:02d}.json").read_text())["key"] for j in (1, 2, 3)
    ]
    seg_key = json.loads((tmp_path / "seg_01.json").read_text())["key"]
    assert seg_key == render._chunked_cache_key(take_keys)
    assert seg_key != render._chunked_cache_key(list(reversed(take_keys)))
    # A scene with the same take keys is a different artifact: a different gap is
    # welded between its takes.
    assert seg_key != render._scene_cache_key(take_keys)


def test_one_bad_chunk_re_renders_one_chunk(tmp_path, fake_tts, fake_transcribe):
    """Two cache levels, the scene contract (#172): the documented recovery deletes
    seg_NN.mp3, which re-joins the banked takes without a model load; deleting one
    chunk take beside it re-renders that chunk and only that chunk."""
    text = _prose(11)
    _render(tmp_path, [{"text": text}])
    fake_tts.calls.clear()
    fake_tts.model_loads.clear()
    (tmp_path / "seg_01.mp3").unlink()
    _render(tmp_path, [{"text": text}])
    assert fake_tts.calls == [] and fake_tts.model_loads == []
    assert (tmp_path / "seg_01.mp3").exists()

    (tmp_path / "seg_01.mp3").unlink()
    (tmp_path / "chunk_01_02.json").unlink()
    _render(tmp_path, [{"text": text}])
    assert [c["text"] for c in fake_tts.calls] == [render.chunk_text(text, 500)[1]]
    assert (tmp_path / "chunk_01_02.json").exists()
    assert (tmp_path / "seg_01.json").exists()


def test_a_fully_cached_chunked_segment_skips_the_model_load(tmp_path, fake_tts, fake_transcribe):
    text = _prose(11)
    _render(tmp_path, [{"text": text}])
    fake_tts.calls.clear()
    fake_tts.model_loads.clear()
    _render(tmp_path, [{"text": text}])
    assert fake_tts.calls == [] and fake_tts.model_loads == []


def test_qwen3_never_chunks(tmp_path, fake_tts):
    """No ceiling, no chunker: the single-engine renderer's plan, files and key."""
    text = _prose(11)
    _render(tmp_path, [{"text": text}], engine="qwen3")
    assert [c["text"] for c in fake_tts.calls] == [text]
    assert list(tmp_path.glob("chunk_*")) == []
    ref_fp = render._ref_audio_fingerprint(str(tmp_path / "ref.wav"))
    expected = render._segment_cache_key(
        text, "clone", "house", ref_fp, "hi", engine="qwen3", model_id=render.MODEL_ID
    )
    assert json.loads((tmp_path / "seg_01.json").read_text())["key"] == expected


def test_a_segment_under_the_ceiling_plans_exactly_as_before(tmp_path, fake_tts, fake_transcribe):
    """A Breeze take already banked in a workdir must stay a cache hit: a short
    segment is one take under the single-take key, with no chunk files."""
    text = _prose(3)
    _render(tmp_path, [{"text": text}])
    assert [c["text"] for c in fake_tts.calls] == [text]
    assert list(tmp_path.glob("chunk_*")) == []
    ref_fp = render._ref_audio_fingerprint(str(tmp_path / "ref.wav"))
    expected = render._segment_cache_key(
        text,
        "clone",
        "house",
        ref_fp,
        "hi",
        engine="breeze",
        model_id=render.ENGINES["breeze"].base_model_id,
    )
    assert json.loads((tmp_path / "seg_01.json").read_text())["key"] == expected


# --- 5. the derailment detector ----------------------------------------------

import bench  # noqa: E402

BABBLE = "這是 一段 胡言亂語"


def test_engines_declare_the_detector_and_only_breeze_opts_in():
    assert render.ENGINES["breeze"].detect_derailment is True
    assert render.ENGINES["qwen3"].detect_derailment is False


def test_the_derailment_rule_has_one_definition_which_the_bench_reuses():
    """The bench's rule is the spec's; render.py now owns it and bench.py aliases
    it, so the two can never drift apart."""
    assert render.DERAIL_WER == 0.15 and render.DERAIL_WORD_RATIO == (0.9, 1.1)
    assert bench.derailment is render.derailment
    assert bench.word_error_rate is render.word_error_rate
    assert bench.normalize_words is render.normalize_words
    assert bench.DERAIL_WER is render.DERAIL_WER
    assert bench.DERAIL_WORD_RATIO is render.DERAIL_WORD_RATIO
    assert bench.WHISPER_MODEL == render.WHISPER_MODEL
    script = "one two three four five six seven eight nine ten"
    assert render.derailment(script, script) == []
    assert "non-ascii" in render.derailment(script, BABBLE)
    assert "wer" in render.derailment(script, "one two three")


@pytest.fixture
def fake_transcribe(monkeypatch, fake_tts):
    """Scripted transcripts, one per transcribe_take call: BABBLE, or None for "the
    script of the take just rendered" (a clean take). Records every call. Every
    Breeze render in this file needs it: the engine declares the detector, and the
    real transcriber would import mlx_whisper (absent on CI, and on a host it would
    read the fake numpy the TTS seam installs)."""
    plan: list[str | None] = []
    seen: list[Path] = []

    def transcribe(path):
        seen.append(Path(path))
        verdict = plan.pop(0) if plan else None
        return verdict if verdict is not None else fake_tts.calls[-1]["text"]

    monkeypatch.setattr(render, "transcribe_take", transcribe)
    return types.SimpleNamespace(plan=plan, seen=seen)


def _index_rows():
    path = render.BLOOPER_DIR / "index.jsonl"
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def test_a_derailed_take_is_banked_then_rerolled_once(tmp_path, fake_tts, fake_transcribe):
    text = _prose(3)
    fake_transcribe.plan[:] = [BABBLE, None]
    first_bytes: list[bytes] = []

    real_bank = render.bank_blooper

    def bank_then_record(audio, **kw):
        first_bytes.append(Path(audio).read_bytes())
        return real_bank(audio, **kw)

    render.bank_blooper = bank_then_record
    try:
        paths = _render(
            tmp_path, [{"text": text, "source_url": "https://x/1"}], blooper_ctx={"title": "T"}
        )
    finally:
        render.bank_blooper = real_bank

    assert [c["text"] for c in fake_tts.calls] == [text, text]  # rendered, re-rolled
    assert len(fake_transcribe.seen) == 2
    rows = _index_rows()
    assert [r["reason"] for r in rows] == ["derailed"]
    row = rows[0]
    assert row["segment"] == 1 and row["text"] == text and row["source_url"] == "https://x/1"
    assert row["tts_engine"] == "breeze" and row["title"] == "T"
    assert "non-ascii" in row["note"] and BABBLE in row["note"]
    # Banked BEFORE the re-render: the clip is the first take's bytes, the shipped
    # seg is the second's, and they differ.
    assert first_bytes == [b"\x01" * 1]
    assert Path(row["clip"]).read_bytes() == b"\x01" * 1
    assert paths[0].read_bytes() == b"\x01" * 2
    assert (tmp_path / "seg_01.json").exists()
    events = render.load_derailed(tmp_path)
    assert len(events) == 1 and events[0]["final"] is False and events[0]["attempt"] == 1


def test_a_take_that_derails_twice_is_left_for_the_gate(tmp_path, fake_tts, fake_transcribe):
    text = _prose(3)
    fake_transcribe.plan[:] = [BABBLE, BABBLE, BABBLE]
    paths = _render(tmp_path, [{"text": text, "source_url": "https://x/1"}])
    assert len(fake_tts.calls) == 2, "bounded: one re-roll, never a third attempt"
    # No sidecar for the bad take, so a same-workdir re-run re-rolls it (and only it).
    assert not (tmp_path / "seg_01.json").exists()
    assert paths == [tmp_path / "seg_01.mp3"] and paths[0].exists()
    events = render.load_derailed(tmp_path)
    assert [(e["final"], e["attempt"]) for e in events] == [(False, 1), (True, 2)]
    assert len(_index_rows()) == 2  # both attempts banked; the run-failed sweep is not needed

    errors = render.verify_artifact(
        paths[0],
        {"items": []},
        duration_ms=10_000,
        profile={},
        derailed=events,
    )
    assert len(errors) == 1
    assert "segment 1" in errors[0] and "derailed" in errors[0] and "non-ascii" in errors[0]
    assert render.classify_incident("artifact gate failed: " + errors[0]) == "tts-degeneration"


def test_a_derailed_chunk_names_its_chunk_and_reroll_leaves_the_others_cached(
    tmp_path, fake_tts, fake_transcribe
):
    text = _prose(11)
    chunks = render.chunk_text(text, 500)
    fake_transcribe.plan[:] = [None, BABBLE, BABBLE, None]
    _render(tmp_path, [{"text": text}])
    assert [c["text"] for c in fake_tts.calls] == [chunks[0], chunks[1], chunks[1], chunks[2]]
    events = render.load_derailed(tmp_path)
    assert [e["final"] for e in events] == [False, True]
    assert events[1]["take"] == "chunk_01_02" and "chunk 2" in events[1]["label"]
    assert (tmp_path / "chunk_01_01.json").exists() and (tmp_path / "chunk_01_03.json").exists()
    assert not (tmp_path / "chunk_01_02.json").exists()
    assert not (tmp_path / "seg_01.json").exists()
    # The re-run re-rolls exactly the bad chunk.
    fake_tts.calls.clear()
    fake_transcribe.plan[:] = [None]
    _render(tmp_path, [{"text": text}])
    assert [c["text"] for c in fake_tts.calls] == [chunks[1]]
    assert (tmp_path / "seg_01.json").exists()
    assert render.load_derailed(tmp_path) == []


def test_dry_run_rerolls_but_banks_nothing(tmp_path, fake_tts, fake_transcribe, capsys):
    text = _prose(3)
    fake_transcribe.plan[:] = [BABBLE, None]
    _render(tmp_path, [{"text": text}], dry_run=True)
    assert len(fake_tts.calls) == 2
    assert _index_rows() == []
    assert not (render.BLOOPER_DIR / "clips").exists()
    assert "would bank derailed" in capsys.readouterr().err


def test_qwen3_never_transcribes(tmp_path, fake_tts, monkeypatch):
    def boom(path):
        raise AssertionError("the detector ran on an engine that did not declare it")

    monkeypatch.setattr(render, "transcribe_take", boom)
    _render(tmp_path, [{"text": _prose(3)}], engine="qwen3")
    # ...and a caller can switch it off on one that did (the bench measures the raw rate).
    _render(tmp_path, [{"text": _prose(3)}], engine="breeze", detect_derailment=False)


def test_a_clean_take_writes_no_derailment_report_under_qwen3(tmp_path, fake_tts):
    _render(tmp_path, [{"text": _prose(3)}], engine="qwen3")
    assert not (tmp_path / render.DERAILED_FILENAME).exists()
    assert render.load_derailed(tmp_path) == []


def test_a_clean_breeze_run_writes_an_empty_report(tmp_path, fake_tts, fake_transcribe):
    """Always rewritten when the detector runs, so a stale report from an earlier run
    in the same workdir cannot fail a clean re-render."""
    (tmp_path / render.DERAILED_FILENAME).write_text(json.dumps([{"final": True}]))
    _render(tmp_path, [{"text": _prose(3)}])
    assert render.load_derailed(tmp_path) == []


def test_load_derailed_tolerates_junk(tmp_path):
    (tmp_path / render.DERAILED_FILENAME).write_text("{not json")
    assert render.load_derailed(tmp_path) == []


def test_derailment_problems_ignore_rerolls_that_succeeded():
    ok = [
        {"final": False, "label": "segment 1", "attempt": 1, "reasons": ["wer"], "transcript": ""}
    ]
    assert render.verify_artifact.__doc__  # sanity: real function
    assert render.derailment_problems(ok) == []
    bad = ok + [{**ok[0], "final": True, "attempt": 2}]
    assert len(render.derailment_problems(bad)) == 1


# --- pre-flight: the detector's dependency is checked before any render ---------


def _preflight(monkeypatch, engine, *, whisper_installed):
    monkeypatch.setattr(render.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(
        render, "_tts_module_check", lambda: render._check("tts-module", True, "stub")
    )
    monkeypatch.setattr(
        render, "check_r2_credentials", lambda cfg, required=False: {"ok": True, "detail": "stub"}
    )
    monkeypatch.setattr(render, "_installed_mlx_audio_version", lambda: "0.5.1")
    real_find_spec = render.importlib.util.find_spec

    def find_spec(name, *a, **k):
        if name == "mlx_whisper":
            return object() if whisper_installed else None
        return real_find_spec(name, *a, **k)

    monkeypatch.setattr(render.importlib.util, "find_spec", find_spec)
    return render.preflight({}, show_id="spotify:show:x", dry_run=True, engine=engine)


def test_preflight_fails_a_breeze_run_without_whisper(monkeypatch):
    ok, checks = _preflight(monkeypatch, "breeze", whisper_installed=False)
    check = next(c for c in checks if c["name"] == "derailment-detector")
    assert ok is False and check["ok"] is False
    assert "mlx-whisper" in check["detail"]


def test_preflight_passes_a_breeze_run_with_whisper_and_names_the_model(monkeypatch):
    ok, checks = _preflight(monkeypatch, "breeze", whisper_installed=True)
    check = next(c for c in checks if c["name"] == "derailment-detector")
    assert ok is True and check["ok"] is True
    assert render.WHISPER_MODEL in check["detail"]


def test_preflight_has_no_detector_check_for_qwen3(monkeypatch):
    ok, checks = _preflight(monkeypatch, "qwen3", whisper_installed=False)
    assert ok is True
    assert "derailment-detector" not in [c["name"] for c in checks]


# --- the daily-show fixture: every band renders on a short-take engine ----------

import orchestrate  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "data" / "daily_manifest.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def test_the_fixture_is_the_daily_shows_shape():
    m = _fixture()
    bodies = [s for s in m["segments"] if s.get("source_url")]
    assert len(bodies) >= render.MIN_RATE_SAMPLE_SEGMENTS  # the speech-rate gate is armed
    lo, hi = orchestrate.SHORT_BAND[0], orchestrate.LEAD_BAND[1]
    assert all(lo <= len(s["text"]) <= hi for s in bodies)
    assert max(len(s["text"]) for s in bodies) > 1000  # a LEAD_BAND lead is present
    assert not m["segments"][0].get("source_url") and not m["segments"][-1].get("source_url")
    assert "tts_engine" not in m  # the engine is applied by the test / the run, not the shape


def test_the_fixture_validates_on_breeze():
    render.validate_manifest({**_fixture(), "tts_engine": "breeze"})


def test_the_fixture_renders_on_breeze_as_chunked_chapters(tmp_path, fake_tts, fake_transcribe):
    m = {**_fixture(), "tts_engine": "breeze"}
    voice, instruct, ref_audio, ref_text = render.resolve_voice(m)  # the bundled house clip
    paths = render.render_segments(
        m["segments"], voice, tmp_path, ref_audio=ref_audio, ref_text=ref_text, engine="breeze"
    )
    assert [p.name for p in paths] == [f"seg_{i:02d}.mp3" for i in range(1, 9)]
    assert all(len(c["text"]) <= 500 for c in fake_tts.calls)
    per_segment = {}
    for name in (p.stem for p in tmp_path.glob("chunk_*.mp3")):
        _, seg, _ = name.split("_")
        per_segment[int(seg)] = per_segment.get(int(seg), 0) + 1
    assert per_segment[2] >= 3  # the 1038-char lead
    assert all(per_segment.get(i, 1) >= 2 for i in range(2, 8))  # every body segment
    assert 1 not in per_segment and 8 not in per_segment  # intro / sign-off fit in one take
    assert len(fake_transcribe.seen) == len(fake_tts.calls)  # every take was verified


# --- _render: the report reaches the gate and the run log ---------------------


def _stub_render_pipeline(monkeypatch, tmp_path, *, events):
    """Everything below render_segments stubbed, the test_reliability pattern; the
    stub render writes the derailment report the real one would."""
    wd = tmp_path / "wd"
    wd.mkdir()

    def fake_render_segments(segments, voice, workdir, **kw):
        (workdir / render.DERAILED_FILENAME).write_text(json.dumps(events))
        p = workdir / "seg_01.mp3"
        p.write_bytes(b"\x00")
        return [p]

    monkeypatch.setattr(render, "render_segments", fake_render_segments)
    monkeypatch.setattr(render, "preflight", lambda *a, **k: (True, []))
    monkeypatch.setattr(render, "load_config", lambda: {"show_id": "spotify:show:1"})
    monkeypatch.setattr(render, "plan_silences", lambda paths: [0])

    def fake_concat(paths, silences, workdir):
        out = workdir / "episode.mp3"
        out.write_bytes(b"\x00")
        return out, None

    monkeypatch.setattr(render, "concat_and_normalize", fake_concat)
    monkeypatch.setattr(render, "build_cover", lambda *a, **k: None)
    monkeypatch.setattr(
        render,
        "build_timeline_and_description",
        lambda *a, **k: ({"items": [{"chapter": {"title": "A", "start_ms": 0}}]}, "<p>d</p>"),
    )
    monkeypatch.setattr(render, "mp3_duration_ms", lambda p: 60_000)
    monkeypatch.setattr(render, "probe_audio_profile", lambda p: {})
    return wd


def _main(monkeypatch, tmp_path, manifest, wd):
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(
        sys, "argv", ["render.py", "--manifest", str(path), "--dry-run", "--workdir", str(wd)]
    )
    return render.main()


def _run_records():
    return [json.loads(ln) for ln in render.RUN_LOG_PATH.read_text().splitlines() if ln.strip()]


def test_a_final_derailment_fails_the_dry_run_through_the_artifact_gate(monkeypatch, tmp_path):
    events = [
        {"segment": 1, "take": "seg_01", "label": "segment 1", "attempt": 1, "final": False,
         "reasons": ["non-ascii"], "transcript": BABBLE, "chars": 10},
        {"segment": 1, "take": "seg_01", "label": "segment 1", "attempt": 2, "final": True,
         "reasons": ["non-ascii"], "transcript": BABBLE, "chars": 10},
    ]  # fmt: skip
    wd = _stub_render_pipeline(monkeypatch, tmp_path, events=events)
    manifest = {"title": "T", "summary": "s", "tts_engine": "breeze", "segments": [{"text": "hi"}]}
    with pytest.raises(SystemExit) as e:
        _main(monkeypatch, tmp_path, manifest, wd)
    assert e.value.code != 0
    rec = _run_records()[-1]
    assert rec["status"] == "failed"
    assert "artifact gate failed" in rec["error_message"] and "derailed" in rec["error_message"]
    assert rec["rerolled_takes"] == 1
    assert render.classify_incident(rec["error_message"]) == "tts-degeneration"


def test_a_clean_breeze_dry_run_records_zero_rerolls(monkeypatch, tmp_path):
    wd = _stub_render_pipeline(monkeypatch, tmp_path, events=[])
    manifest = {"title": "T", "summary": "s", "tts_engine": "breeze", "segments": [{"text": "hi"}]}
    assert _main(monkeypatch, tmp_path, manifest, wd) == 0
    assert _run_records()[-1]["rerolled_takes"] == 0


def test_a_qwen3_run_never_reads_a_stale_report_and_leaves_the_field_null(monkeypatch, tmp_path):
    stale = [{"segment": 1, "label": "segment 1", "attempt": 2, "final": True,
              "reasons": ["wer"], "transcript": "", "chars": 1}]  # fmt: skip
    wd = _stub_render_pipeline(monkeypatch, tmp_path, events=stale)
    manifest = {"title": "T", "summary": "s", "segments": [{"text": "hi"}]}
    assert _main(monkeypatch, tmp_path, manifest, wd) == 0
    assert _run_records()[-1]["rerolled_takes"] is None
