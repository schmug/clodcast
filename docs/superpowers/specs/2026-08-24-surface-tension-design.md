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
| unusual | A call-in radio frame: real Fediverse discussion is worked at a **switchboard**, and stances are **assigned** |

The genre is *argued curation*: four voices who were handed their positions
rather than choosing them, working through a blog post somebody voted up. The
friction is real because it is assigned — the same reason `SHAPE_ORDERS` assigns
segment shapes instead of asking a model to be varied.

**The container is a call-in radio show**, and that is a structural decision
rather than a costume. A panel podcast has no native place to put the outside
world: a panelist reading Fediverse replies aloud is always going to sound like
someone reading their phone at the table. A call-in show has a native one — the
switchboard — and it works even when the calls cannot be put on air.

That last clause is not hedging: the §2 recon established that `/feed/comments`
carries no comment bodies, so the frame's payload is the *board* (how many calls,
from where, on which post) rather than the callers themselves. See §4.4 for the
role as amended. The frame also supplies the furniture the brief asks for —
station idents, bumpers, a rundown — which is where *busier* and *unusual* come
from without inventing novelty per episode, and that half never depended on
quoting anyone.

## 2. Grounding data (recon, 2026-08-25)

**Method.** One unauthenticated `GET` per endpoint from a host that can reach
bubbles.town, UA `clodcast-recon/1.0`, 2026-08-25 ~12:00 UTC. Every response was
`200`. `robots.txt` allows every path used here (it disallows only `/admin` and
`/auth`). Trimmed captures are committed as `tests/data/bubbles_*.xml`, and
**every structural claim below is asserted in `tests/test_bubbles_fixtures.py`** —
so this section cannot quietly drift from the data it describes, and a re-capture
that changes shape goes red.

`bubbles.town` is **not** a Mastodon instance — confirmed, and still the
assumption worth killing early. It is "Hacker News for non-techy blogs":
thousands of personal, independent blogs ranked by community votes, with identity
and comments routed through the Fediverse. Every post gets a companion post on
`@bubbles@social.bubbles.town`; replies from anywhere in the Fediverse become
comments on the post.

Everything it serves is **Atom 1.0** (`application/atom+xml`), never RSS, with
three namespaces declared on every post feed: `media` (Yahoo MRSS), `slash`, and
`thr`. `thr` is declared but never used.

| Endpoint | Entries | Window covered | Votes | Role in this show |
| --- | --- | --- | --- | --- |
| `/feed` | 30 | 136 h (≈5.7 d) | 1–39, median 4 | Main story pool |
| `/feed/hot` | 17 | **2,682 h (≈16 wk)** | 0–46, median 10 | The argued-about segment |
| `/feed/new` | 100 | **6 h** | 0–3, median 0 | Rapid-fire bit |
| `/feed/comments` | 50 | 446 h (≈18 d) | — | Discussion desk (see 2.3) |
| `/briefing/feed` | 60 | daily editions, 06:00 Berlin | — | Pre-curated daily anchor |
| `/weekly/feed` | 18 | weekly editions, Sun 06:00 | — | Pre-curated weekly anchor |
| `/editions` | — | `text/html` only | — | Human hub page; not a data source |
| `/api/vote-count?url=` | — | `{"count":N,"id":M}` | — | Redundant; see 2.2 |

**Two of those windows are load-bearing and neither is what the endpoint's name
suggests.** `/feed/new` returns 100 entries spanning **six hours**, so a weekly
show cannot read a week of new posts from one fetch — the rapid-fire pool is
"the last six hours of unvoted posts", or it requires accumulating snapshots the
way Frontier Commits does. And `/feed/hot` is ranked by discussion rather than
recency: its oldest entry was **three and a half months old**, and only 12 of 17
fell inside a 168 h lookback. Applying `lookback_hours` to `/feed/hot` would
discard exactly the posts it exists to surface.

The three post feeds overlap only lightly — 6 shared URLs between `/feed` and
`/feed/hot`, 9 between `/feed` and `/feed/new`, 132 distinct URLs across all
three — so they are three genuinely different pools, not three sorts of one.

### 2.1 Entry schema — post feeds (`/feed`, `/feed/hot`, `/feed/new`)

Present on **every** entry of all three (147 live entries, no exceptions):

| Field | Shape | Notes |
| --- | --- | --- |
| `title` | plain text | No markup observed |
| `link rel="alternate"` | the **blog post's own URL** | Never a bubbles.town URL — see below |
| `id` | identical to that URL | So `guidislink` dedup keys on the publisher's URL |
| `published` / `updated` | RFC-3339, `Z` | Not RFC-822; both always present |
| `author/name` + `author/uri` | the **blog's** name and site | Not the voter, not a person |
| `content type="html"` | the **entire post body** | See the trap in 2.6 |
| `media:community/media:statistics@favorites` | vote count, attribute string | See 2.2 |
| `slash:comments` | comment count, attribute string | `"0"` when none |

Optional: `media:thumbnail@url` (present on 47 of 147), and a second
`link rel="replies"` pointing at `https://bubbles.town/entry/<id>`.

**`link` points at the blog post, not at bubbles.town.** This matters more than it
looks: it is what lets `covered.json` interoperate with the daily digest, since
both key on the publisher's own URL. A bubbles permalink here would make one post
look like two different URLs across the two shows.

**The `rel="replies"` link appears exactly when `slash:comments > 0`** — zero
mismatches across all 147 entries. That is the cheap "does this post have
discussion" test — what gates the `switchboard` turn in §4.3 — with no second
fetch.

**A post's bubbles.town entry id is only recoverable from `content`**, where every
body is prefixed with an `<a href="https://bubbles.town/entry/<id>">🫧 Open on
Bubbles</a>` anchor (147/147). That id is the sole join key between a post and its
comments, whose ids are `entry/<id>#comment-N`.

### 2.2 Votes are inline; `/api/vote-count` is redundant

`media:community/media:statistics/@favorites` carries the vote count on every post
entry, with no missing values. The separate `/api/vote-count?url=` endpoint
returns `{"count":N,"id":M}` and **agreed with the inline value on 10/10 spot
checks**, so ranking never has to leave the feed it already fetched.

The API works if it is ever needed: ~0.5 s per call, `cache-control: public,
max-age=60`, no rate-limit headers observed and no 429 across 10 sequential
calls (which is already the realistic per-episode ceiling of ~10 candidates).
There is **no batch form** — it takes one `url` per call. But since the inline
value is free and identical, `st_gather` should read the feed and treat this
endpoint as a fallback, not a per-candidate round trip. Note the values are
**attribute strings** (`"3"`, not `3`) and need `int()` coercion.

### 2.3 `/feed/comments` carries no comment bodies — this is the finding

**A comments entry contains navigation links and nothing else.** Its `content` is
a `<ul>` of two or three anchors — "*&lt;post title&gt;* on Bubbles", "Full
discussion on Fediverse", and on a non-first comment "Earlier comments: #1". Strip
the anchors and no prose remains. Checked across all 50 entries; the committed
fixture is stored **verbatim, untrimmed**, precisely so this claim cannot be an
artifact of our own truncation.

What an entry *does* carry:

| Field | Value | Usable for |
| --- | --- | --- |
| `title` | `New comment on: <post title> (Nth, M total)` | Post title **and** thread position, matched 50/50 |
| `id` | `https://bubbles.town/entry/<post id>#comment-<n>` | **Parent-post tie-back, 50/50** |
| `link rel="alternate"` | the comment's permalink on its **home instance** | Which corner of the Fediverse called |
| `published` / `updated` | RFC-3339 `Z` | When the call came in |
| `author/name` | literally `bubbles.town`, on every entry | Nothing — it is the site, not the commenter |

No `media:*`, no `slash:comments`, no author URI. The 50 comments came from **22
distinct instances** (mastodon.social 14, gotosocial.social 4, pkm.social 4, …)
across 36 distinct parent posts.

So, against the three questions §4.4 depended on: **comment body — no. Author
handle — not as a field.** The handle is embedded in the permalink path
(`https://mastodon.social/@snowgoon/117155709265017126`), which means the only
personal identifier the feed exposes is exactly the one §4.4's hard rule forbids
putting on air. **Link back to the parent post — yes, reliably.**

**Consequence: the "real quotes on air" premise does not survive, and §4.4 and §5's
scene 6 are amended accordingly** — `switchboard` becomes a discussion desk rather
than a caller role. This is a downgrade taken deliberately and in the open, which
is the whole reason this recon happened before Phase 2 rather than during it.

**Raised, not decided:** a comment body is one HTTP fetch from its permalink, and
`/feed/hot` is defined by discussion (all 17 entries carried ≥1 comment). Whether
to fetch third-party Fediverse posts to recover quotes is a design decision with
its own consent and rate-limit questions — out of scope for recon. Recorded here
so the option is not rediscovered as a surprise.

### 2.4 The editions are feeds, but not at `/editions`

`/editions` is `text/html` only — a human hub page, no JSON, no structured
payload. The editions themselves *are* Atom, at URLs the page advertises in
`<link rel="alternate">`: **`/briefing/feed`** (daily, 06:00 Berlin) and
**`/weekly/feed`** (weekly, Sunday 06:00). Both parse clean.

The catch: **one entry is one whole edition**, not one entry per post. The
individual posts live as `<h3>`-sectioned HTML inside a single ~18–23 KB
`content` blob, so using an edition as a pre-curated anchor means parsing that
HTML — it is not structured data at the post level. The editions are also
language-specific (`/de/editions` is the German hub).

The same page also advertises **14 per-category feeds** — `/feed/cat/{art, crafts,
culture, film, food, gaming, history, life, music, nature, politics, science,
tech, writing}`. Not needed for v1, but they are a native topical-width lever that
does not depend on the OPML `opml_categories` work in §4.2, and worth knowing
about before that filter is built.

### 2.5 `feedparser` parses all of it without loss

`feedparser` 6.0.12 reports `bozo=False`, `version=atom10` on all six feeds, with
`published_parsed`, `link`, `title` and `content` populated on every entry. Driving
the real capture through `orchestrate.gather_candidates` yields fully-populated
candidates. **The `http(s)`-only link guard (orchestrate.py:449) is a no-op here** —
all 197 live entries had `https` links, so nothing is dropped.

One mapping gotcha worth writing down, because it is silent:

```python
entry.media_community    # ''            <- NOT the nesting the XML uses
entry.media_statistics   # {'favorites': '3'}
entry.slash_comments     # '0'
entry.media_thumbnail    # [{'url': '...'}]
```

feedparser **flattens** `media:community > media:statistics`, so an implementer
walking the XML nesting through feedparser finds an empty string and concludes the
votes are absent. `tests/test_bubbles_fixtures.py` pins this.

### 2.6 The trap for the metadata-only invariant

**Atom `content` is the entire post body**, and feedparser mirrors it into
`summary` — identical on 30/30 entries. So `gather_candidates`'s
`_clean(entry.get("summary") or _first_content(entry))` is picking up article
text, not a blurb, and it is `_clean`'s truncation alone that keeps the
deterministic ranking metadata-only in practice.

This cuts both ways. `st_gather` **must** keep a cap of its own rather than
assuming the field is short — the repo's core invariant is that no LLM request
holds more than one article body, and an uncapped `summary` walks bodies straight
into the ranking path. But it also means the post body arrives **free, in the feed
we already fetched**: the writer subprocess needs no separate article fetch, which
removes the whole blocked-source failure mode the daily show fights with
(`blocked_sources.json`, WebFetch-blocked feeds). One post per writer, still.

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
| `switchboard` | Works the board — reports who called and from where, never what they said (§4.4) | 0–2 |

**`switchboard` is conditional and that is the point.** No comments on the post
→ nothing to report, no `switchboard` turn, and the writer is forbidden from
inventing one. This is the same mechanical honesty as the textless `cold`
transition move and the "never manufacture a connection" rule: a fabricated
caller is strictly worse than a dead phone line, because it is unfalsifiable on
air. The §2 recon narrowed what this role may say — metadata only, per §4.4 —
without changing that it is conditional. Note the plan can decide this from
`slash:comments` alone, with no extra fetch (§2.1).

### 4.4 The discussion desk — what the outside voice became

**Amended 2026-08-25 by the §2 recon. This section previously specified a caller
role carrying verbatim Fediverse comments; `/feed/comments` carries no comment
bodies (§2.3), so that role does not survive contact with the data.** The
downgrade is recorded rather than papered over, per the contingency the original
draft named.

**There is no `caller` speaker and no caller line.** A synthetic voice reading a
quote we never fetched would be fabrication, and the one identifier the feed does
expose — the handle in the permalink path — is exactly the one the hard rule
below forbids. Both roads from "no bodies" to "quotes on air" are closed.

`switchboard` therefore becomes a **discussion desk**: the seat that reports what
the board is doing, from metadata alone. Everything it says is checkable against
`/feed/comments` (§2.3):

| Available | From | On air as |
| --- | --- | --- |
| That a post drew comments, and how many | `slash:comments`, `rel="replies"` | "the board lit up on this one" |
| Which post, by title | comments-entry `title` | Naming the post being argued about |
| Thread position (1st of 2, …) | comments-entry `title` | "and then a second call came in" |
| Which instances called | permalink **host**, never the handle | "one from a photography server, one from Prague" |
| When the calls came | `published` | "these came in overnight" |
| Comparative heat across the week | counts across the feed | The vote desk's natural companion |

**The hard rule survives intact and now has more work to do.** No line is ever
attributed to a named person, and no words are ever put in an outside person's
mouth. Synthesizing a voice to speak a real, identifiable person's words is
impersonation if it carries their name and fabrication if we did not read the
words at all. Concretely: **use the permalink's host, never its handle**; never
characterize what a commenter *said*, only that they commented; never invent a
quote to stand in for one. A fabricated caller is strictly worse than a dead
phone line, because it is unfalsifiable on air.

**The radio frame survives, and arguably improves.** "We're not going to read
them out, but the board is lit — four calls on this one, from four different
corners" is a real radio move, and it is honest. What it costs is the panel
answering a specific outside argument; what it keeps is the outside world's
*presence* as a measurable thing, which is what scenes 2 and 6 were both built to
use. Volume-and-provenance is genuinely available; opinion content is not.

**Voice budget is no longer contested.** With no caller line, all four presets go
to the panel — the §4.8 cast is exactly the four voices, and the "three panel +
one rotating caller" compromise is dropped. Phone-line audio treatment (a
band-pass filter on caller lines) is dropped with it: there are no caller lines to
treat, and §4.7's soundboard already covers the one bumper the desk needs.

**Reopening this is a design decision, not a recon one.** A comment body is one
fetch from its permalink (§2.3), which would restore verbatim quotes along with
the original hard rule. That is out of scope here — it introduces third-party
fetching, consent questions, and a new failure mode — but it is the known path
back, and it should be taken deliberately or not at all.

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
| 2 | Vote desk — the week's numbers, one indefensible ranking | `/feed` inline votes (§2.2) | 1–2 |
| 3–5 | Main posts — assigned stances, adjudicated | `/feed` | 3–4 |
| 6 | **Open lines** — the board on the argued-about post: how many calls, from where, and the panel arguing the post itself | `/feed/hot` + `/feed/comments` | 3–4 |
| 7 | Rapid fire (after the bumper) — six takes on six unvoted posts | `/feed/new`, last 6 h (§2) | 4 |
| 8 | Sign-off — rotating, per `OUTRO_MODES` precedent | — | 2 |

Eight chapters, ~35–50 turns, six or seven distinct topics. The rapid-fire bit
buys topical width almost free: six one-liners from six metadata blurbs, one
chapter, one `claude -p`, and it is the one scene that reads `/feed/new`
unranked — deliberately, since "nobody voted for these" is the bit. `/feed/new`
only reaches back six hours (§2), so those six are the morning's unvoted posts
rather than the week's; that is a narrower claim than the original draft made,
and the scene should say "this morning", not "this week".

**Scene 6, amended 2026-08-25.** It no longer carries a `caller` voice, because
`/feed/comments` has no comment bodies to quote (§2.3, §4.4). What survives is a
scene the metadata fully supports: the switchboard reports the volume and
provenance of the discussion — *four calls, four different instances, all
overnight* — and the panel argues **the post**, which it can do because the post
body is in the feed. Voice count drops from "3–4 + caller" to 3–4, which is what
frees all four presets for the panel (§4.4). Scene 6 remains the show's most
fragile scene: on a week where the argued-about post drew nothing, the honest
move is a short scene, not a fabricated one.

Scenes 1, 2, 7 and 8 carry `"source_url": null` and are non-story frames, exactly
like Frontier Commits' trend-watch close.

The radio frame still earns its place twice, though the first reason is now a
weaker one than the original draft claimed: it gives scene 6 a native shape for
*talking about* an outside conversation without reading it aloud, and it makes
scenes 1, 2 and 7 feel like furniture rather than filler — a rundown, a desk
segment and a bumper are things a radio show *has*, so they need no per-episode
justification. That second reason is untouched by the recon and is now the frame's
main load-bearing justification.

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
- `test_switchboard.py` — **amended 2026-08-25** (was `test_callers.py`, which
  tested verbatim caller quoting; §4.4). No manifest ever carries a `caller`
  speaker; no switchboard line contains a Fediverse handle (`@user@host`) or a
  commenter display name; no comments on the post → no `switchboard` turn; the
  turn is derivable from `slash:comments` without a second fetch.
- `test_bubbles_fixtures.py` — **already landed with the §2 recon.** Pins the
  captured feed shapes so this spec cannot drift from the data: votes inline,
  `link` pointing at the blog rather than bubbles.town, `rel="replies"` iff
  `slash:comments > 0`, `feedparser`'s `media_community` flattening, and — the
  load-bearing one — that `/feed/comments` carries no comment body.
- `test_soundboard.py` — asset-line duration is excluded from
  `speech_rate_rows`, proven by a segment that would otherwise fall below
  `MIN_SPEECH_RATE_RATIO`; an asset line's cache key changes with the file bytes;
  a week whose plan assigns no stinger to a slot renders no stinger there.
- `test_st_skill_md.py` — drift test tying SKILL.md's role/stance tables to
  `st_script_plan`, per the `test_fc_skill_md.py` precedent.

## 8. Phasing

1. **Recon + `lines` layer.** ~~Finish §2's confirming fetch.~~ **Recon done
   2026-08-25 (#173)** — §2 is measured, fixtures are in `tests/data/`, and the
   caller role was downgraded in §4.4. Remaining: land `lines` in `render.py`
   behind the existing manifest validation, with the derived-`text` mitigation and
   its tests. Dry-run a hand-written 2-scene manifest on the four presets. Measure
   the voice-rate spread.
2. **Gather + assign + write.** `st_gather.py`, `st_script_plan.py`,
   `prompts/write_scene.md`, SKILL.md. First full dry-run episode. Start from the
   §2 fixtures rather than re-fetching.
3. **Cast + art + ship.** Record the four `ref_audio` clips (no caller clip — see
   §4.4), cover art, R2 keys, first published episode, then the weekly schedule.
4. **Soundboard.** §4.7, once the format is proven and the rate-gate guard has a
   test. Deliberately last: it is the part most likely to age badly, and the
   easiest to add to a working show.

## 9. Out of scope (v1)

- Spotify distribution. RSS-first, like Frontier Commits; submit the feed later if wanted.
- Per-voice speech-rate medians (§4.6) — build only if Phase 1 measurement warrants.
- Reading `/feed/comments` for anything but the discussion desk (§4.4).
- **Fetching Fediverse comment permalinks to recover comment bodies** (§2.3,
  §4.4). It is the known path back to real quotes on air, and it is deliberately
  not taken in v1: it adds third-party fetching, consent questions and a new
  failure mode. Deferred, not refused — but only as a deliberate decision.
- Caller lines in any form (§4.4) — there is no `caller` speaker, so phone-line
  audio treatment goes with it. Attributing any line to a named outside person,
  or putting words in one's mouth, is **permanently** out of scope, not deferred.
- Interruptions, crosstalk, overlapping audio. Turn-based only; overlapping
  speakers would break the per-line concat model entirely.
- Any cortech.online page beyond the RSS feed the R2 manifest already drives.
