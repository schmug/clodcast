"""
Invariant tests for the episode cover art (#163).

`build_cover` dispatches over a closed `cover_style` whitelist: the daily show's
`ascii-horizon` (an ASCII sun over a horizon rule) and Frontier Commits'
`commit-rail` (a vector commit rail). #168 shipped a single renderer, having
deleted its own whitelist during a rebase — correct at the time, because FC was
on `cover_image` and `build_cover` was never called for that show. FC now
renders its own art, which is what brought the selector back.

CI installs Pillow and fonts-dejavu-core (#164), so the render tests here run
for real on Linux rather than skipping — which is the point: the font fallback
chain exists precisely so the cover path works off a Mac.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

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


# --- the commit rail (Frontier Commits) ------------------------------------
#
# Unlike ASCII_SUN above there is no pinned table here: the rail is parametric,
# so the constants ARE the model. These tests re-derive the node positions from
# them and then check the things the numbers have to guarantee — that the art
# clears the rule, the headline and the footer. A cover whose art overlaps its
# type ships to a public feed unnoticed, which is what these exist to stop.
#
# The motif is a redraw of the show's channel tile in schmug/cortech.online
# (public/frontier-commits-cover.jpg); if it changes here, change it there too.


def test_rail_nodes_are_evenly_spaced_down_the_trunk():
    nodes = render._cover_rail_nodes()
    assert len(nodes) == render.COVER_RAIL_NODES == 8
    gaps = {b - a for a, b in zip(nodes, nodes[1:], strict=False)}
    assert gaps == {render.COVER_RAIL_NODE_GAP}
    # Every commit sits on the drawn trunk, or it is a dot floating in space.
    assert nodes[0] >= render.COVER_RAIL_TOP
    assert nodes[-1] <= render.COVER_RAIL_BOTTOM


def test_rail_lights_the_commits_the_channel_tile_lights():
    assert render.COVER_RAIL_LIT == (1, 4)
    assert all(0 <= i < render.COVER_RAIL_NODES for i in render.COVER_RAIL_LIT)


def test_rail_branch_forks_from_a_lit_commit():
    # A branch off an unlit node reads as decoration. Off a highlighted one it
    # reads as the graph doing something, which is the whole point of keeping it.
    assert render.COVER_RAIL_BRANCH_NODE in render.COVER_RAIL_LIT


def test_rail_branch_clears_the_horizon_rule():
    # The channel tile puts this branch near the top; here the full-bleed rule
    # runs straight through that spot, and an elbow tangled in the rule was the
    # first thing that looked wrong. The whole branch must sit below the rule.
    rule_bottom = render.COVER_RULE_Y + render.COVER_RULE_H
    assert render.COVER_RAIL_BRANCH_TOP > rule_bottom, "branch stub crosses the rule"
    elbow_top = (
        render._cover_rail_nodes()[render.COVER_RAIL_BRANCH_NODE] - 2 * render.COVER_RAIL_ELBOW_R
    )
    assert elbow_top > rule_bottom, "branch elbow crosses the rule"


def test_rail_clears_the_headline_column():
    # The headline wraps inside COVER_RAIL_HEADLINE_MAX_W; the branch stub is the
    # leftmost ink the art puts on the canvas. These two must not meet.
    headline_right = render.COVER_MARGIN + render.COVER_RAIL_HEADLINE_MAX_W
    stub_left = render.COVER_RAIL_X - 2 * render.COVER_RAIL_ELBOW_R - render.COVER_RAIL_STROKE // 2
    assert stub_left > headline_right, (
        f"headline reaches {headline_right}, art starts at {stub_left}"
    )


def test_rail_fits_the_canvas_and_clears_the_footer():
    assert render.COVER_RAIL_X + render.COVER_RAIL_NODE_R < render.COVER_SIZE
    assert render.COVER_RAIL_BOTTOM < render.COVER_FOOTER_Y, "rail runs into the footer"
    assert render.COVER_RAIL_TOP >= 0


def test_rail_palette_is_the_channel_tiles():
    # Sampled from public/frontier-commits-cover.jpg. Drifting these is how the
    # episode art stops matching the show art a client renders above it.
    assert render.COVER_RAIL_GREEN == (94, 234, 148)
    assert render.COVER_RAIL_SLATE == (52, 63, 81)
    assert render.COVER_GROUND == (16, 20, 29)


def test_rendered_cover_actually_paints_the_nodes(tmp_path):
    # The geometry tests above only check the constants agree with each other.
    # This one checks the RENDERER agrees with the constants: read the pixels back
    # at each computed node centre and assert the right commits came out green.
    # Without it the whole model could drift from what is drawn and stay green.
    from PIL import Image

    out = tmp_path / "rail.jpg"
    render._cover_commit_rail(
        out, "Frontier Commits", "2026-08-24", "Topic - Week of August 24, 2026"
    )
    with Image.open(out) as im:
        px = im.convert("RGB").load()
        for i, cy in enumerate(render._cover_rail_nodes()):
            lit = i in render.COVER_RAIL_LIT
            want = render.COVER_RAIL_GREEN if lit else render.COVER_RAIL_SLATE
            got = px[render.COVER_RAIL_X, cy]
            # JPEG is lossy; a wide tolerance still separates green from slate.
            assert all(abs(a - b) <= 18 for a, b in zip(got, want, strict=True)), (
                f"node {i} at y={cy} painted {got}, expected ~{want}"
            )


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


# --- style dispatch --------------------------------------------------------
#
# #168 deleted its own cover_style whitelist during the rebase, because with
# Frontier Commits on cover_image the key would have been dead config and the
# second renderer dead code. This change takes FC off cover_image, which removes
# that premise — so the selector comes back, with the SAME closed-whitelist
# posture ship_mode has: how a show looks is a property of the show, and a typo
# must die at validation rather than silently restyle a published feed.


def test_cover_styles_is_a_closed_whitelist():
    assert render.COVER_STYLE_ASCII == "ascii-horizon"
    assert render.COVER_STYLE_ASCII in render.COVER_STYLES


def test_resolve_cover_style_defaults_to_the_house_design():
    # Absent means the daily show's design — every existing manifest omits the key
    # and must keep rendering exactly what it renders today.
    assert render.resolve_cover_style({}) == render.COVER_STYLE_ASCII
    assert render.resolve_cover_style({"cover_style": None}) == render.COVER_STYLE_ASCII
    assert render.resolve_cover_style({"cover_style": "ascii-horizon"}) == "ascii-horizon"


@pytest.mark.parametrize("bad", ["acsii-horizon", "ASCII-HORIZON", "", 3, [], "ascii-horizon "])
def test_unknown_cover_style_dies_at_validation(bad):
    # Every field but cover_style is valid, so the SystemExit can only come from
    # the whitelist. A manifest that dies on a missing `summary` instead would
    # pass this test forever whether or not the selector exists at all.
    with pytest.raises(SystemExit):
        render.validate_manifest(
            {"title": "T", "summary": "S", "segments": [{"text": "hi"}], "cover_style": bad}
        )


def test_validate_manifest_accepts_every_whitelisted_cover_style():
    # The positive control for the test above: proves the manifest it builds is
    # otherwise valid, so the rejections there are the whitelist doing its job.
    for style in render.COVER_STYLES:
        render.validate_manifest(
            {"title": "T", "summary": "S", "segments": [{"text": "hi"}], "cover_style": style}
        )
    render.validate_manifest({"title": "T", "summary": "S", "segments": [{"text": "hi"}]})


def test_build_cover_dispatches_to_the_horizon_renderer(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(render, "_cover_ascii_horizon", lambda *a, **k: calls.append("horizon"))
    render.build_cover(tmp_path / "c.jpg", "S", "2026-08-24", "t")
    render.build_cover(tmp_path / "c.jpg", "S", "2026-08-24", "t", style="ascii-horizon")
    assert calls == ["horizon", "horizon"]


# --- the commit-rail renderer ----------------------------------------------


def test_commit_rail_is_on_the_whitelist():
    assert render.COVER_STYLE_COMMIT_RAIL == "commit-rail"
    assert render.COVER_STYLE_COMMIT_RAIL in render.COVER_STYLES


def test_resolve_cover_style_reads_the_manifest():
    assert render.resolve_cover_style({"cover_style": "commit-rail"}) == "commit-rail"
    assert render.resolve_cover_style({}) == render.COVER_STYLE_ASCII


def test_build_cover_dispatches_each_style_to_its_own_renderer(monkeypatch, tmp_path):
    calls = []
    for name in ("_cover_ascii_horizon", "_cover_commit_rail"):
        monkeypatch.setattr(render, name, lambda *a, _n=name, **k: calls.append(_n), raising=True)
    out = tmp_path / "c.jpg"
    render.build_cover(out, "S", "2026-08-24", "t", style=render.COVER_STYLE_ASCII)
    render.build_cover(out, "S", "2026-08-24", "t", style=render.COVER_STYLE_COMMIT_RAIL)
    assert calls == ["_cover_ascii_horizon", "_cover_commit_rail"]


def test_commit_rail_renders_a_real_cover(tmp_path):
    # No importorskip: CI installs Pillow and fonts-dejavu-core (#164), so this
    # runs for real on Linux against the DejaVu fallback.
    from PIL import Image

    out = tmp_path / "fc.jpg"
    render._cover_commit_rail(
        out,
        "Frontier Commits",
        "2026-08-24",
        "Anthropic archives claude-quickstarts - Week of August 24, 2026",
    )
    assert out.exists() and out.stat().st_size > 10_000
    with Image.open(out) as im:
        assert im.size == (render.COVER_SIZE, render.COVER_SIZE)
        assert im.mode == "RGB"


# --- the seam between resolve_cover_date and the weekly renderer -----------
#
# Every test above hands _cover_commit_rail an ISO date directly. Production
# never does: _render calls build_cover(cover, show_name, resolve_cover_date(
# manifest), ...), and resolve_cover_date returns the DISPLAY form. The rail
# renderer then fed that to week_label, which strptimes ISO — so every weekly
# cover died with a ValueError, after a full episode of TTS had been spent.
# These tests exercise the composition rather than the pieces.


def test_commit_rail_renders_the_date_build_cover_is_actually_handed(tmp_path):
    manifest = {"date": "2026-08-31"}
    out = tmp_path / "rail.jpg"

    render.build_cover(
        out,
        "Frontier Commits",
        render.resolve_cover_date(manifest),
        "Pre-flight probe, codex, adk-python - Week of August 31, 2026",
        style=render.COVER_STYLE_COMMIT_RAIL,
    )

    assert out.exists() and out.stat().st_size > 0


def test_week_label_accepts_the_display_date_resolve_cover_date_returns():
    # The two forms must agree, or the cover prints one date and the headline
    # strip below looks for another.
    assert render.week_label("August 31, 2026") == "Week of August 31, 2026"
    resolved = render.resolve_cover_date({"date": "2026-08-31"})
    assert render.week_label(resolved) == render.week_label("2026-08-31")


def test_weekly_headline_strips_its_tail_given_the_display_date():
    # cover_headline_weekly exists to keep the tail out of the 96px type; fed the
    # date it is really given, it has to keep doing that.
    title = "Pre-flight probe, codex, adk-python - Week of August 31, 2026"
    assert render.cover_headline_weekly(title, "August 31, 2026") == (
        "Pre-flight probe, codex, adk-python"
    )
