"""The TTS eval bench (#200): skills/tts-eval/bench.py over the engine registry.

The bench exists so that evaluating a new model is "register the adapter, run one
command, read one report" rather than a throwaway harness. Everything here is
organised around the ways a careless bench stops being that:

  1. it re-implements the TTS call instead of rendering through render.py's own
     adapter, so what it measures is never quite what ships;
  2. a new engine in `render.ENGINES` needs a matching change in the bench;
  3. the derailment rule drifts from the spec's definition;
  4. it compares against a stored baseline instead of re-rendering the control.

The fake engine below "speaks" its script: every take's audio is the model id, the
clip it was cloned from and the text, so the fake analyzers can transcribe and
embed a take without a real model, and a test can make one engine babble.
"""

from __future__ import annotations

import json
import re
import sys
import types
from pathlib import Path

import pytest

import bench
import render
import st_write

ROOT = Path(__file__).resolve().parent.parent
BENCH_DIR = ROOT / "skills" / "tts-eval"
PERSONAS = ("Ryan", "Aiden", "Ethan", "Chelsie")

# --- the fake engine -------------------------------------------------------


class _FakeAudioResult:
    def __init__(self, payload: bytes):
        self.audio = list(payload)


@pytest.fixture
def fake_tts(monkeypatch):
    """tests/test_lines.py's fake mlx_audio / numpy / soundfile / ffmpeg seam, with
    one twist: the fake model's "audio" is the bytes of `<model id>|<clip stem>|<text>`,
    and the fake ffmpeg copies its input to its output. A take's mp3 therefore says
    which model spoke it, in whose voice, and what — which is exactly what the fake
    analyzers need to transcribe and embed it."""
    calls: list[dict] = []
    model_loads: list[str] = []

    class FakeModel:
        def __init__(self, model_id: str):
            self.model_id = model_id

        def generate(self, text, **kw):
            calls.append({"method": "generate", "text": text, **kw})
            stem = Path(kw["ref_audio"]).stem if kw.get("ref_audio") else "design"
            return [_FakeAudioResult(f"{self.model_id}|{stem}|{text}".encode())]

        def generate_voice_design(self, text, **kw):
            calls.append({"method": "generate_voice_design", "text": text, **kw})
            return [_FakeAudioResult(f"{self.model_id}|design|{text}".encode())]

    fake_np = types.ModuleType("numpy")
    fake_np.concatenate = lambda arrs: [x for a in arrs for x in a]
    fake_np.array = lambda x: list(x)
    monkeypatch.setitem(sys.modules, "numpy", fake_np)
    fake_sf = types.ModuleType("soundfile")
    fake_sf.write = lambda path, audio, sr: Path(path).write_bytes(bytes(audio))
    monkeypatch.setitem(sys.modules, "soundfile", fake_sf)
    mlx_audio = types.ModuleType("mlx_audio")
    mlx_tts = types.ModuleType("mlx_audio.tts")
    mlx_utils = types.ModuleType("mlx_audio.tts.utils")

    def _load_model(model_id):
        model_loads.append(model_id)
        return FakeModel(model_id)

    mlx_utils.load_model = _load_model
    monkeypatch.setitem(sys.modules, "mlx_audio", mlx_audio)
    monkeypatch.setitem(sys.modules, "mlx_audio.tts", mlx_tts)
    monkeypatch.setitem(sys.modules, "mlx_audio.tts.utils", mlx_utils)

    def fake_run(cmd, **kw):
        cmd = list(cmd)
        out = Path(cmd[-1])
        src = Path(cmd[cmd.index("-i") + 1])
        # A concat join or the lavfi silence generator has no file input to copy.
        out.write_bytes(src.read_bytes() if src.is_file() else b"\x00")
        return None

    monkeypatch.setattr(render, "run", fake_run)
    return types.SimpleNamespace(calls=calls, model_loads=model_loads)


def _payload(path: Path) -> tuple[str, str, str]:
    model_id, stem, text = path.read_bytes().decode().split("|", 2)
    return model_id, stem, text


@pytest.fixture
def fake_analyzers(monkeypatch):
    """Stand-ins for whisper / the speaker encoder / pyin / the audio profile that
    read the fake engine's payload. `babble` names model ids whose transcripts come
    back as non-ASCII babble — the derailment fixture."""
    babble: set[str] = set()

    def transcribe(path):
        model_id, _, text = _payload(Path(path))
        return "這是 一段 胡言亂語" if model_id in babble else text

    def speaker_embedding(path):
        path = Path(path)
        stem = path.stem if path.parent == bench.REFS_DIR else _payload(path)[1]
        return [1.0 if p.lower() == stem else 0.0 for p in PERSONAS] or [1.0]

    monkeypatch.setattr(bench, "check_bench_deps", lambda: [])
    monkeypatch.setattr(bench, "transcribe", transcribe)
    monkeypatch.setattr(bench, "speaker_embedding", speaker_embedding)
    monkeypatch.setattr(bench, "median_f0", lambda path: 120.0)
    monkeypatch.setattr(
        bench,
        "audio_profile",
        lambda path: {
            "duration_s": 2.0,
            "lead_silence_s": 0.1,
            "trail_silence_s": 0.2,
            "rms_dbfs": -20.0,
        },
    )
    monkeypatch.setattr(bench, "installed_version", lambda dist: "0.0-test")
    return types.SimpleNamespace(babble=babble)


@pytest.fixture
def render_spy(monkeypatch):
    """Record every render_segments call's engine while still rendering."""
    seen: list[str] = []
    real = render.render_segments

    def spy(*args, **kwargs):
        seen.append(kwargs["engine"])
        return real(*args, **kwargs)

    monkeypatch.setattr(render, "render_segments", spy)
    return seen


# --- 1. the bench renders through the registry, never around it -------------


def test_bench_never_calls_mlx_audio_directly():
    """The whole point of #200: re-implementing the generate call means measuring
    something other than what ships. The bench may only reach a model through
    render.render_segments and the registry's own adapter."""
    source = (BENCH_DIR / "bench.py").read_text()
    # Import-shaped patterns, not the bare name: the registry's own `min_mlx_audio`
    # field and the ledger's version key legitimately say "mlx_audio".
    for forbidden in (
        r"\b(import|from)\s+mlx_audio\b",
        r"import_module\(\s*['\"]mlx_audio",
        r"sys\.modules\[\s*['\"]mlx_audio",
        r"\bload_model\b",
        r"\.generate\(",
        r"generate_voice_design",
    ):
        assert not re.search(forbidden, source), f"bench.py must not match {forbidden!r}"
    assert "render.render_segments(" in source
    assert "render.validate_manifest(" in source


def test_engine_choices_come_from_the_registry(monkeypatch):
    parser = bench.build_parser()
    action = next(a for a in parser._actions if a.dest == "engine")
    assert list(action.choices) == list(render.ENGINES)
    with pytest.raises(SystemExit):
        parser.parse_args(["--engine", "nope"])


def test_the_control_is_the_registrys_default_engine():
    """Never a stored baseline: the control is whatever a manifest with no
    tts_engine renders on today, re-rendered on every run."""
    assert bench.CONTROL_ENGINE == render.resolve_tts_engine({})
    assert bench.CONTROL_ENGINE in render.ENGINES


# --- 2. the corpus is fixed, and it fits every registered engine ----------


def test_corpus_has_three_bands_per_cast_voice():
    corpus = bench.load_corpus()
    by_voice: dict[str, dict[str, str]] = {}
    for line in corpus["lines"]:
        by_voice.setdefault(line["speaker"], {})[line["band"]] = line["text"]
        assert line["id"] == f"{line['speaker'].lower()}-{line['band']}"
    assert set(by_voice) == set(PERSONAS)
    for voice, bands in by_voice.items():
        assert list(bands) == ["short", "medium", "long"], voice
        assert len(bands["short"]) < len(bands["medium"]) < len(bands["long"]), voice


def test_corpus_scene_is_eight_turns_over_all_four_voices():
    scene = bench.load_corpus()["scene"]
    assert len(scene) == 8
    assert {t["speaker"] for t in scene} == set(PERSONAS)


def test_corpus_probes_carry_what_they_probe():
    corpus = bench.load_corpus()
    stripped, markers = bench.strip_events(corpus["events"]["text"])
    assert markers == ["laugh", "sigh"]
    assert "(" not in stripped
    assert corpus["events"]["speaker"] in PERSONAS
    assert corpus["direction"]["instruct"].strip() and corpus["direction"]["text"].strip()


def test_corpus_fits_under_every_finite_take_ceiling():
    """A corpus line over an engine's ceiling would be refused by validate_manifest
    on that engine — and a bench that cannot run its whole corpus on a registered
    engine is not a bench of that engine."""
    ceilings = [s.max_take_chars for s in render.ENGINES.values() if s.max_take_chars]
    assert ceilings, "no engine declares a ceiling; this test lost its subject"
    for take in bench.corpus_takes(bench.load_corpus()):
        assert len(take["text"]) <= min(ceilings), take["id"]
        assert take["text"].isascii(), take["id"]
        assert not any(ch.isdigit() for ch in take["text"]), take["id"]


def test_corpus_takes_number_twenty_one_plus_the_direction_probe():
    takes = bench.corpus_takes(bench.load_corpus())
    kinds = [t["kind"] for t in takes]
    assert kinds.count("line") == 12
    assert kinds.count("scene") == 8
    assert kinds.count("events") == 1
    assert kinds.count("direction") == 1
    assert len({t["id"] for t in takes}) == len(takes)


def test_the_cast_is_surface_tensions_own():
    """The clone corpus is the production cast, clip for clip and transcript for
    transcript — a bench on different clips would measure a different show."""
    assert bench.cast_map() == st_write.cast_map()


# --- 3. the metric arithmetic is the spec's ---------------------------------


@pytest.mark.parametrize(
    "ref, hyp, wer",
    [
        ("the cat sat on the mat", "The cat sat on the mat.", 0.0),
        ("the cat sat on the mat", "the cat sat on a mat", 1 / 6),
        ("the cat sat on the mat", "the cat on the mat", 1 / 6),
        ("the cat sat on the mat", "", 1.0),
        ("one two three four", "one two three four five", 0.25),
    ],
)
def test_word_error_rate(ref, hyp, wer):
    assert bench.word_error_rate(ref, hyp) == pytest.approx(wer)


def test_derailment_rule_is_the_specs_definition():
    script = "I want to be precise about what he measured. The number is startup time."
    assert bench.derailment(script, script) == []
    # A skipped clause: WER over the threshold AND the word ratio under 0.9.
    reasons = bench.derailment(script, "I want to be precise about what he measured.")
    assert "wer" in reasons and "word-ratio" in reasons
    # A hallucinated clause: more words than the script.
    reasons = bench.derailment(script, script + " Download this album, with my permission.")
    assert "word-ratio" in reasons
    # One wrong word in fourteen is under the WER threshold and inside the ratio band.
    assert bench.derailment(script, script.replace("startup", "start-up")) == []


def test_multilingual_babble_is_flagged_on_non_ascii_alone():
    """The 2026-09-04 failure shape: whisper transcribes a derailed take as words
    in another script. The non-ASCII test catches it even when the word count
    happens to land inside the ratio band."""
    script = "one two three four five six seven eight nine ten"
    transcript = "一 二 三 四 五 六 七 八 九 十"
    reasons = bench.derailment(script, transcript)
    assert "non-ascii" in reasons
    assert bench.DERAIL_WER == 0.15
    assert bench.DERAIL_WORD_RATIO == (0.9, 1.1)


def test_strip_events_removes_markers_and_names_them():
    assert bench.strip_events("Okay. (laugh) Right. (sigh) Sure.") == (
        "Okay. Right. Sure.",
        ["laugh", "sigh"],
    )
    assert bench.strip_events("no markers here") == ("no markers here", [])


def test_nearest_clip_picks_the_closest_embedding():
    clips = {"Ryan": [1.0, 0.0], "Aiden": [0.0, 1.0]}
    assert bench.nearest_clip([0.9, 0.1], clips) == ("Ryan", pytest.approx(0.9939, abs=1e-3))
    assert bench.cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert bench.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_check_bench_deps_names_the_pip_packages(monkeypatch):
    import importlib.util

    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: None if name == "resemblyzer" else object(),
    )
    assert bench.check_bench_deps() == ["resemblyzer"]


# --- 4. the whole bench, on the fake engine ---------------------------------


def test_bench_renders_candidate_and_control_and_writes_both_artifacts(
    tmp_path, fake_tts, fake_analyzers, render_spy
):
    entry = bench.run_bench("breeze", workdir=tmp_path / "wd", keep_workdir=True)

    # The candidate and the control both rendered, through render_segments, once
    # per pass (clone corpus, then the direction probe).
    assert render_spy == ["breeze", "breeze", "qwen3", "qwen3"]
    assert sorted(entry["engines"]) == ["breeze", "qwen3"]
    assert entry["candidate"] == "breeze" and entry["control"] == "qwen3"
    # Every model the fake loaded was one the registry names.
    loaded = set(fake_tts.model_loads)
    assert loaded == {
        render.ENGINES["breeze"].base_model_id,
        render.ENGINES["qwen3"].base_model_id,
        render.ENGINES["qwen3"].design_model_id,
    }

    ledger = Path(entry["ledger_path"])
    assert ledger.parent == render.CONFIG_DIR / "evals"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-breeze\.json", ledger.name)
    on_disk = json.loads(ledger.read_text())
    assert on_disk["candidate"] == "breeze"
    for name in ("breeze", "qwen3"):
        rows = on_disk["engines"][name]["takes"]
        assert len(rows) == 22
        assert on_disk["engines"][name]["license"] == render.ENGINES[name].license
        assert all(r["wer"] == 0.0 for r in rows if r["kind"] != "events")
        events = next(r for r in rows if r["kind"] == "events")
        if render.ENGINES[name].has("events"):
            # The fake engine reads markers aloud, so on the engine that is HANDED
            # them the events take alone carries two inserted words — and names them.
            assert 0 < events["wer"] < bench.DERAIL_WER
            assert events["markers_heard"] == ["laugh", "sigh"]
        else:
            # render.py strips the markers before an engine without `events` sees
            # them (#201), so the probe measures the take a show would ship — which
            # has none to hear. The bench renders through the registry, never
            # around it, and this is that invariant showing.
            assert events["wer"] == 0.0 and events["markers_heard"] == []
        assert all(r["derailed"] is False for r in rows)
        # Every clone take was cloned from its own clip and recognised as it.
        clone_rows = [r for r in rows if r["kind"] != "direction"]
        # Plain tolerance rather than pytest.approx: the fake numpy module in
        # sys.modules confuses approx's ndarray probe.
        assert all(abs(r["similarity"] - 1.0) < 1e-9 for r in clone_rows)
        assert all(r["nearest_ok"] for r in clone_rows)

    report = Path(entry["report_path"])
    assert report.parent == ledger.parent and report.suffix == ".html"
    html = report.read_text()
    assert "data:audio/mpeg;base64," in html
    for name in ("breeze", "qwen3"):
        assert render.ENGINES[name].label in html
        assert render.ENGINES[name].license in html
    assert "<script" not in html.lower() or "src=" not in html.lower()


def test_a_derailed_take_is_flagged_in_the_ledger_and_the_report(
    tmp_path, fake_tts, fake_analyzers
):
    fake_analyzers.babble.add(render.ENGINES["breeze"].base_model_id)
    entry = bench.run_bench("breeze", workdir=tmp_path / "wd", keep_workdir=True)

    breeze = entry["engines"]["breeze"]
    # Breeze designs on its base model, so the direction probe babbles too: every
    # measured take is flagged.
    assert all(r["derailed"] for r in breeze["takes"])
    assert breeze["summary"]["derailed"] == breeze["summary"]["measured"] == 22
    reasons = breeze["takes"][0]["derail_reasons"]
    assert "non-ascii" in reasons and "wer" in reasons
    assert entry["engines"]["qwen3"]["summary"]["derailed"] == 0
    html = Path(entry["report_path"]).read_text()
    assert "derailed" in html
    assert "non-ascii" in html


def test_a_third_engine_in_the_registry_is_benchable_with_no_bench_change(
    tmp_path, fake_tts, fake_analyzers, render_spy, monkeypatch
):
    """Acceptance: adding an engine to ENGINES (with its adapter, which is the
    registry's own job) makes `--engine <name>` work. The bench reads the table."""
    qwen3 = render.ENGINES["qwen3"]
    third = render.EngineSpec(
        name="third",
        label="Third Model 1B",
        base_model_id="mlx-community/Third-1B",
        design_model_id=None,
        capabilities=frozenset({"clone", "design"}),
        presets=(),
        max_take_chars=None,
        max_tokens=None,
        min_mlx_audio=qwen3.min_mlx_audio,
        license="MIT",
    )
    monkeypatch.setattr(render, "ENGINES", {**render.ENGINES, "third": third})
    monkeypatch.setattr(render, "TTS_ENGINES", (*render.TTS_ENGINES, "third"))
    monkeypatch.setattr(
        render, "_ENGINE_GENERATORS", {**render._ENGINE_GENERATORS, "third": render._generate_qwen3}
    )

    rc = bench.main(["--engine", "third", "--workdir", str(tmp_path / "wd"), "--keep-workdir"])
    assert rc == 0
    assert render_spy[:2] == ["third", "third"]
    ledgers = list((render.CONFIG_DIR / "evals").glob("*-third.json"))
    assert len(ledgers) == 1
    entry = json.loads(ledgers[0].read_text())
    assert entry["engines"]["third"]["license"] == "MIT"
    assert "mlx-community/Third-1B" in Path(entry["report_path"]).read_text()


def test_an_engine_without_clone_is_refused_by_the_registrys_own_validation(
    tmp_path, fake_tts, fake_analyzers, monkeypatch
):
    """The bench's manifests go through validate_manifest, so an engine that cannot
    clone the cast is refused the way a show on it would be — not rendered wrong."""
    preset_only = render.EngineSpec(
        name="presetonly",
        label="Preset Only",
        base_model_id="x/preset-only",
        design_model_id=None,
        capabilities=frozenset({"preset"}),
        presets=("A",),
        max_take_chars=None,
        max_tokens=None,
        min_mlx_audio="0.0.0",
        license="MIT",
    )
    monkeypatch.setattr(render, "ENGINES", {**render.ENGINES, "presetonly": preset_only})
    monkeypatch.setattr(render, "TTS_ENGINES", (*render.TTS_ENGINES, "presetonly"))
    with pytest.raises(SystemExit):
        bench.run_bench("presetonly", workdir=tmp_path / "wd", keep_workdir=True)


def test_benching_the_control_itself_renders_it_once(
    tmp_path, fake_tts, fake_analyzers, render_spy
):
    entry = bench.run_bench(bench.CONTROL_ENGINE, workdir=tmp_path / "wd", keep_workdir=True)
    assert render_spy == [bench.CONTROL_ENGINE, bench.CONTROL_ENGINE]
    assert list(entry["engines"]) == [bench.CONTROL_ENGINE]


def test_a_second_run_on_the_same_day_never_overwrites_the_ledger(
    tmp_path, fake_tts, fake_analyzers
):
    first = bench.run_bench("breeze", workdir=tmp_path / "a", keep_workdir=True)
    second = bench.run_bench("breeze", workdir=tmp_path / "b", keep_workdir=True)
    assert first["ledger_path"] != second["ledger_path"]
    assert Path(first["ledger_path"]).exists() and Path(second["ledger_path"]).exists()
    assert Path(second["ledger_path"]).name.endswith("-breeze-2.json")


def test_the_workdir_is_removed_on_success_unless_kept(tmp_path, fake_tts, fake_analyzers):
    wd = tmp_path / "gone"
    bench.run_bench("breeze", workdir=wd)
    assert not wd.exists()
    kept = tmp_path / "kept"
    bench.run_bench("breeze", workdir=kept, keep_workdir=True)
    assert kept.exists()


def test_missing_bench_deps_fail_before_any_render(tmp_path, fake_tts, monkeypatch, render_spy):
    monkeypatch.setattr(bench, "check_bench_deps", lambda: ["librosa"])
    with pytest.raises(SystemExit):
        bench.run_bench("breeze", workdir=tmp_path / "wd")
    assert render_spy == []


def test_report_escapes_model_output(tmp_path, fake_tts, fake_analyzers, monkeypatch):
    """Transcripts are model output; the report is HTML. Whisper has emitted
    angle brackets before, and a report that trusts them is an XSS on the
    operator's own machine."""
    monkeypatch.setattr(bench, "transcribe", lambda path: "<script>alert(1)</script>")
    entry = bench.run_bench("breeze", workdir=tmp_path / "wd", keep_workdir=True)
    html = Path(entry["report_path"]).read_text()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# --- 5. the docs are pinned to the code ---------------------------------------


def _skill_text() -> str:
    return (BENCH_DIR / "SKILL.md").read_text()


def _first_table_after(marker: str) -> list[list[str]]:
    lines = _skill_text().splitlines()
    start = next((i for i, ln in enumerate(lines) if marker in ln), None)
    assert start is not None, f"SKILL.md lost the {marker!r} section"
    i = start + 1
    while i < len(lines) and not lines[i].lstrip().startswith("|"):
        i += 1
    assert i < len(lines), f"SKILL.md has no table after {marker!r}"
    rows = []
    while i < len(lines) and lines[i].lstrip().startswith("|"):
        cells = [c.strip().strip("`").strip() for c in lines[i].strip().strip("|").split("|")]
        rows.append(cells)
        i += 1
    return rows[2:]


def test_skill_md_has_frontmatter_naming_the_skill():
    head = _skill_text().splitlines()[:6]
    assert head[0] == "---"
    assert "name: tts-eval" in head
    assert any(ln.startswith("description:") for ln in head)


def test_skill_md_corpus_table_matches_the_corpus():
    rows = _first_table_after("### The corpus")
    takes = bench.corpus_takes(bench.load_corpus())
    counts = {kind: sum(1 for t in takes if t["kind"] == kind) for kind in bench.TAKE_KINDS}
    assert [r[0] for r in rows] == list(bench.TAKE_KINDS), "corpus table rows drifted"
    for row in rows:
        assert row[1] == str(counts[row[0]]), f"take count for {row[0]} drifted"


def test_skill_md_metrics_table_matches_the_code():
    rows = _first_table_after("### The metrics")
    assert [r[0] for r in rows] == [m.key for m in bench.METRICS], (
        "the metrics table drifted from bench.METRICS (a missing OR phantom row)"
    )
    for row, metric in zip(rows, bench.METRICS, strict=True):
        assert row[1] == metric.label, f"label for {metric.key} drifted"


def test_skill_md_documents_the_ledger_path_and_the_derailment_rule():
    text = _skill_text()
    assert "~/.config/daily-podcast/evals/<date>-<engine>.json" in text
    assert f"WER > {bench.DERAIL_WER}" in text
    lo, hi = bench.DERAIL_WORD_RATIO
    assert f"{lo}-{hi}" in text or f"{lo}–{hi}" in text
    assert "non-ASCII" in text
    assert bench.WHISPER_MODEL in text


def test_skill_md_names_the_control_and_the_license_column():
    text = _skill_text()
    assert "re-render" in text.lower()
    assert "license" in text.lower()
    assert "stored baseline" in text.lower() or "never compare" in text.lower()


def test_pyproject_keeps_the_bench_deps_out_of_the_runtime_deps():
    text = (ROOT / "pyproject.toml").read_text()
    runtime = text[text.index("dependencies = [") : text.index("[project.optional-dependencies]")]
    extras = text[text.index("[project.optional-dependencies]") : text.index("[project.urls]")]
    bench_block = extras[extras.index("bench = [") : extras.index("dev = [")]
    for dist in ("mlx-whisper", "librosa", "resemblyzer"):
        assert f'"{dist}"' in bench_block, f"{dist} missing from the bench extra"
        assert f'"{dist}"' not in runtime, f"{dist} leaked into the runtime deps"
    assert [d for _, d in bench.BENCH_DEPS] == ["mlx-whisper", "librosa", "resemblyzer"]


def test_pkg_resources_stub_fills_only_a_gap(monkeypatch):
    """webrtcvad's import-time pkg_resources call, on a setuptools that no longer
    ships pkg_resources. The stub must appear only when the real thing is absent,
    and must answer the one call webrtcvad makes."""
    import importlib.metadata
    import importlib.util

    monkeypatch.delitem(sys.modules, "pkg_resources", raising=False)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    bench._ensure_pkg_resources()
    assert "pkg_resources" not in sys.modules  # a real one is importable: untouched

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    bench._ensure_pkg_resources()
    stub = sys.modules["pkg_resources"]
    assert stub.get_distribution("pytest").version == importlib.metadata.version("pytest")


def test_the_bench_renders_with_the_derailment_detector_off(monkeypatch, tmp_path):
    """The bench measures the engine's RAW derailment rate (#202): render.py's own
    detector re-rolls a derailed take, which would hide exactly what the `derailed`
    metric exists to count. The kwarg must be explicit, not the engine's default."""
    seen: dict = {}

    def fake_render_segments(segments, voice, workdir, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(render, "render_segments", fake_render_segments)
    manifest = {"title": "T", "summary": "S", "tts_engine": "breeze", "segments": [{"text": "hi"}]}
    bench.render_pass(manifest, tmp_path / "wd", engine="breeze")
    assert seen["detect_derailment"] is False
