# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A Claude Code **plugin** (manifest at [.claude-plugin/plugin.json](.claude-plugin/plugin.json)) that ships three shows and one bench. `daily-podcast`, at [skills/daily-podcast/](skills/daily-podcast/), turns a list of saved URLs into a fully-produced Spotify episode in one pass, on top of the external `save-to-spotify` CLI. `frontier-commits`, at [skills/frontier-commits/](skills/frontier-commits/), turns the frontier AI labs' public GitHub org activity (a daily snapshot store diffed into typed, mention-once stories) into a speculation-forward weekly episode shipped through the same `render.py` in its **web-only mode** (RSS-first: R2/RSS is the canonical channel and `save-to-spotify` is never invoked); its contracts live in [skills/frontier-commits/SKILL.md](skills/frontier-commits/SKILL.md), and drift tests in [tests/test_fc_skill_md.py](tests/test_fc_skill_md.py) tie that document's tables to `fc_script_plan`/`fc_stories`. `surface-tension`, at [skills/surface-tension/](skills/surface-tension/), turns personal independent blog posts surfaced by community vote on [bubbles.town](https://bubbles.town) into a weekly four-voice call-in show, shipped through that same `render.py` web-only mode (RSS-first on its own feed; `save-to-spotify` is never invoked) with each scene rendered as a `lines` multi-voice segment; its contracts live in [skills/surface-tension/SKILL.md](skills/surface-tension/SKILL.md), and drift tests in [tests/test_st_skill_md.py](tests/test_st_skill_md.py) tie that document's tables to `st_script_plan`/`st_write`. `tts-eval`, at [skills/tts-eval/](skills/tts-eval/), is not a show: it benches a TTS engine from `render.ENGINES` against the production engine on a fixed corpus and writes a ledger entry plus an HTML report (#200); its contracts live in [skills/tts-eval/SKILL.md](skills/tts-eval/SKILL.md), pinned by [tests/test_tts_eval.py](tests/test_tts_eval.py). Skill discovery is directory convention (`skills/<name>/SKILL.md` + frontmatter) — plugin.json has no skills key.

There is no build step — the "build" is `python3 render.py`. Linting is `ruff` (lint + format-check) and there's a `pytest` invariant suite under [tests/](tests/), both enforced in [CI](.github/workflows/ci.yml) across Python 3.10–3.12; see the README's *Development* section. The contract between the skill prose ([SKILL.md](skills/daily-podcast/SKILL.md)) and the executable ([render.py](skills/daily-podcast/render.py)) is the manifest schema described in both — keep them in sync when changing either.

## How development works here

**Always test changes via `--dry-run` first.** Real runs upload to Spotify and mutate the user's `covered.json` dedup log. Dry-run produces the mp3, cover, and `timeline.json` locally and prints paths, skipping the `save-to-spotify upload` and `timeline set` calls.

```bash
python3 skills/daily-podcast/render.py --manifest /tmp/manifest.json --dry-run
```

For a fast iteration loop on script-template or formatting changes, write a minimal manifest with one or two short segments and dry-run against it. The Qwen3-TTS model load is ~10-15 s on first invocation; subsequent segments stream at ~4-5x realtime on Apple Silicon.

To exercise the unattended path, run the orchestrator:

```bash
python3 skills/daily-podcast/orchestrate.py --dry-run
```

The orchestrator's final stdout is a single line — `SHIPPED <uri> ...` or `FAILED <reason>`. Don't change that contract; schedulers parse it, and SKILL.md's *"Unattended daily run"* section promises the same shape.

## Architecture: the big picture

Four documents are load-bearing; read all of them before changing behavior:

1. **[skills/daily-podcast/SKILL.md](skills/daily-podcast/SKILL.md)** — the script template, voice rules, chapter-duration guardrail, manifest schema. This is what Claude reads when the skill activates.
2. **[skills/daily-podcast/render.py](skills/daily-podcast/render.py)** — the manifest → episode driver. Single file, ~590 lines, no internal modules.
3. **[skills/daily-podcast/orchestrate.py](skills/daily-podcast/orchestrate.py)** — the **self-contained shell entry point**. Pure-Python gather → deterministic metadata-only ranking → one isolated `claude -p` per item → assemble manifest → invoke `render.py`. **Not universally the unattended path:** its child `claude -p` subprocesses need a durable on-disk/env credential, which a Claude-routine scheduler does not provide (see [incidents/auth-failure.md](incidents/auth-failure.md)). Under a Claude routine, the unattended path is the skill itself — SKILL.md's *"Unattended daily run"* section.
4. **[skills/daily-podcast/prompts/daily.md](skills/daily-podcast/prompts/daily.md)** — a **stub**. The unattended procedure was folded into SKILL.md's *"Unattended daily run"* section so there is exactly one copy; this file only points there.

`render.py` is intentionally "dumb": it consumes a manifest that already has the segments written and only handles TTS, concat, cover, upload, timeline, poll, and dedup-log update. Anything script-shaped (curation, fetching, segment writing, self-critique) lives in the skill prose / headless prompt — i.e., is Claude's job, not the renderer's.

### Orchestrator core invariant

**No LLM request holds more than one article body.** Curation is deterministic metadata-only (feedparser titles, dates, summaries — never article bodies). Each ranked item is summarized by its own isolated `claude -p` subprocess. A per-item block, timeout, or error drops only that item and logs it to `dropped.jsonl`; the remaining items still ship. `feed_usage.json` drives the variety penalty so the same feed doesn't dominate consecutive episodes.

**Auth failure is systemic, not per-item.** A child `claude -p` authenticates from disk/env, not from the parent's in-memory session login, so under a scheduler the children can start with no usable credential and every item 401s. `classify_output` maps that to a distinct `AUTH` outcome (`AUTH_RE`, anchored to auth strings only so transient rate-limit/overload errors stay `ERROR`). When a run ends with **zero survivors and any `auth` drop**, `main()` fails fast with an actionable single line pointing at SKILL.md *"Unattended runs need durable credentials"* — instead of silently degrading to the generic `no viable items`. Detection is post-fan-out (a 401 returns fast, so there's no happy-path cost and no preflight probe). Keep `AUTH_RE` auth-only and keep the diagnostic gated on `not survivors`.

### Script variety is assigned, not requested

**The script template is a date-seeded rotation, and the seed must stay the date.** Seventy-six episodes shipped with a byte-identical opening sentence and twelve identically-shaped segments. Telling a model to "be varied" regresses to the mean, and in the orchestrator each segment is written by an isolated `claude -p` that cannot see its neighbours to differ from them — so the variety is *assigned* from outside: `day_index(date_iso)` picks the cold open (`INTRO_MODES`), the sign-off (`OUTRO_MODES`), and every segment's shape (`SEGMENT_SHAPES`) and length band. A date rather than a random draw is what makes a resumed or repeated run rebuild the same episode.

Three things here are load-bearing:

- **A length band measures the body, never body-plus-segue.** `fill_prompt` hands the band to the per-item writer, which produces only the body; `make_transitions` prepends the segue later in `assemble_manifest`, and `MIN_SEGMENT_CHARS` is checked on the body before that happens. Reading the band as covering both starves every short take — a 500-650 slot carrying a 100-character bridge would leave 400 for the story. This is spelled out in SKILL.md because the in-session path is prose-driven and had no way to tell.
- **Segues are assigned from TITLES, and that is what makes them possible at all.** A per-item `claude -p` has never seen the previous story, so it cannot write a bridge to it — segues therefore come from `make_transitions`, which sees the running order of titles only (the same context `make_intro_outro` already has, so the one-body-per-request invariant is untouched). `TRANSITION_MOVES` names what a segue *does* rather than a phrase to say; `cold` carries no text, which is what makes "not every segment needs a segue" mechanical instead of aspirational. Segues walk `SHAPE_ORDERS` from a different row (`TRANSITION_ROW_OFFSET`) so a given shape doesn't carry the same segue forever — `test_transitions_are_not_locked_to_the_shape_rotation` locks that. A failure in `make_transitions` degrades to all hard cuts, never a dead run, same posture as `make_intro_outro`. The prose rule that matters most: **never manufacture a connection** — a digest's adjacent items are often unrelated, and a false link reads worse than a blunt hand-off.
- **`SHAPE_ORDERS` is a Latin square and must stay one.** Both properties carry weight: every *row* is a permutation of the bank (each shape once per five segments), and every *column* holds each shape exactly once (no position starved, and none repeating two days running). Rows are deliberately not mutual rotations, or `stakes-first` would follow `plain-lede` in every episode ever made. **Do not replace the table with arithmetic.** The first version used a stride (`(day + i * stride) % 5`, `stride = 1 + day % 4`) and passed a year-long coverage test while pinning positions 4, 9 and 14 to a single shape for four days at a time — `(1 + p) % 5 == 0` cancelled the day-varying term, leaving only `day // 4`. A smoke test caught it; the unit tests had not. Three tests now hold the line: `test_no_position_holds_its_shape_two_days_running` (kills the stride), `test_segment_shape_reorders_adjacencies_across_days` (kills a plain rotation), and `test_every_position_sees_every_shape_within_one_bank_cycle` (fairness). `test_skill_md_shape_table_matches_the_code` fails if SKILL.md's copy of the table drifts from `SHAPE_ORDERS`.
- **`SHORT_BAND`'s floor IS `MIN_SEGMENT_CHARS`.** A short take that lands under it is classified `REFUSED` and its item is dropped, so lowering the floor silently shortens episodes instead of varying them. Short chapters are only safe at all because Spotify's sub-30s cap was retired upstream (verified 2026-08-22).
- **SKILL.md is the production path, not `orchestrate.py`.** The scheduled run is a `claude -p` following SKILL.md's *Script template* section, so a shape that exists only in code never reaches a real episode. `test_skill_md_documents_every_shape_and_mode` fails if the two drift, and `test_summarize_prompt_declares_the_variety_placeholders` locks the `<<SHAPE>>` / `<<MIN_CHARS>>` / `<<MAX_CHARS>>` contract between `fill_prompt` and `prompts/summarize_item.md`.

### Two ship modes, one renderer (`ship_mode`)

`render.py` ships an episode one of two ways, chosen by the manifest's `ship_mode`
key: `"spotify"` (the default when absent) or `"web"` (#155). Everything above the
ship — render, cover, timeline, pre-flight's local subset, the artifact gate — is
shared verbatim; only the tail differs. `--dry-run` is unchanged in both.

- **Mode lives on the MANIFEST, not the command line.** The distribution channel is
  a property of the show, and re-running a manifest must ship the same way it shipped
  before. A flag can go missing on one invocation, and the failure mode of a missing
  flag is an episode uploaded to a show that was deliberately deprecated. Validation
  is a closed whitelist for the same reason: a typo must die, never fall back to the
  Spotify default.
- **In `web` mode the R2 publish IS the ship, so its failure is fatal** — the exact
  inversion of the default path, where R2 is additive and a failed publish only warns
  because the episode is already live where it counts. `maybe_publish_r2` itself does
  **not** vary by mode (it still never raises and still returns the 3-state result);
  only the caller's treatment of a non-`R2_PUBLISHED` status differs. Keep it that
  way — a publisher that behaves differently per mode is a publisher with two
  untested halves.
- **R2 config flips from optional to required.** `check_r2_credentials(required=True)`
  makes the `absent` state a FAIL. Absent is a PASS on the default path (the web feed
  is genuinely optional there); here it means the run would render a full episode and
  ship it nowhere. `partial` fails in both.
- **Every Spotify-shaped step is skipped, not stubbed:** no upload, no `set_timeline`,
  no `poll_ready`, no capacity check/prune, no in-flight reconciliation (nothing is
  ever left in flight), no `uploaded.json` marker — and therefore no resume branch,
  which is why the resume gate tests `not web_only`: a stale marker from an earlier
  Spotify-mode run in the same workdir must not drag an RSS-first show onto
  `save-to-spotify`. A web-only re-run is idempotent on its own (R2 PUTs replace, the
  manifest entry upserts by slug), so it simply renders again off the TTS cache.
  `tests/test_web_only.py` asserts this mechanically by wiring the `run()` seam to
  raise, rather than inferring it from an absent mock.
- **The dedup entry records the published mp3 URL** where the Spotify path records an
  episode URI — there is no episode URI in this mode, and a null would lose the trail
  from a covered URL back to the episode that covered it.

### One renderer, two engines (`tts_engine`)

`render.py` renders on the engine its manifest names — `"qwen3"` (the default when absent) or `"breeze"` — through a frozen `EngineSpec` per engine in `ENGINES` (design: [docs/superpowers/specs/2026-09-04-tts-engine-registry-design.md](docs/superpowers/specs/2026-09-04-tts-engine-registry-design.md)). Five things are load-bearing.

- **The engine lives on the MANIFEST, closed whitelist, same posture as `ship_mode`.** A re-run must render the way it rendered before, and a flag that can go missing on one invocation would silently render a different voice. No CLI flag, no `config.json` default; a typo dies.
- **Capabilities gate validation, before the model load.** `_validate_engine_capabilities` refuses `voice: "random"`, a preset name, or a preset cast entry on an engine without `preset`, and `voice_instruct` on one without `design`. The alternative on Breeze is passing a Qwen3 preset name through as a speaker tag and rendering a stranger with no error — the silent wrong-voice class #177 closed. `events` / `direction` are consumed since #201 — the last bullet in this section.
- **The take ceiling is enforced before the render because the gate cannot see a derailment.** Breeze derails about one take in five past ~35 s of audio regardless of the token cap (measured 2026-09-04, in the spec); `_validate_take_lengths` refuses anything over `max_take_chars` (500 for Breeze, none for Qwen3), and `max_tokens` is passed explicitly to bound a derailed take. The speech-rate gate stays normal on a derailed take and whisper transcribes babble as words, so nothing downstream would catch it. Every daily-show band exceeds Breeze's ceiling by design until chunked rendering or verify-and-retry exists.
- **The engine and the loaded model id are in every take's cache key, unconditionally.** A key without them replays Qwen3's banked audio under Breeze's name on a re-run — #177 one level up. Every sidecar written before this field existed misses once.
- **Qwen3's path is byte-identical.** `_generate_qwen3` carries the three original branches with their original kwargs; `MODEL_ID` / `VOICE_DESIGN_MODEL_ID` / `VOICES` are aliases into the qwen3 entry. Only an engine with a separate `design_model_id` switches models for `voice_instruct`; Breeze designs on its base model and always pays one load. The "`voice_instruct` + `lines` cast dies" rule is per engine since #201: it dies only where `design_model_id is not None`, because only there is a wrong model to render the cast off.
- **Events are stripped, direction is refused, and every measurement reads the spoken text (#201).** `EVENT_MARKERS` is a closed list (`laugh`, `sigh`) and `strip_event_markers` is the identity on a marker-free string, which is what keeps every clean take's key and measurement untouched. `lines_text` and `speech_rate_rows` measure marker-stripped text on EVERY engine, and `render_segments` strips a take's markers on an engine without `events` (logged, never refused), so audio and measured script never disagree. A line's `instruct` is refused by `validate_manifest` naming the engine unless the engine has `direction` AND the speaker is a clip clone — Breeze's direction form is `generate(text, ref_audio, ref_text, instruct=, cfg_scale=)`, the reference and the instruction together — and `_generate_qwen3` dies on one at the seam. The instruct is folded into the take's key ONLY when set, so undirected keys are byte-identical to before. The writer's side lives in `st_write` (closed vocabulary `DIRECTIONS`, one directed line per scene, an unlisted `(word)` refused at `classify_scene`), gated per show by `assemble_manifest(engine=...)`; `fill_scene_prompt` must be handed the same engine or the writer is offered what the gate refuses.

### The eval bench renders through the registry, never around it (`skills/tts-eval/`)

`bench.py --engine <name>` is how a candidate engine gets measured (#200), and three things about it are load-bearing. **It renders only through `render.validate_manifest` and `render.render_segments` with the engine on the manifest** — the corpus goes through the same refusals, text prep and mono-44.1k encode a show does, and per-take timing wraps `render._render_take` at the module seam for the length of a pass (restored in `finally`); `test_bench_never_calls_mlx_audio_directly` fails on any import-shaped mention of the TTS package, because a bench with its own generate call measures something other than what ships (the 2026-09-04 scratch harness). **The control is `render.resolve_tts_engine({})` re-rendered every run, never a stored baseline** — an mlx-audio upgrade moves the baseline while it still looks like a number. **The corpus (`corpus.json`) is append-only and stays under every finite `max_take_chars` in `ENGINES`** — a changed line makes every earlier ledger entry incomparable, and a line over a registered ceiling means the bench cannot run its whole corpus on that engine. The ledger (`~/.config/daily-podcast/evals/<date>-<engine>.json`, never overwritten) and the report derive from `render.CONFIG_DIR` at call time so the test sandbox covers them; analysis deps are the `bench` extra, imported function-locally, and checked with `find_spec` before a single take renders. Its SKILL.md tables are pinned to `bench.METRICS` and the corpus by drift tests, the `test_st_skill_md.py` pattern.

Pre-flight's `tts-engine` check fails when the installed mlx-audio is below the engine's floor and prints the engine's license on every run; an absent package is `tts-module`'s finding. `tts_engine` is appended LAST to `RUN_LOG_FIELDS` and `BLOOPER_FIELDS`, null on paths that never resolve one. No show sets the key yet; switching one is a deliberate assembler change of the key plus, where the assembler emits a preset episode voice (Surface Tension does), a clone `voice` the new engine can render.

### The reliability layer (pre-flight, artifact gate, durable state, incidents)

Added after an audit of every failure mode this pipeline hit in production. The
per-failure write-ups in [incidents/](incidents/) are the source of truth for
*why* each guard exists — read the relevant one before changing a guard.

- **Pre-flight runs inside `_render`, before TTS.** `preflight()` checks
  ffmpeg/ffprobe → encoder profile → house voice → `mlx_audio` importable →
  `show_id` → R2 credentials → (non-dry-run only) Spotify auth → episode
  capacity. A failure `die()`s before any expensive work. `--dry-run` runs the
  local subset only: **it must never call Spotify and never prune.** Don't move
  network checks out of that guard. `--selftest` is the *separate* standalone
  probe and is still not a run (it writes no run-log record).
- **R2 credentials are three-state and the asymmetry is load-bearing.**
  `configured` passes, `absent` passes (the web feed is optional), `partial`
  **fails**. Making `partial` a warning would re-open the silent web-feed miss.
- **Capacity is checked before the render, and opt-in stays opt-in.**
  `preflight_capacity` pre-prunes only when `auto_prune_episodes` is true;
  with it off, pre-flight *refuses the run* rather than deleting an episode. This
  is a pre-check, not a replacement — `upload()` keeps its reactive
  prune-then-retry-once path for a cap hit that appears mid-run.
- **`verify_artifact` runs after render and BEFORE the dry-run return.** Every
  check is local (ffprobe + a hash), so a `--dry-run` rehearsal exercises the same
  gate a real run does. Moving it below the dry-run return would make the
  rehearsal stop rehearsing. Its rejection blocklist (`rejections.jsonl`,
  sha256-keyed) is the only guard against re-uploading an artifact Spotify already
  rejected — which is *destructive*, since each retry prunes a published episode.
  The conformance checks guard render regressions; be honest that they would not
  have caught the 2026-08-08 rejection.
- **The auto workdir is deterministic (`daily-podcast-<date>`), not `mkdtemp`.**
  That is what makes an interrupted run resumable by re-running the same command.
  Tests MUST patch `render.TMP_BASE` or they will collide with a real same-day
  run — `tests/conftest.py` does this globally and asserts it.
- **`state.json` supersedes nothing.** It is a stage checkpoint for resume and
  observability. `uploaded.json` is still the authoritative upload marker and
  `covered.json` is still the sole dedup source of truth. A corrupt state file
  degrades to "nothing completed", never a hard failure.
- **In-flight recovery has a give-up path.** On `readiness: FAILED`,
  `_abandon_inflight` records the artifact, writes an incident, clears
  `inflight.json`, and lets the run continue. It deliberately does **not** write
  `covered.json` (those URLs never shipped — they must return to the pool) and
  deliberately does **not** delete the dead episode (irreversible; human-gated;
  the prune tier reclaims it anyway). `wait_for_readiness` returns a status so
  recovery can branch; `poll_ready` keeps the die-on-`FAILED` contract.
- **RETIRED INVARIANT:** `_resume` used to be forbidden from seeing `config.json`
  at all. That is exactly what made the R2 web-feed publish silently skip on every
  recovery. `_render` now resolves config once and passes it in; `_resume` still
  never calls `load_config` itself. The load on the resume branch is deliberately
  **tolerant** (missing file → `{}`), so a recovery still works on a box with no
  config.
- **Incident reports go to `~/.config/daily-podcast/incidents/new/`, NOT a
  repo-relative path.** The scheduled run executes from the version-keyed plugin
  cache, where a repo-relative write is invisible and wiped on the next release.
  Writing one is best-effort and must never change a run's exit code (same
  contract as `write_run_log`). Every slug in `_INCIDENT_SIGNATURES` must have a
  matching `incidents/<slug>.md` — a test enforces it, because the generated
  report tells the reader to go read that file.
- **Tests must never touch real user state.** `tests/conftest.py` redirects every
  writable path (`CONFIG_DIR`, `COVERED_PATH`, `INFLIGHT_PATH`, `RUN_LOG_PATH`,
  `REJECTIONS_PATH`, `INCIDENT_DIR`, `TMP_BASE`) per-test and asserts none of them
  point at `~/.config/daily-podcast` afterwards. This exists because a failure-path
  test scribbled incident files into the real config dir once. Don't remove it.

### Invariants the renderer enforces

These are subtle and easy to break. Preserve them or the produced episode is rejected by Spotify or sounds wrong.

- **The web `slug` is keyed on the episode DATE, never the title.** `slug_for_date(date)`
  cannot see `title` by construction (#128). cortech.online republishes `/podcast/<slug>/`
  as an `isPermaLink` `<guid>`, and Spotify treats a changed guid as a brand-new episode —
  so every published slug is immutable and the title is free display text. Its shape is a
  compatibility artifact reproducing the slugs minted from the old date-only titles;
  `tests/data/published_slugs.tsv` pins all 75 live ones byte-for-byte (append, never edit).
  Month names are a literal table, not `strftime("%B")`, which is LC_TIME-dependent. The
  `--dry-run` preview and the real publish both resolve the URL through
  `r2_episode_mp3_url`, so the rehearsal can't advertise a URL the publish wouldn't write.
- **Strict 1:1 segment ↔ source mapping.** `build_timeline_and_description` assumes every non-null `source_url` becomes a `link` companion to the chapter at that index. Don't merge segments or attach multiple URLs to one segment.
- **Consecutive chapter starts must be at least `MIN_CHAPTER_GAP_MS` = 5 s apart; the final chapter is exempt.** This is the *only* chapter-duration rule the platform still has, and it governs chapter **spacing** (`start_time_ms` deltas), not inter-chapter silence — so `DEFAULT_SILENCE_MS = 800` is unrelated to it and stays. `plan_silences` pads a segment's trailing silence only far enough to reach that 5 s floor, which real segments never trigger; `verify_artifact` re-checks it at the artifact gate. **Retired invariant (verified 2026-08-22):** there used to be a "max 3 chapters under 30 s" rule here, and `plan_silences` padded toward a 30.5 s target — up to 12 s of dead air per chapter — dying with a script-rewrite error when it ran out of room. Upstream PR #44 (save-to-spotify v0.1.4) dropped that cap. Confirmed against CLI 0.2.0 on a throwaway show: a timeline with 11 of 12 chapters under 30 s was accepted by `timeline set` and processed to `READY`, while a 3 s gap was still refused with `chapter at index 0 must be at least 5s long`. Don't reintroduce a 30 s target.
- **A `lines` scene is still ONE chapter, and its derived `text` is not optional (#172).** A segment may carry `lines: [{speaker, text}, ...]` instead of `text`; each line renders in its cast voice into `line_NN_LL.mp3` and the takes join into the same `seg_NN.mp3` — so silences, chapter math, timeline, artifact gate, run log, R2 publish and dedup never learn that the segment was a scene. Four things are load-bearing. **`materialize_line_text` runs immediately after `validate_manifest` and before anything measures a segment**: `speech_rate_rows` treats a zero-char segment as *unmeasurable*, so a lines-only show would fall under `MIN_RATE_SAMPLE_SEGMENTS` and return `[]` — "no evidence of a defect" — silently disarming the TTS-degeneration gate and the bloopers bin for the whole show. **`TURN_GAP_MS = 250` is the pause between TURNS**, not `DEFAULT_SILENCE_MS` (between chapters) and emphatically not `MIN_CHAPTER_GAP_MS` (chapter *spacing*, not silence); it stops inside `join_line_takes` and `plan_silences` is byte-identical for a lines episode. **`text` and `lines` are mutually exclusive** — a scene carrying both dies naming the fields rather than picking one, which would ship audio and a measured script that disagree. **The cast runs on the base `MODEL_ID`, and only on it**: a cast value is a bundled preset name or a recorded clip `{ref_audio, ref_text}` (#177), and both cost one shared model load, while `voice_instruct` is a second model that drifts — that combination dies. `speaker` is a role resolved through the manifest's `cast`, NOT a fifth voice mode; SKILL.md and `docs/durable-voices.md` still promise exactly four EPISODE voice modes.
- **A cast clip is identified in the cache by its BYTES, never its path (#177).** `resolve_cast_voice` resolves one cast entry into `(mode, label, ref_audio, ref_text, ref_fingerprint)` and `render_segments` folds the fingerprint into that member's per-line key. This is the guard against the only *silent* failure the lines layer has: with `mode` hardcoded to `"preset"` and no ref fields in the key — the pre-#177 shape — re-pointing a persona at a different clip, or re-recording one in place, leaves the key unchanged, so the run "succeeds" and replays the previous voice's banked audio under the new one's name. Right text, right length, wrong person, no error anywhere. Loosening `validate_manifest`'s cast whitelist without the key work re-opens it. A **preset** entry's key is unchanged from #172 on purpose, so takes already banked in a workdir stay valid. Both cast shapes stay closed whitelists (`_validate_cast_voice`): a clip entry carrying a stray `voice` key reads like it selects a preset and does nothing at all.

- **The last segment gets `LAST_SILENCE_MS = 0` trailing silence.** Padding the tail breaks chapter math (`last_chapter_start_ms >= episode_duration_ms` is fatal).
- **`covered.json` is only written after the ship succeeds.** On the default path that means after `poll_ready` returns READY; under `ship_mode: "web"` (#155) it means after the R2 publish returns `R2_PUBLISHED`. Don't move either `save_covered` call earlier — a failed ship must leave the dedup log untouched so the next run retries those URLs. The posture is what's load-bearing, not the specific success signal.
- **`covered.json` is pruned to a `COVERED_RETENTION_DAYS = 180` window on every `save_covered`.** Entries whose ISO `date` is strictly older than today − 180 days are dropped before the atomic write; entries with a missing or non-ISO `date` are kept (no data loss on schema drift). 180 days is far larger than the curation lookback (`lookback_hours`), so pruning never re-exposes a URL that dedup still cares about. Don't shorten the window into the lookback range, and don't prune in `load_covered` — the read contract returns the file as-is.
- **The HTML description is capped at `SPOTIFY_SUMMARY_MAX_CHARS = 4000`.** `build_timeline_and_description` drops whole trailing chapter `<p>` blocks (longest-suffix-first, never mid-tag) until the summary fits, always preserving the leading summary `<p>`. The timeline JSON is unaffected — every audio chapter still exists; only the show-notes listing shrinks. Don't ellipsize inside a block or cut markup mid-tag.
- **A successful `upload()` writes `<workdir>/uploaded.json` before the `set_timeline`/`poll_ready` tail.** This is the resume marker: re-running with the same explicit `--workdir` skips re-upload and re-runs only the idempotent tail (`set_timeline`/`poll_ready`/`save_covered`). Don't write it before `upload()` succeeds, and don't gate dedup on it — `covered.json` is still only written after READY. Resume is a manual, same-workdir recovery path; the cron's cross-day duplicate risk (per-date workdirs) is deferred to the in-flight-log work.
- **MP3 is mono 44.1k throughout.** Every ffmpeg invocation re-asserts this. Concat-protocol is fragile across mismatched sample rates / channels; don't relax it.
- **The run log (`~/.config/daily-podcast/runs.jsonl`) is append-only and has a stable schema.** `write_run_log` appends one JSON line per run (`status` = `ready`/`web-ready`/`dry-run`/`failed`) on every terminal path — fresh success, resume success, web-only success, dry-run, and `die()` failure. Records are built from `_new_run_record()` so every line carries the FULL `RUN_LOG_FIELDS` key set (`timestamp`, `status`, `episode_uri`, `title`, `voice`, `voice_mode`, `chapter_count`, `duration_s`, `segment_count`, `workdir`, `manifest_path`, `error_message`, `git_sha`, `loudnorm`, `pruned_workdirs`, `pruned_episodes`, `r2_status`, `resumed`, `preflight`, `abandoned_episodes`, `mp3_url`); missing values are `null`, never absent, so the file parses line-by-line in `jq`/pandas — which is why a new field is APPENDED to `RUN_LOG_FIELDS` and left null on the paths that don't set it, never added ad hoc at one call site. **Never** route this through `_atomic_write_text` (that replaces the file → clobbers history to one line) and never let a log-write failure sink a run (it's best-effort, `try/except`). Writes are gated on `_RUN_CTX` being non-None (set only by `main()`), so direct `die()`/`run_selftest()` calls don't touch the real log. Loudnorm LUFS lands here via `parse_loudnorm` (non-finite `-inf`/`inf` → `null`, never a non-JSON `Infinity`). `--selftest` is **not** a run and writes no record.
- **`--prune-workdirs N` is destructive — its guards are load-bearing.** It deletes directories under `TMP_BASE` (= `tempfile.gettempdir()`, i.e. `$TMPDIR` on macOS, NOT a hardcoded `/tmp`). Every guard exists to make a wrong deletion impossible and must be preserved: name must start with `WORKDIR_PREFIX` (`daily-podcast-`); must be a real directory directly under `TMP_BASE`, **never a symlink** (no following links out of the tree); older than `N` days by mtime; `N <= 0` is refused (so the flag can never mean "delete everything"); and the **active workdir is excluded by resolved path** (a per-date resume can match the glob — never delete the dir the run is using). It's best-effort: a delete error on one dir is logged and skipped. The auto-delete-on-success path (`main()`, gated on `auto_workdir and not --keep-workdir`) only ever removes a *fresh-success* auto workdir — an explicit `--workdir` is always kept (it backs the documented resume/no-op path) and a failed run keeps its workdir for debugging.
- **Episode-cap auto-prune is destructive and irreversible — its guards mirror `--prune-workdirs`.** When `upload()` hits a confirmed episode-cap 429, and `config.auto_prune_episodes` is true, `render.py` deletes the oldest episode(s) and retries the upload **once**. Deleting a published episode cannot be undone (episode metadata is immutable), so every guard is load-bearing: **opt-in, default off** (key absent → behaves exactly as today, i.e. fail with the improved diagnostic — `parse_s2s_error` surfacing the structured `error_code`/`message` instead of an empty `stderr:`); triggered **only on a confirmed cap 429** gated on the *parsed inner* `error_code == "RATE_LIMIT_EXCEEDED"` **and** `reason == "capacity"` (`_is_cap_error`), never on a substring of a human line — and note save-to-spotify wraps this as a **nested string** `{"error": "API error (429): {...}"}`, so parsing is two stages (see `parse_s2s_error`); **bounded** by `max_prune_per_run` (default 1, `<= 0` refused like `--prune-workdirs N <= 0`) so a misparse can never walk the show; **scoped** to the configured `show_id` (list is `episodes --show-id <id>`, never a last-created-show default); **tiered** selection (`select_episodes_to_prune`) prefers `FAILED` episodes — matched **explicitly**, never "anything != READY", so an in-flight `NOT_READY` episode from a concurrent run is never touched — then oldest-by-`created_at`; **skips unparseable `created_at`** (never guesses age, same no-data-loss posture as `covered.json` pruning) and anything created at/after run start (this run / concurrent runs); **`--dry-run` deletes nothing** (logs the plan); **retries the upload at most once** (a second 429 fails with the diagnostic, never a second prune); and **every deletion is logged** (`episode_uri` + `created_at` + `title`) to stdout and into the run record's `pruned_episodes` so a surprise deletion is always traceable. `covered.json` is deliberately **not** rewritten when an episode is pruned — its entries would point at a dead `episode_uri`, but dedup's job ("don't re-cover this URL") stays correct.

### The bloopers bin captures what the recovery used to destroy

The 2026-08-17 TTS degeneration is the only genuinely funny audio this pipeline has
produced, and the documented recovery for it — *"delete that `seg_NN.mp3` from the
workdir and re-run"* — deletes it. Stale workdirs empty themselves within days
regardless. `~/.config/daily-podcast/bloopers/` is the archive that outlives both;
the full table of triggers lives in [SKILL.md](skills/daily-podcast/SKILL.md#bloopers-bin-bloopers).
Four properties are load-bearing:

- **`capture_rate_bloopers` is called BEFORE `verify_artifact`, never after.** No
  branch — and crucially no `die()` — may come between measuring a segment and
  copying it out, because everything downstream of that `die()` is unrecoverable.
- **The `run-failed` sweep is suppressed when `classify_incident` says
  `tts-degeneration`.** The `gate` trigger already banked the precise offender with
  its rate evidence; sweeping would add the eleven clean segments beside it and bury
  the one clip worth keeping. Any *other* failure names no segment, so the sweep is
  the only thing that saves that audio — don't widen the suppression to all gate
  failures.
- **Capture never changes a run's exit code** (`write_run_log` / incident-report
  contract), and `--dry-run` banks nothing.
- **`NEAR_MISS_RATE_RATIO = 0.90` is a capture band, not a second gate.** It banks
  slow-but-passing segments — the gate only ever catches a gross derailment — and it
  must stay below the clean population (0.94x was the slowest clean segment on
  08-17; a live 8-segment rehearsal measured 0.934-1.054). Raise it and every run
  banks half its segments, which turns the archive into noise.

`speech_rate_rows` exists so the gate and the bin share ONE measurement;
`speech_rate_problems` formats its rejections from those rows and its wording is
still matched by `classify_incident` on the `"speech rate"` substring.

### The "house" voice is `ref_audio` cloning, not VoiceDesign

This is the most important design decision in the project and it's load-bearing for every episode. See [docs/durable-voices.md](docs/durable-voices.md) for the full rationale — short version: VoiceDesign drifts ~2.5% in pacing and noticeably in timbre across runs; `ref_audio` cloning is stable. The locked house voice lives in [skills/daily-podcast/refs/house_voice.wav](skills/daily-podcast/refs/house_voice.wav) and its transcript in `refs/house_voice.txt`.

Voice precedence in [render.py](skills/daily-podcast/render.py) (see `main()` around the `voice_instruct`/`ref_audio` resolution):
1. `voice_instruct` in the manifest → VoiceDesign mode (explicit override; lets you A/B against the house voice without unwiring it)
2. `voice: "house"` (default) → Base model + `ref_audio` clone of the bundled clip
3. `voice: "random"` → random pick from `VOICES` preset list
4. `voice: "<preset>"` → that preset name

Don't add a fourth mode without updating SKILL.md and [docs/durable-voices.md](docs/durable-voices.md) — the docs promise these four and only these four.

### Configuration surface

User-level config sits outside the repo at `~/.config/daily-podcast/`:

- `config.json` — `show_id`, `show_name`, `host_name`, `opml_files`, `lookback_hours`, `target_item_count`, and the opt-in episode-cap prune keys `auto_prune_episodes` (default `false`) / `max_prune_per_run` (default `1`, `<= 0` refused). Loaded by `render.py` and `orchestrate.py`.
- `covered.json` — URL → `{date, episode_uri}` dedup log. Written by `render.py` only on successful upload. Treat malformed JSON as `{}` rather than failing the run. Pruned to a 180-day retention window (`COVERED_RETENTION_DAYS`) on each write so it stays bounded; entries with a missing/malformed `date` are retained.
- `runs.jsonl` — append-only JSONL operational log, one record per run (see the run-log invariant above). Best-effort observability; not load-bearing for any pipeline decision. Retention is the operator's job (≈ one line/day).
- `feed_usage.json` — `{feed_name: last_used_date}` map written by `orchestrate.py` after each successful real run. Drives the variety penalty so the same feed doesn't dominate consecutive episodes.
- `bloopers/` — the bloopers bin (#169): `clips/<sha16>.mp3` plus an append-only `index.jsonl`, one full-key-set row per clip (`BLOOPER_FIELDS`). Written by `render.py` on three triggers and by `bloopers.py mark` on the fourth. **Nothing in a run reads it back** — it is write-only until a meta-episode is cut from it by hand, so it is never load-bearing for a pipeline decision. Not pruned: an archive that deletes its oldest material defeats its own purpose, and the sole growth path is roughly a dozen ~1 MB segments per failed run.
- `dropped.jsonl` — append-only JSONL log written by `orchestrate.py` for every item that was blocked, refused, timed out, errored, or hit an auth failure. One record per dropped item: `{timestamp, run_date, feed_name, url, reason, detail}` (`reason` ∈ `refused`/`blocked`/`auth`/`timeout`/`error`). Observability-only; never affects pipeline decisions — except that the systemic `auth` case (zero survivors) drives the fail-fast diagnostic noted in the orchestrator invariant above.

All are documented in [SKILL.md](skills/daily-podcast/SKILL.md#show--dedup-config) and [README.md](README.md#setup).

## Runtime dependencies

Hard requirements that must be present on the host (not pip-installable workarounds):

- `save-to-spotify` CLI on `PATH`, authenticated. Every `run([...])` for `save-to-spotify` assumes this.
- `ffmpeg` + `ffprobe` on `PATH`. Concat + loudnorm + silence generation all shell out.
- Apple Silicon Mac (the cover uses `/System/Library/Fonts/Supplemental/Futura.ttc` directly; Qwen3-TTS via MLX needs Metal). The Futura path is a portability hazard — if you ever move this off macOS, change `build_cover`'s font resolution before anything else.
- Python 3.10+ with `mlx-audio`, `soundfile`, `mutagen`, `Pillow`. The headless prompt additionally needs `feedparser` (it self-installs if missing).

### `save-to-spotify` 0.2.0 quirks (verified 2026-08-22)

Diagnostic gotchas, not runtime issues — `render.py` works correctly against this CLI. They mainly cost time when a human or agent is verifying server state by hand. Upgrade in place with `save-to-spotify update` (the binary lives at `~/.local/bin/save-to-spotify`, installed by the upstream curl-bash script; it self-downloads the darwin-arm64 zip and checksum-verifies).

- **`timeline get` takes the episode id POSITIONALLY and has no `--episode-id` flag** — unlike `timeline set`, which requires one. Passing `--episode-id` to `get` makes the CLI swallow the flag token as the id and look up an episode literally named `--episode-id`, so you get a misleading `API error (404): RESOURCE_NOT_FOUND / "The specified episode was not found"` (exit 1) against an episode that exists. Without `--show-id` the same mistake surfaces honestly as `episode --episode-id not found in any of your shows`. Correct form: `save-to-spotify --json timeline get <episode_id> --show-id <show_id>`.
- **`timeline get` no longer echoes link URLs.** Since 0.1.2 (upstream #9) a `link` item comes back as `{"companion_uri": "time-synced:companion-external-link:<sha256>", "start_time_ms", "duration_ms"}` — the `url` you set is gone. You can confirm a link exists and when it fires, but you cannot read its destination back off the server; compare against the `timeline.json` in the run's workdir instead. This is the constraint to work around when debugging #25.
- **`--json timeline set` returns `{"items":[]}` even on success.** The items aren't echoed back; verify via `timeline get` (positional form) instead. `render.py` only checks for the `error` key, so this doesn't break the pipeline.
- **Every invocation writes a `<claude-code-hint v="1" type="plugin" .../>` line to STDERR**, on success and on failure alike. Errors still go to STDOUT as JSON, so stderr is now boilerplate rather than a diagnostic: never report a save-to-spotify stderr verbatim as the cause of a failure. `_shows_failure_detail` and `_command_failed_message` both prefer the stdout payload for this reason — an expired token surfaces as `` `shows` exited 1: API error (401): ``, not as the plugin advert. This also retires the "transient upload failure has EMPTY stderr" tell: stderr is never empty any more.

## Editing conventions specific to this repo

- **Keep `render.py` single-file.** It's deliberately not split into a package — the skill ships as a flat directory and the prompt at `prompts/daily.md` resolves its path via `${CLAUDE_PLUGIN_ROOT}/skills/daily-podcast/render.py`. Don't introduce sibling modules without also updating that resolution path.
- **`bloopers.py` is a maintenance CLI; nothing in a run calls it.** Same sibling-module precedent as `retitle.py` — it imports `render` (calling `render.run`, not a bare `run`, so the seam stays patchable) rather than duplicating the bin-writing code, which keeps `render.py` single-file and the index schema in one place. Unlike the automatic captures it *raises* on a bad timecode or a missing file: it is a human at a prompt, and the best-effort contract protects runs, not interactive commands. Reading the bin back is deliberately unimplemented — it is one JSONL file and `jq` does it better.
- **`retitle.py` is a maintenance CLI that writes to a PUBLIC feed — its guards are load-bearing.** It back-fills #139's topical titles onto already-published R2 manifest entries (#144). Nothing in a run calls it. Preserve all of: dry run is the **default** and `--apply` is the opt-in; `assert_title_only` refuses any write where a field other than `title` moved or the slug sequence changed (a manifest that fails cortech.online's `episodeSchema` empties the **entire** public feed, silently); `retitle_entries` re-proves per entry that `slug_for_date(pubDate)` reproduces the slug, because the retitle is guid-neutral only while that holds (#128) — a moved guid duplicates a published episode on Spotify; the title is composed by `orchestrate.episode_title`, never re-implemented, so #144 applies #139's format rather than inventing a second one; the topic phrases stay **data** (`backfill_topics.json`, pinned against `tests/data/published_slugs.tsv`), which is what makes a re-run idempotent and the copy reviewable before publication; it deliberately does **not** route through `upsert_manifest` (that re-sorts and caps to 200 — right for adding an episode, wrong for rewriting in place, #124); and `--apply` must fire the Pages deploy hook via `resolve_pages_hook_url`, because cortech.online is a static build that reads the manifest at build time and nothing on its side rebuilds on a schedule. Covers are deliberately not regenerated — the reasoning is in the module docstring; revisit it there rather than silently changing the answer.
- **Comments in `render.py` should explain the *why*, not the *what*.** The existing comments on `HOUSE_VOICE_INSTRUCT`, `MIN_CHAPTER_GAP_MS`, and `LAST_SILENCE_MS` are the model: each captures a constraint or a piece of history that's not obvious from the code.
- **The unattended run procedure has exactly ONE home: SKILL.md's *"Unattended daily run"* section.** `prompts/daily.md` is a stub that points there, and a scheduler's prompt must be a *trigger* ("invoke the skill, follow that section"), never a copy of the steps. This rule exists because three copies drifted and production silently ran a months-old fork missing the content-policy guidance, the `${CLAUDE_PLUGIN_ROOT}` pinning, and the `r2=` reporting field. A test (`test_daily_prompt_stays_a_stub`) fails if the stub starts re-inlining the procedure. Never "helpfully" re-inline it for self-containment.
