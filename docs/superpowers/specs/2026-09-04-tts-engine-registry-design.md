# TTS engine registry: `tts_engine` on the manifest, Breeze-TTS-2 as engine two

**Date:** 2026-09-04
**Status:** approved in design walk (four decisions confirmed by Cory: approach A —
registry inside `render.py`; selectable only, no show switches engine in this slice;
the universal "`voice_instruct` + `lines` cast dies" rule stays; the cache key folds
in engine and model id unconditionally)

## Problem

`render.py` can render exactly one TTS model. The choice is two module constants
(`MODEL_ID`, `VOICE_DESIGN_MODEL_ID`) and a hardcoded mlx-audio call shape in
`_render_take`. Evaluating a candidate model therefore means a throwaway harness
that re-implements the TTS call — which is what the 2026-09-04 Breeze-TTS-2 eval
did — so what gets measured is never quite what ships.

That eval found Breeze-TTS-2 at objective parity with Qwen3-TTS on the Surface
Tension cast (WER, speaker similarity, nearest-clip identity), closer to each clip's
own tempo, with tighter turn edges, and with capabilities Qwen3 has none of: inline
vocal events (`(laugh)`, `(sigh)`) are performed rather than read aloud, a clone can
be *directed* with an instruction, and voice design runs on the same weights as
cloning. It is also ~4× slower to render and its weights are non-commercial.

Cory wants two things this makes possible: to **evaluate new models regularly**, and
to **use the features Qwen3 cannot do**. Both need the renderer to run more than one
engine, chosen per show, through one adapter that the eval bench will later reuse —
so that "writing the adapter" is how a model gets evaluated and "naming it in the
manifest" is how it ships, and the two can never drift.

This is slice 1 of 3:

1. **This spec** — engine registry in `render.py`; Breeze-TTS-2 registered and
   selectable; every show keeps rendering on Qwen3.
2. Eval bench skill that exercises the registry's adapter against a fixed corpus and
   metrics, re-rendering the production engine as the control each time.
3. Capability-gated script features: writers may emit vocal events / per-line
   direction only when the show's engine declares them.

## Measurements that shape the design

Same four cast clips, same 21 lines, same M4 Max (128 GB), both engines through
mlx-audio ref-audio cloning. Qwen3 on the production install (mlx-audio 0.4.3),
Breeze on 0.5.1 in a venv. One take per line, no seed.

| | Qwen3-TTS 1.7B Base 8-bit | Breeze-TTS-2 3B 8-bit (mlx-community) |
|---|---|---|
| WER vs script, mean / worst (12 lines) | 0.000 / 0.000 | 0.005 / 0.056 |
| Speaker similarity to own clip, mean | 0.955 | 0.961 |
| Nearest cast clip correct (20 takes) | 20/20 | 20/20 |
| Generation speed, mean | 3.49× realtime | 0.86× realtime |
| Load / first-call warm-up | 5.1 s / 1.2 s | 7.8 s / 5.5 s |
| Peak memory during a line | 11.6 GB | 10.4 GB |
| Leading silence added per take | 0.33 s | 0.00 s |
| `(laugh)` / `(sigh)` in text | read aloud ("Loff", "Sigh") | performed |
| Design + clone | two models | one model (`instruct=` on the base) |
| Weights license | Apache 2.0 | BreezeBlue Research and Non-Commercial |

**Take-length ceiling.** Breeze's codec runs at 12.5 frames/s
(`decode_upsample_rate` 1920 at 24 kHz) and mlx-audio's Breeze `generate` defaults
to `max_tokens=750`, i.e. 60 s. Two probes, Ryan clone, `max_tokens=1500` unless
stated:

| take length | takes | derailed | audio |
|---|---|---|---|
| ≤ 533 chars (the 24 eval takes) | 24 | 0 | 2–34 s |
| 592 chars | 5 | 1 | 36–40 s |
| 839 chars | 5 | 1 | 52–60 s |
| 1000 chars | 5 | 1 | 61–70 s |
| 1100 chars at the default cap of 750 | 1 | 1 (hit the cap mid-babble) | 60 s |

"Derailed" means whisper-large-v3-turbo WER above 0.15, non-ASCII in the transcript,
or a heard-to-script word ratio outside 0.9–1.1. The failures were multilingual
babble, a hallucinated clause ("download this album. With my permission"), and a
skipped clause. Small samples (five per band); quantization (8-bit), the sampling
temperature (0.9 default) and the voice were not varied.

Three consequences. The derailment is intrinsic past roughly 35 s, not the cap, so
raising the cap does not fix it. Breeze's `max_take_chars` is **500**, from the
longest clean evidence (533), and every daily-show band exceeds it. And the ceiling
has to be enforced *before* the render, with the cap passed explicitly to bound a
derailed take at 60 s, because the speech-rate gate cannot see a derailment: the rate
stays normal and whisper transcribes babble as words. A whisper-based detector can
see it, and that is bench material for slice 2.

## Design

### 1. `tts_engine` on the manifest

A new top-level manifest key, closed whitelist `TTS_ENGINES = ("qwen3", "breeze")`,
resolved by `resolve_tts_engine(manifest)` → `"qwen3"` when absent. `validate_manifest`
dies on any other value, naming the whitelist — the `ship_mode` posture, for the
`ship_mode` reason: the engine is a property of the show, a re-run of a manifest must
render the way it rendered before, and a flag that can go missing on one invocation
would silently render a different voice. There is no CLI flag and no `config.json`
default.

### 2. `EngineSpec` and the `ENGINES` table

A frozen dataclass, one instance per engine, in a module-level `ENGINES` dict keyed by
name. Fields:

| field | qwen3 | breeze |
|---|---|---|
| `label` | Qwen3-TTS 1.7B | Breeze-TTS-2 3B |
| `base_model_id` | `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit` | `mlx-community/Breeze-TTS-2-mlx-8bit` |
| `design_model_id` | `mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-bf16` | `None` (design runs on the base model) |
| `capabilities` | `{preset, clone, design}` | `{clone, design, events, direction}` |
| `presets` | `("Ryan", "Aiden", "Ethan", "Chelsie")` | `()` |
| `max_take_chars` | `None` (no observed cap) | `500` (measured, table above) |
| `max_tokens` | `None` (not passed) | `750` (explicit: 500 chars is ~440 frames; the cap bounds a derailed take at 60 s) |
| `min_mlx_audio` | `"0.4.3"` | `"0.5.1"` |
| `license` | Apache 2.0 | BreezeBlue Research and Non-Commercial |

`MODEL_ID`, `VOICE_DESIGN_MODEL_ID` and `VOICES` remain as module constants aliased
to the qwen3 entry, so nothing else in the file moves and `--selftest`'s model-load
check is unchanged.

`events` and `direction` are **declared, not consumed**. No renderer path reads them
in this slice; they exist so the bench (slice 2) can report them and slice 3 can gate
on them.

### 3. Validation: capabilities gate the voice modes; the ceiling gates the text

All in `validate_manifest`, before any model load, each refusal naming the engine and
the capability it lacks:

- `voice: "random"` or a preset name requires `preset`. On breeze this dies with
  "breeze has no presets" instead of passing `"Ryan"` through as a Breeze speaker tag
  and rendering a stranger with no error — the silent wrong-voice class #177 closed.
- `voice_instruct` requires `design`.
- A cast preset entry requires `preset`; a cast clip and `voice: "house"` require
  `clone`.
- A plain-text segment, or any line of a scene, longer than `max_take_chars` dies:
  "segment 3 is 640 chars; engine breeze renders at most 500 per take". Qwen3 is
  unbounded. Every daily-show band (`SHORT_BAND` 500–650, `BODY_BAND` 600–900,
  `LEAD_BAND` 850–1100) exceeds Breeze's ceiling, so the daily show and Frontier
  Commits cannot select breeze as registered — by design, until chunked rendering or
  a verify-and-retry guard exists (follow-up). Surface Tension's lines run 2–35 s
  and all 24 rendered clean.

The existing universal rule that `voice_instruct` cannot share an episode with a
`lines` cast **stays universal**. Breeze could render both on one model; relaxing the
rule per engine (`design_model_id is None`) is slice-3 work, when direction is used.

### 4. Dispatch and model load

`_render_take` gains the engine spec and dispatches per engine × mode:

- qwen3: the three existing branches, kwargs byte-for-byte unchanged
  (`generate(text, language, ref_audio, ref_text)`, `generate_voice_design(text,
  language, instruct)`, `generate(text, voice, language)`).
- breeze clone: `generate(text, ref_audio, ref_text, max_tokens=spec.max_tokens)`.
- breeze design: `generate(text, instruct=voice_instruct, cfg_scale=4.0,
  max_tokens=spec.max_tokens)`.
- breeze preset: unreachable (section 3); dies if reached.

`render_segments` loads `spec.design_model_id` only when the episode is in design mode
*and* the engine has one; otherwise `spec.base_model_id`. Breeze therefore always pays
one load. The result handling (`np.concatenate` of `result.audio`, write at
`SAMPLE_RATE` 24 kHz, re-assert mono 44.1k mp3) is shared and unchanged.

### 5. Cache key

`_segment_cache_key` gains `engine` and `model_id` (the id actually loaded). A key that
omits the model that rendered a take is the silent-replay class #177 closed, one level
up: a workdir rendered under Qwen3 and re-run under Breeze would replay Qwen3 audio
under the new engine's name with no error. Unconditional, so a later change to Qwen3's
own model id (a quant swap) also invalidates. Cost: every existing sidecar misses once;
auto workdirs are per-date and deleted on success, so that is at most one same-day
resume across the upgrade. `_scene_cache_key` is unchanged (it composes line keys).

### 6. Pre-flight `tts-engine` check

A new ordered check after `mlx_audio importable`: resolves the engine, reads the
installed mlx-audio version via `importlib.metadata.version` (no import of
`mlx_audio`, same posture as the `find_spec` probe), and fails when it is below
`spec.min_mlx_audio` — before TTS, in `--dry-run` too. Version comparison is a
tuple-of-ints on the dotted string; no `packaging` dependency. The PASS line carries the
label and the license: `tts-engine: breeze (Breeze-TTS-2 3B; BreezeBlue Research and
Non-Commercial)`, because pre-flight is exactly the moment an operator is deciding.

### 7. Run log, bloopers index, SHIPPED line

`tts_engine` is **appended** to `RUN_LOG_FIELDS` and to `BLOOPER_FIELDS`, null on every
path that does not set it (the null-never-absent rule). The final `SHIPPED` /
`web-ready` line gains `engine=<name>` beside `voice=`/`mode=`.

### 8. mlx-audio 0.4.3 → 0.5.1 (step 0 of implementation, its own commit)

Breeze support landed in mlx-audio on 2026-08-26 (PR 911) and shipped in 0.5.1 on
2026-08-31; the production install is 0.4.3. Step 0 pins `mlx-audio>=0.5.1` in
`pyproject.toml` and `requirements.txt` and upgrades the global install. Because this
changes the Qwen3 code path for all three shows, the PR carries as evidence:
`--dry-run` run-log lines for the daily show, Frontier Commits and Surface Tension on
Qwen3, plus a two-segment `voice_instruct` manifest dry-run (design mode is exercised
by no show, and 0.5.x may have moved `generate_voice_design`).

### 9. Docs

- `skills/daily-podcast/SKILL.md`: `tts_engine` in the manifest schema; an **Engines**
  table (engine, model, capabilities, take ceiling, min mlx-audio, license) with a
  drift test against `ENGINES`, the way the shape table is pinned.
- `docs/durable-voices.md`: one paragraph — the engine is an axis orthogonal to the
  four episode voice modes, which stay exactly four; Breeze does design on one model.
- `CLAUDE.md`: an invariant paragraph in the "one renderer" style — engine lives on
  the manifest, closed whitelist, capabilities gate validation, model id in the key,
  ceiling enforced before the render because the gate cannot see a cap hit.
- `skills/surface-tension/SKILL.md`: one sentence that `tts_engine` is how the show
  would move engines; its manifest example is unchanged (default qwen3).
- `README.md`: mlx-audio ≥ 0.5.1.

## Testing

- `tts_engine`: absent → qwen3; both names accepted; a typo dies naming the whitelist.
- Cache key: changes with `engine` and with `model_id`; unchanged for the same inputs;
  scene key unaffected.
- Breeze validation: `random`, each preset name, and a preset cast entry die naming
  "breeze has no presets"; `voice_instruct` + cast still dies on both engines; a
  501-char segment and a 501-char line die naming the engine; 500 passes; Qwen3
  accepts any length.
- Dispatch: a fake model records kwargs per engine × mode; Qwen3's kwargs equal
  today's byte-for-byte; Breeze clone passes `max_tokens=750` and no `language`;
  Breeze design passes `instruct`, `cfg_scale=4.0`, `max_tokens`; Breeze loads
  `base_model_id` in design mode, Qwen3 loads `design_model_id`.
- Pre-flight: installed version below `min_mlx_audio` fails, at or above passes,
  via monkeypatched `importlib.metadata.version`; runs under `--dry-run`; PASS line
  carries the license.
- Run log / bloopers: `tts_engine` present and null on the paths that don't set it;
  `RUN_LOG_FIELDS` order preserved with the new field last.
- SKILL.md Engines table drift test.
- `ruff` + full `pytest` counts in the PR body; step-0 evidence per section 8.

## Out of scope

- The eval bench skill (slice 2) — it will import `render` the way `bloopers.py`
  does. Filed: https://github.com/schmug/clodcast/issues/200
- Consuming `events` / `direction` anywhere; relaxing the design+cast rule per engine
  (slice 3). Filed: https://github.com/schmug/clodcast/issues/201
- Switching Surface Tension (or any show) to Breeze: a one-line manifest change made
  after the ear test on the eval report, so the 4× slower weekly render and the
  non-commercial license each get a deliberate yes. Filed:
  https://github.com/schmug/clodcast/issues/203
- Chunked rendering of long segments, and a whisper-based derailment detector with
  re-roll — the two ways a narrator show could ever use Breeze. Filed:
  https://github.com/schmug/clodcast/issues/202
- The bf16 Breeze variant and a sampling-temperature sweep (both may move the
  derailment rate); an `--engine` flag on `--selftest`; a `config.json` default engine.
