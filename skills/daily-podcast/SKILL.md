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
- `./prompts/daily.md` — the self-contained `claude -p` prompt for unattended daily runs

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

This is strictly additive: **Spotify is the canonical artifact.** A missing config
no-ops with one log line; any publish error warns, still writes `covered.json`, still
exits 0, and reports `"r2_published": false` in the final JSON line. `--dry-run` skips
the publish and prints where it *would* have gone (`r2_would_publish`).

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

> Resume note: the R2 publish runs on a normal fresh run only. The `--workdir`
> resume path (`_resume`) recovers the Spotify tail without touching `config.json`,
> so a resumed episode is **not** back-filled to R2 — re-run fresh if you need it on
> the web feed.

## Running the pipeline

Two entry points:

**Interactive (current session):** Claude writes the manifest in conversation, then runs:
```bash
python3 <skill-dir>/render.py --manifest manifest.json
```

**Headless (`claude -p`):** Use the self-contained run prompt at `./prompts/daily.md`. Pipe it to a fresh Claude session and walk away:
```bash
claude -p "$(cat <skill-dir>/prompts/daily.md)"
```
The prompt reads OPML feeds from config, filters against the dedup log, writes the script, builds the manifest, and invokes `render.py` end to end. Final stdout is a single line: `SHIPPED <episode_uri> ...` or `FAILED <reason>`.

`render.py` exits non-zero with a diagnostic on any failure. Always check the exit code; do not assume success.

For testing without uploading, use `--dry-run` — produces the MP3, cover, and timeline.json locally and reports paths, but skips the `save-to-spotify upload` and `timeline set` calls.

### Recovering from a partial failure

The upload → `timeline set` → poll-until-`READY` → dedup sequence can fail *after* the episode is already live on Spotify — most commonly a `poll_ready` timeout where processing simply took longer than the window. To make this recoverable, `render.py` writes `<workdir>/uploaded.json` (the episode URI + title) the moment `upload()` succeeds, before the failure-prone steps.

To resume, **re-run the same manifest with the same `--workdir`**:

```bash
python3 <skill-dir>/render.py --manifest manifest.json --workdir /tmp/daily-podcast-<date>
```

When `--workdir` is passed and it contains `uploaded.json`, `render.py` skips TTS rendering, the cover, and the upload, reuses the existing `episode.mp3` / `cover.jpg` / `timeline.json`, and re-runs only the idempotent tail (`timeline set` + poll + dedup). The final report carries `"resumed": true`. Notes:

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
