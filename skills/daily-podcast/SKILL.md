---
id: daily-podcast
name: daily-podcast
description: Use when the user asks to ship a daily digest podcast — turns a list of saved items (URLs / articles) into a fully-produced Spotify episode using Qwen3-TTS, a deterministic script template, and the save-to-spotify CLI. Skips the standard production interview because defaults are pre-set.
enabled: true
---

# Daily Podcast

Turn a list of saved items into a finished Spotify episode in one pass. This skill is the automated counterpart to the [save-to-spotify](https://github.com/spotify/save-to-spotify) skill — same production rules, no interview, deterministic script template, dated cover.

Depends on the `save-to-spotify` CLI being installed and authenticated. Install it from <https://saveto.spotify.com/install.sh> and run `save-to-spotify auth login` once.

**Trigger phrases:** "ship today's podcast", "make the daily digest", "run the daily routine", "podcast from this list of URLs".

## Layout

This skill ships an executable `render.py` and a headless prompt. References in this document are relative to the skill directory:

- `./render.py` — the manifest → episode driver (audio render, cover, upload, timeline, polling)
- `./orchestrate.py` — the unattended entry point for scheduled runs (deterministic metadata-only curation + one isolated `claude -p` per item)
- `./prompts/daily.md` — **deprecated** reference for the segment/voice rules and manifest shape; no longer the cron entry point (use `./orchestrate.py`)

## Input

Two forms accepted. Both resolve to a `manifest.json` consumed by `render.py`.

### Form 1 — items list (`items.json`)

User supplies saved items; this skill writes the script and the manifest.

```json
{
  "date": "2026-05-22",                    // optional; defaults to today
  "items": [
    {"url": "https://example.com/post",    // required
     "title": "Post title",                // optional; falls back to <title> from fetch
     "content": "pre-fetched body text",   // optional; if absent, WebFetch the URL
     "saved_at": "2026-05-22T10:00:00Z"}   // optional, informational
  ]
}
```

### Form 2 — pre-built manifest (`manifest.json`)

Already-written segments. Skip straight to rendering.

```json
{
  "title": "Daily Digest - May 22, 2026",
  "summary": "Today's one-sentence hook.",
  "show_id": "spotify:show:...",
  "date": "2026-05-22",                    // optional ISO date; stamps the cover. Omit to use today (re-renders of a dated manifest reproduce its date)
  "voice": "house",                        // default; or "random" / preset name; set voice_instruct for custom VoiceDesign
  "segments": [
    {"text": "Intro segment...",            "source_url": null},
    {"text": "Item 1 segment, 600+ chars.", "source_url": "https://...", "source_title": "..."},
    {"text": "Outro segment...",            "source_url": null}
  ]
}
```

## Workflow

```
1. Load config           -> ~/.config/daily-podcast/config.json (show_id)
2. Load dedup log        -> ~/.config/daily-podcast/covered.json
3. Filter input items    -> drop URLs already in the log
4. Fetch missing content -> WebFetch for any item without `content`
5. Write script          -> intro + one segment per item + outro; per template below
6. Self-critique pass    -> tighten verbose segments; never reorder
7. Render manifest       -> hand to render.py
8. render.py             -> TTS + concat + loudnorm + cover + upload + timeline + poll
9. Update dedup log      -> render.py appends covered URLs with today's date + episode URI
10. Report               -> single line: episode URI + voice used + chapter count
```

## Script template

**Intro** (~400 chars, one paragraph): "Today's digest for [date in long form]. [N] items today, covering [2-4 word theme list]. Here's the rundown."

**Per-item segment** (≥600 chars; aim for 700-900):
- Lead with the headline or one-sentence framing
- 3-4 sentences of substance: what, why, key detail
- End on the last sentence of substantive analysis. Never verbally reference the source URL, the show notes, the description, or "the link" — source attribution is handled non-verbally by the per-segment `link` companion in the timeline and by the timestamped chapter links in the HTML description.
- Never read URLs aloud
- One source per segment — strict 1:1 mapping

**Outro** (~300 chars): brief sign-off, no new content. Same rule as segments: don't tell listeners to check the show notes or description.

### Rules
- Convert relative dates from sources to absolute (today's date is available via the system clock)
- Strip markdown, code blocks, emoji, hashtags before TTS
- Numbers under 10 in words; abbreviations expanded ("D R I" not "DRI")
- "CLAUDE dot md" not "CLAUDE.md"
- No em dashes — TTS encoding flakes; use hyphens
- Use 1-2 transition phrases between segments ("Next up", "Moving on", "Also today")
- This is a news digest. Cover security, breach, and research stories at a reporting altitude — what was disclosed, who is affected, the response. Reporting on a disclosed vulnerability or breach is ordinary tech journalism; cover it confidently. Never write exploit steps, payloads, working commands, or attacker how-to; if an item can't be made substantive without them, it doesn't belong in the episode. (The `prompts/daily.md` curation and fetch steps keep coverage at this altitude; this is the writing-side backstop.)

> **Defense in depth:** `render.py` validates the manifest structure (failing fast with a per-field message before the model loads) and re-strips TTS-hostile characters from every segment — em/en dashes, smart quotes, code fences + backticks, leading markdown headings, and bare URLs — regardless of what the caller wrote. It does *not* do the stylistic rules above (numbers-to-words, abbreviation spacing, "CLAUDE dot md") — those stay the writer's job. Set `"raw_text": true` in the manifest to skip normalization (e.g. text pre-formatted for a different TTS).

## Voice selection

The default is the **locked house voice** — `ref_audio` cloning from a ~22-second reference clip. The Base 1.7B model regenerates that voice's timbre and prosody for any new text, so the voice stays consistent across episodes.

The bundled default lives at `refs/house_voice.{wav,txt}` in the skill directory. On the first `voice: "house"` render, `render.py` copies it to `~/.config/daily-podcast/voices/house.{wav,txt}` and reads from there forever after — so plugin updates can't overwrite a customized voice.

Manifest options:
- `"voice": "house"` (default) — Base model + `~/.config/daily-podcast/voices/house.{wav,txt}` (seeded from bundle on first run)
- `"voice": "random"` — preset rotation over `[Ryan, Aiden, Ethan, Chelsie]`
- `"voice": "Ryan"` (or any preset) — single fixed preset
- `"voice_instruct": "..."` — VoiceDesign mode, full override; `voice` becomes a label

The bundled house clip is one good render of a VoiceDesign instruct (`HOUSE_VOICE_INSTRUCT`, kept in `render.py` for reference) — mature female, even prosody, bright but human, not performative. To replace the house voice:

1. Capture a new ~20-30 second reference clip (any TTS or human recording)
2. Save it to `~/.config/daily-podcast/voices/house.wav` (PCM_16, mono, 24 kHz preferred)
3. Update `~/.config/daily-podcast/voices/house.txt` with the exact transcript
4. Done — every subsequent `voice: "house"` render uses the new clip

`ref_audio` precedence: if `voice_instruct` is also set in a manifest, the explicit instruct wins (so you can A/B against the house voice without unwiring it).

Report the voice in the final summary so the user knows which one ran.

## Chapter-duration guardrail

Spotify rejects timelines where >3 chapters are under 30 seconds. Qwen3 reads ~4.6x realtime — segments under 600 chars routinely land below 30s. `render.py` auto-pads silence after short segments to satisfy the constraint. Aim for 600+ chars per segment to avoid awkward gaps.

## Show + dedup config

```jsonc
// ~/.config/daily-podcast/config.json
{
  "show_id": "spotify:show:...",       // required; one-time setup
  "show_name": "Daily Digest",
  "host_name": "Cory",
  "opml_files": ["/path/to/feeds.opml"], // optional; used by prompts/daily.md
  "lookback_hours": 24,                  // optional; default 24
  "target_item_count": 10,               // optional; default 10
  "auto_prune_episodes": false,          // optional; default false. When true, an upload
                                         //   that hits the show's episode cap (429
                                         //   RATE_LIMIT_EXCEEDED / capacity) prunes the
                                         //   oldest episode(s) and retries the upload once.
  "max_prune_per_run": 1,                // optional; default 1. Hard ceiling on how many
                                         //   episodes an auto-prune may delete per run.
                                         //   <= 0 is refused (no prune). Deleting a
                                         //   published episode is irreversible.
  "r2_bucket": "clodcast",               // optional; enables the web feed (see below)
  "r2_public_base_url": "https://audio.cortech.online"  // optional; public URL for <slug>.mp3
}
```

```jsonc
// ~/.config/daily-podcast/covered.json — written by render.py on successful upload.
// Pruned to a 180-day retention window on each write (the `date` field drives this);
// entries with a missing/malformed `date` are kept.
{
  "https://example.com/post-1": {"date": "2026-05-22", "episode_uri": "spotify:episode:..."},
  "https://example.com/post-2": {"date": "2026-05-21", "episode_uri": "spotify:episode:..."}
}
```

`~/.config/daily-podcast/inflight.json` is a transient crash-recovery record (an episode that uploaded but hasn't reached `READY`+dedup yet) — written after `upload()` succeeds and cleared after dedup. It is **not** a second dedup source; `covered.json` stays authoritative. See [Automatic cron recovery](#automatic-cron-recovery-cross-day-workdir-independent) below.

### Episode-cap auto-prune (`auto_prune_episodes`)

A Spotify show has a hard episode cap. When `upload()` hits it, save-to-spotify returns a `429` with `error_code: RATE_LIMIT_EXCEEDED` / `reason: capacity`. By default `render.py` fails with that structured reason (so it's distinguishable from a transient upload flake, which surfaces the same non-zero exit). Set `auto_prune_episodes: true` to have the renderer instead delete the oldest episode(s) and retry the upload **once**. Deleting a published episode is **irreversible**, so the prune is deliberately conservative:

- **Bounded** by `max_prune_per_run` (default 1; `<= 0` is refused).
- **Tiered** selection: `FAILED` episodes first (they count against the cap but have no playable audio), then oldest by `created_at`. An in-flight `NOT_READY` episode is never selected, and an episode with a missing/malformed `created_at` is skipped rather than assumed oldest.
- **Scoped** to the configured `show_id`; never touches this run's own or a concurrent run's just-created episode.
- **`--dry-run` deletes nothing** — it logs what it *would* delete.
- Every deletion is logged (`episode_uri` + `created_at` + `title`) to stdout and recorded under `pruned_episodes` in `runs.jsonl`.

`covered.json` is intentionally left unchanged when an episode is pruned: its entries would point at a now-dead `episode_uri`, but dedup only needs "don't re-cover this URL", which stays correct.

### Run log (across-runs observability)

Every `render.py` run appends one JSON record to `~/.config/daily-podcast/runs.jsonl` — on success, on `--dry-run`, and on failure. Append-only (never rewritten); one line per day, so retention is the operator's job. Each record carries a **stable** key set (missing values are `null`, never absent) so the file parses cleanly line-by-line in `jq`/pandas:

```jsonc
// ~/.config/daily-podcast/runs.jsonl — one line per run
{
  "timestamp": "2026-06-03T06:00:12+00:00",  // ISO 8601 UTC
  "status": "ready",                         // "ready" | "dry-run" | "failed"
  "episode_uri": "spotify:episode:...",      // null unless ready
  "title": "Daily Digest - ...",
  "voice": "house", "voice_mode": "clone",
  "chapter_count": 6, "duration_s": 412.3, "segment_count": 6,
  "workdir": "/var/folders/.../T/daily-podcast-xxxx",
  "manifest_path": "/tmp/manifest.json",
  "error_message": null,                     // the die() message on failure
  "git_sha": "ea5e845",                      // of render.py (mtime fallback off-git)
  "loudnorm": {"input_i": -19.4, "output_i": -16.0, "output_tp": -1.5, "output_lra": 6.9},
  "pruned_workdirs": null,                    // {count, freed_bytes} when --prune-workdirs ran
  "r2_status": "published",                   // "published" | "skipped" | "failed" or null pre-publish (#48)
  "resumed": false
}
```

Sample queries:

```bash
# Every failure and its error
jq -r 'select(.status == "failed") | "\(.timestamp)  \(.error_message)"' ~/.config/daily-podcast/runs.jsonl
# Loudness drift over time (Spotify targets -16 LUFS)
jq -r 'select(.loudnorm) | "\(.timestamp)  \(.loudnorm.output_i)"' ~/.config/daily-podcast/runs.jsonl
# Which voice ran each day
jq -r '"\(.timestamp)  \(.voice) (\(.voice_mode))"' ~/.config/daily-podcast/runs.jsonl
```

First run with no `config.json`: ask the user whether to use an existing show (list via `save-to-spotify --json shows`) or create a new one, then persist the choice.

## Publishing to the web (Cloudflare R2)

Optional, additive. When R2 is configured, `render.py` also publishes each finished
episode to a Cloudflare R2 bucket *after* the Spotify upload reaches `READY`:

- `<bucket>/<slug>.mp3` — the episode audio (publicly fetchable at `r2_public_base_url`)
- `<bucket>/<slug>.jpg` — the cover (best-effort)
- `<bucket>/manifest.json` — a newest-first array of episode entries, capped at 200,
  conforming to cortech.online's `episodeSchema`. [cortech.online](https://github.com/schmug/cortech.online)
  reads this at build time and renders `/podcast/` plus an iTunes RSS feed at
  `/podcast/rss.xml`.

Each manifest entry carries both a Spotify-flavored `description` (HTML — `<p>summary</p>`
followed by one timestamped `<p>… - <a>source</a></p>` per chapter) **and** a clean
`summary` field (#45) so web/RSS consumers can render prose without HTML-stripping the
description. `summary` is **HTML-by-contract** (the user authored it), so a consumer
should still escape it as untrusted text rather than trusting it as guaranteed-plain.
`description` and `chapters[]` are unchanged — the `summary` field is purely additive.

This is strictly additive: **Spotify is the canonical artifact.** A publish never fails
the run, changes the exit code, or rolls back `covered.json`. The final JSON line reports
a 3-state `"r2_status"` (#48): `"published"` (uploaded), `"skipped"` (R2 not configured —
a benign no-op), or `"failed"` (configured but the upload errored — the alarming case an
operator should notice; the episode is still live on Spotify). `--dry-run` skips the
publish and prints where it *would* have gone (`r2_would_publish`).

**Credentials never go in `config.json`.** Read from env (preferred for cron) or an
optional `~/.config/daily-podcast/secrets.json` (mode 0600):

```bash
export R2_ACCESS_KEY_ID=...      # R2 API token access key
export R2_SECRET_ACCESS_KEY=...  # R2 API token secret
export R2_ACCOUNT_ID=...         # Cloudflare account ID (the R2 S3 endpoint host)
# optional: export PAGES_DEPLOY_HOOK_URL=...  # POSTed after publish to rebuild the site
```

`r2_bucket` / `r2_public_base_url` live in `config.json` (or `R2_BUCKET` /
`R2_PUBLIC_BASE_URL` env overrides). All five must resolve or the publish no-ops.

**Pages deploy hook (optional, independent of the five above).** After a successful
publish, `render.py` POSTs `PAGES_DEPLOY_HOOK_URL` to rebuild the site. It resolves
first-non-empty-wins across three homes: env → `secrets.json`
(`"PAGES_DEPLOY_HOOK_URL"`) → `config.json` (`"pages_deploy_hook_url"`). A scheduled
(launchd/cron) run never inherits the interactive shell env, so the durable home is
`secrets.json` (0600) — also preferred because the URL can trigger builds;
`config.json` is the shareable-file convenience. Unset everywhere → no hook fired
(the pre-existing behaviour). `--dry-run` never fires it.

> Resume note: the R2 publish runs on both the fresh run **and** the `--workdir`
> resume path (`_resume`). A resumed episode (e.g. one that first failed at
> `poll_ready` and was recovered) is back-filled to R2 too, so it still lands on the
> web feed (#40). Resume stays `config.json`-free: it resolves R2 config from env /
> `secrets.json` only (never `load_config`), and the publish is additive + non-fatal
> exactly as on the fresh path. (An older workdir from before this change that lacks
> `description.html` degrades to a skipped back-fill rather than aborting the resume.)

## Running the pipeline

Two entry points:

**Interactive (current session):** Claude writes the manifest in conversation, then runs:
```bash
python3 <skill-dir>/render.py --manifest manifest.json
```

**Headless (unattended schedule):** Use the orchestrator, which gathers + curates deterministically and summarizes each item in its own isolated `claude -p` subprocess:
```bash
python3 <skill-dir>/orchestrate.py
```
Final stdout is a single line: `SHIPPED <episode_uri> ...` or `FAILED <reason>`.

`prompts/daily.md` is kept as a deprecated reference for the segment/voice rules and the manifest shape, but is no longer the cron entry point.

### Orchestrator (unattended)

`orchestrate.py` is the unattended entry point for scheduled runs. Core invariant: **no LLM request ever holds more than one article body** — curation is deterministic metadata-only (feedparser titles, dates, summaries), and each ranked item is summarized by its own isolated `claude -p` subprocess. A per-item classifier block, timeout, or error drops only that item (logged to `dropped.jsonl`); the remaining items still ship.

Pipeline:
1. Parse OPML, fetch feeds — metadata only, no article bodies
2. Deterministic ranking: source tier × recency × concreteness, variety penalty (feeds used within 3 days are deprioritized), per-feed cap
3. Fan-out: one `claude -p prompts/summarize_item.md` per ranked item, concurrency-capped
4. Survivors (non-blocked items) assembled into a manifest and handed to `render.py`

**CLI flags** (for `orchestrate.py`):

| Flag | Purpose |
| --- | --- |
| `--dry-run` | Forward to `render.py --dry-run`; skip upload + `feed_usage.json` write |
| `--workdir PATH` | Use this directory for the manifest and render artifacts |
| `--limit N` | Cap items fanned out (useful for testing) |
| `--manifest-only` | Assemble the manifest then stop (no render/upload) |
| `--concurrency N` | Parallel `claude -p` calls (default: 3; wide fan-out can trip API rate limits) |

**State files written by the orchestrator** (under `~/.config/daily-podcast/`):

- `feed_usage.json` — `{feed_name: last_used_date}` map; drives the variety penalty so the same feed doesn't dominate back-to-back episodes. Updated only on a successful real (`ready`) run; `--dry-run` leaves it unchanged.
- `dropped.jsonl` — append-only JSONL record of every item that was blocked, refused, timed out, errored, or hit an auth failure during a run. One record per dropped item: `{timestamp, run_date, feed_name, url, reason, detail}` (`reason` ∈ `refused`/`blocked`/`auth`/`timeout`/`error`). Useful for diagnosing feed-level issues or cyber-content policy patterns; an all-`auth` night means child `claude -p` could not authenticate (see [Unattended runs need durable credentials](#unattended-runs-need-durable-credentials)).

Note: `orchestrate.py` does **not** accept `--selftest` or `--prune-workdirs` — those flags belong to `render.py`. For disk hygiene, call `render.py --prune-workdirs N` separately.

`render.py` exits non-zero with a diagnostic on any failure. Always check the exit code; do not assume success.

For testing without uploading, use `--dry-run` — produces the MP3, cover, and timeline.json locally and reports paths, but skips the `save-to-spotify upload` and `timeline set` calls.

### Unattended-run flags

| Flag | Purpose |
| --- | --- |
| `--selftest` | Pre-flight health check (no real run). Mutually exclusive with `--manifest`. |
| `--load-model` | With `--selftest`: also load the TTS model (slow; the most thorough check). |
| `--keep-workdir` | Keep the auto-created workdir after a successful run (default: delete it). |
| `--prune-workdirs N` | Before rendering, delete auto-created workdirs older than `N` days. |

**`--selftest`** runs an ordered set of checks (ffmpeg + ffprobe on PATH → `save-to-spotify --json shows` returns valid JSON → `config.json` parses with `show_id` → house-voice ref clip + transcript present), prints a pass/fail line each, then a JSON summary `{"status": "ok"/"failed", "checks": [...]}`. It exits `0` only if every check passes, non-zero otherwise — so a scheduler can gate on it:

```bash
python3 <skill-dir>/render.py --selftest || { echo "pre-flight failed" | mail -s "podcast down" you@example.com; exit 1; }
```

It finishes in under 5 seconds (no model load unless `--load-model`).

**Workdir hygiene.** Each run creates `<tmpdir>/daily-podcast-<random>` (the system temp dir — `$TMPDIR` on macOS, often `/tmp` on Linux). On a successful run with default flags the **auto-created** workdir is deleted (a failed run always keeps it for debugging; an explicit `--workdir` is never auto-deleted, since it backs the resume path). `--prune-workdirs N` separately sweeps any `daily-podcast-*` directory older than `N` days — it never deletes the active workdir, never follows symlinks, and refuses a non-positive `N`.

### Scheduled runs (cron / launchd)

Recommended unattended recipe: pre-flight with `render.py --selftest`, then run via the orchestrator. Add `render.py --prune-workdirs 7` for automatic disk hygiene.

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/clodcast"
# Pre-flight: bail loudly if deps/auth are broken BEFORE doing real work.
python3 skills/daily-podcast/render.py --selftest || { echo "selftest failed"; exit 1; }
# Real run: per-item isolated orchestrator (drop-on-block, deterministic curation).
python3 skills/daily-podcast/orchestrate.py
```

For disk hygiene, `render.py --prune-workdirs N` is still the mechanism — pass it when calling `render.py` directly with `--manifest`. `orchestrate.py` does not accept `--prune-workdirs`; sweep the temp dir separately if needed.

### Unattended runs need durable credentials

`orchestrate.py` summarizes each ranked item — and writes the intro/sign-off — by spawning a **child `claude -p` subprocess**. Those children authenticate on their own: they read whatever credential is on disk or in their environment, **not** the parent's in-memory session login. In an interactive `claude` session this is invisible, because a persistent OAuth credential (`~/.claude/.credentials.json`) is already on disk for the children to use.

Under a scheduler (launchd / cron, or any harness that injects a session-scoped credential the parent holds only in memory), the children can start with **no usable credential** — no on-disk token and no `ANTHROPIC_API_KEY`. Every item then fails with `401 Invalid authentication credentials`. The orchestrator detects this case (the `AUTH` outcome) and **fails fast** with a single actionable line rather than silently degrading to the generic "no viable items":

```
FAILED no viable items; at least one item reported a 401 authentication error - under a scheduler, child `claude -p` likely has no credentials. See SKILL.md "Unattended runs need durable credentials".
```

**The requirement:** the scheduled job's child processes must be able to authenticate *without* the interactive session. That means one of:

- a **persistent on-disk credential** the children can read at run time (e.g. a valid `~/.claude/.credentials.json` for the user the job runs as), or
- an **API key in the job's own environment** (`ANTHROPIC_API_KEY`) — set in the launchd plist / cron environment itself, since a scheduled job does **not** inherit your interactive shell env (the same constraint that pushes R2 / Pages secrets into the plist or `secrets.json`). Keep keys out of `config.json` and git.

**Verify it in your actual scheduler before relying on it.** Auth in the scheduled harness is exactly the non-obvious part, so don't assume a recipe works — confirm a bare child can authenticate *from inside the scheduled context* (not your terminal):

```bash
# Run this from the scheduler itself (a one-off scheduled task / `launchctl kickstart`),
# capturing output — NOT from an interactive shell, which has different credentials.
claude -p 'reply with the single word OK' || echo "child claude -p cannot authenticate here"
```

If that 401s, fix the credential before scheduling the orchestrator; if no durable credential is available to children in your environment, drive the daily run with in-session subagents (which share the parent's working auth) instead of the `claude -p` fan-out.

### Recovering from a partial failure

The upload → `timeline set` → poll-until-`READY` → dedup sequence can fail *after* the episode is already live on Spotify — most commonly a `poll_ready` timeout where processing simply took longer than the window. To make this recoverable, `render.py` writes `<workdir>/uploaded.json` (the episode URI + title) the moment `upload()` succeeds, before the failure-prone steps.

To resume, **re-run the same manifest with the same `--workdir`**:

```bash
python3 <skill-dir>/render.py --manifest manifest.json --workdir /tmp/daily-podcast-<date>
```

When `--workdir` is passed and it contains `uploaded.json`, `render.py` skips TTS rendering, the cover, and the upload, reuses the existing `episode.mp3` / `cover.jpg` / `timeline.json`, and re-runs only the idempotent tail (`timeline set` + poll + R2 back-fill + dedup). The final report carries `"resumed": true` and the same 3-state `"r2_status"` as a fresh run (#40). Notes:

- Resume only triggers with an **explicit** `--workdir`; an auto tmpdir cannot be resumed. Keep the workdir around if you want this safety net.
- If the workdir has `uploaded.json` but is missing an artifact, `render.py` fails fast (`workdir has uploaded.json but missing …`) rather than re-uploading.
- `--dry-run` never resumes (it never uploads).
- After a *fully successful* run, `uploaded.json` stays in the workdir, so re-running the same `--workdir` resumes the existing episode (an idempotent no-op) instead of rendering fresh. To force a fresh render (e.g. you fixed the script and want to re-ship), delete the workdir or its `uploaded.json`.

#### Per-segment TTS cache (resume cheaply mid-render)

TTS dominates a run's cost, so a crash on segment 9 of 12 shouldn't re-render segments 1–8. Each rendered `seg_NN.mp3` carries a `seg_NN.json` sidecar with a content-hash **cache key** over the segment's spoken text **and** the resolved voice settings (mode + voice/instruct + a hash of the `ref_audio` bytes + `ref_text`).

- Re-running with the **same `--workdir`** and same manifest reuses every segment whose key still matches and re-renders only the rest. If *every* segment is cached, the ~15 s model load is skipped entirely.
- Editing one segment's `text` invalidates only that segment; the others are reused.
- Changing the `voice` (e.g. `house` → `Ryan`), the `voice_instruct`, or the bytes of `refs/house_voice.wav` invalidates the affected entries (the key changes).
- The cache is **workdir-scoped** — a fresh `--workdir` always renders fresh, so the cache can't leak across unrelated episodes. A stderr line (`cache: N/M segment(s) reusable …` / `cache hit …`) reports what was reused.

#### Automatic cron recovery (cross-day, workdir-independent)

The workdir `uploaded.json` resume above is **manual** — it only helps if you re-run with that exact `--workdir`. The unattended cron uses a **per-date** workdir (`/tmp/daily-podcast-<date>`), so a `poll_ready` timeout on Monday that dies before dedup would otherwise let Tuesday's run (different workdir, no marker) re-curate Monday's still-undeduped URLs and ship a **duplicate**.

To close that gap, the moment `upload()` succeeds `render.py` also writes a long-lived **in-flight log** at `~/.config/daily-podcast/inflight.json` (episode URI, title, workdir, the segment `source_url`s). It records the upload independently of the workdir and is cleared only after dedup completes.

On startup — before curating/rendering a new episode, on any non-`--dry-run` run — `render.py` reconciles a leftover in-flight log:

1. If the prior workdir + `timeline.json` still exist, it re-runs `timeline set` + poll-until-`READY` for that episode.
2. It then marks the recorded `source_url`s in `covered.json` (so curation here can't re-select them).
3. Only then does it clear `inflight.json`.

A crash *during* recovery leaves `inflight.json` intact for the next attempt, and `covered.json` stays the single source of truth — the in-flight log never gates dedup, it only ever *drives* a write into `covered.json`. `--dry-run` skips recovery entirely (it never uploads, calls Spotify, or mutates `covered.json`).

## Dependencies

- `save-to-spotify` CLI on `PATH`, authenticated (`save-to-spotify auth login`)
- Python 3.10+ with the deps declared in [`pyproject.toml`](../../pyproject.toml) — `pip install -r requirements.txt` (canonical list; covers `mlx-audio`, `soundfile`, `mutagen`, `Pillow`, `numpy`, `feedparser`)
- `ffmpeg` + `ffprobe`
- Apple Silicon Mac (Qwen3-TTS via MLX needs Metal)
- ~4 GB free disk for the VoiceDesign model on first run

## Final report

After upload completes and `episodes status` returns `READY`:

> Shipped [episode title]. [N] chapters, voice [voice]. Spotify: spotify:episode:...

Nothing else. The user can listen and judge.
