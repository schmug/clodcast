"""Week-seeded script rotation for Frontier Commits.

Variety is ASSIGNED, never requested — the weekly analogue of the daily show's
date-seeded rotation (orchestrate.py). Each story segment is written in its own
subagent context that cannot see its neighbours to differ from them, so the
week picks the cold open, the sign-off, every segment's shape, and every segue
move, deterministically: a re-run of the same week rebuilds the same episode.

Pure module: no fc_common, no IO beyond the `plan` CLI's single JSON print, and
no wall-clock reads — the --date argument is the only clock.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys

# Strict YYYY-MM-DD. The regex gate matters: 3.11+ fromisoformat also accepts
# compact forms like 20260831, which would vary the plan by Python version.
_DATE_ONLY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def week_index(date_iso: str) -> int:
    """Contiguous week counter: the ISO week's Monday ordinal // 7. Consecutive
    real-world weeks give consecutive integers across EVERY year boundary
    (52- and 53-week ISO years alike) — a multiplier formula like y*53+w steps
    by 2 at 52-week year ends, silently skipping a rotation row."""
    d = dt.date.fromisoformat(date_iso)
    monday = d - dt.timedelta(days=d.isoweekday() - 1)
    return monday.toordinal() // 7


# Named cold opens. Order is load-bearing: build_plan indexes `list(INTRO_MODES_W)`
# by `week % 5`, so inserting or reordering entries reshuffles every future episode.
INTRO_MODES_W = {
    "ledger": (
        "Open with the week's ledger: \"Week of <date>. <N> stories across "
        '<the labs involved>." Then the single most telling number of the week.'
    ),
    "headline": (
        "Open cold on the week's biggest story in one sentence, then pull back: "
        "date, story count, rundown."
    ),
    "question": (
        "Open with the question this week's activity raises, then the date and the rundown."
    ),
    "pattern": (
        "Open by naming a pattern that honestly connects two or more of this "
        "week's stories, then the rundown. No honest pattern? Use the ledger "
        "opening instead — never manufacture one."
    ),
    "time-capsule": (
        "Open by contrasting this week with a specific earlier state "
        '("A month ago this org was quiet - this week..."), then the rundown.'
    ),
}

OUTRO_MODES_W = {
    "plain": "Plain sign-off: a simple thanks. No new content.",
    "watchlist": (
        "Name one or two repos to watch next week and the observable that "
        "would settle the question. Then sign off."
    ),
    "callback": "Close by paying off the cold open in one line, then sign off. No new facts.",
}

# Named openings for story segments. Order is load-bearing: SHAPE_ORDERS_W
# indexes into this bank.
STORY_SHAPES_W = {
    "artifact-first": (
        "Open with the concrete thing that appeared - the repo, what is "
        "actually in it - then what it might mean."
    ),
    "question-first": (
        "Open with the question this repo's existence raises, then walk "
        "the evidence toward the best available answer."
    ),
    "timeline": (
        "Walk the observable sequence in order - created, pushed, released, "
        "went quiet - then read the trajectory."
    ),
    "cross-lab": (
        "Open by placing this against another lab's position in the same space, "
        "then the specifics. Only when the comparison is real."
    ),
    "numbers-first": (
        "Open with the most telling number - stars, days since a push, "
        "repo counts - then the story behind it."
    ),
    "zoom-out": (
        "Open one level up - what this kind of repo says about where the lab "
        "is headed - then drop into the specifics."
    ),
}

# What a segue DOES, not a phrase to say. `cold` carries no text at all — that is
# what makes "not every segment needs a segue" mechanical instead of a hope. The
# prose rule that outranks all of these: never manufacture a connection — adjacent
# stories are often unrelated, and a false link reads worse than a blunt hand-off.
MOVES_W = {
    "cold": "",
    "pivot": "Name the change of subject in a few words, then go.",
    "echo": "Mark that this story rhymes with the previous one: same pattern, new lab.",
    "contrast": "Mark the opposition to the previous story in one clause, then go.",
    "escalate": "Frame this story as raising the stakes of the previous one.",
    "zoom": (
        "Shift altitude from the previous story - from one repo to the big picture, or back down."
    ),
}

# Row = the week's ordering, column = segment position; values index STORY_SHAPES_W.
# FIXED DATA — do NOT regenerate or "improve" it, and do NOT replace it with
# arithmetic. The daily show's stride formula passed a year-long coverage test
# while pinning positions to a single shape for days at a time (PR #108); this
# table was machine-generated and verified for all three properties the tests
# lock: Latin rows AND columns, cyclic consecutive-row disagreement in every
# column, and 6 pairwise-distinct rotation signatures (so adjacencies genuinely
# vary — the property the stride bug faked).
SHAPE_ORDERS_W = (
    (3, 1, 2, 4, 0, 5),
    (4, 3, 0, 1, 5, 2),
    (1, 0, 5, 2, 3, 4),
    (0, 4, 1, 5, 2, 3),
    (5, 2, 4, 3, 1, 0),
    (2, 5, 3, 0, 4, 1),
)

# Segues walk the SAME Latin square as shapes, from a different row. On the same
# row a given shape would carry the same segue forever, collapsing two independent
# axes into one. Any non-zero offset works; `test_transitions_are_not_locked_to_
# the_shape_rotation` locks the decoupling rather than this particular value.
TRANSITION_ROW_OFFSET_W = 3

# Length rhythm (character bands). A uniform band makes every chapter the same
# size; a lead read, bodies, and a short trend-watch close give the episode a
# pulse. TREND_BAND belongs to the fixed non-story close only — band_for never
# returns it.
LEAD_BAND = (1100, 1500)
BODY_BAND = (700, 1100)
TREND_BAND = (450, 700)


def _latin_pick(bank: dict, week: int, position: int, row_offset: int = 0) -> str:
    """Read one name out of `bank` using the week's row of SHAPE_ORDERS_W.
    Positions past the bank size wrap, reusing the week's ordering."""
    names = list(bank)
    order = SHAPE_ORDERS_W[(week + row_offset) % len(SHAPE_ORDERS_W)]
    return names[order[position % len(order)]]


def segment_shape(week: int, pos: int) -> str:
    """Name the shape for the story segment at `pos` in this week's episode."""
    return _latin_pick(STORY_SHAPES_W, week, pos)


def segment_transition(week: int, junction: int) -> str:
    """Name the move for the segue INTO the story at `junction` — the gap before
    story j, for j >= 1. Junction 0 is the lead: it follows the cold open and
    needs no bridge, so it is always the (textless) "cold" move."""
    if junction == 0:
        return "cold"
    return _latin_pick(MOVES_W, week, junction - 1, TRANSITION_ROW_OFFSET_W)


def band_for(pos: int, n_stories: int) -> tuple[int, int]:
    """Character band for the story at `pos`: the lead gets the long read, every
    other story is a body. The trend-watch close is not a story position and
    always uses TREND_BAND directly."""
    return LEAD_BAND if pos == 0 else BODY_BAND


def build_plan(date_iso: str, n_stories: int) -> dict:
    """Assemble the week's full script plan: assigned intro, outro, and one
    entry per story segment (shape + segue move + length band). Deterministic
    in (date_iso, n_stories) — the fixed JSON contract the weekly run consumes."""
    week = week_index(date_iso)
    intro = list(INTRO_MODES_W)[week % len(INTRO_MODES_W)]
    outro = list(OUTRO_MODES_W)[week % len(OUTRO_MODES_W)]
    segments = []
    for i in range(n_stories):
        shape = segment_shape(week, i)
        move = "cold" if i == 0 else segment_transition(week, i)
        segments.append(
            {
                "pos": i,
                "shape": shape,
                "shape_text": STORY_SHAPES_W[shape],
                "move": move,
                "move_text": MOVES_W[move],
                "band": list(band_for(i, n_stories)),
            }
        )
    return {
        "week_row": week % len(SHAPE_ORDERS_W),
        "intro_mode": intro,
        "intro_text": INTRO_MODES_W[intro],
        "outro_mode": outro,
        "outro_text": OUTRO_MODES_W[outro],
        "segments": segments,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Week-seeded script plan for Frontier Commits")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ap_plan = sub.add_parser("plan", help="print the week's script plan as one JSON object")
    ap_plan.add_argument("--date", required=True, help="run date YYYY-MM-DD (the only clock)")
    ap_plan.add_argument("--stories", required=True, type=int, help="number of story segments")
    args = ap.parse_args(argv)

    if not _DATE_ONLY_RE.fullmatch(args.date):
        ap.error(f"--date must be YYYY-MM-DD, got {args.date!r}")
    try:
        dt.date.fromisoformat(args.date)
    except ValueError:
        ap.error(f"--date must be a real date, got {args.date!r}")
    if args.stories < 1:
        ap.error(f"--stories must be at least 1, got {args.stories}")
    print(json.dumps(build_plan(args.date, args.stories)))  # the FINAL stdout line
    return 0


if __name__ == "__main__":
    sys.exit(main())
