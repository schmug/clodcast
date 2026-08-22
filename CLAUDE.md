# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A Claude Code **plugin** (manifest at [.claude-plugin/plugin.json](.claude-plugin/plugin.json)) that ships a single skill, `daily-podcast`, at [skills/daily-podcast/](skills/daily-podcast/). The skill turns a list of saved URLs into a fully-produced Spotify episode in one pass, on top of the external `save-to-spotify` CLI.

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

- **Strict 1:1 segment ↔ source mapping.** `build_timeline_and_description` assumes every non-null `source_url` becomes a `link` companion to the chapter at that index. Don't merge segments or attach multiple URLs to one segment.
- **Consecutive chapter starts must be at least `MIN_CHAPTER_GAP_MS` = 5 s apart; the final chapter is exempt.** This is the *only* chapter-duration rule the platform still has, and it governs chapter **spacing** (`start_time_ms` deltas), not inter-chapter silence — so `DEFAULT_SILENCE_MS = 800` is unrelated to it and stays. `plan_silences` pads a segment's trailing silence only far enough to reach that 5 s floor, which real segments never trigger; `verify_artifact` re-checks it at the artifact gate. **Retired invariant (verified 2026-08-22):** there used to be a "max 3 chapters under 30 s" rule here, and `plan_silences` padded toward a 30.5 s target — up to 12 s of dead air per chapter — dying with a script-rewrite error when it ran out of room. Upstream PR #44 (save-to-spotify v0.1.4) dropped that cap. Confirmed against CLI 0.2.0 on a throwaway show: a timeline with 11 of 12 chapters under 30 s was accepted by `timeline set` and processed to `READY`, while a 3 s gap was still refused with `chapter at index 0 must be at least 5s long`. Don't reintroduce a 30 s target.
- **The last segment gets `LAST_SILENCE_MS = 0` trailing silence.** Padding the tail breaks chapter math (`last_chapter_start_ms >= episode_duration_ms` is fatal).
- **`covered.json` is only written after `poll_ready` returns READY.** Don't move the `save_covered` call earlier; a failed upload must leave the dedup log untouched so the next run retries those URLs.
- **`covered.json` is pruned to a `COVERED_RETENTION_DAYS = 180` window on every `save_covered`.** Entries whose ISO `date` is strictly older than today − 180 days are dropped before the atomic write; entries with a missing or non-ISO `date` are kept (no data loss on schema drift). 180 days is far larger than the curation lookback (`lookback_hours`), so pruning never re-exposes a URL that dedup still cares about. Don't shorten the window into the lookback range, and don't prune in `load_covered` — the read contract returns the file as-is.
- **The HTML description is capped at `SPOTIFY_SUMMARY_MAX_CHARS = 4000`.** `build_timeline_and_description` drops whole trailing chapter `<p>` blocks (longest-suffix-first, never mid-tag) until the summary fits, always preserving the leading summary `<p>`. The timeline JSON is unaffected — every audio chapter still exists; only the show-notes listing shrinks. Don't ellipsize inside a block or cut markup mid-tag.
- **A successful `upload()` writes `<workdir>/uploaded.json` before the `set_timeline`/`poll_ready` tail.** This is the resume marker: re-running with the same explicit `--workdir` skips re-upload and re-runs only the idempotent tail (`set_timeline`/`poll_ready`/`save_covered`). Don't write it before `upload()` succeeds, and don't gate dedup on it — `covered.json` is still only written after READY. Resume is a manual, same-workdir recovery path; the cron's cross-day duplicate risk (per-date workdirs) is deferred to the in-flight-log work.
- **MP3 is mono 44.1k throughout.** Every ffmpeg invocation re-asserts this. Concat-protocol is fragile across mismatched sample rates / channels; don't relax it.
- **The run log (`~/.config/daily-podcast/runs.jsonl`) is append-only and has a stable schema.** `write_run_log` appends one JSON line per run (`status` = `ready`/`dry-run`/`failed`) on every terminal path — fresh success, resume success, dry-run, and `die()` failure. Records are built from `_new_run_record()` so every line carries the FULL `RUN_LOG_FIELDS` key set (`timestamp`, `status`, `episode_uri`, `title`, `voice`, `voice_mode`, `chapter_count`, `duration_s`, `segment_count`, `workdir`, `manifest_path`, `error_message`, `git_sha`, `loudnorm`, `pruned_workdirs`, `pruned_episodes`, `r2_status`, `resumed`, `preflight`, `abandoned_episodes`); missing values are `null`, never absent, so the file parses line-by-line in `jq`/pandas. **Never** route this through `_atomic_write_text` (that replaces the file → clobbers history to one line) and never let a log-write failure sink a run (it's best-effort, `try/except`). Writes are gated on `_RUN_CTX` being non-None (set only by `main()`), so direct `die()`/`run_selftest()` calls don't touch the real log. Loudnorm LUFS lands here via `parse_loudnorm` (non-finite `-inf`/`inf` → `null`, never a non-JSON `Infinity`). `--selftest` is **not** a run and writes no record.
- **`--prune-workdirs N` is destructive — its guards are load-bearing.** It deletes directories under `TMP_BASE` (= `tempfile.gettempdir()`, i.e. `$TMPDIR` on macOS, NOT a hardcoded `/tmp`). Every guard exists to make a wrong deletion impossible and must be preserved: name must start with `WORKDIR_PREFIX` (`daily-podcast-`); must be a real directory directly under `TMP_BASE`, **never a symlink** (no following links out of the tree); older than `N` days by mtime; `N <= 0` is refused (so the flag can never mean "delete everything"); and the **active workdir is excluded by resolved path** (a per-date resume can match the glob — never delete the dir the run is using). It's best-effort: a delete error on one dir is logged and skipped. The auto-delete-on-success path (`main()`, gated on `auto_workdir and not --keep-workdir`) only ever removes a *fresh-success* auto workdir — an explicit `--workdir` is always kept (it backs the documented resume/no-op path) and a failed run keeps its workdir for debugging.
- **Episode-cap auto-prune is destructive and irreversible — its guards mirror `--prune-workdirs`.** When `upload()` hits a confirmed episode-cap 429, and `config.auto_prune_episodes` is true, `render.py` deletes the oldest episode(s) and retries the upload **once**. Deleting a published episode cannot be undone (episode metadata is immutable), so every guard is load-bearing: **opt-in, default off** (key absent → behaves exactly as today, i.e. fail with the improved diagnostic — `parse_s2s_error` surfacing the structured `error_code`/`message` instead of an empty `stderr:`); triggered **only on a confirmed cap 429** gated on the *parsed inner* `error_code == "RATE_LIMIT_EXCEEDED"` **and** `reason == "capacity"` (`_is_cap_error`), never on a substring of a human line — and note save-to-spotify wraps this as a **nested string** `{"error": "API error (429): {...}"}`, so parsing is two stages (see `parse_s2s_error`); **bounded** by `max_prune_per_run` (default 1, `<= 0` refused like `--prune-workdirs N <= 0`) so a misparse can never walk the show; **scoped** to the configured `show_id` (list is `episodes --show-id <id>`, never a last-created-show default); **tiered** selection (`select_episodes_to_prune`) prefers `FAILED` episodes — matched **explicitly**, never "anything != READY", so an in-flight `NOT_READY` episode from a concurrent run is never touched — then oldest-by-`created_at`; **skips unparseable `created_at`** (never guesses age, same no-data-loss posture as `covered.json` pruning) and anything created at/after run start (this run / concurrent runs); **`--dry-run` deletes nothing** (logs the plan); **retries the upload at most once** (a second 429 fails with the diagnostic, never a second prune); and **every deletion is logged** (`episode_uri` + `created_at` + `title`) to stdout and into the run record's `pruned_episodes` so a surprise deletion is always traceable. `covered.json` is deliberately **not** rewritten when an episode is pruned — its entries would point at a dead `episode_uri`, but dedup's job ("don't re-cover this URL") stays correct.

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
- **Comments in `render.py` should explain the *why*, not the *what*.** The existing comments on `HOUSE_VOICE_INSTRUCT`, `MIN_CHAPTER_GAP_MS`, and `LAST_SILENCE_MS` are the model: each captures a constraint or a piece of history that's not obvious from the code.
- **The unattended run procedure has exactly ONE home: SKILL.md's *"Unattended daily run"* section.** `prompts/daily.md` is a stub that points there, and a scheduler's prompt must be a *trigger* ("invoke the skill, follow that section"), never a copy of the steps. This rule exists because three copies drifted and production silently ran a months-old fork missing the content-policy guidance, the `${CLAUDE_PLUGIN_ROOT}` pinning, and the `r2=` reporting field. A test (`test_daily_prompt_stays_a_stub`) fails if the stub starts re-inlining the procedure. Never "helpfully" re-inline it for self-containment.
