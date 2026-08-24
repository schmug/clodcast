---
id: frontier-commits
name: frontier-commits
description: Use when the user asks to ship the Frontier Commits weekly podcast — turns the frontier labs' GitHub org activity (new repos, releases, archivals, star trends) into a speculation-forward episode published to the public RSS feed via the daily snapshot store and render.py. Skips the standard production interview because defaults are pre-set.
enabled: true
---

# Frontier Commits

A weekly show reading the frontier AI labs' **public GitHub activity**: new repos and what they might mean, notable releases, archivals, staleness, and star-velocity trends. Sibling to the daily digest — same house voice, same `render.py` production stack, its own web feed.

**This show is RSS-first.** Its canonical channel is the public feed on cortech.online, and it ships through `render.py`'s web-only mode (`"ship_mode": "web"`): render → artifact gate → R2 publish → deploy hook → dedup. `save-to-spotify` is never invoked, the show has no episode cap to prune against, and there is no readiness poll to wait on. Public Spotify listing, if wanted, comes from submitting the RSS feed — not from the deprecated save-to-spotify show.

The genre is *informed speculation*: "OpenAI forked git and it's actively pushed — probably a staging fork for upstream patches, but if it's more, here's what that would mean." Speculation is a feature, not a bug — but it is always labeled as speculation and anchored to observable facts. The [Speculation rules](#speculation-rules) below are hard rules, not style advice.

**Trigger phrases:** "ship this week's Frontier Commits", "run the frontier labs episode", "weekly GitHub podcast".

## Layout

References are relative to the skill directory:

- `./fc_common.py` — shared paths, config loading/validation, atomic writes, gh runner, R2/secrets resolution
- `./fc_snapshot.py` — the daily collector CLI (runs under launchd): org sweep → snapshot → `labs.json` → R2 publish + deploy hook
- `./fc_stories.py` — the deterministic story detector CLI: `detect` / `mark`
- `./fc_script_plan.py` — the week-seeded rotation CLI: intro/outro modes, segment shapes, segue moves, length bands
- `./prompts/weekly.md` — a stub pointing back here; the unattended procedure lives in [Unattended weekly run](#unattended-weekly-run)
- `./prompts/write_story.md` — the per-story segment prompt (placeholder contract `<<TYPE>>/<<TITLE>>/<<URL>>/<<FACTS>>/<<SHAPE>>/<<MIN_CHARS>>/<<MAX_CHARS>>`)
- `./launchd/com.cortech.frontier-commits-snapshot.plist` — the daily snapshot job (see [Setup](#setup))

The episode renderer is **not** in this skill: manifests are handed to the daily skill's [`render.py`](../daily-podcast/render.py) — TTS, concat, cover, timeline, R2 publish, and all of its reliability layer (pre-flight, artifact gate, durable state, incident capture) are reused as-is. Only the Spotify-shaped tail — upload, timeline set, readiness poll, episode-cap pruning — is skipped, because `ship_mode` is `web`.

## Data layer

The show's facts come from a local snapshot store, never from live queries at episode time. GitHub retired per-repo star history upstream, so **the daily snapshots are the only source of velocity data** — for both this show and the cortech.online `/labs/` dashboard. Losing a day degrades trends; it never breaks a run.

### Daily snapshot collector (`fc_snapshot.py`)

Pure Python + the `gh` CLI, no Claude credential — safe under launchd. Each run:

1. Sweeps every configured org's repos (metadata only), applying the org's `filter` (`"ai"` keeps AI-topic/description matches plus the org's `allowlist`; `denylist` always applies).
2. Checks recently-pushed repos (capped at `releases_repo_cap_per_org`, most-recently-pushed first) for new releases.
3. Writes `~/.config/frontier-commits/snapshots/YYYY-MM-DD.json` atomically (`schema_version` 1). A failed org is **omitted** from `orgs` and recorded in `errors` — one org's timeout never loses the others' day.
4. Prunes snapshots older than `snapshot_retention_days`.
5. Aggregates `labs.json` and publishes it to R2 behind the hash gate below, then fires the Pages deploy hook (best-effort).

**Final-line contract:** the last stdout line is *always* `SNAPSHOT ok date=<d> orgs=<n>/<m> repos=<n> releases=<n> labs=<published|unchanged|skipped|failed|dry-run>` or `SNAPSHOT FAILED <reason>` — on **every** exit path, including config errors: a `die()` anywhere below `main()` still resolves to a final `SNAPSHOT FAILED` line and exit 1. Schedulers parse this line; never change its shape. `--dry-run` sweeps and writes the snapshot but skips the R2 publish and the hook.

**`labs.json` (schema_version 1).** The dashboard's prerendered data: per-lab totals, `new_repos`, `movers`, `stale_watch`, `archived_recent`. Two semantics are load-bearing:

- **`snapshot_date` anchoring.** `date` is the CLI run date; `snapshot_date` names the measurement snapshot the numbers come from, and *every* window and span anchors to `snapshot_date`, never the run date — a run replayed three days late computes exactly the aggregates the on-time run would have. The site's schema must be written from the shipped shape (spec §4.6).
- **`new_repos` is a timeline, not a novelty detector.** A repo is listed iff its `created_at` falls within 30 days of `snapshot_date`, whether or not any baseline saw it — novelty and mention-once are `fc_stories`' job alone, so the dashboard and the show can legitimately disagree about "new".

**Destination-keyed publish gate.** The publish is gated on a stored record `{bucket, key, sha256}` — keyed on the **destination as well as the content** (hash computed excluding `generated_at`). Content alone would make a renamed `labs_manifest_name` or a switched `r2_bucket` report `unchanged` forever: the new destination never written, the miss indistinguishable from a healthy no-op. Any mismatch (or a legacy/malformed record) forces a publish; the record is written only *after* a successful PUT, so a failed publish leaves the gate open and the next run retries. Missing bucket or credentials → `skipped` — the web page is optional and never sinks a snapshot run.

### Story detector (`fc_stories.py`)

Deterministic and metadata-only — a pure function of (snapshots, `reported.json`, config); no network, no LLM. Curation never reads repo contents, the weekly cousin of the daily show's one-body-per-request invariant.

- `detect --date <YYYY-MM-DD> [--lookback-days N]` prints **one JSON object** as its final stdout line: `{"run_date", "baseline_date", "thin", "stories": [{key, type, org, repo, url, title, score, facts}, ...]}`.
- Detection diffs the newest snapshot at or before the run date against a baseline `lookback_days` back (never the current snapshot itself). **Staleness ceiling:** if the newest usable snapshot is more than `MAX_SNAPSHOT_AGE_DAYS = 3` days older than the run date, `detect` dies and demands a fresh snapshot — gap-tolerant across a missed cron day or a weekend, but `GOING_STALE` measures days from the run date against `pushed_at`, so diffing an ancient snapshot yields garbage stories.
- **Unknown-org novelty fallback:** when the baseline actually *saw* an org, "new" is set membership against that org's baseline repo set. An org absent from — or empty in — the baseline is **unknown, not empty**: novelty for that org falls back to the created-after-cutoff date test. Treating unknown as empty would announce the org's entire catalog as "new" (flooding ancient repos at top priority and permanently burning their mention-once keys); the fallback can only delay a genuinely-new repo by at most the lookback window.
- **Mention-once is mechanical:** a story key that reaches `reported.json` never re-emits; a repo resurfaces only when its *stage* changes (see the table below).
- Scoring is `TYPE_PRIORITY[type] + 10 × log10(stars + 10)`; selection takes the top `target_stories_per_episode` under a `per_org_story_cap`, and `"thin": true` when fewer than `min_stories_per_episode` survive.
- `mark --stories <detect-output.json> --episode-uri <uri>` marks every story key in that file as reported. **`mark` takes its date from the detect output's `run_date` field — there is no `--date` flag** — so the reported date always matches the run that selected the stories.

## Story types

Six types, in priority order (`fc_stories.TYPE_PRIORITY`). The **stage** is the third component of the story key (`TYPE:org/repo:stage`) and is the axis mention-once operates on.

| Type | Priority | Trigger | Stage |
| --- | --- | --- | --- |
| `NEW_REPO` | 100 | A non-fork repo appeared since the baseline (set membership when the baseline saw the org; created-after-cutoff fallback otherwise). | `new` — one mention, ever. |
| `ARCHIVED` | 90 | `archived` flipped false → true between baseline and current snapshot. | `archived`. |
| `NOTABLE_FORK` | 80 | A newly-appeared fork that is *actively pushed* (pushed within 14 days of the run date). Deliberate simplification: the spec's upstream-stars arm needs a per-fork parent lookup (network), so actively-pushed is the only arm shipped. | `new`. |
| `RELEASE` | 60 | A release published on/after the cutoff, on a repo with ≥ `release_min_stars` stars or on the org's `allowlist`. Only strictly-before-cutoff releases are skipped, so a boundary-day release may be nominated in two consecutive runs pre-mark — `reported.json` makes the mention exactly-once; permanently missing a release is the real failure mode. | The tag, **verbatim** — `:` is permitted (`v1:beta` stays distinct; sanitizing could collide two releases into one key), and an empty tag is skipped entirely (it cannot form a distinguishable stage). |
| `GOING_STALE` | 50 | An unarchived repo with ≥ `stale_min_stars` stars whose last push crossed a `stale_stages_days` boundary. | `stale-<days>` — the repo legitimately resurfaces at each new stage (`stale-180` → `stale-365`). |
| `STAR_SURGE` | 40 | Stars grew at least `max(surge_min_delta_7d, surge_min_ratio × before)` per 7 days against the baseline. | The ISO week (`2026-W34`) — a same-week re-run rebuilds the same episode; a surge sustained into the next week may legitimately re-emit (scoring already de-prioritizes it). |

## Script template

The template is a **rotation, not a fixed form** — the weekly analogue of the daily show's date-seeded rotation, and for the same reason: each story segment is written in an isolated context that cannot see its neighbours to differ from them, so variety is *assigned* from outside, never requested from the model.

**Seed:** `week` = the contiguous week counter of the run date — the run date's ISO week's Monday, `ordinal // 7` (`fc_script_plan.week_index`). Consecutive real-world weeks give consecutive integers across *every* year boundary, 52- and 53-week ISO years alike — a `year*53 + week` style multiplier steps by 2 at 52-week year ends, silently skipping a rotation row. A re-run of the same week rebuilds the same episode. Every index below is `week` modulo the bank size.

Don't compute assignments by hand — ask the code:

```bash
python3 fc_script_plan.py plan --date <YYYY-MM-DD> --stories <n>
```

prints one JSON object: `week_row`, `intro_mode`/`intro_text`, `outro_mode`/`outro_text`, and per-segment `{pos, shape, shape_text, move, move_text, band}`.

### Cold open

Bank of five, indexed `week % 5`:

| # | Mode | Do |
| --- | --- | --- |
| 0 | `ledger` | Open with the week's ledger: "Week of [date]. [N] stories across [the labs involved]." Then the single most telling number of the week. |
| 1 | `headline` | Open cold on the week's biggest story in one sentence, then pull back: date, story count, rundown. |
| 2 | `question` | Open with the question this week's activity raises, then the date and the rundown. |
| 3 | `pattern` | Open by naming a pattern that honestly connects two or more of this week's stories, then the rundown. No honest pattern? Use the ledger opening instead — never manufacture one. |
| 4 | `time-capsule` | Open by contrasting this week with a specific earlier state ("A month ago this org was quiet - this week..."), then the rundown. |

### Story segments

Each story segment gets a **shape** and a **length band**, both assigned by its position. Take this week's row from the table below by `week % 6`, then read the shapes left to right across your story segments; positions past the sixth wrap around and reuse the row.

| `week % 6` | pos 0 | pos 1 | pos 2 | pos 3 | pos 4 | pos 5 |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | `cross-lab` | `question-first` | `timeline` | `numbers-first` | `artifact-first` | `zoom-out` |
| 1 | `numbers-first` | `cross-lab` | `artifact-first` | `question-first` | `zoom-out` | `timeline` |
| 2 | `question-first` | `artifact-first` | `zoom-out` | `timeline` | `cross-lab` | `numbers-first` |
| 3 | `artifact-first` | `numbers-first` | `question-first` | `zoom-out` | `timeline` | `cross-lab` |
| 4 | `zoom-out` | `timeline` | `numbers-first` | `cross-lab` | `question-first` | `artifact-first` |
| 5 | `timeline` | `zoom-out` | `cross-lab` | `artifact-first` | `numbers-first` | `question-first` |

What each shape means:

| # | Shape | Opening |
| --- | --- | --- |
| 0 | `artifact-first` | Open with the concrete thing that appeared - the repo, what is actually in it - then what it might mean. |
| 1 | `question-first` | Open with the question this repo's existence raises, then walk the evidence toward the best available answer. |
| 2 | `timeline` | Walk the observable sequence in order - created, pushed, released, went quiet - then read the trajectory. |
| 3 | `cross-lab` | Open by placing this against another lab's position in the same space, then the specifics. Only when the comparison is real. |
| 4 | `numbers-first` | Open with the most telling number - stars, days since a push, repo counts - then the story behind it. |
| 5 | `zoom-out` | Open one level up - what this kind of repo says about where the lab is headed - then drop into the specifics. |

That table is a **Latin square** and both properties are load-bearing: every row is a permutation of the bank (each shape once per six segments) and every column holds each shape exactly once (no position starved, none repeating two weeks running). The rows are deliberately not rotations of one another. **Do not replace the table with arithmetic and do not regenerate it** — the daily show's stride formula passed a year-long coverage test while pinning positions to a single shape for days at a time; this table is fixed data (`fc_script_plan.SHAPE_ORDERS_W`), machine-verified for Latin rows *and* columns, cyclic consecutive-row disagreement in every column, and pairwise-distinct rotation signatures. A drift test fails if this section stops matching the code.

**Length band** — the lead gets the long read, the trend watch runs short, everything else is a body:

| Position | Band | Role |
| --- | --- | --- |
| 0 | 1100-1500 chars | Lead read — the week's biggest story |
| every other story | 700-1100 chars | Body segment |
| trend-watch close | 450-700 chars | Fixed non-story close (below) |

### Segues

A segue names the **relationship** between adjacent stories, and it is assigned, not improvised: the story at position `i` (for `i ≥ 1`) takes the move at column `i - 1` of row `(week + 3) % 6` of the *same* Latin square above, read as an index into the move bank below. The **lead story gets no segue** — it follows the cold open, so it is always the textless `cold` move. The `+3` row offset keeps segues off the shapes' row, so a given shape doesn't carry the same segue forever.

| # | Move | Do |
| --- | --- | --- |
| 0 | `cold` | *No connective at all. Hard cut straight into the story.* |
| 1 | `pivot` | Name the change of subject in a few words, then go. |
| 2 | `echo` | Mark that this story rhymes with the previous one: same pattern, new lab. |
| 3 | `contrast` | Mark the opposition to the previous story in one clause, then go. |
| 4 | `escalate` | Frame this story as raising the stakes of the previous one. |
| 5 | `zoom` | Shift altitude from the previous story - from one repo to the big picture, or back down. |

**Never manufacture a connection.** Adjacent stories are often genuinely unrelated — if the assigned move needs a relationship the two stories don't have, write a plain topic change instead. A false link reads worse than a blunt hand-off.

### Trend-watch close

A fixed final **non-story** segment, present every week, at the trend band above: read two or three numbers from today's `labs.json` — the week's biggest mover, the longest quiet streak on the stale watch, a totals shift worth a sentence. Numbers only from `labs.json`, no speculation beyond one closing read of what they suggest. It carries `"source_url": null`.

### Sign-off

Bank of three, indexed `week % 3`:

| # | Mode | Do |
| --- | --- | --- |
| 0 | `plain` | Plain sign-off: a simple thanks. No new content. |
| 1 | `watchlist` | Name one or two repos to watch next week and the observable that would settle the question. Then sign off. |
| 2 | `callback` | Close by paying off the cold open in one line, then sign off. No new facts. |

### TTS rules

Same as the daily show: strip markdown/emoji before TTS, numbers under ten in words, abbreviations expanded ("D R I"), "CLAUDE dot md" not "CLAUDE.md", no em dashes (use hyphens), convert relative dates to absolute, vary sentence rhythm inside a segment. `render.py` re-strips TTS-hostile characters as defense in depth; the stylistic rules stay the writer's job.

## Speculation rules

Hard rules, from the design spec — every one applies to every segment:

1. **Framed as speculation in the prose itself.** "Reads like", "the obvious guess is", "if this is X, then..." — the listener must always be able to tell a guess from a fact.
2. **Anchored to at least one observable** — a creation date, a fork parent, commit cadence, a description, topics, a star trajectory. A speculative claim with no observable under it doesn't ship.
3. **Never presented as confirmed fact, and never attributed to individuals.** The actor is the lab, never a named person's motives.

Plus the two inherited house rules: **never manufacture a connection** between unrelated stories, and **segments end on substance** — never a verbal pointer to the source URL, the show notes, or "check it out" (attribution is handled non-verbally by the per-segment timeline link).

## Manifest

A standard `render.py` manifest (the daily skill's Form 2) plus four keys, all four required for this show:

- `"ship_mode": "web"` — the web-only ship (#155). The R2 publish *is* the ship: no upload, no timeline set, no readiness poll, and `save-to-spotify` is never invoked. **Omitting this key is not a degraded run — it is a different one**, because render.py defaults to a Spotify upload. In this mode R2 configuration is *required* (absent fails pre-flight, before any render), a failed publish fails the run, and `covered.json` is written only after the publish succeeds.
- `"r2_manifest_name": "manifest-frontier-commits.json"` — keeps this show's web feed beside, never inside, the daily show's `manifest.json` (#118).
- `"r2_key_prefix": "frontier-commits/"` — namespaces the episode/cover objects. The slug is date-keyed, so without the prefix a frontier episode publishing the same day as a daily episode would overwrite the daily show's `.mp3`/`.jpg` in the shared bucket (#142).
- `"show_name": "Frontier Commits"` — the name stamped on the generated cover. `render.py` reads `~/.config/daily-podcast/config.json` for every show it renders (see the warning below), so without this key every episode's cover carries the daily show's branding (#157).

**No `show_id`.** There is no Spotify show to upload to; render.py ignores the key in this mode and pre-flight does not ask for one.

⚠️ **The R2 bucket and credentials for the EPISODE publish come from the daily skill's config**, i.e. `~/.config/daily-podcast/config.json` + `secrets.json` + env — never from `~/.config/frontier-commits/config.json`. That file's `r2_bucket` / `r2_public_base_url` drive only `fc_snapshot`'s `labs.json` publish. The two live in different config roots on purpose: `render.py` owns the episode bucket for every show it renders. Do not "fix" render.py to read the frontier config — it would silently change which bucket published episodes land in.

```json
{
  "title": "Frontier Commits — Week of August 24, 2026",
  "summary": "This week's one-sentence hook.",
  "date": "2026-08-24",
  "voice": "house",
  "ship_mode": "web",
  "show_name": "Frontier Commits",
  "r2_manifest_name": "manifest-frontier-commits.json",
  "r2_key_prefix": "frontier-commits/",
  "segments": [
    {"text": "Cold open...", "source_url": null, "title": "Cold open"},
    {"text": "Lead story, 1100-1500 chars...", "source_url": "https://github.com/openai/git", "source_title": "openai/git"},
    {"text": "Body story...", "source_url": "https://github.com/anthropics/example", "source_title": "anthropics/example"},
    {"text": "Trend watch...", "source_url": null, "title": "Trend watch"},
    {"text": "Sign-off...", "source_url": null, "title": "Sign-off"}
  ]
}
```

- **Title format:** `Frontier Commits — Week of <Month D, YYYY>` (the Monday of the run's ISO week).
- **Strict 1:1** segment ↔ source mapping: every story segment carries exactly its own repo URL; the cold open, trend watch, and sign-off carry `null`. Never merge stories or attach two URLs to one segment.
- **Frame segments must carry a `title`.** Story segments get their chapter title from `source_title`, but the three frame segments have no source — without an explicit `"title"` ("Cold open", "Trend watch", "Sign-off"), `render.py` falls back to positional chapter titles like "Segment 1" in the published timeline.
- **Voice defaults to house.** Do not set `voice_instruct`; no new voice modes.

## Show + state config

State lives under `~/.config/frontier-commits/`:

- `config.json` — orgs, filters, thresholds; schema in [Setup](#setup). `fc_common.load_config` dies if it is missing.
- `snapshots/YYYY-MM-DD.json` — daily org state, the star-history substitute. A malformed day is treated as absent, never fatal.
- `reported.json` — story-key → `{date, episode_uri}` mention-once log. Written by `fc_stories.py mark` **only after the R2 publish succeeds** (the same post-success discipline as `covered.json` — the success condition is the publish, since that is the ship here); malformed → `{}` — worst case a repeated mention one week. The recorded `episode_uri` is the published mp3 URL: this show has no Spotify episode URI, and the URL is the episode's durable identity.
- `labs_json.sha256` — the destination-keyed publish-gate record (`{bucket, key, sha256}`).
- `secrets.json` — optional (0600). R2 keys, `PAGES_DEPLOY_HOOK_URL`, and `GH_TOKEN` resolve env-first, then this file, then `~/.config/daily-podcast/secrets.json` — the daily skill's secrets file is the durable fallback tier so credentials have one home per host.

Shared with the daily show: `~/.config/daily-podcast/covered.json` — `render.py`'s URL dedup, written **after the R2 publish** on this show's web-only runs (post-READY on the daily show's); GitHub repo URLs and news-article URLs don't collide — and `runs.jsonl`, where this show's runs appear with their own manifest path, `"status": "web-ready"`, and the published `mp3_url`. The episode bucket and credentials are the daily skill's too — see the warning in [Manifest](#manifest).

## Unattended weekly run

**This section is the canonical procedure for shipping a weekly episode with no human in the loop.** It is the single home: a scheduler invokes this skill and follows this section, never carries its own copy of these steps ([`prompts/weekly.md`](prompts/weekly.md) is a stub pointing here, and a drift test keeps it one). Be decisive, don't ask clarifying questions, and if you genuinely cannot proceed, exit with a single-line error on stdout.

1. **Resolve paths.** Skill dir: `${CLAUDE_PLUGIN_ROOT}/skills/frontier-commits/` when set; when unset (known to happen under scheduled tasks) fall back to the path this SKILL.md was loaded from. The renderer is the sibling `skills/daily-podcast/render.py` under the same root. Workdir: `$TMPDIR/frontier-commits-<date>/`.
2. **Ensure today's snapshot exists:** run `python3 fc_snapshot.py`. If the sweep fails but a snapshot at most 2 days old exists, log it and continue with that snapshot; otherwise print `FAILED no usable snapshot` and stop.
3. **Detect stories:** `python3 fc_stories.py detect --date <today>` → save the stdout JSON to `<workdir>/stories.json`. If `"thin": true` → print `SKIPPED thin-week (<n> stories)` and exit 0. **No filler episodes.**
4. **Get the script plan:** `python3 fc_script_plan.py plan --date <today> --stories <n>` → `<workdir>/plan.json`.
5. **Write each story in its own subagent context** — one story's material per context, the weekly analogue of the daily show's one-body-per-request invariant. Per story: read `prompts/write_story.md`, fill `<<TYPE>>/<<TITLE>>/<<URL>>/<<FACTS>>/<<SHAPE>>/<<MIN_CHARS>>/<<MAX_CHARS>>` from `stories.json` + `plan.json`, research the repo first (README via `gh api repos/<org>/<repo>/readme`, recent commits/releases), write the segment, return the JSON contract. A refused/failed story is dropped and logged; if survivors fall below `min_stories_per_episode` → print `SKIPPED thin-week after drops` and exit 0.
6. **Write the frame:** the intro (assigned mode), the segues (assigned moves — `cold` means no segue text at all), the trend-watch close (from today's `labs.json`), and the outro (assigned mode).
7. **Assemble `<workdir>/manifest.json`** per [Manifest](#manifest) — including `"ship_mode": "web"`, without which this ships to the wrong place — and render in the **background**: `python3 <root>/skills/daily-podcast/render.py --manifest <workdir>/manifest.json --workdir <workdir>` — the 10-minute foreground Bash cap SIGTERMs a long render; monitor the render log instead. Never pass `--dry-run` (this is a real episode) and never pass `--skip-preflight`.
8. **On a successful publish** — exit 0 and a final JSON object with `"status": "web-ready"` and `"r2_status": "published"` — take its `mp3_url` and run `python3 fc_stories.py mark --stories <workdir>/stories.json --episode-uri <mp3_url>`. (`mark` reads the date from the file's `run_date`; there is no `--date` flag.) A nonzero exit means nothing was published: do **not** mark, and report `FAILED`. The renderer leaves the sources unmarked in `covered.json` too, so the next run re-selects them.
9. **Report once and exit.** Single-line stdout: `SHIPPED <mp3_url> - <title> - <n> chapters - <dur>s - r2=ok` on success (all four values come from the renderer's final JSON: `mp3_url`, `title`, `chapter_count`, `duration_s`), `SKIPPED <reason>` for a thin week, `FAILED <reason>` on genuine failure. `r2=ok` is the only success value here — a publish that did not succeed is a failed run, not a degraded one.

## Setup

One-time, in order.

**1. Config.** `fc_common.load_config` refuses to run without `~/.config/frontier-commits/config.json`. Every key has a default (`fc_common.DEFAULT_CONFIG`), so the file only needs the keys you override — an empty `{}` is a valid start:

```jsonc
// ~/.config/frontier-commits/config.json
{
  "show_name": "Frontier Commits",
  "host_name": "Cory",
  "orgs": [                             // each entry {"name", "filter"}; names must match
    {"name": "anthropics", "filter": "none"},        //   [A-Za-z0-9-]+ (they land in gh api
    {"name": "openai", "filter": "none"},            //   paths); filter is "none" or "ai"
    {"name": "xai-org", "filter": "none"},
    {"name": "google", "filter": "ai"},              // google is huge; the AI filter is mandatory
    {"name": "google-deepmind", "filter": "none"}
  ],
  "ai_topics": ["ai", "llm", "..."],    // topic slugs the "ai" filter matches (case-insensitive)
  "ai_description_patterns": ["..."],   // regexes the "ai" filter matches against descriptions
  "allowlist": {"google": ["langextract", "adk-python", "sam", "artemis", "mantis"]},
                                        // per-org: kept past the "ai" filter AND past the
                                        //   RELEASE star floor. Merged PER ORG under defaults —
                                        //   overriding one org's list keeps the others' defaults.
  "denylist": {},                       // per-org: always dropped, filter or not; same per-org merge
  "lookback_days": 7,                   // baseline distance for detect + release window
  "min_stories_per_episode": 2,         // below this, the weekly run SKIPs (thin week)
  "target_stories_per_episode": 6,      // selection size (≈ 8-12 min episode)
  "per_org_story_cap": 3,               // no lab dominates an episode
  "release_min_stars": 500,             // RELEASE floor (allowlist bypasses it)
  "stale_min_stars": 1000,              // GOING_STALE only watches repos people actually use
  "stale_stages_days": [180, 365],      // the stale stages a repo resurfaces at
  "surge_min_delta_7d": 500,            // STAR_SURGE absolute floor (per 7 days)
  "surge_min_ratio": 0.20,              // ...or 20% of the baseline stars, whichever is larger
  "snapshot_retention_days": 400,       // snapshot pruning window (> a year, for YoY trends)
  "releases_repo_cap_per_org": 30,      // per-org cap on release API calls per sweep
  "r2_bucket": null,                    // labs.json publish ONLY (see Data layer). The EPISODE
  "r2_public_base_url": null,           //   bucket is the daily skill's config — see Manifest.
  "labs_manifest_name": "labs.json"     // object key for the dashboard data
}
```

R2/GH credentials never go in `config.json` — env first, then `~/.config/frontier-commits/secrets.json`, then the daily skill's `secrets.json` (see [Show + state config](#show--state-config)).

**2. Episode R2 (required to ship).** The weekly episode publishes through `render.py`, which resolves the bucket from `~/.config/daily-podcast/config.json` (`r2_bucket`, `r2_public_base_url`) and the credentials from env or `~/.config/daily-podcast/secrets.json` — **not** from the frontier config above. Set `PAGES_DEPLOY_HOOK_URL` there too, or cortech.online will hold the bucket's new episode without rebuilding around it. Web-only mode makes these mandatory: pre-flight refuses the run when R2 is absent, so a misconfigured host fails in seconds instead of after a full render. Verify with a rehearsal:

```bash
python3 ../daily-podcast/render.py --manifest <a-frontier-manifest>.json --dry-run
```

which prints the exact `[r2] dry-run: would publish <url>` the real run would write. There is **no Spotify show to create** — this show ships to the RSS feed, and public Spotify listing (if wanted) comes from submitting that feed.

**3. Daily snapshot job (launchd).** Install `launchd/com.cortech.frontier-commits-snapshot.plist` from this skill directory (it runs `fc_snapshot.py` daily at 06:15 local with the repo clone's path — edit the path if your clone lives elsewhere):

```bash
cp launchd/com.cortech.frontier-commits-snapshot.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cortech.frontier-commits-snapshot.plist
launchctl kickstart gui/$(id -u)/com.cortech.frontier-commits-snapshot
```

Then confirm the log's final line is `SNAPSHOT ok ...`. The detector needs at least one snapshot ≥ `lookback_days` old before delta stories (`ARCHIVED`, `STAR_SURGE`) can fire, so install this a week before expecting a full episode.

**4. Weekly routine.** Schedule Mondays 07:30 local (after the 06:15 snapshot) with this trigger-only prompt, verbatim — a scheduler's prompt is a trigger, never a copy of the steps:

```
You are an unattended invocation. Invoke the `frontier-commits` skill via the
Skill tool, then follow its "Unattended weekly run" section exactly, end to end.
Report its single SHIPPED/SKIPPED/FAILED line to stdout and exit.
```
