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
