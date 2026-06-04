# Daily Podcast Run

> **DEPRECATED for unattended runs.** The cron path now uses
> `skills/daily-podcast/orchestrate.py`, which gathers + curates deterministically
> and summarizes each item in its own isolated `claude -p` so a cyber-content
> classifier block drops only that item instead of failing the whole run. This
> prompt is kept as a reference for the segment/voice rules and the manifest shape.
> See `docs/superpowers/specs/2026-06-04-per-item-orchestrator-design.md`.

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
   - Use Python with `feedparser` for parsing (declared in `pyproject.toml`); if the install hasn't been run, fall back to `pip install --user feedparser`
   - Drop items whose `link` is already in `covered.json`

3. **Curate down to `target_item_count` items** (default 10). This is a **news digest** — cover stories the way a tech-and-security *news* show would: what happened, who is affected, why it matters, and the response. First, **drop any item you could only summarize by reproducing attack methodology** — exploit proof-of-concept or how-to walkthroughs, step-by-step intrusion or attacker-technique write-ups, payloads / working commands, or raw breach-and-leak data dumps. Judge this from the `title`, `feed_name`, and `summary` you already captured in step 2 — **before** any `WebFetch` in step 4 — so you never fetch a source you would then have to refuse. A security story is in scope when it can be told at a reporting altitude (a vulnerability was disclosed, a breach occurred, a patch shipped); it is out of scope when the item *is* the technique. Reporting on a disclosed vulnerability, a breach, or published security research is ordinary tech journalism — the policy line is operational how-to, not the security topic, so don't refuse an item merely because it is *about* a vulnerability or an attack. Apply the keep/drop test to the **specific item's content, not its feed's reputation**: a mainstream security-news feed (Help Net Security, darkreading) can still carry an individual write-up built around exploit detail, and that one gets dropped just like a Phrack post would — while a clean disclosure / impact / response story from any feed is kept and summarized at altitude. When an item is borderline (newsworthy core wrapped around some operational detail), keep it but pull only the reporting-level summary in step 4 — don't drop a whole feed to avoid one item. Then rank what remains, in order:
   1. Original reporting and analysis (e.g. Anthropic blog, Simon Willison) over aggregators
   2. Items that name specific products, releases, papers, findings, or numbers (concrete > abstract)
   3. Items from feeds you have not used in the past 3 days (variety across episodes)
   4. Newer items over older within the lookback window
   - If you cannot find at least 5 items meeting the bar, ship a shorter episode rather than padding with filler. Dropping out-of-scope items above is normal and counts toward this — never pad to hit a count.

4. **Fetch full content** for each selected item via `WebFetch`, and extract it at a **reporting altitude** — the who / what / impact / response, the way a news write-up would. `WebFetch` is a summarizing fetch, so ask it for the news summary, not a verbatim dump. If a security or research article embeds operational detail (exploit code, payloads, step-by-step attack procedures), **leave that out of what you save** — you are writing a news summary, not a reproduction, and you never need the operational specifics to report the story. Extract the actual article body, not the homepage. Save the reporting-level text to `/tmp/daily-podcast-<date>/item_NN.md`.

5. **Write segments** per the script template:
   - **Intro segment** (~400 chars): "Today's digest for [today in long form, e.g. May 22, 2026]. [N] stories today, covering [2-4 word theme list]. Here's the rundown."
   - **One segment per item** (≥600 chars; aim 700-900): lead with the headline framing, then 3-4 sentences of substance. End on the last sentence of analysis — never verbally reference the source URL, the show notes, the description, or "the link." Source attribution is handled non-verbally by the per-segment `link` companion in the timeline and the timestamped chapter links in the HTML description.
   - **Outro segment** (~300 chars): brief sign-off, no new content. Same rule: don't tell listeners to check the show notes or description.
   - Strip URLs from the spoken text. Convert "DRI" → "D R I", "CLAUDE.md" → "CLAUDE dot md", em dashes → hyphens. Numbers under ten in words. (`render.py` also validates the manifest up front and re-strips em/en dashes, smart quotes, code fences, headings, and bare URLs as a safety net — but do the stylistic conversions yourself; it won't.)
   - **Report, don't instruct.** For any security, breach, or research item, write at a news-reporting altitude — what was disclosed, who is affected, and the response or fix. Never include exploit steps, payloads, working commands, or any procedure an attacker could follow; if a kept item can't be made substantive without that, drop it rather than sanitize it.
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
   If `${CLAUDE_PLUGIN_ROOT}` is somehow unset, exit immediately with `FAILED CLAUDE_PLUGIN_ROOT unset` — do not search the filesystem for `render.py`. `render.py` will print a final JSON line on stdout with `status`, `episode_uri`, `voice`, `voice_mode` (`clone`/`design`/`preset` — the engine actually used), `chapter_count`, `duration_s`, `r2_status` (`published`/`skipped`/`failed` — the Cloudflare R2 web-feed outcome; **never** fails the run, even on `failed`), and `resumed` (`true` if it picked up a prior partial run from the workdir). It also updates `covered.json` on success.

9. **Report once and exit.** Single-line stdout, with the R2 web-feed outcome appended as a trailing `r2=` field (map `render.py`'s `r2_status`: `published`→`ok`, `skipped`→`skipped`, `failed`→`FAILED`):
    ```
    SHIPPED <episode_uri> - <title> - <chapter_count> chapters - <duration_s>s - r2=ok
    ```
    `r2=ok` means the episode also reached the cortech.online web feed; `r2=skipped` means R2 isn't configured (benign); `r2=FAILED` means the episode is **live on Spotify** but the web-feed publish errored — still a successful run (exit 0, `covered.json` written), but a signal an operator should notice and back-fill. Never turn `r2=FAILED` into a `FAILED <reason>` line — the run did not fail.
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
- **`render.py` failed *after* upload (e.g. a `poll_ready` timeout):** the episode may already be live. Because this prompt always passes a stable `--workdir`, re-running `render.py` with the same `--manifest` and `--workdir` resumes — it skips the re-upload, re-runs only `timeline set` + poll + dedup, and reports `"resumed": true`. Prefer this over re-shipping, which would create a duplicate episode.
- **`covered.json` is malformed:** treat as empty `{}` rather than failing the whole run

## Today's date

Resolve via the system; do not hardcode. The skill expects long-form ("May 22, 2026") in the intro and short form ("2026-05-22") in workdir paths.
