# Show Your Work — design spec

**Date:** 2026-09-26
**Status:** Approved design, pre-implementation. Amended 2026-09-26 during planning (§4.2 `observed.json` + `commit`, §4.3 banks + casebook rule, §4.4 fixed Anthropic reminder + casebook template + inline `basis`, §4.5 band semantics), and again for #236 (§4.3 undated items, §4.8 a crash between render and commit); each amendment is marked *Amended*.
**Decisions locked by Cory:**
- audience = **public explainer** (newcomers, YouTube-forward)
- shape = **one feature + short briefs**, weekly
- voices = **two: Explainer + Skeptic**
- video = **redrawn visual beats**; no third-party figures on screen
- sources = **frontier labs lead, independent orgs check**
- architecture = **sibling show + a post-ship video stage** (approach 1 of 3)
- name = **Show Your Work**
- fourth skill in clodcast, reusing `render.py` in web-only mode with **zero `render.py` changes**

## 1. What this is

A weekly podcast, with a video cut for YouTube, that explains frontier-lab alignment and safety
research to people who don't read papers. Each episode is:

1. a cold open
2. one **feature**: one post explained in five dialogue scenes
3. up to four short **briefs** on the rest of the week's lab output
4. a sign-off

The two voices are roles, not invented people. The **Explainer** states the post's claim as the
post states it, always attributed. The **Skeptic** asks the question a newcomer would ask, then
pushes back.

The show has a conflict of interest built in: it is written and voiced by Claude, an Anthropic
model, and Anthropic is one of the labs it covers. Three mechanisms answer that, and none of them
is optional:

| Mechanism | Where |
| --- | --- |
| A disclosure sentence in every cold open, plus a reminder at the start of any Anthropic feature | §4.4, §6 |
| Every pushback must trace to the post's own stated limitations or to an independent source | §4.5 (`basis`) |
| A lab penalty so one lab can't hold the feature slot week after week | §4.3 |

**Name rationale:** "Show your work" is the plain-language form of chain-of-thought
transparency, and it is what the Skeptic demands of every claim.

## 2. Grounding data (recon 2026-09-26)

Measured by a research pass on 2026-09-26. Every feed was fetched with curl and parsed with
Python's XML parser. "3-mo" means 2026-06-26 to 2026-09-26. Anything not measured is marked
UNVERIFIED.

### 2.1 Sources

| Source | Role | Index | Feed | 3-mo items | Figures |
| --- | --- | --- | --- | --- | --- |
| Anthropic Alignment Science blog | lead | `https://alignment.anthropic.com/` | **none** (rss/feed/atom/index.xml and sitemap all 404); static HTML, **month-level dates only** | 8 | heavy, some interactive |
| Anthropic research (alignment + interpretability teams) | lead | `anthropic.com/research/team/alignment`, `…/interpretability` | none; server-rendered, dates in raw HTML; sitemap `lastmod` was bulk-rewritten Sep 10–11 and is **useless for dating** | 3 + 1 | yes, plus PDFs |
| transformer-circuits.pub | lead | `https://transformer-circuits.pub/` | `https://transformer-circuits.pub/feed.xml` (Atom, valid, 56 entries) | 3 | heavy |
| OpenAI Alignment Research blog | lead | `https://alignment.openai.com/` | `https://alignment.openai.com/rss.xml` (valid, 19 items) — **incomplete**: omits openai.com cross-posts (↗ on the index), all misalignment reports, and the Mar 16 "Metagaming" post | 4 listed | 5–7 images/post |
| OpenAI Misalignment Reports | lead | `https://alignment.openai.com/misalignment-reports/` | **none**; static `<details class="cb-entry" data-date data-title>` markup | 9 reports + 3 notices (since 2026-08-26) | ~none |
| Google DeepMind safety | lead (low yield) | `deepmindsafetyresearch.medium.com`; `deepmind.google/blog/` | Medium `/feed` (valid, 10-item cap); `deepmind.google/blog/rss.xml` (valid, **no categories**) | 0 on Medium since 2026-05-29; ~0–1 safety posts on the main blog | yes |
| METR | check | `metr.org` | `https://metr.org/feed.xml` (valid) | 14 | yes |
| Redwood Research | check | `blog.redwoodresearch.org` | `https://blog.redwoodresearch.org/feed` (Substack, valid) | 15 | some |
| Goodfire | check | `goodfire.ai/research` | `https://www.goodfire.ai/research/rss.xml` (valid) | 11 | heavy |
| AI Alignment Forum | check | `alignmentforum.org` | `https://www.alignmentforum.org/feed.xml?view=curated-rss` (valid) | ~60+ (estimate) | varies |

**Excluded from v1:**
- Meta FAIR (curl gets a 400), xAI (403), Microsoft AI (its feed has 0 items), Amazon and
  Mistral: none of them has an alignment-research venue or published alignment research in the
  window.
- OpenAI News (Safety/Security categories) and the Deployment Safety Hub (system cards): too
  product-shaped for v1.
- Apollo, UK AISI, Transluce and MATS: sitemap only, deferred to v2 as check sources.

### 2.2 Misalignment reports (the distinctive content type)

- **What it is:** a public casebook of specific misalignment incidents seen in OpenAI's RL
  training and internal deployment. It launched 2026-09-16 together with OpenAI's reporting
  framework.
- **Contents so far:**
  - 6 reports at launch
  - 3 more on 2026-09-25
  - 3 **notices** (Aug 26, Sep 5, Sep 11): one-paragraph placeholders issued before a full report
- **Index entry:**
  - title
  - "Report · Updated <date> · <setting>"
  - a 1–2 sentence Observation
  - Model
  - Observed during (RL training / RL self-play / internal deployment)
- **Full report:**
  - Header: model · phase, plus Sample, Discovery and Updated dates.
  - Sections: Summary → What happened → Investigation → How we are addressing it.
  - Length: 418–3,589 words.
  - The main content is **redacted, role-labeled transcript excerpts**: `CoT`, `Tool call`,
    `Tool result`, `Model response`, `User`.
  - The reports have almost no figures. The transcripts themselves are the visual material (§4.7).
- **Framework page:** it returns 403 to bots, so the review-track details (Ready for Disclosure /
  Minor / Larger Investigation → Notice first) come from secondary coverage and are **UNVERIFIED**
  against the primary page. Nothing in this design depends on them.

### 2.3 Licensing

**No source grants reuse of its figures:**
- Anthropic, OpenAI and GDM state only copyright and terms.
- METR, Goodfire and Transluce say "All rights reserved".
- alignment.anthropic.com is **not CC-BY**. Its only license text is Apache-2.0 for the Distill
  template *code*.

This is why video visuals are **redrawn from numbers stated in the post text** (§4.5). Numbers
aren't copyrightable, and the fair-use question never has to be answered. Short verbatim quotes
(≤25 words, attributed) are used as commentary.

### 2.4 Lab video

- Anthropic's channel is `https://www.youtube.com/@anthropic-ai`
  (feed: `https://www.youtube.com/feeds/videos.xml?channel_id=UCrDwWp7EBBv4NwvScIpBDOA`).
- It has explainers whose dates match interpretability papers exactly (May 7, Apr 2, Jul 6). The
  pairing is inferred from dates.
- No alignment-blog videos were found.
- Video licensing is UNVERIFIED; assume standard YouTube terms.
- **Policy:** link to them in the description, never clip them.

### 2.5 Host facts

Verified on this Mac on 2026-09-26:
- `playwright` 1.58.0 and `mlx-whisper` 0.4.3 are importable.
- The video spike this design productionizes is at
  `~/Movies/clodcast-video/fc-week-of-september-14/spike/` (`prepare.py`, `render_ep.py`,
  `template*.js`). It rendered a full 8:25 episode in 97 s (8 Playwright workers, M4 Max).
- The spike drives the `terminal-shorts` kit (`~/clodcast/terminal-shorts.zip`). The kit ships
  **no license for its code** (only `kit/fonts/OFL-LICENSE.txt`), so production code **does not
  copy the kit**. It implements its own frame loop (§4.7).

## 3. Architecture

This is the same three-layer division as Frontier Commits and Surface Tension:
- deterministic Python gathers and assigns
- Claude writes only prose and beats
- `render.py` renders and ships

The video is a fourth layer that runs **after** the ship and can never fail it.

```
 gather (syw_gather.py — pure Python, no LLM, metadata only)
   lead adapters ─┐                         check adapters (METR, Redwood,
   (6 sources)    ├─▶ items (url, source, lab, kind, title, date, …)   Goodfire, AF)
                  │                                    │
 plan (syw_script_plan.py — deterministic, persisted to <wd>/plan.json)
   feature pick ▸ briefs ▸ casebook fold ▸ check matching ▸ week-seeded rotations
                  │
 digest (one subagent per body) ──▶ capped fact sheets for checks + casebook incidents
                  │
 write (syw_write.py + prompts/) — one body + N digests per context
   feature: 5 scenes of {speaker, text} + beats + basis
   briefs:  short scenes of {speaker, text} + beats
   open / sign-off: from titles + plan only
                  │
 validate (syw_write.py) — shape, bands, basis, beat guards (fetches post text itself)
                  │
 assemble ─▶ <wd>/manifest.json (lines only)   <wd>/beats.json (sidecar)
                  │
 render + ship (render.py, ship_mode: web — UNCHANGED)
                  │  exit 0 and r2_status == "published"
                  ▼
 commit ledger (syw_gather.py --commit-seen <wd>/plan.json)
                  │
 video (syw_video.py — post-ship; VIDEO ok|FAILED; never touches the ship)
   whisper words ▸ align lines + cues ▸ HTML template ▸ Playwright frames ▸ ffmpeg mux
```

Files, all under `skills/show-your-work/`:
- `SKILL.md`
- `syw_gather.py`
- `syw_script_plan.py`
- `syw_write.py`
- `syw_video.py`
- `prompts/write_feature.md`
- `prompts/write_brief.md`
- `prompts/write_casebook.md` (*Amended:* the casebook writer holds no body, so it gets its own template with no URL placeholder)
- `prompts/digest.md`
- `prompts/weekly.md` (a stub)
- `video/template.html` (with inline JS/CSS)
- `refs/cover.jpg`

## 4. Components

### 4.1 Config and state root — `~/.config/show-your-work/`

`syw_gather.load_config` refuses to run without `config.json`. Every key has a default
(`DEFAULT_CONFIG`), so `{}` is a valid start. Keys:
- `max_age_days`: 21
- `check_lookback_days`: 60
- `max_briefs`: 4
- `casebook_max`: 3
- `lab_penalty_weeks`: 2
- `adapters`: an enable list, defaulting to every adapter in §2.1

`tests/conftest.py` must register this root's writable paths, the way it already redirects every
daily-podcast path, and must assert afterwards that none of them points at the real directory.

### 4.2 Gather — `syw_gather.py`

**Adapters** each return `list[Item]` and each fail independently. A failed adapter writes one
record to `dropped.jsonl` and the others continue. **If every lead adapter fails, the run
fails.**

**Item schema:**

| Field | Notes |
| --- | --- |
| `url` | identity; normalized (scheme, trailing slash, no fragment) |
| `source` | adapter name |
| `lab` | `anthropic` / `openai` / `gdm` for lead items; the org slug for check items |
| `kind` | `research` / `incident` / `notice` |
| `title`, `summary` | `summary` capped at `SUMMARY_MAX_CHARS`, the Surface Tension posture: feeds that inline whole bodies must not leak them into ranking |
| `date`, `date_precision` | `day` / `month` / `first_seen` |
| `role` | `lead` / `check` |
| `mentions` | check items only: the set of normalized URLs and titles found in the feed content (§4.3). The content itself is discarded after this scan |

**Rules:**
- **"New" means not in `seen.json`**, never "inside a date window". The Anthropic index has
  month-level dates, so a lookback window is wrong at month boundaries.
- **Parsing zero items is a failure; finding zero *new* items is not.** Every scraped index always
  lists posts, so zero parsed means the markup changed. That adapter fails loudly instead of
  reporting a quiet week.
- **The OpenAI alignment adapter reads the RSS and scrapes the index**, then unions the two by
  URL, because the RSS is incomplete (§2.1).
- **Notices** are `kind: notice`. When the full report appears under its own URL, that is a new
  `incident` item.
- **GDM keyword filter:** the main blog feed has no categories, so a closed keyword list decides
  which items are safety items, matched as whole words on title and summary. The list is a module
  constant with a test. *Amended (execution):* the shipped list is `GDM_SAFETY_KEYWORDS` in
  `syw_gather.py`: `evaluation` is dropped as too broad, and scheming/deception/oversight terms
  are added. The fixture check confirmed it excludes all 25 captured product posts.
- **`seed`** marks everything currently parsed as seen and ships nothing, so episode one covers
  only what arrives after installation. *Amended (execution):* `seed` writes nothing and prints
  `SEED FAILED` if ANY lead adapter errored, and the `gather` CLI refuses until `seen.json`
  exists. A partial seed, or none at all, would otherwise present the whole back catalogue as
  new.
- **`commit --plan <plan.json> --render-output <render.log>`** (*Amended:* was `--commit-seen`)
  adds the URLs that **aired** to `seen.json` and appends `features.jsonl`. It refuses unless the
  renderer's final JSON reports `status: web-ready` **and** `r2_status: published`. *Amended
  (execution):* "aired" is `<wd>/aired.json`, written by `syw_write.py assemble`: the feature, each
  assembled brief, and only the casebook incidents that had a digest. A refused brief or an
  undigested incident is never marked covered. The ship gate lives in code, not in the procedure's prose. Leftover
  pool items are deliberately *not* committed, so they compete again next week.
- *Amended:* **`observed.json`** records the first date each lead URL was parsed. It is written
  on every gather, because an observation withholds nothing. A month-precision item (the
  Anthropic index) ages from its first observation. Aged from the first of its month, a
  late-August post would fall out of a 21-day pool the week it appeared.

### 4.3 Plan — `syw_script_plan.py`

Deterministic and pure. Persisted to `<wd>/plan.json`, and a re-run reads an existing
`plan.json` rather than re-planning. Same resume posture as the daily show's date-seeded
rotations.

**Pool:** lead items not in `seen.json`, whose effective date is within `max_age_days`. The
effective date is `date`, except for month-precision or undated items, which use
`first_observed` (§4.2). *Amended (execution):* a month-precision item must also satisfy
`month_end >= first_observed - max_age_days`. A post cannot have been older than the window on
the day it was first seen, which keeps a never-seen 2024 post out of the pool. *Amended (#236):*
an **undated** lead item is not in the pool at all. With no date there is nothing to bound, so a
never-seen old post whose date the parser lost would read as new. Every lead item in the
2026-09-26 fixtures is dated, so this excludes nothing a source publishes correctly.

**Feature score:**
- recency
- minus a **lab penalty** if the item's lab had the feature in any of the last
  `lab_penalty_weeks` episodes
- minus a **kind penalty** if last week's feature had the same `kind`
- ties broken on `url`
- **notices are never features**

The previous features' lab and kind are read from `features.jsonl` (§7), which is appended
after each successful ship. `--feature <url>` overrides the pick and is recorded in `plan.json`
as `feature_override: true`.

**Briefs:**
- Up to `max_briefs` slots, filled by the same score.
- *Amended:* **every non-feature incident and notice** goes to **one "From the casebook"
  brief** of up to `casebook_max` entries, incidents before notices. That way a burst of reports
  (the launch had 6) can't take every slot. The casebook takes one slot when non-empty, and
  research items fill the rest.
- Notices are eligible only as casebook entries.

**Check matching:**
- A check item (within `check_lookback_days`) attaches to the feature if its `mentions` contain
  the feature's normalized URL or normalized title (lowercase, punctuation stripped, ≥4 words).
- This is a plain string test in Python. No model sees a check body at this stage.
- The plan records `checks: []` when nothing matches. Scene 4 then argues from the post's own
  stated limitations.

**Assigned variety** is seeded by `week_index(date)`, with a date as the seed, never a random
draw. That is what makes a re-run rebuild the same episode. Assigned:
- cold-open mode
- fourth-wall angle
- **feature opening move** (`excerpt` / `number` / `scenario` / `question`)
- which role speaks first
- sign-off button angle

Bank lengths are chosen so that no two rotations lock into a fixed pairing. The
`test_fourth_wall_angle_is_not_locked_to_the_intro_mode` lesson applies: a test pins every pair
of banks to a combined cycle equal to the product of their lengths.

*Amended:* the lengths are intro modes **3**, fourth-wall angles **4**, opening moves **5** (adds
`claim`), first speaker **2**, and buttons **7**. These are pairwise coprime except fourth-wall
× first speaker (4, 2). That pair is exempt because the two never share a scene: one is in the
cold open, the other in scene 1. Each bank names an *angle*,
never a phrase, and ships with burned example lines (the `FALLBACK_BUTTONS` pattern).

**Feature arc**, fixed as 5 scenes, each one `lines` segment and one chapter:

| # | Slot | Job |
| --- | --- | --- |
| 1 | `hook` | the assigned opening move; why a newcomer should care |
| 2 | `method` | what the researchers did, with every term of art defined on first use |
| 3 | `finding` | what they found, attributed |
| 4 | `pushback` | the Skeptic's objections, each with a `basis` (§4.5) |
| 5 | `stakes` | why it matters and what's still open |

### 4.4 Digest and write — `syw_write.py`, `prompts/`

**One-body invariant**, carried over from the orchestrator: no model context holds more than one
article body. The design splits the work into digesting and composing:
- A **digest** comes from `prompts/digest.md`, one subagent per body. It is a capped JSON fact
  sheet: `{url, claims[], numbers[], limitations[], transcript_excerpts[]}` with a total cap of
  `DIGEST_MAX_CHARS`.
- A **composing** writer gets **at most one body plus any number of digests**.

| Writer | Body it holds | Digests it gets |
| --- | --- | --- |
| feature (`prompts/write_feature.md`) | the feature post | one per matched check |
| brief (`prompts/write_brief.md`) | its one post | none |
| casebook brief | **none** | one per incident (1–3) |
| cold open / sign-off | none | none; titles + plan only |

**The feature writer is a single context that writes all five scenes.** A per-scene writer
couldn't see its neighbours, so terms would get defined repeatedly and the arc would fragment.
Its output contract is one JSON object:

```json
{"ok": true,
 "scenes": [{"slot": "hook", "lines": [{"speaker": "explainer", "text": "…"}],
             "beats": [{"line": 0, "cue": "…", "type": "number", "…": "…"}]},
            {"slot": "pushback", "lines": [{"speaker": "skeptic", "text": "…", "basis": "post-limitations"}], …},
            …]}
```

*Amended:* `basis` rides inline on each Skeptic line of the pushback scene. `basis` and `beats`
never enter the manifest (§4.6).

**Cold open:**
- It carries the assigned fourth-wall sentence: the show is written and voiced by Claude, an
  Anthropic model, and Anthropic is one of the labs covered. The wording is written fresh, and the
  example lines are burned.
- The line is dry, not a joke. The sign-off's button carries the machine joke.
- **An Anthropic feature adds one plain reminder sentence at the start of scene 1.** *Amended:*
  the reminder is a fixed line, `ANTHROPIC_REMINDER`, that the assembler prepends as an
  Explainer turn, not a line a writer is asked for. A disclosure is the one line that *should*
  be identical every time, and a fixed line can be tested.

**Initial length bands** (characters of spoken text; Phase 2 tunes them against measured
durations). *Amended:* a band is guidance to the writer. Validation refuses only below
`MIN_SEGMENT_CHARS` (500, the daily show's drop floor) or above 1.5× the band's ceiling (a
runaway):

| Segment | Band |
| --- | --- |
| cold open | 400–800 |
| feature scene | 1500–2400 |
| brief | 800–1300 |
| casebook brief | 1100–1800 |
| sign-off | 250–500 |

A brief is an Explainer scene with **at most one** Skeptic line.

**Failure handling:**
- The feature writer's result is classified the Surface Tension way (`classify_scene`:
  OK / REFUSED / ERROR / BLOCKED / AUTH).
- A non-OK feature retries once, then **re-plans with the next-ranked candidate**, recording the
  dropped URL in `plan.json`. Two failed features means `FAILED`.
- A failed brief is dropped and logged.
- Zero surviving writers with any AUTH drop is reported as a credential failure, not a thin week.

### 4.5 Validation and the visual-beat contract

`syw_write.validate_feature` / `validate_brief` run in Python after writing. **The validator
fetches the post text itself**, with the same fetch the writer used, reduced to text, so the
verbatim and number guards don't trust the writer's report.

**Scene-level checks**, where a failure refuses the scene and triggers the §4.4 failure path:
- speakers are in `{explainer, skeptic}`
- the slots appear in order
- each segment is above `MIN_SEGMENT_CHARS` and under 1.5× its band's ceiling
- the pushback scene has ≥1 Skeptic line
- **every Skeptic line in `pushback` has a `basis` that is `post-limitations` or a URL in
  `plan.checks`**. Any other value refuses the scene. This makes "never invent an objection"
  mechanical instead of a matter of prose.

**Beat whitelist**, closed. An unknown `type` or key drops the beat.

| `type` | Fields | Guard |
| --- | --- | --- |
| `number` | `value`, `unit`, `label` | `value` appears in the post text as a numeric token (commas stripped; the writer must use the number as written, e.g. `12` for "12%", never `0.12`) |
| `chart` | `kind` (`bar`/`line`), `title`, `x_label`, `y_label`, `series[{label, points[[x,y]]}]`, `credit` | **every y value** passes the `number` guard; ≤4 series, ≤12 points; `credit` is auto-set to "Data from <lab>, <title>" |
| `quote` | `text`, `attribution` | ≤25 words; whitespace-normalized verbatim substring of the post text |
| `transcript` | `role` (`cot`/`tool_call`/`tool_result`/`model`/`user`), `text` | verbatim substring (redaction markers preserved); ≤600 chars |
| `diagram` | `nodes[{id,label}]` (≤6), `edges[{from,to,label}]` | original work; shape check only |
| `term` | `term`, `definition` | first occurrence of `term` in the episode only |

Every beat carries `line` (an index into its scene's lines) and `cue` (a substring of that line's
text). A missing or mismatched cue falls back to the start of the line.

**A failed beat is dropped, logged, and never fails the scene.** Beats are the video layer, and
the audio never depends on them.

**Chart data only ever comes from numbers stated in the post text, never read off a figure
image.** This rule is what makes a hallucinated chart impossible to publish.

### 4.6 Assemble, render, ship

`syw_write.assemble_manifest` writes two files:
- `<wd>/manifest.json`, with lines reduced to `{speaker, text}`
- `<wd>/beats.json`: `{"version": 1, "segments": {"<seg_index>": [beat, …]}}`, plus each
  beat's scene-relative `line`

Beats and `basis` stay out of the manifest even though `_validate_scene` currently tolerates
extra line keys. Relying on that tolerance would couple this show to an accident of `render.py`.

**Manifest keys:**

| Key | Value | Why |
| --- | --- | --- |
| `ship_mode` | `"web"` | RSS-first; R2 is the ship |
| `show_name` | `"Show Your Work"` | |
| `r2_manifest_name` | `"manifest-show-your-work.json"` | never upserts into another show's feed |
| `r2_key_prefix` | `"show-your-work/"` | a same-day slug can't overwrite another show's mp3 (#142) |
| `slug_prefix` | `"syw-week-of"` | permalink and `isPermaLink` guid namespace; matches `[a-z0-9]+(-[a-z0-9]+)*` |
| `cast` | `{explainer: <preset>, skeptic: <preset>}` | two Qwen3 presets on the base model; clip clones later |
| `description_footer_text` | the disclosure + "charts are redrawn from numbers reported in each post" | show notes |
| `segments[].source_url` | the feature's first scene and each brief; `null` elsewhere | chapter links |

**No `tts_engine` key** (Qwen3 default) and **no `music`** in v1.

`tests/test_sandbox_fixture.py` is extended to re-derive these three namespace keys from
`syw_write`'s constants. It fails if Show Your Work collides with the daily show, Frontier
Commits, or the sandbox.

**Workdir:** `$TMPDIR/daily-podcast-show-your-work-<date>`, always passed explicitly.
- An explicit workdir is never auto-deleted on success, so it survives for the video stage.
- Its `WORKDIR_PREFIX` (`daily-podcast-`) match lets the existing `--prune-workdirs N` clean it
  up later.
- No new retention code.

**Ship:** `render.py --manifest <wd>/manifest.json --workdir <wd>`, run **in the background** and
monitored through its log. That's the 10-minute Bash cap memory. Then:
- Once `render.py` has exited, `syw_gather.py commit --plan <wd>/plan.json --render-output
  <wd>/render.log` marks the items in `<wd>/aired.json` seen and appends
  `{date, feature_url, lab, kind}` to `features.jsonl`. It refuses unless the renderer's final JSON
  reports `status: web-ready` and `r2_status: published`.
- Otherwise nothing is written, and every item returns to the pool.

**Known cross-show interaction, accepted:**
- `render.py` writes each non-null `source_url` into the **shared**
  `~/.config/daily-podcast/covered.json`. The daily show will therefore skip a lab post Show Your
  Work already covered.
- The reverse never happens, because this show reads only `seen.json`.
- This is accepted: the audience overlaps on cortech.online, and double coverage is worse than
  none.

### 4.7 Video stage — `syw_video.py`

**Contract:**
- `python3 syw_video.py --workdir <wd>` runs after the ship.
- Its final stdout line is `VIDEO ok <mp4> duration=<s> beats=<shown>/<total>` or
  `VIDEO FAILED <reason>`.
- It never writes `seen.json`, `covered.json`, the R2 manifest, or `runs.jsonl`.
- Its failure leaves the shipped episode and the workdir untouched, and it can be re-run by hand.

**Pre-flight:** `importlib.util.find_spec` for `playwright` and `mlx_whisper`, and
`chromium` launchable, all checked before any frame renders. This is the bench-extra posture.
Declare a `video` optional-dependency extra in `pyproject.toml` (`playwright`, `mlx-whisper`),
imported function-locally.

**Inputs**, all already in the workdir: `manifest.json`, `timeline.json` (`start_time_ms` per
chapter), `beats.json`, `plan.json`, `episode.mp3`.

**Timing:**
1. mlx-whisper word timestamps on `episode.mp3`, cached to `<wd>/video/words.json`.
2. Each manifest line's text is matched **in order** to the word stream, inside its chapter's
   window from `timeline.json`. The match tolerates TTS prep spellings (`display_text` from the
   spike: "A I" → "AI").
3. A beat starts when its `cue` is spoken and ends at the next beat or the end of the scene.
4. Fallbacks:
   - a cue that isn't found starts the beat at the start of its line
   - a line that isn't found is timed proportionally by character count within its chapter
5. Every fallback is counted and reported. None of them fails the stage.

**Frame composition** (`video/template.html`, 1920×1080, 30 fps). Always on screen:
- the show mark
- a chapter rail
- a speaker indicator (Explainer or Skeptic lit)
- a caption of the current line
- on feature scenes, a source credit ("Source: <lab> — <title>")

The **beat panel** draws the active beat:

| Beat | Rendering |
| --- | --- |
| `number` | large number card |
| `chart` | SVG redrawn from the series data, with the credit always visible |
| `quote` | quote card with attribution |
| `transcript` | a block per role: `cot` muted, `tool_call`/`tool_result` monospace |
| `diagram` | left-to-right layout |
| `term` | glossary card |

The cold open carries a disclosure lower-third.

**Visual design is its own approval gate.** A static mock frame for each beat type goes to Cory
before the template is built (Phase 4). It is a new look, not the daily "field notes" template.

**Renderer:** its own frame loop, modeled on the spike's `render_ep.py`, not the kit's code
(§2.5):
- N parallel Playwright workers, each seeking the template to frame times and screenshotting
- ffmpeg concatenates the parts and muxes `episode.mp3` (AAC 192k, 48 kHz, `+faststart`)
- a contact sheet goes to `final_sheet.jpg`

**Outputs:**
- `<wd>/video/<slug>.mp4`
- `final_sheet.jpg`
- `captions.srt`, built from the whisper words
- `youtube-description.txt`:
  - chapters in YouTube's required form (first at `0:00`, ≥3 chapters, each ≥10 s)
  - source links
  - Anthropic YouTube explainers matched by date (§2.4), linked
  - the disclosure

All outputs are copied to `~/Movies/clodcast-video/<slug>/` for manual upload.

**Gate:** ffprobe confirms:
- the duration is within ±0.5 s of `episode.mp3`
- 1920×1080 resolution
- one audio stream

A failure is `VIDEO FAILED`.

**Upload** stays manual. A YouTube API upload is locked to private until the API project passes
Google's audit.

**Recommendation (Cory's call):** do *not* add this show's RSS feed to YouTube's RSS ingestion.
Otherwise every episode also appears as audio over a static cover next to the real video.

### 4.8 Unattended weekly run

- **Trigger:** a weekly Claude routine whose prompt is a trigger only ("invoke the
  show-your-work skill and follow *Unattended weekly run*").
- **One home for the procedure:** SKILL.md's *Unattended weekly run* section.
  `prompts/weekly.md` is a stub, and a test fails if it inlines steps (the
  `test_daily_prompt_stays_a_stub` precedent).
- **Run day:** chosen so this run doesn't share the Mac's TTS with the Frontier Commits or
  Surface Tension runs. Read their current schedules (`mcp__scheduled-tasks__list_scheduled_tasks`)
  before picking.
- **Reporting line:** `SHIPPED <mp3_url> feature=<url> briefs=<n> gather_errors=<n> r2=ok` or
  `FAILED <reason>`, followed by the video line.
- **Quiet week:** a week with no eligible lead items reports `SKIPPED no new lab items` and ships
  nothing.
- *Amended (#236):* **a crash between render and commit.** `assemble` refuses
  (`ASSEMBLE FAILED already published`) a workdir whose `render.log` already ends in a
  `web-ready` + `published` result. The procedure then runs `commit` against that log instead
  of rendering again, because R2 objects are immutable-cached and a second publish of the slug
  fights the edge cache.

## 5. Episode shape

| Part | Chapters | Target |
| --- | --- | --- |
| cold open (incl. disclosure) | 1 | 30–50 s |
| feature: hook, method, finding, pushback, stakes | 5 | 10–12 min |
| briefs (0–4, incl. at most one casebook) | 0–4 | 60–90 s each |
| sign-off (with button) | 1 | 20–30 s |
| **total** | 7–11 | **~13–18 min** |

## 6. Editorial rules

These go in SKILL.md, and where marked they are enforced in code:

1. The Explainer attributes every claim to its source ("OpenAI reports…"); a finding is never
   stated as settled fact.
2. Every pushback traces to the post's stated limitations or to a matched independent source.
   **Enforced by `basis`.**
3. Never call a lab "better" or "worse" at safety.
4. Define every term of art on first use (`term` beats support this on screen).
5. A misalignment report is told as what the lab says happened. Add no drama the report doesn't
   contain.
6. Quotes and transcript excerpts are verbatim. **Enforced by the beat guards.**
7. Charts use only numbers stated in the post text. **Enforced.**
8. Disclosure in every cold open, plus a reminder at the start of any Anthropic feature.

## 7. State inventory — `~/.config/show-your-work/`

| File | Written by | Contract |
| --- | --- | --- |
| `config.json` | human | §4.1; `{}` is valid |
| `seen.json` | `syw_gather.py commit` / `seed` | URL → `{date, role: feature\|brief\|seeded}`; atomic write; only after a successful ship (or seed) |
| `observed.json` | `syw_gather.py gather` / `seed` | URL → first date parsed; written every gather; drives month-precision aging (*Amended*) |
| `features.jsonl` | the unattended procedure, after ship | append-only `{date, feature_url, lab, kind}`; drives the lab and kind penalties |
| `dropped.jsonl` | gather + write | append-only; observability only |

**Shared, not owned:**
- `~/.config/daily-podcast/covered.json` (written by `render.py`, §4.6)
- `runs.jsonl` (this show's runs appear as `web-ready`)
- `bloopers/`

## 8. Testing

This mirrors the existing suites. Commit tests sized like the neighbours'.

- `test_syw_gather.py`:
  - per-adapter parsing against **captured fixtures** in `tests/data/syw/` (one HTML or XML
    snapshot per source, captured in Phase 1)
  - zero-parsed fails, zero-new passes
  - RSS ∪ index union for OpenAI
  - notice vs incident
  - GDM keyword filter
  - `summary` cap
  - `--seed` and `--commit-seen` atomicity
- `test_syw_script_plan.py`:
  - determinism: same inputs give the same `plan.json`
  - lab and kind penalties
  - notices are never features
  - casebook fold at `casebook_max`
  - leftovers are not committed
  - check matching by URL and normalized title
  - `--feature` override
  - no two rotation banks locked (the product-of-lengths cycle)
- `test_syw_write.py`:
  - every beat guard, each proved to reject: a number not in the text; a chart y value not in
    the text; a non-verbatim quote; a transcript over the cap; an unknown type; an unknown key
  - `basis` refusal
  - a dropped beat keeps its line
  - the manifest carries only `{speaker, text}` lines
  - the digest cap
  - the one-body invariant, asserted on the filled prompts (a feature prompt contains exactly
    one body placeholder)
- `test_syw_video.py`:
  - line-to-word alignment against a **captured whisper fixture**, including a TTS-spelling case
    and a missed-cue fallback
  - the YouTube chapter-format rules
  - SRT formatting
  - the ffprobe gate on a synthetic mp4
- `test_syw_video_smoke.py`: **local only**, skipped unless Playwright and Chromium are
  available. It renders one frame per beat type and asserts no page errors.
- `test_sandbox_fixture.py` (extended): namespace collision covers Show Your Work.
- `test_syw_skill_md.py`: a drift test tying SKILL.md's arc, beat and bank tables to the code
  (the `test_st_skill_md.py` precedent), plus the weekly stub test.

## 9. Phasing

1. **Gather + seed.**
   - Capture fixtures for all ten adapters.
   - Build `syw_gather.py` and its tests.
   - Run `--seed` on the host.
2. **Plan + write, first dry run.**
   - Build `syw_script_plan.py`, `syw_write.py` and the prompts.
   - Dry-run an episode (`render.py --dry-run`) and listen to it.
   - Tune the §4.4 bands against measured durations.
   - Pick the two presets by ear.
3. **First ship.**
   - Cover art.
   - A sandbox-namespace publish (`tests/data/sandbox_manifest.json` pattern) to prove the new
     keys.
   - Then episode one, live.
   - Confirm both other shows' manifests are byte-identical afterwards (the 2026-09-10 sandbox
     check).
4. **Video.**
   - Send Cory static mock frames for each beat type and **wait for approval**.
   - Then build the template, `syw_video.py` and tests.
   - Run it on episode one's kept workdir.
5. **Schedule.** Pick the run day (§4.8), then create the weekly routine.

## 10. Dependencies outside this repo

- **cortech.online** (the feed-generating repo; required checks include Prettier
  `format:check`):
  - a `/show-your-work/` page
  - an RSS feed built from `manifest-show-your-work.json`

  File this as an issue there in Phase 3. Episode one can be published to R2 and checked by URL
  before the page exists.
- **YouTube:** manual upload per episode; the ingestion choice is in §4.7.

## 11. Out of scope (v1)

- **Vertical Shorts** cut from casebook or transcript beats. Probably the best discovery channel
  for a public explainer, so it's the **first v2 candidate**.
- Automated YouTube upload (blocked on the API audit).
- Showing any lab's original figures, or clipping lab video (§2.3, §2.4).
- Music, clip-cloned voices, Breeze, Spotify.
- Sources excluded in §2.1: Meta, xAI, Microsoft, Amazon, Mistral, OpenAI News, system cards,
  and the sitemap-only check sources.
- Follow-up segments when an independent response to an *earlier* feature appears later.
- Moving the daily and Frontier Commits video spikes onto this video stage. That is a separate
  issue, filed once this stage exists.
