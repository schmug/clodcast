# Frontier Commits `ascii-git` Cover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Frontier Commits its own generated episode cover — a pinned ASCII commit rail on brand cyan — replacing the static `cover_image` byte-copy every episode ships today.

**Architecture:** A new `cover_style` value, `"ascii-git"`, selected through the closed whitelist #168 introduces. It reuses `_cover_ascii_horizon`'s board (margins, lockup, date, full-bleed rule, bottom-anchored headline, footer) and changes exactly three things: a different pinned glyph table (`ASCII_RAIL`, 11×40, drawn full height instead of in a corner slot), a cyan accent, and a weekly date form that both the date line and the headline strip derive from. FC's manifest drops `cover_image`; `skills/frontier-commits/refs/cover.jpg` is deleted.

**Tech Stack:** Python 3, Pillow (render only, **not** a CI dependency), pytest, ruff.

**Spec:** [docs/superpowers/specs/2026-08-24-fc-ascii-cover-design.md](../specs/2026-08-24-fc-ascii-cover-design.md)

## Global Constraints

- **Branch off #168, not `main`.** `COVER_STYLES`, `COVER_STYLE_ASCII`, `_cover_face`, `_draw_tracked`, `_cover_wrap`, `cover_headline`, `resolve_cover_style` and every `COVER_*` layout constant arrive with [#168](https://github.com/schmug/clodcast/pull/168). If #168 has merged, branch off `main`; otherwise branch off `claude/cortech-daily-art-redesign-52405c`. Verify with `grep -n "COVER_STYLES" skills/daily-podcast/render.py` before starting — an empty result means you are on the wrong base.
- **Pillow is not a CI dependency.** Every test except the render smoke test must be pure Python. The smoke test uses `pytest.importorskip("PIL")`.
- **The daily show's cover must not move by one byte.** `_cover_ascii_horizon` and `_cover_gradient` are both frozen by this work. Task 5 proves it.
- **`render.py` stays a single file.** See the repo CLAUDE.md; `tests/conftest.py` puts `skills/daily-podcast/` on `sys.path` so `import render` works.
- **Palette, exact values:** ground `#10141d` → `(16, 20, 29)`, cyan `#5ee3d1` → `(94, 227, 209)`, paper `#f2efe6` → `(242, 239, 230)`, muted `#7b7e8a` → `(123, 126, 138)`, footer ink `(76, 82, 97)`.
- **The ramp is `"@#*+=-"`**, densest first — the same `ASCII_SUN_RAMP` value #168 pins.
- **Conventional commits.** `feat(render):`, `test(cover):`, `docs(frontier-commits):`. git-cliff parses these.
- **Do not delete `_cover_gradient`.** The whitelist still accepts `"gradient"`.

---

### Task 1: The `ASCII_RAIL` table, verified against its model

**Files:**

- Modify: `skills/daily-podcast/render.py` — add constants next to `ASCII_SUN`
- Test: `tests/test_cover.py` — add a new section

**Interfaces:**

- Consumes: nothing from earlier tasks. `ASCII_SUN_RAMP` from #168.
- Produces: `render.ASCII_RAIL` (`tuple[str, ...]`, 40 rows × 11 cols), `render.ASCII_RAIL_COLS = 11`, `render.ASCII_RAIL_ROWS = 40`, `render.ASCII_RAIL_LANES = 6`, `render.ASCII_RAIL_FAN_ROWS = 10`, `render.ASCII_RAIL_FAN_STEPS = 3`, `render.ASCII_RAIL_NODE_EVERY = 4`, `render.ASCII_RAIL_STUB_ROW = 18`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cover.py`:

```python
# --- the ASCII rail (Frontier Commits) -------------------------------------
#
# Same posture as ASCII_SUN above: the table is PINNED ART, and these tests are
# what make it verifiable rather than magic. The model is a commit graph —
# `lanes` agent lanes collapsing right into a trunk, then a spine carrying nodes
# every `node_every` rows with one branch that forks left and merges back.
#
# cortech.online's scripts/frontier-cover-art.ts draws this same table; if it
# changes here, change it there too.

RAIL = render.ASCII_RAIL


def test_rail_is_the_pinned_shape():
    assert len(RAIL) == render.ASCII_RAIL_ROWS == 40
    assert max(len(r) for r in RAIL) == render.ASCII_RAIL_COLS == 11
    # No row may be padded out with trailing spaces — a trailing space is an
    # invisible cell that renders as nothing but shifts nothing, so it can only
    # ever be a transcription slip.
    assert all(r == r.rstrip() for r in RAIL)


def test_rail_trunk_is_pinned_to_the_rightmost_column():
    # Everything below the fan hangs off one spine, and that spine is what keeps
    # the art clear of the headline underneath. A row that loses its column-10
    # glyph is a gap in the trunk.
    trunk = render.ASCII_RAIL_COLS - 1
    for r, line in enumerate(RAIL[render.ASCII_RAIL_FAN_ROWS :], render.ASCII_RAIL_FAN_ROWS):
        assert len(line) == trunk + 1, f"row {r} does not reach the trunk column"
        assert line[trunk] in "|+=-", f"row {r} has {line[trunk]!r} in the trunk column"


def test_rail_fan_collapses_one_lane_per_pass():
    # Row 0 has every lane; each node row after it has one fewer, indented by the
    # lane it just lost. This is what makes the silhouette a wedge.
    for i in range(render.ASCII_RAIL_LANES - 1):
        line = RAIL[2 * i]
        nodes = [c for c, ch in enumerate(line) if ch not in " "]
        assert nodes == list(range(2 * i, render.ASCII_RAIL_COLS, 2)), (
            f"fan node row {2 * i} has nodes at {nodes}"
        )


def test_rail_merge_rows_are_backslashes_into_the_trunk():
    trunk = render.ASCII_RAIL_COLS - 1
    for i in range(render.ASCII_RAIL_LANES - 1):
        line = RAIL[2 * i + 1]
        assert line[trunk] == "|"
        merges = [c for c, ch in enumerate(line) if ch == "\\"]
        assert merges == list(range(2 * i + 1, trunk, 2)), (
            f"merge row {2 * i + 1} has slashes at {merges}"
        )


def test_rail_ramp_never_gets_denser_going_down():
    # The whole point of the ramp is a then->now fade. Reading top to bottom, the
    # index into "@#*+=-" must only ever increase — a node that gets DENSER as the
    # history gets older is the bug this test exists to catch.
    ramp = render.ASCII_SUN_RAMP
    seen = [ramp.index(ch) for line in RAIL for ch in line if ch in ramp]
    assert seen == sorted(seen), f"ramp is not monotonic: {seen}"
    assert seen[0] == 0, "the newest commit is not the densest glyph"
    assert seen[-1] == len(ramp) - 1, "the oldest commit is not the faintest glyph"


def test_rail_spends_only_its_fan_budget_on_the_top():
    # The first draft let the fan burn all six ramp steps in ten rows, which left
    # every node on the thirty-row spine as "-" and killed the fade below the
    # horizon. The fan gets three steps; the spine gets the rest.
    ramp = render.ASCII_SUN_RAMP
    fan = RAIL[: render.ASCII_RAIL_FAN_ROWS]
    fan_steps = {ramp.index(ch) for line in fan for ch in line if ch in ramp}
    assert fan_steps == set(range(render.ASCII_RAIL_FAN_STEPS))

    spine = RAIL[render.ASCII_RAIL_FAN_ROWS :]
    spine_steps = {ramp.index(ch) for line in spine for ch in line if ch in ramp}
    assert spine_steps == set(range(render.ASCII_RAIL_FAN_STEPS, len(ramp)))


def test_rail_has_exactly_one_branch_that_forks_and_merges():
    # The original tile's signature: one stub off the spine. Exactly one fork and
    # exactly one merge, below the fan, on adjacent columns to the trunk.
    spine = RAIL[render.ASCII_RAIL_FAN_ROWS :]
    forks = [r for r, line in enumerate(spine) if "/" in line]
    merges = [r for r, line in enumerate(spine) if "\\" in line]
    assert len(forks) == 1 and len(merges) == 1
    assert forks[0] < merges[0], "the branch merges before it forks"
    stub_row = render.ASCII_RAIL_FAN_ROWS + forks[0]
    assert stub_row == render.ASCII_RAIL_STUB_ROW
    # Exactly one commit sits on the branch, and it carries the SAME weight as the
    # trunk nodes bracketing it. A denser glyph here would read as a branch commit
    # newer than the trunk commit above it — the first draft used "*" and broke
    # both this and the monotonicity test.
    ramp = render.ASCII_SUN_RAMP
    branch_col = render.ASCII_RAIL_COLS - 3
    branch = spine[forks[0] : merges[0] + 1]
    on_branch = [
        line[branch_col] for line in branch if len(line) > branch_col and line[branch_col] in ramp
    ]
    assert len(on_branch) == 1, f"branch carries {len(on_branch)} commits"


def test_rail_spine_nodes_land_on_the_documented_interval():
    trunk = render.ASCII_RAIL_COLS - 1
    ramp = render.ASCII_SUN_RAMP
    spine = RAIL[render.ASCII_RAIL_FAN_ROWS :]
    nodes = [r for r, line in enumerate(spine) if line[trunk] in ramp]
    # The stub occupies four rows the interval would otherwise have used, so the
    # nodes are evenly spaced around it rather than strictly every N rows.
    gaps = {b - a for a, b in zip(nodes, nodes[1:])}
    assert gaps <= {render.ASCII_RAIL_NODE_EVERY, render.ASCII_RAIL_NODE_EVERY * 2}
    assert len(nodes) == 6
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_cover.py -k rail -v`
Expected: every test ERRORs with `AttributeError: module 'render' has no attribute 'ASCII_RAIL'`.

- [ ] **Step 3: Write the minimal implementation**

In `skills/daily-podcast/render.py`, immediately after the `ASCII_SUN` block:

```python
# The ASCII rail is Frontier Commits' pinned art — the same posture as ASCII_SUN
# above, and cortech.online's scripts/frontier-cover-art.ts draws the same table,
# which is what makes that show's channel tile and its episode covers one picture.
# tests/test_cover.py re-derives it from the model below. IF THIS TABLE CHANGES,
# CHANGE IT THERE TOO — nothing mechanical links the two repos.
#
# The model: ASCII_RAIL_LANES agent lanes collapse right into a trunk over
# ASCII_RAIL_FAN_ROWS rows, then the spine descends carrying a node roughly every
# ASCII_RAIL_NODE_EVERY rows, with one branch forking left at ASCII_RAIL_STUB_ROW
# and merging back four rows later. The ramp is budgeted across the FULL height:
# the fan spends ASCII_RAIL_FAN_STEPS steps, the spine gets the remainder. A first
# draft let the fan spend all six, which left the whole spine flat.
ASCII_RAIL_COLS = 11
ASCII_RAIL_ROWS = 40
ASCII_RAIL_LANES = 6
ASCII_RAIL_FAN_ROWS = 10
ASCII_RAIL_FAN_STEPS = 3
ASCII_RAIL_NODE_EVERY = 4
ASCII_RAIL_STUB_ROW = 18
ASCII_RAIL = (
    "@ @ @ @ @ @",
    " \\ \\ \\ \\ \\|",
    "  @ @ @ @ @",
    "   \\ \\ \\ \\|",
    "    # # # #",
    "     \\ \\ \\|",
    "      # # #",
    "       \\ \\|",
    "        * *",
    "         \\|",
    "          +",
    "          |",
    "          |",
    "          |",
    "          +",
    "          |",
    "          |",
    "          |",
    "         /|",
    "        | |",
    "        + |",
    "        | |",
    "         \\|",
    "          |",
    "          |",
    "          |",
    "          =",
    "          |",
    "          |",
    "          |",
    "          =",
    "          |",
    "          |",
    "          |",
    "          -",
    "          |",
    "          |",
    "          |",
    "          -",
    "          |",
)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_cover.py -k rail -v`
Expected: 8 passed.

- [ ] **Step 5: Run the full suite and lint**

Run: `pytest -q && ruff check . && ruff format --check .`
Expected: all green, count unchanged from base +8.

- [ ] **Step 6: Commit**

```bash
git add skills/daily-podcast/render.py tests/test_cover.py
git commit -m "feat(render): pin ASCII_RAIL, the Frontier Commits cover table"
```

---

### Task 2: The weekly date form and its headline strip

**Files:**

- Modify: `skills/daily-podcast/render.py` — add next to `cover_headline`
- Test: `tests/test_cover.py`

**Interfaces:**

- Consumes: nothing from Task 1.
- Produces: `render.week_label(date_str: str) -> str` returning e.g. `"Week of August 24, 2026"`; `render.cover_headline_weekly(title: str, date_str: str) -> str`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cover.py`:

```python
# --- the weekly date fork --------------------------------------------------
#
# Frontier Commits titles end " - Week of August 24, 2026", not the ISO date, so
# #168's cover_headline strip misses entirely: the tail survives into 96px type,
# the date lands on the cover twice in two formats, and the headline is pushed to
# COVER_HEADLINE_MAX_LINES where real topics start falling off the bottom.


def test_week_label_reads_the_way_the_title_does():
    # Must match the title tail EXACTLY, because the strip below is built from it.
    assert render.week_label("2026-08-24") == "Week of August 24, 2026"
    assert render.week_label("2026-01-05") == "Week of January 5, 2026"
    # No zero padding on the day: "January 5", never "January 05".
    assert "05" not in render.week_label("2026-01-05")


def test_weekly_headline_strips_the_tail_the_date_line_prints():
    # The one invariant that matters here: the string the cover PRINTS is the
    # string the headline STRIPS. Built independently in two places, they drift
    # and the bug comes back wearing a different suffix.
    date = "2026-08-24"
    title = f"Claude Code, the codex train, OpenAI's Python SDK - {render.week_label(date)}"
    assert render.cover_headline_weekly(title, date) == (
        "Claude Code, the codex train, OpenAI's Python SDK"
    )


def test_weekly_headline_keeps_a_mid_string_dash():
    date = "2026-08-24"
    title = f"grok-2.5, a well-known fork, MCP - {render.week_label(date)}"
    assert render.cover_headline_weekly(title, date) == "grok-2.5, a well-known fork, MCP"


def test_weekly_headline_leaves_the_legacy_em_dash_titles_alone():
    # The two PUBLISHED episodes are titled "Frontier Commits — week of August 17,
    # 2026" — em dash, lowercase "week", show name in front — the exact shape
    # SKILL.md now forbids ("Never title an episode `Frontier Commits — Week of
    # ...`"). They predate #161. Teaching the matcher this form would leave the
    # headline reading "Frontier Commits", which is worse than leaving it alone.
    # Pinned so a later agent does not "improve" the matcher into handling both.
    legacy = "Frontier Commits — week of August 17, 2026"
    assert render.cover_headline_weekly(legacy, "2026-08-17") == legacy


def test_weekly_headline_is_a_noop_without_a_date():
    title = "Anthropic archives claude-quickstarts"
    assert render.cover_headline_weekly(title, "") == title


def test_weekly_strip_does_not_touch_the_daily_iso_form():
    # cover_headline (ISO) and cover_headline_weekly must stay independent; the
    # daily show keeps its own path.
    title = "Cloudflare ships Workers AI batch - 2026-08-24"
    assert render.cover_headline_weekly(title, "2026-08-24") == title
    assert render.cover_headline(title, "2026-08-24") == "Cloudflare ships Workers AI batch"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_cover.py -k "week" -v`
Expected: ERRORs with `AttributeError: module 'render' has no attribute 'week_label'`.

- [ ] **Step 3: Write the minimal implementation**

In `skills/daily-podcast/render.py`, immediately after `cover_headline`:

```python
def week_label(date_str: str) -> str:
    """`"2026-08-24"` -> `"Week of August 24, 2026"`.

    The weekly shows' date form. Matches the tail their episode titles carry
    (frontier-commits SKILL.md, "Title format") EXACTLY, because
    cover_headline_weekly strips what this prints — see the note there.

    `%-d` rather than `%d`: the title says "August 24" and "January 5", never
    "January 05", and a padded day here would silently stop the strip matching.
    """
    if not date_str:
        return ""
    return dt.datetime.strptime(date_str, "%Y-%m-%d").strftime("Week of %B %-d, %Y")


def cover_headline_weekly(title: str, date_str: str) -> str:
    """The episode title with its `" - Week of <Month D, YYYY>"` tail removed.

    The weekly counterpart to cover_headline. It exists because a weekly show's
    tail is not the ISO date, so the ISO strip misses and the tail survives into
    the largest type on the canvas — with the date then printed twice, in two
    formats, and the headline pushed to COVER_HEADLINE_MAX_LINES where real
    topics start dropping off the bottom.

    Built from week_label deliberately: the string the cover prints IS the string
    stripped here. Two independent implementations of "the weekly form" is the
    same bug in a new costume.

    Deliberately does NOT match the legacy `"Frontier Commits — week of ..."`
    form the two published episodes carry (pre-#161, and forbidden by SKILL.md
    since). Applied there it would leave the headline reading "Frontier Commits".
    """
    suffix = f" - {week_label(date_str)}"
    if date_str and title.endswith(suffix):
        return title[: -len(suffix)]
    return title
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_cover.py -k "week" -v`
Expected: 6 passed.

- [ ] **Step 5: Confirm `%-d` works on this platform**

Run: `python3 -c "import datetime as dt; print(dt.datetime(2026,1,5).strftime('Week of %B %-d, %Y'))"`
Expected: `Week of January 5, 2026`

`%-d` is glibc/BSD, not C89. It works on macOS and Linux, which is every host this runs on. If it ever prints `%-d` literally, replace with `f"Week of {d.strftime('%B')} {d.day}, {d.year}"` — do not zero-pad.

- [ ] **Step 6: Commit**

```bash
git add skills/daily-podcast/render.py tests/test_cover.py
git commit -m "feat(render): weekly date form and its matching headline strip"
```

---

### Task 3: The `ascii-git` renderer and its dispatch

**Files:**

- Modify: `skills/daily-podcast/render.py` — `COVER_STYLES`, colour constants, `_cover_ascii_git`, `build_cover`
- Test: `tests/test_cover.py`

**Interfaces:**

- Consumes: `ASCII_RAIL` and its constants (Task 1); `week_label`, `cover_headline_weekly` (Task 2); from #168: `COVER_SIZE`, `COVER_MARGIN`, `COVER_GROUND`, `COVER_PAPER`, `COVER_MUTED`, `COVER_FOOTER_INK`, `COVER_FOOTER`, `COVER_LOCKUP_Y`, `COVER_LOCKUP_SIZE`, `COVER_LOCKUP_TRACKING`, `COVER_DATE_Y`, `COVER_DATE_SIZE`, `COVER_RULE_Y`, `COVER_RULE_H`, `COVER_HEADLINE_*`, `COVER_FOOTER_SIZE`, `COVER_FOOTER_Y`, `COVER_SANS_BOLD_FACES`, `COVER_SANS_TEXT_FACES`, `COVER_MONO_FACES`, `_cover_face`, `_draw_tracked`, `_cover_wrap`, `resolve_cover_style`.
- Produces: `render.COVER_STYLE_ASCII_GIT = "ascii-git"`; `render._cover_ascii_git(out_path, show_name, date_str, title_hint) -> None`; `build_cover(..., style=...)` dispatching to it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cover.py`:

```python
# --- style dispatch --------------------------------------------------------


def test_ascii_git_is_on_the_whitelist():
    assert render.COVER_STYLE_ASCII_GIT == "ascii-git"
    assert render.COVER_STYLE_ASCII_GIT in render.COVER_STYLES


def test_unknown_cover_style_still_dies():
    # The whole reason the whitelist is closed: a typo must fail validation, not
    # fall through to the default and silently restyle a published feed.
    with pytest.raises(SystemExit):
        render.validate_manifest({"title": "t", "segments": [], "cover_style": "acsii-git"})


def test_resolve_cover_style_reads_the_manifest():
    assert render.resolve_cover_style({"cover_style": "ascii-git"}) == "ascii-git"
    assert render.resolve_cover_style({}) == render.COVER_STYLE_ASCII


def test_build_cover_dispatches_each_style_to_its_own_renderer(monkeypatch, tmp_path):
    calls = []
    for name in ("_cover_ascii_horizon", "_cover_ascii_git", "_cover_gradient"):
        monkeypatch.setattr(
            render, name, lambda *a, _n=name, **k: calls.append(_n), raising=True
        )
    out = tmp_path / "c.jpg"
    render.build_cover(out, "S", "2026-08-24", "t", style=render.COVER_STYLE_ASCII)
    render.build_cover(out, "S", "2026-08-24", "t", style=render.COVER_STYLE_ASCII_GIT)
    render.build_cover(out, "S", "2026-08-24", "t", style=render.COVER_STYLE_GRADIENT)
    assert calls == ["_cover_ascii_horizon", "_cover_ascii_git", "_cover_gradient"]


def test_ascii_git_renders_a_real_cover(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    out = tmp_path / "fc.jpg"
    render._cover_ascii_git(
        out,
        "Frontier Commits",
        "2026-08-24",
        "Anthropic archives claude-quickstarts - Week of August 24, 2026",
    )
    assert out.exists() and out.stat().st_size > 10_000
    with Image.open(out) as im:
        assert im.size == (render.COVER_SIZE, render.COVER_SIZE)
        assert im.mode == "RGB"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_cover.py -k "ascii_git or dispatch or cover_style" -v`
Expected: `AttributeError: module 'render' has no attribute 'COVER_STYLE_ASCII_GIT'`.

- [ ] **Step 3: Write the minimal implementation**

Extend the style constants #168 added:

```python
COVER_STYLE_ASCII_GIT = "ascii-git"
COVER_STYLES = (COVER_STYLE_ASCII, COVER_STYLE_ASCII_GIT, COVER_STYLE_GRADIENT)
```

Add the accent next to the other brand tokens:

```python
COVER_CYAN = (94, 227, 209)  # #5ee3d1 — --color-cyan, the weekly show's accent
```

Add the rail's own layout constants next to the sun's:

```python
# The rail runs the FULL height — top margin to the footer's baseline — rather
# than the sun's 350x346 corner slot, and the trunk deliberately crosses the
# horizon rule. That is the composition, not an overflow: the history does not
# stop at the horizon. A corner-slot version of this art was drawn first and
# failed the thumbnail test outright — a line drawing has roughly a sixth of the
# sun's inked cells in the same area, so at 88px it reads as lint. Running it
# full height is what puts the mass back.
COVER_RAIL_X = 947  # trunk lands at 947 + 10*30.4 = 1251, clear of the headline
COVER_RAIL_Y = 74
COVER_RAIL_CELL_W = 30.4
COVER_RAIL_CELL_H = 30.4  # square cells: the `|` glyphs must abut to read as a line
COVER_RAIL_SIZE = 46  # font size > cell height on purpose, so the spine is continuous
COVER_RAIL_HEADLINE_MAX_W = 1050  # keep the headline off the trunk
```

Add the renderer after `_cover_ascii_horizon`:

```python
def _cover_ascii_git(out_path: Path, show_name: str, date_str: str, title_hint: str) -> None:
    """Frontier Commits' cover: an ASCII commit rail over the house horizon rule.

    Shares ascii-horizon's board — margins, lockup, date, rule, bottom-anchored
    headline, footer — because these two are ONE house design in two accents and
    are supposed to move together. That is the opposite of the posture
    _cover_gradient gets, deliberately: that one is frozen legacy, these two are
    live siblings, and a shared helper is what keeps them from drifting apart
    rather than how they drift.

    What is NOT shared is the date: this show's titles end
    " - Week of <Month D, YYYY>", so it uses week_label / cover_headline_weekly.
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (COVER_SIZE, COVER_SIZE), COVER_GROUND)
    d = ImageDraw.Draw(img)

    mono = _cover_face(ImageFont, COVER_MONO_FACES, COVER_RAIL_SIZE)
    lockup_font = _cover_face(ImageFont, COVER_SANS_BOLD_FACES, COVER_LOCKUP_SIZE, "Bold")
    date_font = _cover_face(ImageFont, COVER_SANS_TEXT_FACES, COVER_DATE_SIZE)
    footer_font = _cover_face(ImageFont, COVER_SANS_TEXT_FACES, COVER_FOOTER_SIZE)

    # Every glyph on its own computed cell, so the graph's geometry comes from
    # ASCII_RAIL and never from the font's advance width.
    for row, line in enumerate(ASCII_RAIL):
        y = COVER_RAIL_Y + row * COVER_RAIL_CELL_H
        for col, glyph in enumerate(line):
            if glyph == " ":
                continue
            d.text(
                (COVER_RAIL_X + col * COVER_RAIL_CELL_W, y),
                glyph,
                font=mono,
                fill=COVER_CYAN,
            )

    _draw_tracked(
        d,
        (COVER_MARGIN, COVER_LOCKUP_Y),
        show_name.upper(),
        lockup_font,
        COVER_CYAN,
        COVER_LOCKUP_TRACKING,
    )
    d.text((COVER_MARGIN, COVER_DATE_Y), week_label(date_str), font=date_font, fill=COVER_MUTED)

    d.rectangle(
        [(0, COVER_RULE_Y), (COVER_SIZE, COVER_RULE_Y + COVER_RULE_H - 1)],
        fill=COVER_CYAN,
    )

    headline = cover_headline_weekly(title_hint, date_str)
    size = COVER_HEADLINE_SIZE
    headline_font = _cover_face(ImageFont, COVER_SANS_BOLD_FACES, size, "Bold")
    lines = _cover_wrap(d, headline, headline_font, COVER_RAIL_HEADLINE_MAX_W)
    while len(lines) > COVER_HEADLINE_MAX_LINES and size > COVER_HEADLINE_MIN_SIZE:
        size -= 6
        headline_font = _cover_face(ImageFont, COVER_SANS_BOLD_FACES, size, "Bold")
        lines = _cover_wrap(d, headline, headline_font, COVER_RAIL_HEADLINE_MAX_W)
    lines = lines[:COVER_HEADLINE_MAX_LINES]

    leading = int(size * COVER_HEADLINE_LEADING)
    y = COVER_HEADLINE_BOTTOM - leading * len(lines)
    for line in lines:
        d.text((COVER_MARGIN, y), line, font=headline_font, fill=COVER_PAPER)
        y += leading

    d.text(
        (COVER_MARGIN, COVER_FOOTER_Y),
        COVER_FOOTER,
        font=footer_font,
        fill=COVER_FOOTER_INK,
    )

    img.save(out_path, "JPEG", quality=88, optimize=True)
```

Extend the dispatch in `build_cover`:

```python
def build_cover(
    out_path: Path,
    show_name: str,
    date_str: str,
    title_hint: str,
    style: str = COVER_STYLE_ASCII,
) -> None:
    """Render this episode's cover in the show's cover style."""
    if style == COVER_STYLE_GRADIENT:
        _cover_gradient(out_path, show_name, date_str, title_hint)
    elif style == COVER_STYLE_ASCII_GIT:
        _cover_ascii_git(out_path, show_name, date_str, title_hint)
    else:
        _cover_ascii_horizon(out_path, show_name, date_str, title_hint)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_cover.py -v`
Expected: all pass. The render smoke test skips if Pillow is absent — install it locally (`pip install Pillow`) so it actually runs; do not accept a skip as a pass here.

- [ ] **Step 5: Render a real cover and LOOK AT IT**

```bash
python3 - <<'PY'
import sys, pathlib
sys.path.insert(0, "skills/daily-podcast")
import render
out = pathlib.Path("/tmp/fc-ascii-git.jpg")
render._cover_ascii_git(out, "Frontier Commits", "2026-08-24",
    "Anthropic archives claude-quickstarts, xAI ships grok-2.5 - Week of August 24, 2026")
print(out)
PY
```

Open it. Three things to check, in order of what is most likely wrong:

1. **The trunk reads as a continuous line**, not a stack of separate `|` glyphs. This is the one risk the design was never able to settle on paper — the mock was a browser, and Chromium's Menlo metrics are not Pillow's. If it looks like a dotted line, reduce `COVER_RAIL_CELL_H` or raise `COVER_RAIL_SIZE` until the glyphs abut, then re-run the suite.
2. **The headline never touches the trunk.** If it does, lower `COVER_RAIL_HEADLINE_MAX_W`.
3. **The rail's last row clears the footer** at `COVER_FOOTER_Y = 1275`. `74 + 40*30.4 = 1290` — the bottom row sits just below the footer's top edge, in the right margin where there is no text. Confirm it does not collide.

- [ ] **Step 6: Commit**

```bash
git add skills/daily-podcast/render.py tests/test_cover.py
git commit -m "feat(render): ascii-git cover style for Frontier Commits"
```

---

### Task 4: Move Frontier Commits onto the style and delete the copied asset

**Files:**

- Modify: `skills/frontier-commits/SKILL.md` — the `## Manifest` section
- Delete: `skills/frontier-commits/refs/cover.jpg`
- Modify: `tests/test_fc_skill_md.py:126-176` — three assertions that will go red

**Interfaces:**

- Consumes: `COVER_STYLE_ASCII_GIT` (Task 3).
- Produces: nothing consumed later.

**Context:** `tests/test_fc_skill_md.py` is a drift suite tying SKILL.md to the code. Three of its tests pin the *current* arrangement and must be updated — they are not failing incidentally, they are correctly reporting that the documented contract changed.

- [ ] **Step 1: Run the drift tests to see exactly what breaks**

Run: `pytest tests/test_fc_skill_md.py -v`
Expected: PASS (nothing has changed yet). Note these three names — they are the ones about to move:

- `test_skill_md_pins_the_shows_own_episode_art` — asserts `'"cover_image"' in _section("## Manifest")`
- `test_render_py_honors_the_documented_manifest_keys` — iterates a tuple containing `"cover_image"`
- `test_the_bundled_show_art_is_a_valid_podcast_cover` — opens `refs/cover.jpg`

- [ ] **Step 2: Rewrite the drift tests for the new contract**

Replace `test_skill_md_pins_the_shows_own_episode_art` with:

```python
def test_skill_md_pins_the_shows_own_cover_style():
    # FC used to bypass build_cover entirely with cover_image (#164), so every
    # episode shipped byte-identical art and SKILL.md carried an untested "update
    # both" rule against cortech.online's copy of the same JPEG. It now renders
    # its own cover; the style is the thing that must be pinned, because
    # render.py defaults every cover to the DAILY show's ascii-horizon design.
    # Drop the key and the next weekly episode silently restyles, in a public
    # feed, into art nobody has looked at.
    assert '"cover_style": "ascii-git"' in _section("## Manifest"), (
        "SKILL.md's manifest example must set cover_style; without it every "
        "episode ships the daily show's ASCII-horizon art"
    )


def test_skill_md_no_longer_carries_the_cover_image_copy_rule():
    # The bundled refs/cover.jpg is gone and so is the human-maintained "if that
    # art is ever redesigned, update both" instruction. A re-added cover_image
    # here would short-circuit the renderer and quietly restore the old problem.
    manifest = _section("## Manifest")
    assert "cover_image" not in manifest
    assert not (FC_DIR / "refs" / "cover.jpg").exists()
```

In `test_render_py_honors_the_documented_manifest_keys`, swap the tuple entry:

```python
        "description_footer_text",
        "cover_style",
```

Delete `test_the_bundled_show_art_is_a_valid_podcast_cover` entirely — the asset it guards no longer exists, and Task 3's render smoke test now covers "FC's art is a valid podcast cover" against the thing actually shipped.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_fc_skill_md.py -v`
Expected: `test_skill_md_pins_the_shows_own_cover_style` and `test_skill_md_no_longer_carries_the_cover_image_copy_rule` FAIL — SKILL.md still documents `cover_image` and the JPEG is still on disk.

- [ ] **Step 4: Update SKILL.md and delete the asset**

In `skills/frontier-commits/SKILL.md`, `## Manifest`:

- Change the intro from "plus six keys, all six required" to "plus six keys, all six required" → keep the count accurate: `cover_image` leaves and `cover_style` arrives, so it stays **six**.
- Delete the `cover_image` bullet entirely.
- Replace the `cover_style` bullet (#168 added one pinning `"gradient"`) with:

```markdown
- `"cover_style": "ascii-git"` — this show's own generated episode art: an ASCII commit rail on brand cyan, stamped with the week and the episode's topics (#168 follow-up). `render.py` defaults every cover to the DAILY show's `ascii-horizon` design, so the opt-in has to be explicit — drop the key and the next weekly episode silently restyles, in a public feed, into art nobody has looked at. The value is whitelisted, so a typo fails validation rather than falling back to the default. Replaces the bundled `refs/cover.jpg` that every episode used to ship verbatim (#164), and with it the requirement to keep that file in sync with cortech.online's `public/frontier-commits-cover.jpg` by hand. **The rail table is still duplicated** — `render.ASCII_RAIL` here, `scripts/frontier-cover-art.ts` there — and each file's header names the other.
```

- In the manifest example, replace the `"cover_image"` line with `"cover_style": "ascii-git",`.

Then:

```bash
git rm skills/frontier-commits/refs/cover.jpg
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_fc_skill_md.py -v && pytest -q`
Expected: all green.

- [ ] **Step 6: Validate the documented manifest actually validates**

```bash
python3 - <<'PY'
import json, re, sys, pathlib
sys.path.insert(0, "skills/daily-podcast")
import render
md = pathlib.Path("skills/frontier-commits/SKILL.md").read_text()
block = re.search(r"## Manifest.*?```json\n(.*?)```", md, re.S).group(1)
block = re.sub(r"\s*//.*", "", block)
render.validate_manifest(json.loads(block))
print("manifest example validates")
PY
```

Expected: `manifest example validates`.

- [ ] **Step 7: Commit**

```bash
git add -A skills/frontier-commits tests/test_fc_skill_md.py
git commit -m "feat(frontier-commits): render the ascii-git cover, drop the copied art"
```

---

### Task 5: Prove the daily show did not move, then open the PR

**Files:**

- No source changes. This task is evidence.

**Interfaces:**

- Consumes: everything above.
- Produces: the PR body.

- [ ] **Step 1: Render the daily's cover from the base commit**

```bash
BASE=$(git merge-base HEAD origin/main)
git stash list >/dev/null
git worktree add /tmp/cover-base "$BASE"
python3 - <<'PY'
import sys, pathlib
sys.path.insert(0, "/tmp/cover-base/skills/daily-podcast")
import render
render.build_cover(pathlib.Path("/tmp/old-horizon.jpg"), "Cortech Daily", "2026-08-24",
                   "Cloudflare ships Workers AI batch - 2026-08-24",
                   style=render.COVER_STYLE_ASCII)
render.build_cover(pathlib.Path("/tmp/old-gradient.jpg"), "Cortech Daily", "2026-08-24",
                   "Cloudflare ships Workers AI batch - 2026-08-24",
                   style=render.COVER_STYLE_GRADIENT)
PY
```

If the base predates #168 (no `COVER_STYLES`), the base for this comparison is #168's branch head, not `origin/main`. Use that commit.

- [ ] **Step 2: Render the same two covers from this branch**

```bash
python3 - <<'PY'
import sys, pathlib
sys.path.insert(0, "skills/daily-podcast")
import render
render.build_cover(pathlib.Path("/tmp/new-horizon.jpg"), "Cortech Daily", "2026-08-24",
                   "Cloudflare ships Workers AI batch - 2026-08-24",
                   style=render.COVER_STYLE_ASCII)
render.build_cover(pathlib.Path("/tmp/new-gradient.jpg"), "Cortech Daily", "2026-08-24",
                   "Cloudflare ships Workers AI batch - 2026-08-24",
                   style=render.COVER_STYLE_GRADIENT)
PY
```

- [ ] **Step 3: Compare and capture the output**

Run: `shasum -a 256 /tmp/old-horizon.jpg /tmp/new-horizon.jpg /tmp/old-gradient.jpg /tmp/new-gradient.jpg`
Expected: the two horizon hashes match, and the two gradient hashes match. **If they do not, stop** — a shared helper was edited rather than added to, and the daily show's published art has moved. Paste this output verbatim into the PR body.

Clean up: `git worktree remove /tmp/cover-base`

- [ ] **Step 4: Run every gate and capture counts**

Run: `ruff check . && ruff format --check . && pytest -q`
Expected: all clean. Record the exact pytest count for the PR body.

- [ ] **Step 5: Open the PR**

```bash
git push -u origin HEAD
gh pr create --title "feat(render): ascii-git episode cover for Frontier Commits" --body "$(cat <<'EOF'
Frontier Commits gets its own generated episode art: a pinned ASCII commit rail
on brand cyan, stamped with the week and the episode's topics. The daily show is
deliberately untouched.

## Why

Since #164/#166 FC bypasses `build_cover` entirely via `cover_image`, so every
episode ships byte-identical art and SKILL.md carries an untested "update both"
copy rule against cortech.online's asset. #168 then pins FC to `"gradient"` — a
style it never even reaches. This closes both.

`refs/cover.jpg` and the `cover_image` key are gone; the copy rule with them.

## The shared helpers are deliberate

`_cover_ascii_git` SHARES `_cover_face` / `_draw_tracked` / `_cover_wrap` and the
layout constants with `_cover_ascii_horizon` — the opposite of what #168 did with
`_cover_gradient`, and the first thing worth flagging. The distinction: #168
froze a LEGACY renderer so an unmaintained design cannot drift. These two are
live siblings, one house design in two accents, and are supposed to move
together. Sharing is what keeps them together.

## The date fork

FC's titles end `" - Week of August 24, 2026"`, so #168's ISO-suffix strip missed
entirely: the tail survived into 96px type, the date landed on the cover twice in
two formats, and the headline was pushed to the 4-line cap where real topics start
falling off. `week_label` / `cover_headline_weekly` fix both halves from one
source. The two PUBLISHED episodes use the em-dash form SKILL.md now forbids; the
strip deliberately does NOT match it (it would leave the headline reading
"Frontier Commits"), pinned by a test.

## The daily show did not move

<!-- paste the shasum output from Task 5 Step 3 -->

## Gates

<!-- paste ruff + pytest output from Task 5 Step 4 -->

## Not in this PR

The 3000x3000 channel tile is generated in `schmug/cortech.online` by
`scripts/generate-frontier-cover.ts`; that half ships separately and draws the
same table. Spec: `docs/superpowers/specs/2026-08-24-fc-ascii-cover-design.md`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: Do not merge**

This stacks on #168. Land #168 first, rebase, confirm the gates are still green, then merge through the repo's required checks.

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
| --- | --- |
| `ASCII_RAIL` pinned 11×40, re-derived from its model | 1 |
| Ramp budgeted full height, fan capped at 3 steps | 1 (`test_rail_spends_only_its_fan_budget_on_the_top`) |
| Branch stub forks and merges | 1 (`test_rail_has_exactly_one_branch_that_forks_and_merges`) |
| Weekly date form; strip and date line share a source | 2 |
| Legacy em-dash titles untouched, pinned by test | 2 |
| `cover_style: "ascii-git"` on the closed whitelist; typo dies | 3 |
| Cyan accent from `global.css` | 3 (`COVER_CYAN`) |
| Trunk crosses the horizon; art runs full height | 3 (`COVER_RAIL_*`) |
| Shared helpers with `ascii-horizon`, argued in the PR | 3, 5 |
| `cover_image` and `refs/cover.jpg` removed | 4 |
| SKILL.md drift tests updated | 4 |
| Daily cover byte-identical, proven with `shasum` | 5 |
| Render smoke test with `importorskip` | 3 |
| Thumbnail check at 176/88px | **gap → see below** |

**Gap found and closed:** the spec's acceptance criteria include "the rendered cover downsampled to 176px and 88px still reads as a commit rail — this is the test that killed the first design", and no task covered it. Task 3 Step 5 checks the full-size render only. Add to Task 3 Step 5 as a fourth check:

```bash
python3 - <<'PY'
from PIL import Image
im = Image.open("/tmp/fc-ascii-git.jpg")
for px in (176, 88):
    im.resize((px, px), Image.LANCZOS).save(f"/tmp/fc-{px}.png")
PY
```

Open both. The rail must still read as a vertical spine with a denser mass at the top. If it dissolves, the pitch is too loose — this is the same failure that killed the corner-slot design, and it is not a detail to wave through.

**Placeholder scan:** no TBDs. Every code step carries real code. Task 4's SKILL.md bullet is written out verbatim rather than described.

**Type consistency:** `week_label` / `cover_headline_weekly` are named identically in Tasks 2 and 3. `COVER_STYLE_ASCII_GIT` consistent across 3 and 4. `ASCII_RAIL_*` constants defined in Task 1 are the ones Task 1's tests read. `_cover_ascii_git`'s signature matches `_cover_ascii_horizon`'s, which is what lets `build_cover` dispatch uniformly.
