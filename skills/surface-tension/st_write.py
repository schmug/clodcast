"""The Surface Tension write layer's deterministic half (#176).

`prompts/write_scene.md` asks one isolated `claude -p` for one post's entire
multi-speaker scene. This module is everything around that request that must not
be left to prose: what the writer is told (`fill_scene_prompt`), what counts as
a usable answer (`classify_scene`), what the discussion desk is allowed to say
(`scene_violations`), and how the answers become a render.py manifest
(`assemble_manifest`).

The split matters because the content rules here are not style guidance. The
comments feed carries no comment bodies (spec section 2.3), so any wording
attributed to a commenter is fabricated and the one identifier the feed does
expose - the handle in a permalink - is the one the hard rule forbids. A prompt
can ask for that; only code can prove it.

Pure module: no network, no LLM, no filesystem state, no wall-clock reads. The
caller owns the subprocess and the clock.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import NoReturn

# orchestrate.py owns the outcome taxonomy every show's writer subprocess is
# classified against, and its AUTH/POLICY regexes carry fixes this module must
# not re-derive (AUTH_RE is deliberately auth-only so a rate-limit stays ERROR).
# Imported by path for the same reason st_script_plan imports week_index: the
# skills ship as flat directories, and a second copy is a second chance to
# reintroduce a bug that was already fixed once.
_DP_SKILL_DIR = Path(__file__).resolve().parent.parent / "daily-podcast"
if str(_DP_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_DP_SKILL_DIR))

from orchestrate import (  # noqa: E402  (must follow the sys.path insert)
    AUTH_RE,
    MIN_SEGMENT_CHARS,
    POLICY_RE,
    extract_last_json,
)

# render.py owns what an engine can perform (#201): the closed marker list every
# measurement strips and the capability table the gate below consults. Imported
# rather than copied for the reason above — a second list is a second drift.
from render import (  # noqa: E402  (same)
    ENGINES,
    EVENT_MARKERS,
    TTS_ENGINE_QWEN3,
    strip_event_markers,
)
from st_script_plan import VOICES_ST  # noqa: E402  (same)


def scene_text(lines: list[dict]) -> str:
    """The scene's spoken text: its line texts joined, mirroring render.lines_text
    — markers gone, because a performed laugh is not script.

    This is what MIN_SEGMENT_CHARS measures. A scene is eight or so turns of a
    hundred-odd characters each, so applying the floor per LINE would refuse
    every well-formed scene the show produces.
    """
    spoken = (
        strip_event_markers(str(ln.get("text", ""))).strip() for ln in lines if isinstance(ln, dict)
    )
    return " ".join(text for text in spoken if text)


# --- what the writer may ask of the engine (#201) ----------------------------
#
# Direction is a per-line `instruct` over a clone (render.py's `direction`
# capability). Free text bends identity — a full "exasperated" voice description
# moved Ethan from 93 Hz to 183 Hz and his speaker similarity from 0.96 to 0.88 in
# the 2026-09-04 eval — so the writer chooses a WORD from this closed list and the
# engine hears the instruct the word maps to, which can be retuned after a listen
# without touching the writer contract. One directed line per scene until that
# drift has been measured against a listen: the budget is the show's, not the
# writer's. Both are checked at classify time, so an over-reaching writer costs
# one scene, not the run.
DIRECTIONS: dict[str, str] = {
    # pace
    "slower": "Slower than usual, unhurried, giving each clause room.",
    "faster": "Faster than usual, pressing on, not waiting for a reply.",
    # affect
    "amused": "Amused, on the edge of a laugh.",
    "exasperated": "Plainly exasperated, but keeping composure.",
    "weary": "Weary and flat, as if this has come up before.",
    "urgent": "Urgent, leaning in, as if time were short.",
    "warm": "Warm and unguarded, meaning it.",
}
MAX_DIRECTED_LINES_PER_SCENE = 1

# Any single lowercase word in parentheses is marker-SHAPED. render.py strips only
# the closed EVENT_MARKERS list and treats an unlisted one as text (a silent edit
# is not its call); here an unlisted one is refused, because on this show the only
# thing it could be is a stage direction, and every engine would read it aloud.
_MARKER_SHAPE_RE = re.compile(r"\(([a-z]+)\)")


def _shape_problem(lines) -> str:
    """Why this `lines` payload could not become a scene, or "" if it could.

    render.validate_manifest dies on a malformed line, which would take the whole
    episode down over one bad subprocess. Checking the same shape here turns that
    into one dropped scene.
    """
    if not isinstance(lines, list) or not lines:
        return "lines must be a non-empty list"
    directed = 0
    for j, line in enumerate(lines):
        if not isinstance(line, dict):
            return f"line {j} is not an object"
        speaker = line.get("speaker")
        if not isinstance(speaker, str) or not speaker.strip():
            return f"line {j} is missing 'speaker'"
        if speaker not in VOICES_ST:
            known = ", ".join(VOICES_ST)
            return f"line {j} speaker {speaker!r} is not in the cast ({known})"
        text = line.get("text")
        if not isinstance(text, str) or not text.strip():
            return f"line {j} is missing 'text'"
        if not strip_event_markers(text).strip():
            return f"line {j} has no spoken text, only markers"
        stray = [m for m in _MARKER_SHAPE_RE.findall(text) if m not in EVENT_MARKERS]
        if stray:
            allowed = ", ".join(f"({m})" for m in EVENT_MARKERS)
            return (
                f"line {j} carries a marker no engine performs (({stray[0]})); events are {allowed}"
            )
        instruct = line.get("instruct")
        if instruct is not None:
            if not isinstance(instruct, str) or instruct not in DIRECTIONS:
                words = ", ".join(DIRECTIONS)
                return (
                    f"line {j} instruct {instruct!r} is not in the direction vocabulary ({words})"
                )
            directed += 1
    if directed > MAX_DIRECTED_LINES_PER_SCENE:
        return (
            f"{directed} directed lines; a scene may direct at most "
            f"{MAX_DIRECTED_LINES_PER_SCENE} (the identity drift is unmeasured)"
        )
    return ""


def _refused(detail: str) -> dict:
    return {"outcome": "REFUSED", "lines": None, "detail": detail[:300]}


def classify_scene(stdout: str, stderr: str, returncode: int) -> dict:
    """Map one scene-writer result to an outcome. Pure: no I/O.

    The taxonomy is orchestrate.classify_output's, unchanged - OK / REFUSED /
    AUTH / BLOCKED / ERROR - because the failure modes are the show-independent
    ones. Only the OK branch differs: the payload is `lines` rather than a
    `segment`, and the length floor measures their SUM.
    """
    obj = extract_last_json(stdout)
    if isinstance(obj, dict) and obj.get("ok") is True:
        lines = obj.get("lines")
        problem = _shape_problem(lines)
        if problem:
            return _refused(problem)
        text = scene_text(lines)
        if len(text) < MIN_SEGMENT_CHARS:
            # Too thin to be worth a chapter of its own, exactly as on the daily
            # show. Drop this one scene; the rest of the episode still ships.
            return _refused(f"scene too short ({len(text)} chars)")
        return {"outcome": "OK", "lines": lines, "detail": ""}
    if isinstance(obj, dict) and obj.get("ok") is False:
        return _refused(str(obj.get("reason", "")))
    blob = f"{stdout}\n{stderr}"
    if AUTH_RE.search(blob):
        return {"outcome": "AUTH", "lines": None, "detail": "401 / no usable credentials"}
    if POLICY_RE.search(blob):
        return {"outcome": "BLOCKED", "lines": None, "detail": "usage-policy classifier"}
    return {
        "outcome": "ERROR",
        "lines": None,
        "detail": (stderr or stdout or f"exit {returncode}").strip()[:300],
    }


# --- what the writer is told ------------------------------------------------

# The scene band measures the SUM of a scene's line texts, never one turn - the
# daily show's band-excludes-the-segue lesson, restated for dialogue. Eight or so
# turns at a hundred-odd characters each is a scene; the same 1400 characters in
# one breath is a monologue with names attached.
SCENE_BAND = (900, 1500)

# The show's fixed manifest identity. Every one of these keys is required for a
# web-only show and the failure mode of a missing one is silent: no ship_mode
# uploads to a Spotify show this show does not have, and no r2_key_prefix mints
# the daily digest's same-day <slug>.mp3 key in the shared bucket and overwrites
# it (#142).
SHOW_NAME = "Surface Tension"
R2_MANIFEST_NAME = "manifest-surface-tension.json"
R2_KEY_PREFIX = "surface-tension/"
SLUG_PREFIX = "surface-tension"
DESCRIPTION_FOOTER = (
    "Posts surfaced by vote on bubbles.town - every post links its blog above. "
    "More at cortech.online."
)

# The show's bundled assets: four cast clips (`<persona>.wav` + `.txt`) and this
# show's album art. Absolute, resolved off this file, because a scheduled run's CWD
# is arbitrary and CLAUDE_PLUGIN_ROOT is unset under the cron. Bundled rather than
# fetched for the same reason the house-voice clip is: a render must not depend on
# the network for a local artifact.
REFS_DIR = Path(__file__).resolve().parent / "refs"
COVER_IMAGE = REFS_DIR / "cover.jpg"

PLACEHOLDERS = (
    "<<TITLE>>",
    "<<URL>>",
    "<<SUMMARY>>",
    "<<CAST>>",
    "<<ROLES>>",
    "<<STANCES>>",
    "<<BOARD>>",
    "<<MIN_CHARS>>",
    "<<MAX_CHARS>>",
    "<<PERFORMANCE>>",
)

# Said in full rather than left as an absence. A writer handed an empty board and
# a role it was told it had will resolve the contradiction by inventing a call;
# the plan's `no_discussion` ordering exists so it never sees the contradiction.
BOARD_NO_CALLS = (
    "No comments on this post - the board is dark. There is no call to report, "
    "no switchboard turn in this scene, and no honest way to invent one."
)


def board_facts(post: dict, comment_entries: tuple | list = ()) -> dict:
    """The complete set of claims the discussion desk may make about this post.

    `comment_entries` is optional and usually empty: the count alone comes off
    the post's own feed entry (`slash:comments`) with NO EXTRA FETCH, which is
    what decides whether the turn renders at all. Supplying the parsed
    /feed/comments entries for this post is what unlocks instance provenance -
    and nothing else. There is no path here to what anyone said, because the
    feed does not carry it.
    """
    count = int(post.get("comment_count") or 0)
    hosts, positions = [], []
    for entry in comment_entries:
        host = _permalink_host(entry.get("link") or "")
        if host and host not in hosts:
            hosts.append(host)
        pos = _thread_position(entry.get("title") or "")
        if pos:
            positions.append(pos)
    return {"count": count, "hosts": hosts, "positions": sorted(positions)}


def board_brief(facts: dict) -> str:
    """Render the board facts as the writer's <<BOARD>> block."""
    if not facts.get("count"):
        return BOARD_NO_CALLS
    n = facts["count"]
    parts = [f"{n} comment{'s' if n != 1 else ''} on this post - that number is the one hard fact."]
    if facts.get("hosts"):
        parts.append(
            "Instances that called: "
            + ", ".join(facts["hosts"])
            + ". Describe an instance, never a person on it."
        )
    else:
        parts.append(
            "The instances are NOT available for this post - name no server, no "
            "location and no person."
        )
    parts.append("Nothing about what anyone said is available at any price.")
    return " ".join(parts)


# What each assigned side is told to do. The gloss is half the instruction: a bare
# "argue against" produces contrarianism, where "name what it has not earned"
# produces criticism.
_STANCE_GLOSS = {
    "for": ("FOR", "steelman it, whatever they privately think."),
    "against": ("AGAINST", "name what it has not earned."),
}


def _stance_block(scene: dict) -> str:
    """The STANCES instruction, with the desk collision resolved.

    The role square and the stance square are deliberately coprime, so they
    collide often: roughly one scene in four hands a side to the voice that is
    also working the board. Stating both would tell one voice to argue a
    position AND to report the board without ever holding one - and with no
    comments that voice does not speak at all, so the "side" would be argued by
    a silence. The same class of bug as #184, one layer along: the fix is to say
    the side is unassigned, not to quietly reassign it (that would break the
    determinism the squares exist to provide) and not to drop it silently (which
    reads as agreement).
    """
    desk = (scene.get("roles") or {}).get("switchboard")
    out = []
    for side, (verb, gloss) in _STANCE_GLOSS.items():
        voice = scene["stance"][side]
        if desk and voice == desk:
            out.append(
                f"- The {verb} side is UNASSIGNED this scene: {voice} is working "
                "the board, and the desk reports rather than argues. Nobody "
                "carries that side - do not hand it to another voice. The "
                "advocate and skeptic seats still do their own jobs."
            )
        else:
            out.append(f"- {voice} argues {verb} the post - {gloss}")
    return "\n".join(out)


def _rundown(scene: dict, has_calls: bool) -> list[dict]:
    """The turn entries this scene actually plays.

    With no calls the plan's precomputed `no_discussion` ordering replaces the
    full one, so the conditional role never reaches the writer as an assigned
    turn it then has to talk its way out of.
    """
    if has_calls:
        return list(scene["turn_order"])
    keep = set(scene["no_discussion"]["turn_order"])
    return [entry for entry in scene["turn_order"] if entry["role"] in keep]


def performance_rules(engine: str) -> str:
    """The <<PERFORMANCE>> block: what this engine lets a line carry, and the
    complete list of it. Conditional on the engine's CAPABILITIES, never on prose
    about a model: an engine without `events` reads (laugh) aloud as a word, and
    one without `direction` has an instruct refused rather than quietly ignored,
    so the writer is told exactly what will survive the gate."""
    spec = ENGINES[engine]
    out = []
    if spec.has("events"):
        markers = ", ".join(f"({m})" for m in EVENT_MARKERS)
        out.append(
            f"- Vocal events: a line's text may carry {markers}, written exactly so, "
            "where a real panelist would laugh or sigh - sparingly, and never instead "
            "of a reaction in words. Nothing else in parentheses: any other marker is "
            "refused."
        )
    else:
        out.append(
            "- No markers: this voice engine reads a parenthesised word aloud as a "
            "word. Nothing in parentheses, ever."
        )
    if spec.has("direction"):
        words = ", ".join(DIRECTIONS)
        out.append(
            f"- Direction: at most {MAX_DIRECTED_LINES_PER_SCENE} line in this scene may "
            f'carry a third field, "instruct", whose value is exactly one of: {words}. '
            "It tells the voice how to deliver that one turn. Spend it where the "
            "argument turns, or not at all; a second directed line, or any other word, "
            "is refused."
        )
    else:
        out.append(
            '- No direction: a line is {"speaker", "text"} and nothing else. An '
            '"instruct" field is refused on this engine.'
        )
    return "\n".join(out)


def fill_scene_prompt(
    template: str,
    post: dict,
    scene: dict,
    band: tuple[int, int] = SCENE_BAND,
    comment_entries: tuple | list = (),
    engine: str | None = None,
) -> str:
    """Substitute one post and its assigned scene into the writer's template.

    `engine` is the one the episode will render on (None: the renderer's default),
    and it decides the <<PERFORMANCE>> block — the same engine assemble_manifest
    must later be handed, or the writer is promised what the gate then refuses."""
    lo, hi = band
    facts = board_facts(post, comment_entries)
    rundown = _rundown(scene, bool(facts["count"]))
    roles = "\n".join(
        f"- {e['voice']} plays the {e['role']}: {e['does']} ({e['turns'][0]}-{e['turns'][1]} turns)"
        for e in rundown
    )
    roles += (
        f"\n- {rundown[0]['voice']} opens the scene; {rundown[-1]['voice']} gets the last word."
    )
    stances = _stance_block(scene)
    return (
        template.replace("<<TITLE>>", str(post.get("title", "")))
        .replace("<<URL>>", str(post.get("url", "")))
        .replace("<<SUMMARY>>", str(post.get("summary", "")) or "(no summary in the feed)")
        .replace("<<CAST>>", "\n".join(f"- {v}" for v in VOICES_ST))
        .replace("<<ROLES>>", roles)
        .replace("<<STANCES>>", stances)
        .replace("<<BOARD>>", board_brief(facts))
        .replace("<<MIN_CHARS>>", str(lo))
        .replace("<<MAX_CHARS>>", str(hi))
        .replace("<<PERFORMANCE>>", performance_rules(engine or TTS_ENGINE_QWEN3))
    )


# --- what the discussion desk is allowed to say -----------------------------
#
# Prose asks; this proves. The desk's failure mode is fabrication on a public
# feed - a synthesised voice attributing words or an identity to a real private
# person - and a prompt rule has no way to fail a run. Each check below maps to
# one thing the feed either does or does not support (spec section 4.4).

# `@user@host` and the bare `@user` a writer is likelier to produce. The handle
# is the ONE personal identifier /feed/comments exposes (it is in the permalink
# path), which makes it precisely the one the hard rule forbids.
_HANDLE_RE = re.compile(r"@[A-Za-z0-9_]{2,}(?:@[A-Za-z0-9.\-]+)?")

# A host-shaped token, used to catch an INVENTED instance. Deliberately not a
# strict domain grammar: this is a fabrication tripwire, not a parser.
_HOSTISH_RE = re.compile(r"\b[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)*\.[a-z]{2,}\b", re.I)

_NUMBER_WORDS = {
    "no": 0,
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}

# A count claim is a number sitting next to a call noun ("four calls", "3
# comments"). Scoped this tightly on purpose: flagging every integer in the line
# would refuse an honest "in overnight, around 2am".
_COUNT_CLAIM_RE = re.compile(
    r"\b([a-z]+|\d+)\s+(?:more\s+|further\s+|other\s+)?(calls?|comments?|callers?)\b",
    re.I,
)

_THREAD_POS_RE = re.compile(r"\((\d+)(?:st|nd|rd|th),\s*\d+\s*total\)")


def _permalink_host(url: str) -> str:
    """The instance host of a Fediverse permalink - never its handle."""
    m = re.match(r"https?://([^/]+)/", url or "")
    return m.group(1).lower() if m else ""


def _thread_position(title: str) -> int:
    m = _THREAD_POS_RE.search(title or "")
    return int(m.group(1)) if m else 0


def comments_for(post: dict, entries: tuple | list) -> list:
    """The /feed/comments entries belonging to `post`.

    Matched on the post title, which the comments feed repeats verbatim inside
    "New comment on: <title> (1st, 1 total)". The bubbles entry id would be a
    tighter key, but it survives only inside the post entry's HTML content and
    the candidate schema does not carry it.
    """
    title = (post.get("title") or "").strip()
    if not title:
        return []
    return [e for e in entries if title in (e.get("title") or "")]


def claimed_counts(text: str) -> set[int]:
    """Every call-count claim in a line, as integers. Words and digits both."""
    out: set[int] = set()
    for raw, _noun in _COUNT_CLAIM_RE.findall(text or ""):
        if raw.isdigit():
            out.add(int(raw))
        elif raw.lower() in _NUMBER_WORDS:
            out.add(_NUMBER_WORDS[raw.lower()])
    return out


def scene_violations(
    lines: list[dict],
    scene: dict,
    post: dict,
    comment_entries: tuple | list = (),
) -> list[str]:
    """Content rules the discussion desk must not break, as a list of problems.

    Empty means clean. Scoped to the switchboard's OWN turns for everything
    board-related - the panel arguing the post is not making claims about the
    board - except the handle rule, which applies to every line because no voice
    on this show has any business naming a private commenter.
    """
    problems: list[str] = []
    desk = (scene.get("roles") or {}).get("switchboard")
    facts = board_facts(post, comment_entries)
    for j, line in enumerate(lines):
        if not isinstance(line, dict):
            continue
        speaker, text = line.get("speaker"), str(line.get("text") or "")
        found = _HANDLE_RE.findall(text)
        if found:
            problems.append(f"line {j} speaks a Fediverse handle ({text[:60]!r})")
        if desk is None or speaker != desk:
            continue
        if not facts["count"]:
            problems.append(
                f"line {j} is a switchboard turn but the post has no comments — "
                "use the plan's no_discussion ordering"
            )
            continue
        bogus = claimed_counts(text) - {facts["count"]}
        if bogus:
            problems.append(
                f"line {j} claims a call count of {sorted(bogus)} on a "
                f"{facts['count']}-comment post"
            )
        for host in _HOSTISH_RE.findall(text):
            if host.lower() not in facts["hosts"]:
                problems.append(f"line {j} names an instance the feed does not support ({host})")
    return problems


# --- the engine gate (#201) --------------------------------------------------


def engine_violations(lines: list[dict], engine: str) -> list[str]:
    """What this scene asks of the engine that the engine cannot render, as a
    list of problems. Empty means clean.

    Only direction lives here. An event on an engine without `events` is not a
    violation but a strip (lines_for_engine): a stripped marker is the same line,
    where a dropped direction is a different performance that nobody would be
    told about — so it is refused, naming the engine, and never stripped.
    """
    if ENGINES[engine].has("direction"):
        return []
    return [
        f"line {j} is directed ({line.get('instruct')!r}), but engine {engine} cannot "
        "direct a voice"
        for j, line in enumerate(lines)
        if isinstance(line, dict) and line.get("instruct") is not None
    ]


def lines_for_engine(lines: list[dict], engine: str) -> list[dict]:
    """The lines as the engine will render them: event markers stripped on an
    engine without `events`, and each direction WORD expanded to the instruct the
    engine hears (DIRECTIONS). New objects; the writer's lines are never mutated.
    Refuses nothing — engine_violations is the refusal, and runs first."""
    spec = ENGINES[engine]
    out = []
    for line in lines:
        new = dict(line)
        if not spec.has("events"):
            new["text"] = strip_event_markers(str(new.get("text", "")))
        word = new.get("instruct")
        if word is not None:
            new["instruct"] = DIRECTIONS[word]
        out.append(new)
    return out


# --- the manifest ------------------------------------------------------------


def die(msg: str, code: int = 1) -> NoReturn:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def cast_map() -> dict[str, dict[str, str]]:
    """The manifest `cast`: persona -> that persona's recorded clip (#177).

    The speaker is the PERSONA, not the role. Roles rotate per scene
    (st_script_plan.scene_roles) while the manifest cast is one map for the whole
    episode, so keying it on roles would silently freeze the rotation the assign
    layer exists to produce. Phase 3 swapped the VALUES from the four bundled
    presets to recorded clips; the keys are what stayed put, which is why every
    scene written before this change still names a valid speaker.

    Each clip is a `ref_audio` clone, never VoiceDesign: the drift
    docs/durable-voices.md measures (~2.5% in pacing, audibly in timbre, run to
    run) would make a panel of four sound like a different panel every week. Clones
    run on the same base model a preset does, so four voices still cost one model
    load.
    """
    cast: dict[str, dict[str, str]] = {}
    for persona in VOICES_ST:
        clip = REFS_DIR / f"{persona.lower()}.wav"
        transcript = clip.with_suffix(".txt")
        for path in (clip, transcript):
            if not path.is_file():
                die(f"cast clip for {persona!r} is missing: {path}")
        cast[persona] = {"ref_audio": str(clip), "ref_text": transcript.read_text().strip()}
    return cast


def build_scene_segment(
    post: dict,
    scene: dict,
    lines: list[dict],
    comment_entries: tuple | list = (),
    engine: str = TTS_ENGINE_QWEN3,
) -> dict:
    """One post scene as a render.py segment: one chapter, one source_url, gated
    through the engine it will render on (#201)."""
    problem = _shape_problem(lines)
    if problem:
        die(f"scene for {post.get('url')!r}: {problem}")
    violations = scene_violations(lines, scene, post, comment_entries)
    violations += engine_violations(lines, engine)
    if violations:
        die(f"scene for {post.get('url')!r}: " + "; ".join(violations))
    return {
        "title": (str(post.get("title") or "The post"))[:120],
        "source_url": post.get("url"),
        "lines": lines_for_engine(lines, engine),
    }


def build_frame_segment(item: dict, engine: str = TTS_ENGINE_QWEN3) -> dict:
    """A non-story frame (ident, vote desk, rapid fire, sign-off).

    Carries `source_url: null` and an explicit title: without one render.py falls
    back to positional chapter names like "Segment 1" in the published timeline.
    Gated through the engine like a post scene: a frame is written in the main
    context, but it renders on the same engine.
    """
    lines = item.get("lines")
    problem = _shape_problem(lines)
    if problem:
        die(f"frame {item.get('title')!r}: {problem}")
    for j, line in enumerate(lines):
        if _HANDLE_RE.search(str(line.get("text") or "")):
            die(f"frame {item.get('title')!r} line {j} speaks a Fediverse handle")
    violations = engine_violations(lines, engine)
    if violations:
        die(f"frame {item.get('title')!r}: " + "; ".join(violations))
    title = str(item.get("title") or "").strip()
    if not title:
        die("a frame segment needs a title, or its chapter publishes as 'Segment N'")
    return {"title": title[:120], "source_url": None, "lines": lines_for_engine(lines, engine)}


def assemble_manifest(
    date_iso: str,
    title: str,
    summary: str,
    items: list[dict],
    engine: str | None = None,
) -> dict:
    """Build the render.py manifest for one Surface Tension episode.

    `items` is the episode's running order (spec section 5), each entry either a
    frame or a post scene. The order lives with the caller because the episode
    SHAPE is editorial; what lives here is every invariant a wrong one would
    breach.

    `engine` is the `tts_engine` the manifest will carry — None keeps the key
    absent, so the show renders on the renderer's default exactly as before (#201).
    Every scene and frame is gated through the resolved engine BEFORE it becomes a
    segment: events it cannot perform are stripped, direction it cannot render is
    refused. Moving the show to another engine is this one argument; the episode
    voice follows it, because an engine without presets refuses one.
    """
    resolved = engine or TTS_ENGINE_QWEN3
    if resolved not in ENGINES:
        die(f"tts_engine must be one of {list(ENGINES)} (got {engine!r})")
    spec = ENGINES[resolved]
    segments = [
        build_scene_segment(
            it["post"], it["plan"], it["lines"], it.get("comment_entries", ()), engine=resolved
        )
        if it.get("kind") == "scene"
        else build_frame_segment(it, engine=resolved)
        for it in items
    ]
    manifest = {
        # Display-only free text: `date` is what keys the slug and the guid (#128).
        "title": title,
        "summary": summary,
        "date": date_iso,
        # A preset where the engine has one: the daily show's narrator is not a
        # panelist. Every segment here is a `lines` scene, so this is only the
        # fallback render.py resolves for a segment carrying plain text — and since
        # #177 it is no longer any panelist's voice, since the cast are clones.
        # Nothing should ever hear it; a segment that does is a bug upstream of
        # here. An engine without presets (Breeze) refuses a preset name before the
        # model load, so there the fallback is the "house" clone — still nobody's.
        "voice": VOICES_ST[0] if spec.has("preset") else "house",
        "cast": cast_map(),
        # This show's own art, used verbatim instead of build_cover's generated
        # gradient (#164). show_name alone only renames the DAILY show's template,
        # and that template is what a podcast client renders as episode artwork.
        "cover_image": str(COVER_IMAGE),
        "ship_mode": "web",
        "show_name": SHOW_NAME,
        "r2_manifest_name": R2_MANIFEST_NAME,
        "r2_key_prefix": R2_KEY_PREFIX,
        "slug_prefix": SLUG_PREFIX,
        "description_footer_text": DESCRIPTION_FOOTER,
        "segments": segments,
    }
    if engine is not None:
        manifest["tts_engine"] = engine
    return manifest
