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

import html
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import NoReturn

import syw_gather

_HERE = Path(__file__).resolve().parent
_DP_SKILL_DIR = _HERE.parent / "daily-podcast"
if str(_DP_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_DP_SKILL_DIR))

# orchestrate owns the outcome taxonomy, the regexes that carry fixes (AUTH_RE is
# auth-only so a rate limit stays ERROR), and MIN_SEGMENT_CHARS, the daily show's
# drop floor reused as the scene/brief/frame floor here; FALLBACK_* are the daily
# show's example lines, burned here by identity. Imported, never copied. (Both
# blocks must follow the sys.path insert above.)
from orchestrate import (  # noqa: E402
    AUTH_RE,
    FALLBACK_BUTTONS,
    FALLBACK_FOURTH_WALLS,
    MIN_SEGMENT_CHARS,
    POLICY_RE,
    extract_last_json,
)
from syw_script_plan import (  # noqa: E402
    ARC,
    ARC_JOBS,
    OPENING_MOVES,
    SPEAKERS,
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


# --- normalization for the verbatim guards --------------------------------------

_TYPO_MAP = str.maketrans(
    {
        "‘": "'", "’": "'", "‚": "'", "‛": "'",
        "“": '"', "”": '"', "„": '"', "‟": '"',
        "–": "-", "—": "-", "−": "-",
        " ": " ", " ": " ", " ": " ", "…": "...",
    }
)  # fmt: skip


def norm(s: str) -> str:
    """Typography-blind form for substring checks: NFKC, straight quotes, one kind
    of dash, entities decoded, whitespace collapsed. Case is kept — a quote that
    changes case is not verbatim."""
    s = unicodedata.normalize("NFKC", html.unescape(str(s)))
    return re.sub(r"\s+", " ", s.translate(_TYPO_MAP)).strip()


_NUM_RE = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> set[float]:
    """Every number in the text, as floats, with the absolute value of a negative
    too ('a -12% drop' supports a beat that says 12)."""
    out: set[float] = set()
    for tok in _NUM_RE.findall(norm(text)):
        try:
            v = float(tok.replace(",", ""))
        except ValueError:
            continue
        out |= {v, abs(v)}
    return out


def as_number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip().rstrip("%").strip())
    except ValueError:
        return None


def fetch_post_text(url: str) -> str:
    """The visible text of a post, fetched by THIS module so the guards never trust
    the writer's report of what the post says. A notice's #fragment is dropped for
    the fetch; the fragment is identity, not a location on a server."""
    return syw_gather.page_text(syw_gather.fetch_text(url.split("#", 1)[0]))


# --- beats ------------------------------------------------------------------------


def validate_beat(
    beat, lines: list[dict], source_text: str, seen_terms: set
) -> tuple[dict | None, str]:
    """(beat, "") when kept — its cue cleared if it is not in the line — or
    (None, reason) when dropped."""
    if not isinstance(beat, dict) or beat.get("type") not in BEAT_FIELDS:
        got = beat.get("type") if isinstance(beat, dict) else beat
        return None, f"unknown beat type {got!r}"
    kind = beat["type"]
    extra = set(beat) - set(BEAT_FIELDS[kind]) - set(COMMON_BEAT_FIELDS)
    if extra:
        return None, f"{kind} beat has unknown key(s) {sorted(extra)}"
    line = beat.get("line")
    if not isinstance(line, int) or isinstance(line, bool) or not 0 <= line < len(lines):
        return None, f"{kind} beat line {line!r} out of range"
    beat = dict(beat)
    cue = beat.get("cue")
    if not isinstance(cue, str) or not cue.strip() or norm(cue) not in norm(lines[line]["text"]):
        beat["cue"] = None  # the video stage falls back to the start of the line
    src = norm(source_text)
    nums = numbers_in(source_text)

    if kind == "number":
        v = as_number(beat.get("value"))
        if v is None or v not in nums:
            return None, f"number {beat.get('value')!r} not in the post text"
    elif kind == "chart":
        if beat.get("kind") not in ("bar", "line"):
            return None, "chart kind must be bar or line"
        series = beat.get("series")
        if not isinstance(series, list) or not 0 < len(series) <= CHART_MAX_SERIES:
            return None, f"chart needs 1-{CHART_MAX_SERIES} series"
        for s in series:
            pts = s.get("points") if isinstance(s, dict) else None
            if not isinstance(pts, list) or not 0 < len(pts) <= CHART_MAX_POINTS:
                return None, f"chart series needs 1-{CHART_MAX_POINTS} points"
            for pt in pts:
                y = as_number(pt[1]) if isinstance(pt, list) and len(pt) == 2 else None
                if y is None or y not in nums:
                    return None, f"chart value {pt!r} not in the post text"
    elif kind == "quote":
        text = str(beat.get("text", ""))
        if len(text.split()) > QUOTE_MAX_WORDS:
            return None, f"quote over {QUOTE_MAX_WORDS} words"
        if not text.strip() or norm(text) not in src:
            return None, "quote is not verbatim in the post"
    elif kind == "transcript":
        text = str(beat.get("text", ""))
        if beat.get("role") not in TRANSCRIPT_ROLES:
            return None, f"transcript role must be one of {list(TRANSCRIPT_ROLES)}"
        if len(text) > TRANSCRIPT_MAX_CHARS:
            return None, f"transcript over {TRANSCRIPT_MAX_CHARS} chars"
        if not text.strip() or norm(text) not in src:
            return None, "transcript is not verbatim in the source"
    elif kind == "diagram":
        nodes, edges = beat.get("nodes"), beat.get("edges")
        if not isinstance(nodes, list) or not 0 < len(nodes) <= DIAGRAM_MAX_NODES:
            return None, f"diagram needs 1-{DIAGRAM_MAX_NODES} nodes"
        ids = [n.get("id") for n in nodes if isinstance(n, dict)]
        if (
            len(ids) != len(nodes)
            or len(set(ids)) != len(ids)
            or not all(isinstance(i, str) for i in ids)
        ):
            return None, "diagram node ids must be unique strings"
        if not isinstance(edges, list) or any(
            not isinstance(e, dict) or e.get("from") not in ids or e.get("to") not in ids
            for e in edges
        ):
            return None, "diagram edges must join known node ids"
    elif kind == "term":
        term = str(beat.get("term", "")).strip()
        if not term or not str(beat.get("definition", "")).strip():
            return None, "term needs a term and a definition"
        if term.casefold() in seen_terms:
            return None, f"term {term!r} already defined this episode"
        seen_terms.add(term.casefold())
    return beat, ""


def _validate_beats(beats, lines, source_text, seen_terms, where):
    kept, dropped = [], []
    for beat in beats if isinstance(beats, list) else []:
        ok, reason = validate_beat(beat, lines, source_text, seen_terms)
        if ok is None:
            dropped.append({"where": where, "beat": beat, "reason": reason})
        else:
            kept.append(ok)
    return kept, dropped


# --- scenes -----------------------------------------------------------------------


def lines_text(lines: list[dict]) -> str:
    return " ".join(str(ln.get("text", "")) for ln in lines)


def _line_problems(lines, where: str) -> list[str]:
    if not isinstance(lines, list) or not lines:
        return [f"{where}: lines must be a non-empty list"]
    probs = []
    for j, ln in enumerate(lines):
        if not isinstance(ln, dict) or ln.get("speaker") not in SPEAKERS:
            probs.append(f"{where} line {j}: speaker must be one of {list(SPEAKERS)}")
        elif not isinstance(ln.get("text"), str) or not ln["text"].strip():
            probs.append(f"{where} line {j}: text must be a non-empty string")
    return probs


def _band_problems(lines, band, where: str) -> list[str]:
    n = len(lines_text(lines))
    if n < MIN_SEGMENT_CHARS:
        return [f"{where}: too short ({n} chars < {MIN_SEGMENT_CHARS})"]
    if n > band[1] * RUNAWAY_FACTOR:
        return [f"{where}: runaway length ({n} chars > {int(band[1] * RUNAWAY_FACTOR)})"]
    return []


def validate_feature(obj: dict, plan: dict, post_text: str, seen_terms: set) -> dict:
    """Any problem refuses the whole feature (spec §4.4 failure path); a bad beat
    is only dropped."""
    scenes = obj.get("scenes")
    slots = (
        [s.get("slot") for s in scenes if isinstance(s, dict)] if isinstance(scenes, list) else []
    )
    if slots != list(ARC) or len(slots) != len(scenes):
        return {
            "ok": False,
            "problems": [f"scenes must be exactly the arc {list(ARC)} in order"],
            "scenes": [],
            "dropped_beats": [],
        }
    allowed_basis = {"post-limitations"} | {c["url"] for c in plan.get("checks", [])}
    problems: list[str] = []
    out, dropped = [], []
    # A structural problem elsewhere in the loop still lets this scene's beats
    # validate (no `continue` on the hook/pushback checks below), so a term's
    # first use is only committed to the caller's set once the whole feature is
    # `ok` — a refused writer attempt must not consume it before the retry.
    local_terms = set(seen_terms)
    for scene in scenes:
        slot, lines = scene["slot"], scene.get("lines")
        probs = _line_problems(lines, slot)
        if not probs:
            probs = _band_problems(lines, FEATURE_SCENE_BAND, slot)
        if probs:
            problems += probs
            continue
        first_speaker = plan["rotation"]["first_speaker"]
        if slot == "hook" and lines[0]["speaker"] != first_speaker:
            problems.append(f"hook must open with the {first_speaker}")
        if slot == "pushback":
            if not any(ln["speaker"] == "skeptic" for ln in lines):
                problems.append("pushback has no skeptic line")
            for j, ln in enumerate(lines):
                if ln["speaker"] == "skeptic" and ln.get("basis") not in allowed_basis:
                    problems.append(
                        f"pushback line {j} basis {ln.get('basis')!r} is neither "
                        "post-limitations nor a matched check"
                    )
        kept, drop = _validate_beats(scene.get("beats"), lines, post_text, local_terms, slot)
        dropped += drop
        out.append({"slot": slot, "lines": lines, "beats": kept})
    if not problems:
        seen_terms |= local_terms
    return {"ok": not problems, "problems": problems, "scenes": out, "dropped_beats": dropped}


def validate_brief(obj: dict, casebook: bool, source_text: str, seen_terms: set) -> dict:
    lines = obj.get("lines")
    where = "casebook" if casebook else "brief"
    problems = _line_problems(lines, where)
    if not problems:
        problems = _band_problems(lines, CASEBOOK_BAND if casebook else BRIEF_BAND, where)
    if not problems and sum(1 for ln in lines if ln["speaker"] == "skeptic") > 1:
        problems = [f"{where}: at most one skeptic line"]
    if problems:
        return {"ok": False, "problems": problems, "lines": [], "beats": [], "dropped_beats": []}
    kept, dropped = _validate_beats(obj.get("beats"), lines, source_text, seen_terms, where)
    return {"ok": True, "problems": [], "lines": lines, "beats": kept, "dropped_beats": dropped}


def _burned(text: str) -> list[str]:
    t = norm(text).casefold()
    return [b for b in BURNED_LINES if norm(b).casefold() in t]


def validate_frame(lines, which: str) -> list[str]:
    """The cold open and the sign-off, written in the main context."""
    band = COLD_OPEN_BAND if which == "cold_open" else SIGN_OFF_BAND
    probs = _line_problems(lines, which)
    if probs:
        return probs
    text = lines_text(lines)
    if len(text) > band[1] * RUNAWAY_FACTOR:
        probs.append(f"{which}: runaway length ({len(text)} chars)")
    if which == "cold_open" and not ("Claude" in text and "Anthropic" in text):
        probs.append("cold_open must disclose Claude and Anthropic (spec §1)")
    probs += [f"{which} uses a burned line: {b!r}" for b in _burned(text)]
    return probs
