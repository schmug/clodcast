# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A Claude Code **plugin** (manifest at [.claude-plugin/plugin.json](.claude-plugin/plugin.json)) that ships a single skill, `daily-podcast`, at [skills/daily-podcast/](skills/daily-podcast/). The skill turns a list of saved URLs into a fully-produced Spotify episode in one pass, on top of the external `save-to-spotify` CLI.

There is no test suite, no linter, no build step. The "build" is `python3 render.py`. The contract between the skill prose ([SKILL.md](skills/daily-podcast/SKILL.md)) and the executable ([render.py](skills/daily-podcast/render.py)) is the manifest schema described in both — keep them in sync when changing either.

## How development works here

**Always test changes via `--dry-run` first.** Real runs upload to Spotify and mutate the user's `covered.json` dedup log. Dry-run produces the mp3, cover, and `timeline.json` locally and prints paths, skipping the `save-to-spotify upload` and `timeline set` calls.

```bash
python3 skills/daily-podcast/render.py --manifest /tmp/manifest.json --dry-run
```

For a fast iteration loop on script-template or formatting changes, write a minimal manifest with one or two short segments and dry-run against it. The Qwen3-TTS model load is ~10-15 s on first invocation; subsequent segments stream at ~4-5x realtime on Apple Silicon.

To exercise the headless path, pipe the prompt to a fresh Claude session:

```bash
claude -p "$(cat skills/daily-podcast/prompts/daily.md)"
```

The headless prompt's final stdout is a single line — `SHIPPED <uri> ...` or `FAILED <reason>`. Don't change that contract; schedulers parse it.

## Architecture: the big picture

Three documents are load-bearing; read all of them before changing behavior:

1. **[skills/daily-podcast/SKILL.md](skills/daily-podcast/SKILL.md)** — the script template, voice rules, chapter-duration guardrail, manifest schema. This is what Claude reads when the skill activates.
2. **[skills/daily-podcast/render.py](skills/daily-podcast/render.py)** — the manifest → episode driver. Single file, ~590 lines, no internal modules.
3. **[skills/daily-podcast/prompts/daily.md](skills/daily-podcast/prompts/daily.md)** — the `claude -p` prompt that drives an unattended end-to-end run (OPML → curation → manifest → render).

`render.py` is intentionally "dumb": it consumes a manifest that already has the segments written and only handles TTS, concat, cover, upload, timeline, poll, and dedup-log update. Anything script-shaped (curation, fetching, segment writing, self-critique) lives in the skill prose / headless prompt — i.e., is Claude's job, not the renderer's.

### Invariants the renderer enforces

These are subtle and easy to break. Preserve them or the produced episode is rejected by Spotify or sounds wrong.

- **Strict 1:1 segment ↔ source mapping.** `build_timeline_and_description` assumes every non-null `source_url` becomes a `link` companion to the chapter at that index. Don't merge segments or attach multiple URLs to one segment.
- **Max 3 chapters under 30 seconds.** Spotify rejects timelines that violate this. `plan_silences` auto-pads trailing silence after short segments up to a 12 s cap; if more padding is needed it dies with a script-rewrite error. Don't lower the cap to "make it work" — the script is the problem.
- **The last segment gets `LAST_SILENCE_MS = 0` trailing silence.** Padding the tail breaks chapter math (`last_chapter_start_ms >= episode_duration_ms` is fatal).
- **`covered.json` is only written after `poll_ready` returns READY.** Don't move the `save_covered` call earlier; a failed upload must leave the dedup log untouched so the next run retries those URLs.
- **MP3 is mono 44.1k throughout.** Every ffmpeg invocation re-asserts this. Concat-protocol is fragile across mismatched sample rates / channels; don't relax it.

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

- `config.json` — `show_id`, `show_name`, `host_name`, `opml_files`, `lookback_hours`, `target_item_count`. Loaded by `render.py` and the headless prompt.
- `covered.json` — URL → `{date, episode_uri}` dedup log. Written by `render.py` only on successful upload. Treat malformed JSON as `{}` rather than failing the run.

Both are documented in [SKILL.md](skills/daily-podcast/SKILL.md#show--dedup-config) and [README.md](README.md#setup).

## Runtime dependencies

Hard requirements that must be present on the host (not pip-installable workarounds):

- `save-to-spotify` CLI on `PATH`, authenticated. Every `run([...])` for `save-to-spotify` assumes this.
- `ffmpeg` + `ffprobe` on `PATH`. Concat + loudnorm + silence generation all shell out.
- Apple Silicon Mac (the cover uses `/System/Library/Fonts/Supplemental/Futura.ttc` directly; Qwen3-TTS via MLX needs Metal). The Futura path is a portability hazard — if you ever move this off macOS, change `build_cover`'s font resolution before anything else.
- Python 3.10+ with `mlx-audio`, `soundfile`, `mutagen`, `Pillow`. The headless prompt additionally needs `feedparser` (it self-installs if missing).

### `save-to-spotify` 0.1.1 quirks (verified 2026-05-23)

These are diagnostic gotchas, not runtime issues — `render.py` itself works correctly against this CLI. They mainly affect humans verifying server state.

- **`timeline get --episode-id <id>` returns a spurious `RESOURCE_NOT_FOUND` 404** even when the timeline exists. Use the positional form to inspect server state: `save-to-spotify --json timeline get <episode_id> --show-id <show_id>`. The positional form returns the full timeline including `link` companions.
- **`--json timeline set` returns `{"items":[]}` even on success.** The items aren't echoed back; verify via `timeline get` (positional form) instead. `render.py` only checks for the `error` key, so this doesn't break the pipeline.
- **The passive update check advertises a sentinel `9.9.9` release that doesn't exist.** `save-to-spotify update` correctly reports "Already up to date (0.1.1)". Set `SAVE_TO_SPOTIFY_NO_UPDATE_CHECK=1` to silence the nag.

## Editing conventions specific to this repo

- **Keep `render.py` single-file.** It's deliberately not split into a package — the skill ships as a flat directory and the prompt at `prompts/daily.md` resolves its path via `${CLAUDE_PLUGIN_ROOT}/skills/daily-podcast/render.py`. Don't introduce sibling modules without also updating that resolution path.
- **Comments in `render.py` should explain the *why*, not the *what*.** The existing comments on `HOUSE_VOICE_INSTRUCT`, `TARGET_CHAPTER_MS`, and `LAST_SILENCE_MS` are the model: each captures a constraint or a piece of history that's not obvious from the code.
- **When you change the manifest schema or the script template, update both [SKILL.md](skills/daily-podcast/SKILL.md) and [prompts/daily.md](skills/daily-podcast/prompts/daily.md).** The headless prompt repeats the template inline because it runs without re-reading the skill — but the skill is still source of truth, so any divergence is a bug.
