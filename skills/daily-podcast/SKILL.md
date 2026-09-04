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
- `./orchestrate.py` — the unattended entry point for scheduled runs (deterministic metadata-only curation + one isolated `claude -p` per item)
- `./prompts/daily.md` — a stub pointing back here; the unattended procedure lives in [Unattended daily run](#unattended-daily-run)
- `./blocked_sources.json` — outlets that can't be fetched for article bodies, with recovery strategies

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
  // Lead stories then the date, per "Episode title". Display-only free text: it does
  // NOT key the slug/guid (see "Publishing to the web"), so it is safe to enrich.
  "title": "Mojo goes open source, OpenAI's pause, Cursor vs GitHub - May 22, 2026",
  "summary": "Today's one-sentence hook.",
  "show_id": "spotify:show:...",
  "show_name": "Daily Digest",             // optional; overrides config.json's show_name on the COVER only (big title + top label). Set it when rendering a SECOND show — render.py reads one config for every show it renders, so without this its covers carry the daily show's branding
  "cover_image": "/path/to/cover.jpg",     // optional; use this image as the episode cover instead of generating one. Absolute, or relative to the MANIFEST's directory (never the CWD). Must be square, 1400-3000px — pre-flight fails the run otherwise. Set it when a show has DESIGNED art: show_name only changes the name on the generated gradient, which is this show's look
  "date": "2026-05-22",                    // optional ISO date; stamps the cover AND keys the web slug/guid. Omit to use today (re-renders of a dated manifest reproduce its date)
  "voice": "house",                        // default; or "random" / preset name; set voice_instruct for custom VoiceDesign
  "ship_mode": "spotify",                  // optional; "spotify" (default) or "web". "web" skips save-to-spotify entirely and makes the R2 publish the ship — see "Web-only shipping"
  "tts_engine": "qwen3",                   // optional; "qwen3" (default) or "breeze". Closed whitelist that lives on the manifest like ship_mode — see "TTS engines"
  "description_footer_text": "Sources: …", // optional; replaces the standard credit footer on the episode description (see "Episode description footer"). PLAIN TEXT: render.py escapes it into one <p> and rejects markup. Set it when rendering a SECOND show — the default footer credits the daily show's feeds
  "cast": {"anchor": "Ryan", "skeptic": "Ethan"}, // optional; speaker -> preset name OR {"ref_audio","ref_text"} clip, for multi-voice `lines` segments (see "Multi-voice scenes"). The daily show does not use this
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
7. Render manifest       -> title per "Episode title"; hand to render.py
8. render.py             -> TTS + concat + loudnorm + cover + upload + timeline + poll
9. Update dedup log      -> render.py appends covered URLs with today's date + episode URI
10. Report               -> single line: episode URI + voice used + chapter count
```

## Script template

The template is a **rotation, not a fixed form**. Seventy-six consecutive episodes opened with the same sentence and ran twelve identically-shaped segments; the rotation exists to stop that. Take today's assignments from the date, never at random — a re-run of the same day must rebuild the same episode.

**Seed:** `day` = day-of-year of today's date (1-365). Every index below is `day` modulo the bank size.

### Cold open (~350-400 chars, one paragraph)

Bank of five, indexed `day % 5`:

| # | Mode | Do |
| --- | --- | --- |
| 0 | `classic` | The show's standard line: "Today's digest for [date in long form]. [N] stories today, covering [2-4 word theme list]. Here's the rundown." |
| 1 | `theme-first` | Name the through-line connecting today's headlines in one sentence, then the date and the story count. |
| 2 | `lead-first` | Open on the single biggest story in one line, then say how many more follow, plus the date. |
| 3 | `number-first` | Open on the most striking concrete figure across today's headlines, then the date and the story count. |
| 4 | `tension` | Open on two of today's headlines that pull against each other, then the date and the story count. |

`classic` stays in the rotation deliberately — the show keeps a recognizable open about one day in five instead of losing its signature altogether. Every mode still states the date and the story count; that is the show's contract with a daily listener, not a stylistic flourish.

### Per-item segments

Each segment gets a **shape** and a **length band**, both assigned by its position.

**Shape** — take today's row from the table below by `day % 5`, then read the shapes left to right across your segments. Positions past the fifth wrap around and reuse the row.

| `day % 5` | pos 0 | pos 1 | pos 2 | pos 3 | pos 4 |
| --- | --- | --- | --- | --- | --- |
| 0 | `stakes-first` | `scene` | `plain-lede` | `contrast` | `number-first` |
| 1 | `scene` | `stakes-first` | `contrast` | `number-first` | `plain-lede` |
| 2 | `plain-lede` | `number-first` | `scene` | `stakes-first` | `contrast` |
| 3 | `contrast` | `plain-lede` | `number-first` | `scene` | `stakes-first` |
| 4 | `number-first` | `contrast` | `stakes-first` | `plain-lede` | `scene` |

What each shape means:

| # | Shape | Opening |
| --- | --- | --- |
| 0 | `plain-lede` | Headline framing, then the substance. |
| 1 | `stakes-first` | Who is affected and what changes for them, then what happened. |
| 2 | `number-first` | The single most concrete figure - a count, a sum, a version, a share - then what it measures. |
| 3 | `scene` | One concrete detail or a short quoted line from the reporting, then widen to the news. |
| 4 | `contrast` | The gap between what was assumed and what this item shows, then the substance. |

That table is a **Latin square**, and both of its properties are load-bearing. Every *row* is a permutation of the bank, so each shape appears once per five segments. Every *column* holds each shape exactly once, so no position is starved and no position repeats its shape two days running. The rows are deliberately not rotations of one another — under a plain rotation `stakes-first` would follow `plain-lede` in every episode ever made.

Don't replace the table with arithmetic. The first version of this used a stride (`(day + i * stride) % 5`, `stride = 1 + day % 4`) and looked correct in a year-long coverage check, but pinned positions 4, 9 and 14 to one shape for four days at a time.

**Length band** — the lead story gets room, and roughly one non-lead segment in four runs short:

| Position | Band | Role |
| --- | --- | --- |
| 0 | 850-1100 chars | Lead read — the day's biggest story |
| `(day + i) % 4 == 0` | 500-650 chars | Short take |
| everything else | 600-900 chars | Body segment |

**The band measures the story body, not the body plus its segue.** A segue is added on top of whatever the band allows, so a 500-650 short take carrying a 100-character bridge lands near 750 in the finished manifest — that is correct, not an overrun. This is not a stylistic reading: in `orchestrate.py` the band reaches the item writer through `fill_prompt`, which fills `<<MIN_CHARS>>`/`<<MAX_CHARS>>` in [`prompts/summarize_item.md`](prompts/summarize_item.md), and that writer only ever produces the body; `make_transitions` prepends the segue afterward in `assemble_manifest`. Measure the two separately when writing by hand, or every short take comes out starved.

500 is a hard floor, not a target: a body under it reads as filler next to its neighbours and `orchestrate.py` drops the item outright (`MIN_SEGMENT_CHARS`, checked on the body before any segue is attached). Short takes are only safe at all because Spotify's sub-30s chapter cap was retired upstream — see [Chapter-duration guardrail](#chapter-duration-guardrail).

**Every segment, whatever its shape:**
- Substance in the middle: what, why, the key detail
- End on the last sentence of substantive analysis. Never verbally reference the source URL, the show notes, the description, or "the link" — source attribution is handled non-verbally by the per-segment `link` companion in the timeline and by the timestamped chapter links in the HTML description.
- Never read URLs aloud
- One source per segment — strict 1:1 mapping

### Segues

A segue names the **relationship** between two adjacent stories. The old fixed list ("Next up", "Moving on", "Also today") was interchangeable filler — nothing about it could differ from one episode to the next, which is the same failure the shape rotation exists to fix.

The story at position `i` takes the move at column `i - 1` of row `(day + 2) % 5` in the same table above. The **lead story gets no segue** — it follows the cold open, which already set the episode up. The row offset keeps segues off the shapes' row, so a given shape doesn't carry the same segue forever.

| # | Move | Do |
| --- | --- | --- |
| 0 | `cold` | *No connective at all. Hard cut straight into the story.* |
| 1 | `pivot` | Name the change of subject in a few words, then go. |
| 2 | `contrast` | Set this story against the one before it - they point different ways. |
| 3 | `escalate` | Mark that this story raises the stakes on the theme just covered. |
| 4 | `echo` | Mark that this story rhymes with the previous one: same pattern, new actor. |

**Never manufacture a connection.** A digest's adjacent items are often genuinely unrelated. If the assigned move needs a relationship the two stories don't have, write a plain topic change instead — a false link between unrelated news items is worse than a blunt hand-off. One short clause each; these are bridges, not summaries.

### Sign-off (~250-300 chars)

Bank of three, indexed `day % 3`:

| # | Mode | Do |
| --- | --- | --- |
| 0 | `plain` | A simple thanks. |
| 1 | `throughline` | Call back to the episode's through-line in one clause, then sign off. |
| 2 | `forward-look` | One line on what is worth watching next out of today's stories, then sign off. |

No new facts in any of them, and the same rule as segments: don't tell listeners to check the show notes or description.

**If the script names the host anywhere - cold open or sign-off - take the name from `host_name` in [the config](#show--dedup-config), never a hardcoded one.** It is the same credit the public show page carries; a spoken name that disagrees with the show's `<itunes:author>` reads as a different person.

### Rules
- Convert relative dates from sources to absolute (today's date is available via the system clock)
- Strip markdown, code blocks, emoji, hashtags before TTS
- Numbers under 10 in words; abbreviations expanded ("D R I" not "DRI")
- "CLAUDE dot md" not "CLAUDE.md"
- No em dashes — TTS encoding flakes; use hyphens
- Segues are assigned too — see [Segues](#segues) below. Don't fall back to "Next up / Moving on / Also today"
- Vary sentence rhythm inside a segment too: don't open every sentence with its subject, and don't close on a summarizing "ultimately" / "in short" clause
- This is a news digest. Cover security, breach, and research stories at a reporting altitude — what was disclosed, who is affected, the response. Reporting on a disclosed vulnerability or breach is ordinary tech journalism; cover it confidently. Never write exploit steps, payloads, working commands, or attacker how-to; if an item can't be made substantive without them, it doesn't belong in the episode. (The [Unattended daily run](#unattended-daily-run) curation and fetch steps keep coverage at this altitude; this is the writing-side backstop.)

> **Defense in depth:** `render.py` validates the manifest structure (failing fast with a per-field message before the model loads) and re-strips TTS-hostile characters from every segment — em/en dashes, smart quotes, code fences + backticks, leading markdown headings, and bare URLs — regardless of what the caller wrote. It does *not* do the stylistic rules above (numbers-to-words, abbreviation spacing, "CLAUDE dot md") — those stay the writer's job. Set `"raw_text": true` in the manifest to skip normalization (e.g. text pre-formatted for a different TTS).

### Episode description footer

`render.py` appends one credit paragraph to every episode description, **after**
the timestamped chapter blocks. It is `DESCRIPTION_FOOTER` in `render.py` — a
constant, not model output. Don't write it into a segment, don't reproduce it in
the manifest `summary`, and don't remove it from a rendered description thinking
it is stray text:

```html
<p>Sources: <a href="https://cortech.online/podcast/sources.opml">every feed this show reads</a>, curated in <a href="https://donthype.me">Don&#x27;t Hype Me</a>.</p>
```

Two things about it are load-bearing:

- **It goes after the chapters, never before.** cortech.online's `summaryText()`
  keeps only the paragraphs *before* the first `(mm:ss)` chapter line and renders
  them as the website summary. Below the chapters the credit reaches Spotify's
  show notes in full and the web summary not at all; above them it would leak
  into every episode summary on the site. It is also why the cap-trimmer pins the
  footer last instead of treating it as a droppable trailing block.
- **The credit links the product, `https://donthype.me`** — never the repo behind
  it, which is private and would 404 for every listener.

This is a show-notes line only. Nothing in the audio points at it: the sign-off
rule above still stands, so never say "check the show notes" or read the source
list aloud.

The credit is per-show (#152): a manifest may set `"description_footer_text"`
to replace it — the default names the daily show's feeds, which is wrong
attribution on any other show rendered through `render.py`. The value is PLAIN
TEXT by contract: `render.py` escapes it and builds the single `<p>` itself,
and the manifest validator rejects any `<`/`>` (an operator-authored HTML
fragment would otherwise land verbatim in public RSS show notes). Links are
not supported in a custom footer — per-story links live on the chapter lines.
Absent, the default footer above is used, byte-identical. Everything else
about the footer — placement after the chapters, pinned last by the
cap-trimmer — applies to a custom footer unchanged.

## Episode title

The title is **display text, never narration** — nothing in the audio refers to it. It is
also the only thing a browsing listener sees before pressing play, so it names the day's
lead stories instead of repeating the date.

**Format** — 3 topics, then the date:

```
<topic>, <topic>, <topic> - <Month D, YYYY>
```

Worked example, for the episode of 2026-08-20 (T-Mobile cutting a cable to evict Salt
Typhoon, CareCloud confirming a breach, five federal agencies warning on Siemens
controllers):

```
Salt Typhoon, the CareCloud breach, Siemens PLC warnings - August 20, 2026
```

- **Topics first, date last.** Spotify publishes no maximum length for an episode title;
  its *Podcast Delivery Specification* (v1.9, §4.3) says only that consumer-facing
  elements are truncated at whatever the device can display. The front of the string is
  therefore the budget that matters — and `Daily Digest - August 20, 2026` spent all of
  it on 30 characters that were byte-identical across all 75 published episodes.
- **The date stays**, in long form, matching the cover and the cold open. This is a daily
  show and a listener orients by date; the RSS `<pubDate>` alone is easy to miss in a
  Spotify list view. Putting it last is what makes truncation cost the least.
- **Three topics, from the first three stories in the running order** — the ranked lead
  stories, in that order. Fewer stories than that, name as many as there are.
- **Each topic is a short noun phrase, 2-3 words**, naming the *subject* — the company,
  product, incident or release — not a sentence and not a whole headline. The band is
  measured, not guessed: at 2-4 words, three topics plus the date routinely overran the
  ceiling and the third was dropped, losing a searchable keyword outright.
- **Digits stay digits.** The narration rules (numbers under ten in words, spaced
  abbreviations) exist for TTS; a title is read on a screen, where `3.75 million` scans
  better than `three point seven five million`.
- **Hyphens, never em dashes**, and no emoji or smart quotes — special characters render
  inconsistently across podcast directories, which is why the public show's own title
  dropped its em dash.
- **"Report, don't instruct" applies here too** (step 5): name the subject, never the
  technique. `Citrix NetScaler emergency patch`, not the mechanism of the flaw.
- **100 characters** is the ceiling, and it is a runaway guard rather than a platform
  limit — three short phrases never approach it. Over it, drop a whole trailing topic;
  never cut a word in half.
- **With nothing worth naming, fall back to `Daily Digest - August 20, 2026`** — the
  title every published episode already carries. A blank title fails `validate_manifest`
  and costs the whole episode over a cosmetic field.

### Why retitling is safe, and what it cannot reach

The R2 `slug` and the `isPermaLink` `<guid>` built from it come from
`slug_for_date(<episode date>)` alone — see [`slug` is keyed on the date, never the
title](#slug-is-keyed-on-the-date-never-the-title). Nothing may re-couple them. But the
two Spotify surfaces differ, and only one is mutable:

| Surface | Title comes from | Mutable after publish? |
| --- | --- | --- |
| Public show (RSS-ingested from cortech.online) | `title` in the R2 `manifest.json` entry | **Yes** — ordinary data, and guid-neutral since #128 |
| Private Save-to-Spotify show | `upload --title` | **No.** Episode metadata is immutable after creation, and the show sits at its 60-episode cap, so delete-and-recreate would permanently destroy a published episode |

On the private show a format change therefore reaches **new episodes only**. Pick one and
hold it: every episode there is frozen under whatever format shipped it, and a churn of
formats reads worse to a browsing listener than one merely-adequate format held
consistently. That is also why the title format is deliberately **not** part of the
date-seeded rotation that varies the cold open, the segues and the segment shapes —
variety is right for prose, wrong for a name.

In `orchestrate.py` the composition is `episode_title()`, and the topic phrases come back
from the same `claude -p` call that writes the intro, sign-off and summary — from titles
only, so the one-body-per-request invariant is untouched. `TITLE_TOPIC_COUNT`,
`TITLE_MAX_CHARS`, the fallback and the worked example above are pinned against this
section by `test_skill_md_states_the_episode_title_format`.

### Back-filling the published back catalogue

`retitle.py` applies this same format retroactively to the **public** show, by rewriting
`title` on entries already in the R2 `manifest.json` (#144). It is a maintenance CLI, not
part of a run — nothing in the daily pipeline calls it.

```bash
python3 skills/daily-podcast/retitle.py                        # review every title
python3 skills/daily-podcast/retitle.py --only <slug> --apply  # canary: one episode
python3 skills/daily-podcast/retitle.py --apply                # the rest
```

The topic phrases are checked-in data (`backfill_topics.json`, keyed by published slug),
and the title is composed by `orchestrate.episode_title` — so the back-fill applies this
format rather than a second one, and a re-run is idempotent. Dry run is the default;
`--apply` writes the manifest, backs the previous one up under
`~/.config/daily-podcast/manifest-backups/`, and fires the Pages deploy hook (without
which the rewrite stays invisible until the next episode ships, because cortech.online
reads the manifest at **build** time). `assert_title_only` refuses any write where a
field other than `title` moved or the slug order changed — a manifest that fails the
consumer's `episodeSchema` empties the entire public feed.

Covers are deliberately not regenerated; see the module docstring for why.


## Voice selection

The default is the **locked house voice** — `ref_audio` cloning from a ~22-second reference clip. The Base 1.7B model regenerates that voice's timbre and prosody for any new text, so the voice stays consistent across episodes.

The bundled default lives at `refs/house_voice.{wav,txt}` in the skill directory. On the first `voice: "house"` render, `render.py` copies it to `~/.config/daily-podcast/voices/house.{wav,txt}` and reads from there forever after — so plugin updates can't overwrite a customized voice.

Manifest options:
- `"voice": "house"` (default) — Base model + `~/.config/daily-podcast/voices/house.{wav,txt}` (seeded from bundle on first run)
- `"voice": "random"` — preset rotation over `[Ryan, Aiden, Ethan, Chelsie]`
- `"voice": "Ryan"` (or any preset) — single fixed preset
- `"voice_instruct": "..."` — VoiceDesign mode, full override; `voice` becomes a label

The bundled house clip is one good render of a VoiceDesign instruct (`HOUSE_VOICE_INSTRUCT`, kept in `render.py` for reference) — mature female, even prosody, bright but human, not performative. To replace the house voice:

1. Capture a new ~20-30 second reference clip (any TTS or human recording)
2. Save it to `~/.config/daily-podcast/voices/house.wav` (PCM_16, mono, 24 kHz preferred)
3. Update `~/.config/daily-podcast/voices/house.txt` with the exact transcript
4. Done — every subsequent `voice: "house"` render uses the new clip

`ref_audio` precedence: if `voice_instruct` is also set in a manifest, the explicit instruct wins (so you can A/B against the house voice without unwiring it).

Report the voice in the final summary so the user knows which one ran.

### TTS engines

The engine is a property of the show, chosen by the manifest's `tts_engine` key (default `qwen3`); a typo dies rather than falling back, the `ship_mode` posture. The four voice modes above are unchanged by the engine — it is an orthogonal axis, not a fifth mode — but an engine only renders the modes it declares, and `render.py` refuses the rest before the model load. Pre-flight prints the engine and its license on every run. This table is pinned to `render.ENGINES` by a test.

| engine | model | capabilities | take ceiling | min mlx-audio | license |
| --- | --- | --- | --- | --- | --- |
| `qwen3` | `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit` | clone, design, preset | none | 0.4.3 | Apache 2.0 |
| `breeze` | `mlx-community/Breeze-TTS-2-mlx-8bit` | clone, design, direction, events | 500 chars | 0.5.1 | BreezeBlue Research and Non-Commercial |

- `qwen3` designs on a second model (`VoiceDesign-bf16`); `breeze` designs on the same model it clones with, so a Breeze episode always pays one load.
- `breeze` has no presets: `voice: "random"`, a preset name, or a preset cast entry dies naming the engine. Clones (`house`, cast clips) and `voice_instruct` work.
- **The 500-character ceiling is Breeze's own, not the token cap.** Measured 2026-09-04: 0 of 24 takes at or under 533 characters derailed; 1 in 5 did at 592–1000. A plain-text segment or a scene line over it dies before the render, because the speech-rate gate cannot see a derailment (the rate stays normal and whisper hears babble as words). Every band this show writes exceeds it, so the daily show cannot select `breeze` as-is; Surface Tension's lines can.
- `events` and `direction` are declared for the eval bench and future script features; nothing in `render.py` reads them yet. Paralinguistic markers still do not work on `qwen3`.
- Breeze's weights are non-commercial with no creator or monetization exception: no sponsor reads or paid tiers on a show that renders with it.

### Multi-voice scenes (`lines`)

A segment may carry a `lines` array instead of `text`, so one scene can hold several speakers (#172). The daily show does not use this — it exists for multi-voice shows rendering through the same `render.py`.

```json
"cast": {"anchor": "Ryan", "advocate": "Aiden", "skeptic": "Ethan", "switchboard": "Chelsie"},
"segments": [
  {"source_url": "https://...", "source_title": "...", "lines": [
    {"speaker": "anchor",  "text": "Sets up the post."},
    {"speaker": "skeptic", "text": "Names what is unsupported."}
  ]}
]
```

The rules, all enforced by `validate_manifest`:

- **`text` and `lines` are mutually exclusive.** A scene's `text` is *derived* from its line texts and materialized before anything measures the segment — that is what keeps the speech-rate gate and the bloopers bin armed, since a zero-char segment is skipped as unmeasurable. An author-written `text` beside `lines` is a malformed manifest and dies naming both fields.
- **`speaker` is a role, not a voice.** It resolves through the manifest's `cast`, whose values are either a bundled preset name (`Ryan` / `Aiden` / `Ethan` / `Chelsie`) or a recorded clip `{"ref_audio": "<abs path>.wav", "ref_text": "<its exact transcript>"}` (#177). Both run on the base model, so a mixed cast still pays one model load. This is not a fifth voice mode — the four-mode precedence above governs the EPISODE voice and is unchanged — and it means recasting a role is a manifest edit, not a script edit.
- **A cast cannot share an episode with `voice_instruct`.** VoiceDesign is a second model and it drifts; the cast runs on the base model, so the combination dies. `voice: "house"` (clone) is fine — same base model, one load.
- **One scene is still one chapter with one `source_url`.** Lines render to `line_NN_LL.mp3` and join into the same `seg_NN.mp3` a single-voice segment produces, separated by `TURN_GAP_MS` (250 ms) — a *turn* gap, unrelated to the 800 ms between chapters and to the 5 s chapter-spacing floor. Everything downstream is untouched.
- **Caching is per line.** Rewriting one line re-renders that line, not the scene; a fully cached re-run still skips the model load.
- **A cast clip is keyed by its BYTES.** Re-recording one member's clip, or pointing a member at a different one, re-renders that member's lines and leaves the rest cached. Without that the run would quietly replay the old voice's audio under the new one's name — right text, right length, wrong person — so a clip entry is never cached on its path alone.

## Chapter-duration guardrail

Spotify requires consecutive chapter starts to be at least 5 seconds apart (the final chapter is exempt). That is the only platform rule on chapter length, and no real segment comes close to violating it — `render.py` pads trailing silence only if a segment is short enough to breach the 5s floor.

Chapters under 30 seconds used to be capped at 3 per episode, and `render.py` padded up to 12s of silence to comply. Upstream dropped that cap (save-to-spotify PR #44), verified 2026-08-22 against CLI 0.2.0. Short segments no longer risk the episode, so the 600+ chars-per-segment target is now editorial pacing, not a platform constraint.

## Show + dedup config

```jsonc
// ~/.config/daily-podcast/config.json
{
  "show_id": "spotify:show:...",       // required; one-time setup
  "show_name": "Daily Digest",         // rendered onto every generated cover unless
                                       //   a manifest overrides it (a second show
                                       //   sets its own; see the manifest schema), so a
                                       //   change here splits the catalogue's art;
                                       //   the public show is now "Cortech Daily"
                                       //   and this has NOT been renamed to match
                                       //   - that decision is issue #133.
  "host_name": "Schmug",               // narration only - the name the script says
                                       //   aloud. Matches the public show's
                                       //   <itunes:author>/<itunes:owner> credit;
                                       //   nothing derives a slug or filename from it.
  "opml_files": ["/path/to/feeds.opml"], // optional; used by the unattended run
  "lookback_hours": 24,                  // optional; default 24
  "target_item_count": 10,               // optional; default 10
  "auto_prune_episodes": false,          // optional; default false. When true, an upload
                                         //   that hits the show's episode cap (429
                                         //   RATE_LIMIT_EXCEEDED / capacity) prunes the
                                         //   oldest episode(s) and retries the upload once.
  "max_prune_per_run": 1,                // optional; default 1. Hard ceiling on how many
                                         //   episodes an auto-prune may delete per run.
                                         //   <= 0 is refused (no prune). Deleting a
                                         //   published episode is irreversible.
  "episode_cap": 60,                     // optional; default 60. Pre-flight compares the
                                         //   show's episode count against this and
                                         //   pre-prunes a slot BEFORE the render, so a
                                         //   cap 429 never costs a wasted TTS pass.
  "poll_timeout_s": 1800,                // optional; default 1800. How long to wait for
                                         //   Spotify processing. The old 600 expired while
                                         //   an episode was legitimately still PROCESSING.
  "r2_bucket": "clodcast",               // optional; enables the web feed (see below)
  "r2_public_base_url": "https://audio.cortech.online"  // optional; public URL for <slug>.mp3
}
```

Two more append-only logs live beside `covered.json` and `runs.jsonl`:

- **`rejections.jsonl`** — artifacts Spotify rejected server-side, keyed by
  sha256. The artifact gate refuses to re-upload identical bytes; see
  [rejected-artifact.md](../../incidents/rejected-artifact.md).
- **`incidents/new/`** — structured reports written on any non-clean exit
  (`DAILY_PODCAST_INCIDENT_DIR` overrides the location).

### Bloopers bin (`bloopers/`)

An archive of TTS clips worth keeping, written as a side effect of paths that
already exist. Nothing in a run ever reads it back — it is write-only until a
meta-episode is cut from it by hand. Clips are content-addressed under
`bloopers/clips/<sha16>.mp3`; `bloopers/index.jsonl` is append-only with one
full-key-set row per clip (same line-by-line read contract as `runs.jsonl`).

| `reason` | Fires when | Banks |
| --- | --- | --- |
| `gate` | the artifact gate is about to reject a segment (< `MIN_SPEECH_RATE_RATIO`) | that one segment, with the rate evidence |
| `near-miss` | a segment PASSED but reads slow (`MIN_SPEECH_RATE_RATIO`–`NEAR_MISS_RATE_RATIO`) | that segment; the episode still ships |
| `run-failed` | any run dies after TTS | every `seg_*.mp3` in the workdir |
| `manual` | you run `bloopers.py mark` | an ffmpeg trim of any audio file |

Four things here are load-bearing:

- **Capture runs BEFORE `verify_artifact`, not after.** A speech-rate rejection's
  documented recovery deletes the offending `seg_NN.mp3`, and a stale workdir
  empties itself within days — so anything measured after the `die()` is already
  unrecoverable. No branch may be introduced between the measurement and the copy.
- **The `run-failed` sweep is suppressed on a speech-rate rejection.** The `gate`
  trigger has already banked the precise offender; sweeping too would bank the
  eleven clean segments beside it and bury the one clip worth keeping. Every other
  failure identifies no segment, so there the sweep is the only thing that saves
  the audio.
- **Capture is best-effort and never changes a run's exit code** — the same
  contract as `write_run_log` and the incident reports. A full disk loses a joke,
  not an episode. `--dry-run` banks nothing and logs what it would have banked.
- **Clips are content-addressed**, so a same-day resume (which re-runs the gate
  against a cache-hit segment) is a no-op rather than a duplicate.

Most failures are upload/poll problems whose audio is perfectly fine, so the bin
deliberately fills with non-bloopers; `reason` is what keeps them siftable:

```bash
# what is actually worth listening to
jq -r 'select(.reason!="run-failed") | [.reason,.segment,.note] | @tsv' \
  ~/.config/daily-podcast/bloopers/index.jsonl

# how much material is banked, in seconds
jq -s 'map(.duration_ms // 0) | add / 1000' ~/.config/daily-podcast/bloopers/index.jsonl

# bank a clip you heard yourself (works on a workdir segment, an episode.mp3, or
# a feed episode you downloaded)
python3 bloopers.py mark --from episode.mp3 --start 4:12 --end 4:58 --note "birdsbirdsbirds"
```

```jsonc
// ~/.config/daily-podcast/covered.json — written by render.py on successful upload.
// Pruned to a 180-day retention window on each write (the `date` field drives this);
// entries with a missing/malformed `date` are kept.
{
  "https://example.com/post-1": {"date": "2026-05-22", "episode_uri": "spotify:episode:..."},
  "https://example.com/post-2": {"date": "2026-05-21", "episode_uri": "spotify:episode:..."}
}
```

`~/.config/daily-podcast/inflight.json` is a transient crash-recovery record (an episode that uploaded but hasn't reached `READY`+dedup yet) — written after `upload()` succeeds and cleared after dedup. It is **not** a second dedup source; `covered.json` stays authoritative. See [Automatic cron recovery](#automatic-cron-recovery-cross-day-workdir-independent) below.

### Episode-cap auto-prune (`auto_prune_episodes`)

A Spotify show has a hard episode cap. When `upload()` hits it, save-to-spotify returns a `429` with `error_code: RATE_LIMIT_EXCEEDED` / `reason: capacity`. By default `render.py` fails with that structured reason (so it's distinguishable from a transient upload flake, which surfaces the same non-zero exit). Set `auto_prune_episodes: true` to have the renderer instead delete the oldest episode(s) and retry the upload **once**. Deleting a published episode is **irreversible**, so the prune is deliberately conservative:

- **Bounded** by `max_prune_per_run` (default 1; `<= 0` is refused).
- **Tiered** selection: `FAILED` episodes first (they count against the cap but have no playable audio), then oldest by `created_at`. An in-flight `NOT_READY` episode is never selected, and an episode with a missing/malformed `created_at` is skipped rather than assumed oldest.
- **Scoped** to the configured `show_id`; never touches this run's own or a concurrent run's just-created episode.
- **`--dry-run` deletes nothing** — it logs what it *would* delete.
- Every deletion is logged (`episode_uri` + `created_at` + `title`) to stdout and recorded under `pruned_episodes` in `runs.jsonl`.

`covered.json` is intentionally left unchanged when an episode is pruned: its entries would point at a now-dead `episode_uri`, but dedup only needs "don't re-cover this URL", which stays correct.

### Run log (across-runs observability)

Every `render.py` run appends one JSON record to `~/.config/daily-podcast/runs.jsonl` — on success, on `--dry-run`, and on failure. Append-only (never rewritten); one line per day, so retention is the operator's job. Each record carries a **stable** key set (missing values are `null`, never absent) so the file parses cleanly line-by-line in `jq`/pandas:

```jsonc
// ~/.config/daily-podcast/runs.jsonl — one line per run
{
  "timestamp": "2026-06-03T06:00:12+00:00",  // ISO 8601 UTC
  "status": "ready",                         // "ready" | "web-ready" (#155) | "dry-run" | "failed"
  "episode_uri": "spotify:episode:...",      // null unless ready; always null on a web-only ship
  "title": "Mojo open source, OpenAI's pause, Cursor vs GitHub - June 3, 2026",
  "voice": "house", "voice_mode": "clone",
  "chapter_count": 6, "duration_s": 412.3, "segment_count": 6,
  "workdir": "/var/folders/.../T/daily-podcast-xxxx",
  "manifest_path": "/tmp/manifest.json",
  "error_message": null,                     // the die() message on failure
  "git_sha": "ea5e845",                      // of render.py (mtime fallback off-git)
  "loudnorm": {"input_i": -19.4, "output_i": -16.0, "output_tp": -1.5, "output_lra": 6.9},
  "pruned_workdirs": null,                    // {count, freed_bytes} when --prune-workdirs ran
  "r2_status": "published",                   // "published" | "skipped" | "failed" or null pre-publish (#48)
  "resumed": false,
  "mp3_url": null,                            // public R2 URL on a web-only ship, else null (#155)
  "bloopers_captured": 0                      // clips banked into the bloopers bin this run (#169)
}
```

Sample queries:

```bash
# Every failure and its error
jq -r 'select(.status == "failed") | "\(.timestamp)  \(.error_message)"' ~/.config/daily-podcast/runs.jsonl
# Loudness drift over time (Spotify targets -16 LUFS)
jq -r 'select(.loudnorm) | "\(.timestamp)  \(.loudnorm.output_i)"' ~/.config/daily-podcast/runs.jsonl
# Which voice ran each day
jq -r '"\(.timestamp)  \(.voice) (\(.voice_mode))"' ~/.config/daily-podcast/runs.jsonl
```

First run with no `config.json`: ask the user whether to use an existing show (list via `save-to-spotify --json shows`) or create a new one, then persist the choice.

## Publishing to the web (Cloudflare R2)

Optional, additive. When R2 is configured, `render.py` also publishes each finished
episode to a Cloudflare R2 bucket *after* the Spotify upload reaches `READY`:

- `<bucket>/<slug>.mp3` — the episode audio (publicly fetchable at `r2_public_base_url`)
- `<bucket>/<slug>.jpg` — the cover (best-effort)
- `<bucket>/manifest.json` — a newest-first array of episode entries, capped at 200,
  conforming to cortech.online's `episodeSchema`. [cortech.online](https://github.com/schmug/cortech.online)
  reads this at build time and renders `/podcast/` plus an iTunes RSS feed at
  `/podcast/rss.xml`.

A manifest may set an optional `"r2_manifest_name"` — a bare filename matching
`[A-Za-z0-9._-]+\.json` (no path separators; anything else fails validation) — to route
its entry into a differently-named manifest object in the same bucket. This is how a
second show sharing the bucket keeps its web feed out of the daily show's
`manifest.json`. Unset means `manifest.json`; episode and cover object keys are
unaffected either way.

Episode and cover keys have their own optional `"r2_key_prefix"` — a bare prefix
matching `[A-Za-z0-9._-]+` with an optional trailing `/` (no paths, no dot-only
values; anything else fails validation) — prepended to the `<slug>.mp3`/`<slug>.jpg`
object keys and to the `mp3_url`/`cover_url` the web-feed entry advertises. The slug
is date-keyed, so two shows publishing the same day into the shared bucket would
otherwise mint identical keys and the later publish would overwrite the earlier
show's objects (#142). Unset means no prefix — the daily show's keys byte-identical
to before. The `slug` field itself (the permalink guid) is never prefixed.

The slug's own literal is per-show: an optional `"slug_prefix"` — lowercase kebab
matching `[a-z0-9]+(-[a-z0-9]+)*`, at most 62 chars; anything else fails validation —
replaces the historical `daily-digest` literal in `<slug>` (#162), so a second show's
permalinks and guids stop carrying this show's name. Unset means `daily-digest` and
every published daily slug stays byte-identical. The slug remains keyed on the date
alone (#128): the prefix swaps a literal and can never re-couple the slug to the title.

### Web-only shipping (`"ship_mode": "web"`)

A manifest may set `"ship_mode"` to `"spotify"` (the default when the key is absent)
or `"web"`; anything else fails validation, because falling back to the default on a
typo would upload an episode that was never meant to reach Spotify.

`"web"` inverts the relationship above — the R2 publish stops being additive and
becomes the ship itself (#155). It is how the RSS-first
[Frontier Commits](../frontier-commits/SKILL.md) show publishes:

- **`save-to-spotify` is never invoked.** No upload, no `timeline set`, no readiness
  poll, no episode-cap capacity check or prune, and no in-flight reconciliation
  (nothing is ever left in flight). `show_id` is not required and is ignored.
- **Pre-flight runs the local subset plus R2, and R2 is REQUIRED.** The three-state
  credential check's `absent` — a pass on the default path, where the web feed is
  optional — is a failure here, so a misconfigured host fails in seconds instead of
  after a full TTS render.
- **`verify_artifact` still gates the publish**, exactly as it gates the upload.
- **`covered.json` is written only after the publish succeeds.** Same
  only-after-success posture as the default path, with the publish as the success
  condition; a failed publish fails the run and leaves those URLs in the pool. The
  dedup entry records the published mp3 URL in place of an episode URI.
- **The run-log record uses `"status": "web-ready"`** and fills `mp3_url` (null on
  every Spotify-path record). The final JSON on stdout carries `mp3_url` instead of
  `episode_uri`.

`--dry-run` behaves the same as it does anywhere else: it renders, runs the artifact
gate, previews the R2 URL, and publishes nothing.

### `slug` is keyed on the date, never the title

`slug` comes from `slug_for_date(<episode date>)` alone — the manifest `title` is
display-only free text and cannot reach it. This is not cosmetic: cortech.online
republishes `/podcast/<slug>/` as an `isPermaLink` `<guid>`, and Spotify treats a
changed guid as a brand-new episode, so every published slug is immutable in practice.
Coupling the two (as `slugify(title, date)` used to) meant rewriting a title silently
duplicated the whole back catalogue. Keep them decoupled: enrich titles freely, and
never derive the slug from anything but the date.

The slug's shape (`daily-digest-august-23-2026`) is a compatibility artifact — it
reproduces the slugs minted from the old date-only titles, hence the historical
`daily-digest-` prefix, the unpadded day, and the year. `tests/data/published_slugs.tsv`
pins every live slug byte-for-byte; append to it, never edit it. The `daily-digest`
literal is only the default `slug_prefix` (#162) — a second show swaps the literal,
nothing else about the shape. The date resolves the
same way `resolve_pubdate` does (explicit manifest `date` wins), so a back-fill
re-render reproduces its historical slug and upserts the same R2 object instead of
minting a second one. `--dry-run` prints the URL through the same resolver the real
publish uses, so the rehearsal is exact.

Each manifest entry carries both a Spotify-flavored `description` (HTML — `<p>summary</p>`,
one timestamped `<p>… - <a>source</a></p>` per chapter, then the credit footer — see
[Episode description footer](#episode-description-footer)) **and** a clean
`summary` field (#45) so web/RSS consumers can render prose without HTML-stripping the
description. `summary` is **HTML-by-contract** (the user authored it), so a consumer
should still escape it as untrusted text rather than trusting it as guaranteed-plain.
`description` and `chapters[]` are unchanged — the `summary` field is purely additive.

This is strictly additive: **Spotify is the canonical artifact.** A publish never fails
the run, changes the exit code, or rolls back `covered.json`. The final JSON line reports
a 3-state `"r2_status"` (#48): `"published"` (uploaded), `"skipped"` (R2 not configured —
a benign no-op), or `"failed"` (configured but the upload errored — the alarming case an
operator should notice; the episode is still live on Spotify). `--dry-run` skips the
publish and prints where it *would* have gone (`r2_would_publish`).

**Credentials never go in `config.json`.** Read from env (preferred for cron) or an
optional `~/.config/daily-podcast/secrets.json` (mode 0600):

```bash
export R2_ACCESS_KEY_ID=...      # R2 API token access key
export R2_SECRET_ACCESS_KEY=...  # R2 API token secret
export R2_ACCOUNT_ID=...         # Cloudflare account ID (the R2 S3 endpoint host)
# optional: export PAGES_DEPLOY_HOOK_URL=...  # POSTed after publish to rebuild the site
```

`r2_bucket` / `r2_public_base_url` live in `config.json` (or `R2_BUCKET` /
`R2_PUBLIC_BASE_URL` env overrides). All five must resolve or the publish no-ops.

**Pages deploy hook (optional, independent of the five above).** After a successful
publish, `render.py` POSTs `PAGES_DEPLOY_HOOK_URL` to rebuild the site. It resolves
first-non-empty-wins across three homes: env → `secrets.json`
(`"PAGES_DEPLOY_HOOK_URL"`) → `config.json` (`"pages_deploy_hook_url"`). A scheduled
(launchd/cron) run never inherits the interactive shell env, so the durable home is
`secrets.json` (0600) — also preferred because the URL can trigger builds;
`config.json` is the shareable-file convenience. Unset everywhere → no hook fired
(the pre-existing behaviour). `--dry-run` never fires it.

> Resume note: the R2 publish runs on both the fresh run **and** the `--workdir`
> resume path (`_resume`). A resumed episode (e.g. one that first failed at
> `poll_ready` and was recovered) is back-filled to R2 too, so it still lands on the
> web feed (#40). Resume stays `config.json`-free: it resolves R2 config from env /
> `secrets.json` only (never `load_config`), and the publish is additive + non-fatal
> exactly as on the fresh path. (An older workdir from before this change that lacks
> `description.html` degrades to a skipped back-fill rather than aborting the resume.)

## Unattended daily run

**This section is the canonical procedure for shipping an episode with no human in the loop** — a scheduled Claude routine, a cron `claude -p`, or any headless invocation. It is the single source of truth: a scheduler should invoke this skill and follow this section rather than carrying its own copy of these steps, which drift.

You are an unattended invocation. Ship today's episode and exit. Be decisive, don't ask clarifying questions, and if you genuinely cannot proceed, exit with a single-line error on stdout.

1. **Read config**
   - `~/.config/daily-podcast/config.json` — `show_id`, `opml_files`, `lookback_hours`, `target_item_count`
   - `~/.config/daily-podcast/covered.json` — URLs already covered; treat as "do not repeat". Absent or malformed → `{}`, never a failed run.

2. **Gather candidates from OPML.** For each path in `opml_files`:
   - Parse the OPML XML (a `<body>` of nested `<outline>` elements; leaves with `type="rss"` carry the feed URL in `xmlUrl`)
   - Fetch entries newer than `lookback_hours` ago; skip feeds that 404/timeout after one retry and move on
   - Capture `title`, `link`, `published`, `summary` (or first 1000 chars of content), `feed_name`
   - Use Python with `feedparser` (declared in `pyproject.toml`); fall back to `pip install --user feedparser`
   - Drop items whose `link` is already in `covered.json`

3. **Curate down to `target_item_count`** (default 10). This is a **news digest** — cover stories the way a tech-and-security *news* show would: what happened, who is affected, why it matters, and the response.

   First, **drop any item you could only summarize by reproducing attack methodology** — exploit proof-of-concept or how-to walkthroughs, step-by-step intrusion write-ups, payloads / working commands, or raw breach-and-leak dumps. Judge this from the `title`, `feed_name`, and `summary` captured in step 2 — **before** any `WebFetch` in step 4 — so you never fetch a source you would then have to refuse.

   A security story is in scope when it can be told at a reporting altitude (a vulnerability was disclosed, a breach occurred, a patch shipped); out of scope when the item *is* the technique. Reporting on a disclosed vulnerability, a breach, or published security research is ordinary tech journalism — the line is operational how-to, not the security topic. Apply the keep/drop test to **the specific item, not its feed's reputation**: a mainstream security-news feed can still carry one write-up built around exploit detail, and that one gets dropped — while a clean disclosure / impact / response story from any feed is kept. When an item is borderline (newsworthy core wrapped around some operational detail), keep it and pull only the reporting-level summary in step 4; don't drop a whole feed to avoid one item.

   Next, apply the **"same story, different URL"** test. Step 2 only dropped exact `link` matches against `covered.json`, so a story the show already ran arrives under a new URL and sails past it. Check each surviving item against `covered.json` *and* against the rest of today's candidate set; three shapes recur:

   - **Primary ↔ commentary.** A link-blog post and the announcement it links to are one story, and both are in the OPML. Pick whichever has more to say and run it once — never both, and never on consecutive days.
   - **Rumor → confirmation.** "X reportedly acquires Y" and Y's own announcement are one story. Cover it at confirmation unless the rumor itself was the news; if the rumor already shipped, the confirmation is only worth a segment when the terms changed.
   - **Outlet B rewrites outlet A.** Match on the entity — CVE ID, company, product, incident — not the URL. The same flaw with the same framing a few days apart is re-coverage, whichever outlet carries it.

   Then drop **weekly roundups and conference recaps** outright: for a *daily* show, a "week in review" is by construction a digest of what the show already ran. Match the item's `link` and `title` against the `roundup_patterns` list in [`blocked_sources.json`](blocked_sources.json) — separators are interchangeable, so `week-in-review` matches both "Week in review:" in a title and `/week-in-review-…/` in a path. That list is the pattern data; read it there rather than re-inlining it here, and note a `preferred` outlet's roundup is still a roundup.

   Then rank what remains, in order:
   1. Original reporting and analysis (e.g. Anthropic blog, Simon Willison) over aggregators
   2. Items naming specific products, releases, papers, findings, or numbers (concrete > abstract)
   3. Items from feeds not used in the past 3 days (variety across episodes)
   4. Newer over older within the lookback window

   If you cannot find at least 5 items meeting the bar, ship a shorter episode rather than padding. Dropping out-of-scope, duplicate, and roundup items is normal and counts toward this — never pad to hit a count, and never raise `target_item_count` to compensate for the extra drops.

4. **Fetch full content** for each selected item via `WebFetch`, at a **reporting altitude** — the who / what / impact / response. `WebFetch` is a summarizing fetch, so ask it for the news summary, not a verbatim dump. If an article embeds operational detail, **leave it out of what you save**. Extract the article body, not the homepage. Save to `/tmp/daily-podcast-<date>/item_NN.md`.

   Several outlets cannot be fetched at all. Consult [`blocked_sources.json`](blocked_sources.json) *before* spending a fetch: it lists each blocked domain with the reason, a recovery `strategy` (`primary-source`, `alt-outlet`, `feed-summary`), and a `substitute`. It also lists `preferred` outlets that fetch clean, and `non_article_hosts` (YouTube, Reddit, HN permalinks) that must never be a segment `source_url`. When you recover a story from a different outlet, use **that** URL as `source_url` — it is what you actually read.

5. **Write segments** per the [script template](#script-template) above. Compute `day` (day-of-year) once and use it for the cold open, the sign-off, and each segment's shape and length band — the template is a date-seeded rotation, so do not fall back to one fixed form. **Strict 1:1**: `segment[i]` ↔ `source[i]`, no merging, no reordering. **Report, don't instruct**: never include exploit steps, payloads, working commands, or any procedure an attacker could follow; if a kept item can't be substantive without them, drop it rather than sanitize it.

6. **Self-critique pass** (silent): tighten segments over 900 chars or repetitive. Never reorder, never drop a segment.

7. **Build the manifest** at `/tmp/daily-podcast-<date>/manifest.json` per the [manifest schema](#form-2--pre-built-manifest-manifestjson). Title it per [Episode title](#episode-title) — the day's three lead stories, then the date, never the bare date. Do **not** set `voice_instruct` (`"voice": "house"` resolves to the locked house voice) and do **not** set `show_id` (let `render.py` read it from config).

8. **Run the renderer** at the pinned plugin path. `${CLAUDE_PLUGIN_ROOT}` is set when this runs under a Claude Code plugin; if it is somehow unset, exit immediately with `FAILED CLAUDE_PLUGIN_ROOT unset` — do **not** search the filesystem for `render.py`.

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/daily-podcast/render.py" \
     --manifest /tmp/daily-podcast-<date>/manifest.json \
     --workdir /tmp/daily-podcast-<date>
   ```

   Always pass a stable per-date `--workdir` — it is what makes a failed run resumable. Never pass `--dry-run` (this is a real episode) and never pass `--skip-preflight`: a pre-flight failure is a real problem reported cheaply, and skipping the gate turns a five-second diagnostic into a wasted render or a destructive prune.

   `render.py` prints a final JSON line on stdout with `status`, `episode_uri`, `voice`, `voice_mode`, `chapter_count`, `duration_s`, `r2_status`, and `resumed`. It updates `covered.json` only on success.

9. **Report once and exit.** Single-line stdout, with the R2 outcome as a trailing `r2=` field (`published`→`ok`, `skipped`→`skipped`, `failed`→`FAILED`):

   ```
   SHIPPED <episode_uri> - <title> - <chapter_count> chapters - <duration_s>s - r2=ok - engine=<tts_engine>
   ```

   `r2=skipped` means R2 isn't configured (benign). `r2=FAILED` means the episode is **live on Spotify** but the web-feed publish errored — still a successful run (exit 0, `covered.json` written). **Never** turn `r2=FAILED` into a `FAILED` line; the run did not fail. On genuine failure:

   ```
   FAILED <reason>
   ```

### Unattended failure handling

| Situation | Do |
| --- | --- |
| Feed unreachable | skip, note, continue |
| Fewer than 5 viable items | ship shorter; do not pad |
| `render.py` non-zero exit | print `FAILED <stderr last line>` — the last stderr line is always the diagnostic |
| Pre-flight failure | report it; **do not** retry with `--skip-preflight`. The named check is the real problem |
| Spotify readiness `FAILED` | print `FAILED processing failed for <episode_uri>` — the upload happened, processing didn't |
| Failure *after* upload (e.g. poll timeout) | re-run the same `--manifest` + `--workdir`. It resumes: skips re-upload, re-runs `timeline set` + poll + dedup, reports `"resumed": true`. Prefer this over re-shipping, which duplicates the episode |
| `covered.json` malformed | treat as `{}` rather than failing the run |
| A leftover `inflight.json` | **leave it alone.** Recovery reconciles it automatically and abandons a rejected episode on its own; deleting it by hand is no longer the remedy |

After the run, any non-clean exit leaves a structured report in `~/.config/daily-podcast/incidents/new/`. Mention its path in the `FAILED` line's context if one was written — a report tagged `unclassified` is a failure mode nobody has documented yet.

**Today's date:** resolve via the system, never hardcode. Long form ("May 22, 2026") in the intro, short form ("2026-05-22") in workdir paths.

## Running the pipeline

Two entry points:

**Interactive (current session):** Claude writes the manifest in conversation, then runs:
```bash
python3 <skill-dir>/render.py --manifest manifest.json
```

**Headless (unattended schedule):** Use the orchestrator, which gathers + curates deterministically and summarizes each item in its own isolated `claude -p` subprocess:
```bash
python3 <skill-dir>/orchestrate.py
```
Final stdout is a single line: `SHIPPED <episode_uri> ...` or `FAILED <reason>`.

`prompts/daily.md` is a stub: the unattended procedure lives in [Unattended daily run](#unattended-daily-run) so a scheduler has exactly one source of truth to follow.

### Orchestrator (unattended)

`orchestrate.py` is the unattended entry point for scheduled runs. Core invariant: **no LLM request ever holds more than one article body** — curation is deterministic metadata-only (feedparser titles, dates, summaries), and each ranked item is summarized by its own isolated `claude -p` subprocess. A per-item classifier block, timeout, or error drops only that item (logged to `dropped.jsonl`); the remaining items still ship.

Pipeline:
1. Parse OPML, fetch feeds — metadata only, no article bodies
2. Deterministic ranking: source tier × recency × concreteness, variety penalty (feeds used within 3 days are deprioritized), per-feed cap
3. Fan-out: one `claude -p prompts/summarize_item.md` per ranked item, concurrency-capped
4. Survivors (non-blocked items) assembled into a manifest and handed to `render.py`

**CLI flags** (for `orchestrate.py`):

| Flag | Purpose |
| --- | --- |
| `--dry-run` | Forward to `render.py --dry-run`; skip upload + `feed_usage.json` write |
| `--workdir PATH` | Use this directory for the manifest and render artifacts |
| `--limit N` | Cap items fanned out (useful for testing) |
| `--manifest-only` | Assemble the manifest then stop (no render/upload) |
| `--concurrency N` | Parallel `claude -p` calls (default: 3; wide fan-out can trip API rate limits) |

**State files written by the orchestrator** (under `~/.config/daily-podcast/`):

- `feed_usage.json` — `{feed_name: last_used_date}` map; drives the variety penalty so the same feed doesn't dominate back-to-back episodes. Updated only on a successful real (`ready`) run; `--dry-run` leaves it unchanged.
- `dropped.jsonl` — append-only JSONL record of every item that was blocked, refused, timed out, errored, or hit an auth failure during a run. One record per dropped item: `{timestamp, run_date, feed_name, url, reason, detail}` (`reason` ∈ `refused`/`blocked`/`auth`/`timeout`/`error`). Useful for diagnosing feed-level issues or cyber-content policy patterns; an all-`auth` night means child `claude -p` could not authenticate (see [Unattended runs need durable credentials](#unattended-runs-need-durable-credentials)).

Note: `orchestrate.py` does **not** accept `--selftest` or `--prune-workdirs` — those flags belong to `render.py`. For disk hygiene, call `render.py --prune-workdirs N` separately.

`render.py` exits non-zero with a diagnostic on any failure. Always check the exit code; do not assume success.

For testing without uploading, use `--dry-run` — produces the MP3, cover, and timeline.json locally and reports paths, but skips the `save-to-spotify upload` and `timeline set` calls.

### Unattended-run flags

| Flag | Purpose |
| --- | --- |
| `--selftest` | Standalone health check (no real run). Mutually exclusive with `--manifest`. |
| `--load-model` | With `--selftest`: also load the TTS model (slow; the most thorough check). |
| `--skip-preflight` | Skip the built-in pre-flight gate. Escape hatch; you own the outcome. |
| `--keep-workdir` | Keep the auto-created workdir after a successful run (default: delete it). |
| `--prune-workdirs N` | Before rendering, delete auto-created workdirs older than `N` days. |

### Pre-flight (automatic, every run)

**Every render runs a pre-flight gate before any expensive work.** This is not the
same thing as `--selftest`, which is the standalone command you can call yourself;
the gate runs inside the render, and a failure aborts before a single TTS segment
is generated:

```
preflight: verifying dependencies, credentials, and capacity...
  [PASS] ffmpeg: /usr/local/bin/ffmpeg
  [PASS] ffprobe: /opt/homebrew/bin/ffprobe
  [PASS] encoder-profile: 1ch @ 44100Hz 192k libmp3lame
  [PASS] house-voice: ref wav + transcript present
  [PASS] tts-module: mlx_audio importable
  [PASS] show-id: spotify:show:…
  [FAIL] r2-credentials: R2 partially configured; missing R2_ACCESS_KEY_ID, …
preflight: FAIL (6/7)
error: preflight failed (r2-credentials); nothing was rendered or uploaded
```

| Check | Gates against |
| --- | --- |
| `ffmpeg` / `ffprobe` | missing encoder |
| `encoder-profile` | encoder settings drifting off mono / 44.1 kHz / 192 kbps |
| `house-voice` | missing ref clip or transcript |
| `tts-module` | `mlx_audio` not importable (a `find_spec` probe, not a model load) |
| `show-id` | no show configured |
| `r2-credentials` | **partially** configured R2 → the silent web-feed miss |
| `save-to-spotify-auth` | dead or missing credentials *(skipped on `--dry-run`)* |
| `episode-capacity` | the 60-episode cap — **pre-prunes a slot** *(skipped on `--dry-run`)* |

R2 is three-state: fully configured passes, **fully absent also passes** (the web
feed is optional), and *partially* configured fails. `--dry-run` runs the local
subset only — it never calls Spotify and never prunes.

### Artifact gate (automatic, after render, before upload)

Once the mp3 exists, `verify_artifact` runs a local conformance check —
encoder profile, monotonic chapter starts, the 5 s minimum gap between
consecutive chapter starts, last chapter inside the duration — and refuses to upload an artifact whose sha256 is in
`rejections.jsonl` (Spotify rejected those exact bytes before; retrying costs a
pruned episode). It runs under `--dry-run` too, so a rehearsal is a real rehearsal.

It also rejects a **TTS-degenerated segment**: each body segment's chars/sec is
compared against the median across body segments, and anything under
`MIN_SPEECH_RATE_RATIO` (0.75x) fails the gate. Qwen3-TTS occasionally derails
into looping babble mid-segment, which leaves most of that chapter's script
unspoken while every structural check still passes (see
[incidents/tts-degeneration.md](../../incidents/tts-degeneration.md)). Intro and
sign-off are excluded — they are short and legitimately slower — and the check is
skipped below `MIN_RATE_SAMPLE_SEGMENTS` body segments, where a median means
nothing. Fix by deleting the flagged `seg_NN.mp3` and re-running: the per-segment
cache re-renders only that one, and the failure is stochastic, so a retry
normally comes back clean.

### Durable state + resume

The auto workdir is `<tmpdir>/daily-podcast-<date>` — **deterministic**, so an
interrupted run resumes by re-invoking the same command. `<workdir>/state.json`
records each completed stage (`preflight`, `segments`, `concat`, `cover`,
`timeline`, `artifact_gate`, `upload`, `set_timeline`, `poll_ready`, `r2`,
`dedup`) with its metadata.

### Incident reports

On any non-clean exit the run writes a structured report (markdown + JSON
sidecar) to `~/.config/daily-podcast/incidents/new/` — override with
`DAILY_PODCAST_INCIDENT_DIR`. Each is classified against a known failure mode and
points at the matching playbook in the repo's [`incidents/`](../../incidents/)
directory. A report tagged `unclassified` means a failure mode nobody has written
up yet. Writing a report is best-effort and never changes a run's exit code.

**`--selftest`** runs an ordered set of checks (ffmpeg + ffprobe on PATH → `save-to-spotify --json shows` returns valid JSON → `config.json` parses with `show_id` → house-voice ref clip + transcript present), prints a pass/fail line each, then a JSON summary `{"status": "ok"/"failed", "checks": [...]}`. It exits `0` only if every check passes, non-zero otherwise — so a scheduler can gate on it:

```bash
python3 <skill-dir>/render.py --selftest || { echo "pre-flight failed" | mail -s "podcast down" you@example.com; exit 1; }
```

It finishes in under 5 seconds (no model load unless `--load-model`).

**Workdir hygiene.** Each run creates `<tmpdir>/daily-podcast-<date>` (the system temp dir — `$TMPDIR` on macOS, often `/tmp` on Linux). On a successful run with default flags the **auto-created** workdir is deleted (a failed run always keeps it for debugging; an explicit `--workdir` is never auto-deleted, since it backs the resume path). `--prune-workdirs N` separately sweeps any `daily-podcast-*` directory older than `N` days — it never deletes the active workdir, never follows symlinks, and refuses a non-positive `N`.

### Scheduled runs (cron / launchd)

The render now gates itself (see [Pre-flight](#pre-flight-automatic-every-run)), so the wrapper no longer has to. `--selftest` remains useful as a *separate* liveness probe — for alerting on a broken host without starting a run at all.

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/clodcast"
# Optional: alert on a broken host without starting a run. The render gates itself
# regardless, so this is monitoring, not a prerequisite.
python3 skills/daily-podcast/render.py --selftest || { echo "selftest failed"; exit 1; }
# Real run: per-item isolated orchestrator (drop-on-block, deterministic curation).
python3 skills/daily-podcast/orchestrate.py
# Triage anything the run left behind.
ls ~/.config/daily-podcast/incidents/new/ 2>/dev/null
```

For disk hygiene, `render.py --prune-workdirs N` is still the mechanism — pass it when calling `render.py` directly with `--manifest`. `orchestrate.py` does not accept `--prune-workdirs`; sweep the temp dir separately if needed.

### Unattended runs need durable credentials

`orchestrate.py` summarizes each ranked item — and writes the intro/sign-off — by spawning a **child `claude -p` subprocess**. Those children authenticate on their own: they read whatever credential is on disk or in their environment, **not** the parent's in-memory session login. In an interactive `claude` session this is invisible, because a persistent OAuth credential (`~/.claude/.credentials.json`) is already on disk for the children to use.

Under a scheduler (launchd / cron, or any harness that injects a session-scoped credential the parent holds only in memory), the children can start with **no usable credential** — no on-disk token and no `ANTHROPIC_API_KEY`. Every item then fails with `401 Invalid authentication credentials`. The orchestrator detects this case (the `AUTH` outcome) and **fails fast** with a single actionable line rather than silently degrading to the generic "no viable items":

```
FAILED no viable items; at least one item reported a 401 authentication error - under a scheduler, child `claude -p` likely has no credentials. See SKILL.md "Unattended runs need durable credentials".
```

**The requirement:** the scheduled job's child processes must be able to authenticate *without* the interactive session. That means one of:

- a **persistent on-disk credential** the children can read at run time (e.g. a valid `~/.claude/.credentials.json` for the user the job runs as), or
- an **API key in the job's own environment** (`ANTHROPIC_API_KEY`) — set in the launchd plist / cron environment itself, since a scheduled job does **not** inherit your interactive shell env (the same constraint that pushes R2 / Pages secrets into the plist or `secrets.json`). Keep keys out of `config.json` and git.

**Verify it in your actual scheduler before relying on it.** Auth in the scheduled harness is exactly the non-obvious part, so don't assume a recipe works — confirm a bare child can authenticate *from inside the scheduled context* (not your terminal):

```bash
# Run this from the scheduler itself (a one-off scheduled task / `launchctl kickstart`),
# capturing output — NOT from an interactive shell, which has different credentials.
claude -p 'reply with the single word OK' || echo "child claude -p cannot authenticate here"
```

If that 401s, fix the credential before scheduling the orchestrator; if no durable credential is available to children in your environment, drive the daily run with in-session subagents (which share the parent's working auth) instead of the `claude -p` fan-out.

### Recovering from a partial failure

> **Runbook.** Most of what used to be manual here is now automatic. The table
> below is the current division of labour; each row links to a write-up in the
> repo's [`incidents/`](../../incidents/) directory with the symptom, root cause,
> and the test that guards the remedy.
>
> | Failure | Now handled by | You do |
> | --- | --- | --- |
> | [Processing rejection (`FAILED`)](../../incidents/processing-failed.md) | recovery abandons it, records the artifact, unblocks the run | nothing |
> | [Episode-cap 429](../../incidents/episode-cap.md) | pre-flight pre-prunes; upload prunes + retries once | nothing (watch `pruned_episodes`) |
> | [Poll timeout](../../incidents/poll-timeout.md) | 1800 s window, `PROCESSING`-aware, transient-tolerant | nothing |
> | [Transient upload flake](../../incidents/transient-upload-failure.md) | one automatic retry | nothing |
> | [Connection drop](../../incidents/connection-drop.md) | deterministic workdir + `state.json` | re-run the same command |
> | [R2 skip on resume](../../incidents/r2-skip-on-resume.md) | config passed into resume; pre-flight fails a partial R2 | nothing |
> | [Rejected artifact re-upload](../../incidents/rejected-artifact.md) | artifact gate blocks identical bytes | re-render to change content |
> | [Blocked source](../../incidents/webfetch-blocked-source.md) | registry ships in the skill | pick the substitute |
> | [Child `claude -p` 401](../../incidents/auth-failure.md) | detection + fail-fast only | **fix the credential** |
>
> **`rm ~/.config/daily-podcast/inflight.json` is no longer the remedy for a
> stuck pipeline.** Recovery clears a rejected record itself. If you still find a
> lingering `inflight.json`, the episode is `PROCESSING`, not `FAILED` — leave it.

The upload → `timeline set` → poll-until-`READY` → dedup sequence can fail *after* the episode is already live on Spotify — most commonly a `poll_ready` timeout where processing simply took longer than the window. To make this recoverable, `render.py` writes `<workdir>/uploaded.json` (the episode URI + title) the moment `upload()` succeeds, before the failure-prone steps.

To resume, **re-run the same manifest with the same `--workdir`**:

```bash
python3 <skill-dir>/render.py --manifest manifest.json --workdir /tmp/daily-podcast-<date>
```

When `--workdir` is passed and it contains `uploaded.json`, `render.py` skips TTS rendering, the cover, and the upload, reuses the existing `episode.mp3` / `cover.jpg` / `timeline.json`, and re-runs only the idempotent tail (`timeline set` + poll + R2 back-fill + dedup). The final report carries `"resumed": true` and the same 3-state `"r2_status"` as a fresh run (#40). Notes:

- Resume triggers on **any** workdir containing `uploaded.json` — including the auto one, which is now deterministic (`daily-podcast-<date>`), so re-running the identical command recovers an interrupted run. A *failed* run always keeps its workdir, which is exactly when this matters.
- If the workdir has `uploaded.json` but is missing an artifact, `render.py` fails fast (`workdir has uploaded.json but missing …`) rather than re-uploading.
- `--dry-run` never resumes (it never uploads).
- After a *fully successful* run, `uploaded.json` stays in the workdir, so re-running the same `--workdir` resumes the existing episode (an idempotent no-op) instead of rendering fresh. To force a fresh render (e.g. you fixed the script and want to re-ship), delete the workdir or its `uploaded.json`.

#### Per-segment TTS cache (resume cheaply mid-render)

TTS dominates a run's cost, so a crash on segment 9 of 12 shouldn't re-render segments 1–8. Each rendered `seg_NN.mp3` carries a `seg_NN.json` sidecar with a content-hash **cache key** over the segment's spoken text **and** the resolved voice settings (mode + voice/instruct + a hash of the `ref_audio` bytes + `ref_text`).

- Re-running with the **same `--workdir`** and same manifest reuses every segment whose key still matches and re-renders only the rest. If *every* segment is cached, the ~15 s model load is skipped entirely.
- Editing one segment's `text` invalidates only that segment; the others are reused.
- Changing the `voice` (e.g. `house` → `Ryan`), the `voice_instruct`, or the bytes of `refs/house_voice.wav` invalidates the affected entries (the key changes).
- The cache is **workdir-scoped** — a fresh `--workdir` always renders fresh, so the cache can't leak across unrelated episodes. A stderr line (`cache: N/M segment(s) reusable …` / `cache hit …`) reports what was reused.

#### Automatic cron recovery (cross-day, workdir-independent)

The workdir `uploaded.json` resume above is **manual** — it only helps if you re-run with that exact `--workdir`. The unattended cron uses a **per-date** workdir (`/tmp/daily-podcast-<date>`), so a `poll_ready` timeout on Monday that dies before dedup would otherwise let Tuesday's run (different workdir, no marker) re-curate Monday's still-undeduped URLs and ship a **duplicate**.

To close that gap, the moment `upload()` succeeds `render.py` also writes a long-lived **in-flight log** at `~/.config/daily-podcast/inflight.json` (episode URI, title, workdir, the segment `source_url`s). It records the upload independently of the workdir and is cleared only after dedup completes.

On startup — before curating/rendering a new episode, on any non-`--dry-run` run — `render.py` reconciles a leftover in-flight log:

1. If the prior workdir + `timeline.json` still exist, it re-runs `timeline set` + polls that episode.
2. **`READY`** → it marks the recorded `source_url`s in `covered.json` (so curation here can't re-select them), then clears `inflight.json`.
3. **`FAILED`** → it **abandons** the record: writes an incident, records the artifact's sha256 in `rejections.jsonl`, clears `inflight.json`, and lets today's episode render. `covered.json` is deliberately *not* written — those URLs never shipped, so they return to the pool. Without this, one rejected episode disabled every future run (see [processing-failed.md](../../incidents/processing-failed.md)).
4. **Timeout** → it leaves `inflight.json` intact and stops; the episode may still be processing.

A crash *during* recovery leaves `inflight.json` intact for the next attempt, and `covered.json` stays the single source of truth — the in-flight log never gates dedup, it only ever *drives* a write into `covered.json`. `--dry-run` skips recovery entirely (it never uploads, calls Spotify, or mutates `covered.json`).

## Dependencies

- `save-to-spotify` CLI on `PATH`, authenticated (`save-to-spotify auth login`)
- Python 3.10+ with the deps declared in [`pyproject.toml`](../../pyproject.toml) — `pip install -r requirements.txt` (canonical list; covers `mlx-audio`, `soundfile`, `mutagen`, `Pillow`, `numpy`, `feedparser`)
- `ffmpeg` + `ffprobe`
- Apple Silicon Mac (Qwen3-TTS via MLX needs Metal)
- ~4 GB free disk for the VoiceDesign model on first run

## Final report

After upload completes and `episodes status` returns `READY`:

> Shipped [episode title]. [N] chapters, voice [voice]. Spotify: spotify:episode:...

Nothing else. The user can listen and judge.
