# Surface Tension — design spec

**Date:** 2026-08-24
**Status:** Approved design, pre-implementation
**Decisions locked by Cory:** source = bubbles.town **and** the OPML "blogs" category · cast = 4 voices, presets first / recorded `ref_audio` clips later · format = hybrid (variety-desk skeleton + assigned stances), framed as a **call-in radio show** · show name = **Surface Tension** · third skill in clodcast, reusing `render.py` in web-only mode

## 1. What this is

A third podcast, sibling to the daily digest and Frontier Commits, whose beat is
**personal independent blogs surfaced by community vote** — and whose form is a
**3–5 voice panel** rather than a single narrator. Weekly, RSS-first, shipped
through the same `render.py` in `ship_mode: "web"`.

The brief was three words: *busier*, *wide-ranging*, *unusual in format*. Each
maps to one mechanism:

| Brief | Mechanism |
| --- | --- |
| busier | Multi-voice scenes: ~8 chapters, ~35–50 speaker turns, vs. today's 12 monologue segments |
| wide-ranging | The source. Personal blogs about anything, not an AI-news OPML — both existing shows are tech beats |
| unusual | A call-in radio frame: real Fediverse comments arrive as **callers**, and stances are **assigned** |

The genre is *argued curation*: four voices who were handed their positions
rather than choosing them, working through a blog post somebody voted up. The
friction is real because it is assigned — the same reason `SHAPE_ORDERS` assigns
segment shapes instead of asking a model to be varied.

**The container is a call-in radio show**, and that is a structural decision
rather than a costume. A panel podcast has no native place to put an outside
voice: `room` reading Fediverse replies aloud is always going to sound like
someone reading their phone at the table. A call-in show has exactly one job for
an outside voice, and the Fediverse comment *is* the caller. The frame also
supplies the furniture the brief asks for — station idents, bumpers, a rundown,
"you're on the air" — which is where *busier* and *unusual* come from without
inventing novelty per episode.

## 2. Grounding data (recon, 2026-08-24)

`bubbles.town` is **not** a Mastodon instance — the assumption worth killing
early. It is "Hacker News for non-techy blogs": thousands of personal,
independent blogs ranked by community votes, with identity and comments routed
through the Fediverse. Every post gets a companion post on
`@bubbles@social.bubbles.town`; replies from anywhere in the Fediverse become
comments on the post.

| Endpoint | Content | Role in this show |
| --- | --- | --- |
| `/feed` | Front page — votes × freshness | Main story pool |
| `/feed/hot` | Posts generating discussion right now | The argued-about segment |
| `/feed/new` | Every post, chronological | Rapid-fire bit |
| `/feed/comments` | One entry per new Fediverse comment | The `room` role — real quotes |
| `/editions` | Daily 06:00, weekly Sun 06:00 (Berlin) | Pre-curated weekly anchor |
| `/api/vote-count?url=` | Vote count for a URL | Scoring + vote-desk material |

**Recon is incomplete and must be finished before Phase 1.** The session that
produced this spec had `bubbles.town` blocked by an egress proxy; the table above
comes from search results, not from reading the feeds. One confirming fetch from
a machine that can reach the host must pin, for each feed: the actual XML shape,
whether vote counts appear inline in `/feed` (or require the `vote-count` call
per URL), and what a `/feed/comments` entry carries (comment text? author? a link
back to the parent post?). **The `room` role is contingent on that last answer** —
if comment bodies are not in the feed, `room` becomes a vote-and-context role and
the "real quotes" pitch is downgraded, not faked.

### Why this source is a better fit than either existing show's

The repo's curation invariant is *deterministic, metadata-only ranking; no LLM
request holds more than one article body*. Both existing shows satisfy it with
invented proxies for quality — `concreteness_score` heuristics in the daily
show, `TYPE_PRIORITY + 10·log10(stars)` in Frontier Commits. Votes are a **human
quality signal that is already metadata**. This show can satisfy the invariant
more honestly than either predecessor, not less.

## 3. Architecture

Same three-layer shape as Frontier Commits, and the same division of labour:
deterministic Python does the gathering and the assignment; Claude writes only
prose; `render.py` renders and ships.

```
 gather (pure Python, no LLM)
   ├─ bubbles.town feeds ──┐
   └─ OPML "blogs" category┴─▶ candidates (title/url/date/summary/votes)
                                     │
 rank (deterministic, metadata-only) ▼
   votes × recency × variety-penalty ─▶ ~4 main posts + rapid-fire pool
                                     │
 assign (date-seeded, no LLM) ───────▼
   st_script_plan: who speaks, in what order, which stance, which bit
                                     │
 write (isolated claude -p per post) ▼
   one post body per request ─▶ scene = [{speaker, text}, ...]
                                     │
 render (render.py, ship_mode: web) ─▼
   per-line TTS ▸ concat to seg_NN.mp3 ▸ [unchanged pipeline] ▸ R2
```

The **assign** layer is the load-bearing addition and the direct descendant of
`SHAPE_ORDERS` / `fc_script_plan`. Each writer subprocess sees one post and
cannot see its neighbours, so nothing about the episode's variety can be
negotiated between writers — it is handed to them.

## 4. Components

### 4.1 Config — `~/.config/surface-tension/config.json`

Its **own** config directory, following the `frontier-commits` precedent. This is
not cosmetic: `covered.json` is the dedup source of truth, and a shared one means
a blog post covered here is silently withheld from the daily digest and vice
versa.

```jsonc
{
  "show_name": "Surface Tension",
  "bubbles_feeds": ["https://bubbles.town/feed", "https://bubbles.town/feed/hot"],
  "rapid_fire_feed": "https://bubbles.town/feed/new",
  "comments_feed": "https://bubbles.town/feed/comments",
  "opml_files": ["/path/to/feeds.opml"],
  "opml_categories": ["Blogs"],          // NEW — see 4.2
  "lookback_hours": 168,                 // weekly
  "target_post_count": 4,
  "rapid_fire_count": 6,
  "r2_bucket": "clodcast",
  "r2_public_base_url": "https://audio.cortech.online",
  "r2_manifest_name": "surface-tension.json",
  "r2_key_prefix": "st/",
  "slug_prefix": "surface-tension"
}
```

### 4.2 Gather — `st_gather.py`

Two sources, one candidate schema. Metadata only, exactly like
`orchestrate.gather_candidates`.

**The OPML side needs one small addition that does not exist today.**
`orchestrate.parse_opml` already extracts a `category` from each
`<outline type="rss">` leaf and `gather_candidates` already carries it into the
candidate dict — but **`score_candidate` ignores it entirely**. There is no
category filtering anywhere in the repo. `opml_categories` is therefore new
behaviour, not a new spelling of an existing feature.

Two hazards, both worth guarding at gather time:

- **Nested exports lose the category.** The parser walks `root.iter("outline")`
  flat and reads `category=` off the *leaf*. An export that nests feeds under a
  folder `<outline text="Blogs">` without stamping the attribute on each child
  yields `""` for everything, and a category filter would then match nothing and
  silently produce an empty pool. Feedly-shaped exports do stamp it (the existing
  test fixture uses `category="/x"`), but this must be verified against the real
  file before Phase 1 — and matching must be substring/suffix-tolerant, since
  Feedly writes paths like `/user/<id>/category/Blogs`, not a bare `Blogs`.
- **An empty filtered pool must be loud.** A category that matches zero feeds is
  a config error, not a thin day. Die with the categories actually seen in the
  OPML, so the fix is obvious.

**Ranking** replaces `concreteness_score` with the vote count, keeps the recency
and per-feed variety terms, and keeps `rank_candidates`' per-feed cap so one
prolific blog cannot take the episode.

### 4.3 Assign — `st_script_plan.py`

A CLI in the shape of `fc_script_plan.py` — `plan --date <ISO> --posts <n>`
prints one JSON object, and nothing computes assignments by hand. Seeded on
`week_index`, reusing `fc_script_plan.week_index` rather than re-deriving it (the
`year*53 + week` trap is already documented and already solved there).

It assigns four things:

1. **Role → voice.** Voices are fixed; roles rotate across a Latin square. The
   same voice must not always be the skeptic, or the show calcifies into "Aiden
   is the funny one" — the panel-show form of the 76-episodes-one-opening
   failure.
2. **Stance pairs.** Per post, which voice argues *for* and which *against*,
   independent of the role rotation.
3. **Turn order and the last word.** Who opens the scene, who closes it.
4. **Bit ownership.** Which voice runs the vote desk and the rapid-fire this week.

The Latin-square rules are inherited verbatim and are not negotiable: every row a
permutation of the bank, every column holding each entry exactly once, rows that
are not mutual rotations, **fixed data — not arithmetic**. The stride bug that
pinned positions 4/9/14 to one shape for four days at a time is documented in
CLAUDE.md and will reappear in any regenerated table.

**Roles bank (5 roles over 4 voices — one sits out each scene, which is itself a
rotation axis):**

| Role | Does | Turns |
| --- | --- | --- |
| `anchor` | Sets up the post, owns the running order, hands off | 2–3 |
| `advocate` | Steelmans the post — why the blogger is right | 2 |
| `skeptic` | Names what is unsupported. Assigned, not felt | 2 |
| `tangent` | Takes the honest swerve — the topical-width engine | 1–2 |
| `switchboard` | Takes the calls — introduces each caller, reacts, cuts them off | 0–2 |

**`switchboard` is conditional and that is the point.** No comments on the post
→ no call, no `switchboard` turn, and the writer is forbidden from inventing one.
This is the same mechanical honesty as the textless `cold` transition move and
the "never manufacture a connection" rule: a fabricated caller is strictly worse
than a dead phone line, because it is unfalsifiable on air.

### 4.4 Callers — the outside voice, and its one hard rule

A caller is a `line` whose `speaker` is `caller`, carrying a **quoted, unaltered**
Fediverse comment. The switchboard frames it; the panel answers it.

**The hard rule: a caller is never attributed to a named person, and their words
are never rewritten.** Synthesizing a voice to speak a real, identifiable
person's words — framed as a live phone call — is impersonation if it carries
their name, and putting words in their mouth if the text is paraphrased. Both are
avoidable at zero cost to the bit: callers are introduced by handle-free framing
("we've got a caller on line two"), quoted verbatim, and trimmed only by
truncation, never by rewording. If a comment cannot be used whole, it is not
used. This is a content rule for the writer prompt and a test on the
manifest-build step, not a suggestion.

**Voice budget.** `VOICES` holds exactly four presets, so a caller voice competes
with the cast in Phase 1. Options, in preference order: (a) three panel voices +
one rotating caller preset — the caller *should* sound like someone else each
week anyway, which turns the constraint into the bit; (b) a recorded caller clip
at Phase 3, once `ref_audio` lands. **Not** VoiceDesign: it loads
`VOICE_DESIGN_MODEL_ID`, a *second* model, roughly doubling the ~15 s load, and
it drifts. Option (a) is the Phase 1 answer.

**Phone treatment is a stretch goal, not v1.** A band-pass filter on caller lines
would sell the frame, and ffmpeg is already in the pipeline — but it is a new
per-line filter path, and every filter is a place the mono-44.1k invariant can be
broken. Ship callers dry first.

### 4.5 Write — `prompts/write_scene.md`

One isolated `claude -p` per post, holding exactly one post body. **The
one-body-per-request invariant survives dialogue intact** — the writer produces
the whole multi-speaker scene for its single post, with the cast bible, the
assigned roles, the assigned stances, and the turn budget supplied as
placeholders (`<<CAST>>/<<ROLES>>/<<STANCES>>/<<MIN_CHARS>>/<<MAX_CHARS>>`),
mirroring the existing `<<SHAPE>>` contract and its drift test.

Output is `{"ok": true, "lines": [{"speaker": "...", "text": "..."}, ...]}`.
`classify_output`'s outcome mapping (OK / REFUSED / AUTH / BLOCKED / ERROR) is
reused as-is, with the length check applied to the **sum of line texts**.

### 4.6 `render.py` — one bounded change, plus one trap

Everything the ship needs already exists: `ship_mode: "web"`, `slug_prefix`,
`cover_image`, `show_name`, `r2_manifest_name`, `r2_key_prefix`,
`description_footer_text`. Shipping Frontier Commits generalized the renderer
further than it looks; this show needs none of that plumbing rebuilt.

**The one blocker: `render_segments()` resolves exactly one voice per episode.**
It picks a single mode — clone, design, or preset — and applies it to every
segment. There is no per-segment voice in the manifest schema.

The naïve fix — one utterance per segment — breaks three documented invariants at
once: the strict 1:1 segment ↔ chapter ↔ `source_url` mapping in
`build_timeline_and_description`; the `MIN_CHAPTER_GAP_MS = 5_000` floor on
consecutive chapter starts (which `plan_silences` would "satisfy" by padding
seconds of dead air per turn); and it would emit a couple hundred chapters.

**The insertion point is a `lines` layer inside a segment.** A segment stays one
scene = one chapter = one `source_url`. Inside it,
`segment.lines = [{speaker, text}, ...]` renders per line into
`line_NN_LL.mp3` and concatenates into the same `seg_NN.mp3` the rest of the
pipeline already expects. Silences, chapter math, timeline, artifact gate, run
log, R2 publish and dedup are all untouched.

Three properties make this cheaper than it looks:

- **Extra voices cost no extra model load.** Clone-mode and preset voices all run
  on the same base `MODEL_ID`; only VoiceDesign uses a second model. Four voices
  = one ~15 s load, same as today. Keeping the whole cast off VoiceDesign keeps
  this true — and is independently required by `docs/durable-voices.md`.
- **The cache already supports it.** `_segment_cache_key` keys on
  `(text, mode, voice, ref_fingerprint, ref_text)` — everything a per-line key
  needs. Line-level caching is *strictly better* than today's: re-writing one bad
  line re-renders one line, not the whole scene.
- **`ref_audio` fingerprinting already handles a multi-voice cast.** Each voice's
  clip hashes into its own lines' keys, so re-recording one cast member
  invalidates only that member's audio.

**New constant: the turn gap.** `DEFAULT_SILENCE_MS = 800` is the pause *between
chapters*; between two speakers mid-scene it sounds like a hostage negotiation.
Dialogue wants roughly 150–350 ms. This is a new per-line constant and it has
**nothing to do with `MIN_CHAPTER_GAP_MS`**, which governs chapter *spacing*, not
inter-chapter silence — a distinction CLAUDE.md is emphatic about, and which a
future reader of a `lines`-aware `plan_silences` will be tempted to conflate.

**The trap, and it is a silent one.** `speech_rate_rows` measures
`chars = len(seg.get("text") or "")` and skips any segment measuring zero chars
as *unmeasurable*. A `lines`-only segment therefore measures zero, gets skipped,
and — below `MIN_RATE_SAMPLE_SEGMENTS = 5` — returns an empty list, which the
function's own docstring defines as "no evidence of a defect". **The TTS
degeneration gate and the bloopers bin would both switch themselves off for this
entire show, and nothing would report it.** Mitigation: the `lines` layer
materializes a derived `segment["text"]` (the joined line texts) at manifest-build
time, so the gate keeps its population. A test must assert a `lines` segment
yields a non-empty rate row.

Secondary, and to be measured rather than guessed: the gate compares each segment
against a **population median across segments**, and with four voices a segment's
rate is now a blend of whichever voices spoke in it. If the four presets' natural
rates differ by more than roughly 15%, the median gets noisy and
`MIN_SPEECH_RATE_RATIO = 0.75` could fire on a naturally-slow voice rather than a
derailment. Per-line rates are available for free once lines render separately,
so the fix — per-voice medians — is cheap if needed. **Measure the spread in the
Phase 1 dry run before building it.**

### 4.7 Soundboard — yes, but rationed and guarded

Station idents, stingers, a bumper into the rapid-fire, one airhorn that has to
earn its keep. Mechanically this is cheap: `concat_and_normalize` already
concatenates mp3s, so a soundboard clip is a `line` whose content is an **asset
path** rather than text, pre-conformed to mono 44.1k like everything else.

Three guards, one of which is sharp enough to fail a run:

- **It corrupts the speech-rate gate if unguarded.** `speech_rate_rows` computes
  `chars / duration` per segment. A three-second airhorn adds duration and **zero
  characters**, dragging that segment's rate down against the population median.
  Enough of them and a clean segment trips `MIN_SPEECH_RATE_RATIO = 0.75`,
  failing the artifact gate on a perfectly good episode — or banks false
  positives into the bloopers bin. Asset-line duration must be subtracted from
  the segment duration before the rate is computed. This is the same class of bug
  as the derived-`text` trap in §4.6 and needs its own test.
- **Cache key.** An asset line's key hashes the **file bytes**, exactly as
  `_ref_audio_fingerprint` already does for the house clip, so replacing a
  stinger invalidates only the lines that use it.
- **Loudness.** Clips must be pre-normalized at build time. The episode-level
  loudnorm pass cannot rescue a stinger mastered 10 LU hotter than the voices; it
  will just duck the whole episode around it.

**"Obnoxious" is the failure mode, not the goal.** A drop that fires every scene
becomes the new byte-identical opening — the exact failure this repo already
shipped 76 times. So the soundboard is subject to the same discipline as
everything else here: **`st_script_plan` assigns which stingers fire and where,
and some slots are assigned nothing.** A textless `cold` equivalent for audio.
Rationed, a stinger is a joke; sprinkled, it is a laugh track.

Start with roughly six clips: station ident, call-in bumper, rapid-fire bumper,
two reaction stings, sign-off bed. All must be licensed for commercial podcast
distribution — the feed is public — and their provenance recorded in the repo
beside them.

### 4.8 Cast — presets now, clips later

Phase 1 ships on the four bundled presets (`Ryan`, `Aiden`, `Ethan`, `Chelsie`) —
a four-person cast at zero asset cost, on the same base model, letting the format
be judged before anyone records anything. Presets are deterministic model voices
and do not carry VoiceDesign's drift.

Phase 3 swaps them for four recorded `ref_audio` clips (~30 s each, matching
`refs/house_voice.wav`'s treatment) under `skills/surface-tension/refs/`. The
swap is a manifest change and a cache invalidation, nothing more — which is
exactly why the presets are safe to start on.

**The house voice is not one of the four.** Reusing it here would make the daily
digest's narrator a panelist on a different show, and the daily show's identity
is the more valuable asset.

## 5. Episode shape

| # | Scene | Source | Voices |
| --- | --- | --- | --- |
| 1 | Station ident + cold open — two voices already mid-argument | — | 2 |
| 2 | Vote desk — the week's numbers, one indefensible ranking | `/api/vote-count` | 1–2 |
| 3–5 | Main posts — assigned stances, adjudicated | `/feed` | 3–4 |
| 6 | **Open lines** — callers on the argued-about post | `/feed/hot` + `/feed/comments` | 3–4 + caller |
| 7 | Rapid fire (after the bumper) — six takes on six unvoted posts | `/feed/new` | 4 |
| 8 | Sign-off — rotating, per `OUTRO_MODES` precedent | — | 2 |

Eight chapters, ~35–50 turns, six or seven distinct topics. The rapid-fire bit
buys topical width almost free: six one-liners from six metadata blurbs, one
chapter, one `claude -p`, and it is the one scene that reads `/feed/new`
unranked — deliberately, since "nobody voted for these" is the bit.

Scenes 1, 2, 7 and 8 carry `"source_url": null` and are non-story frames, exactly
like Frontier Commits' trend-watch close.

The radio frame earns its place here twice: it gives scene 6 a native shape
instead of a panel awkwardly reading replies aloud, and it makes scenes 1, 2 and
7 feel like furniture rather than filler — a rundown, a desk segment and a
bumper are things a radio show *has*, so they need no per-episode justification.

## 6. State inventory (all under `~/.config/surface-tension/`)

| File | Written by | Contract |
| --- | --- | --- |
| `config.json` | human | §4.1 |
| `covered.json` | `render.py` | URL → `{date, mp3_url}`; written only after `R2_PUBLISHED`; 180-day prune |
| `runs.jsonl` | `render.py` | Append-only, full `RUN_LOG_FIELDS` key set per line |
| `dropped.jsonl` | `st_gather.py` | One record per dropped item; observability only |
| `feed_usage.json` | orchestrator | Variety penalty across weeks. **#95 reports the daily show's has been dead since 2026-06-05** — wire it here rather than copying the dead path, and assert it with a test |
| `bloopers/` | `render.py` | Shared archive shape; see §4.6 for the gate trap |

## 7. Testing

Mirrors the existing suites; `tests/conftest.py`'s per-test redirection of every
writable path already covers a new config dir once its constants are registered.

- `test_st_gather.py` — category filtering (including the nested-OPML empty case
  dying loudly), vote ranking, per-feed cap, dedup.
- `test_st_script_plan.py` — the Latin-square properties, verbatim in spirit from
  `test_fc_script_plan.py`: permutation rows, permutation columns, no column
  repeating between consecutive rows, pairwise-distinct rotation signatures, and
  no voice holding a role two weeks running.
- `test_lines.py` — a `lines` segment yields exactly one chapter; the derived
  `text` is non-empty and `speech_rate_rows` measures it; per-line cache hit/miss;
  turn gap applied between lines and *not* between chapters; a lines-and-text
  segment is rejected rather than silently preferring one.
- `test_callers.py` — a caller line is byte-identical to its source comment
  (truncation allowed, rewording not); a caller line never carries a handle or
  display name; no comments → no caller line and no `switchboard` turn.
- `test_soundboard.py` — asset-line duration is excluded from
  `speech_rate_rows`, proven by a segment that would otherwise fall below
  `MIN_SPEECH_RATE_RATIO`; an asset line's cache key changes with the file bytes;
  a week whose plan assigns no stinger to a slot renders no stinger there.
- `test_st_skill_md.py` — drift test tying SKILL.md's role/stance tables to
  `st_script_plan`, per the `test_fc_skill_md.py` precedent.

## 8. Phasing

1. **Recon + `lines` layer.** Finish §2's confirming fetch. Land `lines` in
   `render.py` behind the existing manifest validation, with the derived-`text`
   mitigation and its tests. Dry-run a hand-written 2-scene manifest on the four
   presets. Measure the voice-rate spread.
2. **Gather + assign + write.** `st_gather.py`, `st_script_plan.py`,
   `prompts/write_scene.md`, SKILL.md. First full dry-run episode.
3. **Cast + art + ship.** Record the four `ref_audio` clips (plus a caller clip),
   cover art, R2 keys, first published episode, then the weekly schedule.
4. **Soundboard.** §4.7, once the format is proven and the rate-gate guard has a
   test. Deliberately last: it is the part most likely to age badly, and the
   easiest to add to a working show.

## 9. Out of scope (v1)

- Spotify distribution. RSS-first, like Frontier Commits; submit the feed later if wanted.
- Per-voice speech-rate medians (§4.6) — build only if Phase 1 measurement warrants.
- Reading `/feed/comments` for anything but callers.
- Phone-line audio treatment on caller lines (§4.4) — stretch goal.
- Any caller line that is not a verbatim quote (§4.4) — permanently out of scope,
  not deferred.
- Interruptions, crosstalk, overlapping audio. Turn-based only; overlapping
  speakers would break the per-line concat model entirely.
- Any cortech.online page beyond the RSS feed the R2 manifest already drives.
