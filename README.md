# clodcast

A Claude Code skill that turns a list of saved articles (or RSS items) into a fully-produced Spotify episode in one pass:

- Pulls full content for each item
- Writes a segmented script using a deterministic template (intro + per-item + outro)
- Renders TTS via Qwen3-TTS with a locked house voice (`ref_audio` cloning, no run-to-run drift)
- Concatenates with auto-padded silences to satisfy Spotify's chapter rules
- Builds a date-stamped cover, timeline, and HTML description
- Uploads via the `save-to-spotify` CLI and polls until the episode is `READY`
- Updates a per-user dedup log so the same URLs are not re-covered

Ships an executable `render.py` and a self-contained `claude -p` prompt so the whole thing can run unattended on a schedule.

## Install

```bash
/plugin marketplace add schmug/clodcast
/plugin install daily-podcast@clodcast
```

## Dependencies

- **`save-to-spotify` CLI** on `PATH`, authenticated
  - `curl -fsSL https://saveto.spotify.com/install.sh | bash`
  - `save-to-spotify auth login`
- **Apple Silicon Mac** (Qwen3-TTS via MLX uses Metal). Swap the renderer if you want a different TTS provider.
- **Python 3.10+** with `mlx-audio`, `soundfile`, `mutagen`, `Pillow`, `feedparser`
  - `pip install --user mlx-audio soundfile mutagen Pillow feedparser`
- **`ffmpeg`** and **`ffprobe`**
- ~4 GB free disk for the first model download (Qwen3-TTS Base 1.7B-8bit)

## Setup

One-time config:

```bash
mkdir -p ~/.config/daily-podcast
cat > ~/.config/daily-podcast/config.json << 'EOF'
{
  "show_id": "spotify:show:<your-show-id>",
  "show_name": "Your Show Name",
  "host_name": "Your Name",
  "opml_files": ["/path/to/your-feeds.opml"],
  "lookback_hours": 24,
  "target_item_count": 10
}
EOF
```

Get a `show_id` by running `save-to-spotify --json shows` (creates a default show if you don't have one) and copying the URI.

## Usage

### Interactive (one episode in a conversation)

Ask Claude to ship today's podcast. The skill activates automatically:

> "ship today's daily digest"

### Headless (unattended schedule)

Run the bundled `claude -p` prompt:

```bash
claude -p "$(cat skills/daily-podcast/prompts/daily.md)"
```

Final stdout is a single line: `SHIPPED <episode_uri> ...` or `FAILED <reason>`.

Hook it up to launchd, cron, or any scheduler.

## Voice

The default "house" voice is `ref_audio` cloning from `skills/daily-podcast/refs/house_voice.wav` — a ~22 second reference clip. The Base 1.7B Qwen3-TTS model regenerates that voice's timbre and prosody for any new text, so the voice stays consistent across episodes.

To change the voice:
1. Capture a new ~20-30 second reference clip (any TTS or human recording)
2. Save it to `skills/daily-podcast/refs/house_voice.wav` (PCM_16, mono, 24 kHz preferred)
3. Update `skills/daily-podcast/refs/house_voice.txt` with the exact transcript
4. Done — every subsequent `voice: "house"` render uses the new clip

Other voice options (set in manifest):
- `"voice": "random"` — preset rotation over `[Ryan, Aiden, Ethan, Chelsie]`
- `"voice": "Ryan"` (or any preset) — single fixed preset
- `"voice_instruct": "..."` — VoiceDesign mode, full natural-language override

## License

MIT
