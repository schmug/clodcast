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

import argparse
import datetime as dt
import hashlib
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
    SLOT_TITLES,
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


class CliError(RuntimeError):
    """A workdir CLI step cannot proceed. `main` prints it on the command's one
    line (`FILL FAILED` / `ACCEPT refused` / `ASSEMBLE FAILED`), never as a
    traceback — the procedure in SKILL.md branches on that line."""


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


# --- assembly ---------------------------------------------------------------------

_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)  # fmt: skip  (literal, not strftime("%B"), which is LC_TIME-dependent)


def episode_title(plan: dict) -> str:
    """Display text only — the slug is keyed on the date (#128), so the title is
    free to carry the feature's name."""
    d = dt.date.fromisoformat(plan["date"])
    return f"{plan['feature']['title']} - week of {_MONTHS[d.month - 1]} {d.day}, {d.year}"


def _speak(lines: list[dict]) -> list[dict]:
    """What render.py sees: speaker and text only. `basis` and beats stay out of
    the manifest even though _validate_scene tolerates extra keys today — relying
    on that would couple this show to an accident of the renderer."""
    return [{"speaker": ln["speaker"], "text": ln["text"]} for ln in lines]


def assemble_manifest(
    date_iso: str,
    title: str,
    summary: str,
    plan: dict,
    cold_open: list[dict],
    feature_scenes: list[dict],
    briefs: list[dict],
    sign_off: list[dict],
    allow_missing_cover: bool = False,
) -> tuple[dict, dict]:
    """(manifest, beats) for one episode.

    `feature_scenes` is validate_feature's `scenes`; each of `briefs` is
    {"kind": "single"|"casebook", "item": <lead item or None>, "lines", "beats"}.
    Beat `line` indices in the sidecar are relative to each segment's FINAL lines,
    i.e. after the Anthropic reminder is prepended."""
    for which, lines in (("cold_open", cold_open), ("sign_off", sign_off)):
        probs = validate_frame(lines, which)
        if probs:
            die("; ".join(probs))
    if not COVER_IMAGE.is_file() and not allow_missing_cover:
        die(f"cover art missing: {COVER_IMAGE} (a live ship needs the show's own art)")

    feature = plan["feature"]
    segments: list[dict] = [
        {"title": "Cold open", "role": "intro", "source_url": None, "lines": _speak(cold_open)}
    ]
    beats: dict[str, list[dict]] = {}
    for scene in feature_scenes:
        lines = _speak(scene["lines"])
        shift = 0
        if scene["slot"] == "hook" and feature.get("lab") == "anthropic":
            lines = [{"speaker": "explainer", "text": ANTHROPIC_REMINDER}, *lines]
            shift = 1
        hook = scene["slot"] == "hook"
        if scene["beats"]:
            beats[str(len(segments))] = [{**b, "line": b["line"] + shift} for b in scene["beats"]]
        segments.append(
            {
                "title": (feature["title"] if hook else SLOT_TITLES[scene["slot"]])[:120],
                "source_url": feature["url"] if hook else None,
                "lines": lines,
            }
        )
    for brief in briefs:
        casebook = brief["kind"] == "casebook"
        if brief["beats"]:
            beats[str(len(segments))] = brief["beats"]
        segments.append(
            {
                "title": (CASEBOOK_TITLE if casebook else brief["item"]["title"])[:120],
                "source_url": CASEBOOK_URL if casebook else brief["item"]["url"],
                "lines": _speak(brief["lines"]),
            }
        )
    segments.append(
        {"title": "Sign-off", "role": "outro", "source_url": None, "lines": _speak(sign_off)}
    )
    manifest = {
        # Display-only: `date` is what keys the slug and the guid (#128).
        "title": title,
        "summary": summary,
        "date": date_iso,
        # Fallback voice for a plain-text segment; every segment here is a scene.
        "voice": CAST["explainer"],
        "cast": dict(CAST),
        "ship_mode": "web",
        "show_name": SHOW_NAME,
        "r2_manifest_name": R2_MANIFEST_NAME,
        "r2_key_prefix": R2_KEY_PREFIX,
        "slug_prefix": SLUG_PREFIX,
        "description_footer_text": DESCRIPTION_FOOTER,
        "segments": segments,
    }
    if COVER_IMAGE.is_file():
        manifest["cover_image"] = str(COVER_IMAGE)
    return manifest, {"version": 1, "segments": beats}


# --- workdir CLI --------------------------------------------------------------------


def _read(p: Path):
    return json.loads(p.read_text())


def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False))


def digest_path(workdir: Path, url: str) -> Path:
    return workdir / "digests" / f"{hashlib.sha1(url.encode()).hexdigest()[:12]}.json"


def _digests(workdir: Path, urls: list[str]) -> list[dict]:
    return [_read(p) for u in urls if (p := digest_path(workdir, u)).is_file()]


def _find_item(plan: dict, url: str) -> dict:
    pool = [plan["feature"], *plan.get("checks", [])]
    pool += [it for b in plan.get("briefs", []) for it in b["items"]]
    for it in pool:
        if it["url"] == url:
            return it
    die(f"{url} is not an item in this plan")


def _cmd_fill(a) -> int:
    wd = Path(a.workdir)
    plan = _read(wd / "plan.json")
    if a.what == "digest":
        print(fill_digest((PROMPTS_DIR / "digest.md").read_text(), _find_item(plan, a.url)))
    elif a.what == "feature":
        checks = _digests(wd, [c["url"] for c in plan["checks"]])
        print(fill_feature((PROMPTS_DIR / "write_feature.md").read_text(), plan, checks))
    else:
        brief = plan["briefs"][a.index]
        if brief["kind"] == "casebook":
            ds = _digests(wd, [it["url"] for it in brief["items"]])
            if not ds:
                # The casebook writer holds no body, only digests: with none it
                # could only invent. The procedure drops this brief (SKILL.md).
                print("FILL refused casebook has no digests")
                return 2
            print(fill_casebook((PROMPTS_DIR / "write_casebook.md").read_text(), ds))
        else:
            print(fill_brief((PROMPTS_DIR / "write_brief.md").read_text(), brief["items"][0]))
    return 0


def _refused(reason: str) -> int:
    print(f"ACCEPT refused {reason}"[:400])
    return 2


def _cmd_accept(a) -> int:
    wd = Path(a.workdir)
    plan = _read(wd / "plan.json")
    res = classify_output(Path(a.output).read_text(), "", 0)
    if res["outcome"] != "OK":
        return _refused(f"{res['outcome']} {res['detail']}")
    obj = res["obj"]
    terms_p = wd / "writes" / "terms.json"
    seen_terms = set(_read(terms_p)) if terms_p.is_file() else set()
    dropped: list[dict] = []
    if a.what == "digest":
        digest, why = validate_digest(obj, a.url)
        if digest is None:
            return _refused(why)
        _write(digest_path(wd, a.url), digest)
    elif a.what == "feature":
        v = validate_feature(obj, plan, fetch_post_text(plan["feature"]["url"]), seen_terms)
        if not v["ok"]:
            return _refused("; ".join(v["problems"]))
        # `url` names the plan this was written for; assemble refuses a mismatch.
        _write(
            wd / "writes" / "feature.json", {"url": plan["feature"]["url"], "scenes": v["scenes"]}
        )
        dropped = v["dropped_beats"]
    elif a.what == "brief":
        brief = plan["briefs"][a.index]
        casebook = brief["kind"] == "casebook"
        if casebook and not _digests(wd, [it["url"] for it in brief["items"]]):
            return _refused("casebook has no digests")
        text = " ".join(fetch_post_text(it["url"]) for it in brief["items"])
        v = validate_brief(obj, casebook, text, seen_terms)
        if not v["ok"]:
            return _refused("; ".join(v["problems"]))
        _write(
            wd / "writes" / f"brief_{a.index:02d}.json",
            {
                "kind": brief["kind"],
                "item": None if casebook else brief["items"][0],
                # What this brief was written for; assemble uses it only if the
                # plan's brief at this index still has exactly these items.
                "urls": [it["url"] for it in brief["items"]],
                "lines": v["lines"],
                "beats": v["beats"],
            },
        )
        dropped = v["dropped_beats"]
    else:
        probs = validate_frame(obj.get("lines"), a.what)
        if probs:
            return _refused("; ".join(probs))
        _write(wd / "writes" / f"{a.what}.json", {"lines": obj["lines"]})
    _write(terms_p, sorted(seen_terms))
    for d in dropped:
        syw_gather.append_dropped({"stage": "beat", **d})
    print(f"ACCEPT ok {a.what} dropped_beats={len(dropped)}")
    return 0


def accepted_briefs(wd: Path, plan: dict) -> list[tuple[dict, dict]]:
    """(plan brief, its accepted write) for each brief that will air, in plan order.

    Iterates the PLAN, never a glob of writes/: a write left by an earlier plan
    (a re-plan after `--exclude`) is used only if its `urls` equal this plan's
    brief at that index. The review's probe: plan 1's accepted brief for F2 sat at
    brief_00 when plan 2 made F2 the feature, and shipped it twice."""
    feature_url = plan["feature"]["url"]
    out = []
    for i, brief in enumerate(plan.get("briefs", [])):
        p = wd / "writes" / f"brief_{i:02d}.json"
        if not p.is_file():
            continue  # refused or never written: the brief is dropped
        write = _read(p)
        urls = [it["url"] for it in brief["items"]]
        if write.get("urls") != urls or feature_url in urls:
            continue
        out.append((brief, write))
    return out


def _cmd_assemble(a) -> int:
    wd = Path(a.workdir)
    plan = _read(wd / "plan.json")
    writes = wd / "writes"
    feature = _read(writes / "feature.json")
    if feature.get("url") != plan["feature"]["url"]:
        raise CliError(
            f"writes/feature.json was written for {feature.get('url')!r}, not this plan's "
            f"feature {plan['feature']['url']} — re-run fill/accept feature"
        )
    briefs = accepted_briefs(wd, plan)
    manifest, beats = assemble_manifest(
        plan["date"],
        episode_title(plan),
        a.summary,
        plan,
        _read(writes / "cold_open.json")["lines"],
        feature["scenes"],
        [write for _, write in briefs],
        _read(writes / "sign_off.json")["lines"],
        allow_missing_cover=a.allow_missing_cover,
    )
    # What `syw_gather commit` marks seen: exactly what went into the manifest (I1).
    # A casebook incident aired only if its digest reached the casebook writer.
    aired = []
    for brief, _ in briefs:
        if brief["kind"] == "casebook":
            aired += [it["url"] for it in brief["items"] if digest_path(wd, it["url"]).is_file()]
        else:
            aired.append(brief["items"][0]["url"])
    _write(wd / "manifest.json", manifest)
    _write(wd / "beats.json", beats)
    _write(wd / "aired.json", {"feature": plan["feature"]["url"], "briefs": aired})
    n_beats = sum(len(v) for v in beats["segments"].values())
    print(f"ASSEMBLE ok segments={len(manifest['segments'])} beats={n_beats}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="syw_write.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fill")
    f.add_argument("what", choices=("digest", "feature", "brief"))
    f.add_argument("--workdir", required=True)
    f.add_argument("--url")
    f.add_argument("--index", type=int)
    c = sub.add_parser("accept")
    c.add_argument("what", choices=("digest", "feature", "brief", "cold_open", "sign_off"))
    c.add_argument("--workdir", required=True)
    c.add_argument("--output", required=True)
    c.add_argument("--url")
    c.add_argument("--index", type=int)
    s = sub.add_parser("assemble")
    s.add_argument("--workdir", required=True)
    s.add_argument("--summary", required=True)
    s.add_argument("--allow-missing-cover", action="store_true")
    a = ap.parse_args(argv)
    try:
        return {"fill": _cmd_fill, "accept": _cmd_accept, "assemble": _cmd_assemble}[a.cmd](a)
    except CliError as e:
        if a.cmd == "accept":
            return _refused(str(e))
        print(f"{a.cmd.upper()} FAILED {e}"[:400])
        return 1


if __name__ == "__main__":
    sys.exit(main())
