#!/usr/bin/env python3
"""Regenerate this show's album art (`refs/cover.jpg`).

Nothing in a run calls this. The art is a COMMITTED asset (same posture as
Surface Tension's `refs/make_cover.py`): a render must not depend on a font or this
script to produce an episode cover, and this file exists so the next agent can
reproduce or adjust the art rather than reverse-engineering a binary.

    python3 skills/show-your-work/refs/make_cover.py

The motif is the show's name read literally: three lines of worked steps on faint
graph paper, the last one double-underlined as the answer, and a red-pen circle
around the middle step with a question mark beside it. The skeptic asking the
explainer to show its work. It keeps the sibling shows' family resemblance (dark
ground, Futura, the cortech.online footer) and departs on the accent: red-pen
coral, against the daily digest's amber, Frontier Commits' mint and Surface
Tension's aqua. It carries no lab's name, logo or brand mark (spec §1).

3000px square: the plan's size, and the top of what `render.py`'s
`check_cover_image` accepts (1400-3000, square). The layout is authored in the
1400px units the sibling covers use and scaled by `K`, so the two read alike.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 3000
K = SIZE / 1400  # the sibling covers' 1400px layout units -> this canvas
SS = 2  # supersample factor for the pen strokes and the rounded bars

GROUND = (14, 17, 30)  # #0e111e: ink-blue, not the siblings' slate or navy
GRID = (27, 32, 50)  # #1b2032: graph paper, visible but never louder than the work
CORAL = (255, 107, 91)  # #ff6b5b: red pen, this show's accent
INK = (236, 238, 244)  # #eceef4: the first title line
WORK = (86, 96, 128)  # #566080: the worked steps, deliberately recessive
MUTED = (128, 136, 160)  # #8088a0: tagline + footer

FUTURA = "/System/Library/Fonts/Supplemental/Futura.ttc"
MEDIUM, BOLD = 0, 2

MARGIN = 122
GRID_STEP = 50
FOOTER = "cortech.online"
TAGLINE = "alignment research, explained and questioned"

# Worked steps, in 1400 units: (y, left term width, right term width). Each row is
# `term = term`; the widths differ so the column reads as work, not a table.
ROWS = [(178, 250, 330), (298, 170, 420), (418, 220, 150)]
BAR_H = 34
EQ_X = MARGIN + 290  # the "=" column, shared so the steps align like real work
CIRCLED = 1  # the step the skeptic questions


def bar(d: ImageDraw.ImageDraw, x: float, y: float, w: float, s: float) -> None:
    d.rounded_rectangle(
        [x * s, (y - BAR_H / 2) * s, (x + w) * s, (y + BAR_H / 2) * s],
        radius=BAR_H / 2 * s,
        fill=WORK,
    )


def equals(d: ImageDraw.ImageDraw, x: float, y: float, s: float) -> None:
    for dy in (-11, 11):
        d.rounded_rectangle(
            [x * s, (y + dy - 5) * s, (x + 46) * s, (y + dy + 5) * s], radius=5 * s, fill=WORK
        )


def pen_loop(d: ImageDraw.ImageDraw, cx: float, cy: float, rx: float, ry: float, s: float):
    """A hand-drawn circle: slightly tilted, radius wobbling, and a bit more than one
    turn so the ends overshoot the way a pen stroke does."""
    pts = []
    tilt = math.radians(-4)
    for i in range(0, 400):
        t = 2 * math.pi * 1.1 * i / 399 + 0.6
        wob = 1 + 0.035 * math.sin(3 * t) + 0.02 * math.cos(5 * t)
        x, y = rx * wob * math.cos(t), ry * wob * math.sin(t)
        xr = x * math.cos(tilt) - y * math.sin(tilt)
        yr = x * math.sin(tilt) + y * math.cos(tilt)
        pts.append(((cx + xr) * s, (cy + yr) * s))
    d.line(pts, fill=CORAL, width=int(9 * s), joint="curve")


def draw_motif(d: ImageDraw.ImageDraw, s: float) -> None:
    """Everything in the top band, at scale `s` (1400 units -> supersampled px)."""
    for gx in range(0, 1400 + 1, GRID_STEP):
        d.line([(gx * s, 0), (gx * s, 1400 * s)], fill=GRID, width=max(1, int(1.2 * s)))
    for gy in range(0, 1400 + 1, GRID_STEP):
        d.line([(0, gy * s), (1400 * s, gy * s)], fill=GRID, width=max(1, int(1.2 * s)))

    for i, (y, left, right) in enumerate(ROWS):
        bar(d, EQ_X - 40 - left, y, left, s)
        equals(d, EQ_X, y, s)
        bar(d, EQ_X + 86, y, right, s)
        if i == len(ROWS) - 1:  # the answer: double-underlined
            x0, x1 = EQ_X + 76, EQ_X + 96 + right
            for dy in (40, 58):
                d.line(
                    [(x0 * s, (y + dy) * s), (x1 * s, (y + dy) * s)], fill=WORK, width=int(5 * s)
                )

    y, _, right = ROWS[CIRCLED]
    cx = EQ_X + 86 + right / 2
    pen_loop(d, cx, y, right / 2 + 30, 62, s)  # clears the "=" at EQ_X..EQ_X+46


def main() -> None:
    out = Path(__file__).resolve().parent / "cover.jpg"
    s = K * SS
    big = Image.new("RGB", (SIZE * SS, SIZE * SS), GROUND)
    draw_motif(ImageDraw.Draw(big), s)
    img = big.resize((SIZE, SIZE), Image.LANCZOS)

    d = ImageDraw.Draw(img)

    def font(px: float, weight: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(FUTURA, round(px * K), index=weight)

    def at(x: float, y: float) -> tuple[float, float]:
        return (x * K, y * K)

    y, _, right = ROWS[CIRCLED]
    d.text(at(EQ_X + 86 + right + 70, y - 96), "?", font=font(170, BOLD), fill=CORAL)

    # "SHOW YOUR" is wider than the siblings' first words: size the title so it
    # keeps the same margin on the right as on the left, capped at their 196.
    size = 196
    while d.textlength("SHOW YOUR", font=font(size, MEDIUM)) > (1400 - 2 * MARGIN) * K:
        size -= 2
    d.text(at(MARGIN, 648 + (196 - size) * 0.8), "SHOW YOUR", font=font(size, MEDIUM), fill=INK)
    d.text(at(MARGIN, 848), "WORK", font=font(size, BOLD), fill=CORAL)
    d.text(at(MARGIN, 1083), TAGLINE, font=font(54, MEDIUM), fill=MUTED)
    d.text(at(MARGIN, 1196), FOOTER, font=font(44, MEDIUM), fill=MUTED)

    img.save(out, "JPEG", quality=90, optimize=True)
    print(f"{out} ({img.width}x{img.height})")


if __name__ == "__main__":
    main()
