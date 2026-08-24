# Frontier Commits episode cover — `ascii-git`

**Date:** 2026-08-24
**Status:** approved, not implemented
**Depends on:** [#168](https://github.com/schmug/clodcast/pull/168) — every constant, helper and the
`cover_style` whitelist this design extends arrives with it. Nothing here can land first.
**Scope:** this repo only — `render.py`'s cover renderer and the FC skill's manifest.
**Sibling spec:** `schmug/cortech.online` → `docs/superpowers/specs/2026-08-24-fc-show-art-design.md`
covers the 3000×3000 channel tile. The two documents describe **one picture**.

## Problem

Since [#164](https://github.com/schmug/clodcast/issues/164) / [#166](https://github.com/schmug/clodcast/pull/166),
Frontier Commits bypasses `build_cover` entirely: its manifest sets
`cover_image` to `skills/frontier-commits/refs/cover.jpg`, a byte copy of the portfolio's
`public/frontier-commits-cover.jpg`. Consequences:

1. **Every episode ships identical art.** The daily show stamps each episode with its date and its
   topics; FC stamps nothing, so its episodes are indistinguishable in a client's list. FC's back
   catalogue is small — two published episodes — which is exactly why this is worth fixing now,
   before the catalogue is the thing making the case.
2. **A copy rule instead of a build.** `skills/frontier-commits/SKILL.md` carries the instruction
   "if that art is ever redesigned, update both". That is a human-maintained invariant with no test
   behind it.
3. **The two shows are about to diverge visually.** #168 gives the daily an ASCII cover and pins FC
   to `cover_style: "gradient"` — a style FC does not even reach, since `cover_image` short-circuits
   it. FC is now excluded from the house design twice over, by two different mechanisms.

## Non-goals

- The 3000×3000 channel tile. Different repo, sibling spec.
- The daily show's `ascii-horizon` cover. Untouched.
- `_cover_gradient`. It stays byte-identical and now becomes genuinely dead for FC; **do not delete
  it** — the whitelist still accepts `"gradient"`, and #168's reason for freezing it (a proven
  renderer nothing should perturb) is unchanged.
- The `cover_image` manifest key. It stays a supported feature; FC simply stops using it.
- [#133](https://github.com/schmug/clodcast/issues/133), the `config.json` show-name mismatch. FC
  pins `show_name` in its manifest and is unaffected.

## The design

A new cover style, `"ascii-git"`, selected through the existing `COVER_STYLES` whitelist. It reuses
`ascii-horizon`'s entire board — margins, lockup, date line, full-bleed rule, bottom-anchored
headline, footer — and changes three things: the glyph table, the accent, and the date semantics.

### `ASCII_RAIL` — pinned, 11 × 40

Six agent lanes collapse right into a single trunk; the trunk descends the rest of the canvas with
commit nodes at a fixed interval and one branch that forks left and merges back. The ramp
`@#*+=-` is budgeted across the **full height** — the fan spends `@ # *`, the spine fades
`+ = -` to the footer.

```
@ @ @ @ @ @      row  0   now: six agent lanes
 \ \ \ \ \|
  @ @ @ @ @
   \ \ \ \|
    # # # #
     \ \ \|
      # # #
       \ \|
        * *
         \|
          +      row 10   the trunk, established
          |
          |
          |
          +
          |
          |
          |
         /|      row 18   branch: fork
        | |
        * |
        | |
         \|      row 22   ...and merge
          |
          |
          |
          =
          |
          |
          |
          =
          |
          |
          |
          -      row 34   then: oldest history, faintest
          |
          |
          |
          -
          |      row 39
```

Pinned as a literal beside `ASCII_SUN`, for the reason #168 already records: a table you can verify
beats arithmetic you have to trust, and the grid the design was approved at is the grid that ships.

**Trunk on the rightmost column.** The fan opens up and to the left; the spine clears the headline,
which occupies the lower left. The art box runs the full height (top margin to footer baseline)
rather than `ascii-horizon`'s 350 × 346 corner slot — the trunk deliberately crosses the horizon
rule. That crossing is the point: the history does not stop at the horizon.

**Why not a compact object.** The first design was an ASCII wedge in the sun's corner slot. It
failed the thumbnail test: the sun is a filled stipple (~300 inked cells of 304), a wedge is a line
drawing (~50 in the same area), so at 88px the sun stays a solid disc and the wedge becomes lint.
Both were rendered at 176px and 88px and compared before this spec was written. Running the rail
full height restores the mass by putting more object on the canvas.

### Accent

`--color-cyan #5ee3d1`, from the portfolio's `src/styles/global.css` — the same source #168 lifted
the amber, paper, ground and muted tokens from. FC's previous green (~`#6ee7a0`) is in no palette.

The daily is the amber show, FC is the cyan show; the objects differ too (disc vs. rail), so the two
tiles are distinguishable in a library grid at any size.

### Date and headline — the behaviour fork

This is the part that is **not** a table swap, and the reason this needs its own renderer rather
than a parameter on `_cover_ascii_horizon`.

FC's episode titles end `" - Week of August 24, 2026"` (see `skills/frontier-commits/SKILL.md`,
"Title format"), not the ISO date. #168's `cover_headline` strips only an exact
`f" - {date_str}"` match, where `date_str` comes from `resolve_cover_date` — the manifest's ISO
date. So today, unchanged:

- the strip **misses**, and `- Week of August 24, 2026` survives into 96px type;
- the date then appears **twice on one cover, in two formats**;
- the headline is pushed to four lines, which is `COVER_HEADLINE_MAX_LINES`, so a busy week starts
  silently dropping real topics off the bottom.

**Fix, both halves:**

1. The date line reads the show's own weekly form — `Week of August 24, 2026` — matching the
   title's tail exactly, so cover and title agree.
2. The headline strip is taught that suffix, so the tail never reaches the type.

Derive both from one function so they cannot disagree: the string the date line prints is the string
the headline strips. A weekly form built independently in two places is the bug in a new costume.

**Only the current title format is in scope.** The two published FC episodes are titled
`Frontier Commits — week of August 17, 2026` — em dash, lowercase "week", show name in front — the
exact shape SKILL.md now forbids ("Never title an episode `Frontier Commits — Week of ...`"). They
predate [#161](https://github.com/schmug/clodcast/pull/161). Do **not** teach the strip that form:
applied to it, it would leave the headline reading `Frontier Commits`, which is worse than leaving
it alone, and those episodes' covers are already published anyway. The strip matches the hyphen +
capital-W form the skill specifies today, and nothing else. Add a test that pins this — a legacy
em-dash title passes through untouched — so a later agent does not "improve" the matcher into
handling both.

## Files

| File | Change |
| --- | --- |
| `skills/daily-podcast/render.py` | add `COVER_STYLE_ASCII_GIT` to `COVER_STYLES`; `ASCII_RAIL` + its cell constants; `_cover_ascii_git`; the weekly date/strip helper; dispatch in `build_cover` |
| `skills/frontier-commits/SKILL.md` | manifest keys: `cover_style: "ascii-git"` replaces `cover_image`; six required keys become five; drop the "update both" copy rule |
| `skills/frontier-commits/refs/cover.jpg` | **delete** — nothing reads it once `cover_image` is gone |
| `tests/test_cover.py` | re-derive `ASCII_RAIL` from its model; lock the dispatch; cover the date/strip fork |
| `tests/test_fc_common.py` (or the FC drift test's home) | assert the SKILL.md manifest example pins `ascii-git` and no longer carries `cover_image` |

### On sharing helpers with `ascii-horizon`

`_cover_ascii_git` **shares** `_cover_face`, `_draw_tracked`, `_cover_wrap` and the layout constants
with `_cover_ascii_horizon`. This is the opposite of the posture #168 took with `_cover_gradient`,
and the PR body should say so explicitly, because it is the first thing a reviewer will flag.

The distinction: #168 froze a **legacy** renderer so a design nobody is maintaining cannot drift.
These two are both live and are *supposed* to move together — they are one house design in two
accents. Sharing is the mechanism that keeps them together, not the risk. If the daily's margin
changes and FC's does not, that is the bug.

What is **not** shared: the date/headline logic, which genuinely forks per show.

## Testing

Pillow is deliberately not a CI dependency, so everything except the render smoke test must be pure
Python — which is why the drawing is split into a pinned table plus layout helpers.

- **`ASCII_RAIL` re-derived from its model**: lane count per fan row, one ramp band per step with
  the fan capped at three steps, exactly one root, the stub forking at its claimed column and
  merging back, trunk pinned to the rightmost column in every row below the fan. Same posture as
  `ASCII_SUN` and `orchestrate.SHAPE_ORDERS`.
- **Dispatch**: `cover_style: "ascii-git"` routes to the rail; `"gradient"` still routes to the
  frozen gradient; an unknown value dies in `validate_manifest` rather than falling through to a
  default.
- **The date fork**: a title ending `" - Week of August 24, 2026"` strips to bare topics; a title
  with a mid-string dash keeps it; a title with the *daily's* ISO tail is untouched by the weekly
  strip. Assert the printed date line and the stripped suffix come from the same source.
- **Headline line count**: the FC fixture that produces four lines today produces three after the
  strip.
- **Render smoke test**, `importorskip("PIL")`: a real 1400×1400 JPEG, square, opaque, non-trivial
  file size.

## Acceptance

- A real 1400×1400 cover renders and is **inspected**, not merely asserted — the pitch that makes
  the trunk read as a continuous line has only been validated in a browser so far.
- The daily's cover is **byte-identical** before and after. Prove it the way #168 did, with
  `shasum -a 256` on a cover rendered from `HEAD` and from the branch, and put the output in the PR
  body. `_cover_gradient`'s output must also be unchanged.
- `ruff check .`, `ruff format --check .`, `pytest -q` all clean, with counts in the PR body.
- The rendered cover downsampled to 176px and 88px still reads as a commit rail. This is the test
  that killed the first design.
- FC's manifest example in SKILL.md validates against `validate_manifest`.

## Open risks

- **Glyph pitch is unverified in Pillow.** Chromium's Menlo metrics are close to Pillow's but not
  identical, and the trunk reading as a line depends on `|` glyphs abutting vertically. Derive the
  cell pitch from the measured advance rather than pinning it by eye, and confirm against a real
  render before merge. The sibling spec's librsvg render must match.
- **The table is duplicated across two repos**, in Python here and JavaScript there, with no
  mechanical link. Accepted; mitigations are in the sibling spec — both sides pin a literal, both
  test it against its model, both headers name the other file.
- **Deleting `refs/cover.jpg` removes the fallback.** If `build_cover` fails, the run fails — same
  as the daily show, which has never had a static fallback. Acceptable, and stated so the next agent
  does not "restore" it.
- **Published feed changes.** Both the art and the accent move for existing subscribers. Past
  episodes keep the art already published; only new ones get the rail.
