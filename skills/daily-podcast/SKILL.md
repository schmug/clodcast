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
- Close with the deep-link reference: "Link to the full piece in the show notes."
- Never read URLs aloud
- One source per segment — strict 1:1 mapping

**Outro** (~300 chars): brief sign-off, mention show-notes links, no new content.

### Rules
- Convert relative dates from sources to absolute (today's date is available via the system clock)
- Strip markdown, code blocks, emoji, hashtags before TTS
- Numbers under 10 in words; abbreviations expanded ("D R I" not "DRI")
- "CLAUDE dot md" not "CLAUDE.md"
- No em dashes — TTS encoding flakes; use hyphens
- Use 1-2 transition phrases between segments ("Next up", "Moving on", "Also today")

## Voice selection

The default is the **locked house voice** — `ref_audio` cloning from `refs/house_voice.wav`, a ~22-second reference clip bundled with the skill. The Base 1.7B model regenerates that voice's timbre and prosody for any new text, so the voice stays consistent across episodes.

Manifest options:
- `"voice": "house"` (default) — Base model + `refs/house_voice.wav` + `refs/house_voice.txt` (transcript)
- `"voice": "random"` — preset rotation over `[Ryan, Aiden, Ethan, Chelsie]`
- `"voice": "Ryan"` (or any preset) — single fixed preset
- `"voice_instruct": "..."` — VoiceDesign mode, full override; `voice` becomes a label

The house clip is one good render of a VoiceDesign instruct (`HOUSE_VOICE_INSTRUCT`, kept in `render.py` for reference) — mature female, even prosody, bright but human, not performative. To replace the house voice:

1. Capture a new ~20-30 second reference clip (any TTS or human recording)
2. Save it to `refs/house_voice.wav` (PCM_16, mono, 24 kHz preferred)
3. Update `refs/house_voice.txt` with the exact transcript
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

## Dependencies

- `save-to-spotify` CLI on `PATH`, authenticated (`save-to-spotify auth login`)
- Python 3.10+ with `mlx-audio`, `soundfile`, `mutagen`, `Pillow` (`pip install --user mlx-audio soundfile mutagen Pillow`)
- `ffmpeg` + `ffprobe`
- Apple Silicon Mac (Qwen3-TTS via MLX needs Metal)
- ~4 GB free disk for the VoiceDesign model on first run
- For the headless prompt: `feedparser` (the prompt installs it if missing)

## Final report

After upload completes and `episodes status` returns `READY`:

> Shipped [episode title]. [N] chapters, voice [voice]. Spotify: spotify:episode:...

Nothing else. The user can listen and judge.
