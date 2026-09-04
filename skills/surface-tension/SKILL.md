---
id: surface-tension
name: surface-tension
description: Use when the user asks to ship the Surface Tension weekly podcast — turns personal independent blog posts surfaced by community vote on bubbles.town into a four-voice call-in radio episode published to its own public RSS feed via st_gather / st_script_plan / st_write and render.py. Skips the standard production interview because defaults are pre-set.
enabled: true
---

# Surface Tension

A weekly call-in radio show in which a **four-voice panel** argues about **personal independent blog posts surfaced by community vote** on [bubbles.town](https://bubbles.town). Sibling to the daily digest and Frontier Commits — same `render.py` production stack, its own web feed, and the only one of the three whose beat is not tech.

Two things make it different from both siblings, and both are mechanical rather than aspirational:

- **It is a panel, not a narrator.** A segment carries `lines: [{speaker, text}, ...]` (#172) and renders per line into one chapter. Eight chapters, roughly 35–50 speaker turns.
- **The friction is assigned.** Who holds which seat, who argues which side, who opens and who closes, and who runs each bit are all decided by the week's seed before any prose exists — the panel analogue of the daily show's `SHAPE_ORDERS`. Each scene is written by an isolated `claude -p` that cannot see its neighbours, so variety cannot be negotiated between writers; it is handed to them.

**This show is RSS-first.** Its canonical channel is the public feed, and it ships through `render.py`'s web-only mode (`"ship_mode": "web"`): render → artifact gate → R2 publish → deploy hook → dedup. The R2 publish **is** the ship, so its failure fails the run.

## Layout

- `./st_gather.py` — two-source candidate gather (bubbles.town feeds + the OPML "Blogs" category), vote-ranked, metadata only.
- `./st_script_plan.py` — the week-seeded assignment layer: roles, stances, turn order, bit ownership.
- `./st_write.py` — the write layer's deterministic half: the prompt filler, the outcome classifier, the discussion desk's content guards, and manifest assembly.
- `./prompts/write_scene.md` — the per-post scene writer. One isolated `claude -p`, one post body.
- `./prompts/weekly.md` — a stub pointing at *Unattended weekly run* below. It never carries the procedure.

The renderer is the sibling `skills/daily-podcast/render.py`, used unchanged.

## Episode shape

| # | Scene | Source | Voices |
| --- | --- | --- | --- |
| 1 | Station ident + cold open — two voices already mid-argument | — | 2 |
| 2 | Vote desk — the week's numbers, one indefensible ranking | `/feed` inline votes | 1–2 |
| 3–5 | Main posts — assigned stances, adjudicated | `/feed` | 3–4 |
| 6 | Open lines — the board on the argued-about post, and the panel arguing the post itself | `/feed/hot` + `/feed/comments` | 3–4 |
| 7 | Rapid fire (after the bumper) — six takes on six unvoted posts | `/feed/new`, last ~6 h | 4 |
| 8 | Sign-off | — | 2 |

Scenes 1, 2, 7 and 8 are **frames**: they carry `"source_url": null` and an explicit `"title"`, without which the published timeline names their chapters "Segment 1". Scenes 3–6 are **post scenes**, each carrying exactly its own post URL — strict 1:1 segment ↔ chapter ↔ source.

⚠️ **At `target_post_count: 4` the TTS-degeneration gate is disarmed for this show.** `speech_rate_rows` only counts segments carrying a `source_url` — the four frames never join the population — and it returns `[]` below `MIN_RATE_SAMPLE_SEGMENTS = 5`, which its own contract reads as *no evidence of a defect*. The `lines` layer's derived-`text` mitigation (#172) is working and is not the cause; the population is simply one short. Five post scenes arms it. Measured on the first dry run (#176) and tracked separately — do not read a clean run as evidence the gate looked.

`/feed/new` only reaches back about six hours, so the rapid-fire six are *this morning's* unvoted posts, not the week's. Say "this morning", never "this week".

## Script template

### The cast

Four recorded clips, fixed for the life of the show. The manifest `cast` maps **persona → its clip**; the persona is the speaker name a scene's lines carry. `st_write.cast_map` builds it.

| voice | bundled clip | designed as | measured f0 |
| --- | --- | --- | --- |
| `Ryan` | `refs/ryan.wav` | sixties, low, slow, clipped | 113 Hz |
| `Aiden` | `refs/aiden.wav` | late twenties, light, quick, soft | 156 Hz |
| `Ethan` | `refs/ethan.wav` | early forties, thin, even, precise | 92 Hz |
| `Chelsie` | `refs/chelsie.wav` | early thirties, female, brisk, crisp | 261 Hz |

Every clip is an `ref_audio` **clone**, never VoiceDesign at render time: VoiceDesign is a second model that drifts ~2.5% in pacing and audibly in timbre run to run ([`docs/durable-voices.md`](../../docs/durable-voices.md)), which would make the panel a different panel every week. Clones run on the same base model a preset does, so four voices still cost **one** model load. The clips themselves were *made* with VoiceDesign and then locked — Path A in that document, the same way the daily show's house voice was made.

Each `<persona>.wav` ships beside a `<persona>.txt` holding its exact transcript, and both go into the manifest (`ref_audio` + `ref_text`). `render.py` keys a line's cache on the clip's **bytes**, so re-recording one voice re-renders only that voice's lines.

**The speaker is the persona, never the role.** Roles rotate per *scene* while the manifest `cast` is one map for the whole episode, so keying the cast on roles would silently freeze the rotation the assign layer exists to produce. The house voice is deliberately absent: it is the daily show's narrator, not a panelist, and the daily show's identity is the more valuable asset.

⚠️ **Three of the four voices are male, and that is the format's standing sonic risk.** The pitch ladder above is monotone and the tightest pair (`Ethan` 92 Hz / `Ryan` 113 Hz) is about three and a half semitones apart, with speech rates 11% apart on top of it — separable, but the least separable thing about the show. If a fast-cut scene ever reads as one person arguing with themselves, this table is where to look first; `refs/make_cover.py`'s sibling generator script in the PR for #177 shows how the clips were produced.

### The roles

Five roles over four voices, so **one role sits out every scene** — itself a rotation axis, not an accident. `turns` is the seat's turn budget for one scene.

| role | what the seat does | turns |
| --- | --- | --- |
| `anchor` | Sets up the post, owns the running order, hands off. | 2-3 |
| `advocate` | Steelmans the post - why the blogger is right. | 2-2 |
| `skeptic` | Names what is unsupported. Assigned, not felt. | 2-2 |
| `tangent` | Takes the honest swerve - the topical-width engine. | 1-2 |
| `switchboard` | Works the board: reports how many called, from which instances, and when. Never what they said. | 0-2 |

`switchboard` is **conditional**: the plan may assign the seat, but a post with no comments renders no switchboard turn at all. See *The discussion desk*.

### The recurring bits

One owner each per week, assigned off the stance square.

| bit | what it is |
| --- | --- |
| `vote_desk` | Runs the week's vote numbers and defends one indefensible ranking. |
| `rapid_fire` | Runs the post-bumper rapid-fire: six takes on six unvoted posts. |

### The panel square

Row = a panel; column = a role; `-` is the role that sits out. Selected by `ROLE_ORDERS_ST[week % 5][scene % 5]`, so the scene axis has its own rotation rather than a cyclic shift of the week's. **Fixed data — do not regenerate it and do not replace it with arithmetic**: the daily show's stride formula passed a year-long coverage test while pinning three positions to one shape for four days at a time (PR #108).

| row | `anchor` | `advocate` | `skeptic` | `tangent` | `switchboard` |
| --- | --- | --- | --- | --- | --- |
| 0 | `Ethan` | `Aiden` | `Ryan` | - | `Chelsie` |
| 1 | `Chelsie` | `Ryan` | - | `Ethan` | `Aiden` |
| 2 | - | `Chelsie` | `Aiden` | `Ryan` | `Ethan` |
| 3 | `Ryan` | `Ethan` | `Chelsie` | `Aiden` | - |
| 4 | `Aiden` | - | `Ethan` | `Chelsie` | `Ryan` |

Turn order walks the same square from a different row (`TURN_ROW_OFFSET_ST`), so a given panel does not always speak in one order.

### The stance square

Row = the week's table; column = post position. The for-side reads this row; the against-side reads the row two below, and because the square is Latin those never collide. Sized to the cast (4) rather than the role bank (5): **coprime on purpose**, so a panel and a stance table only pair up again after twenty weeks.

| row | pos 0 | pos 1 | pos 2 | pos 3 |
| --- | --- | --- | --- | --- |
| 0 | `Aiden` | `Chelsie` | `Ethan` | `Ryan` |
| 1 | `Ryan` | `Aiden` | `Chelsie` | `Ethan` |
| 2 | `Ethan` | `Ryan` | `Aiden` | `Chelsie` |
| 3 | `Chelsie` | `Ethan` | `Ryan` | `Aiden` |

### Scene length

**900-1500 characters across all of a scene's line texts combined** (`st_write.SCENE_BAND`). The band measures the SUM, never one turn — individual turns are short, and that is what makes a scene sound like a conversation rather than four monologues. A scene whose summed text falls under 500 characters is REFUSED and its post is dropped.

### TTS rules

No headings, no lists, no URLs read aloud, no stage directions, no paralinguistic markers (they do not work on this model). Spell out anything a reader would skim.

### Content rules

- **Not every post is arguable, and this gate belongs in curation.** The pool is
  vote-ranked over *personal* blogs, so it surfaces grief, illness, death, abuse
  and private crisis on their merits — the first live gather ranked a post about
  the author's mother's cancer diagnosis sixth. Every main post is handed an
  assigned FOR and AGAINST, so such a post must never reach a writer with a
  stance attached: skip it at selection and take the next candidate. The writer
  has a second line of defence (`{"ok": false}`, which drops the one scene), but
  the cheap place to stop it is here, before a stance exists.
- **Argue the post's ideas, never the blogger.** These are private individuals writing personal blogs. No speculation about an author's life, motives or identity.
- **Never manufacture a connection** between posts. A vote-ranked digest's neighbours are usually unrelated, and a false link reads worse than a blunt hand-off.
- **End on substance** — never on a pointer to the source, a URL, or "check it out".
- The advocate steelmans and the skeptic names what is unsupported; neither concedes wholesale. Nobody chose their side, which is what makes the friction real.

## The discussion desk

`/feed/comments` carries **no comment bodies** — only navigation links (verified 2026-08-25, #173, pinned by `tests/test_bubbles_fixtures.py`). The desk therefore reports the *board*, never the callers, and these are content rules with tests behind them (`tests/test_switchboard.py`), not style guidance:

| Available | From | On air as |
| --- | --- | --- |
| That a post drew comments, and how many | `slash:comments` on the post entry | "the board lit up on this one" |
| Which post | the post's own title | Naming the post being argued about |
| Which instances called | the permalink **host**, only if the comments feed was supplied | "one from a photography server" |

- **There is no `caller` speaker.** Not in the cast, not in the manifest, not in the plan. A `caller` line is a bug, and `st_write` refuses it.
- **Host, never handle.** The commenter's handle is embedded in the permalink path, which makes it the one personal identifier the feed exposes — and the one the hard rule forbids. Never a handle (`@someone` or `@someone@example.social`), never a display name. `st_write.scene_violations` rejects both shapes, on **every** line, not just the desk's.
- **Never characterise what anyone said.** No quotes, no paraphrase, no "one caller thought". Any wording attributed to a commenter is necessarily fabricated, because nobody ever read it.
- **Never manufacture a call.** No comments → no switchboard turn, and the plan's precomputed `no_discussion` ordering is what the writer is handed so it never sees the contradiction. A fabricated call is strictly worse than a dead phone line: it is unfalsifiable on air.
- **The count claim is checked.** A "four calls" line on a two-comment post is a violation, and instance hosts are only allowed when the comments feed actually supplied them.

Recovering real quotes is one fetch from each permalink and is deliberately **out of scope** — it adds third-party fetching, consent questions and a new failure mode. Take it deliberately or not at all.

## Manifest

A standard `render.py` manifest plus the web-only keys, all required, and the `cast`. `st_write.assemble_manifest` builds it — do not hand-write one.

```json
{
  "title": "Small software, AI writing tells, nine games - Week of August 31, 2026",
  "summary": "This week's one-sentence hook.",
  "date": "2026-08-31",
  "voice": "Ryan",
  "ship_mode": "web",
  "show_name": "Surface Tension",
  "r2_manifest_name": "manifest-surface-tension.json",
  "r2_key_prefix": "surface-tension/",
  "slug_prefix": "surface-tension",
  "description_footer_text": "Posts surfaced by vote on bubbles.town - every post links its blog above. More at cortech.online.",
  "cover_image": "<root>/skills/surface-tension/refs/cover.jpg",
  "cast": {"Ryan": {"ref_audio": "<root>/skills/surface-tension/refs/ryan.wav", "ref_text": "..."}, "Aiden": {"...": "..."}, "Ethan": {"...": "..."}, "Chelsie": {"...": "..."}},
  "segments": [
    {"title": "Cold open", "source_url": null, "lines": [{"speaker": "Ryan", "text": "..."}]},
    {"title": "A post title", "source_url": "https://example.com/a-post",
     "lines": [{"speaker": "Aiden", "text": "..."}, {"speaker": "Ethan", "text": "..."}]},
    {"title": "Sign-off", "source_url": null, "lines": [{"speaker": "Chelsie", "text": "..."}]}
  ]
}
```

- `"ship_mode": "web"` — the web-only ship (#155). Omitting it is not a degraded run but a different one: `render.py` defaults to a Spotify upload. In this mode R2 config is **required** (absent fails pre-flight before any render), a failed publish fails the run, and `covered.json` is written only after the publish succeeds.
- `"tts_engine"` is deliberately absent, so the show renders on `qwen3`. Moving it to another engine is this key plus the episode `voice`: `st_write` emits the `Ryan` preset there (a fallback no scene ever renders with), and an engine without presets — Breeze — refuses it before the model load, so the switch sets `"voice": "house"` alongside (the daily skill's *TTS engines* table lists the engines). Breeze's 500-character take ceiling fits every line this show writes; its weights are non-commercial, so a show on it carries no sponsor reads.
- `"r2_key_prefix": "surface-tension/"` — the slug is date-keyed, so without a prefix an episode publishing the same day as a daily digest overwrites the daily show's `.mp3`/`.jpg` in the shared bucket (#142).
- `"slug_prefix": "surface-tension"` — the `/podcast/<slug>/` permalink and the `isPermaLink` guid, **immutable once published** (#128). It keys on `date` alone; the prefix swaps the literal and never re-couples the slug to the title.
- `"show_name"` and `"description_footer_text"` — without them every cover carries the daily show's branding and every set of show notes credits the daily show's OPML feeds. The footer is plain text by contract; `render.py` rejects markup.
- `"cover_image": "<root>/skills/surface-tension/refs/cover.jpg"` — this show's own album art, used verbatim as the episode cover instead of `build_cover`'s generated gradient (#164). `show_name` alone only changes the *name* on the daily show's template; the art itself stays the daily show's, and that is what a podcast client renders as per-episode artwork. `st_write` emits an **absolute** path resolved off its own file — a relative value resolves against the manifest's directory, and a scheduled run's CWD is arbitrary. Pre-flight fails the run if the file is missing, non-square, or outside 1400-3000px: a directory rejects the whole *feed* over bad art, not just the episode. Regenerate it with `python3 skills/surface-tension/refs/make_cover.py`.
- **No `show_id`.** There is no Spotify show; pre-flight does not ask for one.
- **Title format** — the topic-first style both siblings use, with the weekly tail: `<topic>, <topic>, <topic> - Week of <Month D, YYYY>`. Three short noun phrases from the first three post scenes in running order; hyphens, never em dashes. Display-only free text — the slug and guid key on `date`.

⚠️ **The R2 bucket and credentials come from the daily skill's config** (`~/.config/daily-podcast/config.json` + `secrets.json` + env), never from this show's config dir. `render.py` owns the episode bucket for every show it renders.

## Show + state config

State lives under `~/.config/surface-tension/`, its own root on purpose: a shared `covered.json` would silently withhold a post from whichever show ran second.

- `config.json` — the gather's sources and thresholds; schema in [Setup](#setup). Every key has a default, so `{}` is a valid start.
- `dropped.jsonl` — one record per dropped candidate. Observability only.

Shared with the daily show: `~/.config/daily-podcast/covered.json` (URL dedup, written after the R2 publish) and `runs.jsonl`, where this show's runs appear with `"status": "web-ready"` and the published `mp3_url`.

## Unattended weekly run

**This section is the canonical procedure for shipping an episode with no human in the loop.** It is the single home: a scheduler invokes this skill and follows this section, never carries its own copy ([`prompts/weekly.md`](prompts/weekly.md) is a stub pointing here, and a drift test keeps it one). Be decisive, don't ask clarifying questions, and if you genuinely cannot proceed, exit with a single-line error on stdout.

1. **Resolve paths.** Skill dir: `${CLAUDE_PLUGIN_ROOT}/skills/surface-tension/` when set; when unset (known to happen under scheduled tasks) fall back to the path this SKILL.md was loaded from. The renderer is the sibling `skills/daily-podcast/render.py` under the same root. Workdir: `$TMPDIR/surface-tension-<date>/`.
2. **Gather:** `python3 st_gather.py gather --date <today>` → `<workdir>/candidates.json`. If fewer than two posts survive, print `SKIPPED thin-week (<n> posts)` and exit 0. **No filler episodes.**
3. **Plan:** `python3 st_script_plan.py plan --date <today> --posts <n>` → `<workdir>/plan.json`.
4. **Write each post scene in its own subagent context** — one post's body per context, and nothing else in it. Per post: fill `prompts/write_scene.md` via `st_write.fill_scene_prompt`, read the post at its URL, write the scene, return the JSON contract. Classify each result with `st_write.classify_scene`; a REFUSED/ERROR/BLOCKED scene is dropped and logged, and a run that ends with zero survivors **and any AUTH drop** is a credential failure, not a thin week — report it as such.
5. **Write the frames** in the main context, where the running order is visible: the ident and cold open, the vote desk (from the gathered vote counts), the rapid-fire six (from `rapid_fire`), and the sign-off. Each is a `lines` scene with an explicit title.
6. **Assemble and render.** `st_write.assemble_manifest(<date>, <title>, <summary>, <items>)` → `<workdir>/manifest.json`, then render in the **background**: `python3 <root>/skills/daily-podcast/render.py --manifest <workdir>/manifest.json --workdir <workdir>` — the 10-minute foreground Bash cap kills a long render, so monitor the log instead. Never pass `--dry-run` (this is a real episode) and never pass `--skip-preflight`.
7. **Report once and exit.** Single-line stdout: `SHIPPED <mp3_url> - <title> - <n> chapters - <dur>s - r2=ok` on success (every value from the renderer's final JSON), `SKIPPED <reason>` for a thin week, `FAILED <reason>` otherwise. `r2=ok` is the only success value: a publish that did not succeed is a failed run, not a degraded one.

## Setup

**1. Config.** `st_gather.load_config` **refuses to run without** `~/.config/surface-tension/config.json` — the file must exist, though every key inside it has a default (`st_gather.DEFAULT_CONFIG`), so `{}` is a valid start and you override only what you need:

```jsonc
{
  "show_name": "Surface Tension",
  "bubbles_feeds": ["https://bubbles.town/feed", {"url": "https://bubbles.town/feed/hot", "lookback_hours": null}],
  "rapid_fire_feed": "https://bubbles.town/feed/new",
  "opml_files": [],                     // optional second source; bubbles-only is the shipped default
  "opml_categories": ["Blogs"],         // which OPML folder/category counts as a blog
  "lookback_hours": 168,                // weekly
  "target_post_count": 4,               // main post scenes
  "rapid_fire_count": 6,                // the rapid-fire bit's takes
  "buffer": 4,                          // extra candidates fanned out, so drops still leave a full episode
  "per_domain_cap": 2,                  // no blog dominates an episode
  "opml_share": 0.25,                   // at most this fraction of the pool from OPML
  "variety_days": 21                    // domains covered inside this window are penalised
}
```

`/feed/hot` is ranked by discussion rather than recency, so its lookback is explicitly unbounded — a uniform window would discard exactly the posts that feed exists to surface. Votes carry it instead.

**2. R2.** The episode publish reads the **daily skill's** R2 config and credentials (see the warning in [Manifest](#manifest)). Nothing extra is needed here.

**3. Cast and art.** Done (#177): four recorded `ref_audio` clips under `refs/` and this show's own cover, wired through `st_write.cast_map` / `cover_image`.
