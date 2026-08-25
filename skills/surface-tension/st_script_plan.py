"""Week-seeded assignment layer for Surface Tension.

Variety is ASSIGNED, never requested — the panel-show analogue of the daily
show's date-seeded rotation (orchestrate.py) and of Frontier Commits' weekly one
(fc_script_plan.py). Each scene is written by an isolated `claude -p` holding one
post body that cannot see its neighbours to differ from them, so nothing about
an episode's variety can be negotiated between writers. It is handed to them:
the week decides which voice holds which role, which two voices argue which
side, who opens and who gets the last word, and which voice runs each bit.

Four things are assigned, per the design spec's section 4.3:

1. Role -> voice.  Voices are fixed (`ref_audio` stability, docs/durable-voices.md);
   roles rotate across a Latin square. Five roles over four voices, so one role
   sits out each scene — which is itself a rotation axis, not an accident.
2. Stance pairs.  Per post, who argues for and who against, on a bank cycle of a
   different length from the role rotation so the two axes never lock together.
3. Turn order and the last word.  Read off the role square from a different row,
   for the same reason.
4. Bit ownership.  Which voice runs the vote desk and which runs the rapid-fire.

Pure module: no fc_common, no network, no LLM, no filesystem state, no IO beyond
the `plan` CLI's single JSON print, and no wall-clock reads — the --date argument
is the only clock.

Soundboard slot assignment (spec section 4.7) is Phase 4 and deliberately absent:
it belongs on the scene entries here, subject to the same rationing discipline as
everything else, but a stinger seam filled before the format is proven is the
byte-identical opening sentence all over again.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

# `week_index` is imported from the Frontier Commits module rather than copied:
# the year*53+week trap (a step of 2 at 52-week ISO year ends, which silently
# skips a rotation row) is already solved and documented there, and a second
# copy is a second chance to reintroduce it. The skills ship as flat directories,
# so the sibling is reached by path exactly as the tests' conftest reaches both.
_FC_SKILL_DIR = Path(__file__).resolve().parent.parent / "frontier-commits"
if str(_FC_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_FC_SKILL_DIR))

from fc_script_plan import week_index  # noqa: E402  (must follow the sys.path insert)

# Strict YYYY-MM-DD. The regex gate matters: 3.11+ fromisoformat also accepts
# compact forms like 20260831, which would vary the plan by Python version.
_DATE_ONLY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# The cast. Fixed for the life of the show — Phase 1 ships on these four bundled
# presets and Phase 3 swaps them for recorded ref_audio clips, which is a
# manifest change and a cache invalidation, nothing more. This module permutes
# ROLES across the cast; it never permutes the cast. Order is load-bearing:
# STANCE_ORDERS_ST indexes into it.
VOICES_ST = ("Ryan", "Aiden", "Ethan", "Chelsie")

# Five roles over four voices. Order is load-bearing: ROLE_ORDERS_ST indexes
# into this bank, so inserting or reordering entries reshuffles every future
# episode. `turns` is the role's turn budget for one scene.
ROLES_ST = {
    "anchor": {
        "does": "Sets up the post, owns the running order, hands off.",
        "turns": (2, 3),
    },
    "advocate": {
        "does": "Steelmans the post - why the blogger is right.",
        "turns": (2, 2),
    },
    "skeptic": {
        "does": "Names what is unsupported. Assigned, not felt.",
        "turns": (2, 2),
    },
    "tangent": {
        "does": "Takes the honest swerve - the topical-width engine.",
        "turns": (1, 2),
    },
    "switchboard": {
        "does": "Takes the calls - introduces each caller, reacts, cuts them off.",
        "turns": (0, 2),
    },
}

# Roles the DATA can veto after the plan is made. The plan proposes a slot; a
# scene with no callers renders no turn in it. This is the same mechanical
# honesty as the textless `cold` transition move in fc_script_plan and the
# "never manufacture a connection" rule: a fabricated caller is strictly worse
# than a dead phone line, because it is unfalsifiable on air. `build_plan`
# precomputes the no-caller ordering for every scene precisely so a writer
# handed an empty comment list is never left resolving it by hand — the
# temptation to invent a caller has to be designed out, not forbidden in prose.
CONDITIONAL_ROLES_ST = ("switchboard",)

SWITCHBOARD_CONDITION = (
    "Renders turns ONLY if this post has at least one real Fediverse comment to "
    "quote verbatim. With no comments there is no call and no switchboard turn: "
    "use the no_callers ordering. Never invent a caller to fill this slot."
)

# The recurring bits, one owner each per week. Order is load-bearing:
# STANCE_ORDERS_ST indexes into this bank too.
BITS_ST = {
    "vote_desk": "Runs the week's vote numbers and defends one indefensible ranking.",
    "rapid_fire": "Runs the post-bumper rapid-fire: six takes on six unvoted posts.",
}

# Row = a panel; column = a role; values index the CHAIRS — VOICES_ST by
# position, and the last chair (index len(VOICES_ST)) is the role that sits out.
#
# FIXED DATA — do NOT regenerate or "improve" it, and do NOT replace it with
# arithmetic. The daily show's stride formula passed a year-long coverage test
# while pinning positions 4, 9 and 14 to a single shape for four days at a time,
# because `(1 + p) % 5 == 0` cancelled the day-varying term (PR #108); a smoke
# test caught it, the unit tests had not. This table was machine-searched and
# verified for every property the tests lock: Latin rows AND columns, and 5
# pairwise-distinct rotation signatures (so panels genuinely vary rather than
# being rotations of one another — the property the stride bug faked).
ROLE_ORDERS_ST = (
    (2, 1, 0, 4, 3),
    (3, 0, 4, 2, 1),
    (4, 3, 1, 0, 2),
    (0, 2, 3, 1, 4),
    (1, 4, 2, 3, 0),
)

# Row = a week's stance table; column = post position; values index VOICES_ST.
# Same fixed-data rules, same machine verification, sized to the cast rather
# than the role bank. The two squares having COPRIME sizes is what decouples the
# stance axis from the role axis: a panel recurs every 5 weeks and a stance
# table every 4, so the pairing only repeats after 20 — each of the five times a
# given panel comes back, it argues from a different table.
STANCE_ORDERS_ST = (
    (1, 3, 2, 0),
    (0, 1, 3, 2),
    (2, 0, 1, 3),
    (3, 2, 0, 1),
)

# Turn order walks the SAME role square as the role assignment, from a different
# row. On the same row a given panel would always speak in one order, collapsing
# two independent axes into one — the TRANSITION_ROW_OFFSET_W lesson.
#
# Unlike that constant, this VALUE is load-bearing and not merely non-zero: the
# sat-out role is dropped from the ordering, so the offset decides which roles
# survive at the ends of it. At offset 2 this table never lets the anchor or the
# advocate close a scene, and at 1 or 4 two roles can never open one — "the
# anchor never gets the last word" is exactly the calcification this module
# exists to prevent, and it is invisible by inspection. The two coverage tests
# (`test_every_role_opens_a_scene_within_one_bank_cycle` and
# `..._gets_the_last_word_...`) pin it; re-verify them before changing either
# this offset or ROLE_ORDERS_ST.
TURN_ROW_OFFSET_ST = 3

# The two stance sides read the SAME column of two DIFFERENT rows. Because the
# square is Latin, distinct rows disagree in every column — which is what makes
# "the for-voice is never also the against-voice" a property of the table rather
# than a runtime check. Must be non-zero mod len(STANCE_ORDERS_ST).
STANCE_SIDE_OFFSET_ST = 2

# Bit ownership reads the stance square from a third row, so the week's vote-desk
# host is not mechanically the week's for-voice on post 0.
BIT_ROW_OFFSET_ST = 1


def _role_row(week: int, scene: int) -> int:
    """Index of the ROLE_ORDERS_ST row that casts scene `scene` of week `week`.

    The square doubles as its own row selector — `ROLE_ORDERS_ST[week][scene]` —
    which is what gives the scene axis its own rotation instead of a cyclic
    shift of the week's. It is sound for exactly one reason, and it is the same
    reason everywhere else in this module: a Latin square's columns each hold
    every symbol once, so consecutive weeks at a fixed scene always select
    DIFFERENT rows (no voice holds a role two weeks running), five consecutive
    weeks select ALL FIVE rows (every voice sees every role), and the five
    scenes of one episode likewise select all five (the anchor of scene 0 is not
    the anchor of every scene). Scenes past the bank size wrap.
    """
    n = len(ROLE_ORDERS_ST)
    return ROLE_ORDERS_ST[week % n][scene % n]


def _turn_row(week: int, scene: int) -> int:
    """Index of the ROLE_ORDERS_ST row that orders the turns in that scene."""
    return (_role_row(week, scene) + TURN_ROW_OFFSET_ST) % len(ROLE_ORDERS_ST)


def _stance_row(week: int) -> int:
    """Index of the STANCE_ORDERS_ST row holding this week's for-side."""
    return week % len(STANCE_ORDERS_ST)


def scene_roles(week: int, scene: int) -> dict[str, str | None]:
    """Map every role to the voice holding it in this scene, or to None for the
    one role that sits out. Every voice is cast exactly once."""
    chairs = ROLE_ORDERS_ST[_role_row(week, scene)]
    return {
        role: (VOICES_ST[chair] if chair < len(VOICES_ST) else None)
        for role, chair in zip(ROLES_ST, chairs, strict=True)
    }


def sits_out(week: int, scene: int) -> str:
    """Name the one role with no voice in this scene."""
    chairs = ROLE_ORDERS_ST[_role_row(week, scene)]
    return list(ROLES_ST)[chairs.index(len(VOICES_ST))]


def scene_stance(week: int, pos: int) -> dict[str, str]:
    """Name the voice arguing FOR the post at `pos` and the voice arguing
    AGAINST it. Assigned, not felt — the friction is real because nobody chose
    their side. Positions past the cast size wrap."""
    n = len(STANCE_ORDERS_ST)
    column = pos % n
    for_row = STANCE_ORDERS_ST[_stance_row(week)]
    against_row = STANCE_ORDERS_ST[(_stance_row(week) + STANCE_SIDE_OFFSET_ST) % n]
    return {
        "for": VOICES_ST[for_row[column]],
        "against": VOICES_ST[against_row[column]],
    }


def scene_turn_order(week: int, scene: int) -> list[str]:
    """Order the roles that speak in this scene. The role that sits out is
    dropped; the conditional switchboard is NOT — the data decides that later."""
    names = list(ROLES_ST)
    out = sits_out(week, scene)
    order = [names[i] for i in ROLE_ORDERS_ST[_turn_row(week, scene)]]
    return [role for role in order if role != out]


def bit_owners(week: int) -> dict[str, str]:
    """Name the voice running each recurring bit this week. One row of the
    stance square is a permutation of the cast, so the two bits never collide."""
    n = len(STANCE_ORDERS_ST)
    row = STANCE_ORDERS_ST[(_stance_row(week) + BIT_ROW_OFFSET_ST) % n]
    return {bit: VOICES_ST[row[i]] for i, bit in enumerate(BITS_ST)}


def _turn_entry(role: str, voice: str | None) -> dict:
    """One self-contained instruction for the writer: who, doing what, for how
    many turns, and whether the data can veto the slot."""
    entry = {
        "role": role,
        "voice": voice,
        "does": ROLES_ST[role]["does"],
        "turns": list(ROLES_ST[role]["turns"]),
        "conditional": role in CONDITIONAL_ROLES_ST,
    }
    if entry["conditional"]:
        entry["condition"] = SWITCHBOARD_CONDITION
    return entry


def build_scene(week: int, pos: int) -> dict:
    """Assemble one post scene: the panel, the sat-out role, the stance pair,
    the turn order, and the ordering that survives a post with no callers."""
    roles = scene_roles(week, pos)
    order = scene_turn_order(week, pos)
    fallback = [role for role in order if role not in CONDITIONAL_ROLES_ST]
    return {
        "pos": pos,
        "roles": roles,
        "sits_out": sits_out(week, pos),
        "stance": scene_stance(week, pos),
        "turn_order": [_turn_entry(role, roles[role]) for role in order],
        "opens": order[0],
        "last_word": order[-1],
        # Precomputed rather than left to the writer: handed only the full
        # ordering, a writer with no comments to quote has to decide who takes
        # the freed slot, and inventing a caller is the easier answer.
        "no_callers": {
            "turn_order": fallback,
            "opens": fallback[0],
            "last_word": fallback[-1],
        },
    }


def build_plan(date_iso: str, n_posts: int) -> dict:
    """Assemble the week's full assignment: bit ownership plus one entry per
    post scene. Deterministic in (date_iso, n_posts) — the fixed JSON contract
    the weekly run consumes, and what makes a resumed or repeated run rebuild
    the same episode."""
    week = week_index(date_iso)
    owners = bit_owners(week)
    return {
        "week_row": week % len(ROLE_ORDERS_ST),
        "cast": list(VOICES_ST),
        "bits": {bit: {"voice": owners[bit], "does": text} for bit, text in BITS_ST.items()},
        "scenes": [build_scene(week, i) for i in range(n_posts)],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Week-seeded assignment plan for Surface Tension")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ap_plan = sub.add_parser("plan", help="print the week's assignment plan as one JSON object")
    ap_plan.add_argument("--date", required=True, help="run date YYYY-MM-DD (the only clock)")
    ap_plan.add_argument("--posts", required=True, type=int, help="number of post scenes")
    args = ap.parse_args(argv)

    if not _DATE_ONLY_RE.fullmatch(args.date):
        ap.error(f"--date must be YYYY-MM-DD, got {args.date!r}")
    try:
        dt.date.fromisoformat(args.date)
    except ValueError:
        ap.error(f"--date must be a real date, got {args.date!r}")
    if args.posts < 1:
        ap.error(f"--posts must be at least 1, got {args.posts}")
    print(json.dumps(build_plan(args.date, args.posts)))  # the FINAL stdout line
    return 0


if __name__ == "__main__":
    sys.exit(main())
