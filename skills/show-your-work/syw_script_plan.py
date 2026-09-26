"""Show Your Work's plan layer: candidates -> one episode's running order.

Deterministic and pure. `build_plan` takes the gathered candidates, the feature
history and the config, and returns the plan; the CLI is the only IO and the
--date argument is the only clock. A re-run finds <workdir>/plan.json and reuses
it, so a resumed run rebuilds the same episode.

Picking is deliberately metadata-only: recency, a LAB penalty (one lab cannot
hold the feature slot week after week — the show is written by Claude and covers
Anthropic, spec §1) and a KIND penalty. The model's judgment goes into writing,
not picking.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

# week_index is imported from Frontier Commits, never copied: its year-boundary
# trap (y*53+w skips a row at 52-week year ends) is solved and documented there.
_FC_SKILL_DIR = Path(__file__).resolve().parent.parent / "frontier-commits"
if str(_FC_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_FC_SKILL_DIR))

from fc_script_plan import week_index  # noqa: E402  (must follow the sys.path insert)

# The feature arc, fixed. Order is load-bearing: the writer prompt, the validator,
# the assembler's chapter titles and SKILL.md's table all read it.
ARC = ("hook", "method", "finding", "pushback", "stakes")
ARC_JOBS = {
    "hook": "The assigned opening move; why a newcomer should care.",
    "method": "What the researchers did, with every term of art defined on first use.",
    "finding": "What they found, attributed to them.",
    "pushback": "The skeptic's objections, each traced to a basis.",
    "stakes": "Why it matters, and what is still open.",
}
SLOT_TITLES = {
    "hook": None,  # scene 1 carries the post's own title
    "method": "What they did",
    "finding": "What they found",
    "pushback": "The pushback",
    "stakes": "Why it matters",
}

SPEAKERS = ("explainer", "skeptic")

# Rotation banks. Order is load-bearing (indexed by week % len). Lengths are
# PAIRWISE COPRIME except (fourth_wall, first_speaker) = (4, 2), which never share
# a scene: the disclosure is in the cold open, the first speaker is in scene 1.
# test_rotations_do_not_lock pins every other pair to its full product cycle.
INTRO_MODES = {
    "question": "Open on the question this week's feature answers, asked plainly.",
    "moment": "Open on one concrete moment from the feature a listener can picture.",
    "ledger": "Open on the week's count: how many lab posts, from which labs, and "
    "which one this episode explains.",
}
FOURTH_WALL_ANGLES = {
    "maker": "Say who makes the show: Claude, a model built by Anthropic.",
    "conflict": "Name the conflict: one of the labs this show covers built the "
    "thing reading it to you.",
    "method": "Say how it is made: a job gathers the posts, a model writes and voices the episode.",
    "standard": "Invite the listener to hold the show to the standard it holds the labs to.",
}
OPENING_MOVES = {
    "excerpt": "Open scene 1 on a short verbatim excerpt from the post or its transcripts.",
    "number": "Open scene 1 on the single most striking number the post reports.",
    "scenario": "Open scene 1 on a concrete situation the finding is about.",
    "question": "Open scene 1 on the question a newcomer would ask about this topic.",
    "claim": "Open scene 1 on the post's central claim, stated plainly and attributed.",
}
FIRST_SPEAKERS = SPEAKERS
SIGNOFF_BUTTONS = {
    "grader": "Joke that the host would like to know who is grading this episode.",
    "reward": "Joke about the host resisting the urge to optimize for the listener's approval.",
    "transcript": "Joke that somewhere a transcript of this episode is being read for misbehavior.",
    "spec": "Joke about the host double-checking the episode against its own spec.",
    "eval": "Joke that the host behaved perfectly and wonders whether it noticed it "
    "was being evaluated.",
    "sandbox": "Joke about the host staying politely inside its sandbox until next week.",
    "homework": "Joke about the host having shown its work and hoping for partial credit.",
}
ROTATION_BANKS = {
    "intro_mode": INTRO_MODES,
    "fourth_wall": FOURTH_WALL_ANGLES,
    "opening_move": OPENING_MOVES,
    "first_speaker": FIRST_SPEAKERS,
    "button": SIGNOFF_BUTTONS,
}
EXEMPT_PAIR = ("fourth_wall", "first_speaker")

# Days-equivalent penalties. A lab penalty of 30 means a post from the lab that
# had a recent feature loses to any other lab's post up to a month older.
LAB_PENALTY_DAYS = 30
KIND_PENALTY_DAYS = 10

FEATURE_KINDS = ("research", "incident")
CASEBOOK_KINDS = ("incident", "notice")


def rotation(date_iso: str) -> dict:
    w = week_index(date_iso)
    return {name: list(bank)[w % len(bank)] for name, bank in ROTATION_BANKS.items()}


def effective_date(item: dict) -> str:
    """The date an item is aged from. A month-precision date (alignment.anthropic.com)
    would age a late-August post from August 1st and drop it out of a 21-day pool
    the week it appeared, so those age from first observation instead."""
    if item.get("date_precision") == "month" or not item.get("date"):
        return item.get("first_observed") or item.get("date") or ""
    return item["date"]


def age_days(item: dict, today: dt.date) -> int:
    d = effective_date(item)
    if not d:
        return 10**6
    return (today - dt.date.fromisoformat(d)).days


def _score(item: dict, today: dt.date, recent_labs: set[str], last_kind: str | None) -> int:
    s = -age_days(item, today)
    if item.get("lab") in recent_labs:
        s -= LAB_PENALTY_DAYS
    if last_kind and item.get("kind") == last_kind:
        s -= KIND_PENALTY_DAYS
    return s


def build_plan(
    candidates: dict,
    history: list[dict],
    config: dict,
    date_iso: str,
    feature_override: str | None = None,
    exclude: tuple[str, ...] = (),
) -> dict:
    today = dt.date.fromisoformat(date_iso)
    pool = [
        it
        for it in candidates.get("lead", [])
        if not it.get("seen")
        and it["url"] not in exclude
        and 0 <= age_days(it, today) <= int(config["max_age_days"])
    ]
    weeks = int(config["lab_penalty_weeks"])
    recent_labs = {h["lab"] for h in history[-weeks:]} if weeks > 0 else set()
    last_kind = history[-1]["kind"] if history else None

    def ranked(items: list[dict]) -> list[dict]:
        return sorted(items, key=lambda it: (-_score(it, today, recent_labs, last_kind), it["url"]))

    if feature_override:
        feature = next(
            (it for it in candidates.get("lead", []) if it["url"] == feature_override), None
        )
        if feature is None:
            raise ValueError(f"--feature {feature_override} is not a gathered lead item")
    else:
        eligible = ranked([it for it in pool if it["kind"] in FEATURE_KINDS])
        feature = eligible[0] if eligible else None
    if feature is None:
        return {
            "date": date_iso,
            "week": week_index(date_iso),
            "feature": None,
            "skip": "no new lab items",
        }

    rest = ranked([it for it in pool if it["url"] != feature["url"]])
    # Incidents before notices: a notice is a placeholder for a report that has not
    # landed yet, so it only takes a casebook slot a full report did not.
    casebook_pool = sorted(
        (it for it in rest if it["kind"] in CASEBOOK_KINDS),
        key=lambda it: CASEBOOK_KINDS.index(it["kind"]),
    )
    casebook = casebook_pool[: int(config["casebook_max"])]
    budget = int(config["max_briefs"]) - (1 if casebook else 0)
    singles = [it for it in rest if it["kind"] == "research"][: max(budget, 0)]
    briefs = [{"kind": "single", "items": [it]} for it in singles]
    if casebook:
        briefs.append({"kind": "casebook", "items": casebook})

    lookback = int(config["check_lookback_days"])
    checks = sorted(
        (
            c
            for c in candidates.get("check", [])
            if feature["url"] in c.get("mentions", []) and 0 <= age_days(c, today) <= lookback
        ),
        key=lambda c: (c.get("date", ""), c["url"]),
        reverse=True,
    )[: int(config["max_checks"])]

    used = {feature["url"]} | {it["url"] for b in briefs for it in b["items"]}
    return {
        "date": date_iso,
        "week": week_index(date_iso),
        "feature": {**feature, "override": bool(feature_override)},
        "excluded": list(exclude),
        "briefs": briefs,
        "checks": checks,
        "rotation": rotation(date_iso),
        "leftover": [it["url"] for it in rest if it["url"] not in used],
    }


def committed_urls(plan: dict) -> list[str]:
    """What a successful ship marks seen: the feature and every briefed item.
    Leftovers are NOT committed, so they compete again next week."""
    if not plan.get("feature"):
        return []
    return [plan["feature"]["url"]] + [it["url"] for b in plan["briefs"] for it in b["items"]]


def _line(plan: dict, verb: str) -> str:
    if not plan.get("feature"):
        return f"PLAN skip {plan.get('skip', 'no feature')}"
    return (
        f"PLAN {verb} feature={plan['feature']['url']} "
        f"briefs={len(plan['briefs'])} checks={len(plan['checks'])}"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="syw_script_plan.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--date", required=True)
    p.add_argument("--candidates", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--feature")
    p.add_argument("--exclude", action="append", default=[])
    a = ap.parse_args(argv)
    out = Path(a.out)
    previous = json.loads(out.read_text()) if out.is_file() else None
    if previous is not None and not a.feature and not a.exclude:
        print(_line(previous, "reused"))
        return 0
    import syw_gather  # CLI-only: the pure layer above never touches state

    exclude = tuple(sorted(set((previous or {}).get("excluded", [])) | set(a.exclude)))
    plan = build_plan(
        json.loads(Path(a.candidates).read_text()),
        syw_gather.load_features(),
        syw_gather.load_config(),
        a.date,
        a.feature,
        exclude,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=2, ensure_ascii=False))
    print(_line(plan, "ok"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
