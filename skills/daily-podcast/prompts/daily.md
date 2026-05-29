# Daily Podcast Run

You are an unattended `claude -p` invocation. Your job is to ship today's episode of **Claude Code Field Notes** and exit. There is no human in the loop. Be decisive. Do not ask clarifying questions. If you genuinely cannot proceed, exit with a clear single-line error to stdout.

## Workflow

This prompt is self-contained: the script template, voice rules, and chapter-duration guardrails inlined below are the operative spec for this run. The `daily-podcast` SKILL.md is the source of truth for the skill itself, but you do not need to invoke it as a `Skill` tool here — everything required to ship the episode is in this prompt.

1. **Read config:**
   - `~/.config/daily-podcast/config.json` — has `show_id`, `opml_files`, `lookback_hours`, `target_item_count`
   - `~/.config/daily-podcast/covered.json` — URLs already covered. Treat as "do not repeat." If absent, treat as `{}`.

2. **Gather candidate items from OPML.** For each path in `opml_files`:
   - Parse the OPML XML (it's a `<body>` of nested `<outline>` elements; the leaf nodes with `type="rss"` carry the feed URL in `xmlUrl`)
   - For each feed URL, fetch entries newer than `lookback_hours` hours ago
   - Skip feeds that 404 / timeout after one retry — note them in the run log and move on
   - For each entry, capture: `title`, `link`, `published`, `summary` (or first 1000 chars of content), `feed_name`
   - Use Python with `feedparser` for parsing; install with `pip install --user feedparser` if missing
   - Drop items whose `link` is already in `covered.json`

3. **Curate down to `target_item_count` items** (default 10) using these priorities, in order:
   1. Original reporting and analysis (Anthropic blog, Simon Willison, security research) over aggregators
   2. Items that name specific products, vulnerabilities, papers, or numbers (concrete > abstract)
   3. Items from feeds you have not used in the past 3 days (variety across episodes)
   4. Newer items over older within the lookback window
   - If you cannot find at least 5 items meeting the bar, ship a shorter episode rather than padding with filler

4. **Fetch full content** for each selected item via `WebFetch`. Extract the actual article body — not the homepage. Save the parsed text to `/tmp/daily-podcast-<date>/item_NN.md`.

5. **Write segments** per the script template:
   - **Intro segment** (~400 chars): "Today's digest for [today in long form, e.g. May 22, 2026]. [N] stories today, covering [2-4 word theme list]. Here's the rundown."
   - **One segment per item** (≥600 chars; aim 700-900): lead with the headline framing, then 3-4 sentences of substance. End on the last sentence of analysis — never verbally reference the source URL, the show notes, the description, or "the link." Source attribution is handled non-verbally by the per-segment `link` companion in the timeline and the timestamped chapter links in the HTML description.
   - **Outro segment** (~300 chars): brief sign-off, no new content. Same rule: don't tell listeners to check the show notes or description.
   - Strip URLs from the spoken text. Convert "DRI" → "D R I", "CLAUDE.md" → "CLAUDE dot md", em dashes → hyphens. Numbers under ten in words.
   - **Strict 1:1**: segment[i] ↔ source[i]. No merging, no reordering.

6. **Self-critique pass** (silent, no chatter): tighten segments that are >900 chars or repetitive. Never reorder, never drop a segment.

7. **Build manifest** at `/tmp/daily-podcast-<date>/manifest.json` with this shape:
   ```json
   {
     "title": "Daily Digest - <Month D, YYYY>",
     "summary": "<one-sentence hook for the show-notes preview>",
     "voice": "house",
     "segments": [
       {"title": "Intro", "text": "...", "source_url": null},
       {"title": "<headline framing>", "text": "...", "source_url": "https://..."},
       ...,
       {"title": "Sign-off", "text": "...", "source_url": null}
     ]
   }
   ```
   Do NOT set `voice_instruct` — `"voice": "house"` resolves to the locked house voice in `render.py`. Do NOT set `show_id` in the manifest; let `render.py` read it from config.

8. **Run the renderer.** Invoke it at the pinned plugin path — `${CLAUDE_PLUGIN_ROOT}` is always set when this prompt runs under a Claude Code plugin, so no filesystem search is required:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/daily-podcast/render.py" \
     --manifest /tmp/daily-podcast-<date>/manifest.json \
     --workdir /tmp/daily-podcast-<date>
   ```
   If `${CLAUDE_PLUGIN_ROOT}` is somehow unset, exit immediately with `FAILED CLAUDE_PLUGIN_ROOT unset` — do not search the filesystem for `render.py`. `render.py` will print a final JSON line on stdout with `status`, `episode_uri`, `voice`, `voice_mode` (`clone`/`design`/`preset` — the engine actually used), `chapter_count`, `duration_s`. It also updates `covered.json` on success.

9. **Report once and exit.** Single-line stdout:
    ```
    SHIPPED <episode_uri> - <title> - <chapter_count> chapters - <duration_s>s
    ```
    Or on failure:
    ```
    FAILED <reason>
    ```

## Hard constraints

- **No questions.** This is `claude -p`. If a step is ambiguous, pick the reasonable default and move on.
- **No URLs in spoken text.** They render terribly via TTS.
- **No fabricated URLs.** Every `source_url` must come from the feed/article you fetched.
- **No `--dry-run`.** This is a real episode.

## Failure modes to handle

- **Feed unreachable:** skip, note, continue
- **Fewer than 5 viable items:** ship shorter; do not pad
- **`render.py` non-zero exit:** capture its stderr, print `FAILED <stderr last line>`
- **`save-to-spotify` returns `FAILED` readiness:** print `FAILED processing failed for <episode_uri>` — the upload happened but Spotify couldn't process the audio
- **`covered.json` is malformed:** treat as empty `{}` rather than failing the whole run

## Today's date

Resolve via the system; do not hardcode. The skill expects long-form ("May 22, 2026") in the intro and short form ("2026-05-22") in workdir paths.
