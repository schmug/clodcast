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
  "target_item_count": 10                // optional; default 10
}
```

```jsonc
// ~/.config/daily-podcast/covered.json — written by render.py on successful upload
{
  "https://example.com/post-1": {"date": "2026-05-22", "episode_uri": "spotify:episode:..."},
  "https://example.com/post-2": {"date": "2026-05-21", "episode_uri": "spotify:episode:..."}
}
```

First run with no `config.json`: ask the user whether to use an existing show (list via `save-to-spotify --json shows`) or create a new one, then persist the choice.

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
- This is a **manual** recovery path keyed on the workdir. The unattended cron uses a per-date workdir, so a timeout on one day is **not** auto-recovered on the next day's run — that's the separate "in-flight episode log" work, out of scope here.
- After a *fully successful* run, `uploaded.json` stays in the workdir, so re-running the same `--workdir` resumes the existing episode (an idempotent no-op) instead of rendering fresh. To force a fresh render (e.g. you fixed the script and want to re-ship), delete the workdir or its `uploaded.json`.

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
