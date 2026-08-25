"""Week-seeded assignment layer for Surface Tension.

Variety is ASSIGNED, never requested — the panel-show descendant of the daily
show's `SHAPE_ORDERS` and Frontier Commits' `fc_script_plan`. Each scene is
written by an isolated `claude -p` that cannot see its neighbours, so nothing
about an episode's variety can be negotiated between writers; it is handed to
them from outside. Telling a model to "vary the panel" regresses to the mean —
the daily show shipped 76 episodes with a byte-identical opening sentence before
that lesson landed.

Four things are assigned here, per design spec 4.3:

1. Role -> voice. The cast is FIXED (`ref_audio` stability, docs/durable-voices.md);
   the five roles rotate across it, one sitting out each scene.
2. Stance pairs. Which voice argues for the post and which against, decided
   INDEPENDENTLY of the role rotation — the `TRANSITION_ROW_OFFSET_W` lesson.
3. Turn order and the last word.
4. Bit ownership: who runs the vote desk and the rapid fire this week.

Pure module: no config, no network, no LLM, no IO beyond the `plan` CLI's single
JSON print, and no wall-clock reads — the --date argument is the only clock.

Phase 4 seam (spec 4.7): the soundboard's stinger slots get assigned here too,
by the same discipline ("some slots are assigned nothing" — the audio analogue
of the textless `cold` transition move). Deliberately NOT built yet; the format
has to prove out first. Scene shape/mode banks (the INTRO_MODES_W/OUTRO_MODES_W
analogue) belong to the write layer and its SKILL.md drift test, not here.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

# Frontier Commits and Surface Tension ship as sibling flat directories under one
# plugin root, so a CLI invoked by path only has its OWN directory on sys.path.
# week_index is BORROWED rather than re-derived: the y*53+w trap (a step of 2 at
# 52-week ISO year ends, silently skipping a rotation row) is solved exactly once,
# over there, and a second copy is how it comes back.
_FC_DIR = Path(__file__).resolve().parent.parent / "frontier-commits"
if str(_FC_DIR) not in sys.path:
    sys.path.insert(0, str(_FC_DIR))

from fc_script_plan import week_index  # noqa: E402  (must follow the sys.path insert)

# Strict YYYY-MM-DD. The regex gate matters: 3.11+ fromisoformat also accepts
# compact forms like 20260831, which would vary the plan by Python version.
_DATE_ONLY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# The cast. Spec 4.8: four bundled presets now, four recorded ref_audio clips in
# Phase 3 — a manifest change and a cache invalidation, nothing here. Order is
# load-bearing: VOICE_ORDERS_W and ROLE_ORDERS_W index into this tuple, so
# reordering it reshuffles every future episode. The daily show's house voice is
# deliberately NOT among them: reusing it would make the digest's narrator a
# panelist on a different show, and the digest's identity is worth more.
CAST = ("Ryan", "Aiden", "Ethan", "Chelsie")

# Five roles over four voices — one sits out every scene, and which one is itself
# a rotation axis (ROLE_ORDERS_W's last column). Order is load-bearing:
# ROLE_ORDERS_W indexes into this bank.
ROLES = {
    "anchor": {
        "does": "Sets up the post, owns the running order, hands off.",
        "turns": (2, 3),
        "conditional": False,
    },
    "advocate": {
        "does": "Steelmans the post - why the blogger is right.",
        "turns": (2, 2),
        "conditional": False,
    },
    "skeptic": {
        "does": "Names what is unsupported. Assigned, not felt.",
        "turns": (2, 2),
        "conditional": False,
    },
    "tangent": {
        "does": "Takes the honest swerve - the topical-width engine.",
        "turns": (1, 2),
        "conditional": False,
    },
    # The discussion desk (spec 4.4, amended by the 2026-08-25 recon).
    # /feed/comments carries no comment bodies, so this seat reports VOLUME and
    # PROVENANCE only: how many calls, which instance HOST (never the handle),
    # when they came. It never characterizes what anyone said.
    "switchboard": {
        "does": (
            "Works the board: how many comments the post drew, from which "
            "instance hosts, and when - never who, never what they said."
        ),
        "turns": (0, 2),
        "conditional": True,
    },
}

# What must be true for the conditional seat to render at all, and what happens
# when it isn't. The plan PROPOSES the slot; the data disposes. A fabricated
# caller is strictly worse than a dead phone line, because it is unfalsifiable on
# air — so the drop rule travels with the slot rather than living in prose the
# writer may not be looking at.
SWITCHBOARD_CONDITION = (
    "the post's own comment count (slash:comments) is greater than zero - no "
    "second fetch is needed to know this"
)
SWITCHBOARD_IF_ABSENT = (
    "drop this turn entirely and give its time to the panel; never invent a "
    "caller, a handle, a quote, or a count"
)

# Non-role speaking parts, used by the four frame scenes. Kept in their own bank
# so the roles table above stays exactly the five the spec names.
FRAME_PARTS = {
    "arguing-for": ("Already mid-argument, holding the week's lead post up.", (1, 2)),
    "arguing-against": ("Already mid-argument, taking it apart.", (1, 2)),
    "desk": ("Reads the week's vote numbers straight.", (2, 3)),
    "foil": ("Disputes exactly one ranking, and says why.", (1, 2)),
    "bumper": ("Calls the rapid fire in, then takes the first post.", (1, 2)),
    "take": ("One take on one unvoted post. No follow-up.", (1, 2)),
    "sign-off": ("Closes the show.", (1, 2)),
    "button": ("One last line after the sign-off.", (1, 1)),
}

# Row = the week's ordering; columns 0..3 = the four voices; column 4 = the role
# that sits out. Values index ROLES.
#
# FIXED DATA — do NOT regenerate it, and do NOT replace it with arithmetic. The
# daily show's stride formula passed a year-long coverage test while pinning
# positions 4/9/14 to one shape for four days at a time (PR #108). This table was
# machine-generated and verified for every property the tests lock: Latin rows
# AND columns (so no voice repeats a role between consecutive weeks, and every
# voice sees every role within one bank cycle), 5 pairwise-distinct rotation
# signatures, and no row a positional rotation of another (the half of the stride
# lesson a value-space check misses).
ROLE_ORDERS_W = (
    (3, 0, 1, 2, 4),
    (0, 1, 4, 3, 2),
    (1, 4, 2, 0, 3),
    (2, 3, 0, 4, 1),
    (4, 2, 3, 1, 0),
)

# The same construction over the four VOICES, used for every axis that orders or
# picks voices rather than roles: turn order, stance pairs, bit ownership. Values
# index CAST. Same fixed-data rule, same verified properties.
VOICE_ORDERS_W = (
    (2, 1, 0, 3),
    (3, 0, 1, 2),
    (1, 3, 2, 0),
    (0, 2, 3, 1),
)

# Three axes read VOICE_ORDERS_W, and they must not read the same row: on one row
# the week's opener would also be its for-voice and its vote-desk owner forever,
# collapsing three independent axes into one. This is the TRANSITION_ROW_OFFSET_W
# trick. The decoupling is what the tests lock, not these particular values.
TURN_ROW_OFFSET_W = 1
STANCE_ROW_OFFSET_W = 2
BIT_ROW_OFFSET_W = 3

# Full key sets, RUN_LOG_FIELDS-style: every scene and every turn carries every
# key, with null where a value does not apply, so a consumer can index blindly
# rather than probing for absence.
SCENE_FIELDS = (
    "index",
    "kind",
    "post_index",
    "bench",
    "stances",
    "stance_note",
    "opener",
    "last_word",
    "turns",
)
TURN_FIELDS = (
    "order",
    "voice",
    "role",
    "part",
    "does",
    "turns",
    "stance",
    "conditional",
    "condition",
    "if_absent",
)


def role_row_index(week: int, post: int) -> int:
    """Which ROLE_ORDERS_W row this week's post at `post` reads. Advancing with
    the post as well as the week is what rotates the bench WITHIN an episode."""
    return (week + post) % len(ROLE_ORDERS_W)


def stance_row_index(week: int, post: int) -> int:
    """Which VOICE_ORDERS_W row supplies this post's stance pair. Its row walks a
    4-cycle against the roles' 5-cycle, so the two axes only realign every 20
    weeks — a for-voice is never welded to the advocate's seat."""
    return (week + post + STANCE_ROW_OFFSET_W) % len(VOICE_ORDERS_W)


def seat_roles(week: int, post: int) -> dict[str, str]:
    """voice -> role for the post at `post`. Exactly four of the five roles are
    seated; `benched_role` names the fifth."""
    row = ROLE_ORDERS_W[role_row_index(week, post)]
    return {CAST[i]: list(ROLES)[row[i]] for i in range(len(CAST))}


def benched_role(week: int, post: int) -> str:
    """The role that sits out this scene — ROLE_ORDERS_W's fifth column, which is
    Latin like every other, so the bench never repeats two weeks running and every
    role sits out exactly once per bank cycle."""
    return list(ROLES)[ROLE_ORDERS_W[role_row_index(week, post)][len(CAST)]]


def _voice_row(week: int, post: int, offset: int) -> list[str]:
    return [CAST[i] for i in VOICE_ORDERS_W[(week + post + offset) % len(VOICE_ORDERS_W)]]


def _turn(order: int, voice: str, *, role=None, part, does, turns, stance=None) -> dict:
    conditional = bool(role and ROLES[role]["conditional"])
    return {
        "order": order,
        "voice": voice,
        "role": role,
        "part": part,
        "does": does,
        "turns": list(turns),
        "stance": stance,
        "conditional": conditional,
        "condition": SWITCHBOARD_CONDITION if conditional else None,
        "if_absent": SWITCHBOARD_IF_ABSENT if conditional else None,
    }


def _scene(
    index: int,
    kind: str,
    turns: list[dict],
    *,
    post_index=None,
    bench=None,
    stances=None,
    stance_note=None,
) -> dict:
    return {
        "index": index,
        "kind": kind,
        "post_index": post_index,
        "bench": bench,
        "stances": stances,
        "stance_note": stance_note,
        "opener": turns[0]["voice"],
        "last_word": turns[-1]["voice"],
        "turns": turns,
    }


def stance_pair(week: int, post: int) -> tuple[str, str]:
    """(for_voice, against_voice) for this post, drawn from the voices whose turn
    is unconditional. A stance handed to the switchboard would be an argument that
    can vanish with the comment count, so that seat is filtered out first."""
    seated = seat_roles(week, post)
    pool = [v for v in _voice_row(week, post, STANCE_ROW_OFFSET_W) if seated[v] != "switchboard"]
    return pool[0], pool[1]


def _stance_note(seated: dict[str, str], for_voice: str, against_voice: str) -> str:
    """How this week's stance lands against the seats it fell on. Roles are desk
    functions; stances are per-post debate assignments, and they are decided
    separately — so `crossed` (the skeptic having to make the case) is a feature
    of the design, not a collision to be smoothed away."""
    if seated[for_voice] == "advocate" and seated[against_voice] == "skeptic":
        return "aligned"
    if seated[for_voice] == "skeptic" or seated[against_voice] == "advocate":
        return "crossed"
    return "open"


def turn_order(week: int, post: int) -> list[str]:
    """The scene's speaking order. The conditional switchboard voice is pulled out
    of the raw row and re-inserted at an interior slot, so it is never the opener
    and never the last word: dropping it on a post with no comments must leave a
    scene that still opens and still closes."""
    seated = seat_roles(week, post)
    row = _voice_row(week, post, TURN_ROW_OFFSET_W)
    desk = next((v for v in row if seated[v] == "switchboard"), None)
    rest = [v for v in row if v != desk]
    if desk is None:
        return rest
    # 1 .. len(rest)-1 — never index 0, and never the final index (len(rest)).
    at = 1 + ((week + post) % (len(rest) - 1))
    return rest[:at] + [desk] + rest[at:]


def post_scene(week: int, post: int, *, index: int, kind: str) -> dict:
    """One post-backed scene: seats, stance pair, turn order, last word."""
    seated = seat_roles(week, post)
    for_voice, against_voice = stance_pair(week, post)
    stance_of = {for_voice: "for", against_voice: "against"}
    turns = [
        _turn(
            i,
            voice,
            role=seated[voice],
            part=seated[voice],
            does=ROLES[seated[voice]]["does"],
            turns=ROLES[seated[voice]]["turns"],
            stance=stance_of.get(voice),
        )
        for i, voice in enumerate(turn_order(week, post))
    ]
    return _scene(
        index,
        kind,
        turns,
        post_index=post,
        bench=benched_role(week, post),
        stances={"for": for_voice, "against": against_voice},
        stance_note=_stance_note(seated, for_voice, against_voice),
    )


def open_lines_post_index(week: int, n_posts: int) -> int:
    """Which post slot carries the open-lines scene (spec 5, scene 6).

    Normally the last one. But that scene is BUILT around the discussion desk, and
    one week in five the rotation benches `switchboard` there — so the slot walks
    back to the nearest post whose row seats the desk, rather than the scene
    fabricating a board report it has no seat for. A one-post episode has nowhere
    to walk to and simply runs desk-less."""
    for post in range(n_posts - 1, -1, -1):
        if benched_role(week, post) != "switchboard":
            return post
    return n_posts - 1


def _frame_turns(parts: list[tuple[str, str]]) -> list[dict]:
    """Turns for a frame scene, from (voice, frame-part) pairs."""
    return [
        _turn(i, voice, part=part, does=FRAME_PARTS[part][0], turns=FRAME_PARTS[part][1])
        for i, (voice, part) in enumerate(parts)
    ]


def build_plan_for_week(week: int, n_posts: int) -> dict:
    """The week's whole assignment, from an already-resolved week index."""
    bit_row = _voice_row(week, 0, BIT_ROW_OFFSET_W)
    vote_desk, vote_foil, rapid_fire, closer = bit_row
    open_lines = open_lines_post_index(week, n_posts)

    # Scene 1 — the cold open is the lead post's two stance holders, already
    # mid-argument. Tying the frame to the week's content means it rotates for
    # free instead of needing an axis of its own.
    lead_for, lead_against = stance_pair(week, 0)
    first, second = (lead_against, lead_for) if week % 2 == 0 else (lead_for, lead_against)
    parts = {lead_for: "arguing-for", lead_against: "arguing-against"}
    scenes = [
        _scene(1, "cold-open", _frame_turns([(first, parts[first]), (second, parts[second])])),
        _scene(2, "vote-desk", _frame_turns([(vote_desk, "desk"), (vote_foil, "foil")])),
    ]
    for post in range(n_posts):
        kind = "open-lines" if post == open_lines else "post"
        scenes.append(post_scene(week, post, index=len(scenes) + 1, kind=kind))
    # Rapid fire leads with its bit owner; the rest of the week's row follows.
    rapid = [rapid_fire] + [v for v in bit_row if v != rapid_fire]
    scenes.append(
        _scene(
            len(scenes) + 1,
            "rapid-fire",
            _frame_turns([(rapid[0], "bumper")] + [(v, "take") for v in rapid[1:]]),
        )
    )
    scenes.append(
        _scene(
            len(scenes) + 1,
            "sign-off",
            _frame_turns([(closer, "sign-off"), (vote_desk, "button")]),
        )
    )
    return {
        "week": week,
        "week_row": week % len(ROLE_ORDERS_W),
        "cast": list(CAST),
        "roles": {
            name: {"does": r["does"], "turns": list(r["turns"]), "conditional": r["conditional"]}
            for name, r in ROLES.items()
        },
        "bits": {
            "vote_desk": vote_desk,
            "vote_desk_foil": vote_foil,
            "rapid_fire": rapid_fire,
            "sign_off": closer,
        },
        "open_lines_post_index": open_lines,
        "scenes": scenes,
    }


def build_plan(date_iso: str, n_posts: int) -> dict:
    """The week's plan, seeded on `date_iso` and nothing else. Deterministic in
    (date_iso, n_posts) — the fixed JSON contract the write layer consumes."""
    plan = {"date": date_iso}
    plan.update(build_plan_for_week(week_index(date_iso), n_posts))
    return plan


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Week-seeded assignment plan for Surface Tension")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ap_plan = sub.add_parser("plan", help="print the week's assignment plan as one JSON object")
    ap_plan.add_argument("--date", required=True, help="run date YYYY-MM-DD (the only clock)")
    ap_plan.add_argument("--posts", required=True, type=int, help="number of post-backed scenes")
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
