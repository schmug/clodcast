---
id: tts-eval
name: tts-eval
description: Use when the user asks to evaluate, bench, or compare a TTS model or engine for the shows — renders a fixed corpus through render.py's engine registry on the candidate engine and on the production engine as a re-rendered control, measures a fixed metric set, appends a dated ledger entry and writes one self-contained HTML report with side-by-side players. Not a show; nothing here ships audio.
enabled: true
---

# TTS eval bench

One command that says whether a new model is better than what ships, on the same lines and the same metrics every time, so the next evaluation costs an adapter and a run rather than a day.

```bash
python3 skills/tts-eval/bench.py --engine breeze
```

That renders the corpus below on `breeze` **and** on the production engine, analyzes every take, and prints the ledger and report paths on its last two lines. Expect ten to fifteen minutes on an M4 Max for two engines (Breeze renders at under realtime); the analysis deps are checked before anything renders.

## How to evaluate a new model

1. Register it in `render.ENGINES` (`skills/daily-podcast/render.py`) with its adapter in `_ENGINE_GENERATORS` — capabilities, take ceiling, minimum mlx-audio, license. That is the registry's job and the whole of the model-specific work; the design is in `docs/superpowers/specs/2026-09-04-tts-engine-registry-design.md`.
2. Run the bench with `--engine <name>`. The `--engine` choices are read from `ENGINES` at parse time, so nothing in this skill changes.
3. Open the report and listen. The numbers rank; the ear decides. Then, if the answer is yes, switching a show is a one-line manifest change made deliberately (the Surface Tension case is #203).

## What it renders, and how

The bench renders **through** `render.validate_manifest` and `render.render_segments` with the engine named on the manifest — the registry's own adapter, never a re-implementation of the TTS call. That is the point of #200: the 2026-09-04 Breeze evaluation used a scratch harness with its own generate call, so what it measured was never quite what ships. Here the same validation applies (an engine that cannot clone the cast is refused the way a show on it would be), the same text prep, the same mono-44.1k encode. Per-take timing wraps `render._render_take` at the module seam for the length of a pass; a test greps `bench.py` for `mlx_audio` and fails on any mention.

The clone corpus is rendered as `lines` scenes over the Surface Tension cast (`skills/surface-tension/refs/{ryan,aiden,ethan,chelsie}.{wav,txt}`, the same map `st_write.cast_map` builds), one segment per line and the eight-turn scene as one segment — exactly how that show renders. The direction probe is a second pass through `voice_instruct`, the only instruct path the registry exposes today (VoiceDesign on `qwen3`, `instruct=` on the base model on `breeze`); per-line direction over a clone is #201 and will move the probe when it lands. An engine without `design` skips that probe and says so in the report.

### The corpus

Fixed in `corpus.json`. Never edit a line in place — every ledger entry ever written measured that text, and a changed line makes old entries incomparable; add a new probe as a new key. ASCII only, no digits (WER is measured on spoken words), and every line stays under the smallest finite take ceiling in `ENGINES` (a test pins it). This table is pinned to the corpus by a test.

| probe | takes | rendered as | measures |
| --- | --- | --- | --- |
| `line` | 12 | one single-line scene per line; three bands (short / medium / long) per cast voice | identity, tempo and pitch against each voice's own clip, at three lengths |
| `scene` | 8 | one eight-turn four-voice scene, also joined as render.py ships it | turn edges (lead/trail silence) and identity under fast cuts |
| `events` | 1 | one clone line carrying `(laugh)` and `(sigh)` | whether markers are performed or read aloud (`markers heard`) |
| `direction` | 1 | one plain-text segment through `voice_instruct` | the instruct path: intelligibility, pitch, tempo, level |

### The metrics

Every take gets the same set; the summary aggregates per engine. Transcription is `mlx-community/whisper-large-v3-turbo`; speaker embeddings are resemblyzer's; pitch is librosa's pyin. This table is pinned to `bench.METRICS` by a test.

| key | metric | how |
| --- | --- | --- |
| `wer` | WER | word error rate of the whisper transcript against the script (Levenshtein over normalized words) |
| `similarity` | speaker similarity | cosine similarity of the take's speaker embedding to its own clip's |
| `nearest` | nearest clip | the cast clip the take's embedding is closest to; ok when it is its own |
| `f0_hz` | median f0 | median voiced pitch, beside the clip's own |
| `chars_per_s` | chars/s | script characters per second of audio, beside the clip's own (the ratio is the tempo drift) |
| `lead_silence_s` | leading silence | seconds before the first sample over -40 dBFS |
| `trail_silence_s` | trailing silence | seconds after the last sample over -40 dBFS |
| `rms_dbfs` | RMS | overall level in dBFS |
| `x_realtime` | x realtime | seconds of audio per second of render, per take |
| `load_s` | load time | seconds from the pass start to the first take (the model load), per pass |
| `peak_memory_bytes` | peak memory | MLX's peak memory during the pass, when `mlx.core` is importable |
| `markers_heard` | markers heard | event marker words present in the events probe's transcript (read aloud rather than performed) |
| `derailed` | derailed | the rule below |

**Derailment** is the registry spec's definition: a take is derailed when `WER > 0.15`, or the transcript contains non-ASCII (typographic quotes and dashes are folded first), or the heard-to-script word ratio is outside 0.9-1.1. All three were needed on 2026-09-04 — multilingual babble, a hallucinated clause, a skipped clause — and the speech-rate gate saw none of them. A derailed take is flagged red in the report with its reasons. Short lines make WER coarse: on the first real run a 13-word line was flagged at 0.154 because whisper wrote "Alright" for "All right" — read a flag with the transcript beside it, which is why the report prints both.

**License is a first-class column**, read from `EngineSpec.license`: the report's engines table carries it beside the model and the capabilities, because the moment someone is reading a report is exactly the moment they are deciding.

## The control

The production engine — `render.resolve_tts_engine({})`, whatever a manifest with no `tts_engine` renders on, `qwen3` today — is **re-rendered on every run**. Never compare against a stored baseline: an mlx-audio upgrade or a quantization swap moves it while it still looks like a number, and only a control rendered beside the candidate on the same install on the same day means anything. Benching the control itself (`--engine qwen3`) renders it once and reports one column; it is how to re-baseline after an upgrade.

## Ledger and report

Both land in user state, not the repo:

- `~/.config/daily-podcast/evals/<date>-<engine>.json` — the ledger entry: engine specs (label, model ids, license, capabilities, ceiling), mlx-audio version, `render.py` SHA, the corpus hash, per-pass load time and peak memory, every take's metrics and transcript, and the per-engine summary. A second run the same day gets `-2`, `-3`, ...; nothing is ever overwritten. `jq` over `evals/*.json` is the cross-run view.
- `~/.config/daily-podcast/evals/<date>-<engine>.html` — the report: one self-contained page, no scripts, no external resources, every take's audio embedded as base64 with a player per engine side by side and the metrics under each. Expect around 15 MB for two engines. Transcripts are model output and are escaped.

The render workdir is a fresh temp directory (`tts-eval-*` under the system temp dir) deleted on success; `--keep-workdir` keeps it, and an explicit `--workdir` is always kept. A bench never reuses a cached take — a cached take has no timing.

## Dependencies

The analysis deps are the `bench` extra in `pyproject.toml`, never runtime deps:

```bash
python3 -m pip install --user mlx-whisper librosa resemblyzer
```

`bench.py` imports them function-locally, so `import bench` needs none of them, and a missing one fails the run before a single take is rendered (resemblyzer's VAD imports `pkg_resources` at import time and setuptools 81+ no longer ships it, so `bench.py` supplies a one-call stub when it is absent). The whisper model downloads on first use.

## What this is not

- Not a show, and not on any schedule. Nothing in a run calls it.
- Not a decision: which engine a show uses is a manifest change made after listening (#203).
- Not a guard: the daily show's derailment protection is #202, which will use the same rule at render time.
- Not a sweep: quantization and temperature variants are a later bench feature.
