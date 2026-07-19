#!/usr/bin/env python3
"""
daily-podcast/render.py — dumb manifest -> episode driver.

Consumes a manifest.json that already contains the written segments, then:
  1. Picks a voice (random from preset list, unless overridden)
  2. Renders each segment via Qwen3-TTS (mlx-audio)
  3. Concatenates with auto-padded silences to satisfy Spotify's
     "max 3 chapters <30s" rule
  4. Loudnorm via ffmpeg
  5. Builds a date-stamped Pillow cover
  6. Builds timeline.json (chapter per segment + link companion when present)
  7. Builds HTML description (summary + timestamped chapters + source links)
  8. Uploads via save-to-spotify CLI, sets timeline, polls until READY
  9. Optionally publishes the mp3 + a manifest entry to Cloudflare R2 (for the
     cortech.online web feed) — additive, never blocks the run
 10. Updates ~/.config/daily-podcast/covered.json dedup log
 11. Appends one record to ~/.config/daily-podcast/runs.jsonl (across-runs observability)

Use --dry-run to skip upload/timeline/R2 calls (still writes mp3, cover, timeline.json,
and a "dry-run" run-log record).
Use --selftest (mutually exclusive with --manifest) for a pre-flight health check of
deps + credentials without a real run — recommended in an unattended scheduler's
pre-flight (`render.py --selftest || alert`).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

# --- constants -------------------------------------------------------------

VOICES = ["Ryan", "Aiden", "Ethan", "Chelsie"]
MODEL_ID = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"
VOICE_DESIGN_MODEL_ID = "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-bf16"
SAMPLE_RATE = 24000

# The locked "house" voice for the daily podcast.
#
# History: tuned through A/B iteration of VoiceDesign instructs (B1a → C → D → E → F),
# locked 2026-05-22 on the F2_human candidate (mature female, even prosody, bright but
# human, not performative). Originally driven by HOUSE_VOICE_INSTRUCT below; switched
# to ref_audio cloning on the same date to eliminate run-to-run voice drift.
#
# The reference clip is one good render of F2_human's instruct (~22s). For voice
# cloning, Qwen3 needs both the audio and a transcript of what was said.
#
# Resolution: user copies in ~/.config/daily-podcast/voices/ win; bundled defaults
# below are copied there on first run so plugin updates can't clobber user changes.
SCRIPT_DIR = Path(__file__).resolve().parent
BUNDLED_HOUSE_AUDIO = SCRIPT_DIR / "refs" / "house_voice.wav"
BUNDLED_HOUSE_TEXT = SCRIPT_DIR / "refs" / "house_voice.txt"

# Kept for reference (and for anyone who wants to re-derive a new house clip from
# VoiceDesign rather than ref_audio cloning). NOT used by the default house voice path.
HOUSE_VOICE_INSTRUCT = (
    "A female voice in her early forties speaking in an even tone. "
    "Low pitch variation, no host energy, no broadcast inflection, "
    "no dramatic emphasis. Bright but human, unobtrusive, not performative. "
    "Clear and natural. Resonant lower register."
)
TARGET_CHAPTER_MS = 30_500  # 30s + buffer; Spotify rejects <30s strict
MAX_SHORT_CHAPTERS = 3
DEFAULT_SILENCE_MS = 800
LAST_SILENCE_MS = 0  # no silence after the final segment
# Spotify caps an episode description at 4000 characters (Spotify Web API
# `description`/`html_description` field; same limit surfaces in Spotify for
# Podcasters episode show notes). Past the cap the upload silently truncates or
# rejects the summary, so build_timeline_and_description fits the HTML under it
# by dropping whole trailing chapter <p> blocks rather than cutting mid-tag.
SPOTIFY_SUMMARY_MAX_CHARS = 4000
# covered.json dedup-log retention. A daily run covers ~10 URLs, so the log
# would grow ~3.6k entries/year unbounded. 180 days is comfortably larger than
# the feed-curation lookback window (lookback_hours, default 24h — the only
# window in which dedup actually matters), and bounds the file at ~1800 entries.
COVERED_RETENTION_DAYS = 180
CONFIG_DIR = Path.home() / ".config" / "daily-podcast"
CONFIG_PATH = CONFIG_DIR / "config.json"
COVERED_PATH = CONFIG_DIR / "covered.json"
# Long-lived, workdir-independent record of an episode that uploaded but hasn't
# reached READY+dedup yet. Unlike the per-workdir uploaded.json marker, this lives
# in the config dir so a *different* next-day cron run (per-date workdir) can still
# recover it — closing the cross-day duplicate gap (#37). Written right after
# upload() succeeds, cleared only after dedup. covered.json stays the sole dedup
# source of truth: the in-flight log only ever *drives* a write into it.
INFLIGHT_PATH = CONFIG_DIR / "inflight.json"
# Append-only JSONL operational log: one record per render.py run (success,
# dry-run, or failure). Lives next to covered.json so a single `jq` over one file
# answers across-runs questions (which voice yesterday? which run failed? did LUFS
# drift?) without spelunking ephemeral workdirs. Append-only by contract — NEVER
# rewritten atomically (that would clobber history to a single line); see
# write_run_log. One line/day ≈ trivial size, so retention is the operator's job.
RUN_LOG_PATH = CONFIG_DIR / "runs.jsonl"
VOICES_DIR = CONFIG_DIR / "voices"
USER_HOUSE_AUDIO = VOICES_DIR / "house.wav"
USER_HOUSE_TEXT = VOICES_DIR / "house.txt"

# Base directory under which auto-created per-run workdirs live. On macOS
# tempfile.mkdtemp() places dirs under $TMPDIR (/var/folders/.../T/), NOT /tmp, so
# --prune-workdirs derives the scan root from gettempdir() rather than hardcoding
# /tmp (the issue's literal path would silently match nothing for auto workdirs).
# Module-level + patchable so the destructive prune logic can be tested against a
# throwaway tree instead of the real temp dir.
WORKDIR_PREFIX = "daily-podcast-"
TMP_BASE = Path(tempfile.gettempdir())

# --- helpers ---------------------------------------------------------------


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# Mutable per-run record, set by main() for the duration of a render (None at
# import time and during unit tests that call die()/run() directly). die() stashes
# its message here so the failure path in main() can write a complete run-log record
# — sys.exit carries only the exit code, not the diagnostic string. Gating every
# run-log write on this being non-None keeps direct die()/selftest calls from
# touching the real ~/.config/daily-podcast/runs.jsonl.
_RUN_CTX: dict[str, Any] | None = None


def die(msg: str, code: int = 1) -> None:
    log(f"error: {msg}")
    if _RUN_CTX is not None:
        _RUN_CTX["error_message"] = msg
    sys.exit(code)


# save-to-spotify writes its --json error payload to STDOUT (not stderr) and can
# append a human-readable update-check nag on a trailing line, so json.loads() over
# the whole stream raises "JSONDecodeError: Extra data". Parse only the first
# non-empty line to recover the JSON object without tripping on the nag. Returns the
# decoded value, or None when the first line isn't JSON. Never raises.
def _first_json_line(text: str) -> Any | None:
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None
    return None


# The structured error save-to-spotify wraps around an upstream API failure is a
# NESTED STRING, not an object (verified against 0.1.1, 2026-07-18):
#   {"error": "API error (429): {\"error_code\":\"RATE_LIMIT_EXCEEDED\",
#              \"reason\":\"capacity\",\"message\":\"You've reached the episode limit...\"}"}
# So recovering error_code/reason/message is two stages: parse the outer object, then
# strip the `API error (<code>): ` prefix off data["error"] and parse the remainder.
# Writing data["error"]["error_code"] would raise TypeError — always go through here.
_API_ERROR_PREFIX_RE = re.compile(r"^API error \([^)]*\):\s*")


def parse_s2s_error(stdout: str) -> dict[str, Any] | None:
    """Extract save-to-spotify's structured error from --json stdout, or None.

    Returns a normalized {"error_code", "reason", "message"} dict when stdout carries
    an error payload (any of the three may be None if only the outer string parsed),
    or None when stdout has no JSON `error` at all (e.g. an ffmpeg failure whose
    stdout is empty). Never raises — a parse failure at either stage falls back to a
    less-structured result rather than an exception, so callers can always branch."""
    data = _first_json_line(stdout)
    if not isinstance(data, dict) or "error" not in data:
        return None
    err = data["error"]
    if not isinstance(err, str):
        # Defensive: a future/object error shape — surface whatever it is as message.
        return {"error_code": None, "reason": None, "message": str(err)}
    inner = _API_ERROR_PREFIX_RE.sub("", err, count=1)
    try:
        parsed = json.loads(inner)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return {
            "error_code": parsed.get("error_code"),
            "reason": parsed.get("reason"),
            "message": parsed.get("message") or err,
        }
    # Only the outer string parsed — keep the human-readable line as the message.
    return {"error_code": None, "reason": None, "message": err}


def _command_failed_message(cmd: list[str], stdout: str, stderr: str) -> str:
    """Diagnostic for a failed subprocess. Prefers the structured error save-to-spotify
    writes to STDOUT (so a cap 429 no longer surfaces as an empty `stderr:`, issue
    #78); falls back to stderr for commands (ffmpeg, git) that report there."""
    parsed = parse_s2s_error(stdout)
    if parsed is not None:
        code = parsed.get("error_code")
        reason = parsed.get("reason")
        detail = parsed.get("message") or ""
        if code:
            tag = code if not reason else f"{code}/{reason}"
            detail = f"[{tag}] {detail}".rstrip()
        return f"command failed: {' '.join(cmd)}\n{detail}"
    return f"command failed: {' '.join(cmd)}\nstderr: {stderr}"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """Run a command, raising on failure with the command line in the message."""
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)
    except subprocess.CalledProcessError as e:
        die(_command_failed_message(cmd, e.stdout or "", e.stderr or ""))


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        die(
            f"missing {CONFIG_PATH}. Create it with: "
            '{"show_id": "spotify:show:...", "show_name": "...", "host_name": "..."}'
        )
    return json.loads(CONFIG_PATH.read_text())


def load_covered() -> dict[str, Any]:
    # Malformed covered.json should not abort a run: the dedup log is best-effort and
    # the headless prompt (prompts/daily.md) treats unparseable content as empty.
    if not COVERED_PATH.exists():
        return {}
    try:
        data = json.loads(COVERED_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        log(f"warn: {COVERED_PATH} unreadable/malformed, treating as empty")
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write_text(path: Path, text: str) -> None:
    """Write text to path atomically: temp file in the SAME dir, then os.replace.
    A crash mid-write leaves the prior file intact instead of a truncated one, and
    os.replace is a consistent atomic rename across platforms (unlike os.rename,
    which fails on Windows when the target exists)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        # On any failure/interrupt, drop the temp file so it can't masquerade as state.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _prune_covered(data: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Drop entries whose `date` is strictly older than COVERED_RETENTION_DAYS.

    Entries with a missing or non-ISO-date `date` are KEPT — we never lose dedup
    state on schema drift; an unparseable date is treated as "recent enough".
    Returns (pruned_dict, dropped_count). Pure: does not touch the filesystem.
    """
    cutoff = dt.date.today() - dt.timedelta(days=COVERED_RETENTION_DAYS)
    kept: dict[str, Any] = {}
    dropped = 0
    for url, entry in data.items():
        raw = entry.get("date") if isinstance(entry, dict) else None
        try:
            entry_date = dt.date.fromisoformat(raw) if isinstance(raw, str) else None
        except ValueError:
            entry_date = None  # malformed date string ("yesterday") — keep the entry
        if entry_date is not None and entry_date < cutoff:
            dropped += 1
            continue
        kept[url] = entry
    return kept, dropped


def save_covered(data: dict[str, Any]) -> None:
    # The dedup log is load-bearing for "don't re-upload the same URLs", so write it
    # atomically — a crash mid-write must not truncate it. Formatting preserved.
    #
    # Prune on write (not on load — load_covered returns the file as-is so the read
    # contract stays predictable). Pruning only drops entries OUTSIDE the retention
    # window, so the dedup invariant holds: any URL covered within the last
    # COVERED_RETENTION_DAYS (>> the curation lookback) is still recorded and won't
    # be re-podcasted. covered.json is still only written after poll_ready -> READY.
    pruned, dropped = _prune_covered(data)
    if dropped:
        log(f"pruned {dropped} covered.json entr(ies) older than {COVERED_RETENTION_DAYS}d")
    _atomic_write_text(COVERED_PATH, json.dumps(pruned, indent=2, sort_keys=True))


# --- run log (#18) ---------------------------------------------------------
#
# One JSONL record per run for across-runs observability. The schema is stable:
# every record carries the SAME key set (missing values are null, never absent) so
# the file parses cleanly line-by-line in jq/pandas across schema evolution.

# Stable record shape. main() copies this, fills it, and hands it to write_run_log
# on every terminal path (ready / dry-run / failed) so #21's loudnorm/prune slots
# never reshape #18's schema. Keep additions here null-by-default.
RUN_LOG_FIELDS: tuple[str, ...] = (
    "timestamp",  # ISO 8601 UTC
    "status",  # "ready" | "dry-run" | "failed"
    "episode_uri",
    "title",
    "voice",
    "voice_mode",
    "chapter_count",
    "duration_s",
    "segment_count",
    "workdir",
    "manifest_path",
    "error_message",  # only on failure
    "git_sha",  # of render.py (mtime fallback when not a git checkout)
    "loudnorm",  # {input_i, output_i, output_tp, output_lra} or null (#21)
    "pruned_workdirs",  # {count, freed_bytes} when --prune-workdirs ran, else null (#21)
    "pruned_episodes",  # [{episode_uri, created_at, title, status}] on a cap prune, else null (#78)
    "r2_status",  # "published" | "skipped" | "failed" or null pre-publish (#48)
    "resumed",
)


def _new_run_record() -> dict[str, Any]:
    """An all-null run-log record with the full, stable key set. Callers overwrite
    only the fields they know; everything else stays explicitly null so a parser
    never has to handle a missing key."""
    return dict.fromkeys(RUN_LOG_FIELDS, None)


def resolve_render_sha() -> str:
    """Best-effort identity of the running render.py: the repo's short git SHA when
    this is a git checkout, else `mtime:<epoch>` of the file. Never raises — it's
    observability metadata, not a gate. Lets the operator correlate a behavior change
    in runs.jsonl with a specific version of the renderer."""
    try:
        result = subprocess.run(
            ["git", "-C", str(SCRIPT_DIR), "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        sha = result.stdout.strip()
        if sha:
            return sha
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        pass
    try:
        return f"mtime:{int(Path(__file__).stat().st_mtime)}"
    except OSError:
        return "unknown"


def write_run_log(record: dict[str, Any]) -> None:
    """Append one JSON record as a line to runs.jsonl (#18). Append-only by contract
    — NEVER atomic-replace (that would truncate the log to a single line). Best-effort:
    a log-write failure is logged and swallowed, because by the time we write a "ready"
    record the episode has already shipped — observability must never sink a live run.
    Always stamps `timestamp` here so every record is consistently dated."""
    record = dict(record)
    record.setdefault("timestamp", None)
    if record.get("timestamp") is None:
        record["timestamp"] = dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(RUN_LOG_PATH, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as e:
        log(f"warn: could not append run log {RUN_LOG_PATH}: {e}")


def resolve_house_voice() -> tuple[Path, Path]:
    """
    Return (audio, text) paths for the house voice, copying bundled defaults to
    ~/.config/daily-podcast/voices/ on first run so user edits survive plugin updates.
    """
    if not USER_HOUSE_AUDIO.exists() or not USER_HOUSE_TEXT.exists():
        if not BUNDLED_HOUSE_AUDIO.exists() or not BUNDLED_HOUSE_TEXT.exists():
            die(
                f"bundled house voice missing: {BUNDLED_HOUSE_AUDIO} / {BUNDLED_HOUSE_TEXT}. "
                "Reinstall the plugin or provide your own at "
                f"{USER_HOUSE_AUDIO} + {USER_HOUSE_TEXT}."
            )
        VOICES_DIR.mkdir(parents=True, exist_ok=True)
        if not USER_HOUSE_AUDIO.exists():
            shutil.copy2(BUNDLED_HOUSE_AUDIO, USER_HOUSE_AUDIO)
            log(f"installed bundled house voice -> {USER_HOUSE_AUDIO}")
        if not USER_HOUSE_TEXT.exists():
            shutil.copy2(BUNDLED_HOUSE_TEXT, USER_HOUSE_TEXT)
            log(f"installed bundled house transcript -> {USER_HOUSE_TEXT}")
    return USER_HOUSE_AUDIO, USER_HOUSE_TEXT


def mp3_duration_ms(path: Path) -> int:
    from mutagen.mp3 import MP3

    return int(MP3(str(path)).info.length * 1000)


def resolve_voice(manifest: dict[str, Any]) -> tuple[str, str | None, str | None, str | None]:
    """
    Resolve voice precedence into (voice, voice_instruct, ref_audio, ref_text).

    Precedence (documented in SKILL.md and docs/durable-voices.md — keep in sync):
      1. `voice_instruct` in manifest → VoiceDesign mode; voice acts as a label only
         (becomes "custom" when the requested voice is the default "house" or "random")
      2. `voice: "house"` (default) → Base model + ref_audio clone of the bundled clip
      3. `voice: "random"` → random preset from VOICES
      4. `voice: "<preset>"` → that preset name (must be in VOICES)
    """
    voice_instruct = manifest.get("voice_instruct")
    ref_audio: str | None = None
    ref_text: str | None = None
    requested = manifest.get("voice", "house")
    if voice_instruct:
        voice = requested if requested not in ("random", "house") else "custom"
    elif requested == "house":
        voice = "house"
        house_audio, house_text = resolve_house_voice()
        ref_audio = str(house_audio)
        ref_text = house_text.read_text().strip()
    elif requested == "random":
        voice = random.choice(VOICES)
    elif requested in VOICES:
        voice = requested
    else:
        die(
            f"unknown voice: {requested}. Expected 'house', 'random', "
            f"one of {VOICES}, or set voice_instruct directly."
        )
    return voice, voice_instruct, ref_audio, ref_text


def resolve_voice_mode(voice_instruct: str | None, ref_audio: str | None) -> str:
    """
    The rendering engine actually used, independent of the `voice` label.

    The label can read "Ryan" while voice_instruct routes to VoiceDesign, so the
    label alone lies about what the listener hears. Operators read the SHIPPED line
    to catch voice regressions, so the mode is reported truthfully alongside it:
      - "clone"  : ref_audio cloning (the house voice)
      - "design" : VoiceDesign instruct
      - "preset" : a named Qwen3 preset voice
    Mirrors the clone-wins-over-design precedence in render_segments().
    """
    if ref_audio:
        return "clone"
    if voice_instruct:
        return "design"
    return "preset"


def resolve_cover_date(manifest: dict[str, Any]) -> str:
    """
    Date for the cover subtitle. Prefer the manifest's ISO `date` so re-rendering a
    dated manifest reproduces its original date (archive / back-fill workflows);
    fall back to the wall clock when absent. A present-but-unparseable date is fatal.
    """
    raw = manifest.get("date")
    if not raw:
        return dt.date.today().strftime("%B %-d, %Y")
    try:
        return dt.date.fromisoformat(raw).strftime("%B %-d, %Y")
    except (ValueError, TypeError):
        die(f"manifest.date must be ISO YYYY-MM-DD, got: {raw!r}")


# --- input safety ----------------------------------------------------------


def validate_manifest(manifest: dict[str, Any]) -> None:
    """
    Fail fast (via die) on a malformed manifest BEFORE the ~15s model load, naming
    the offending field. Structural safety net for hand-authored manifests or any
    caller that bypassed the skill writer. Pure: no I/O, no mutation.
    """
    if not isinstance(manifest, dict):
        die("manifest must be a JSON object")

    for field in ("title", "summary"):
        val = manifest.get(field)
        if not isinstance(val, str) or not val.strip():
            die(f"manifest '{field}' is required and must be a non-empty string")

    segments = manifest.get("segments")
    if not isinstance(segments, list) or not segments:
        die("manifest 'segments' is required and must be a non-empty list")
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            die(f"manifest segment[{i}] must be an object")
        if not isinstance(seg.get("text"), str):
            die(f"manifest segment[{i}] missing required field 'text'")
        if not seg["text"].strip():
            die(f"manifest segment[{i}] field 'text' must be non-empty")
        for opt in ("title", "source_title"):
            if seg.get(opt) is not None and not isinstance(seg[opt], str):
                die(f"manifest segment[{i}].{opt} must be a string")
        url = seg.get("source_url")
        if url is not None and not (
            isinstance(url, str) and url.startswith(("http://", "https://"))
        ):
            die(f"manifest segment[{i}].source_url must be an http(s) URL (got {url!r})")

    voice = manifest.get("voice")
    if voice is not None:
        if manifest.get("voice_instruct"):
            # With voice_instruct set, resolve_voice treats `voice` as a free-form
            # label (SKILL.md) — only require it to be a string, don't gate on presets.
            if not isinstance(voice, str):
                die("manifest 'voice' must be a string")
        else:
            allowed = ["house", "random", *VOICES]
            if voice not in allowed:
                shown = "{" + ", ".join(f'"{v}"' for v in allowed) + "}"
                die(f"manifest 'voice' must be one of {shown} or unset (got {voice!r})")
    for field in ("voice_instruct", "show_id"):
        if manifest.get(field) is not None and not isinstance(manifest[field], str):
            die(f"manifest '{field}' must be a string")
    if manifest.get("date"):  # treat "" as absent, matching resolve_cover_date
        try:
            dt.date.fromisoformat(manifest["date"])
        except (ValueError, TypeError):
            die(f"manifest 'date' must be ISO YYYY-MM-DD (got {manifest['date']!r})")
    if manifest.get("raw_text") is not None and not isinstance(manifest["raw_text"], bool):
        die("manifest 'raw_text' must be a boolean")


# Bare URLs, markdown code fences, and leading heading markers — characters that
# TTS reads badly. Compiled once; normalize_for_tts runs per segment.
_URL_RE = re.compile(r"https?://[^\s)\]>—–]+")  # stop at ws, brackets, em/en dash
_CODE_BLOCK_RE = re.compile(r"(```|~~~).*?\1", flags=re.DOTALL)  # backtick or tilde fence
_HEADING_RE = re.compile(r"^[ \t]{0,3}#+[ \t]*", flags=re.MULTILINE)  # any leading-# run
_SMART_QUOTES = {"“": '"', "”": '"', "‘": "'", "’": "'"}


def normalize_for_tts(text: str) -> str:
    """
    Strip TTS-hostile characters from spoken text — defense in depth at the rendering
    boundary, since the skill writer is *supposed* to do this but external manifests
    may not. Pure. Removes: em/en dashes -> hyphen, smart quotes -> ASCII, code
    fences + inline backticks, leading markdown heading markers, and bare URLs.
    Deliberately leaves emoji, numbers, abbreviations, and identifiers like
    "CLAUDE.md" alone — those are stylistic and the script writer's job (see #19).
    """
    # URLs first, with a boundary-aware pattern (stops at whitespace, brackets, and
    # em/en dashes) so a URL flanked by an em dash can't swallow the next word. Must
    # precede the dash->hyphen step, which would turn that boundary into a plain char
    # the greedy URL match would run straight through.
    text = _URL_RE.sub("", text)
    text = text.replace("—", "-").replace("–", "-")  # em / en dash
    for smart, plain in _SMART_QUOTES.items():
        text = text.replace(smart, plain)
    text = _CODE_BLOCK_RE.sub("", text)  # whole fenced blocks (fences + content)
    text = text.replace("`", "")  # stray inline backticks
    text = _HEADING_RE.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)  # collapse runs left by stripped tokens
    return text.strip()


def _prep_segment_text(text: str, raw_text: bool) -> str:
    """Strip + (unless raw_text) normalize one segment's text for the TTS model."""
    text = text.strip()
    return text if raw_text else normalize_for_tts(text)


# --- per-segment TTS cache (#9) --------------------------------------------
#
# TTS is the dominant cost of a run (minutes), so a crash on segment 9 of 12
# shouldn't re-pay segments 1-8. Each rendered seg_NN.mp3 gets a seg_NN.json
# sidecar recording the cache key; a re-run with the SAME --workdir reuses any
# segment whose key still matches and re-renders only the rest. The cache is
# workdir-scoped on purpose (a fresh --workdir = a fresh render), so it can't
# leak across unrelated episodes.


def _ref_audio_fingerprint(ref_audio: str | None) -> str | None:
    """SHA256 of the ref-audio file's bytes, or None when not cloning. Folding the
    bytes (not just the path) into the cache key means re-recording the house clip
    invalidates every clone-mode segment even though the path is unchanged."""
    if not ref_audio:
        return None
    return hashlib.sha256(Path(ref_audio).read_bytes()).hexdigest()


def _segment_cache_key(
    text: str,
    voice_mode: str,
    voice: str,
    ref_fingerprint: str | None,
    ref_text: str | None,
) -> str:
    """Content hash identifying one rendered segment. Any input that changes the
    audio the model would produce changes the key:
      - `text`        : the (already prepped/normalized) spoken text
      - `voice_mode`  : clone / design / preset — the engine actually used
      - `voice`       : preset name, or the VoiceDesign instruct in design mode
      - `ref_fingerprint` : hash of the ref-audio bytes (clone mode only)
      - `ref_text`    : the clone transcript (clone mode only)
    Serialized through json so field boundaries can't collide (e.g. "a"+"bc" vs
    "ab"+"c"). Pure; no I/O."""
    payload = json.dumps(
        {
            "text": text,
            "mode": voice_mode,
            "voice": voice,
            "ref_fingerprint": ref_fingerprint,
            "ref_text": ref_text,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_hit(workdir: Path, i: int, key: str) -> bool:
    """A segment is reusable iff its mp3 exists AND its sidecar records this exact
    key. A bare mp3 with no/mismatched sidecar (older run, partial write, changed
    script) is NOT trusted — content identity is then unknown, so we re-render."""
    mp3 = workdir / f"seg_{i:02d}.mp3"
    sidecar = workdir / f"seg_{i:02d}.json"
    if not mp3.exists() or not sidecar.exists():
        return False
    try:
        meta = json.loads(sidecar.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    return isinstance(meta, dict) and meta.get("key") == key


# --- audio rendering -------------------------------------------------------


def render_segments(
    segments: list[dict],
    voice: str,
    workdir: Path,
    voice_instruct: str | None = None,
    ref_audio: str | None = None,
    ref_text: str | None = None,
    raw_text: bool = False,
) -> list[Path]:
    """
    Render each segment text to an mp3 in workdir; return list of mp3 paths.

    Three voice modes:
    - `ref_audio` set (+ `ref_text`): voice cloning via Base model + generate(ref_audio=...)
    - `voice_instruct` set: VoiceDesign model + generate_voice_design(instruct=...)
    - Otherwise: Base model with `voice` as a preset name (Ryan/Aiden/Ethan/Chelsie)

    `ref_audio` takes precedence if both are set.

    Per-segment cache (#9): each seg_NN.mp3 carries a seg_NN.json sidecar with a
    content-hash key (text + voice settings). On a re-run with the same --workdir,
    any segment whose key matches is reused as-is and only the rest are rendered;
    if *every* segment is cached the model load is skipped entirely. The mono-44.1k
    invariant and strict 1:1 segment<->source mapping are unchanged — a cache hit
    returns the byte-identical mp3 a fresh render would have produced.
    """
    use_clone = bool(ref_audio)
    use_design = bool(voice_instruct) and not use_clone
    mode = "clone" if use_clone else ("design" if use_design else "preset")
    # In design mode the instruct is what shapes the voice, so it must be part of
    # the key; otherwise the voice label is. Resolve the ref-audio fingerprint once.
    key_voice = voice_instruct if use_design else voice
    ref_fingerprint = _ref_audio_fingerprint(ref_audio)

    # Pass 1 (no model needed): prep text + compute keys + classify hit/miss.
    prepped: list[str] = []
    keys: list[str] = []
    cached: list[bool] = []
    for i, seg in enumerate(segments, start=1):
        text = _prep_segment_text(seg["text"], raw_text)
        if not text:
            die(f"segment {i} has empty text")
        key = _segment_cache_key(text, mode, key_voice or "", ref_fingerprint, ref_text)
        prepped.append(text)
        keys.append(key)
        cached.append(_cache_hit(workdir, i, key))

    n_hits = sum(cached)
    if n_hits:
        log(f"cache: {n_hits}/{len(segments)} segment(s) reusable from {workdir}")

    # Load the model only if at least one segment is a miss. A fully-cached re-run
    # pays nothing for the ~15s model load (acceptance criterion in #9).
    model = None
    if n_hits < len(segments):
        model_id = VOICE_DESIGN_MODEL_ID if use_design else MODEL_ID
        log(f"loading {model_id}...")
        t0 = time.time()
        import numpy as np
        import soundfile as sf
        from mlx_audio.tts.utils import load_model

        model = load_model(model_id)
        log(f"  model loaded in {time.time() - t0:.1f}s")
    else:
        log("cache: all segments cached, skipping model load")

    paths: list[Path] = []
    for idx in range(len(segments)):
        i = idx + 1
        text = prepped[idx]
        mp3 = workdir / f"seg_{i:02d}.mp3"
        if cached[idx]:
            log(f"[{i}/{len(segments)}] cache hit (voice={voice}, mode={mode}), reusing {mp3.name}")
            paths.append(mp3)
            continue
        log(f"[{i}/{len(segments)}] rendering ({len(text)} chars, voice={voice}, mode={mode})...")
        t0 = time.time()
        if use_clone:
            results = list(
                model.generate(
                    text=text,
                    language="English",
                    ref_audio=ref_audio,
                    ref_text=ref_text,
                )
            )
        elif use_design:
            results = list(
                model.generate_voice_design(
                    text=text,
                    language="English",
                    instruct=voice_instruct,
                )
            )
        else:
            results = list(
                model.generate(
                    text=text,
                    voice=voice,
                    language="English",
                )
            )
        audio = np.concatenate([np.array(r.audio) for r in results])
        wav = workdir / f"seg_{i:02d}.wav"
        sf.write(wav, audio, SAMPLE_RATE)
        # convert to mp3 at 44.1k mono so concat is clean
        run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(wav),
                "-ar",
                "44100",
                "-ac",
                "1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "192k",
                str(mp3),
            ]
        )
        # Write the sidecar only AFTER the mp3 is on disk, so a crash between the
        # two never records a cache hit for a half-written segment. _atomic_write_text
        # ensures the sidecar itself can't be torn either.
        _atomic_write_text(workdir / f"seg_{i:02d}.json", json.dumps({"key": keys[idx]}))
        dur_s = len(audio) / SAMPLE_RATE
        elapsed = time.time() - t0
        log(f"  -> {dur_s:.2f}s in {elapsed:.1f}s ({dur_s / elapsed:.1f}x rt)")
        paths.append(mp3)
    return paths


def plan_silences(seg_paths: list[Path]) -> list[int]:
    """
    Return silence_ms[i] = silence AFTER segment i. Last entry is LAST_SILENCE_MS.

    Strategy:
      - Start with DEFAULT_SILENCE_MS between every pair, 0 after last.
      - Compute provisional chapter durations (seg + trailing silence).
      - Last chapter has no trailing silence; if short, count it as short.
      - While more than MAX_SHORT_CHAPTERS are short, find the shortest
        non-last chapter and bump its trailing silence to hit TARGET_CHAPTER_MS.
      - Give up if all non-last chapters have already been padded and we're
        still over budget (means too many tiny segments — script needs work).
    """
    n = len(seg_paths)
    seg_ms = [mp3_duration_ms(p) for p in seg_paths]
    silence = [DEFAULT_SILENCE_MS] * n
    silence[-1] = LAST_SILENCE_MS

    def chapter_ms() -> list[int]:
        return [seg_ms[i] + silence[i] for i in range(n)]

    def short_indices() -> list[int]:
        return [i for i, c in enumerate(chapter_ms()) if c < 30_000]

    # Cap on silence padding; refuse to insert >12s gaps (would sound broken)
    MAX_SILENCE_MS = 12_000

    while len(short_indices()) > MAX_SHORT_CHAPTERS:
        candidates = [i for i in short_indices() if i < n - 1 and silence[i] < MAX_SILENCE_MS]
        if not candidates:
            die(
                f"can't satisfy chapter rule: {len(short_indices())} short chapters, "
                f"max {MAX_SHORT_CHAPTERS}, and no more padding room. "
                "Rewrite short segments to be ≥600 chars."
            )
        # Pick the chapter that needs the least padding to clear the bar
        candidates.sort(key=lambda i: (silence[i] - (TARGET_CHAPTER_MS - seg_ms[i])))
        i = candidates[0]
        needed = TARGET_CHAPTER_MS - seg_ms[i]
        silence[i] = min(max(needed, silence[i] + 500), MAX_SILENCE_MS)

    log(f"chapter ms: {chapter_ms()}")
    log(f"silence ms: {silence}")
    log(f"short chapters: {short_indices()} (limit {MAX_SHORT_CHAPTERS})")
    return silence


def write_silence(workdir: Path, ms: int) -> Path:
    """Generate (or reuse) a silence mp3 of the given duration."""
    p = workdir / f"silence_{ms}ms.mp3"
    if p.exists():
        return p
    secs = ms / 1000
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            f"{secs:.3f}",
            "-q:a",
            "9",
            "-acodec",
            "libmp3lame",
            str(p),
        ]
    )
    return p


# ffmpeg's loudnorm filter, with print_format=json, emits a measurement block to
# stderr: a `[Parsed_loudnorm_0 @ ...]` line followed by a JSON object with
# input_i/output_i/output_tp/output_lra etc. We grab the LAST {...} block (robust to
# other bracketed log lines preceding it).
_LOUDNORM_JSON_RE = re.compile(r"\{[^{}]*\}", flags=re.DOTALL)
# The subset surfaced in the final JSON + run log: measured integrated loudness and
# true-peak / loudness-range, in vs out. -16 LUFS mono is Spotify's target, so
# output_i drifting is the audio-QA signal #21 wants in the run log.
_LOUDNORM_KEYS = ("input_i", "output_i", "output_tp", "output_lra")


def parse_loudnorm(stderr: str) -> dict[str, Any] | None:
    """Parse ffmpeg loudnorm's print_format=json measurement block from stderr.

    Returns {input_i, output_i, output_tp, output_lra} as floats, or None when the
    block is absent/unparseable (e.g. ffmpeg changed its output format, or a value is
    the literal "-inf"/"inf" on silent audio). Pure; never raises — a parse miss must
    NOT fail a run, it just means loudnorm is recorded as null."""
    if not stderr:
        return None
    blocks = _LOUDNORM_JSON_RE.findall(stderr)
    for raw in reversed(blocks):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or "output_i" not in data:
            continue
        out: dict[str, Any] = {}
        for k in _LOUDNORM_KEYS:
            try:
                val = float(data[k])
            except (KeyError, ValueError, TypeError):
                val = None
            # A missing key, or a non-finite value ("-inf"/"inf"/"nan" on silent input)
            # → null. float("-inf") parses fine but json.dumps emits a bare `Infinity`,
            # which is NOT valid JSON and would make the runs.jsonl line unparseable by
            # jq/pandas. Null keeps the run-log schema strictly JSON-clean.
            out[k] = val if (val is not None and math.isfinite(val)) else None
        return out
    return None


def concat_and_normalize(
    seg_paths: list[Path], silences_ms: list[int], workdir: Path
) -> tuple[Path, dict[str, Any] | None]:
    """Build concat list, encode raw, loudnorm. Return (final mp3 path, loudnorm dict).

    The loudnorm dict is the parsed LUFS measurement (#21) or None on a parse miss.
    `print_format=json` only makes the (already single-pass) loudnorm filter REPORT
    its measurements on stderr — it does not change the produced audio."""
    parts: list[Path] = []
    for i, seg in enumerate(seg_paths):
        parts.append(seg)
        if silences_ms[i] > 0:
            parts.append(write_silence(workdir, silences_ms[i]))

    concat_list = workdir / "concat.txt"
    concat_list.write_text("\n".join(f"file '{p}'" for p in parts) + "\n")

    raw = workdir / "episode_raw.mp3"
    final = workdir / "episode.mp3"
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-ar",
            "44100",
            "-ac",
            "1",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(raw),
        ]
    )
    loudnorm_proc = run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(raw),
            "-af",
            "loudnorm=print_format=json",
            "-ar",
            "44100",
            "-ac",
            "1",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(final),
        ]
    )
    loudnorm = parse_loudnorm(loudnorm_proc.stderr if loudnorm_proc else "")
    if loudnorm is None:
        log("warn: could not parse loudnorm measurement from ffmpeg stderr")
    else:
        log(f"loudnorm: input_i={loudnorm.get('input_i')} output_i={loudnorm.get('output_i')}")
    log(f"final episode: {mp3_duration_ms(final) / 1000:.1f}s")
    return final, loudnorm


# --- cover -----------------------------------------------------------------


def resolve_font() -> str:
    """
    Resolve a TrueType font for the cover, in order:
      1. DAILY_PODCAST_FONT env override (wins over everything)
      2. macOS Futura — keeps the default macOS install byte-identical
      3. common Linux fallbacks (DejaVu, Liberation)
    die() with an actionable message if none exist — never let Pillow raise a bare
    FileNotFoundError. Cover rendering is pure Pillow and must run off macOS (Linux
    CI); only the TTS path is Apple-Silicon-locked.
    """
    candidates = [
        os.environ.get("DAILY_PODCAST_FONT"),
        "/System/Library/Fonts/Supplemental/Futura.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for path in candidates:
        if path and Path(path).exists():
            return path
    die(
        "no cover font found. Install Futura (macOS) or DejaVu/Liberation (Linux), "
        "or set DAILY_PODCAST_FONT=/path/to/font.ttf"
    )


def build_cover(out_path: Path, show_name: str, date_str: str, title_hint: str) -> None:
    """Pillow cover: gradient + show name + date + short subtitle."""
    from PIL import Image, ImageDraw, ImageFont

    W = H = 1400
    top = (28, 24, 50)
    bot = (220, 110, 60)

    bg = Image.new("RGB", (W, H), top)
    d = ImageDraw.Draw(bg)
    for y in range(H):
        t = y / (H - 1)
        d.line(
            [(0, y), (W, y)],
            fill=(
                int(top[0] + (bot[0] - top[0]) * t),
                int(top[1] + (bot[1] - top[1]) * t),
                int(top[2] + (bot[2] - top[2]) * t),
            ),
        )

    # Bottom darkening for legibility
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for y in range(750, H):
        a = int((y - 750) / (H - 750) * 190)
        od.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")

    d = ImageDraw.Draw(bg)
    font_path = resolve_font()
    title_font = ImageFont.truetype(font_path, 130)
    sub_font = ImageFont.truetype(font_path, 54)
    date_font = ImageFont.truetype(font_path, 44)
    small_font = ImageFont.truetype(font_path, 36)

    def shadowed(xy, text, font, fill=(255, 255, 255)):
        x, y = xy
        d.text((x + 3, y + 3), text, font=font, fill=(0, 0, 0))
        d.text((x, y), text, font=font, fill=fill)

    def wrap_to_lines(text: str, font, max_width: int) -> list[str]:
        """Greedy word-wrap so each line's pixel width <= max_width."""
        words = text.split()
        lines, cur = [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if d.textlength(trial, font=font) <= max_width:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines or [text]

    MARGIN = 60
    MAX_TITLE_WIDTH = 1400 - 2 * MARGIN
    title_lines = wrap_to_lines(show_name, title_font, MAX_TITLE_WIDTH)
    # If wrapping produced too many lines, downsize the title font
    while len(title_lines) > 2 and title_font.size > 70:
        title_font = ImageFont.truetype(font_path, title_font.size - 10)
        title_lines = wrap_to_lines(show_name, title_font, MAX_TITLE_WIDTH)

    # Top label (also truncate if needed)
    shadowed((MARGIN, 70), show_name.upper(), small_font, fill=(255, 220, 180))

    # Big title — anchored to bottom block, one line per element
    line_h = title_font.size + 10
    title_block_h = line_h * len(title_lines)
    title_y = 1400 - 400 - title_block_h  # leave room for date + hint
    for i, line in enumerate(title_lines):
        shadowed((MARGIN, title_y + i * line_h), line, title_font)

    # Date subtitle directly under title
    date_y = title_y + title_block_h + 10
    shadowed((MARGIN, date_y), date_str, sub_font, fill=(240, 220, 200))

    # Short title hint (truncated, only if it fits)
    hint = title_hint[:48] + ("..." if len(title_hint) > 48 else "")
    shadowed((MARGIN, date_y + 80), hint, date_font, fill=(220, 200, 180))

    bg.save(out_path, "JPEG", quality=88, optimize=True)


# --- timeline + description ------------------------------------------------


def build_timeline_and_description(
    segments: list[dict],
    seg_paths: list[Path],
    silences_ms: list[int],
    summary: str,
    episode_mp3: Path,
) -> tuple[dict, str]:
    items: list[dict] = []
    chapters: list[tuple[int, str, str | None]] = []  # (ms, title, url)
    cursor = 0
    for i, seg in enumerate(segments):
        title = seg.get("title") or seg.get("source_title") or f"Segment {i + 1}"
        url = seg.get("source_url")
        items.append({"chapter": {"title": title, "start_time_ms": cursor}})
        dur = mp3_duration_ms(seg_paths[i])
        if url:
            link_start = cursor + max(1000, int(dur * 0.40))
            link_dur = min(6000, max(2000, dur - 2000))
            items.append(
                {
                    "link": {
                        "start_time_ms": link_start,
                        "duration_ms": link_dur,
                        "url": url,
                    }
                }
            )
        chapters.append((cursor, title, url))
        cursor += dur + silences_ms[i]

    final_ms = mp3_duration_ms(episode_mp3)
    last_ch = max(c[0] for c in chapters)
    if last_ch >= final_ms:
        die(f"last chapter at {last_ch}ms >= episode duration {final_ms}ms")

    # Description. title/url come from untrusted feed metadata, so escape them — a
    # stray quote/&/< would otherwise corrupt the markup (a "'" closes the href).
    # summary is HTML-by-contract (the user authored it), so it passes through raw.
    # The timeline JSON above carries the raw strings; escaping is description-only.
    parts = [f"<p>{summary}</p>"]
    for ms, title, url in chapters:
        ts = f"({ms // 60000}:{(ms % 60000) // 1000:02d})"
        safe_title = html.escape(title, quote=True)
        if url:
            safe_url = html.escape(url, quote=True)
            parts.append(f'<p>{ts} - {safe_title} - <a href="{safe_url}">source</a></p>')
        else:
            parts.append(f"<p>{ts} - {safe_title}</p>")
    description = "".join(parts)

    # Fit under Spotify's summary cap WITHOUT breaking the HTML: each list entry
    # is a self-contained <p>…</p>, so drop whole chapter blocks from the end
    # (longest-suffix-first) until it fits — never cut mid-tag, never ellipsize a
    # block. parts[0] is the summary <p> and is always preserved (it's the hook).
    # The timeline JSON above is untouched: the audio chapters still exist, only
    # the show-notes listing is trimmed.
    if len(description) > SPOTIFY_SUMMARY_MAX_CHARS:
        kept = list(parts)
        while len(kept) > 1 and len("".join(kept)) > SPOTIFY_SUMMARY_MAX_CHARS:
            kept.pop()
        dropped = len(parts) - len(kept)
        log(
            f"description {len(description)} chars > {SPOTIFY_SUMMARY_MAX_CHARS} cap: "
            f"dropped {dropped} trailing chapter block(s) from show notes "
            "(timeline/audio chapters unaffected)"
        )
        description = "".join(kept)

    return {"items": items}, description


# --- upload + poll ---------------------------------------------------------


# --- episode-cap auto-prune (#78) -----------------------------------------
#
# When `save-to-spotify upload` fails because the show is at its episode cap, the
# renderer can prune the oldest episodes and retry the upload ONCE. Deleting a
# published episode is IRREVERSIBLE (episode metadata is immutable; there is no
# undelete), so every guard below mirrors the --prune-workdirs invariant and must be
# preserved: opt-in (default off); only on a confirmed cap 429; bounded by a hard
# per-run ceiling; scoped to the configured show_id; never touching an in-flight
# (NOT_READY) or this-run episode; skipping any item with an unparseable created_at;
# and no deletes at all under --dry-run.
#
# Note: a pruned episode's covered.json entries would point at a now-dead episode_uri,
# but dedup's job is "don't re-cover this URL", which stays correct — so covered.json
# is deliberately left untouched here (out of scope per #78).
CAP_ERROR_CODE = "RATE_LIMIT_EXCEEDED"
CAP_ERROR_REASON = "capacity"
# Only these two states are ever prune-eligible. Matching FAILED must be EXPLICIT, not
# "anything != READY": an episode still transcoding is NOT_READY, and a broad match
# could delete an in-flight episode from a concurrent run.
PRUNABLE_STATUSES = ("READY", "FAILED")


def _is_cap_error(parsed: dict[str, Any] | None) -> bool:
    """True only for a confirmed episode-cap 429 — gate on the inner structured
    error_code AND reason, never on a substring of the human-readable message."""
    if not parsed:
        return False
    return parsed.get("error_code") == CAP_ERROR_CODE and parsed.get("reason") == CAP_ERROR_REASON


def _prune_config(config: dict[str, Any] | None) -> tuple[bool, int]:
    """Resolve (enabled, max_prune_per_run) from config. Absent key -> disabled, so a
    run with the key missing behaves exactly as before (part-1 diagnostic, no prune).
    max_prune_per_run defaults to 1; a value <= 0 is refused by the caller."""
    cfg = config or {}
    enabled = bool(cfg.get("auto_prune_episodes", False))
    try:
        max_prune = int(cfg.get("max_prune_per_run", 1))
    except (TypeError, ValueError):
        max_prune = 0  # unparseable -> refuse (caller treats <= 0 as "don't prune")
    return enabled, max_prune


def _parse_created_at(s: Any) -> dt.datetime | None:
    """Strict ISO-8601 -> aware datetime, or None when missing/malformed. Unlike
    _parse_pubdate (which maps unparseable to datetime.min for sorting), this returns
    None so an item with no confirmable age is SKIPPED, never guessed to be oldest."""
    if not isinstance(s, str) or not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def select_episodes_to_prune(
    episodes: list[dict[str, Any]], max_prune: int, now: dt.datetime
) -> list[dict[str, Any]]:
    """Choose up to `max_prune` episodes to delete, in deletion order. Pure + total.

    Tiered selection (cheapest first): FAILED episodes — which have no playable audio
    yet still count against the cap — before oldest-by-created_at READY episodes.
    An episode is a candidate only if its status is exactly READY or FAILED, its
    created_at parses, and it was created strictly before `now` (so this run's own
    upload and any concurrent run's just-created episode are excluded). NOT_READY /
    unknown statuses and unparseable timestamps are never selected."""
    if max_prune <= 0:
        return []
    candidates: list[tuple[int, dt.datetime, dict[str, Any]]] = []
    for ep in episodes:
        if not isinstance(ep, dict):
            continue
        status = ep.get("status")
        if status not in PRUNABLE_STATUSES:
            continue
        created = _parse_created_at(ep.get("created_at"))
        if created is None or created >= now:
            continue
        tier = 0 if status == "FAILED" else 1  # FAILED first, then READY
        candidates.append((tier, created, ep))
    candidates.sort(key=lambda c: (c[0], c[1]))
    return [ep for _, _, ep in candidates[:max_prune]]


def _list_episodes(show_id: str) -> list[dict[str, Any]]:
    """Episodes for the configured show, scoped by --show-id (never a last-created-show
    default). Parses the first JSON line to survive the update-check nag."""
    result = run(["save-to-spotify", "--json", "episodes", "--show-id", show_id])
    data = _first_json_line(result.stdout)
    eps = data.get("episodes") if isinstance(data, dict) else None
    return eps if isinstance(eps, list) else []


def _delete_episode(episode_uri: str) -> None:
    episode_id = episode_uri.removeprefix("spotify:episode:")
    run(["save-to-spotify", "episodes", "delete", episode_id])


def prune_episodes_for_capacity(
    show_id: str,
    config: dict[str, Any] | None,
    *,
    dry_run: bool,
    record: dict[str, Any] | None = None,
    now: dt.datetime | None = None,
) -> int:
    """Free episode-cap slots by deleting the selected episodes. Returns the number
    actually deleted (0 when disabled, misconfigured, nothing eligible, or --dry-run).

    Guards (all load-bearing — a wrong deletion is unrecoverable):
      - opt-in: no-op unless auto_prune_episodes is true;
      - refuses max_prune_per_run <= 0 (mirrors --prune-workdirs N <= 0);
      - deletes at most max_prune_per_run per run;
      - scopes the episode list to the configured show_id;
      - --dry-run logs the plan but deletes nothing;
      - logs every deletion (uri + created_at + title) to stdout and into the run
        record so a surprise deletion is always traceable after the fact."""
    enabled, max_prune = _prune_config(config)
    if not enabled:
        return 0
    if max_prune <= 0:
        log(f"auto-prune refused: max_prune_per_run {max_prune} must be a positive count")
        return 0
    now = now or dt.datetime.now(dt.timezone.utc)
    episodes = _list_episodes(show_id)
    victims = select_episodes_to_prune(episodes, max_prune, now)
    if not victims:
        log("auto-prune: no eligible episodes to delete (need a READY/FAILED, dated, older one)")
        return 0

    deleted: list[dict[str, Any]] = []
    for ep in victims:
        uri = ep.get("episode_uri") or ""
        rec = {
            "episode_uri": uri,
            "created_at": ep.get("created_at"),
            "title": ep.get("title"),
            "status": ep.get("status"),
        }
        desc = f"{uri} ({rec['status']}, {rec['created_at']}, {rec['title']!r})"
        if dry_run:
            log(f"[auto-prune] dry-run: would delete {desc}")
            continue
        if not uri:
            continue  # can't delete without an episode_uri; skip rather than guess
        log(f"[auto-prune] deleting {desc} to free a cap slot")
        _delete_episode(uri)
        deleted.append(rec)

    if record is not None and deleted:
        existing = record.get("pruned_episodes") or []
        record["pruned_episodes"] = existing + deleted
    return len(deleted)


def upload(
    episode_mp3: Path,
    title: str,
    description: str,
    cover: Path,
    show_id: str,
    *,
    config: dict[str, Any] | None = None,
    dry_run: bool = False,
    record: dict[str, Any] | None = None,
) -> str:
    """Upload the episode and return its episode_uri.

    On a confirmed cap 429 (RATE_LIMIT_EXCEEDED / capacity) with auto_prune_episodes
    enabled, prune the oldest episode(s) and retry the upload ONCE (never a loop). Any
    other failure — or a retry that also 429s — dies with the structured diagnostic
    from parse_s2s_error (part 1), so a permanent cap is distinguishable from a
    transient flake."""
    cmd = [
        "save-to-spotify",
        "--json",
        "upload",
        str(episode_mp3),
        "--title",
        title,
        "--summary",
        description,
        "--show-id",
        show_id,
        "--image",
        str(cover),
    ]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        parsed = parse_s2s_error(e.stdout or "")
        enabled, _ = _prune_config(config)
        if _is_cap_error(parsed) and enabled:
            pruned = prune_episodes_for_capacity(show_id, config, dry_run=dry_run, record=record)
            if pruned > 0:
                log(f"auto-prune freed {pruned} slot(s); retrying upload once")
                try:
                    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
                except subprocess.CalledProcessError as e2:
                    # Retry also failed — fail with the diagnostic, never a second prune.
                    die(_command_failed_message(cmd, e2.stdout or "", e2.stderr or ""))
                return _parse_upload_result(result.stdout)
        die(_command_failed_message(cmd, e.stdout or "", e.stderr or ""))
    return _parse_upload_result(result.stdout)


def _parse_upload_result(stdout: str) -> str:
    """episode_uri from a successful upload. The success payload is single-line JSON
    (json.loads over the whole stream has worked since 0.1.1); keep that parse."""
    data = json.loads(stdout)
    if "error" in data:
        die(f"upload error: {data['error']}")
    return data["episode_uri"]


def set_timeline(episode_id: str, timeline_path: Path) -> None:
    result = run(
        [
            "save-to-spotify",
            "--json",
            "timeline",
            "set",
            "--episode-id",
            episode_id,
            "--from-file",
            str(timeline_path),
        ]
    )
    data = json.loads(result.stdout)
    if "error" in data:
        die(f"timeline set error: {data['error']}")


def poll_ready(episode_id: str, timeout_s: int = 600) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        result = run(
            [
                "save-to-spotify",
                "--json",
                "episodes",
                "status",
                episode_id,
            ]
        )
        data = json.loads(result.stdout)
        r = data.get("readiness", "")
        log(f"  status: {r}")
        if r == "READY":
            return
        if r == "FAILED":
            die("episode processing FAILED")
        time.sleep(15)
    die(f"episode not READY after {timeout_s}s")


def _save_dedup(segments: list[dict], episode_uri: str) -> None:
    """Mark every segment's source_url as covered by this episode. Idempotent:
    re-writing the same keys is a no-op, which is what makes resume safe."""
    covered = load_covered()
    today_iso = dt.date.today().isoformat()
    for seg in segments:
        url = seg.get("source_url")
        if url:
            covered[url] = {"date": today_iso, "episode_uri": episode_uri}
    save_covered(covered)


def _segment_urls(segments: list[dict]) -> list[str]:
    """Every non-null source_url, in segment order. These are exactly the keys
    _save_dedup writes, so the in-flight log records the same set it must cover."""
    return [seg["source_url"] for seg in segments if seg.get("source_url")]


def _write_inflight(*, episode_uri: str, title: str, workdir: Path, source_urls: list[str]) -> None:
    """Record an uploaded-but-not-yet-deduped episode in the workdir-independent
    in-flight log. Written right after upload() succeeds (the same moment as the
    workdir uploaded.json marker) and read on the NEXT run — even from a different
    per-date workdir — so the cron's cross-day duplicate gap is closed (#37). The
    stored workdir lets recovery re-run the server tail if its artifacts survive;
    source_urls let recovery mark the URLs covered even if they don't."""
    _atomic_write_text(
        INFLIGHT_PATH,
        json.dumps(
            {
                "episode_uri": episode_uri,
                "title": title,
                "workdir": str(workdir),
                "source_urls": source_urls,
            },
            indent=2,
        ),
    )


def _load_inflight() -> dict[str, Any] | None:
    """The in-flight record, or None when absent/unreadable. A malformed log is
    treated as None (not fatal) — mirroring load_covered's best-effort contract —
    rather than wedging every future run on a corrupt file."""
    if not INFLIGHT_PATH.exists():
        return None
    try:
        data = json.loads(INFLIGHT_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        log(f"warn: {INFLIGHT_PATH} unreadable/malformed, treating as no in-flight episode")
        return None
    return data if isinstance(data, dict) and data.get("episode_uri") else None


def _clear_inflight() -> None:
    """Remove the in-flight log once its episode has been deduped. Idempotent: a
    missing file is a no-op (a crash may have already cleared it)."""
    try:
        INFLIGHT_PATH.unlink()
    except FileNotFoundError:
        pass


def _recover_inflight() -> None:
    """Reconcile a leftover in-flight episode BEFORE the current run renders anything.

    The failure this closes: a prior run's upload() succeeded but the process died
    before dedup, so its URLs never reached covered.json. With per-date workdirs the
    next cron run can't see the workdir uploaded.json marker, so it would re-curate
    and re-ship those same URLs as a duplicate. The in-flight log is workdir-
    independent, so this run can finish the job.

    Order is load-bearing for the "crash during recovery leaves the log intact"
    guarantee: re-run the server tail (only if the prior workdir + timeline survive)
    -> mark URLs covered -> THEN clear the log. dedup is the source of truth; the log
    is only ever cleared once those URLs are durably in covered.json. covered.json is
    still only written here AFTER the episode is (or already was) READY."""
    rec = _load_inflight()
    if rec is None:
        return
    episode_uri = rec["episode_uri"]
    urls = [u for u in rec.get("source_urls", []) if isinstance(u, str)]
    log(f"in-flight recovery: found leftover episode {episode_uri} ({len(urls)} url(s))")

    # If the prior workdir + timeline still exist, finish the server tail so a
    # genuinely pending episode reaches READY. If they're gone (tmp cleared), skip
    # the tail and just mark covered — the episode is already uploaded, and the only
    # job left that matters for dedup is keeping curation from re-selecting its URLs.
    wd = Path(rec["workdir"]) if rec.get("workdir") else None
    timeline_path = wd / "timeline.json" if wd else None
    if timeline_path and timeline_path.exists():
        episode_id = episode_uri.removeprefix("spotify:episode:")
        log(f"in-flight recovery: re-running timeline set + poll for {episode_uri}")
        set_timeline(episode_id, timeline_path)
        poll_ready(episode_id)
    else:
        log("in-flight recovery: prior workdir/timeline gone; marking URLs covered only")

    if urls:
        covered = load_covered()
        today_iso = dt.date.today().isoformat()
        for url in urls:
            covered[url] = {"date": today_iso, "episode_uri": episode_uri}
        save_covered(covered)
    _clear_inflight()
    log("in-flight recovery: complete")


def _resume(
    workdir: Path,
    marker: Path,
    segments: list[dict],
    title: str,
    manifest: dict[str, Any],
    record: dict[str, Any] | None = None,
) -> int:
    """
    Resume a run whose upload already succeeded (uploaded.json present). Skip TTS,
    cover, and upload; reuse the workdir artifacts and re-run only the idempotent
    tail: set_timeline -> poll_ready -> R2 publish -> dedup. This recovers the common
    failure — a poll_ready timeout where the episode is actually live and Spotify was
    just slow — without re-uploading a duplicate.

    R2 back-fill (#40): the resume tail now also publishes to R2, mirroring the fresh
    path, so an episode that first failed at poll_ready and was later recovered still
    lands on the web feed. It MUST preserve the resume invariant that `_resume` never
    calls `load_config` (pinned by test_resume_skips_upload_and_runs_idempotent_tail):
    R2 config is resolved env-only via `maybe_publish_r2({}, ...)`. The publish is
    additive + non-fatal exactly as on the fresh path — it cannot block the dedup
    write below or change the exit code.

    `record`, when given, is the shared run-log record (#18) populated as the resume
    succeeds so the JSONL log captures resumed runs identically to fresh ones.
    """
    try:
        data = json.loads(marker.read_text())
        episode_uri = data["episode_uri"]
    except (json.JSONDecodeError, OSError, KeyError) as e:
        die(f"{marker} unreadable or missing episode_uri ({e}); cannot resume")

    episode_id = episode_uri.removeprefix("spotify:episode:")
    log(f"resume: upload already complete ({episode_uri}); skipping render + upload")

    episode_mp3 = workdir / "episode.mp3"
    cover = workdir / "cover.jpg"
    timeline_path = workdir / "timeline.json"
    for path, name in (
        (episode_mp3, "episode.mp3"),
        (cover, "cover.jpg"),
        (timeline_path, "timeline.json"),
    ):
        if not path.exists():
            die(f"workdir has uploaded.json but missing {name}; cannot resume safely")

    set_timeline(episode_id, timeline_path)
    log("timeline set; polling for READY...")
    poll_ready(episode_id)

    timeline = json.loads(timeline_path.read_text())

    # R2 back-fill (#40), after READY and before dedup — mirrors the fresh path's
    # ordering. Env-only config ({}) keeps the resume path's no-load_config invariant.
    # description.html was written to the workdir on the original fresh run (before the
    # upload that produced uploaded.json), so it is present whenever a current resume
    # is valid; an older workdir predating it degrades to a skipped publish rather than
    # aborting the already-live episode's idempotent tail. additive + non-fatal: this
    # never blocks the dedup write below or changes the exit code.
    desc_path = workdir / "description.html"
    if desc_path.exists():
        r2_status = maybe_publish_r2(
            {},
            episode_mp3=episode_mp3,
            cover=cover,
            timeline=timeline,
            manifest=manifest,
            description=desc_path.read_text(),
            episode_uri=episode_uri,
        )
    else:
        log("[r2] resume: description.html absent in workdir, skipping R2 back-fill")
        r2_status = R2_SKIPPED

    _save_dedup(segments, episode_uri)
    # This episode reached READY+dedup, so any in-flight record for it is now stale.
    _clear_inflight()

    chapter_count = sum(1 for it in timeline.get("items", []) if "chapter" in it)
    duration_s = mp3_duration_ms(episode_mp3) / 1000
    if record is not None:
        record.update(
            status="ready",
            episode_uri=episode_uri,
            title=data.get("title", title),
            voice=data.get("voice"),
            voice_mode=data.get("voice_mode"),
            chapter_count=chapter_count,
            duration_s=duration_s,
            segment_count=len(segments),
            workdir=str(workdir),
            r2_status=r2_status,
            resumed=True,
        )
    print(
        json.dumps(
            {
                "status": "ready",
                "episode_uri": episode_uri,
                "title": data.get("title", title),
                "voice": data.get("voice"),
                "voice_mode": data.get("voice_mode"),
                "chapter_count": chapter_count,
                "duration_s": duration_s,
                "r2_status": r2_status,
                "resumed": True,
            },
            indent=2,
        )
    )
    return 0


# --- r2 publish ------------------------------------------------------------
#
# After Spotify (the canonical artifact) confirms READY, also publish the mp3 + a
# manifest entry to a Cloudflare R2 bucket. cortech.online reads that manifest at
# build time and renders /podcast/ + an iTunes RSS feed (schmug/cortech.online#131).
#
# This is strictly additive: R2 is never allowed to block the dedup-log write or
# fail the run. A missing config no-ops; any publish error warns and continues.
# Runs on BOTH the fresh path and the resume path (#40): each publishes after
# READY and before the dedup-log write. The resume path stays config-free — it
# passes an empty config so R2 settings resolve from env / secrets.json only and
# never call load_config (pinned by test_resume_skips_upload_and_runs_idempotent_tail).


def slugify(title: str, date: str) -> str:
    """Lowercase kebab slug matching the consumer schema's ^[a-z0-9-]+$. It keys both
    the R2 object (<slug>.mp3) and the /podcast/<slug>/ permalink, so it must be
    stable for a given title: re-rendering the same title upserts, never duplicates."""
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if not s:
        s = f"episode-{date}"
    return s[:80].strip("-")


def resolve_pubdate(manifest: dict[str, Any]) -> str:
    """ISO 8601 publish timestamp. A manifest with an explicit `date` reproduces that
    date (archive / back-fill re-renders stay stable, mirroring resolve_cover_date);
    otherwise stamp the wall clock."""
    raw = manifest.get("date")
    if raw:
        return f"{raw}T12:00:00+00:00"
    return dt.datetime.now(dt.timezone.utc).isoformat()


def chapters_from_timeline(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    """Reconstruct the consumer-side chapters[] ({title, start_ms, source_url}) from a
    rendered timeline. build_timeline_and_description emits each chapter immediately
    followed by its optional `link` companion, so a link attaches to the most recent
    chapter — the same strict 1:1 segment<->source invariant the renderer enforces.
    Reading it back from the timeline means the fresh and resume shapes can never drift."""
    chapters: list[dict[str, Any]] = []
    for item in timeline.get("items", []):
        if "chapter" in item:
            ch = item["chapter"]
            chapters.append(
                {
                    "title": ch.get("title", ""),
                    "start_ms": ch.get("start_time_ms", 0),
                    "source_url": None,
                }
            )
        elif "link" in item and chapters:
            chapters[-1]["source_url"] = item["link"].get("url")
    return chapters


def build_manifest_entry(
    *,
    slug: str,
    title: str,
    description: str,
    summary: str,
    pubdate: str,
    mp3_url: str,
    mp3_bytes: int,
    duration_s: float,
    chapters: list[dict[str, Any]],
    spotify_uri: str | None = None,
    cover_url: str | None = None,
    explicit: bool = False,
) -> dict[str, Any]:
    """One entry conforming to cortech.online's episodeSchema. Pure — the caller
    supplies byte size and duration so this stays trivially testable. Optional fields
    are omitted (not null) when absent to keep the manifest tidy; the schema treats
    both the same.

    `description` is Spotify-flavored HTML (`<p>summary</p>` + one `<p>(mm:ss) - title
    - <a>source</a></p>` per chapter); `summary` is the clean lead blurb the user
    authored, surfaced separately (issue #45) so web/RSS consumers render prose
    without HTML-stripping the description. `summary` is **HTML-by-contract**, same as
    build_timeline_and_description treats it (render.py: "summary is HTML-by-contract
    (the user authored it)") — the consumer should still escape it as untrusted text,
    not trust it as guaranteed-plain. `description` and `chapters[]` are unchanged by
    this addition; it is purely additive."""
    entry: dict[str, Any] = {
        "slug": slug,
        "title": title,
        "description": description,
        "summary": summary,
        "pubDate": pubdate,
        "mp3_url": mp3_url,
        "mp3_bytes": int(mp3_bytes),
        "duration_s": round(duration_s, 3),
        "chapters": chapters,
        "explicit": explicit,
    }
    if spotify_uri:
        entry["spotify_uri"] = spotify_uri
    if cover_url:
        entry["cover_url"] = cover_url
    return entry


def _parse_pubdate(s: Any) -> dt.datetime:
    """Best-effort ISO 8601 -> aware datetime for sorting; unparseable sorts oldest."""
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def upsert_manifest(
    entries: list[dict[str, Any]], entry: dict[str, Any], cap: int = 200
) -> list[dict[str, Any]]:
    """Insert `entry`, replacing any existing entry with the same slug, sort
    newest-first by pubDate, and cap to the most recent `cap`. Pure; the atomic PUT
    happens in the caller. Newest-first + cap keeps the consumer's build-time fetch
    bounded (issue #33)."""
    slug = entry.get("slug")
    kept = [e for e in entries if isinstance(e, dict) and e.get("slug") != slug]
    kept.append(entry)
    kept.sort(key=lambda e: _parse_pubdate(e.get("pubDate", "")), reverse=True)
    return kept[:cap]


def _load_r2_secrets() -> dict[str, str]:
    """R2 credentials: env first (simplest for cron), then an optional 0600
    secrets.json fallback. Credentials never live in config.json (meant to be
    shareable) or in git."""
    keys = ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ACCOUNT_ID")
    out = {k: os.environ[k] for k in keys if os.environ.get(k)}
    if all(k in out for k in keys):
        return out
    secrets_path = CONFIG_DIR / "secrets.json"
    if secrets_path.exists():
        try:
            data = json.loads(secrets_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            log(f"[r2] {secrets_path} unreadable ({e}); ignoring")
            data = {}
        for k in keys:
            if k not in out and isinstance(data.get(k), str):
                out[k] = data[k]
    return out


def load_r2_config(config: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve the full R2 publish config, or None if anything required is missing
    (the publish then no-ops). Bucket + public base URL come from config.json, with
    env overrides; credentials come from env / secrets.json only. Pure-ish: reads
    env + the secrets file, no network."""
    secrets = _load_r2_secrets()
    required = {
        "account_id": secrets.get("R2_ACCOUNT_ID"),
        "access_key": secrets.get("R2_ACCESS_KEY_ID"),
        "secret_key": secrets.get("R2_SECRET_ACCESS_KEY"),
        "bucket": os.environ.get("R2_BUCKET") or config.get("r2_bucket"),
        "public_base_url": os.environ.get("R2_PUBLIC_BASE_URL") or config.get("r2_public_base_url"),
    }
    if any(not v for v in required.values()):
        return None
    return required


def r2_client(cfg: dict[str, Any]):
    """boto3 S3 client pointed at R2's S3-compatible endpoint. Imported lazily so the
    renderer never hard-requires boto3 unless R2 is actually configured — mirrors the
    mutagen import inside mp3_duration_ms."""
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=f"https://{cfg['account_id']}.r2.cloudflarestorage.com",
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"],
        region_name="auto",
    )


def _r2_put(
    client, bucket: str, key: str, body: bytes, content_type: str, cache_control: str | None = None
) -> None:
    kwargs: dict[str, Any] = {
        "Bucket": bucket,
        "Key": key,
        "Body": body,
        "ContentType": content_type,
    }
    if cache_control:
        kwargs["CacheControl"] = cache_control
    client.put_object(**kwargs)


def _r2_get_manifest(client, bucket: str, key: str = "manifest.json") -> list[dict[str, Any]]:
    """Current manifest array, or [] when the object doesn't exist yet (first run).
    A genuinely missing key returns []; any *other* error (auth, network, 5xx)
    propagates so the caller aborts instead of clobbering history with a one-entry
    file. Malformed JSON is treated as empty, matching the consumer's tolerance."""
    from botocore.exceptions import ClientError

    try:
        resp = client.get_object(Bucket=bucket, Key=key)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code", "") in ("NoSuchKey", "404"):
            return []
        raise
    try:
        data = json.loads(resp["Body"].read())
    except (json.JSONDecodeError, ValueError) as e:
        log(f"[r2] existing manifest unparseable, starting fresh: {e}")
        return []
    return data if isinstance(data, list) else []


def fire_pages_hook(url: str) -> None:
    """POST the Cloudflare Pages deploy hook so cortech.online rebuilds. Best-effort:
    a timeout or error is logged, never raised — the episode is already published."""
    try:
        req = urllib.request.Request(url, data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 (configured URL)
            log(f"[r2] pages deploy hook fired: {resp.status}")
    except Exception as e:
        log(f"[r2] pages deploy hook failed (non-fatal): {e}")


def resolve_pages_hook_url(config: dict[str, Any]) -> str | None:
    """Cloudflare Pages deploy-hook URL, first non-empty wins: env →
    secrets.json → config.json. The scheduled launchd/cron run never inherits the
    interactive shell env, so the hook's durable home is the 0600 secrets.json
    (where the R2 credentials already fall back); config.json's
    `pages_deploy_hook_url` is a shareable-file convenience — looser, since the
    hook can trigger site rebuilds. None when unset everywhere — the hook then
    no-ops, the original env-only behaviour. Never raises: the publish tail is
    best-effort (see fire_pages_hook), so resolution must warn-and-continue too."""
    env = os.environ.get("PAGES_DEPLOY_HOOK_URL")
    if env:
        return env
    secrets_path = CONFIG_DIR / "secrets.json"
    if secrets_path.exists():
        try:
            data = json.loads(secrets_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            log(f"[r2] {secrets_path} unreadable ({e}); ignoring")
            data = {}
        if isinstance(data, dict):
            from_secrets = data.get("PAGES_DEPLOY_HOOK_URL")
            if isinstance(from_secrets, str) and from_secrets:
                return from_secrets
    from_config = config.get("pages_deploy_hook_url")
    if isinstance(from_config, str) and from_config:
        return from_config
    return None


# 3-state R2 publish outcome (#48). The not-configured no-op and a configured-but-
# failed publish used to both return a bare False, so the caller couldn't tell an
# intentional skip from a silent web-feed miss. These strings travel through the
# final JSON line and the unattended SHIPPED stdout (prompts/daily.md) as
# r2=ok / r2=skipped / r2=FAILED. None of them ever fail the run — Spotify stays
# canonical — but FAILED is now visible to an operator scanning run output.
R2_PUBLISHED = "published"
R2_SKIPPED = "skipped"
R2_FAILED = "failed"


def maybe_publish_r2(
    config: dict[str, Any],
    *,
    episode_mp3: Path,
    cover: Path | None,
    timeline: dict[str, Any],
    manifest: dict[str, Any],
    description: str,
    episode_uri: str | None,
) -> str:
    """Publish the episode mp3, optional cover, and a manifest entry to R2. Returns a
    3-state result (#48): R2_PUBLISHED on success, R2_SKIPPED when R2 isn't configured
    (a benign no-op), R2_FAILED when configured-but-the-upload-errored. Never raises:
    Spotify is the canonical artifact, so a broken R2 must not fail the run or roll
    back the dedup log — the distinction only surfaces in the run's output so an
    operator can spot a silent web-feed miss (the failure R2_SKIPPED used to hide)."""
    cfg = load_r2_config(config)
    if cfg is None:
        log("[r2] not configured, skipping")
        return R2_SKIPPED
    try:
        client = r2_client(cfg)
        date_iso = manifest.get("date") or dt.date.today().isoformat()
        title = manifest["title"]
        slug = slugify(title, date_iso)
        base = cfg["public_base_url"].rstrip("/")
        immutable = "public, max-age=31536000, immutable"

        # mp3 first: the manifest must never reference an object that isn't up yet.
        mp3_key = f"{slug}.mp3"
        _r2_put(
            client,
            cfg["bucket"],
            mp3_key,
            episode_mp3.read_bytes(),
            "audio/mpeg",
            cache_control=immutable,
        )
        mp3_url = f"{base}/{mp3_key}"

        # Cover is best-effort: a flaky image upload must not sink the episode.
        cover_url: str | None = None
        if cover and Path(cover).exists():
            try:
                cover_key = f"{slug}.jpg"
                _r2_put(
                    client,
                    cfg["bucket"],
                    cover_key,
                    Path(cover).read_bytes(),
                    "image/jpeg",
                    cache_control=immutable,
                )
                cover_url = f"{base}/{cover_key}"
            except Exception as e:
                log(f"[r2] cover upload failed (non-fatal): {e}")

        entry = build_manifest_entry(
            slug=slug,
            title=title,
            description=description,
            # validate_manifest guarantees a non-empty summary on the input manifest,
            # so this is always present (#45). HTML-by-contract; see build_manifest_entry.
            summary=manifest["summary"],
            pubdate=resolve_pubdate(manifest),
            mp3_url=mp3_url,
            mp3_bytes=episode_mp3.stat().st_size,
            duration_s=mp3_duration_ms(episode_mp3) / 1000,
            chapters=chapters_from_timeline(timeline),
            spotify_uri=episode_uri,
            cover_url=cover_url,
        )

        # manifest last + single atomic PUT. Object PUTs replace wholesale (no torn
        # writes like a local file), so the read-modify-write is safe without a temp
        # key. no-cache keeps the consumer's build-time fetch from reading a stale CDN
        # copy right after a deploy-hook rebuild.
        entries = upsert_manifest(_r2_get_manifest(client, cfg["bucket"]), entry)
        _r2_put(
            client,
            cfg["bucket"],
            "manifest.json",
            json.dumps(entries, indent=2).encode(),
            "application/json",
            cache_control="no-cache",
        )
        log(f"[r2] published {mp3_url} (manifest now {len(entries)} entries)")

        hook = resolve_pages_hook_url(config)
        if hook:
            fire_pages_hook(hook)
        return R2_PUBLISHED
    except Exception as e:
        log(f"[r2] publish failed (non-fatal, Spotify episode is live): {e}")
        return R2_FAILED


# --- workdir retention (#21) -----------------------------------------------
#
# ⚠️ DESTRUCTIVE. prune_workdirs() deletes directories. Every guard below exists to
# make a wrong deletion impossible:
#   - only directories whose name starts with WORKDIR_PREFIX, sitting DIRECTLY under
#     TMP_BASE (no recursion, no globbing into unrelated trees);
#   - symlinks are skipped (never follow a link out of TMP_BASE);
#   - older-than is by mtime against a positive age in days; N<=0 is refused so the
#     flag can never mean "delete everything";
#   - the ACTIVE workdir is excluded by resolved path, so a same-day/per-date resume
#     can't delete the dir it is currently rendering into;
#   - best-effort: any error deleting one dir is logged and skipped, never fatal.


def prune_workdirs(older_than_days: int, *, exclude: Path | None = None) -> dict[str, Any] | None:
    """Delete stale auto-created workdirs (TMP_BASE/daily-podcast-*) older than
    `older_than_days`, never touching `exclude` (the active run's workdir). Returns
    {count, freed_bytes} describing what was removed, or None when the flag is a no-op
    (older_than_days <= 0). Best-effort: a failure on one dir is logged and skipped."""
    if older_than_days <= 0:
        # Refuse a 0/negative age — that would select every workdir and delete all
        # of them, including ones from concurrent or just-finished runs.
        log(f"--prune-workdirs {older_than_days} ignored (must be a positive day count)")
        return None
    if not TMP_BASE.exists():
        return {"count": 0, "freed_bytes": 0}

    cutoff = time.time() - older_than_days * 86400
    exclude_resolved = exclude.resolve() if exclude else None
    count = 0
    freed = 0
    for entry in TMP_BASE.iterdir():
        # Name + shape gate: only our own auto-workdirs, real dirs, never symlinks.
        if not entry.name.startswith(WORKDIR_PREFIX):
            continue
        if entry.is_symlink() or not entry.is_dir():
            continue
        try:
            if exclude_resolved is not None and entry.resolve() == exclude_resolved:
                continue  # never delete the directory this run is using
            if entry.stat().st_mtime >= cutoff:
                continue  # younger than the retention window — keep
            size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
            shutil.rmtree(entry)
        except OSError as e:
            log(f"warn: could not prune {entry}: {e}")
            continue
        count += 1
        freed += size
    if count:
        log(f"pruned {count} stale workdir(s) (~{freed} bytes) older than {older_than_days}d")
    return {"count": count, "freed_bytes": freed}


# --- selftest (#21) --------------------------------------------------------


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    status = "PASS" if ok else "FAIL"
    log(f"  [{status}] {name}: {detail}")
    return {"name": name, "ok": ok, "detail": detail}


def run_selftest(load_model: bool = False) -> int:
    """Pre-flight health check for unattended runs (#21). Runs ordered dependency +
    credential checks WITHOUT a real render — each prints a pass/fail line. Prints a
    JSON summary to stdout and returns 0 iff every check passed, non-zero otherwise.

    Deliberately does NOT use run() (which die()s on any non-zero subprocess) — a
    failing check must be recorded and the remaining checks still run. Designed to
    finish in <5s unless --load-model forces the slow MLX model load."""
    checks: list[dict[str, Any]] = []
    log("selftest: checking dependencies and credentials...")

    # 1. ffmpeg + ffprobe on PATH.
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        checks.append(_check(tool, path is not None, path or "not found on PATH"))

    # 2. save-to-spotify auth is live (lists shows as valid JSON).
    try:
        proc = subprocess.run(
            ["save-to-spotify", "--json", "shows"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            checks.append(
                _check(
                    "save-to-spotify-auth",
                    False,
                    f"`shows` exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}",
                )
            )
        else:
            try:
                json.loads(proc.stdout)
                checks.append(_check("save-to-spotify-auth", True, "shows returned valid JSON"))
            except json.JSONDecodeError:
                checks.append(
                    _check("save-to-spotify-auth", False, "shows did not return valid JSON")
                )
    except FileNotFoundError:
        checks.append(_check("save-to-spotify-auth", False, "save-to-spotify not on PATH"))
    except subprocess.TimeoutExpired:
        checks.append(_check("save-to-spotify-auth", False, "shows timed out (auth/network?)"))

    # 3. config.json exists, parses, and has show_id.
    if not CONFIG_PATH.exists():
        checks.append(_check("config", False, f"{CONFIG_PATH} missing"))
    else:
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError) as e:
            checks.append(_check("config", False, f"{CONFIG_PATH} unparseable: {e}"))
        else:
            has_show = isinstance(cfg, dict) and bool(cfg.get("show_id"))
            checks.append(
                _check("config", has_show, "show_id set" if has_show else "show_id missing")
            )

    # 4. House voice ref clip + transcript exist (bundled or user copy).
    audio_ok = USER_HOUSE_AUDIO.exists() or BUNDLED_HOUSE_AUDIO.exists()
    text_ok = USER_HOUSE_TEXT.exists() or BUNDLED_HOUSE_TEXT.exists()
    house_ok = audio_ok and text_ok
    checks.append(
        _check(
            "house-voice",
            house_ok,
            "ref wav + transcript present" if house_ok else "ref wav/transcript missing",
        )
    )

    # 5. Opt-in: actually load the TTS model (slow; the most thorough check).
    if load_model:
        try:
            t0 = time.time()
            from mlx_audio.tts.utils import load_model as _load

            _load(MODEL_ID)
            detail = f"{MODEL_ID} loaded in {time.time() - t0:.1f}s"
            checks.append(_check("model-load", True, detail))
        except Exception as e:  # noqa: BLE001 — any model-load failure is a check failure
            checks.append(_check("model-load", False, f"{MODEL_ID} failed to load: {e}"))

    all_ok = all(c["ok"] for c in checks)
    print(json.dumps({"status": "ok" if all_ok else "failed", "checks": checks}, indent=2))
    return 0 if all_ok else 1


# --- main ------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    # --manifest and --selftest are mutually exclusive: selftest is a pre-flight
    # health check that never touches a manifest. One of them is required.
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--manifest", type=Path, help="manifest.json to render into an episode")
    mode.add_argument(
        "--selftest",
        action="store_true",
        help="pre-flight: check deps + credentials without a real run; exits non-zero on any fail",
    )
    ap.add_argument(
        "--load-model",
        action="store_true",
        help="with --selftest: also load the TTS model (slow; the most thorough check)",
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="render audio/cover/timeline locally; skip upload"
    )
    ap.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="working directory (default: a tmpdir under the system temp dir)",
    )
    ap.add_argument(
        "--keep-workdir",
        action="store_true",
        help="keep the auto-created workdir after a successful run (default: delete it; "
        "a failed run always keeps it for debugging)",
    )
    ap.add_argument(
        "--prune-workdirs",
        type=int,
        default=0,
        metavar="N",
        help="before rendering, delete auto-created workdirs older than N days "
        "(disk hygiene for unattended runs; 0 = off). Never deletes the active workdir.",
    )
    return ap.parse_args(argv)


def main() -> int:
    args = _parse_args()

    # --selftest short-circuits everything: no manifest, no config load, no run-log
    # record (it is not a "run"). Its own JSON summary + exit code are the contract.
    if args.selftest:
        return run_selftest(load_model=args.load_model)

    global _RUN_CTX
    record = _new_run_record()
    record["manifest_path"] = str(args.manifest)
    record["git_sha"] = resolve_render_sha()
    _RUN_CTX = record
    try:
        return _render(args, record)
    except SystemExit as e:
        # die() (or any sys.exit) reached us with a non-zero code: log the failure
        # record before propagating. The error string was stashed into the record by
        # die(); a bare sys.exit(1) leaves it null. A zero exit is a clean return.
        code = e.code if isinstance(e.code, int) else 1
        if code != 0:
            record["status"] = "failed"
            write_run_log(record)
        raise
    finally:
        _RUN_CTX = None


def _render(args: argparse.Namespace, record: dict[str, Any]) -> int:
    # Disk hygiene first (#21): prune stale auto-workdirs BEFORE creating this run's
    # own, so the active workdir (created just below) can't exist yet and is therefore
    # never a prune candidate. prune_workdirs() also supports an explicit `exclude` by
    # resolved path (covered by tests) for any caller that prunes after creation.
    if args.prune_workdirs:
        pruned = prune_workdirs(args.prune_workdirs)
        if pruned is not None:
            record["pruned_workdirs"] = pruned

    if not args.manifest.exists():
        die(f"manifest not found: {args.manifest}")

    try:
        manifest = json.loads(args.manifest.read_text())
    except (json.JSONDecodeError, OSError) as e:
        die(f"manifest is not valid JSON: {e}")
    validate_manifest(manifest)
    title = manifest["title"]
    summary = manifest["summary"]
    segments = manifest["segments"]
    record["title"] = title
    record["segment_count"] = len(segments)

    auto_workdir = args.workdir is None
    workdir = args.workdir or Path(tempfile.mkdtemp(prefix=WORKDIR_PREFIX))
    workdir.mkdir(parents=True, exist_ok=True)
    record["workdir"] = str(workdir)
    marker = workdir / "uploaded.json"

    # Resume: a prior run already uploaded into this workdir, so skip render + upload
    # and re-run only the idempotent tail. Only when --workdir was given explicitly
    # (an auto tmpdir can't be resumed) and never for --dry-run (which never uploads).
    if args.workdir is not None and marker.exists() and not args.dry_run:
        log(f"workdir: {workdir}")
        rc = _resume(workdir, marker, segments, title, manifest, record)
        if rc == 0:
            write_run_log(record)
        return rc

    # Cross-day cron recovery (#37): before rendering a NEW episode, reconcile any
    # leftover in-flight episode (uploaded last run but never deduped). This marks
    # its URLs covered so curation here can't re-select them — closing the duplicate
    # gap the per-workdir uploaded.json marker can't reach. Skipped for --dry-run,
    # which by contract never uploads, calls Spotify, or mutates covered.json.
    if not args.dry_run:
        _recover_inflight()

    config = load_config()
    show_id = manifest.get("show_id") or config.get("show_id")
    if not show_id:
        die("show_id required (in manifest or ~/.config/daily-podcast/config.json)")
    show_name = config.get("show_name") or "Daily Digest"

    voice, voice_instruct, ref_audio, ref_text = resolve_voice(manifest)
    voice_mode = resolve_voice_mode(voice_instruct, ref_audio)
    cover_date = resolve_cover_date(manifest)
    record["voice"] = voice
    record["voice_mode"] = voice_mode

    log(f"workdir: {workdir}")
    if ref_audio:
        log(f"voice: {voice} (ref_audio clone)")
        log(f"ref_audio: {ref_audio}")
    elif voice_instruct:
        log(f"voice: {voice} (VoiceDesign)")
        log(f"voice_instruct: {voice_instruct[:120]}{'...' if len(voice_instruct) > 120 else ''}")
    else:
        log(f"voice: {voice}")

    # 1-3: render, plan silences, concat
    seg_paths = render_segments(
        segments,
        voice,
        workdir,
        voice_instruct=voice_instruct,
        ref_audio=ref_audio,
        ref_text=ref_text,
        raw_text=manifest.get("raw_text", False),
    )
    silences_ms = plan_silences(seg_paths)
    episode_mp3, loudnorm = concat_and_normalize(seg_paths, silences_ms, workdir)
    record["loudnorm"] = loudnorm

    # 4: cover
    cover = workdir / "cover.jpg"
    build_cover(cover, show_name, cover_date, title)

    # 5: timeline + description
    timeline, description = build_timeline_and_description(
        segments, seg_paths, silences_ms, summary, episode_mp3
    )
    timeline_path = workdir / "timeline.json"
    timeline_path.write_text(json.dumps(timeline, indent=2))
    (workdir / "description.html").write_text(description)

    log(f"\nartifacts in {workdir}:")
    for f in sorted(workdir.iterdir()):
        log(f"  {f.name}: {f.stat().st_size} bytes")

    if args.dry_run:
        # Preview where R2 publish *would* have gone, without uploading anything.
        r2_cfg = load_r2_config(config)
        if r2_cfg:
            slug = slugify(title, manifest.get("date") or dt.date.today().isoformat())
            r2_would_publish = f"{r2_cfg['public_base_url'].rstrip('/')}/{slug}.mp3"
            log(
                f"[r2] dry-run: would publish {r2_would_publish} + manifest entry "
                f"to bucket {r2_cfg['bucket']}"
            )
        else:
            r2_would_publish = None
            log("[r2] dry-run: not configured, would skip")
        chapter_count = sum(1 for it in timeline["items"] if "chapter" in it)
        duration_s = mp3_duration_ms(episode_mp3) / 1000
        record.update(
            status="dry-run",
            chapter_count=chapter_count,
            duration_s=duration_s,
            resumed=False,
        )
        write_run_log(record)
        print(
            json.dumps(
                {
                    "status": "dry-run",
                    "workdir": str(workdir),
                    "episode_mp3": str(episode_mp3),
                    "cover": str(cover),
                    "timeline": str(timeline_path),
                    "voice": voice,
                    "voice_mode": voice_mode,
                    "chapter_count": chapter_count,
                    "duration_s": duration_s,
                    "loudnorm": loudnorm,
                    "r2_would_publish": r2_would_publish,
                },
                indent=2,
            )
        )
        return 0

    # 6: upload, then immediately record the upload — BEFORE the failure-prone tail
    # (set_timeline / poll_ready). If either fails, a re-run with the same --workdir
    # resumes from here instead of re-uploading a duplicate episode.
    episode_uri = upload(
        episode_mp3,
        title,
        description,
        cover,
        show_id,
        config=config,
        dry_run=args.dry_run,
        record=record,
    )
    episode_id = episode_uri.removeprefix("spotify:episode:")
    _atomic_write_text(
        marker,
        json.dumps(
            {
                "episode_uri": episode_uri,
                "title": title,
                "voice": voice,
                "voice_mode": voice_mode,
            },
            indent=2,
        ),
    )
    # Also record the upload in the workdir-INDEPENDENT in-flight log, so if this
    # process dies before dedup the NEXT run (even a different per-date workdir)
    # recovers it instead of re-shipping these URLs as a duplicate (#37). Written
    # after upload() succeeds and the workdir marker, cleared only after dedup.
    _write_inflight(
        episode_uri=episode_uri,
        title=title,
        workdir=workdir,
        source_urls=_segment_urls(segments),
    )
    log(f"uploaded: {episode_uri}")
    set_timeline(episode_id, timeline_path)
    log("timeline set; polling for READY...")
    poll_ready(episode_id)

    # 7: R2 publish — additive, after READY. Never blocks the dedup write below or
    # fails the run; the 3-state result (published/skipped/failed, #48) surfaces in
    # the final JSON line so a configured-but-failed publish is no longer silent.
    r2_status = maybe_publish_r2(
        config,
        episode_mp3=episode_mp3,
        cover=cover,
        timeline=timeline,
        manifest=manifest,
        description=description,
        episode_uri=episode_uri,
    )

    # 8: dedup log update (only after READY, regardless of R2 outcome)
    _save_dedup(segments, episode_uri)
    # URLs are durably covered now, so the in-flight log has done its job — clear it
    # LAST, after dedup, so a crash anywhere above leaves it for the next run.
    _clear_inflight()

    chapter_count = sum(1 for it in timeline["items"] if "chapter" in it)
    duration_s = mp3_duration_ms(episode_mp3) / 1000
    record.update(
        status="ready",
        episode_uri=episode_uri,
        chapter_count=chapter_count,
        duration_s=duration_s,
        r2_status=r2_status,
        resumed=False,
    )
    write_run_log(record)

    print(
        json.dumps(
            {
                "status": "ready",
                "episode_uri": episode_uri,
                "title": title,
                "voice": voice,
                "voice_mode": voice_mode,
                "chapter_count": chapter_count,
                "duration_s": duration_s,
                "loudnorm": loudnorm,
                "r2_status": r2_status,
                "resumed": False,
            },
            indent=2,
        )
    )

    # 9: workdir hygiene (#21). Only auto-created workdirs are eligible — deleting an
    # explicit --workdir would break the documented same-workdir resume/no-op path, so
    # an explicit one is always kept. A failed run never reaches here, so failures keep
    # their workdir for debugging automatically. Best-effort: a cleanup error is logged,
    # not fatal (the episode is already live + deduped). The resume path keeps its
    # workdir untouched (it never auto-deletes), preserving the idempotent re-run.
    if auto_workdir and not args.keep_workdir:
        try:
            shutil.rmtree(workdir)
            log(f"deleted workdir {workdir} (pass --keep-workdir to retain)")
        except OSError as e:
            log(f"warn: could not delete workdir {workdir}: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
