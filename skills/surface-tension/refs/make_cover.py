#!/usr/bin/env python3
"""Regenerate this show's album art (`refs/cover.jpg`, #177).

Nothing in a run calls this. The art is a COMMITTED asset — a render must not depend
on a font, a network fetch, or this script to produce an episode cover — and this
file exists so the next agent can reproduce or adjust it rather than reverse-
engineering a binary. Same posture as `refs/*.wav`: generated once, then locked.

    python3 skills/surface-tension/refs/make_cover.py

The motif is the show's name read literally: a waterline, four droplets of different
sizes resting on it, the meniscus dimpling under each, and one droplet that has
broken through and sunk. Four voices held in tension on one surface, until they
aren't. It keeps the sibling shows' family resemblance — dark ground, geometric
sans, the cortech.online footer — and departs on the accent (aqua, against Frontier
Commits' mint and the daily digest's amber) so three covers in one library are
never mistaken for each other.

Sized 1400px square: what `render.py`'s `check_cover_image` accepts (1400-3000,
square) and the floor Apple Podcasts and Spotify both require. A directory rejects
the whole FEED over bad art, not just the episode.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 1400
SS = 3  # supersample factor; the meniscus curve and the discs alias badly without it

GROUND = (9, 18, 26)  # #09121a — deeper and cooler than the daily show's #10141d
AQUA = (62, 214, 216)  # #3ed6d8 — this show's accent
INK = (232, 240, 244)  # #e8f0f4 — near-white for the first title word
SLATE = (52, 72, 86)  # #344856 — the submerged/among-the-crowd discs
MUTED = (120, 140, 152)  # #788c98 — tagline + footer
WATER = (33, 52, 65)  # #213441 — the body of water below the line

FUTURA = "/System/Library/Fonts/Supplemental/Futura.ttc"
MEDIUM, BOLD = 0, 2

MARGIN = 122
LINE_Y = 408  # the waterline
FOOTER = "cortech.online"
TAGLINE = "independent blogs, argued weekly"

# (centre x, radius, colour, sunk). Sizes deliberately unequal — a panel is not
# four identical voices — and the sunk one is what makes the surface a *tension*
# rather than a shelf.
DROPS = [
    (268, 72, AQUA, False),
    (486, 44, SLATE, False),
    (768, 99, AQUA, False),
    (1022, 35, SLATE, False),
    (1204, 58, SLATE, True),
]


def meniscus(x: float) -> float:
    """The waterline's y at x: flat, dipping under each resting droplet.

    A gaussian well per droplet, scaled to its radius — a bigger droplet pushes the
    surface down further and wider, which is the whole visual claim of the cover.
    """
    y = float(LINE_Y)
    for cx, r, _, sunk in DROPS:
        if sunk:
            continue
        y += 0.42 * r * math.exp(-(((x - cx) / (r * 1.9)) ** 2))
    return y


def draw(d: ImageDraw.ImageDraw, s: int) -> None:
    """Everything below/at the waterline. `s` scales the design to the supersampled
    canvas so one set of coordinates serves both."""
    # The body of water: a filled polygon under the meniscus, edge to edge.
    xs = [x for x in range(0, SIZE + 1, 2)]
    surface = [(x * s, meniscus(x) * s) for x in xs]
    d.polygon(surface + [(SIZE * s, SIZE * s), (0, SIZE * s)], fill=WATER)

    # The line itself, drawn over the fill so the curve stays crisp.
    d.line(surface, fill=AQUA, width=int(3.5 * s), joint="curve")

    for cx, r, colour, sunk in DROPS:
        cy = meniscus(cx) - r * 0.55 if not sunk else LINE_Y + r * 1.5
        box = [(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s]
        if sunk:
            # Outline only: it is under the surface, seen through the water.
            d.ellipse(box, outline=colour, width=int(3 * s))
        else:
            d.ellipse(box, fill=colour)


def main() -> None:
    out = Path(__file__).resolve().parent / "cover.jpg"
    big = Image.new("RGB", (SIZE * SS, SIZE * SS), GROUND)
    draw(ImageDraw.Draw(big), SS)
    img = big.resize((SIZE, SIZE), Image.LANCZOS)

    d = ImageDraw.Draw(img)
    title = ImageFont.truetype(FUTURA, 196, index=MEDIUM)
    accent = ImageFont.truetype(FUTURA, 196, index=BOLD)
    tag = ImageFont.truetype(FUTURA, 54, index=MEDIUM)
    foot = ImageFont.truetype(FUTURA, 44, index=MEDIUM)

    d.text((MARGIN, 648), "SURFACE", font=title, fill=INK)
    d.text((MARGIN, 848), "TENSION", font=accent, fill=AQUA)
    d.text((MARGIN, 1083), TAGLINE, font=tag, fill=MUTED)
    d.text((MARGIN, 1196), FOOTER, font=foot, fill=MUTED)

    img.save(out, "JPEG", quality=92, optimize=True)
    print(f"{out} ({img.width}x{img.height})")


if __name__ == "__main__":
    main()
