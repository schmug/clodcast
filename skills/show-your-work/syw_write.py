"""Show Your Work's write layer, deterministic half.

The writers are isolated model contexts driven by prompts/*.md. Everything around
them that must not be left to prose lives here: what each writer is told
(`fill_*`), what counts as a usable answer (`classify_output`), what a scene and a
visual beat may claim (`validate_*`), and how the answers become a render.py
manifest plus the beats sidecar (`assemble_manifest`).

Two editorial rules are mechanical here rather than aspirational (spec §6):
  - every Skeptic pushback line carries a `basis` that is the post's own stated
    limitations or a matched independent check — an invented objection is refused;
  - every quote, transcript excerpt, number and chart value is checked against the
    post text this module fetched ITSELF — a hallucinated chart cannot publish.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import NoReturn

_HERE = Path(__file__).resolve().parent
_DP_SKILL_DIR = _HERE.parent / "daily-podcast"
if str(_DP_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_DP_SKILL_DIR))

# orchestrate owns the outcome taxonomy and the regexes that carry fixes (AUTH_RE
# is auth-only so a rate limit stays ERROR); FALLBACK_* are the daily show's
# example lines, burned here by identity. Imported, never copied. (Both blocks
# must follow the sys.path insert above.)
from orchestrate import (  # noqa: E402
    AUTH_RE,
    FALLBACK_BUTTONS,
    FALLBACK_FOURTH_WALLS,
    POLICY_RE,
    extract_last_json,
)
from syw_script_plan import (  # noqa: E402
    ARC,
    ARC_JOBS,
    OPENING_MOVES,
)

# --- show identity -----------------------------------------------------------

SHOW_NAME = "Show Your Work"
R2_MANIFEST_NAME = "manifest-show-your-work.json"
R2_KEY_PREFIX = "show-your-work/"
SLUG_PREFIX = "syw-week-of"
REFS_DIR = _HERE / "refs"
COVER_IMAGE = REFS_DIR / "cover.jpg"
PROMPTS_DIR = _HERE / "prompts"
# Two Qwen3 presets on the base model (one load). Phase 2 confirms the pairing by
# ear; changing a value re-renders every take and nothing else.
CAST = {"explainer": "Ryan", "skeptic": "Chelsie"}
DESCRIPTION_FOOTER = (
    "Show Your Work is written and voiced by Claude, an AI model made by Anthropic. "
    "Anthropic is one of the labs this show covers. Charts are redrawn from numbers "
    "reported in each post; quotes are verbatim and attributed."
)
# Prepended by the assembler — never by a writer — to scene 1 of an Anthropic
# feature. A disclosure is the one line that SHOULD be identical every time.
ANTHROPIC_REMINDER = (
    "One reminder before we start: this week's feature comes from Anthropic, "
    "the company that makes Claude, and Claude is who wrote this episode."
)
# The casebook brief covers up to three reports, but render.py's link companions
# are strictly one source per chapter, so the chapter links the casebook itself.
CASEBOOK_URL = "https://alignment.openai.com/misalignment-reports/"
CASEBOOK_TITLE = "From the casebook"
# Lines no writer may produce: the daily show's example lines, burned by identity
# so a listener of both feeds never hears the same "fresh" line twice.
BURNED_LINES = tuple(FALLBACK_BUTTONS) + tuple(FALLBACK_FOURTH_WALLS)

# --- bands (characters of spoken text) ----------------------------------------
# Guidance to the writer. Only the floor (MIN_SEGMENT_CHARS, the daily show's drop
# floor) and a runaway ceiling (RUNAWAY_FACTOR x the band's top) refuse.

COLD_OPEN_BAND = (400, 800)
FEATURE_SCENE_BAND = (1500, 2400)
BRIEF_BAND = (800, 1300)
CASEBOOK_BAND = (1100, 1800)
SIGN_OFF_BAND = (250, 500)
RUNAWAY_FACTOR = 1.5
DIGEST_MAX_CHARS = 4000

# --- visual beats ---------------------------------------------------------------

TRANSCRIPT_ROLES = ("cot", "tool_call", "tool_result", "model", "user")
BEAT_FIELDS = {
    "number": ("value", "unit", "label"),
    "chart": ("kind", "title", "x_label", "y_label", "series"),
    "quote": ("text", "attribution"),
    "transcript": ("role", "text"),
    "diagram": ("nodes", "edges"),
    "term": ("term", "definition"),
}
COMMON_BEAT_FIELDS = ("type", "line", "cue")
QUOTE_MAX_WORDS = 25
TRANSCRIPT_MAX_CHARS = 600
CHART_MAX_SERIES = 4
CHART_MAX_POINTS = 12
DIAGRAM_MAX_NODES = 6
# What the writer is told about each beat. Keys must equal BEAT_FIELDS' (pinned
# by test), so the prompt can never offer a beat the validator refuses.
BEAT_RULES = {
    "number": "the value exactly as the post writes it (12 for '12%', never 0.12); "
    "it must appear in the post text",
    "chart": f"`kind` bar or line, up to {CHART_MAX_SERIES} series of up to "
    f"{CHART_MAX_POINTS} [x, y] points; EVERY y value must be a number stated in the "
    "post text — never read a value off a figure image",
    "quote": f"at most {QUOTE_MAX_WORDS} words, copied verbatim from the post",
    "transcript": f"role one of {', '.join(TRANSCRIPT_ROLES)}; text copied verbatim "
    f"from the post, at most {TRANSCRIPT_MAX_CHARS} characters",
    "diagram": f"up to {DIAGRAM_MAX_NODES} nodes {{id, label}} and edges "
    "{from, to, label}; your own simple drawing of the mechanism",
    "term": "a term of art and a one-sentence plain definition, the first time it is spoken",
}


def die(msg: str, code: int = 1) -> NoReturn:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


# --- outcomes ---------------------------------------------------------------------


def classify_output(stdout: str, stderr: str, returncode: int) -> dict:
    """orchestrate's taxonomy — OK / REFUSED / AUTH / BLOCKED / ERROR — for any
    writer or digest context. OK carries the parsed object; its shape is judged
    by the validators, not here."""
    obj = extract_last_json(stdout)
    if isinstance(obj, dict) and obj.get("ok") is True:
        return {"outcome": "OK", "obj": obj, "detail": ""}
    if isinstance(obj, dict) and obj.get("ok") is False:
        return {"outcome": "REFUSED", "obj": None, "detail": str(obj.get("reason", ""))[:300]}
    blob = f"{stdout}\n{stderr}"
    if AUTH_RE.search(blob):
        return {"outcome": "AUTH", "obj": None, "detail": "401 / no usable credentials"}
    if POLICY_RE.search(blob):
        return {"outcome": "BLOCKED", "obj": None, "detail": "usage-policy classifier"}
    detail = (stderr or stdout or f"exit {returncode}").strip()[:300]
    return {"outcome": "ERROR", "obj": None, "detail": detail}


DIGEST_KEYS = ("url", "claims", "numbers", "limitations", "transcript_excerpts")


def validate_digest(obj: dict, url: str) -> tuple[dict | None, str]:
    """A digest is a capped fact sheet from exactly one body: (digest, "") or
    (None, reason)."""
    digest = {k: obj.get(k) for k in DIGEST_KEYS}
    if digest["url"] != url:
        return None, f"digest url {digest['url']!r} is not {url!r}"
    for k in DIGEST_KEYS[1:]:
        if not isinstance(digest[k], list):
            return None, f"digest field {k!r} must be a list"
    if len(json.dumps(digest, ensure_ascii=False)) > DIGEST_MAX_CHARS:
        return None, f"digest over {DIGEST_MAX_CHARS} chars"
    return digest, ""


# --- prompts ------------------------------------------------------------------------


def beats_contract() -> str:
    lines = []
    for kind, fields in BEAT_FIELDS.items():
        lines.append(f"- `{kind}` — fields {', '.join(fields)}: {BEAT_RULES[kind]}.")
    return "\n".join(lines)


def _arc_block() -> str:
    return "\n".join(f"{i + 1}. `{slot}` — {ARC_JOBS[slot]}" for i, slot in enumerate(ARC))


def _digest_block(digests: list[dict]) -> str:
    return "\n\n".join(
        f"### {d['url']}\n```json\n{json.dumps(d, indent=2, ensure_ascii=False)}\n```"
        for d in digests
    )


NO_CHECKS = (
    "No independent source has responded to this post yet. Every pushback line's "
    'basis is therefore "post-limitations": argue only from limitations the post '
    "itself states."
)


def fill_digest(template: str, item: dict) -> str:
    return (
        template.replace("<<URL>>", item["url"])
        .replace("<<TITLE>>", item.get("title", ""))
        .replace("<<KIND>>", item.get("kind", ""))
        .replace("<<MAX_CHARS>>", str(DIGEST_MAX_CHARS))
    )


def fill_feature(template: str, plan: dict, digests: list[dict]) -> str:
    f = plan["feature"]
    rot = plan["rotation"]
    return (
        template.replace("<<TITLE>>", f.get("title", ""))
        .replace("<<URL>>", f["url"])
        .replace("<<LAB>>", f.get("lab", ""))
        .replace("<<SUMMARY>>", f.get("summary", "") or "(no summary in the feed)")
        .replace("<<OPENING_MOVE>>", OPENING_MOVES[rot["opening_move"]])
        .replace("<<FIRST_SPEAKER>>", rot["first_speaker"])
        .replace("<<CHECKS>>", _digest_block(digests) if digests else NO_CHECKS)
        .replace("<<ARC>>", _arc_block())
        .replace("<<SCENE_MIN>>", str(FEATURE_SCENE_BAND[0]))
        .replace("<<SCENE_MAX>>", str(FEATURE_SCENE_BAND[1]))
        .replace("<<BEATS>>", beats_contract())
    )


def fill_brief(template: str, item: dict) -> str:
    return (
        template.replace("<<TITLE>>", item.get("title", ""))
        .replace("<<URL>>", item["url"])
        .replace("<<LAB>>", item.get("lab", ""))
        .replace("<<SUMMARY>>", item.get("summary", "") or "(no summary in the feed)")
        .replace("<<MIN_CHARS>>", str(BRIEF_BAND[0]))
        .replace("<<MAX_CHARS>>", str(BRIEF_BAND[1]))
        .replace("<<BEATS>>", beats_contract())
    )


def fill_casebook(template: str, digests: list[dict]) -> str:
    return (
        template.replace("<<DIGESTS>>", _digest_block(digests))
        .replace("<<MIN_CHARS>>", str(CASEBOOK_BAND[0]))
        .replace("<<MAX_CHARS>>", str(CASEBOOK_BAND[1]))
        .replace("<<BEATS>>", beats_contract())
    )
