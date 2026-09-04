"""TTS eval bench over the engine registry (#200).

    python3 skills/tts-eval/bench.py --engine breeze

Renders a fixed corpus (corpus.json: three lines per Surface Tension cast voice, one
eight-turn four-voice scene, a vocal-events probe, a direction probe) on the named
engine AND on the production engine as the control, measures a fixed metric set on
every take, appends one dated ledger entry under the user's state dir, and writes
one self-contained HTML report with side-by-side players.

Two things are load-bearing.

The bench renders through `render.validate_manifest` and `render.render_segments`
with the engine named on the manifest — the registry's own adapter, never a
re-implementation of the TTS call. What it measures is therefore exactly what would
ship: the same validation (an engine that cannot clone the cast is refused here the
way a show on it would be), the same text prep, the same mono-44.1k encode. Timing is
taken by wrapping `render._render_take` at the module seam for the duration of a
pass, which is instrumentation, not a second render path. A test greps this file for
the TTS package's import name and fails on any mention.

The control is `render.resolve_tts_engine({})` — whatever a manifest with no
`tts_engine` renders on today — and it is RE-RENDERED on every run. A stored
baseline would drift under every mlx-audio upgrade and quantization swap while
still looking like a number; a control rendered beside the candidate, on the same
install, on the same day, is the only comparison that means anything.

Analysis deps (whisper, the speaker encoder, pyin) are the `bench` extra in
pyproject.toml and are imported function-locally, so `import bench` needs none of
them and a run fails on a missing one BEFORE it spends twenty minutes rendering.
Nothing in a show's run calls this module (the bloopers.py precedent).
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import functools
import hashlib
import html
import importlib.metadata
import importlib.util
import json
import math
import re
import shutil
import statistics
import sys
import tempfile
import time
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "daily-podcast"))

import render  # noqa: E402  (must follow the sys.path insert above)

SCRIPT_DIR = Path(__file__).resolve().parent
CORPUS_PATH = SCRIPT_DIR / "corpus.json"
# The clone corpus IS the production cast: Surface Tension's four recorded clips,
# transcript beside each. A test pins cast_map() to st_write.cast_map().
REFS_DIR = SCRIPT_DIR.parent / "surface-tension" / "refs"

# Never a stored baseline (module docstring). Resolved from the registry so that if
# the production default ever moves, the control moves with it.
CONTROL_ENGINE = render.resolve_tts_engine({})

WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"
# The derailment rule from the registry spec ("Measurements that shape the design"):
# WER over the threshold, any non-ASCII in the transcript, or a heard-to-script word
# ratio outside the band. All three were needed on 2026-09-04 — the failures were
# multilingual babble, a hallucinated clause and a skipped clause, and the
# speech-rate gate saw none of them.
DERAIL_WER = 0.15
DERAIL_WORD_RATIO = (0.9, 1.1)
ANALYSIS_SAMPLE_RATE = 16_000
SILENCE_DBFS = -40.0  # a sample over this is "speech" for the lead/trail measurement
F0_RANGE_HZ = (60.0, 400.0)

# (import name, pip distribution). Checked with find_spec before any render.
BENCH_DEPS = (
    ("mlx_whisper", "mlx-whisper"),
    ("librosa", "librosa"),
    ("resemblyzer", "resemblyzer"),
)

TAKE_KINDS = ("line", "scene", "events", "direction")
WORKDIR_PREFIX = "tts-eval-"
EVENT_MARKER_RE = re.compile(r"\(([a-z]+)\)")
_WORD_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)*")


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    description: str


# The fixed metric set. SKILL.md's metrics table is pinned to this tuple by a test;
# a metric that exists only here never reaches the operator, and one documented but
# not measured is a column of dashes.
METRICS = (
    Metric("wer", "WER", "word error rate of the whisper transcript against the script"),
    Metric(
        "similarity",
        "speaker similarity",
        "cosine similarity of the take's speaker embedding to its own clip's",
    ),
    Metric("nearest", "nearest clip", "the cast clip the take is closest to; ok when its own"),
    Metric("f0_hz", "median f0", "median voiced pitch (pyin), beside the clip's own"),
    Metric(
        "chars_per_s",
        "chars/s",
        "script characters per second of audio, beside the clip's own (tempo ratio)",
    ),
    Metric("lead_silence_s", "leading silence", "seconds before the first sample over -40 dBFS"),
    Metric("trail_silence_s", "trailing silence", "seconds after the last sample over -40 dBFS"),
    Metric("rms_dbfs", "RMS", "overall level in dBFS"),
    Metric("x_realtime", "x realtime", "seconds of audio per second of render, per take"),
    Metric("load_s", "load time", "seconds from the pass start to the first take, per pass"),
    Metric("peak_memory_bytes", "peak memory", "MLX peak memory during the pass, when measurable"),
    Metric(
        "markers_heard",
        "markers heard",
        "event marker words present in the events probe's transcript (read, not performed)",
    ),
    Metric(
        "derailed",
        "derailed",
        "WER > 0.15, non-ASCII in the transcript, or heard/script word ratio outside 0.9-1.1",
    ),
)


# --- corpus ------------------------------------------------------------------


def load_corpus() -> dict[str, Any]:
    return json.loads(CORPUS_PATH.read_text())


def corpus_sha256() -> str:
    return hashlib.sha256(CORPUS_PATH.read_bytes()).hexdigest()


def personas(corpus: dict[str, Any]) -> list[str]:
    """The cast the corpus names, in order of first appearance."""
    seen: list[str] = []
    for line in corpus["lines"]:
        if line["speaker"] not in seen:
            seen.append(line["speaker"])
    return seen


def cast_map(corpus: dict[str, Any] | None = None) -> dict[str, dict[str, str]]:
    """persona -> {ref_audio, ref_text} from the Surface Tension refs dir, the shape
    st_write.cast_map builds for the show itself."""
    cast: dict[str, dict[str, str]] = {}
    for persona in personas(corpus or load_corpus()):
        clip = REFS_DIR / f"{persona.lower()}.wav"
        transcript = clip.with_suffix(".txt")
        for path in (clip, transcript):
            if not path.is_file():
                render.die(f"cast clip for {persona!r} is missing: {path}")
        cast[persona] = {"ref_audio": str(clip), "ref_text": transcript.read_text().strip()}
    return cast


def strip_events(text: str) -> tuple[str, list[str]]:
    """Remove `(laugh)`-style markers and name them. The stripped text is what WER
    is measured against: an engine that performs the marker says no word for it, and
    one that reads it aloud is caught by `markers_heard` rather than by WER alone."""
    markers = EVENT_MARKER_RE.findall(text)
    stripped = EVENT_MARKER_RE.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", stripped).strip(), markers


def corpus_takes(corpus: dict[str, Any]) -> list[dict[str, Any]]:
    """Every take the bench renders, in render order, with the `script` WER measures
    against (the events probe's is marker-stripped)."""
    takes: list[dict[str, Any]] = []
    for line in corpus["lines"]:
        takes.append(
            {
                "id": line["id"],
                "kind": "line",
                "speaker": line["speaker"],
                "band": line["band"],
                "text": line["text"],
                "script": line["text"],
                "markers": [],
            }
        )
    for j, turn in enumerate(corpus["scene"], start=1):
        takes.append(
            {
                "id": f"scene-{j:02d}",
                "kind": "scene",
                "speaker": turn["speaker"],
                "band": None,
                "text": turn["text"],
                "script": turn["text"],
                "markers": [],
            }
        )
    events = corpus["events"]
    script, markers = strip_events(events["text"])
    takes.append(
        {
            "id": "events",
            "kind": "events",
            "speaker": events["speaker"],
            "band": None,
            "text": events["text"],
            "script": script,
            "markers": markers,
        }
    )
    direction = corpus["direction"]
    takes.append(
        {
            "id": "direction",
            "kind": "direction",
            "speaker": None,
            "band": None,
            "text": direction["text"],
            "script": direction["text"],
            "markers": [],
            "instruct": direction["instruct"],
        }
    )
    return takes


# --- manifests: the bench speaks to the renderer in its own contract ----------


def build_clone_manifest(
    engine: str, takes: list[dict[str, Any]], cast: dict[str, dict[str, str]]
) -> tuple[dict[str, Any], list[tuple[int, int]]]:
    """The clone pass: every non-direction take as a `lines` scene over the cast —
    one single-line scene per corpus line and per events probe, the eight-turn scene
    as one segment — exactly how Surface Tension renders. Returns the manifest and,
    aligned with the clone takes, each take's (segment, line) placement."""
    segments: list[dict[str, Any]] = []
    placement: list[tuple[int, int]] = []
    scene_lines: list[dict[str, str]] = []
    for take in takes:
        if take["kind"] == "direction":
            continue
        if take["kind"] == "scene":
            scene_lines.append({"speaker": take["speaker"], "text": take["text"]})
            continue
        if scene_lines:
            segments.append({"lines": scene_lines})
            scene_lines = []
        segments.append({"lines": [{"speaker": take["speaker"], "text": take["text"]}]})
    if scene_lines:
        segments.append({"lines": scene_lines})
    # Placement mirrors the loop above so a later probe kind can't misalign it.
    i = 0
    j_scene = 0
    for take in takes:
        if take["kind"] == "direction":
            continue
        if take["kind"] == "scene":
            if j_scene == 0:
                i += 1
            j_scene += 1
            placement.append((i, j_scene))
            continue
        j_scene = 0
        i += 1
        placement.append((i, 1))
    manifest = {
        "title": f"tts-eval {engine}",
        "summary": "TTS eval bench: the fixed clone corpus on one engine.",
        "tts_engine": engine,
        # The episode voice is only the fallback a plain-text segment renders with,
        # and every segment here is a scene — but the engine's capabilities are
        # validated against it regardless, as they would be for a show.
        "voice": "house",
        "cast": cast,
        "segments": segments,
    }
    return manifest, placement


def build_design_manifest(engine: str, take: dict[str, Any]) -> dict[str, Any]:
    """The direction probe: the instruct path the registry exposes today, which is
    `voice_instruct` (VoiceDesign on qwen3, `instruct=` on the base model on breeze).
    Per-line direction over a clone is #201 and will move this probe when it lands."""
    return {
        "title": f"tts-eval {engine} direction",
        "summary": "TTS eval bench: the direction probe on one engine.",
        "tts_engine": engine,
        "voice": "custom",
        "voice_instruct": take["instruct"],
        "segments": [{"text": take["text"]}],
    }


# --- rendering through the registry --------------------------------------------


class _NoMemoryProbe:
    def reset(self) -> None:
        return None

    def read(self) -> int | None:
        return None


def peak_memory_probe() -> Any:
    """MLX's own peak-memory counter when mlx.core is importable (the engines need it
    anyway), else a probe that reads None. ru_maxrss does not see Metal allocations,
    so there is no useful fallback."""
    try:
        import gc

        import mlx.core as mx
    except ImportError:
        return _NoMemoryProbe()

    class _Probe:
        def reset(self) -> None:
            gc.collect()
            mx.clear_cache()
            mx.reset_peak_memory()

        def read(self) -> int | None:
            return int(mx.get_peak_memory())

    return _Probe()


def render_pass(manifest: dict[str, Any], workdir: Path, *, engine: str) -> dict[str, Any]:
    """Validate and render one manifest through render.py, timing each take.

    The wrapper around `_render_take` is the whole instrumentation: it records the
    mp3, the audio seconds the adapter reports and the wall seconds it took, and the
    pass's load time is the gap from the pass start to the first take (the model load
    plus the cheap pass-1 planning). Restored in `finally` so a failed pass leaves
    render.py as it found it."""
    render.validate_manifest(manifest)
    voice, voice_instruct, ref_audio, ref_text = render.resolve_voice(manifest)
    workdir.mkdir(parents=True, exist_ok=True)
    calls: list[dict[str, Any]] = []
    first_take: list[float] = []
    original = render._render_take

    def timed(model: Any, **kw: Any) -> float:
        t0 = time.perf_counter()
        if not first_take:
            first_take.append(t0)
        audio_s = original(model, **kw)
        calls.append(
            {
                "mp3": Path(kw["mp3"]),
                "text": kw["text"],
                "audio_s": float(audio_s),
                "render_s": time.perf_counter() - t0,
            }
        )
        return audio_s

    probe = peak_memory_probe()
    probe.reset()
    t_start = time.perf_counter()
    render._render_take = timed
    try:
        seg_paths = render.render_segments(
            manifest["segments"],
            voice,
            workdir,
            voice_instruct=voice_instruct,
            ref_audio=ref_audio,
            ref_text=ref_text,
            raw_text=manifest.get("raw_text", False),
            cast=manifest.get("cast"),
            engine=engine,
        )
    finally:
        render._render_take = original
    wall_s = time.perf_counter() - t_start
    return {
        "seg_paths": seg_paths,
        "calls": calls,
        "load_s": (first_take[0] - t_start) if first_take else None,
        "wall_s": wall_s,
        "peak_memory_bytes": probe.read(),
    }


def render_engine(
    engine: str,
    takes: list[dict[str, Any]],
    cast: dict[str, dict[str, str]],
    workdir: Path,
) -> dict[str, Any]:
    """Both passes for one engine. Returns per-take audio paths and timings plus the
    per-pass load/memory figures; analysis happens later so whisper's own MLX memory
    never lands inside a render pass's peak."""
    spec = render.ENGINES[engine]
    render.log(f"=== {engine} ({spec.label}; {spec.license}) ===")
    clone_takes = [t for t in takes if t["kind"] != "direction"]
    manifest, placement = build_clone_manifest(engine, takes, cast)
    clone = render_pass(manifest, workdir, engine=engine)
    by_mp3 = {c["mp3"]: c for c in clone["calls"]}
    audio: dict[str, dict[str, Any]] = {}
    scene_segment: int | None = None
    for take, (i, j) in zip(clone_takes, placement, strict=True):
        mp3 = workdir / f"line_{i:02d}_{j:02d}.mp3"
        call = by_mp3.get(mp3)
        if call is None:
            render.die(
                f"{engine}: no render recorded for {mp3.name} — a cached take in a "
                "bench workdir; benches render fresh, use a new --workdir"
            )
        audio[take["id"]] = {"mp3": mp3, "audio_s": call["audio_s"], "render_s": call["render_s"]}
        if take["kind"] == "scene":
            scene_segment = i
    passes = {
        "clone": {
            "model_id": spec.base_model_id,
            "load_s": clone["load_s"],
            "wall_s": clone["wall_s"],
            "peak_memory_bytes": clone["peak_memory_bytes"],
        }
    }
    scene_mp3 = clone["seg_paths"][scene_segment - 1] if scene_segment else None

    direction = next(t for t in takes if t["kind"] == "direction")
    if spec.has("design"):
        design = render_pass(
            build_design_manifest(engine, direction), workdir / "design", engine=engine
        )
        call = design["calls"][0]
        audio[direction["id"]] = {
            "mp3": call["mp3"],
            "audio_s": call["audio_s"],
            "render_s": call["render_s"],
        }
        passes["design"] = {
            "model_id": spec.design_model_id or spec.base_model_id,
            "load_s": design["load_s"],
            "wall_s": design["wall_s"],
            "peak_memory_bytes": design["peak_memory_bytes"],
        }
    else:
        render.log(f"{engine}: no design capability; the direction probe is not rendered")
    return {"audio": audio, "passes": passes, "scene_mp3": scene_mp3}


# --- metrics: pure arithmetic, no deps -----------------------------------------


def normalize_words(text: str) -> list[str]:
    return [w.strip("'") for w in _WORD_RE.findall(text.lower()) if w.strip("'")]


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein distance over normalized words, divided by the reference length.
    Uncapped above 1.0 (insertions can exceed the script), like jiwer."""
    ref = normalize_words(reference)
    hyp = normalize_words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        cur = [i]
        for j, h in enumerate(hyp, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(ref)


def _ascii_fold(text: str) -> str:
    """Typography is not derailment: fold the smart quotes and dashes whisper
    sometimes emits before asking whether the transcript left the Latin script."""
    return render.normalize_for_tts(text).replace("…", "...")


def derailment(script: str, transcript: str) -> list[str]:
    """The spec's rule, as reasons; empty means clean."""
    reasons: list[str] = []
    if word_error_rate(script, transcript) > DERAIL_WER:
        reasons.append("wer")
    if not _ascii_fold(transcript).isascii():
        reasons.append("non-ascii")
    ref = normalize_words(script)
    hyp = normalize_words(transcript)
    lo, hi = DERAIL_WORD_RATIO
    ratio = len(hyp) / len(ref) if ref else None
    if ratio is None or not lo <= ratio <= hi:
        reasons.append("word-ratio")
    return reasons


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def nearest_clip(
    embedding: list[float], clips: dict[str, list[float]]
) -> tuple[str | None, float | None]:
    best: tuple[str | None, float | None] = (None, None)
    for name, clip in clips.items():
        score = cosine(embedding, clip)
        if best[1] is None or score > best[1]:
            best = (name, score)
    return best


# --- analyzers: the only functions that touch the bench deps ------------------


def check_bench_deps() -> list[str]:
    """pip names of the missing analysis deps. A find_spec probe, not an import:
    whisper and the encoder cost seconds to import and this runs before a render."""
    return [dist for mod, dist in BENCH_DEPS if importlib.util.find_spec(mod) is None]


def installed_version(dist: str) -> str | None:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return None


@functools.lru_cache(maxsize=128)
def _decode_16k(path: str) -> Any:
    """One canonical decode per file — mono float32 at 16 kHz — through ffmpeg, so
    every analyzer hears the same samples whether the source is a take's mp3 or a
    cast clip's wav. Cached because four analyzers read each take."""
    import numpy as np
    import soundfile as sf

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "analysis.wav"
        render.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                path,
                "-ar",
                str(ANALYSIS_SAMPLE_RATE),
                "-ac",
                "1",
                "-f",
                "wav",
                str(wav),
            ]
        )
        audio, _ = sf.read(wav, dtype="float32")
    return np.asarray(audio, dtype="float32")


def transcribe(path: Path) -> str:
    import mlx_whisper

    result = mlx_whisper.transcribe(
        _decode_16k(str(path)), path_or_hf_repo=WHISPER_MODEL, verbose=False
    )
    return str(result.get("text", "")).strip()


def _ensure_pkg_resources() -> None:
    """webrtcvad 2.0.10 — resemblyzer's VAD, and the version it pins — runs
    `pkg_resources.get_distribution('webrtcvad').version` at import time, and
    setuptools 81+ no longer ships pkg_resources at all. The first real bench run
    rendered both engines for seven minutes and then died on that import. A stub
    carrying exactly that one call keeps the import alive; nothing in the bench or
    its deps reads anything else from it. (webrtcvad-wheels fixed this upstream,
    but resemblyzer still pins the original, and listing both would race on which
    `webrtcvad.py` pip wrote last.)"""
    if "pkg_resources" in sys.modules or importlib.util.find_spec("pkg_resources"):
        return
    stub = types.ModuleType("pkg_resources")
    stub.get_distribution = lambda name: types.SimpleNamespace(
        version=importlib.metadata.version(name)
    )
    sys.modules["pkg_resources"] = stub


@functools.lru_cache(maxsize=1)
def _voice_encoder() -> Any:
    _ensure_pkg_resources()
    from resemblyzer import VoiceEncoder

    return VoiceEncoder(verbose=False)


def speaker_embedding(path: Path) -> list[float]:
    _ensure_pkg_resources()
    from resemblyzer import preprocess_wav

    wav = preprocess_wav(_decode_16k(str(path)), source_sr=ANALYSIS_SAMPLE_RATE)
    return [float(x) for x in _voice_encoder().embed_utterance(wav)]


def median_f0(path: Path) -> float | None:
    import librosa
    import numpy as np

    fmin, fmax = F0_RANGE_HZ
    f0, voiced, _ = librosa.pyin(
        _decode_16k(str(path)), fmin=fmin, fmax=fmax, sr=ANALYSIS_SAMPLE_RATE
    )
    values = f0[voiced & ~np.isnan(f0)]
    return float(np.median(values)) if len(values) else None


def audio_profile(path: Path) -> dict[str, float | None]:
    """Duration, leading/trailing silence against SILENCE_DBFS, and RMS in dBFS."""
    import numpy as np

    audio = _decode_16k(str(path))
    n = int(len(audio))
    sr = ANALYSIS_SAMPLE_RATE
    duration = n / sr
    if n == 0:
        return {"duration_s": 0.0, "lead_silence_s": 0.0, "trail_silence_s": 0.0, "rms_dbfs": None}
    above = np.flatnonzero(np.abs(audio) > 10 ** (SILENCE_DBFS / 20))
    lead = float(above[0]) / sr if len(above) else duration
    trail = float(n - 1 - above[-1]) / sr if len(above) else duration
    rms = float(np.sqrt(np.mean(np.square(audio, dtype="float64"))))
    dbfs = 20 * math.log10(rms) if rms > 0 else None
    return {
        "duration_s": duration,
        "lead_silence_s": lead,
        "trail_silence_s": trail,
        "rms_dbfs": dbfs if dbfs is not None and math.isfinite(dbfs) else None,
    }


# --- analysis ------------------------------------------------------------------


def analyze_clips(cast: dict[str, dict[str, str]]) -> dict[str, dict[str, Any]]:
    """Each cast clip's own embedding, f0 and tempo — the reference every clone take
    is measured against."""
    stats: dict[str, dict[str, Any]] = {}
    for persona, entry in cast.items():
        clip = Path(entry["ref_audio"])
        profile = audio_profile(clip)
        duration = profile["duration_s"] or 0.0
        stats[persona] = {
            "embedding": speaker_embedding(clip),
            "f0_hz": median_f0(clip),
            "chars_per_s": len(entry["ref_text"]) / duration if duration > 0 else None,
        }
    return stats


def analyze_take(
    take: dict[str, Any], rendered: dict[str, Any] | None, clip_stats: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": take["id"],
        "kind": take["kind"],
        "speaker": take["speaker"],
        "band": take["band"],
        "chars": len(take["script"]),
        "words": len(normalize_words(take["script"])),
        "markers": list(take["markers"]),
        "mp3": None,
        "audio_s": None,
        "render_s": None,
        "x_realtime": None,
        "transcript": None,
        "wer": None,
        "derailed": None,
        "derail_reasons": [],
        "markers_heard": [],
        "similarity": None,
        "nearest": None,
        "nearest_score": None,
        "nearest_ok": None,
        "f0_hz": None,
        "clip_f0_hz": None,
        "chars_per_s": None,
        "clip_chars_per_s": None,
        "tempo_ratio": None,
        "lead_silence_s": None,
        "trail_silence_s": None,
        "rms_dbfs": None,
        "skipped": None,
    }
    if rendered is None:
        row["skipped"] = "engine lacks the capability this probe needs"
        return row
    mp3 = Path(rendered["mp3"])
    transcript = transcribe(mp3)
    heard = set(normalize_words(transcript))
    embedding = speaker_embedding(mp3)
    clips = {name: s["embedding"] for name, s in clip_stats.items()}
    nearest, nearest_score = nearest_clip(embedding, clips)
    profile = audio_profile(mp3)
    audio_s = rendered["audio_s"]
    render_s = rendered["render_s"]
    speaker = take["speaker"]
    own = clip_stats.get(speaker) if speaker else None
    reasons = derailment(take["script"], transcript)
    row.update(
        {
            "mp3": mp3.name,
            "audio_s": audio_s,
            "render_s": render_s,
            "x_realtime": audio_s / render_s if render_s and render_s > 0 else None,
            "transcript": transcript,
            "wer": word_error_rate(take["script"], transcript),
            "derailed": bool(reasons),
            "derail_reasons": reasons,
            "markers_heard": [m for m in take["markers"] if m in heard],
            "similarity": cosine(embedding, own["embedding"]) if own else None,
            "nearest": nearest,
            "nearest_score": nearest_score,
            "nearest_ok": (nearest == speaker) if speaker else None,
            "f0_hz": median_f0(mp3),
            "clip_f0_hz": own["f0_hz"] if own else None,
            "chars_per_s": len(take["script"]) / audio_s if audio_s and audio_s > 0 else None,
            "clip_chars_per_s": own["chars_per_s"] if own else None,
            "lead_silence_s": profile["lead_silence_s"],
            "trail_silence_s": profile["trail_silence_s"],
            "rms_dbfs": profile["rms_dbfs"],
        }
    )
    if row["chars_per_s"] and row["clip_chars_per_s"]:
        row["tempo_ratio"] = row["chars_per_s"] / row["clip_chars_per_s"]
    return row


def _mean(values: list[Any]) -> float | None:
    real = [float(v) for v in values if v is not None]
    return statistics.fmean(real) if real else None


def summarize(rows: list[dict[str, Any]], passes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    measured = [r for r in rows if r["skipped"] is None]
    clone = [r for r in measured if r["kind"] != "direction"]
    events = next((r for r in measured if r["kind"] == "events"), None)
    memory = [p["peak_memory_bytes"] for p in passes.values() if p["peak_memory_bytes"]]
    return {
        "takes": len(rows),
        "measured": len(measured),
        "audio_s_total": sum(r["audio_s"] or 0.0 for r in measured),
        "render_s_total": sum(r["render_s"] or 0.0 for r in measured),
        "x_realtime_mean": _mean([r["x_realtime"] for r in measured]),
        "wer_mean": _mean([r["wer"] for r in measured]),
        "wer_worst": max((r["wer"] for r in measured if r["wer"] is not None), default=None),
        "similarity_mean": _mean([r["similarity"] for r in clone]),
        "nearest_ok": sum(1 for r in clone if r["nearest_ok"]),
        "nearest_total": sum(1 for r in clone if r["nearest_ok"] is not None),
        "tempo_ratio_mean": _mean([r["tempo_ratio"] for r in clone]),
        "lead_silence_mean": _mean([r["lead_silence_s"] for r in measured]),
        "trail_silence_mean": _mean([r["trail_silence_s"] for r in measured]),
        "rms_dbfs_mean": _mean([r["rms_dbfs"] for r in measured]),
        "derailed": sum(1 for r in measured if r["derailed"]),
        "markers_heard": list(events["markers_heard"]) if events else [],
        "load_s": {name: p["load_s"] for name, p in passes.items()},
        "peak_memory_bytes": max(memory) if memory else None,
    }


# --- ledger + report ---------------------------------------------------------------


def evals_dir() -> Path:
    """Resolved per call, off render.CONFIG_DIR, so the test sandbox that redirects
    every writable render.py path covers the ledger too."""
    return Path(render.CONFIG_DIR) / "evals"


def ledger_paths(engine: str, date: str) -> tuple[Path, Path]:
    """<date>-<engine>.json and .html; a second run the same day gets -2, -3, ...
    rather than overwriting an entry the operator may already have read."""
    base = evals_dir()
    n = 1
    while True:
        stem = f"{date}-{engine}" + (f"-{n}" if n > 1 else "")
        ledger, report = base / f"{stem}.json", base / f"{stem}.html"
        if not ledger.exists() and not report.exists():
            return ledger, report
        n += 1


def _fmt(value: Any, digits: int = 2, unit: str = "") -> str:
    if value is None:
        return "&mdash;"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.{digits}f}{unit}"
    return html.escape(str(value)) + unit


def _gb(value: int | None) -> str:
    return "&mdash;" if value is None else f"{value / 1e9:.1f} GB"


def _data_uri(path: Path | None) -> str | None:
    if path is None or not Path(path).is_file():
        return None
    return "data:audio/mpeg;base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")


_REPORT_CSS = """
body { font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
       margin: 2rem auto; max-width: 1200px; padding: 0 1rem; color: #1b1e24; background: #fafaf7; }
h1 { font-size: 1.6rem; margin-bottom: .2rem; } h2 { margin-top: 2.2rem; }
.meta { color: #667; font-size: .9rem; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
th, td { text-align: left; padding: .35rem .6rem; border-bottom: 1px solid #ddd;
         vertical-align: top; }
th { background: #eeeee8; }
.take { margin: 1.4rem 0; padding: 1rem; background: #fff; border: 1px solid #e3e3dc;
        border-radius: 6px; }
.take h3 { margin: 0 0 .3rem; font-size: 1.05rem; }
.script { color: #333; margin: .2rem 0 .8rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 1rem; }
.cell { padding: .7rem; border: 1px solid #e3e3dc; border-radius: 6px; background: #fcfcfa; }
.cell h4 { margin: 0 0 .4rem; font-size: .95rem; }
.cell.derailed { border-color: #c0392b; background: #fff4f2; }
audio { width: 100%; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: .1rem .8rem;
     margin: .6rem 0; font-size: .88rem; }
dt { color: #667; } dd { margin: 0; }
.transcript { font-size: .88rem; color: #444; margin: .4rem 0 0; }
.flag { color: #c0392b; font-weight: 600; margin: .4rem 0 0; }
.ok { color: #2e7d32; }
"""


def build_report(entry: dict[str, Any], audio: dict[str, dict[str, Path | None]]) -> str:
    """One self-contained HTML page: an engines table, then every take with a player
    per engine side by side. Every string that came from a model or a file goes
    through html.escape — a transcript is model output."""
    e = html.escape
    names = list(entry["engines"])
    specs = {n: render.ENGINES[n] for n in names}
    title = f"TTS eval: {entry['candidate']}"
    if entry["control"] != entry["candidate"]:
        title += f" vs {entry['control']}"
    out: list[str] = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f"<title>{e(title)} ({e(entry['date'])})</title>",
        f"<style>{_REPORT_CSS}</style></head><body>",
        f"<h1>{e(title)}</h1>",
        '<p class="meta">'
        + " &middot; ".join(
            e(s)
            for s in (
                entry["date"],
                f"render.py {entry['git_sha']}",
                f"mlx-audio {entry['mlx_audio_version'] or 'unknown'}",
                f"whisper {entry['whisper_model']}",
                f"corpus {entry['corpus_sha256'][:12]}",
                f"control: {entry['control']} (re-rendered this run)",
            )
        )
        + "</p>",
        "<h2>Engines</h2>",
        "<table><thead><tr><th></th>"
        + "".join(f"<th>{e(specs[n].label)} (<code>{e(n)}</code>)</th>" for n in names)
        + "</tr></thead><tbody>",
    ]

    def row(label: str, cells: list[str]) -> None:
        out.append(f"<tr><th>{e(label)}</th>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")

    eng = entry["engines"]
    row("model", [f"<code>{e(specs[n].base_model_id)}</code>" for n in names])
    row(
        "design model",
        [
            f"<code>{e(specs[n].design_model_id or 'same (on the base model)')}</code>"
            for n in names
        ],
    )
    row("license", [e(specs[n].license) for n in names])
    row("capabilities", [e(", ".join(sorted(specs[n].capabilities))) for n in names])
    row(
        "take ceiling",
        [
            "none" if specs[n].max_take_chars is None else f"{specs[n].max_take_chars} chars"
            for n in names
        ],
    )
    row("min mlx-audio", [e(specs[n].min_mlx_audio) for n in names])
    row(
        "load time (clone / design pass)",
        [
            " / ".join(_fmt(v, 1, " s") for v in eng[n]["summary"]["load_s"].values()) or "&mdash;"
            for n in names
        ],
    )
    row("peak memory", [_gb(eng[n]["summary"]["peak_memory_bytes"]) for n in names])
    row("x realtime, mean", [_fmt(eng[n]["summary"]["x_realtime_mean"], 2, "x") for n in names])
    row(
        "audio / render, total",
        [
            f"{_fmt(eng[n]['summary']['audio_s_total'], 0, ' s')} / "
            f"{_fmt(eng[n]['summary']['render_s_total'], 0, ' s')}"
            for n in names
        ],
    )
    row(
        "WER, mean / worst",
        [
            f"{_fmt(eng[n]['summary']['wer_mean'], 3)} / {_fmt(eng[n]['summary']['wer_worst'], 3)}"
            for n in names
        ],
    )
    row("speaker similarity, mean", [_fmt(eng[n]["summary"]["similarity_mean"], 3) for n in names])
    row(
        "nearest clip correct",
        [f"{eng[n]['summary']['nearest_ok']}/{eng[n]['summary']['nearest_total']}" for n in names],
    )
    row(
        "tempo vs clip, mean ratio",
        [_fmt(eng[n]["summary"]["tempo_ratio_mean"], 2, "x") for n in names],
    )
    row(
        "lead / trail silence, mean",
        [
            f"{_fmt(eng[n]['summary']['lead_silence_mean'], 2, ' s')} / "
            f"{_fmt(eng[n]['summary']['trail_silence_mean'], 2, ' s')}"
            for n in names
        ],
    )
    row("RMS, mean", [_fmt(eng[n]["summary"]["rms_dbfs_mean"], 1, " dBFS") for n in names])
    row(
        "derailed takes",
        [
            f'<span class="{"flag" if eng[n]["summary"]["derailed"] else "ok"}">'
            f"{eng[n]['summary']['derailed']}/{eng[n]['summary']['measured']}</span>"
            for n in names
        ],
    )
    row(
        "event markers heard",
        [e(", ".join(eng[n]["summary"]["markers_heard"]) or "none") for n in names],
    )
    out.append("</tbody></table>")

    out.append("<h2>Takes</h2>")
    takes_by_engine = {n: {r["id"]: r for r in eng[n]["takes"]} for n in names}
    first = eng[names[0]]["takes"]
    last_scene_id = next((t["id"] for t in reversed(first) if t["kind"] == "scene"), None)
    for ref in first:
        tid = ref["id"]
        head = tid + (f" &middot; {e(ref['speaker'])}" if ref["speaker"] else "")
        if ref["band"]:
            head += f" &middot; {e(ref['band'])}"
        out.append(f'<section class="take"><h3>{head}</h3>')
        script = next(t for t in entry["corpus_takes"] if t["id"] == tid)
        if script.get("instruct"):
            out.append(f'<p class="script"><em>instruct:</em> {e(script["instruct"])}</p>')
        out.append(f'<p class="script">{e(script["text"])}</p><div class="grid">')
        for n in names:
            r = takes_by_engine[n].get(tid)
            cls = "cell derailed" if r and r["derailed"] else "cell"
            out.append(f'<div class="{cls}"><h4>{e(specs[n].label)}</h4>')
            if r is None or r["skipped"]:
                out.append(f"<p>{e((r or {}).get('skipped') or 'not rendered')}</p></div>")
                continue
            uri = _data_uri(audio[n].get(tid))
            if uri:
                out.append(f'<audio controls preload="none" src="{uri}"></audio>')
            out.append("<dl>")
            for label, value in (
                ("audio", _fmt(r["audio_s"], 1, " s")),
                ("x realtime", _fmt(r["x_realtime"], 2, "x")),
                ("WER", _fmt(r["wer"], 3)),
                ("similarity", _fmt(r["similarity"], 3)),
                (
                    "nearest",
                    (e(r["nearest"] or "") + (" &#10003;" if r["nearest_ok"] else ""))
                    if r["nearest"]
                    else "&mdash;",
                ),
                ("f0 / clip", f"{_fmt(r['f0_hz'], 0, ' Hz')} / {_fmt(r['clip_f0_hz'], 0, ' Hz')}"),
                (
                    "chars/s / clip",
                    f"{_fmt(r['chars_per_s'], 1)} / {_fmt(r['clip_chars_per_s'], 1)}"
                    + (f" ({_fmt(r['tempo_ratio'], 2, 'x')})" if r["tempo_ratio"] else ""),
                ),
                (
                    "lead / trail",
                    f"{_fmt(r['lead_silence_s'], 2, ' s')} / {_fmt(r['trail_silence_s'], 2, ' s')}",
                ),
                ("RMS", _fmt(r["rms_dbfs"], 1, " dBFS")),
            ):
                out.append(f"<dt>{label}</dt><dd>{value}</dd>")
            if r["markers"]:
                out.append(
                    f"<dt>markers heard</dt><dd>{e(', '.join(r['markers_heard']) or 'none')}</dd>"
                )
            out.append("</dl>")
            out.append(f'<p class="transcript">{e(r["transcript"] or "")}</p>')
            if r["derailed"]:
                out.append(f'<p class="flag">derailed: {e(", ".join(r["derail_reasons"]))}</p>')
            out.append("</div>")
        out.append("</div></section>")
        if tid == last_scene_id:
            out.append('<section class="take"><h3>scene &middot; joined</h3>')
            out.append(
                '<p class="script">The eight turns joined as render.py ships a scene: '
                f"one chapter, {render.TURN_GAP_MS} ms between turns.</p>"
                '<div class="grid">'
            )
            for n in names:
                uri = _data_uri(audio[n].get("scene"))
                out.append(f'<div class="cell"><h4>{e(specs[n].label)}</h4>')
                out.append(
                    f'<audio controls preload="none" src="{uri}"></audio>'
                    if uri
                    else "<p>not rendered</p>"
                )
                out.append("</div>")
            out.append("</div></section>")
    out.append("</body></html>")
    return "\n".join(out)


# --- the run ------------------------------------------------------------------------


def run_bench(
    candidate: str,
    *,
    workdir: Path | None = None,
    keep_workdir: bool = False,
    control: str = CONTROL_ENGINE,
) -> dict[str, Any]:
    if candidate not in render.ENGINES:
        render.die(f"unknown engine {candidate!r}; registered: {', '.join(render.ENGINES)}")
    missing = check_bench_deps()
    if missing:
        render.die(
            "bench deps missing: "
            + ", ".join(missing)
            + f" (python3 -m pip install --user {' '.join(missing)} — the `bench` extra in "
            "pyproject.toml); nothing was rendered"
        )
    corpus = load_corpus()
    takes = corpus_takes(corpus)
    cast = cast_map(corpus)
    engines = [candidate] + ([control] if control != candidate else [])
    date = dt.date.today().isoformat()
    if workdir is None:
        Path(render.TMP_BASE).mkdir(parents=True, exist_ok=True)
        workdir = Path(tempfile.mkdtemp(prefix=WORKDIR_PREFIX, dir=render.TMP_BASE))
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    render.log(f"tts-eval: {candidate} vs control {control}; {len(takes)} takes; workdir {workdir}")

    rendered = {name: render_engine(name, takes, cast, workdir / name) for name in engines}

    render.log("analyzing cast clips...")
    clip_stats = analyze_clips(cast)
    entry: dict[str, Any] = {
        "date": date,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "candidate": candidate,
        "control": control,
        "git_sha": render.resolve_render_sha(),
        "mlx_audio_version": installed_version("mlx-audio"),
        "whisper_model": WHISPER_MODEL,
        "corpus_sha256": corpus_sha256(),
        "corpus_takes": [
            {k: t.get(k) for k in ("id", "kind", "speaker", "band", "text", "instruct")}
            for t in takes
        ],
        "engines": {},
    }
    audio_paths: dict[str, dict[str, Path | None]] = {}
    for name in engines:
        spec = render.ENGINES[name]
        render.log(f"analyzing {name} ({len(rendered[name]['audio'])} takes)...")
        rows = [analyze_take(t, rendered[name]["audio"].get(t["id"]), clip_stats) for t in takes]
        entry["engines"][name] = {
            "label": spec.label,
            "base_model_id": spec.base_model_id,
            "design_model_id": spec.design_model_id,
            "license": spec.license,
            "capabilities": sorted(spec.capabilities),
            "max_take_chars": spec.max_take_chars,
            "min_mlx_audio": spec.min_mlx_audio,
            "passes": rendered[name]["passes"],
            "takes": rows,
            "summary": summarize(rows, rendered[name]["passes"]),
        }
        audio_paths[name] = {tid: a["mp3"] for tid, a in rendered[name]["audio"].items()}
        audio_paths[name]["scene"] = rendered[name]["scene_mp3"]
    _decode_16k.cache_clear()

    ledger, report = ledger_paths(candidate, date)
    entry["ledger_path"] = str(ledger)
    entry["report_path"] = str(report)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    render._atomic_write_text(ledger, json.dumps(entry, indent=2, sort_keys=True) + "\n")
    render._atomic_write_text(report, build_report(entry, audio_paths))
    if not keep_workdir:
        shutil.rmtree(workdir, ignore_errors=True)
    for name in engines:
        s = entry["engines"][name]["summary"]
        render.log(
            f"{name}: WER {_plain(s['wer_mean'], 3)} mean / {_plain(s['wer_worst'], 3)} worst; "
            f"similarity {_plain(s['similarity_mean'], 3)}; nearest {s['nearest_ok']}/"
            f"{s['nearest_total']}; {_plain(s['x_realtime_mean'], 2)}x realtime; "
            f"derailed {s['derailed']}/{s['measured']}; license {render.ENGINES[name].license}"
        )
    render.log(f"ledger: {ledger}")
    render.log(f"report: {report}")
    return entry


def _plain(value: float | None, digits: int) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bench one TTS engine from render.ENGINES against the production engine."
    )
    # Choices come from the registry at parse time: a third engine in ENGINES is
    # benchable with no change here (#200 acceptance).
    parser.add_argument("--engine", required=True, choices=list(render.ENGINES))
    parser.add_argument(
        "--workdir",
        type=Path,
        help="render into this directory (kept) instead of a fresh temp dir; must be new",
    )
    parser.add_argument(
        "--keep-workdir", action="store_true", help="keep the render workdir on success"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    entry = run_bench(
        args.engine,
        workdir=args.workdir,
        keep_workdir=args.keep_workdir or args.workdir is not None,
    )
    print(
        f"BENCHED {args.engine} control={entry['control']} "
        f"ledger={entry['ledger_path']} report={entry['report_path']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
