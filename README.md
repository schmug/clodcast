# clodcast

[![CI](https://github.com/schmug/clodcast/actions/workflows/ci.yml/badge.svg)](https://github.com/schmug/clodcast/actions/workflows/ci.yml)

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

### Releases

Versions follow [semver](https://semver.org/) and are tagged `vX.Y.Z`. See the
[**Releases**](https://github.com/schmug/clodcast/releases) page for tagged
versions and the [**CHANGELOG**](CHANGELOG.md) for what changed in each. Both are
generated from conventional commits by [git-cliff](https://git-cliff.org); a
release is cut manually via the [release workflow](.github/workflows/release.yml).

## Dependencies

- **`save-to-spotify` CLI** on `PATH`, authenticated
  - `curl -fsSL https://saveto.spotify.com/install.sh | bash`
  - `save-to-spotify auth login`
- **Apple Silicon Mac** (Qwen3-TTS via MLX uses Metal). Swap the renderer if you want a different TTS provider.
- **Python 3.10+** — runtime deps are declared in [`pyproject.toml`](pyproject.toml) (`mlx-audio`, `soundfile`, `mutagen`, `Pillow`, `numpy`, `feedparser`)
  - `pip install -r requirements.txt` (or `pip install -e .` for an editable checkout)
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

### Optional: publish to a web feed (Cloudflare R2)

Beyond Spotify, each finished episode can also be published to a Cloudflare R2 bucket,
which [cortech.online](https://github.com/schmug/cortech.online) turns into a `/podcast/`
page and an iTunes RSS feed at `/podcast/rss.xml`. This is additive — if it's not
configured, runs behave exactly as before.

Add the bucket + public URL to `config.json`:

```jsonc
  "r2_bucket": "clodcast",
  "r2_public_base_url": "https://audio.cortech.online"   // your R2 public domain
```

Provide credentials via env (never in `config.json` or git) — or a `0600`
`~/.config/daily-podcast/secrets.json` with the same keys:

```bash
export R2_ACCESS_KEY_ID=...      # R2 API token
export R2_SECRET_ACCESS_KEY=...
export R2_ACCOUNT_ID=...         # Cloudflare account ID
# optional:
export PAGES_DEPLOY_HOOK_URL=...  # POSTed after publish so the site rebuilds in ~30s
```

The optional **Pages deploy hook** is POSTed after a successful publish so the site
rebuilds. It resolves the same cron-friendly way as the credentials — env first,
then `secrets.json` (`"PAGES_DEPLOY_HOOK_URL"`), then `config.json`
(`"pages_deploy_hook_url"`). A **scheduled** run (launchd/cron) never inherits your
interactive shell env, so put the hook in `secrets.json` (0600) for unattended runs —
that's also its preferred home because the URL can trigger builds. `config.json`
support is a convenience for the shareable file; if all three are unset, no hook fires
(unchanged).

When all five resolve, a successful run publishes `<slug>.mp3` + a `manifest.json`
entry and prints `"r2_published": true`. On any R2 error the run still succeeds (the
Spotify episode is canonical) and prints `"r2_published": false`. See
[SKILL.md](skills/daily-podcast/SKILL.md#publishing-to-the-web-cloudflare-r2) for details.

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

The default "house" voice is `ref_audio` cloning from a ~22 second reference clip. The Base 1.7B Qwen3-TTS model regenerates that voice's timbre and prosody for any new text, so the voice stays consistent across episodes.

On first run, the bundled default is copied to `~/.config/daily-podcast/voices/house.{wav,txt}`. Anything you put there wins over the bundled copy and survives plugin updates.

To change the voice:
1. Capture a new ~20-30 second reference clip (any TTS or human recording)
2. Save it to `~/.config/daily-podcast/voices/house.wav` (PCM_16, mono, 24 kHz preferred)
3. Update `~/.config/daily-podcast/voices/house.txt` with the exact transcript
4. Done — every subsequent `voice: "house"` render uses the new clip

Other voice options (set in manifest):
- `"voice": "random"` — preset rotation over `[Ryan, Aiden, Ethan, Chelsie]`
- `"voice": "Ryan"` (or any preset) — single fixed preset
- `"voice_instruct": "..."` — VoiceDesign mode, full natural-language override

**Want to design your own voice from scratch?** See [docs/durable-voices.md](docs/durable-voices.md) — covers why `ref_audio` cloning beats VoiceDesign for long-running shows, the iteration workflow that produced the bundled house voice, common failure modes to avoid (over-enunciation, theatrical drift, noir weight), and how to verify a new clip is stable.

## Development

Install the dev tools (lint + tests). The runtime deps are Apple-Silicon-only, so for tooling alone just install the two tools directly:

```bash
pip install ruff pytest      # tooling only (no MLX)
# or, for a full editable env on Apple Silicon:
pip install -e ".[dev]"
```

**Lint & format** — both `ruff check` and `ruff format --check` are enforced (CI fails on a format diff). The renderer's once hand-tuned layout was reformatted to `ruff format` in one isolated commit; run `ruff format .` to fix any diff before committing:

```bash
ruff check .
ruff format --check .   # enforced — fails on any diff
```

The reformat commit is listed in [`.git-blame-ignore-revs`](.git-blame-ignore-revs) so the bulk-format churn doesn't pollute `git blame`. To skip it locally:

```bash
git config blame.ignoreRevsFile .git-blame-ignore-revs
```

(GitHub honors this file automatically in its blame view.)

**Tests:**

```bash
pytest
```

**Pre-commit hooks** — run `ruff check --fix` plus whitespace/EOF/YAML/JSON hygiene on staged files (the reference clip in `refs/` is excluded):

```bash
pip install pre-commit && pre-commit install
```

After that, `git commit` runs the hooks automatically; `pre-commit run --all-files` checks the whole tree.

## License

MIT
