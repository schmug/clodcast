"""
Invariant tests for the episode cover art (#163).

`build_cover` draws one design: an ASCII sun over a horizon rule. There is no
style selector — a show that wants its own art supplies `cover_image` (#164),
which is what retired the old gradient renderer. Those tests live in
tests/test_render.py alongside the rest of the cover_image gate.

CI installs Pillow and fonts-dejavu-core (#164), so the render tests here run
for real on Linux rather than skipping — which is the point: the font fallback
chain exists precisely so the cover path works off a Mac.
"""

from __future__ import annotations

import math
from pathlib import Path

import render

# --- the ASCII sun --------------------------------------------------------
#
# render.ASCII_SUN is pinned art, not something regenerated at import time —
# the grid the design was approved at is the grid that ships, and
# cortech.online's show-art SVG draws the same one. These tests are what make
# the table verifiable rather than magic: they re-derive it from the radial
# model and fail if either side drifts.


def test_ascii_sun_has_the_pinned_grid_shape():
    assert len(render.ASCII_SUN) == render.ASCII_SUN_ROWS
    for line in render.ASCII_SUN:
        assert len(line) <= render.ASCII_SUN_COLS


def test_ascii_sun_matches_the_radial_model():
    """Every glyph is the ramp step its cell's radius falls in."""
    cols, rows = render.ASCII_SUN_COLS, render.ASCII_SUN_ROWS
    cx, cy = (cols - 1) / 2, (rows - 1) / 2
    for r in range(rows):
        line = render.ASCII_SUN[r].ljust(cols)
        for c in range(cols):
            t = (
                math.hypot((c - cx) * render.ASCII_SUN_CELL_W, (r - cy) * render.ASCII_SUN_CELL_H)
                / render.ASCII_SUN_RADIUS
            )
            expected = " "
            for glyph, band in zip(render.ASCII_SUN_RAMP, render.ASCII_SUN_BANDS, strict=True):
                if t < band:
                    expected = glyph
                    break
            assert line[c] == expected, f"cell ({c},{r}) t={t:.3f}"


def test_ascii_sun_is_symmetric():
    """A sun that is not symmetric is a bug you cannot see until it ships."""
    cols = render.ASCII_SUN_COLS
    padded = [line.ljust(cols) for line in render.ASCII_SUN]
    for line in padded:
        assert line == line[::-1], f"row not mirrored: {line!r}"
    assert padded == padded[::-1], "grid not mirrored top-to-bottom"


def test_ascii_sun_ramp_never_brightens_outward():
    """Density is monotone: scanning out from the centre never gets denser."""
    cols = render.ASCII_SUN_COLS
    order = {g: i for i, g in enumerate(render.ASCII_SUN_RAMP)}
    order[" "] = len(render.ASCII_SUN_RAMP)
    mid = (cols - 1) // 2
    for line in render.ASCII_SUN:
        right = line.ljust(cols)[mid:]
        steps = [order[ch] for ch in right]
        assert steps == sorted(steps), f"ramp reverses along {right!r}"


def test_ascii_sun_ramp_and_bands_line_up():
    assert len(render.ASCII_SUN_RAMP) == len(render.ASCII_SUN_BANDS)
    assert list(render.ASCII_SUN_BANDS) == sorted(render.ASCII_SUN_BANDS)


def test_show_art_svg_draws_the_same_sun():
    """The 3000px show cover is generated in cortech.online from this same table.
    Nothing mechanical can enforce that across two repos, so at least fail here if
    the copy in this repo is edited without a note pointing at the other one."""
    src = Path(render.__file__).read_text()
    assert "cortech.online" in src.split("ASCII_SUN_COLS")[0][-1500:], (
        "the ASCII_SUN comment must keep pointing at the show-art generator that shares this grid"
    )


# --- title handling -------------------------------------------------------


def test_cover_headline_drops_the_trailing_date():
    """Episode titles carry a ' - <date>' suffix (#139) and the cover already shows
    the date on its own line; repeating it would spend the headline on nothing."""
    got = render.cover_headline(
        "Uber's GDPR fine, Flock backlash, car head-unit malware - August 24, 2026",
        "August 24, 2026",
    )
    assert got == "Uber's GDPR fine, Flock backlash, car head-unit malware"


def test_cover_headline_leaves_an_unsuffixed_title_alone():
    assert render.cover_headline("A title with - a dash in it", "August 24, 2026") == (
        "A title with - a dash in it"
    )


def test_cover_headline_survives_a_missing_date():
    assert render.cover_headline("Just a title", "") == "Just a title"


# --- rendering ------------------------------------------------------------


def _cover_size(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as im:
        assert im.mode == "RGB"
        return im.size


def test_ascii_cover_renders_a_1400_square_jpeg(tmp_path):
    out = tmp_path / "cover.jpg"
    render.build_cover(
        out,
        "Cortech Daily",
        "August 24, 2026",
        "Uber's GDPR fine, Flock backlash, car head-unit malware - August 24, 2026",
    )
    assert _cover_size(out) == (render.COVER_SIZE, render.COVER_SIZE) == (1400, 1400)


def test_generated_cover_passes_the_supplied_art_gate(tmp_path):
    """The generated and supplied cover paths must be interchangeable (#164): art
    this renderer produces has to clear the same pre-flight gate a bundled file does,
    or the two paths disagree about what a valid cover is."""
    out = tmp_path / "cover.jpg"
    render.build_cover(out, "Cortech Daily", "August 24, 2026", "A short title")
    assert render.check_cover_image(out)["ok"]


def test_a_long_show_name_stays_inside_the_canvas(tmp_path):
    """The lockup shrinks, then truncates — it never runs under the sun. A show name
    is arbitrary user config, and art that overflows ships to a public feed
    unnoticed. Asserted on the fitted width itself, with no size-floor escape
    hatch: a first version of this test passed while the name still overflowed."""
    from PIL import Image, ImageDraw, ImageFont

    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    for name in (
        "Cortech Daily",
        "Claude Code Field Notes",
        "The Extremely Long Cortech Daily Technology And Security Briefing",
    ):
        text, font = render._fit_lockup(d, ImageFont, name.upper())
        width = render._tracked_width(d, text, font, render.COVER_LOCKUP_TRACKING)
        assert width <= render.COVER_LOCKUP_MAX_W, f"{name!r} overflows at {width:.0f}px"

    out = tmp_path / "cover.jpg"
    render.build_cover(
        out,
        "The Extremely Long Cortech Daily Technology And Security Briefing",
        "August 24, 2026",
        "A short title",
    )
    assert _cover_size(out) == (1400, 1400)


def test_the_lockup_never_reaches_the_sun():
    """The width budget is the gap to the disc, not the right margin."""
    assert render.COVER_LOCKUP_MAX_W < render.COVER_SUN_X - render.COVER_MARGIN


def test_a_short_show_name_is_not_truncated():
    from PIL import Image, ImageDraw, ImageFont

    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    text, _ = render._fit_lockup(d, ImageFont, "CORTECH DAILY")
    assert text == "CORTECH DAILY"


def _cover_face_for(image_font, size: int):
    return render._cover_face(image_font, render.COVER_SANS_BOLD_FACES, size, "Bold")


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
    # The stub spans five rows and swallows the TWO node slots inside it — the fork
    # row and the merge row both fall on the interval — so the spine carries one
    # long gap where the branch is and NODE_EVERY everywhere else. Every gap is
    # still a multiple of the interval; that is what "fixed interval" means here.
    gaps = sorted({b - a for a, b in zip(nodes, nodes[1:], strict=False)})
    assert all(g % render.ASCII_RAIL_NODE_EVERY == 0 for g in gaps), gaps
    assert gaps == [render.ASCII_RAIL_NODE_EVERY, render.ASCII_RAIL_NODE_EVERY * 3]
    assert len(nodes) == 6


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
