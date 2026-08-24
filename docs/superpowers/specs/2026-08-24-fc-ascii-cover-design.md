# Frontier Commits episode cover — `ascii-git`

**Date:** 2026-08-24
**Status:** approved; **revised after #168 landed** — see [What #168 actually shipped](#what-168-actually-shipped)
**Base:** [#168](https://github.com/schmug/clodcast/pull/168) merged as `c4e841c`. This design was
written against #168's pre-rebase shape and has been corrected against what is on `main`.
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
3. **The two shows have now diverged visually.** #168 gave the daily an ASCII cover. FC is on a
   static JPEG. Two shows from one publisher, one with generated art that names the week's stories
   and one with a fixed tile, sitting next to each other in the same client.

## What #168 actually shipped

This spec was written while #168 was open, against the shape it had then. It was **rebased before
merging** and came out materially different. Recorded here because the original text assumed
otherwise and an implementer reading only that would be wrong on four counts:

| Assumed while #168 was open | On `main` at `c4e841c` |
| --- | --- |
| `cover_style` whitelist exists; this design adds a value | **Deleted during the rebase.** No `COVER_STYLES`, no `resolve_cover_style`, no `cover_style` key |
| `_cover_gradient` frozen and still reachable | **Deleted.** `build_cover` draws exactly one design |
| `_cover_ascii_horizon` is a sibling function to add beside | **No such name.** `build_cover` itself *is* the horizon renderer |
| Pillow is not a CI dependency; render tests `importorskip` | **CI installs Pillow + `fonts-dejavu-core`.** Render tests run for real on Linux |

The rebase rationale, from #168's own PR comment, was that `cover_image` strictly dominates
`cover_style`: with FC on `cover_image`, `build_cover` is never called for that show, so
`cover_style` would have been dead config and the gradient renderer dead code.

**That reasoning is correct, and its premise is exactly what this change removes.** `cover_style`
was dead *because* FC used `cover_image`. Once FC renders its own art, the key stops being dead —
it becomes the thing that selects which art. So this design reintroduces the mechanism #168
deleted, deliberately, and a reviewer is entitled to challenge that; the answer is that the
deletion was conditional on a premise that no longer holds.

The other half of that rationale — "supplying real art beats picking between two templates a show
did not design" — does not apply either. This is not FC picking a template it did not design. It is
FC getting its own design, generated per episode with the week and the topics, which a static
`cover_image` structurally cannot do: one file means identical art forever.

Surviving and reused unchanged: `_cover_face`, `_draw_tracked`, `_tracked_width`, `_cover_wrap`,
`_fit_lockup`, `cover_headline`, `ASCII_SUN` and every `COVER_*` layout constant.

## Non-goals

- The 3000×3000 channel tile. Different repo, sibling spec.
- The daily show's cover **output**. Its rendering code moves — `build_cover`'s body becomes
  `_cover_ascii_horizon` so a dispatcher can sit above it — but the JPEG it produces must not change
  by one byte. Proven, not assumed.
- Restoring `_cover_gradient`. #168 deleted it and it stays deleted; nothing here wants it.
- The `cover_image` manifest key. It stays a supported feature; FC simply stops using it.
- [#133](https://github.com/schmug/clodcast/issues/133), the `config.json` show-name mismatch. FC
  pins `show_name` in its manifest and is unaffected.

## The design

Two steps, in order.

**First, reintroduce the selector.** `build_cover`'s body moves verbatim into
`_cover_ascii_horizon`, and `build_cover` becomes a dispatcher over a closed `COVER_STYLES`
whitelist validated in `validate_manifest` — the mechanism #168 deleted, restored because its
premise has changed (see above). This step must produce a byte-identical daily cover: it is a pure
move plus a dispatch, no drawing changes.

**Then add the second design.** `_cover_ascii_git` reuses the horizon's entire board — margins,
lockup, date line, full-bleed rule, bottom-anchored headline, footer — and changes three things:
the glyph table, the accent, and the date semantics.

One helper needs widening rather than reusing as-is: `_fit_lockup` shrinks and truncates the show
name against `COVER_LOCKUP_MAX_W`, which is defined as `COVER_SUN_X - COVER_MARGIN - 40` — the gap
to the *sun*. The rail sits at a different x, so the budget becomes a parameter with the existing
value as the default. Leave it hardcoded and FC's lockup gets the wrong budget, which is precisely
the overflow bug #168's rebase had to fix once already.

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

`_cover_ascii_git` **shares** `_cover_face`, `_draw_tracked`, `_tracked_width`, `_cover_wrap`,
`_fit_lockup` and the layout constants with `_cover_ascii_horizon`, and the PR body should say so
explicitly, because a reviewer who remembers #168's frozen-gradient posture will flag it.

The distinction: #168 froze (then deleted) a **legacy** renderer so a design nobody maintains could
not drift. These two are both live and are *supposed* to move together — they are one house design in two
accents. Sharing is the mechanism that keeps them together, not the risk. If the daily's margin
changes and FC's does not, that is the bug.

What is **not** shared: the date/headline logic, which genuinely forks per show.

## Testing

**CI installs Pillow and `fonts-dejavu-core`** (added with #164, confirmed in
`.github/workflows/ci.yml` on `c4e841c`), so render tests run for real on Linux rather than
skipping. No `importorskip`. The pure-Python table tests still matter — they are what makes the
pinned art verifiable — but the rendering assertions are now enforced by CI, not just locally.

- **`ASCII_RAIL` re-derived from its model**: lane count per fan row, one ramp band per step with
  the fan capped at three steps, exactly one root, the stub forking at its claimed column and
  merging back, trunk pinned to the rightmost column in every row below the fan. Same posture as
  `ASCII_SUN` and `orchestrate.SHAPE_ORDERS`.
- **Dispatch**: `cover_style: "ascii-git"` routes to the rail, absent/`"ascii-horizon"` routes to
  the sun, and an unknown value dies in `validate_manifest` rather than falling through to a
  default. Only two values exist — the gradient is gone.
- **The date fork**: a title ending `" - Week of August 24, 2026"` strips to bare topics; a title
  with a mid-string dash keeps it; a title with the *daily's* ISO tail is untouched by the weekly
  strip. Assert the printed date line and the stripped suffix come from the same source.
- **Headline line count**: the FC fixture that produces four lines today produces three after the
  strip.
- **Render smoke test**, no skip guard: a real 1400×1400 JPEG, square, opaque, non-trivial file
  size. Runs in CI on Linux against DejaVu, which is the point of the font fallback chain.

## Acceptance

- A real 1400×1400 cover renders and is **inspected**, not merely asserted — the pitch that makes
  the trunk read as a continuous line has only been validated in a browser so far.
- The daily's cover is **byte-identical** before and after. This matters more here than it did in
  #168, because this change physically moves `build_cover`'s body into a new function rather than
  leaving it alone. Prove it the way #168 did — `shasum -a 256` on a cover rendered from `c4e841c`
  and from the branch — and put the output in the PR body.
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
