#!/usr/bin/env python3
"""
daily-podcast/render.py — dumb manifest -> episode driver.

Consumes a manifest.json that already contains the written segments, then:
  0. PRE-FLIGHT: deps, credentials, encoder profile, and episode capacity — before
     any expensive work, so a broken host or a full show costs seconds, not a render
  1. Picks a voice (random from preset list, unless overridden)
  2. Renders each segment via Qwen3-TTS (mlx-audio)
  3. Concatenates with fixed inter-segment silences, padded only when a segment
     is short enough to put two chapter starts under Spotify's 5s minimum gap
  4. Loudnorm via ffmpeg
  5. Installs the manifest's cover_image, or builds a date-stamped Pillow cover
  6. Builds timeline.json (chapter per segment + link companion when present)
  7. Builds HTML description (summary + timestamped chapters + source links)
  7b. ARTIFACT GATE: local conformance + refusal to re-upload bytes Spotify already
     rejected (runs under --dry-run too, so a rehearsal is a real rehearsal)
  8. Uploads via save-to-spotify CLI, sets timeline, polls until READY
  9. Optionally publishes the mp3 + a manifest entry to Cloudflare R2 (for the
     cortech.online web feed) — additive, never blocks the run
 10. Updates ~/.config/daily-podcast/covered.json dedup log
 11. Appends one record to ~/.config/daily-podcast/runs.jsonl (across-runs observability)

Progress is checkpointed to <workdir>/state.json and the auto workdir is
deterministic (daily-podcast-<date>), so an interrupted run resumes by re-running the
same command. Any non-clean exit writes a structured incident report to
~/.config/daily-podcast/incidents/new/ (see the repo's incidents/ directory).

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
import importlib.util
import json
import math
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# --- constants -------------------------------------------------------------

VOICES = ["Ryan", "Aiden", "Ethan", "Chelsie"]
# The two halves of a recorded cast clip (#177) — a manifest `cast` value is either
# a preset name from VOICES or exactly these two keys. Both are needed: the clip is
# what the model imitates, the transcript is what it believes the clip says, and a
# clone rendered against the wrong transcript drifts audibly.
CAST_CLIP_FIELDS = ("ref_audio", "ref_text")
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
# Consecutive chapter starts must be >= 5s apart; the final chapter is exempt.
# This is the ONLY chapter-duration rule the platform still has. save-to-spotify
# used to also cap sub-30s chapters at ceil(N*0.15+1), and render.py padded
# trailing silence toward a 30.5s target to satisfy it. Upstream PR #44 (v0.1.4)
# dropped that cap. Verified empirically 2026-08-22 against CLI 0.2.0 on a
# throwaway show: an episode whose timeline had 11 of 12 chapters under 30s was
# accepted by `timeline set` and processed to READY, while a 3s gap was still
# refused ("chapter at index 0 must be at least 5s long"). Don't reintroduce a
# 30s target — it bought nothing and cost up to 12s of dead air per chapter.
MIN_CHAPTER_GAP_MS = 5_000
DEFAULT_SILENCE_MS = 800
LAST_SILENCE_MS = 0  # no silence after the final segment
# The pause between two speakers' lines INSIDE one scene (#172). Read the three
# constants above it as a set, because they are three different things and the
# first two get conflated constantly:
#   MIN_CHAPTER_GAP_MS is not a silence at all — it is the minimum SPACING between
#     consecutive chapter start times, which plan_silences satisfies with padding.
#   DEFAULT_SILENCE_MS is the beat between CHAPTERS: a breath between two topics.
#   TURN_GAP_MS is the beat between TURNS inside one chapter. 800ms here would make
#     a four-hander sound like a hostage negotiation; dialogue wants 150-350ms.
# It is baked into the concatenated seg_NN.mp3, so it is part of a scene's cache key.
TURN_GAP_MS = 250
# TTS speech-rate outlier gate. render.py samples Qwen3-TTS with mlx-audio's
# defaults (no seed, temperature, or repetition penalty), and it occasionally
# degenerates mid-segment into looping babble: on 2026-08-17 segment 6 of
# spotify:episode:3Vtw1gRMf33G0QetyjyFl8 read 1017 chars in 92.16s instead of
# ~55s, leaving 55% of that chapter's script unspoken. Degeneration only ever
# makes a segment SLOWER (extra audio for the same text), so the check is
# one-sided — a high-side bound would just false-positive on terse writing.
# On that episode the clean body segments measured 0.94-1.06x the median rate
# and the failure 0.59x, so 0.75 separates them with wide margin either way.
MIN_SPEECH_RATE_RATIO = 0.75
# A median needs a population. With only a handful of body segments one bad
# render *is* the median (or half of it), so below this floor the check is
# skipped rather than guessed at — same no-data-loss posture as the covered.json
# date pruning. The daily episode carries ~10 body segments, well clear of this.
MIN_RATE_SAMPLE_SEGMENTS = 5
# Ceiling of the bloopers bin's "near-miss" band (the floor is MIN_SPEECH_RATE_RATIO).
# The gate only ever catches a GROSS derailment; a phrase that garbles for a second
# or two barely moves its segment's rate and ships unnoticed, which is where the
# funny audio actually lives. 0.90 sits below the slowest CLEAN segment on 08-17
# (0.94x) so a normal episode banks nothing — widen it and every run banks half its
# segments, which is how an archive turns into noise. Capture only: this band never
# fails a run.
NEAR_MISS_RATE_RATIO = 0.90
# Spotify caps an episode description at 4000 characters (Spotify Web API
# `description`/`html_description` field; same limit surfaces in Spotify for
# Podcasters episode show notes). Past the cap the upload silently truncates or
# rejects the summary, so build_timeline_and_description fits the HTML under it
# by dropping whole trailing chapter <p> blocks rather than cutting mid-tag.
SPOTIFY_SUMMARY_MAX_CHARS = 4000
# Per-episode credit line, appended AFTER the chapter blocks (#130). The
# placement IS the contract: cortech.online's summaryText() keeps only the
# paragraphs BEFORE the first timestamped chapter line and renders them as the
# website summary, so a footer above the chapters would leak into every episode
# summary on the web. Below them it reaches Spotify's show notes in full and the
# website not at all — which is the split the show wants. The credit links the
# PRODUCT (donthype.me); the repo behind it is private and would 404 for every
# listener. Escaped like the chapter titles so a consumer's entity decoding
# round-trips.
SOURCES_OPML_URL = "https://cortech.online/podcast/sources.opml"
CURATION_TOOL_URL = "https://donthype.me"
CURATION_TOOL_NAME = "Don't Hype Me"
DESCRIPTION_FOOTER = (
    f'<p>Sources: <a href="{html.escape(SOURCES_OPML_URL, quote=True)}">'
    "every feed this show reads</a>, curated in "
    f'<a href="{html.escape(CURATION_TOOL_URL, quote=True)}">'
    f"{html.escape(CURATION_TOOL_NAME, quote=True)}</a>.</p>"
)


def resolve_description_footer(manifest: dict[str, Any]) -> str:
    """
    Per-show source credit (#152). DESCRIPTION_FOOTER names the daily show's
    sources (the OPML feeds, Don't Hype Me) — factually wrong attribution on any
    other show, and under ship_mode=web it lands verbatim in public RSS show
    notes. `description_footer_text` replaces it: PLAIN TEXT by contract
    (validate_manifest rejects markup), escaped and wrapped in one <p> here
    rather than trusting an operator-authored HTML fragment. Links are
    deliberately unsupported — per-story links live on the chapter lines.
    Absent/null → the daily footer, byte-identical.
    """
    text = manifest.get("description_footer_text")
    if not text:
        return DESCRIPTION_FOOTER
    return f"<p>{html.escape(text, quote=True)}</p>"


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

# --- reliability layer -----------------------------------------------------
#
# Every constant below traces to a failure that actually happened in production;
# see incidents/ for the one-file-per-failure-mode write-ups.

# The encoder profile every ffmpeg invocation re-asserts. Named (rather than
# repeated as literals) so preflight can check the *actual* settings the encoder
# uses instead of a doc comment that could drift away from the code.
AUDIO_SAMPLE_RATE = 44100
AUDIO_CHANNELS = 1  # mono; concat-protocol is fragile across mismatched layouts
AUDIO_BITRATE = "192k"
AUDIO_CODEC = "libmp3lame"
# What ffprobe must report back for a finished episode. Keyed by ffprobe's own
# field names so verify_artifact can compare a probe dict directly.
ENCODER_PROFILE = {
    "codec_name": "mp3",
    "channels": AUDIO_CHANNELS,
    "sample_rate": AUDIO_SAMPLE_RATE,
}

# Durable per-stage checkpoint inside the workdir. Combined with a deterministic
# per-date auto workdir, this is what makes a dropped connection resumable rather
# than a lost render: the next invocation re-enters at the first incomplete stage.
# uploaded.json remains the authoritative upload marker (state.json supersedes
# nothing) — this is additive observability + resume metadata.
STATE_FILENAME = "state.json"
STAGES: tuple[str, ...] = (
    "preflight",
    "segments",
    "concat",
    "cover",
    "timeline",
    "upload",
    "set_timeline",
    "artifact_gate",
    "poll_ready",
    "r2",
    "dedup",
)

# Spotify's per-show episode cap. Confirmed hard at 60 (2026-07-17/18): the upload
# 429s with RATE_LIMIT_EXCEEDED/capacity. Overridable per-show via config
# `episode_cap` in case the limit ever moves.
EPISODE_CAP_DEFAULT = 60

# poll_ready's window. The original 600s expired while Spotify was legitimately
# still PROCESSING — the observed settle time was ~16 minutes (2026-07-28) — which
# turned a healthy episode into a "failed" run needing a manual resume.
DEFAULT_POLL_TIMEOUT_S = 1800
POLL_INTERVAL_S = 15

# One automatic retry for a transient upload flake (2026-06-07: failed once with
# empty stderr, succeeded on an immediate re-run). Strictly one — never a loop,
# and never compounded with the cap-429 prune-then-retry path.
UPLOAD_RETRY_DELAY_S = 10

# Append-only log of artifacts Spotify rejected server-side, keyed by sha256.
# The 2026-08-08 incident proved the rejection is tied to the *artifact*: two
# independent uploads of one byte-identical mp3 both went NOT_READY -> FAILED.
# Since auto-prune is on, each attempt permanently deletes a published episode to
# free a cap slot, so re-uploading known-dead bytes is destructive, not merely futile.
REJECTIONS_PATH = CONFIG_DIR / "rejections.jsonl"

# Structured incident reports written on any non-clean exit. Deliberately NOT a
# repo-relative incidents/new/: the scheduled run executes from the version-keyed
# plugin cache (~/.claude/plugins/cache/clodcast/clodcast/<version>/), where a
# repo-relative write would be invisible to the operator and wiped by the next
# release. Lives beside runs.jsonl instead; DAILY_PODCAST_INCIDENT_DIR overrides.
INCIDENT_DIR = CONFIG_DIR / "incidents" / "new"

# The bloopers bin: an archive of TTS clips worth keeping, written as a side effect
# of paths that already exist (#169). It is here rather than in the workdir because
# the workdir is precisely what disappears — the documented recovery for a speech-rate
# rejection deletes the offending seg_NN.mp3, and /tmp itself empties a stale workdir
# within days. Clips are content-addressed under clips/, indexed by an append-only
# index.jsonl (runs.jsonl posture: never atomic-replaced, one full-key-set row per
# clip). Nothing in a run reads it back; it is write-only until a meta-episode is cut
# from it by hand.
BLOOPER_DIR = CONFIG_DIR / "bloopers"

# One row per banked clip. Same contract as RUN_LOG_FIELDS: every row carries the
# FULL key set with nulls for what does not apply, so the index parses line-by-line
# in jq/pandas without a per-row schema check. `text` and `duration_ms` are the two
# that must never go missing — the meta-episode narrates over these clips and needs
# to know what each was supposed to say and how much material is banked.
BLOOPER_FIELDS: tuple[str, ...] = (
    "timestamp",  # ISO 8601 UTC
    "reason",  # "gate" | "near-miss" | "run-failed" | "manual"
    "sha256",  # of the clip bytes; the clip's filename is its first 16 chars
    "clip",  # absolute path inside the bin
    "source",  # where it came from (workdir segment, episode mp3, ...)
    "run_date",
    "title",  # episode title, when known
    "segment",  # 1-based segment number, matching the render log
    "source_url",
    "text",  # what the segment was supposed to say
    "chars",
    "duration_ms",
    "rate",  # measured chars/sec (rate triggers only)
    "median",  # the population median it was judged against
    "ratio",  # rate / median
    "note",  # free text, manual captures only
    "workdir",
)

# Registry of feeds/outlets that can't be fetched for article bodies, moved out of
# operator memory and into the shipped skill so curation can consult it.
BLOCKED_SOURCES_PATH = SCRIPT_DIR / "blocked_sources.json"

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
# The `API error (<code>): <body>` wrapper is unchanged on 0.2.0 (re-confirmed
# 2026-08-22 against a live 401, which yields the body-less `{"error":"API error
# (401): "}` and correctly falls through to the outer-string branch below). The cap
# 429's inner payload was NOT re-observed on 0.2.0 — provoking one needs a real
# upload against a full show, which prunes a published episode.
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


# Detail line for a failed `save-to-spotify --json shows` auth probe. Prefers the
# structured error on STDOUT because since 0.2.0 the CLI writes a
# `<claude-code-hint .../>` plugin advert to STDERR on EVERY invocation, failures
# included — reporting stderr verbatim turned an expired-token pre-flight into an
# advert with no 401 anywhere in it. stderr is still the fallback for a crash that
# dies before the JSON payload is written.
def _shows_failure_detail(proc: subprocess.CompletedProcess) -> str:
    parsed = parse_s2s_error(proc.stdout or "")
    detail = (parsed or {}).get("message") or (proc.stderr or "").strip()
    return f"`shows` exited {proc.returncode}: {detail[:200]}"


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
    "status",  # "ready" | "web-ready" (#155) | "dry-run" | "failed"
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
    "preflight",  # {ok, checks:[{name, ok, detail}]} or null when skipped
    "abandoned_episodes",  # [{episode_uri, title, source_urls}] on a poison-pill give-up
    "mp3_url",  # public R2 URL on a web-only ship, else null (#155)
    "bloopers_captured",  # clips banked into the bloopers bin this run (#169)
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


# --- ship mode (#155) ------------------------------------------------------
#
# Which channel an episode ships to. The daily show ships to Spotify (with the R2
# web feed as an additive extra); Frontier Commits is RSS-first, so its R2 publish
# IS the ship and save-to-spotify is never invoked at all.
#
# The mode lives on the MANIFEST rather than the command line on purpose: the
# distribution channel is a property of the show, and re-running the same manifest
# must ship the same way. A flag can go missing on one invocation — and the failure
# mode of a missing flag here is an episode uploaded to a deprecated Spotify show.
SHIP_MODE_SPOTIFY = "spotify"
SHIP_MODE_WEB = "web"
SHIP_MODES = (SHIP_MODE_SPOTIFY, SHIP_MODE_WEB)


def resolve_ship_mode(manifest: dict[str, Any]) -> str:
    """The manifest's ship mode, defaulting to Spotify. validate_manifest has
    already whitelisted the value, so an absent/empty key is the only fallback."""
    return manifest.get("ship_mode") or SHIP_MODE_SPOTIFY


def is_web_only(manifest: dict[str, Any]) -> bool:
    """True when this manifest ships to the web feed only — no Spotify upload, no
    timeline set, no readiness poll, and R2 config REQUIRED rather than optional."""
    return resolve_ship_mode(manifest) == SHIP_MODE_WEB


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


def resolve_show_name(manifest: dict[str, Any], config: dict[str, Any]) -> str:
    """
    Name stamped on the cover (big wrapped title + uppercase top label). The
    manifest wins because render.py renders every show from ONE config —
    ~/.config/daily-podcast/config.json — and that is deliberate: it owns the
    episode bucket and the Spotify show for all of them. Without this override a
    second show's covers carried the daily show's branding (#157).
    """
    return manifest.get("show_name") or config.get("show_name") or "Daily Digest"


def resolve_cover_image(manifest: dict[str, Any], manifest_path: Path) -> Path | None:
    """
    Path to a supplied cover image, or None to generate one with build_cover.

    #157 let a second show put its own NAME on the cover; the ART stayed the daily
    show's gradient template, which is what a podcast client renders as per-episode
    artwork — under a channel image that is the other show's real cover. A show with
    designed art supplies it here instead.

    Relative values resolve against the MANIFEST's directory, never the CWD: a
    scheduled run's working directory is arbitrary and CLAUDE_PLUGIN_ROOT is unset
    under the cron, so the manifest is the only anchor a caller can count on. Pure:
    resolves a path, never touches the file (check_cover_image does that).
    """
    raw = manifest.get("cover_image")
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_absolute() else manifest_path.parent / path


# --- multi-voice scenes (#172) ---------------------------------------------
#
# A segment may carry `lines: [{speaker, text}, ...]` instead of a `text`. Each line
# renders in its own cast voice and the takes join into the same seg_NN.mp3 the rest
# of the pipeline already expects, so one scene stays one chapter with one
# source_url. The alternative — one utterance per segment — breaks the strict 1:1
# segment<->chapter<->source_url mapping, makes plan_silences pad seconds of dead air
# between every turn to reach MIN_CHAPTER_GAP_MS, and emits a couple hundred
# chapters. Putting the lines INSIDE the segment leaves silences, chapter math,
# timeline, artifact gate, run log, R2 publish and dedup untouched.

LINE_TEXT_JOINER = " "


def segment_lines(seg: Any) -> list[Any] | None:
    """The segment's non-empty `lines` array, or None for a single-voice segment.

    Deliberately tolerant of junk: this is also read on the failure path
    (capture_workdir_segments), where the manifest is whatever JSON was on disk."""
    lines = seg.get("lines") if isinstance(seg, dict) else None
    return lines if isinstance(lines, list) and lines else None


def lines_text(lines: list[Any]) -> str:
    """The spoken text of a scene: its line texts, in order, joined."""
    return LINE_TEXT_JOINER.join(
        line["text"].strip()
        for line in lines
        if isinstance(line, dict) and isinstance(line.get("text"), str) and line["text"].strip()
    )


def materialize_line_text(manifest: dict[str, Any]) -> None:
    """Give every `lines` segment a derived `text`, in place. NOT a convenience.

    speech_rate_rows measures `len(seg["text"])` and treats zero chars as
    *unmeasurable*, skipping the segment. A lines-only episode would therefore
    measure nothing, fall under MIN_RATE_SAMPLE_SEGMENTS, and get back `[]` — which
    that function's docstring defines as "no evidence of a defect". The
    TTS-degeneration gate and the bloopers bin would both switch themselves off for
    an entire show and report nothing. Deriving the text keeps their population
    intact rather than routing around the contract.

    Runs immediately AFTER validate_manifest, which is what rejects a segment
    carrying both an author-written `text` and `lines` — by the time this has run
    every scene has a `text` and the two are indistinguishable. Never overwrites an
    existing `text`, so it is idempotent and safe to re-run on a workdir manifest."""
    if not isinstance(manifest, dict):
        return
    segments = manifest.get("segments")
    if not isinstance(segments, list):
        return
    for seg in segments:
        lines = segment_lines(seg)
        if lines is None or seg.get("text") is not None:
            continue
        seg["text"] = lines_text(lines)


# --- input safety ----------------------------------------------------------


def _validate_cast_voice(speaker: str, cast_voice: Any) -> None:
    """One cast entry (#172, #177): a bundled preset NAME, or a recorded clip
    `{"ref_audio": <path>, "ref_text": <transcript>}`.

    Both shapes are closed, for the same reason. The preset side stays a whitelist so
    a mistyped "Rian" dies rather than silently rendering a scene in the wrong voice.
    The clip side must carry both halves of the reference and nothing else, because a
    stray key that LOOKS like it selects something — `{"ref_audio": ..., "voice":
    "Ryan"}` — would otherwise be accepted and then ignored, which is the same wrong
    voice arriving by a different door.

    Pure: whether the clip is actually on disk is checked where its bytes are read
    (resolve_cast_voice), since validate_manifest does no I/O.
    """
    if isinstance(cast_voice, dict):
        for field in CAST_CLIP_FIELDS:
            val = cast_voice.get(field)
            if not isinstance(val, str) or not val.strip():
                die(
                    f"manifest cast[{speaker!r}].{field} is required and must be a "
                    f"non-empty string — a recorded clip carries {list(CAST_CLIP_FIELDS)}"
                )
        extra = sorted(set(cast_voice) - set(CAST_CLIP_FIELDS))
        if extra:
            die(
                f"manifest cast[{speaker!r}] has unknown field(s) {extra} — a recorded "
                f"clip carries exactly {list(CAST_CLIP_FIELDS)}"
            )
        return
    if cast_voice not in VOICES:
        shown = "{" + ", ".join(f'"{v}"' for v in VOICES) + "}"
        die(
            f"manifest cast[{speaker!r}] must be one of {shown}, or a recorded clip "
            f'{{"ref_audio": ..., "ref_text": ...}} (got {cast_voice!r})'
        )


def _validate_scene(i: int, lines: Any, cast: dict[str, Any] | None) -> None:
    """Structural checks for one segment's `lines` array. Dies naming the line."""
    if not isinstance(lines, list) or not lines:
        die(f"manifest segment[{i}] field 'lines' must be a non-empty list")
    for j, line in enumerate(lines):
        where = f"manifest segment[{i}].lines[{j}]"
        if not isinstance(line, dict):
            die(f"{where} must be an object")
        speaker = line.get("speaker")
        if not isinstance(speaker, str) or not speaker.strip():
            die(f"{where} missing required field 'speaker'")
        if not isinstance(line.get("text"), str) or not line["text"].strip():
            die(f"{where} field 'text' must be a non-empty string")
        if not cast or speaker not in cast:
            known = ", ".join(sorted(cast)) if cast else "the manifest has no 'cast'"
            die(f"{where}.speaker {speaker!r} is not in the manifest 'cast' ({known})")


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

    # The cast is validated before the segments because every line's `speaker` is
    # checked against it (#172). The house voice is deliberately excluded from the
    # preset whitelist — it is the daily show's narrator, not a panelist.
    cast = manifest.get("cast")
    if cast is not None:
        if not isinstance(cast, dict) or not cast:
            die("manifest 'cast' must be a non-empty object mapping speaker -> voice")
        for speaker, cast_voice in cast.items():
            if not isinstance(speaker, str) or not speaker.strip():
                die("manifest 'cast' keys must be non-empty speaker names")
            _validate_cast_voice(speaker, cast_voice)

    segments = manifest.get("segments")
    if not isinstance(segments, list) or not segments:
        die("manifest 'segments' is required and must be a non-empty list")
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            die(f"manifest segment[{i}] must be an object")
        if seg.get("lines") is not None:
            # A scene's `text` is DERIVED from its lines (materialize_line_text), so an
            # author-written one is a malformed manifest: silently preferring either
            # would ship an episode whose audio and whose measured script disagree.
            if seg.get("text") is not None:
                die(
                    f"manifest segment[{i}] has both 'text' and 'lines' — they are mutually "
                    "exclusive; a scene's 'text' is derived from its line texts"
                )
            _validate_scene(i, seg["lines"], cast)
        elif not isinstance(seg.get("text"), str):
            die(f"manifest segment[{i}] missing required field 'text'")
        elif not seg["text"].strip():
            die(f"manifest segment[{i}] field 'text' must be non-empty")
        for opt in ("title", "source_title"):
            if seg.get(opt) is not None and not isinstance(seg[opt], str):
                die(f"manifest segment[{i}].{opt} must be a string")
        url = seg.get("source_url")
        if url is not None and not (
            isinstance(url, str) and url.startswith(("http://", "https://"))
        ):
            die(f"manifest segment[{i}].source_url must be an http(s) URL (got {url!r})")

    # A cast is presets on the base MODEL_ID; voice_instruct routes the episode to
    # VOICE_DESIGN_MODEL_ID — a SECOND model, roughly doubling the ~15s load, that
    # also drifts run to run (docs/durable-voices.md). One episode cannot be rendered
    # from both, so the combination dies here rather than quietly rendering a cast
    # off the wrong model. Clone mode is fine: it shares the base model.
    if manifest.get("voice_instruct") and any(
        isinstance(seg, dict) and seg.get("lines") for seg in segments
    ):
        die(
            "manifest sets 'voice_instruct' and carries 'lines' segments — VoiceDesign is a "
            "second model and a multi-voice cast runs on the base model's presets; drop one"
        )

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
    # A second show publishes into the same R2 bucket; a bare-filename key keeps
    # its web feed out of the daily show's manifest.json without touching episode
    # object paths (#118). THREAT MODEL: the key names an R2 object the publish
    # will overwrite, and manifests can be hand-authored — so this is a whitelist,
    # not a blocklist. No path separators (a key like "../x.json" or "a/b.json"
    # could never be a sibling manifest) and a forced ".json" suffix (so the
    # no-cache JSON PUT can never clobber an episode mp3 or cover object).
    r2_name = manifest.get("r2_manifest_name")
    if r2_name is not None and (
        not isinstance(r2_name, str) or not re.fullmatch(r"[A-Za-z0-9._-]+\.json", r2_name)
    ):
        die(
            f"manifest 'r2_manifest_name' must be a bare filename ending in .json (got {r2_name!r})"
        )
    # r2_manifest_name isolates only the manifest object; the date-keyed slug (#128)
    # still mints identical <slug>.mp3/<slug>.jpg keys for two shows publishing the
    # same day into the shared bucket, so a second show's publish would silently
    # overwrite the daily show's episode (#142). r2_key_prefix namespaces those keys.
    # Same whitelist posture as above, with one addition: the prefix also lands in
    # public URLs (base_url + key), where a dot-only prefix like "../" normalizes
    # back onto the default show's objects — so dot-only values die too.
    prefix = manifest.get("r2_key_prefix")
    if prefix is not None and (
        not isinstance(prefix, str)
        or not re.fullmatch(r"[A-Za-z0-9._-]+/?", prefix)
        or not prefix.strip("./")
    ):
        die(
            "manifest 'r2_key_prefix' must be a bare object-key prefix "
            f"([A-Za-z0-9._-]+ with an optional trailing '/') (got {prefix!r})"
        )
    # Per-show slug prefix (#162). The slug is the /podcast/<slug>/ permalink and
    # the isPermaLink <guid> — a second show must not publish under the daily
    # show's daily-digest-<date> name. Whitelist posture again, tighter than
    # r2_key_prefix because the value lands VERBATIM in the published slug: it
    # must already match the consumer schema's ^[a-z0-9-]+$ with no edge or
    # doubled hyphens (the kebab normalizer would silently rewrite those, making
    # the stored key and the minted slug disagree), and 62 chars keeps prefix +
    # the longest date tail ("-september-30-2026", 18 chars) inside the schema's
    # 80-char cap — a truncated tail would let two dates share one slug/guid.
    slug_prefix = manifest.get("slug_prefix")
    if slug_prefix is not None and (
        not isinstance(slug_prefix, str)
        or not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug_prefix)
        or len(slug_prefix) > 62
    ):
        die(
            "manifest 'slug_prefix' must be a lowercase kebab literal "
            f"([a-z0-9]+(-[a-z0-9]+)*, at most 62 chars) or unset (got {slug_prefix!r})"
        )
    # Cover branding override (#157). A blank value is a typo, not a request for
    # the default: falling through to config would stamp the daily show's name on a
    # second show's cover, which is the exact bug this key exists to fix.
    cover_show_name = manifest.get("show_name")
    if cover_show_name is not None and (
        not isinstance(cover_show_name, str) or not cover_show_name.strip()
    ):
        die(f"manifest 'show_name' must be a non-empty string (got {cover_show_name!r})")
    # Supplied episode art (#164). Same posture as show_name: a blank value is a
    # typo, not a request for the generated cover — falling through would ship the
    # daily show's gradient on a show that has its own art. Existence and shape are
    # checked at pre-flight (check_cover_image), not here: validate_manifest is pure.
    cover_image = manifest.get("cover_image")
    if cover_image is not None and (not isinstance(cover_image, str) or not cover_image.strip()):
        die(f"manifest 'cover_image' must be a non-empty path string (got {cover_image!r})")
    # Per-show source credit (#152). A blank value is a typo, not a request for
    # the default (same posture as show_name). Plain text by contract: the value
    # is html.escape()d into the footer <p>, so markup here would reach the
    # public show notes as literal angle brackets — reject it early instead.
    footer_text = manifest.get("description_footer_text")
    if footer_text is not None:
        if not isinstance(footer_text, str) or not footer_text.strip():
            die(
                "manifest 'description_footer_text' must be a non-empty string "
                f"or unset (got {footer_text!r})"
            )
        if "<" in footer_text or ">" in footer_text:
            die(
                "manifest 'description_footer_text' is plain text (render.py escapes "
                f"it into the footer <p>); remove the markup (got {footer_text!r})"
            )
    # Ship mode is a closed set (#155): an unrecognized value must never fall back
    # to the Spotify default, because for an RSS-first show that means uploading to
    # a deprecated show instead of failing. Typos ("webb", "WEB") die here.
    ship_mode = manifest.get("ship_mode")
    if ship_mode is not None and ship_mode not in SHIP_MODES:
        shown = "{" + ", ".join(f'"{m}"' for m in SHIP_MODES) + "}"
        die(f"manifest 'ship_mode' must be one of {shown} or unset (got {ship_mode!r})")
    # cover_style: closed whitelist, same posture as ship_mode. A typo must die
    # rather than fall through to the default and quietly restyle a published show.
    style = manifest.get("cover_style")
    if style is not None and style not in COVER_STYLES:
        shown = "{" + ", ".join(f'"{v}"' for v in COVER_STYLES) + "}"
        die(f"manifest 'cover_style' must be one of {shown} or unset (got {style!r})")
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


def resolve_cast_voice(speaker: str, cast_voice: Any) -> dict[str, Any]:
    """Resolve one cast entry into the fields one line take needs: mode, log label,
    the two reference halves, and the clip fingerprint.

    A preset entry resolves to preset mode with no reference — byte-identical to the
    pre-#177 behaviour, so every take already banked in a workdir stays valid. A
    recorded clip resolves to CLONE mode on the same base MODEL_ID a preset uses,
    which is what lets a mixed cast still pay exactly one model load and keeps
    `speaker` a role rather than a fifth voice mode.

    The fingerprint is the load-bearing part. Per-line takes are content-addressed,
    so with no clip identity in the key a member re-pointed at a different clip — or
    a clip re-recorded in place, where even the path is unchanged — keeps its old key
    and the run replays the previous voice's banked audio under the new one's name.
    Nothing errors: right text, right length, wrong person.

    Not pure (reads the clip's bytes), which is why validate_manifest checks the
    SHAPE and this checks the file. Both run before the ~15s model load.
    """
    _validate_cast_voice(speaker, cast_voice)
    if not isinstance(cast_voice, dict):
        return {
            "mode": "preset",
            "voice": cast_voice,
            "ref_audio": None,
            "ref_text": None,
            "ref_fingerprint": None,
        }
    ref_audio = cast_voice["ref_audio"]
    if not Path(ref_audio).is_file():
        die(f"manifest cast[{speaker!r}] ref_audio not found: {ref_audio}")
    return {
        "mode": "clone",
        # A log label only. The clip's BYTES are what identify this voice in the
        # cache key, so the label can be readable without being load-bearing.
        "voice": Path(ref_audio).stem,
        "ref_audio": ref_audio,
        "ref_text": cast_voice["ref_text"],
        "ref_fingerprint": _ref_audio_fingerprint(ref_audio),
    }


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


def _scene_cache_key(line_keys: list[str]) -> str:
    """Content hash for a scene assembled from per-line takes (#172): the ordered line
    keys plus the turn gap welded between them. A line changing, moving, appearing or
    disappearing changes the scene's key — and so does retuning TURN_GAP_MS, since
    that silence is baked into the concatenated seg_NN.mp3. Pure; no I/O."""
    payload = json.dumps({"lines": line_keys, "turn_gap_ms": TURN_GAP_MS}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _take_cache_hit(mp3: Path, sidecar: Path, key: str) -> bool:
    """A rendered take (a whole segment, or one line of a scene) is reusable iff its
    mp3 exists AND its sidecar records this exact key. A bare mp3 with no/mismatched
    sidecar (older run, partial write, changed script) is NOT trusted — content
    identity is then unknown, so we re-render."""
    if not mp3.exists() or not sidecar.exists():
        return False
    try:
        meta = json.loads(sidecar.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    return isinstance(meta, dict) and meta.get("key") == key


def _cache_hit(workdir: Path, i: int, key: str) -> bool:
    """Whether segment `i`'s finished seg_NN.mp3 can be reused as-is. For a scene the
    key is the composite one from _scene_cache_key, so the joined mp3 and its line
    takes are cached independently."""
    return _take_cache_hit(workdir / f"seg_{i:02d}.mp3", workdir / f"seg_{i:02d}.json", key)


# --- audio rendering -------------------------------------------------------


def _render_take(
    model: Any,
    *,
    text: str,
    mode: str,
    voice: str,
    voice_instruct: str | None,
    ref_audio: str | None,
    ref_text: str | None,
    mp3: Path,
) -> float:
    """Render ONE take — a whole segment, or one line of a multi-voice scene — to a
    mono-44.1k mp3. Returns the generated audio's duration in seconds.

    Both callers share this body so the per-line path cannot drift from the
    per-segment one: the mono-44.1k re-assertion in particular is a place the concat
    invariant can be broken, and there is now one of it rather than two."""
    import numpy as np
    import soundfile as sf

    if mode == "clone":
        results = list(
            model.generate(
                text=text,
                language="English",
                ref_audio=ref_audio,
                ref_text=ref_text,
            )
        )
    elif mode == "design":
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
    wav = mp3.with_suffix(".wav")
    sf.write(wav, audio, SAMPLE_RATE)
    # convert to mp3 at 44.1k mono so concat is clean
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(wav),
            "-ar",
            str(AUDIO_SAMPLE_RATE),
            "-ac",
            str(AUDIO_CHANNELS),
            "-c:a",
            AUDIO_CODEC,
            "-b:a",
            AUDIO_BITRATE,
            str(mp3),
        ]
    )
    return len(audio) / SAMPLE_RATE


def render_segments(
    segments: list[dict],
    voice: str,
    workdir: Path,
    voice_instruct: str | None = None,
    ref_audio: str | None = None,
    ref_text: str | None = None,
    raw_text: bool = False,
    cast: dict[str, str] | None = None,
) -> list[Path]:
    """
    Render each segment text to an mp3 in workdir; return list of mp3 paths.

    Three voice modes:
    - `ref_audio` set (+ `ref_text`): voice cloning via Base model + generate(ref_audio=...)
    - `voice_instruct` set: VoiceDesign model + generate_voice_design(instruct=...)
    - Otherwise: Base model with `voice` as a preset name (Ryan/Aiden/Ethan/Chelsie)

    `ref_audio` takes precedence if both are set.

    Multi-voice scenes (#172): a segment carrying `lines` renders one take per line,
    each in the voice `cast` maps its `speaker` to, and joins them into the same
    seg_NN.mp3 a single-voice segment produces — one scene, one chapter, one
    source_url. A cast voice is a bundled preset or a recorded clip (#177); either
    way it runs on the same base MODEL_ID, so a four-hander still pays exactly one
    model load. `speaker` is a role, NOT a fifth voice mode, and the four-mode
    precedence above — which governs the EPISODE voice — is untouched.

    Per-segment cache (#9): each seg_NN.mp3 carries a seg_NN.json sidecar with a
    content-hash key (text + voice settings). On a re-run with the same --workdir,
    any segment whose key matches is reused as-is and only the rest are rendered;
    if *every* segment is cached the model load is skipped entirely. A scene caches
    at both levels — line_NN_LL.mp3 per take, seg_NN.mp3 for the join — so rewriting
    one bad line re-renders that line, not the whole scene. The mono-44.1k invariant
    and strict 1:1 segment<->source mapping are unchanged — a cache hit returns the
    byte-identical mp3 a fresh render would have produced.
    """
    use_clone = bool(ref_audio)
    use_design = bool(voice_instruct) and not use_clone
    mode = "clone" if use_clone else ("design" if use_design else "preset")
    # In design mode the instruct is what shapes the voice, so it must be part of
    # the key; otherwise the voice label is. Resolve the ref-audio fingerprint once.
    key_voice = voice_instruct if use_design else voice
    ref_fingerprint = _ref_audio_fingerprint(ref_audio)
    cast = cast or {}
    if use_design and any(segment_lines(seg) for seg in segments):
        # validate_manifest already rejects this; re-asserted here so the function is
        # honest to a direct caller rather than rendering the cast off the wrong model.
        die(
            "voice_instruct (VoiceDesign) cannot render a 'lines' cast — "
            "the cast needs the base model"
        )
    # Resolved once per MEMBER, not per line: a clip's fingerprint costs a file read,
    # and a four-hander scene would otherwise re-hash the same four clips every turn.
    resolved_cast = {sp: resolve_cast_voice(sp, cv) for sp, cv in cast.items()}

    # Pass 1 (no model needed): prep text + compute keys + classify hit/miss. A scene
    # expands into one take per line here, so per-line hits are known before anything
    # is loaded — which is what keeps the model load conditional for scenes too.
    plans: list[dict[str, Any]] = []
    for i, seg in enumerate(segments, start=1):
        lines = segment_lines(seg)
        if lines is None:
            text = _prep_segment_text(seg["text"], raw_text)
            if not text:
                die(f"segment {i} has empty text")
            takes = [
                {
                    "text": text,
                    "mode": mode,
                    "voice": voice,
                    "ref_audio": ref_audio,
                    "ref_text": ref_text,
                    "key": _segment_cache_key(
                        text, mode, key_voice or "", ref_fingerprint, ref_text
                    ),
                    "mp3": workdir / f"seg_{i:02d}.mp3",
                    "sidecar": workdir / f"seg_{i:02d}.json",
                }
            ]
            key = takes[0]["key"]
        else:
            takes = []
            for j, line in enumerate(lines, start=1):
                text = _prep_segment_text(line.get("text") or "", raw_text)
                if not text:
                    die(f"segment {i} line {j} has empty text")
                speaker = line.get("speaker")
                spec = resolved_cast.get(speaker) if isinstance(speaker, str) else None
                if not spec:
                    known = ", ".join(sorted(cast)) if cast else "no cast in the manifest"
                    die(f"segment {i} line {j} speaker {speaker!r} is not in the cast ({known})")
                takes.append(
                    {
                        "text": text,
                        # A cast voice runs on the base model — a preset, or a clone of
                        # a recorded clip — never the VoiceDesign engine. Its key
                        # records the member's OWN mode and reference, so it stays
                        # stable when the EPISODE voice moves and changes the moment
                        # that member's clip does.
                        "mode": spec["mode"],
                        "voice": spec["voice"],
                        "ref_audio": spec["ref_audio"],
                        "ref_text": spec["ref_text"],
                        "key": _segment_cache_key(
                            text,
                            spec["mode"],
                            spec["voice"],
                            spec["ref_fingerprint"],
                            spec["ref_text"],
                        ),
                        "mp3": workdir / f"line_{i:02d}_{j:02d}.mp3",
                        "sidecar": workdir / f"line_{i:02d}_{j:02d}.json",
                    }
                )
            key = _scene_cache_key([t["key"] for t in takes])
        for take in takes:
            take["cached"] = _take_cache_hit(take["mp3"], take["sidecar"], take["key"])
        plans.append(
            {
                "scene": lines is not None,
                "takes": takes,
                "key": key,
                "cached": _cache_hit(workdir, i, key),
            }
        )

    n_hits = sum(p["cached"] for p in plans)
    if n_hits:
        log(f"cache: {n_hits}/{len(segments)} segment(s) reusable from {workdir}")

    # Load the model only if at least one TAKE is a miss. A fully-cached re-run pays
    # nothing for the ~15s model load (acceptance criterion in #9), and a scene whose
    # line takes all survive needs only re-joining, which is an ffmpeg call.
    pending = [t for p in plans if not p["cached"] for t in p["takes"] if not t["cached"]]
    model = None
    if pending:
        model_id = VOICE_DESIGN_MODEL_ID if use_design else MODEL_ID
        log(f"loading {model_id}...")
        t0 = time.time()
        from mlx_audio.tts.utils import load_model

        model = load_model(model_id)
        log(f"  model loaded in {time.time() - t0:.1f}s")
    elif n_hits == len(segments):
        log("cache: all segments cached, skipping model load")
    else:
        log("cache: every line take is cached, re-joining scenes only; skipping model load")

    paths: list[Path] = []
    for idx, plan in enumerate(plans):
        i = idx + 1
        mp3 = workdir / f"seg_{i:02d}.mp3"
        if plan["cached"]:
            what = (
                f"scene, {len(plan['takes'])} line take(s)"
                if plan["scene"]
                else f"voice={voice}, mode={mode}"
            )
            log(f"[{i}/{len(segments)}] cache hit ({what}), reusing {mp3.name}")
            paths.append(mp3)
            continue
        n_takes = len(plan["takes"])
        for j, take in enumerate(plan["takes"], start=1):
            where = f"[{i}/{len(segments)}]" + (f" line {j}/{n_takes}" if plan["scene"] else "")
            if take["cached"]:
                log(f"{where} cache hit (voice={take['voice']}), reusing {take['mp3'].name}")
                continue
            log(
                f"{where} rendering ({len(take['text'])} chars, "
                f"voice={take['voice']}, mode={take['mode']})..."
            )
            t0 = time.time()
            dur_s = _render_take(
                model,
                text=take["text"],
                mode=take["mode"],
                voice=take["voice"],
                voice_instruct=voice_instruct,
                # Per TAKE, not per episode: a scene's lines each carry their own
                # cast member's reference, and the episode's is only ever the
                # fallback a plain-text segment renders with.
                ref_audio=take["ref_audio"],
                ref_text=take["ref_text"],
                mp3=take["mp3"],
            )
            # Write the sidecar only AFTER the mp3 is on disk, so a crash between the
            # two never records a cache hit for a half-written take. _atomic_write_text
            # ensures the sidecar itself can't be torn either.
            _atomic_write_text(take["sidecar"], json.dumps({"key": take["key"]}))
            elapsed = time.time() - t0
            log(f"  -> {dur_s:.2f}s in {elapsed:.1f}s ({dur_s / elapsed:.1f}x rt)")
        if plan["scene"]:
            join_line_takes([t["mp3"] for t in plan["takes"]], workdir, mp3)
            _atomic_write_text(workdir / f"seg_{i:02d}.json", json.dumps({"key": plan["key"]}))
        paths.append(mp3)
    return paths


def plan_silences(seg_paths: list[Path]) -> list[int]:
    """
    Return silence_ms[i] = silence AFTER segment i. Last entry is LAST_SILENCE_MS.

    Every chapter starts where the previous segment's audio ended plus that
    segment's trailing silence, so silence[i] is what separates chapter i's start
    from chapter i+1's. The only platform constraint on that spacing is
    MIN_CHAPTER_GAP_MS; the final segment has no successor, so it keeps
    LAST_SILENCE_MS (padding the tail breaks chapter math).

    In practice no segment is anywhere near 5s, so this returns the flat default.
    The guard exists so a degenerate short segment can never build a timeline the
    CLI will refuse.
    """
    n = len(seg_paths)
    seg_ms = [mp3_duration_ms(p) for p in seg_paths]
    silence = [DEFAULT_SILENCE_MS] * n
    silence[-1] = LAST_SILENCE_MS

    for i in range(n - 1):
        silence[i] = max(silence[i], MIN_CHAPTER_GAP_MS - seg_ms[i])

    chapter_ms = [seg_ms[i] + silence[i] for i in range(n)]
    padded = [i for i in range(n - 1) if silence[i] > DEFAULT_SILENCE_MS]
    log(f"chapter ms: {chapter_ms}")
    log(f"silence ms: {silence}")
    log(f"padded to the {MIN_CHAPTER_GAP_MS}ms minimum gap: {padded}")
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


def join_line_takes(line_paths: list[Path], workdir: Path, out: Path) -> Path:
    """Join one scene's per-line takes into the single seg_NN.mp3 the rest of the
    pipeline expects, separated by TURN_GAP_MS of silence (#172).

    The gap between takes is a TURN gap and stops at this function: plan_silences
    still spaces the chapters exactly as it does for a single-voice episode, and
    nothing below seg_NN.mp3 can tell that this segment was a scene.

    Re-asserts mono 44.1k for the same reason every other ffmpeg call in this file
    does — the concat protocol is fragile across mismatched sample rates and channel
    layouts, and a scene is one more place to break it."""
    gap = write_silence(workdir, TURN_GAP_MS)
    parts: list[Path] = []
    for k, take in enumerate(line_paths):
        if k:
            parts.append(gap)
        parts.append(take)

    concat_list = workdir / f"{out.stem}_lines.txt"
    concat_list.write_text("\n".join(f"file '{p}'" for p in parts) + "\n")
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
            str(AUDIO_SAMPLE_RATE),
            "-ac",
            str(AUDIO_CHANNELS),
            "-c:a",
            AUDIO_CODEC,
            "-b:a",
            AUDIO_BITRATE,
            str(out),
        ]
    )
    log(f"  joined {len(line_paths)} line take(s) -> {out.name} ({TURN_GAP_MS}ms turn gap)")
    return out


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
            str(AUDIO_SAMPLE_RATE),
            "-ac",
            str(AUDIO_CHANNELS),
            "-c:a",
            AUDIO_CODEC,
            "-b:a",
            AUDIO_BITRATE,
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
            str(AUDIO_SAMPLE_RATE),
            "-ac",
            str(AUDIO_CHANNELS),
            "-c:a",
            AUDIO_CODEC,
            "-b:a",
            AUDIO_BITRATE,
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

COVER_SIZE = 1400

# The house cover (#163): an ASCII sun over a horizon rule. There is deliberately
# no second built-in design and no style selector — a show that wants its own art
# supplies it with the manifest's `cover_image` (#164), which is strictly better
# than a template it did not design. That key is what retired the old gradient
# renderer: Frontier Commits was its only user.
# Brand tokens, lifted from cortech.online's src/styles/global.css. RGB tuples
# because that is what Pillow takes; the hex is in the comment so the two files
# can be diffed by eye.
COVER_GROUND = (16, 20, 29)  # #10141d — the ground both shows' covers sit on
COVER_AMBER = (246, 195, 74)  # #f6c34a — --color-amber
COVER_PAPER = (242, 239, 230)  # #f2efe6 — --color-text
COVER_MUTED = (123, 126, 138)  # #7b7e8a — --color-muted
COVER_FOOTER_INK = (76, 82, 97)  # one step below muted; the domain is a whisper
# The sun is drawn at 92% over the ground rather than composited through an alpha
# layer — one flattened constant is cheaper and exact.
COVER_SUN_INK = (228, 181, 70)
COVER_FOOTER = "cortech.online"  # publisher of every show this renderer serves

# The ASCII sun is PINNED ART, not a table regenerated at import time: this is the
# grid the design was approved at, and cortech.online's show-art SVG draws the same
# one, which is what makes the show cover and the episode cover the same picture.
# tests/test_cover.py re-derives it from the radial model below and fails if either
# side drifts — the same posture as orchestrate.SHAPE_ORDERS, and for the same
# reason: a table you can verify beats arithmetic you have to trust.
ASCII_SUN_COLS = 19
ASCII_SUN_ROWS = 16
ASCII_SUN_CELL_W = 18.4  # px between glyph origins at COVER_SIZE
ASCII_SUN_CELL_H = 21.6
ASCII_SUN_RADIUS = 169.6  # disc radius in the same px space
ASCII_SUN_RAMP = "@#*+=-"  # densest at the core, faintest at the rim
ASCII_SUN_BANDS = (0.30, 0.48, 0.64, 0.78, 0.90, 1.02)  # upper bound per ramp step
ASCII_SUN = (
    "      -------",
    "    --=======--",
    "   -==+++++++==-",
    "  -==++*****++==-",
    " -==+***###***+==-",
    " -=+**#######**+=-",
    "-==+*##@@@@@##*+==-",
    "-=++*##@@@@@##*++=-",
    "-=++*##@@@@@##*++=-",
    "-==+*##@@@@@##*+==-",
    " -=+**#######**+=-",
    " -==+***###***+==-",
    "  -==++*****++==-",
    "   -==+++++++==-",
    "    --=======--",
    "      -------",
)

# The ASCII rail is Frontier Commits' pinned art — the same posture as ASCII_SUN
# above, and schmug/cortech.online's scripts/frontier-cover-art.ts draws the same
# table, which is what makes that show's channel tile and its episode covers one
# picture. tests/test_cover.py re-derives it from the model below. Nothing
# mechanical links the two repos: IF THIS TABLE CHANGES, CHANGE BOTH.
#
# The model: ASCII_RAIL_LANES agent lanes collapse right into a trunk over
# ASCII_RAIL_FAN_ROWS rows, then the spine descends carrying a node roughly every
# ASCII_RAIL_NODE_EVERY rows, with one branch forking left at ASCII_RAIL_STUB_ROW
# and merging back four rows later. The ramp is budgeted across the FULL height:
# the fan spends ASCII_RAIL_FAN_STEPS steps, the spine gets the remainder. A first
# draft let the fan spend all six, which left the whole spine flat.
ASCII_RAIL_COLS = 11
ASCII_RAIL_ROWS = 40
ASCII_RAIL_LANES = 6
ASCII_RAIL_FAN_ROWS = 10
ASCII_RAIL_FAN_STEPS = 3
ASCII_RAIL_NODE_EVERY = 4
ASCII_RAIL_STUB_ROW = 18
ASCII_RAIL = (
    "@ @ @ @ @ @",
    " \\ \\ \\ \\ \\|",
    "  @ @ @ @ @",
    "   \\ \\ \\ \\|",
    "    # # # #",
    "     \\ \\ \\|",
    "      # # #",
    "       \\ \\|",
    "        * *",
    "         \\|",
    "          +",
    "          |",
    "          |",
    "          |",
    "          +",
    "          |",
    "          |",
    "          |",
    "         /|",
    "        | |",
    "        + |",
    "        | |",
    "         \\|",
    "          |",
    "          |",
    "          |",
    "          =",
    "          |",
    "          |",
    "          |",
    "          =",
    "          |",
    "          |",
    "          |",
    "          -",
    "          |",
    "          |",
    "          |",
    "          -",
    "          |",
)


# Layout, in COVER_SIZE px. These are the design's 640px board scaled by 2.1875.
COVER_MARGIN = 122
COVER_SUN_X = 923
COVER_SUN_Y = 74
COVER_LOCKUP_Y = 254
COVER_LOCKUP_SIZE = 37
COVER_LOCKUP_TRACKING = 8
COVER_LOCKUP_MIN_SIZE = 19
# The lockup shares its band with the sun, so its width runs to the sun's left
# edge, NOT to the right margin. Measuring against the full canvas width is what
# a first pass did, and a long show name ran straight under the disc.
COVER_LOCKUP_MAX_W = COVER_SUN_X - COVER_MARGIN - 40
COVER_DATE_Y = 328
COVER_DATE_SIZE = 35
COVER_RULE_Y = 438
COVER_RULE_H = 11
COVER_HEADLINE_SIZE = 96
COVER_HEADLINE_MIN_SIZE = 64
COVER_HEADLINE_LEADING = 1.16
COVER_HEADLINE_BOTTOM = 1159  # the headline grows UP from here, so a long title
COVER_HEADLINE_MAX_LINES = 4  # never runs down into the footer
COVER_FOOTER_SIZE = 33
COVER_FOOTER_Y = 1275

# (path, face index) pairs, best first. The env overrides come first so a host with
# the real brand faces installed can use them without a code change; everything
# after is "what is actually on the machine": macOS ships Helvetica and Menlo, and
# the Linux entries keep cover rendering possible off a Mac (only TTS is
# Apple-Silicon-locked). Face INDEX matters for .ttc collections — Helvetica.ttc
# index 1 is Bold — but the index is only a hint: _cover_face verifies the style
# name it actually loaded, so a future macOS reordering its faces degrades to a
# wrong-weight cover rather than being silently wrong about which font it used.
#
# Bold and regular are separate lists rather than one list plus a style request,
# because the index IS the weight inside a .ttc: asking Helvetica.ttc for
# "Regular" is useless if the entry already pinned index 1. The date and the
# footer are set regular in the design, and a first pass that shared one list
# rendered both bold.
COVER_SANS_BOLD_FACES = (
    (os.environ.get("DAILY_PODCAST_SANS_BOLD_FONT"), 0),
    ("/System/Library/Fonts/Helvetica.ttc", 1),
    ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 0),
)
COVER_SANS_TEXT_FACES = (
    (os.environ.get("DAILY_PODCAST_SANS_FONT"), 0),
    ("/System/Library/Fonts/Helvetica.ttc", 0),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 0),
)
COVER_MONO_FACES = (
    (os.environ.get("DAILY_PODCAST_MONO_FONT"), 0),
    ("/System/Library/Fonts/Menlo.ttc", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 0),
    ("/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf", 0),
)


def cover_headline(title: str, date_str: str) -> str:
    """The episode title with its ' - <date>' suffix (#139) removed.

    The cover shows the date on its own line, so repeating it inside the headline
    would spend the largest type on the canvas saying nothing. Only an exact
    trailing match is stripped — a title with a dash in the middle keeps it."""
    suffix = f" - {date_str}"
    if date_str and title.endswith(suffix):
        return title[: -len(suffix)]
    return title


def week_label(date_str: str) -> str:
    """`"2026-08-24"` -> `"Week of August 24, 2026"`.

    The weekly shows' date form. Matches the tail their episode titles carry
    (frontier-commits SKILL.md, "Title format") EXACTLY, because
    cover_headline_weekly strips what this prints — see the note there.

    `%-d` rather than `%d`: the title says "August 24" and "January 5", never
    "January 05", and a padded day here would silently stop the strip matching.
    """
    if not date_str:
        return ""
    return dt.datetime.strptime(date_str, "%Y-%m-%d").strftime("Week of %B %-d, %Y")


def cover_headline_weekly(title: str, date_str: str) -> str:
    """The episode title with its `" - Week of <Month D, YYYY>"` tail removed.

    The weekly counterpart to cover_headline. It exists because a weekly show's
    tail is not the ISO date, so the ISO strip misses and the tail survives into
    the largest type on the canvas — with the date then printed twice, in two
    formats, and the headline pushed to COVER_HEADLINE_MAX_LINES where real
    topics start dropping off the bottom.

    Built from week_label deliberately: the string the cover prints IS the string
    stripped here. Two independent implementations of "the weekly form" is the
    same bug in a new costume.

    Deliberately does NOT match the legacy `"Frontier Commits — week of ..."`
    form the two published episodes carry (pre-#161, and forbidden by SKILL.md
    since). Applied there it would leave the headline reading "Frontier Commits".
    """
    suffix = f" - {week_label(date_str)}"
    if date_str and title.endswith(suffix):
        return title[: -len(suffix)]
    return title


def _cover_face(image_font, faces, size: int, want_style: str = ""):
    """First face in `faces` that loads, preferring one whose style name contains
    `want_style` ("Bold"). Falls back to the first that loaded at all, so a host
    without the preferred weight still renders a cover instead of dying — this is
    art, not an invariant, and a slightly-wrong weight beats a failed run."""
    fallback = None
    for path, index in faces:
        if not path or not Path(path).exists():
            continue
        try:
            font = image_font.truetype(path, size, index=index)
        except Exception:
            continue
        if fallback is None:
            fallback = font
        if not want_style:
            return font
        try:
            style = (font.getname() or ("", ""))[1] or ""
        except Exception:
            style = ""
        if want_style.lower() in style.lower():
            return font
    if fallback is not None:
        return fallback
    # Last resort: the general-purpose chain, which carries the documented
    # DAILY_PODCAST_FONT override and die()s with an actionable message if even that
    # finds nothing. A cover in the wrong face still ships; no cover fails the run.
    return image_font.truetype(resolve_font(), size)


def _tracked_width(draw, text: str, font, tracking: float) -> float:
    """Rendered width of `text` at this tracking — the same sum _draw_tracked walks,
    so a fit check and the drawing can never disagree."""
    if not text:
        return 0.0
    return sum(draw.textlength(ch, font=font) for ch in text) + tracking * (len(text) - 1)


def _draw_tracked(draw, xy, text: str, font, fill, tracking: float) -> None:
    """Draw `text` with letter-spacing. Pillow has no tracking, so the glyphs are
    placed one at a time; the wide-set small caps in this design depend on it."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking


def _cover_wrap(draw, text: str, font, max_width: int) -> list[str]:
    """Greedy word-wrap to a pixel width."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [text]


def _fit_lockup(draw, image_font, text: str):
    """Shrink, then truncate, so the show-name lockup always fits beside the sun.

    A show name is arbitrary user config — the daily show's is still four words
    (#133) — and art that overflows ships to a public feed unnoticed. Shrinking
    alone is not enough: below COVER_LOCKUP_MIN_SIZE the small caps stop reading,
    so a name too long even at the floor loses its tail to an ellipsis rather than
    running under the disc."""
    size = COVER_LOCKUP_SIZE
    font = _cover_face(image_font, COVER_SANS_BOLD_FACES, size, "Bold")
    while (
        _tracked_width(draw, text, font, COVER_LOCKUP_TRACKING) > COVER_LOCKUP_MAX_W
        and size > COVER_LOCKUP_MIN_SIZE
    ):
        size -= 2
        font = _cover_face(image_font, COVER_SANS_BOLD_FACES, size, "Bold")
    while (
        len(text) > 1
        and _tracked_width(draw, text, font, COVER_LOCKUP_TRACKING) > COVER_LOCKUP_MAX_W
    ):
        text = text[:-2].rstrip() + "\u2026"
    return text, font


# Two cover designs live here, selected per SHOW rather than per invocation. The
# selector is a closed whitelist for the same reason ship_mode is: how a show looks
# is a property of the show, and a typo must die at validation rather than quietly
# restyle a published feed.
#
# #168 shipped without this, having deleted its own whitelist during a rebase —
# right at the time, because Frontier Commits was on cover_image and build_cover
# was never called for it, so the key would have been dead config. That premise is
# what this change removes: once a show renders its own art, the key is the thing
# that picks which art.
COVER_STYLE_ASCII = "ascii-horizon"
COVER_STYLES = (COVER_STYLE_ASCII,)


def resolve_cover_style(manifest: dict[str, Any]) -> str:
    """Cover design for this manifest. validate_manifest whitelists the value, so
    anything reaching here is already known-good. Absent means the house design —
    every manifest written before this key existed must keep rendering what it
    renders today."""
    return manifest.get("cover_style") or COVER_STYLE_ASCII


def _cover_ascii_horizon(out_path: Path, show_name: str, date_str: str, title_hint: str) -> None:
    """The house cover: an ASCII sun over a full-bleed horizon rule, with the
    episode's topics set large.

    The hierarchy is the whole point of the redesign. The old cover gave the show
    name 130px and the actual stories 44px along the bottom; a player already shows
    the show name, so here the topics take the size and the lockup goes small."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (COVER_SIZE, COVER_SIZE), COVER_GROUND)
    d = ImageDraw.Draw(img)

    mono = _cover_face(ImageFont, COVER_MONO_FACES, 27)
    date_font = _cover_face(ImageFont, COVER_SANS_TEXT_FACES, COVER_DATE_SIZE)
    footer_font = _cover_face(ImageFont, COVER_SANS_TEXT_FACES, COVER_FOOTER_SIZE)

    # The sun: every glyph placed on its own cell, so the disc's geometry comes from
    # this table and never from the font's advance width. That is what lets any
    # monospace render it — Menlo, DejaVu Sans Mono, JetBrains Mono — identically.
    for row, line in enumerate(ASCII_SUN):
        y = COVER_SUN_Y + row * ASCII_SUN_CELL_H
        for col, glyph in enumerate(line):
            if glyph == " ":
                continue
            d.text(
                (COVER_SUN_X + col * ASCII_SUN_CELL_W, y),
                glyph,
                font=mono,
                fill=COVER_SUN_INK,
            )

    lockup, lockup_font = _fit_lockup(d, ImageFont, show_name.upper())

    _draw_tracked(
        d,
        (COVER_MARGIN, COVER_LOCKUP_Y),
        lockup,
        lockup_font,
        COVER_AMBER,
        COVER_LOCKUP_TRACKING,
    )
    d.text((COVER_MARGIN, COVER_DATE_Y), date_str, font=date_font, fill=COVER_MUTED)

    # The horizon: full bleed, edge to edge. Inset it and the whole composition
    # turns into a box.
    d.rectangle(
        [(0, COVER_RULE_Y), (COVER_SIZE, COVER_RULE_Y + COVER_RULE_H - 1)],
        fill=COVER_AMBER,
    )

    headline = cover_headline(title_hint, date_str)
    max_width = COVER_SIZE - 2 * COVER_MARGIN
    size = COVER_HEADLINE_SIZE
    headline_font = _cover_face(ImageFont, COVER_SANS_BOLD_FACES, size, "Bold")
    lines = _cover_wrap(d, headline, headline_font, max_width)
    while len(lines) > COVER_HEADLINE_MAX_LINES and size > COVER_HEADLINE_MIN_SIZE:
        size -= 6
        headline_font = _cover_face(ImageFont, COVER_SANS_BOLD_FACES, size, "Bold")
        lines = _cover_wrap(d, headline, headline_font, max_width)
    lines = lines[:COVER_HEADLINE_MAX_LINES]

    # Bottom-anchored: a longer title grows up into the empty middle instead of down
    # into the footer.
    leading = int(size * COVER_HEADLINE_LEADING)
    y = COVER_HEADLINE_BOTTOM - leading * len(lines)
    for line in lines:
        d.text((COVER_MARGIN, y), line, font=headline_font, fill=COVER_PAPER)
        y += leading

    d.text(
        (COVER_MARGIN, COVER_FOOTER_Y),
        COVER_FOOTER,
        font=footer_font,
        fill=COVER_FOOTER_INK,
    )

    img.save(out_path, "JPEG", quality=88, optimize=True)


def build_cover(
    out_path: Path,
    show_name: str,
    date_str: str,
    title_hint: str,
    style: str = COVER_STYLE_ASCII,
) -> None:
    """Render this episode's cover in the show's cover style."""
    _cover_ascii_horizon(out_path, show_name, date_str, title_hint)


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


# Apple Podcasts and Spotify both require square art, 1400-3000px. A directory
# rejects the whole FEED over a bad image, not just the episode, so supplied art is
# gated at pre-flight rather than discovered at submission. build_cover's own output
# is 1400x1400 by construction, which is where these numbers come from.
COVER_MIN_PX = 1400
COVER_MAX_PX = 3000


def check_cover_image(path: Path) -> dict[str, Any]:
    """Pre-flight gate for a manifest-supplied cover: readable, square, in range.

    Returns the {"ok", "detail"} shape preflight's _check consumes. Never raises —
    an unreadable or corrupt file is a FAIL with a reason, not a traceback."""
    if not path.is_file():
        return {"ok": False, "detail": f"{path}: not found"}
    try:
        from PIL import Image

        with Image.open(path) as im:
            w, h = im.size
            fmt = im.format
    except Exception as e:
        return {"ok": False, "detail": f"{path}: not a readable image ({e})"}
    if w != h:
        return {"ok": False, "detail": f"{path}: must be square (got {w}x{h})"}
    if not COVER_MIN_PX <= w <= COVER_MAX_PX:
        return {
            "ok": False,
            "detail": f"{path}: must be {COVER_MIN_PX}-{COVER_MAX_PX}px square (got {w}px)",
        }
    return {"ok": True, "detail": f"{path} ({fmt} {w}x{h})"}


def apply_cover_image(src: Path, out_path: Path) -> None:
    """Install supplied art as the workdir cover, normalized to JPEG.

    The R2 publish hardcodes a `<slug>.jpg` key and an `image/jpeg` content-type
    (and save-to-spotify's --image expects a real image), so a PNG passed through
    verbatim would be served under a lying content type. An input that is ALREADY
    JPEG is byte-copied rather than re-encoded: the art is fixed for the life of
    the show, so a generation loss on every episode is pure downside."""
    from PIL import Image

    with Image.open(src) as im:
        already_jpeg = im.format == "JPEG"
        if not already_jpeg:
            im.convert("RGB").save(out_path, "JPEG", quality=88, optimize=True)
    if already_jpeg:
        shutil.copyfile(src, out_path)


# --- timeline + description ------------------------------------------------


def build_timeline_and_description(
    segments: list[dict],
    seg_paths: list[Path],
    silences_ms: list[int],
    summary: str,
    episode_mp3: Path,
    footer_html: str = DESCRIPTION_FOOTER,
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
    description = "".join(parts) + footer_html

    # Fit under Spotify's summary cap WITHOUT breaking the HTML: each list entry
    # is a self-contained <p>…</p>, so drop whole chapter blocks from the end
    # (longest-suffix-first) until it fits — never cut mid-tag, never ellipsize a
    # block. parts[0] is the summary <p> and is always preserved (it's the hook).
    # The timeline JSON above is untouched: the audio chapters still exist, only
    # the show-notes listing is trimmed. The footer (per-show since #152) is
    # pinned last rather than trimmed — it counts against the cap but is never a
    # drop candidate, so every episode keeps its credit line no matter how long
    # the chapter list is.
    if len(description) > SPOTIFY_SUMMARY_MAX_CHARS:
        kept = list(parts)
        while len(kept) > 1 and len("".join(kept)) + len(footer_html) > SPOTIFY_SUMMARY_MAX_CHARS:
            kept.pop()
        dropped = len(parts) - len(kept)
        log(
            f"description {len(description)} chars > {SPOTIFY_SUMMARY_MAX_CHARS} cap: "
            f"dropped {dropped} trailing chapter block(s) from show notes "
            "(timeline/audio chapters unaffected)"
        )
        description = "".join(kept) + footer_html

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
        if _is_cap_error(parsed):
            # Confirmed cap 429. Retrying without freeing a slot is useless, so this
            # branch never falls through to the transient retry below.
            if enabled:
                pruned = prune_episodes_for_capacity(
                    show_id, config, dry_run=dry_run, record=record
                )
                if pruned > 0:
                    log(f"auto-prune freed {pruned} slot(s); retrying upload once")
                    try:
                        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
                    except subprocess.CalledProcessError as e2:
                        # Retry also failed — fail with the diagnostic, never a second prune.
                        die(_command_failed_message(cmd, e2.stdout or "", e2.stderr or ""))
                    return _parse_upload_result(result.stdout)
            die(_command_failed_message(cmd, e.stdout or "", e.stderr or ""))

        # Not a cap error: the 2026-06-07 transient case, which failed once with an
        # empty stderr and succeeded on an immediate re-run. Retry exactly ONCE —
        # a loop here would hammer a genuinely broken upload path.
        log(f"upload failed (not a capacity error); retrying once in {UPLOAD_RETRY_DELAY_S}s")
        time.sleep(UPLOAD_RETRY_DELAY_S)
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e2:
            die(_command_failed_message(cmd, e2.stdout or "", e2.stderr or ""))
        log("upload succeeded on retry (transient failure)")
    return _parse_upload_result(result.stdout)


def _parse_upload_result(stdout: str) -> str:
    """episode_uri from a successful upload. The success payload is single-line JSON
    (json.loads over the whole stream has worked from 0.1.1 through 0.2.0, the latter
    re-checked 2026-08-22 against a throwaway show); keep that parse."""
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


def poll_ready(
    episode_id: str,
    timeout_s: int | None = None,
    *,
    show_id: str | None = None,
    config: dict[str, Any] | None = None,
) -> str:
    """Block until the episode is READY, or die.

    The window defaults to DEFAULT_POLL_TIMEOUT_S (30 min), not the original 10:
    Spotify legitimately took ~16 minutes to settle on 2026-07-28, and the short
    window turned a healthy episode into a failed run that needed a manual resume.
    A transient unknown status is waited through, never treated as terminal."""
    timeout_s = timeout_s if timeout_s is not None else resolve_poll_timeout(config)
    outcome = wait_for_readiness(episode_id, timeout_s, show_id=show_id)
    if outcome == "READY":
        return "READY"
    if outcome == "FAILED":
        die("episode processing FAILED")
    die(
        f"episode not READY after {timeout_s}s — it may still be PROCESSING; "
        "check the show listing and resume with the same --workdir before re-rendering"
    )
    return outcome  # unreachable: die() exits


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
        outcome = wait_for_readiness(episode_id, resolve_poll_timeout(None))
        if outcome == "FAILED":
            _abandon_inflight(rec, wd)
            return
        if outcome == "TIMEOUT":
            # Still plausibly PROCESSING. Leave the log intact so the next run
            # retries — the crash-safety guarantee this whole path is built on.
            die(
                f"in-flight episode {episode_uri} not READY yet; leaving the in-flight "
                "log in place for the next run"
            )
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


def _abandon_inflight(rec: dict[str, Any], workdir: Path | None) -> None:
    """Give up on an in-flight episode Spotify rejected, and let today's run proceed.

    This is the poison pill (2026-06-29, 2026-08-08). A FAILED leftover used to make
    `_recover_inflight` die on EVERY subsequent run — before today's episode rendered
    — and because the crash left inflight.json in place, it re-poisoned the next run
    too. One bad episode disabled the pipeline until a human deleted the file.

    Two things are deliberately NOT done here:
      - `covered.json` is not written. Those URLs never shipped, so they must return
        to the pool for the next run to re-cover; that is why the documented manual
        remedy is safe.
      - The dead episode is not deleted from Spotify. Deleting a published episode is
        irreversible and stays human-gated; the cap prune already prefers FAILED
        episodes, so it gets reclaimed on the next capacity prune anyway."""
    episode_uri = rec.get("episode_uri", "")
    log(f"in-flight recovery: {episode_uri} is FAILED server-side; abandoning it")

    # Record the artifact so a later run cannot re-upload the same cursed bytes.
    # With auto-prune on, each retry permanently deletes a published episode.
    mp3 = (workdir / "episode.mp3") if workdir else None
    profile: dict[str, Any] = {}
    if mp3 and mp3.exists():
        profile = probe_audio_profile(mp3)
        record_rejection(mp3, episode_uri=episode_uri, profile=profile, reason="processing FAILED")

    record = _RUN_CTX if _RUN_CTX is not None else _new_run_record()
    abandoned = {
        "episode_uri": episode_uri,
        "title": rec.get("title"),
        "source_urls": rec.get("source_urls", []),
        "artifact_profile": profile,
    }
    if _RUN_CTX is not None:
        existing = _RUN_CTX.get("abandoned_episodes") or []
        _RUN_CTX["abandoned_episodes"] = existing + [abandoned]
    write_incident(
        {**record, "episode_uri": episode_uri},
        kind="processing-failed",
        message=(
            f"episode processing FAILED for {episode_uri}; abandoned the in-flight record "
            f"so the pipeline is not blocked. {len(rec.get('source_urls', []))} source URL(s) "
            "returned to the curation pool (deliberately NOT marked covered). The dead "
            "episode still occupies a cap slot and will be reclaimed by the next prune."
        ),
    )
    _clear_inflight()
    log("in-flight recovery: abandoned; today's run continues")


def _resume(
    workdir: Path,
    marker: Path,
    segments: list[dict],
    title: str,
    manifest: dict[str, Any],
    record: dict[str, Any] | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> int:
    """
    Resume a run whose upload already succeeded (uploaded.json present). Skip TTS,
    cover, and upload; reuse the workdir artifacts and re-run only the idempotent
    tail: set_timeline -> poll_ready -> R2 publish -> dedup. This recovers the common
    failure — a poll_ready timeout where the episode is actually live and Spotify was
    just slow — without re-uploading a duplicate.

    R2 back-fill (#40): the resume tail also publishes to R2, mirroring the fresh
    path, so an episode that first failed at poll_ready and was later recovered still
    lands on the web feed. The publish is additive + non-fatal exactly as on the fresh
    path — it cannot block the dedup write below or change the exit code.

    INVARIANT CHANGE: this path used to be deliberately config-free, resolving R2
    from env only. That is precisely what made the web-feed publish silently skip on
    every recovery (2026-07-28) — `r2_bucket` / `r2_public_base_url` live in
    config.json, so an operator who had configured R2 correctly still got
    `r2_status: "skipped"` and a missing episode on the website, discovered days
    later. `_render` now resolves the config once and passes it in; `_resume` still
    never calls `load_config` itself, so it stays a pure function of its arguments
    and remains callable with `config=None` (env-only) for a bare recovery.

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
    poll_ready(episode_id, config=config)
    mark_stage(workdir, "poll_ready", readiness="READY", resumed=True)

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
            config or {},
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
    mark_stage(workdir, "dedup", urls=len(_segment_urls(segments)), resumed=True)
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


def _ship_web_only(
    config: dict[str, Any],
    *,
    manifest: dict[str, Any],
    workdir: Path,
    episode_mp3: Path,
    cover: Path,
    timeline: dict[str, Any],
    description: str,
    segments: list[dict],
    title: str,
    voice: str,
    voice_mode: str,
    loudnorm: dict[str, Any] | None,
    episode_duration_ms: int,
    record: dict[str, Any],
) -> int:
    """Ship an episode to the web feed only: publish to R2, then dedup. No upload,
    no timeline set, no readiness poll — save-to-spotify is never invoked (#155).

    Two inversions of the default path, both deliberate:

    * **The publish is load-bearing, not additive.** On the Spotify path a failed R2
      publish only warns, because the episode is already live where it counts. Here
      R2 is the only channel, so anything short of R2_PUBLISHED fails the run.
      `maybe_publish_r2` keeps its never-raises contract; the fatality is the
      caller's call, which is why this branch checks the status rather than the
      publisher changing behavior by mode.
    * **covered.json is written after the PUBLISH, not after READY.** Same
      only-after-success posture, new success condition: a failed publish must leave
      those source URLs in the pool for the next run to re-select.

    The dedup log records the public mp3 URL where the Spotify path records an
    episode URI — it is the episode's durable identity in this mode, and a null
    would lose the trail from a covered URL back to the episode that covered it."""
    # Belt and braces: pre-flight already required R2 here, but --skip-preflight can
    # bypass it, and a web-only render that "succeeds" while publishing nowhere is
    # the exact silent miss this mode exists to prevent.
    r2_cfg = load_r2_config(config)
    if r2_cfg is None:
        die(
            "ship_mode=web requires R2 (bucket, public base URL, and credentials) but "
            "none resolved; nothing was published and covered.json is untouched"
        )
    mp3_url = r2_episode_mp3_url(r2_cfg, manifest)

    r2_status = maybe_publish_r2(
        config,
        episode_mp3=episode_mp3,
        cover=cover,
        timeline=timeline,
        manifest=manifest,
        description=description,
        # No Spotify episode exists in this mode, so the web-feed entry carries no
        # spotify_uri (build_manifest_entry omits the field rather than nulling it).
        episode_uri=None,
    )
    record["r2_status"] = r2_status
    mark_stage(workdir, "r2", status=r2_status)
    if r2_status != R2_PUBLISHED:
        die(
            f"web-only publish did not succeed (r2={r2_status}); covered.json is "
            "untouched, so these sources return to the pool for the next run"
        )

    _save_dedup(segments, mp3_url)
    mark_stage(workdir, "dedup", urls=len(_segment_urls(segments)))

    chapter_count = sum(1 for it in timeline["items"] if "chapter" in it)
    duration_s = episode_duration_ms / 1000
    record.update(
        status="web-ready",
        mp3_url=mp3_url,
        chapter_count=chapter_count,
        duration_s=duration_s,
        resumed=False,
    )
    print(
        json.dumps(
            {
                "status": "web-ready",
                "mp3_url": mp3_url,
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
    return 0


# --- r2 publish ------------------------------------------------------------
#
# After Spotify (the canonical artifact) confirms READY, also publish the mp3 + a
# manifest entry to a Cloudflare R2 bucket. cortech.online reads that manifest at
# build time and renders /podcast/ + an iTunes RSS feed (schmug/cortech.online#131).
#
# On the DEFAULT path this is strictly additive: R2 is never allowed to block the
# dedup-log write or fail the run. A missing config no-ops; any publish error warns
# and continues. Runs on BOTH the fresh path and the resume path (#40): each
# publishes after READY and before the dedup-log write. The resume path stays
# config-free — it passes an empty config so R2 settings resolve from env /
# secrets.json only and never call load_config (pinned by
# test_resume_skips_upload_and_runs_idempotent_tail).
#
# Under `ship_mode: "web"` (#155) the SAME publisher is the ship itself, and its
# failure is fatal — but that decision lives in _ship_web_only, not here.
# maybe_publish_r2 never raises and never varies by mode; only the caller's
# treatment of R2_FAILED differs.


# Spelled out rather than taken from strftime("%B"), which is LC_TIME-dependent: these
# slugs are permanent public identifiers, and a run on a non-English box must never
# mint `daily-digest-agosto-23-2026` for a date that already published as `august`.
_LEGACY_TITLE_MONTHS = (
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
)


# The daily show's historical slug literal. The default keeps every published
# daily slug byte-identical (tests/data/published_slugs.tsv); a second show
# renames only this literal via the manifest's `slug_prefix` key (#162).
DEFAULT_SLUG_PREFIX = "daily-digest"


def slug_for_date(date: str, prefix: str = DEFAULT_SLUG_PREFIX) -> str:
    """Lowercase kebab slug matching the consumer schema's ^[a-z0-9-]+$. It keys both
    the R2 object (<slug>.mp3) and the /podcast/<slug>/ permalink, which cortech.online
    republishes as an isPermaLink <guid> — and Spotify treats a changed guid as a
    brand-new episode. So the slug must be stable for a given DATE, and deliberately
    cannot see the `title` (#128): the title is display-only free text that is expected
    to be rewritten, and coupling the two made retitling duplicate the back catalogue.

    The shape is not a fresh design — it reproduces the slugs already published, which
    were minted by running the old date-only titles ("Daily Digest - August 23, 2026")
    through the kebab normalizer below. Hence the historical prefix, the unpadded day
    and the comma. tests/data/published_slugs.tsv pins every live one byte-for-byte.

    `prefix` is the only per-show part (#162): validate_manifest guarantees it is
    already kebab, so a second show's slugs come out `<prefix>-<month>-<d>-<yyyy>`
    while the date-keyed, title-blind property stays untouched.
    """
    try:
        d = dt.datetime.strptime(date, "%Y-%m-%d").date()
        raw = f"{prefix} - {_LEGACY_TITLE_MONTHS[d.month - 1]} {d.day}, {d.year}"
    except (TypeError, ValueError):
        # validate_manifest never checked `date`, so a malformed one must still yield a
        # deterministic schema-valid slug rather than crash the publish. Same shape as
        # the empty-title fallback this replaced. Deliberately prefix-blind: the
        # fallback predates the per-show prefix and is pinned by tests.
        raw = f"episode-{date}"
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")[:80].strip("-")


def resolve_slug_date(manifest: dict[str, Any]) -> str:
    """The ISO date the slug is keyed on. An explicit manifest `date` wins, mirroring
    resolve_pubdate: a back-fill or archive re-render must reproduce that date's
    historical slug, not stamp the day it happened to be re-rendered."""
    return manifest.get("date") or dt.date.today().isoformat()


def resolve_slug_prefix(manifest: dict[str, Any]) -> str:
    """The slug's per-show literal (#162), DEFAULT_SLUG_PREFIX when unset — the daily
    show's slugs stay byte-identical. validate_manifest whitelists the value; only
    the literal varies per show, the date-keying (#128) is slug_for_date's own."""
    return manifest.get("slug_prefix") or DEFAULT_SLUG_PREFIX


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


def _r2_key_prefix(manifest: dict[str, Any]) -> str:
    """Episode/cover object-key prefix, "" when unset — the daily show's keys stay
    byte-identical. A second show sharing the bucket sets r2_key_prefix so its
    date-keyed slug cannot mint the daily show's same-day keys (#142);
    validate_manifest whitelists the value."""
    return manifest.get("r2_key_prefix") or ""


def r2_episode_mp3_url(cfg: dict[str, Any], manifest: dict[str, Any]) -> str:
    """Public URL of the episode mp3 for this manifest. The --dry-run preview and the
    real publish both resolve it here, so a rehearsal can never advertise a URL the
    publish would not actually write (#128)."""
    base = cfg["public_base_url"].rstrip("/")
    slug = slug_for_date(resolve_slug_date(manifest), resolve_slug_prefix(manifest))
    return f"{base}/{_r2_key_prefix(manifest)}{slug}.mp3"


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
        title = manifest["title"]
        slug = slug_for_date(resolve_slug_date(manifest), resolve_slug_prefix(manifest))
        base = cfg["public_base_url"].rstrip("/")
        immutable = "public, max-age=31536000, immutable"

        # mp3 first: the manifest must never reference an object that isn't up yet.
        key_prefix = _r2_key_prefix(manifest)
        mp3_key = f"{key_prefix}{slug}.mp3"
        _r2_put(
            client,
            cfg["bucket"],
            mp3_key,
            episode_mp3.read_bytes(),
            "audio/mpeg",
            cache_control=immutable,
        )
        mp3_url = r2_episode_mp3_url(cfg, manifest)

        # Cover is best-effort: a flaky image upload must not sink the episode.
        cover_url: str | None = None
        if cover and Path(cover).exists():
            try:
                cover_key = f"{key_prefix}{slug}.jpg"
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
        #
        # A second show sharing this bucket names its own manifest object via the
        # optional r2_manifest_name key (validate_manifest whitelists it to a bare
        # *.json filename), keeping its web feed out of the daily show's
        # manifest.json. Absent key -> "manifest.json", byte-identical to before.
        manifest_key = manifest.get("r2_manifest_name") or "manifest.json"
        entries = upsert_manifest(_r2_get_manifest(client, cfg["bucket"], manifest_key), entry)
        _r2_put(
            client,
            cfg["bucket"],
            manifest_key,
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
        # Deliberately says nothing about consequences: whether a failed publish is
        # survivable depends on the ship mode, and only the caller knows. The old
        # "non-fatal, Spotify episode is live" reassurance became a lie under
        # ship_mode=web, printed at exactly the moment an operator is deciding
        # whether to panic. The caller's next line carries the meaning.
        log(f"[r2] publish failed: {e}")
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
            checks.append(_check("save-to-spotify-auth", False, _shows_failure_detail(proc)))
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


# --- durable run state -----------------------------------------------------
#
# A dropped connection used to cost a whole render: the auto workdir was a random
# mkdtemp(), so there was nothing to resume into. state.json + a deterministic
# per-date workdir make every run resumable, not just the ones where someone
# remembered to pass --workdir.


def default_workdir(today: dt.date | None = None) -> Path:
    """The auto workdir for a given day. Deterministic (not mkdtemp) so a crashed
    run can be resumed by re-invoking with no arguments at all: the second run
    lands in the same directory and reuses its TTS cache, artifacts, and state."""
    day = (today or dt.date.today()).isoformat()
    return TMP_BASE / f"{WORKDIR_PREFIX}{day}"


def _state_path(workdir: Path) -> Path:
    return Path(workdir) / STATE_FILENAME


def load_state(workdir: Path) -> dict[str, Any]:
    """The workdir's stage checkpoints, or an empty state. Best-effort by contract:
    a corrupt state file degrades to "nothing completed" (the run simply redoes
    work) rather than wedging every future run — same posture as load_covered."""
    path = _state_path(workdir)
    if not path.exists():
        return {"stages": {}}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        log(f"warn: {path} unreadable/malformed, treating as no completed stages")
        return {"stages": {}}
    if not isinstance(data, dict) or not isinstance(data.get("stages"), dict):
        return {"stages": {}}
    return data


def save_state(workdir: Path, state: dict[str, Any]) -> None:
    try:
        _atomic_write_text(_state_path(workdir), json.dumps(state, indent=2))
    except OSError as e:
        # Checkpointing is observability + a resume hint, never a gate. Losing it
        # costs redone work on the next attempt; it must not sink a live run.
        log(f"warn: could not write {_state_path(workdir)}: {e}")


def mark_stage(workdir: Path, stage: str, **data: Any) -> dict[str, Any]:
    """Checkpoint `stage` as complete, carrying whatever metadata the caller knows."""
    state = load_state(workdir)
    state.setdefault("stages", {})[stage] = {
        **data,
        "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    save_state(workdir, state)
    return state


def stage_done(state: dict[str, Any], stage: str) -> bool:
    stages = state.get("stages")
    return isinstance(stages, dict) and stage in stages


# --- artifact gate ---------------------------------------------------------


def artifact_fingerprint(path: Path) -> str:
    """sha256 of the rendered episode. Content-addressed on purpose: the 2026-08-08
    incident showed Spotify's rejection follows the *bytes*, so identity — not the
    episode URI or the title — is what a blocklist has to key on."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def probe_audio_profile(path: Path) -> dict[str, Any]:
    """ffprobe's view of the first audio stream: codec, channels, sample rate.
    Returns {} when ffprobe is unavailable or the probe fails — an unprobeable file
    yields no *evidence of a defect*, and verify_artifact must not invent one."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name,channels,sample_rate",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        streams = json.loads(result.stdout).get("streams") or []
    except (subprocess.CalledProcessError, FileNotFoundError, OSError, json.JSONDecodeError) as e:
        log(f"warn: could not probe {path}: {e}")
        return {}
    return streams[0] if streams else {}


def load_rejected_fingerprints() -> set[str]:
    """Every artifact sha256 Spotify has rejected. Corrupt lines are skipped, not
    fatal — a malformed log must never block a legitimate ship."""
    if not REJECTIONS_PATH.exists():
        return set()
    out: set[str] = set()
    try:
        text = REJECTIONS_PATH.read_text()
    except OSError as e:
        log(f"warn: could not read {REJECTIONS_PATH}: {e}")
        return set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        sha = rec.get("sha256") if isinstance(rec, dict) else None
        if isinstance(sha, str) and sha:
            out.add(sha)
    return out


def record_rejection(mp3: Path, *, episode_uri: str, profile: dict[str, Any], reason: str) -> None:
    """Append one rejected artifact to rejections.jsonl. Append-only like runs.jsonl
    (never _atomic_write_text, which would clobber the history to a single line)."""
    try:
        rec = {
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "sha256": artifact_fingerprint(mp3),
            "episode_uri": episode_uri,
            "profile": profile,
            "reason": reason,
        }
        REJECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(REJECTIONS_PATH, "a") as f:
            f.write(json.dumps(rec) + "\n")
        log(f"recorded rejected artifact {rec['sha256'][:12]}… ({reason})")
    except OSError as e:
        log(f"warn: could not append {REJECTIONS_PATH}: {e}")


def _new_blooper_record() -> dict[str, Any]:
    return dict.fromkeys(BLOOPER_FIELDS, None)


def bank_blooper(audio: Any, *, reason: str, **fields: Any) -> dict[str, Any] | None:
    """Copy one clip into the bin and append its index row. Returns the record, or
    None when nothing was banked.

    Best-effort by contract, exactly like write_run_log and the incident reports: it
    NEVER raises and never changes a run's exit code. A full disk loses a joke, not
    an episode — and this runs on failure paths, where a recovery that can itself
    crash is worse than no recovery.

    Content-addressed: the clip's name is its own hash, so identical bytes can only
    land once. That is what makes a same-day resume (which re-runs the gate against a
    cache-hit segment) a no-op instead of a duplicate, and it is why a re-bank returns
    None rather than a second row."""
    try:
        source = Path(audio)
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        clips = BLOOPER_DIR / "clips"
        clips.mkdir(parents=True, exist_ok=True)
        clip = clips / f"{digest[:16]}.mp3"
        if clip.exists():
            return None
        # Write-then-rename so a crash mid-copy cannot leave a truncated clip under a
        # hash that claims to describe its contents.
        staged = clip.with_suffix(".part")
        staged.write_bytes(data)
        staged.replace(clip)

        record = _new_blooper_record()
        # Filtered to the known field set: an unrecognised key here would put a row in
        # the index that no other row has, breaking the line-by-line read contract.
        record.update({k: v for k, v in fields.items() if k in BLOOPER_FIELDS})
        record.update(
            {
                "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                "reason": reason,
                "sha256": digest,
                "clip": str(clip),
                "source": str(source),
            }
        )
        # Append-only, never _atomic_write_text — same reason as runs.jsonl: an
        # atomic replace would truncate the archive to its newest row.
        with open(BLOOPER_DIR / "index.jsonl", "a") as f:
            f.write(json.dumps(record) + "\n")
        return record
    except (OSError, TypeError, ValueError) as e:
        log(f"warn: could not bank blooper from {audio}: {e}")
        return None


def capture_rate_bloopers(
    segments: list[dict],
    seg_paths: list[Path],
    *,
    dry_run: bool = False,
    **ctx: Any,
) -> list[dict[str, Any]]:
    """Bank every body segment whose speech rate is odd, in two bands off one
    measurement.

    Below MIN_SPEECH_RATE_RATIO is what the gate is about to reject — and whose
    documented recovery ("delete that seg_NN.mp3 from the workdir and re-run") is
    precisely what has been destroying the only funny audio this pipeline makes.
    Between that floor and NEAR_MISS_RATE_RATIO the episode SHIPS: a phrase that
    garbled too briefly to move the segment's rate is invisible to the gate, which
    only ever catches a gross derailment.

    Called BEFORE verify_artifact rather than after, so no branch — and no die() —
    can sit between measuring a segment and copying it out.

    --dry-run banks nothing: a rehearsal must not mutate user state, the same posture
    that keeps it out of covered.json and stops --prune-workdirs deleting anything."""
    banked: list[dict[str, Any]] = []
    for row in speech_rate_rows(segments, seg_paths):
        if row["rate"] < row["median"] * MIN_SPEECH_RATE_RATIO:
            reason = "gate"
        elif row["rate"] < row["median"] * NEAR_MISS_RATE_RATIO:
            reason = "near-miss"
        else:
            continue
        if dry_run:
            log(
                f"would bank {reason} blooper: segment {row['number']} at "
                f"{row['rate']:.1f} chars/sec ({row['ratio']:.2f}x median)"
            )
            continue
        seg = segments[row["index"]]
        record = bank_blooper(
            row["path"],
            reason=reason,
            segment=row["number"],
            chars=row["chars"],
            duration_ms=row["duration_ms"],
            rate=round(row["rate"], 2),
            median=round(row["median"], 2),
            ratio=round(row["ratio"], 3),
            text=seg.get("text"),
            source_url=seg.get("source_url"),
            **ctx,
        )
        if record:
            banked.append(record)
    if banked:
        log(f"banked {len(banked)} blooper clip(s) in {BLOOPER_DIR}")
    return banked


def _safe_duration_ms(path: Path) -> int | None:
    """mp3_duration_ms without the exception. The sweep runs on the failure path,
    where assuming ffprobe works is how a recovery becomes a second crash."""
    try:
        return mp3_duration_ms(path)
    except Exception:  # noqa: BLE001 — a measurement is nice to have, never required
        return None


def capture_workdir_segments(
    workdir: Any,
    *,
    error_message: str | None = None,
    dry_run: bool = False,
    **ctx: Any,
) -> list[dict[str, Any]]:
    """Sweep a dead run's workdir into the bin before the workdir is gone.

    Suppressed for a speech-rate rejection: capture_rate_bloopers has already banked
    the precise offending segment with the rate evidence that condemned it, and
    sweeping would bank the eleven clean segments beside it — burying the one clip
    worth keeping under an episode's worth of ordinary narration. Every other failure
    identifies no segment at all, so there the sweep is the only thing that saves the
    audio.

    Most failures are upload/poll problems whose audio is perfectly fine, so this
    deliberately banks non-bloopers; `reason` is what keeps them siftable."""
    if error_message and classify_incident(error_message) == "tts-degeneration":
        return []
    try:
        wd = Path(workdir)
        seg_paths = sorted(wd.glob("seg_*.mp3"))
    except (OSError, TypeError, ValueError):
        return []
    if not seg_paths:
        return []

    # The manifest is sitting right next to the segments and carries what each one was
    # supposed to say — a swept clip without its script is a sound with no story. Its
    # absence or corruption is not a reason to lose the audio.
    manifest: dict[str, Any] = {}
    try:
        manifest = json.loads((wd / "manifest.json").read_text())
        # A scene's script lives in its `lines`, so derive the same text the run
        # measured — otherwise a swept multi-voice clip banks with no script at all.
        materialize_line_text(manifest)
    except (OSError, ValueError):
        pass
    manifest_segments = manifest.get("segments") or []

    banked: list[dict[str, Any]] = []
    for path in seg_paths:
        number = int(path.stem.split("_")[-1]) if path.stem.split("_")[-1].isdigit() else None
        seg = {}
        if number is not None and 0 < number <= len(manifest_segments):
            seg = manifest_segments[number - 1] or {}
        if dry_run:
            log(f"would bank run-failed blooper: {path.name}")
            continue
        record = bank_blooper(
            path,
            reason="run-failed",
            segment=number,
            duration_ms=_safe_duration_ms(path),
            text=seg.get("text"),
            source_url=seg.get("source_url"),
            chars=len(seg.get("text") or "") or None,
            title=manifest.get("title"),
            workdir=str(wd),
            **ctx,
        )
        if record:
            banked.append(record)
    if banked:
        log(f"banked {len(banked)} clip(s) from the failed run in {BLOOPER_DIR}")
    return banked


def speech_rate_rows(segments: list[dict], seg_paths: list[Path]) -> list[dict[str, Any]]:
    """Measure every body segment's speech rate against the population median.

    Only segments with a `source_url` count: the intro and sign-off are short and
    legitimately slower (16.5 / 16.9 c/s against an 18.4 median on 2026-08-17), so
    they neither join the population nor get judged by it. The median — not the
    mean — is the reference precisely because the outlier being detected drags a
    mean down toward itself.

    Durations come from mp3_duration_ms, the same per-segment measurement
    plan_silences and build_timeline_and_description already use, so this adds no
    second measurement path, no network call, and no model load.

    Returns rows rather than formatted strings because two consumers now share this
    one measurement: the gate (which rejects the low outliers) and the bloopers bin
    (which banks them, plus the near-misses the gate lets through). An empty list
    means "no evidence of a defect" — the caller cannot tell a too-small population
    apart from an all-clean one, and must not try."""
    measured: list[dict[str, Any]] = []
    for i, seg in enumerate(segments[: len(seg_paths)]):
        if not seg.get("source_url"):
            continue
        chars = len(seg.get("text") or "")
        duration_ms = mp3_duration_ms(seg_paths[i])
        if chars <= 0 or duration_ms <= 0:
            continue  # unmeasurable: no evidence of a defect, so don't invent one
        measured.append(
            {
                "index": i,
                "number": i + 1,  # 1-based, matching the render log
                "path": seg_paths[i],
                "chars": chars,
                "duration_ms": duration_ms,
                "rate": chars / (duration_ms / 1000),
            }
        )

    if len(measured) < MIN_RATE_SAMPLE_SEGMENTS:
        return []
    median = statistics.median(row["rate"] for row in measured)
    if median <= 0:
        return []

    for row in measured:
        row["median"] = median
        row["ratio"] = row["rate"] / median
    return measured


def speech_rate_problems(segments: list[dict], seg_paths: list[Path]) -> list[str]:
    """Reject the low outliers speech_rate_rows measured.

    The wording is load-bearing twice over: classify_incident matches "speech rate" to
    route the operator to incidents/tts-degeneration.md, and the message names the
    segment, its rate and the median so the recovery needs no second lookup."""
    return [
        f"segment {row['number']} speech rate {row['rate']:.1f} chars/sec is "
        f"{row['ratio']:.2f}x the {row['median']:.1f} chars/sec median "
        f"(floor {MIN_SPEECH_RATE_RATIO:.2f}x) — the TTS "
        "model likely degenerated mid-segment and left part of the script unspoken; "
        "re-render it (delete that seg_NN.mp3 from the workdir and re-run) before "
        "shipping — the clip is already banked in the bloopers bin, so deleting it here "
        "loses nothing"
        for row in speech_rate_rows(segments, seg_paths)
        if row["rate"] < row["median"] * MIN_SPEECH_RATE_RATIO
    ]


def verify_artifact(
    mp3: Path,
    timeline: dict[str, Any],
    *,
    duration_ms: int,
    profile: dict[str, Any],
    segments: list[dict] | None = None,
    seg_paths: list[Path] | None = None,
) -> list[str]:
    """Local conformance gate, run after render and before upload. Returns a list of
    human-readable problems (empty == good).

    Scope note, because it is easy to over-claim: the 2026-08-08 rejected artifact
    passed every check here. This gate does NOT diagnose server-side processing
    rejection — nothing local does. It catches render regressions, and it refuses a
    byte-identical retry of an artifact Spotify already rejected, which is the one
    thing the incident actually proved (and which is destructive now that a retry
    prunes a published episode to free a cap slot).

    Since 2026-08-17 it also catches one *content* defect: a segment the TTS model
    garbled into looping babble, spotted as a speech-rate outlier. That is a
    statistical smell, not a transcript check — it catches the gross derailment that
    shipped, and would miss a short mangled phrase that barely moves the rate.
    `segments`/`seg_paths` stay optional so a caller with no per-segment view still
    gets everything above."""
    errors: list[str] = []

    try:
        fingerprint = artifact_fingerprint(mp3)
    except OSError as e:
        return [f"cannot read artifact {mp3}: {e}"]
    if fingerprint in load_rejected_fingerprints():
        errors.append(
            f"artifact was previously rejected by Spotify (sha256 {fingerprint[:12]}…); "
            "re-uploading identical bytes reproduces the failure and costs a pruned episode"
        )

    for key, expected in ENCODER_PROFILE.items():
        if key not in profile:
            continue  # unprobeable: no evidence of a defect, so don't invent one
        if str(profile[key]) != str(expected):
            errors.append(f"encoder {key} is {profile[key]!r}, expected {expected!r}")

    starts = [
        item["chapter"].get("start_ms")
        for item in timeline.get("items", [])
        if isinstance(item, dict) and isinstance(item.get("chapter"), dict)
    ]
    starts = [s for s in starts if isinstance(s, int)]
    if starts != sorted(starts) or len(set(starts)) != len(starts):
        errors.append("chapter starts must be strictly monotonic")
    elif starts:
        if starts[-1] >= duration_ms:
            errors.append(
                f"last chapter starts at {starts[-1]}ms, at or past the "
                f"{duration_ms}ms episode duration"
            )
        # Sub-30s chapters are fine (upstream PR #44). The live rule is the gap
        # between consecutive chapter *starts*; the final chapter is exempt
        # because it has no successor start to measure against.
        gaps = [b - a for a, b in zip(starts, starts[1:], strict=False)]
        tight = [g for g in gaps if g < MIN_CHAPTER_GAP_MS]
        if tight:
            errors.append(
                f"{len(tight)} chapter(s) start less than {MIN_CHAPTER_GAP_MS}ms apart "
                f"(smallest gap {min(tight)}ms); Spotify requires consecutive chapter "
                f"starts to be at least {MIN_CHAPTER_GAP_MS // 1000}s apart"
            )

    if segments is not None and seg_paths is not None:
        errors.extend(speech_rate_problems(segments, seg_paths))
    return errors


# --- readiness polling -----------------------------------------------------


def resolve_poll_timeout(config: dict[str, Any] | None) -> int:
    """Seconds to wait for Spotify processing. Config `poll_timeout_s` wins; an
    unparseable or non-positive value falls back to the default rather than
    turning the poll into an instant failure."""
    raw = (config or {}).get("poll_timeout_s", DEFAULT_POLL_TIMEOUT_S)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_POLL_TIMEOUT_S
    return value if value > 0 else DEFAULT_POLL_TIMEOUT_S


def episode_status(episode_id: str, show_id: str | None = None) -> str | None:
    """Readiness for one episode: READY / FAILED / PROCESSING / NOT_READY, or None
    when it cannot be determined *right now*.

    None means "unknown, ask again" — never "gone". The show listing intermittently
    omits a just-uploaded episode (2026-07-28), and a caller that treats that as
    terminal reports a phantom disappearance for an episode that is present and
    PROCESSING on the very next query."""
    try:
        result = run(["save-to-spotify", "--json", "episodes", "status", episode_id])
        data = _first_json_line(result.stdout)
        if isinstance(data, dict) and data.get("readiness"):
            return str(data["readiness"])
    except (SystemExit, subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        pass  # fall through to the listing form

    if not show_id:
        return None
    # Documented fallback: `episodes status <id> --show-id <show>` rejects the flag,
    # so the listing form is the only other way to read server state.
    try:
        for ep in _list_episodes(show_id):
            uri = ep.get("episode_uri") or ""
            if uri.removeprefix("spotify:episode:") == episode_id:
                return str(ep.get("status")) if ep.get("status") else None
    except (SystemExit, subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        return None
    return None


def wait_for_readiness(
    episode_id: str,
    timeout_s: int,
    *,
    show_id: str | None = None,
) -> str:
    """Poll until the episode settles. Returns "READY", "FAILED", or "TIMEOUT".

    Unlike poll_ready this never exits the process, so callers that must survive a
    terminal failure (in-flight recovery, which has to unblock today's episode) can
    branch on the result instead of dying."""
    deadline = time.time() + timeout_s
    while True:
        status = episode_status(episode_id, show_id=show_id)
        log(f"  status: {status or 'unknown (transient)'}")
        if status == "READY":
            return "READY"
        if status == "FAILED":
            return "FAILED"
        if time.time() >= deadline:
            return "TIMEOUT"
        time.sleep(POLL_INTERVAL_S)


# --- pre-flight ------------------------------------------------------------


def check_r2_credentials(config: dict[str, Any], *, required: bool = False) -> dict[str, Any]:
    """Three-state R2 readiness: configured / absent / partial.

    `absent` is a PASS — the web feed is optional and a show without one must still
    ship. `partial` is a FAIL, and that asymmetry is the whole point: a half-configured
    R2 is exactly the 2026-07-28 shape where the episode ships to Spotify and silently
    never reaches the website, which is only noticed days later.

    `required=True` (web-only mode, #155) collapses that asymmetry: with R2 as the
    only channel, `absent` becomes a FAIL too — otherwise a misconfigured host pays
    for a full TTS render and ships the episode precisely nowhere. `partial` stays a
    FAIL either way."""
    secrets = _load_r2_secrets()
    fields = {
        "R2_ACCOUNT_ID": secrets.get("R2_ACCOUNT_ID"),
        "R2_ACCESS_KEY_ID": secrets.get("R2_ACCESS_KEY_ID"),
        "R2_SECRET_ACCESS_KEY": secrets.get("R2_SECRET_ACCESS_KEY"),
        "R2_BUCKET": os.environ.get("R2_BUCKET") or config.get("r2_bucket"),
        "R2_PUBLIC_BASE_URL": (
            os.environ.get("R2_PUBLIC_BASE_URL") or config.get("r2_public_base_url")
        ),
    }
    missing = sorted(k for k, v in fields.items() if not v)
    if not missing:
        return {"ok": True, "state": "configured", "detail": "all R2 settings resolved"}
    if len(missing) == len(fields):
        if required:
            return {
                "ok": False,
                "state": "absent",
                "detail": "R2 not configured, but this manifest is web-only "
                "(ship_mode=web) and the R2 publish is the ship",
            }
        return {"ok": True, "state": "absent", "detail": "R2 not configured (web feed disabled)"}
    return {
        "ok": False,
        "state": "partial",
        "detail": f"R2 partially configured; missing {', '.join(missing)}",
    }


def _episode_cap(config: dict[str, Any]) -> int:
    try:
        cap = int(config.get("episode_cap", EPISODE_CAP_DEFAULT))
    except (TypeError, ValueError):
        return EPISODE_CAP_DEFAULT
    return cap if cap > 0 else EPISODE_CAP_DEFAULT


def preflight_capacity(
    show_id: str,
    config: dict[str, Any],
    *,
    dry_run: bool,
    record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare the show's episode count against the cap and reclaim a slot *before*
    the render, not after.

    The cap-429 auto-prune already self-heals, but only reactively: it fires after a
    full ~5-minute TTS render has been spent on an upload that was always going to
    fail. Checking first makes the cap cost nothing."""
    cap = _episode_cap(config)
    episodes = _list_episodes(show_id)
    count = len(episodes)
    if count < cap:
        return {"ok": True, "count": count, "pruned": 0, "detail": f"{count}/{cap} episodes"}

    if dry_run:
        return {
            "ok": True,
            "count": count,
            "pruned": 0,
            "detail": f"{count}/{cap} at cap (dry-run: no prune)",
        }

    enabled, _ = _prune_config(config)
    if not enabled:
        return {
            "ok": False,
            "count": count,
            "pruned": 0,
            "detail": (
                f"{count}/{cap} at the episode cap and auto_prune_episodes is disabled; "
                "enable it in config.json or delete an episode manually"
            ),
        }

    pruned = prune_episodes_for_capacity(show_id, config, dry_run=dry_run, record=record)
    return {
        "ok": pruned > 0,
        "count": count,
        "pruned": pruned,
        "detail": (
            f"{count}/{cap} at cap; pruned {pruned} to free a slot"
            if pruned
            else f"{count}/{cap} at cap and nothing was eligible to prune"
        ),
    }


def preflight(
    config: dict[str, Any],
    *,
    show_id: str | None,
    dry_run: bool,
    record: dict[str, Any] | None = None,
    web_only: bool = False,
    cover_image: Path | None = None,
) -> tuple[bool, list[dict[str, Any]]]:
    """Verify everything the run depends on BEFORE spending a render on it.

    Ordered so the cheap local checks fail fast and the network ones only run when
    they can matter. `--dry-run` runs the local subset only: by contract a dry run
    never calls Spotify and never prunes.

    `web_only` (#155) drops every Spotify-shaped gate — show id, auth probe, episode
    capacity — because that mode never talks to save-to-spotify at all, and flips the
    R2 check from optional to required. What remains is the local subset plus R2.

    `cover_image` (#164) is checked only when the manifest supplies one — a local
    check, so a --dry-run rehearsal gates the same art a real run would ship."""
    checks: list[dict[str, Any]] = []
    log("preflight: verifying dependencies, credentials, and capacity...")

    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        checks.append(_check(tool, path is not None, path or "not found on PATH"))

    profile_ok = ENCODER_PROFILE == {
        "codec_name": "mp3",
        "channels": AUDIO_CHANNELS,
        "sample_rate": AUDIO_SAMPLE_RATE,
    }
    checks.append(
        _check(
            "encoder-profile",
            profile_ok,
            f"{AUDIO_CHANNELS}ch @ {AUDIO_SAMPLE_RATE}Hz {AUDIO_BITRATE} {AUDIO_CODEC}",
        )
    )

    audio_ok = USER_HOUSE_AUDIO.exists() or BUNDLED_HOUSE_AUDIO.exists()
    text_ok = USER_HOUSE_TEXT.exists() or BUNDLED_HOUSE_TEXT.exists()
    checks.append(
        _check(
            "house-voice",
            audio_ok and text_ok,
            "ref wav + transcript present"
            if audio_ok and text_ok
            else "ref wav/transcript missing",
        )
    )

    checks.append(_tts_module_check())

    if cover_image is not None:
        art = check_cover_image(cover_image)
        checks.append(_check("cover-image", art["ok"], art["detail"]))

    if not web_only:
        checks.append(
            _check("show-id", bool(show_id), show_id or "no show_id in manifest or config.json")
        )

    r2 = check_r2_credentials(config, required=web_only)
    checks.append(_check("r2-credentials", r2["ok"], r2["detail"]))

    if not dry_run and not web_only:
        checks.append(_spotify_auth_check())
        if show_id:
            cap = preflight_capacity(show_id, config, dry_run=dry_run, record=record)
            checks.append(_check("episode-capacity", cap["ok"], cap["detail"]))

    ok = all(c["ok"] for c in checks)
    if record is not None:
        record["preflight"] = {"ok": ok, "checks": checks}
    log(f"preflight: {'PASS' if ok else 'FAIL'} ({sum(c['ok'] for c in checks)}/{len(checks)})")
    return ok, checks


def _tts_module_check() -> dict[str, Any]:
    """Is the TTS backend importable?

    A `find_spec` probe, not a real import — importing mlx_audio pulls in MLX and
    costs seconds, which a gate that runs on every render cannot afford. Gating
    (not advisory), and it runs under --dry-run too, because a dry run still
    renders audio. This check exists because a rehearsal got all the way to
    "loading mlx-community/Qwen3-TTS..." before discovering the module was absent."""
    try:
        spec = importlib.util.find_spec("mlx_audio")
    except (ImportError, ValueError):
        spec = None
    return _check(
        "tts-module",
        spec is not None,
        "mlx_audio importable" if spec else "mlx_audio not installed (see requirements.txt)",
    )


def _spotify_auth_check() -> dict[str, Any]:
    """`save-to-spotify --json shows` as an auth liveness probe. Deliberately not
    routed through run() — a failing check must be recorded, not exit the process."""
    try:
        proc = subprocess.run(
            ["save-to-spotify", "--json", "shows"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return _check("save-to-spotify-auth", False, "save-to-spotify not on PATH")
    except subprocess.TimeoutExpired:
        return _check("save-to-spotify-auth", False, "shows timed out (auth/network?)")
    if proc.returncode != 0:
        return _check("save-to-spotify-auth", False, _shows_failure_detail(proc))
    try:
        json.loads(proc.stdout)
    except json.JSONDecodeError:
        return _check("save-to-spotify-auth", False, "shows did not return valid JSON")
    return _check("save-to-spotify-auth", True, "shows returned valid JSON")


# --- incident capture ------------------------------------------------------


def incident_dir() -> Path:
    """Where runtime incident reports land. Env override first so an operator (or a
    test) can redirect them without touching config."""
    env = os.environ.get("DAILY_PODCAST_INCIDENT_DIR")
    return Path(env) if env else INCIDENT_DIR


# Signature -> incident slug. Each maps to a file in the repo's incidents/ directory,
# so a report names the playbook that already covers it.
_INCIDENT_SIGNATURES: tuple[tuple[str, str], ...] = (
    ("RATE_LIMIT_EXCEEDED", "episode-cap"),
    ("processing FAILED", "processing-failed"),
    ("not READY after", "poll-timeout"),
    ("cannot resume", "resume-blocked"),
    ("401", "auth-failure"),
    ("authentication", "auth-failure"),
    ("preflight failed", "preflight-failed"),
    ("previously rejected", "rejected-artifact"),
    ("speech rate", "tts-degeneration"),
    ("manifest", "manifest-invalid"),
)


def classify_incident(message: str) -> str:
    """Map an error string to a known failure mode, or "unclassified" — which is the
    interesting bucket: it means a new failure mode nobody has written up yet."""
    text = message or ""
    lowered = text.lower()
    for needle, slug in _INCIDENT_SIGNATURES:
        if needle in text or needle.lower() in lowered:
            return slug
    return "unclassified"


def _incident_markdown(record: dict[str, Any], kind: str, message: str, when: str) -> str:
    lines = [
        f"# Incident: {kind}",
        "",
        f"- **When:** {when}",
        f"- **Kind:** `{kind}`",
        f"- **Status:** `{record.get('status')}`",
        f"- **Episode:** `{record.get('episode_uri')}`",
        f"- **Workdir:** `{record.get('workdir')}`",
        f"- **Manifest:** `{record.get('manifest_path')}`",
        f"- **render.py:** `{record.get('git_sha')}`",
        "",
        "## Message",
        "",
        "```",
        message,
        "```",
        "",
        "## Next step",
        "",
        (
            f"See `incidents/{kind}.md` for the playbook and the test that guards it."
            if kind != "unclassified"
            else "No playbook covers this yet — this is a NEW failure mode. "
            "Write it up in `incidents/` and add a guarding test."
        ),
        "",
    ]
    return "\n".join(lines)


def write_incident(record: dict[str, Any], *, kind: str, message: str) -> Path | None:
    """Write a structured incident report (markdown + json sidecar) for a future run
    — or a human — to pick up and codify. Returns the markdown path, or None.

    Best-effort by contract, exactly like write_run_log: a failure to write an
    incident must never change the exit code of the run that produced it."""
    try:
        target = incident_dir()
        target.mkdir(parents=True, exist_ok=True)
        now = dt.datetime.now(dt.timezone.utc)
        stem = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{kind}"
        path = target / f"{stem}.md"
        path.write_text(_incident_markdown(record, kind, message, now.isoformat()))
        (target / f"{stem}.json").write_text(
            json.dumps(
                {"kind": kind, "message": message, "timestamp": now.isoformat(), "run": record},
                indent=2,
                default=str,
            )
        )
        log(f"incident report written: {path}")
        return path
    except (OSError, TypeError, ValueError) as e:
        log(f"warn: could not write incident report: {e}")
        return None


def _write_run_incident(record: dict[str, Any]) -> None:
    """Post-run hook: on any non-clean exit, leave a report behind.

    The trailing re-emit of the error is load-bearing, not noise. The scheduled
    Claude routine that drives the daily run reports failures as
    `FAILED <stderr last line>` — so anything this hook logs after die()'s
    `error: …` would silently HIJACK that report (it would surface the incident
    file path instead of the diagnostic). Re-emitting the error last keeps the
    last stderr line the actual reason, whatever the hook printed."""
    message = record.get("error_message") or "run failed without a diagnostic message"
    write_incident(record, kind=classify_incident(message), message=message)
    log(f"error: {message}")


# --- blocked-source registry -----------------------------------------------


def load_blocked_sources() -> dict[str, Any]:
    """Outlets that cannot be fetched for article bodies. Shipped with the skill so
    curation consults data instead of operator recall. A missing/corrupt registry
    degrades to "nothing is known to be blocked" — never a hard failure."""
    if not BLOCKED_SOURCES_PATH.exists():
        return {}
    try:
        data = json.loads(BLOCKED_SOURCES_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log(f"warn: {BLOCKED_SOURCES_PATH} unreadable ({e}); treating as empty")
        return {}
    domains = data.get("domains") if isinstance(data, dict) else None
    return domains if isinstance(domains, dict) else {}


def is_blocked_domain(url: str, blocked: dict[str, Any]) -> str | None:
    """The registry key matching `url`'s host, or None.

    Host-based, never substring: `notwired.com` must not match `wired.com`, and a
    path containing a blocked domain is not itself blocked."""
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return None
    if not host:
        return None
    for domain in blocked:
        d = str(domain).lower()
        if host == d or host.endswith(f".{d}"):
            return domain
    return None


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
        "--skip-preflight",
        action="store_true",
        help="skip the pre-flight gate (deps, auth, capacity, R2). Escape hatch for "
        "when a check is wrong and you need to ship anyway; you own the outcome.",
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
            _sweep_bloopers_on_failure(record)
            write_run_log(record)
            _write_run_incident(record)
        raise
    except BaseException as e:  # noqa: BLE001 — post-run hook must cover every non-clean exit
        # An unexpected crash (or a KeyboardInterrupt / SIGTERM-driven unwind) is
        # exactly the case that used to leave nothing behind but a scrollback buffer.
        # Record it, then re-raise unchanged — this hook never swallows anything.
        record["status"] = "failed"
        if not record.get("error_message"):
            record["error_message"] = f"{type(e).__name__}: {e}"
        _sweep_bloopers_on_failure(record)
        write_run_log(record)
        _write_run_incident(record)
        raise
    finally:
        _RUN_CTX = None


def _sweep_bloopers_on_failure(record: dict[str, Any]) -> None:
    """Bank a dead run's segments before its workdir is gone (#169), and record the
    count on the run. Best-effort in the same way as write_run_log beside it: the run
    has already failed and nothing here may change how it failed."""
    try:
        banked = capture_workdir_segments(
            record.get("workdir"),
            error_message=record.get("error_message"),
            run_date=record.get("run_date"),
            title=record.get("title"),
        )
        record["bloopers_captured"] = (record.get("bloopers_captured") or 0) + len(banked)
    except Exception as e:  # noqa: BLE001 — a failed sweep must not mask the real failure
        log(f"warn: blooper sweep failed: {e}")


def _cleanup_auto_workdir(workdir: Path, eligible: bool) -> None:
    """Delete the run's workdir on a clean finish (#21). Only AUTO-created workdirs
    are eligible — deleting an explicit --workdir would break the documented
    same-workdir resume/no-op path, so an explicit one is always kept. A failed run
    never reaches this call, so failures keep their workdir for debugging
    automatically. Best-effort: a cleanup error is logged, not fatal (the episode is
    already shipped + deduped). The resume path never calls this, preserving its
    idempotent re-run."""
    if not eligible:
        return
    try:
        shutil.rmtree(workdir)
        log(f"deleted workdir {workdir} (pass --keep-workdir to retain)")
    except OSError as e:
        log(f"warn: could not delete workdir {workdir}: {e}")


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
    # A `lines` scene carries no author-written text, and a segment measuring zero
    # chars is invisible to speech_rate_rows — which would silently disarm the
    # TTS-degeneration gate and the bloopers bin for the whole show (#172). Derive it
    # here, once, before anything downstream measures a segment.
    materialize_line_text(manifest)
    title = manifest["title"]
    summary = manifest["summary"]
    segments = manifest["segments"]
    web_only = is_web_only(manifest)
    record["title"] = title
    record["segment_count"] = len(segments)

    auto_workdir = args.workdir is None
    # The auto workdir is deterministic per-date rather than a random mkdtemp(): a
    # dropped connection used to be unrecoverable simply because nobody could name
    # the directory the work landed in. Now a bare re-invocation resumes it.
    workdir = args.workdir or default_workdir()
    workdir.mkdir(parents=True, exist_ok=True)
    record["workdir"] = str(workdir)
    marker = workdir / "uploaded.json"

    # Resume: a prior run already uploaded into this workdir, so skip render + upload
    # and re-run only the idempotent tail. Never for --dry-run (which never uploads).
    # Never for a web-only manifest either (#155): that tail is set_timeline +
    # poll_ready, and a stale marker from an earlier Spotify-mode run in the same
    # workdir must not drag an RSS-first show back onto save-to-spotify. A web-only
    # re-run is already idempotent on its own — the R2 PUTs replace and the manifest
    # entry upserts by slug — so it simply renders again (off the TTS cache).
    if marker.exists() and not args.dry_run and not web_only:
        log(f"workdir: {workdir}")
        # Config is resolved HERE (not inside _resume) so the R2 back-fill can see
        # r2_bucket / r2_public_base_url and stop silently skipping the web feed on
        # every recovery (2026-07-28). Deliberately tolerant: a recovery must still
        # work on a box with no config.json, so a missing file degrades to {} —
        # env-only, the old behaviour — instead of dying mid-recovery.
        resume_config = load_config() if CONFIG_PATH.exists() else {}
        rc = _resume(workdir, marker, segments, title, manifest, record, config=resume_config)
        if rc == 0:
            write_run_log(record)
        return rc

    # Cross-day cron recovery (#37): before rendering a NEW episode, reconcile any
    # leftover in-flight episode (uploaded last run but never deduped). This marks
    # its URLs covered so curation here can't re-select them — closing the duplicate
    # gap the per-workdir uploaded.json marker can't reach. Skipped for --dry-run,
    # which by contract never uploads, calls Spotify, or mutates covered.json.
    # Skipped for web-only too: reconciliation reads an episode's readiness off
    # save-to-spotify, and this mode never uploads one to leave in flight (#155).
    if not args.dry_run and not web_only:
        _recover_inflight()

    config = load_config()
    show_id = manifest.get("show_id") or config.get("show_id")
    if not show_id and not web_only:
        die("show_id required (in manifest or ~/.config/daily-podcast/config.json)")
    if web_only:
        # An RSS-first show has no Spotify show to upload to, so a show_id here is
        # meaningless — drop it rather than let it reach a gate or an API call.
        show_id = None
    show_name = resolve_show_name(manifest, config)
    cover_image = resolve_cover_image(manifest, Path(args.manifest))

    # PRE-FLIGHT: verify everything the run depends on before spending a render on
    # it. Capacity is the headline — the cap-429 auto-prune only ever fired *after*
    # a full TTS render had already been paid for.
    if args.skip_preflight:
        log("preflight: skipped (--skip-preflight)")
    else:
        ok, checks = preflight(
            config,
            show_id=show_id,
            dry_run=args.dry_run,
            record=record,
            web_only=web_only,
            cover_image=cover_image,
        )
        if not ok:
            failed = ", ".join(c["name"] for c in checks if not c["ok"])
            die(f"preflight failed ({failed}); nothing was rendered or uploaded")
        mark_stage(workdir, "preflight", checks=len(checks))

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
        cast=manifest.get("cast"),
    )
    mark_stage(workdir, "segments", count=len(seg_paths))
    silences_ms = plan_silences(seg_paths)
    episode_mp3, loudnorm = concat_and_normalize(seg_paths, silences_ms, workdir)
    record["loudnorm"] = loudnorm
    mark_stage(workdir, "concat", loudnorm=loudnorm)

    # 4: cover. Supplied art wins over the generated template — a show with its own
    # designed cover should not wear the daily show's gradient in a podcast client
    # (#164). build_cover remains the default for every show without one.
    cover = workdir / "cover.jpg"
    if cover_image:
        apply_cover_image(cover_image, cover)
        log(f"cover: {cover_image} (supplied)")
    else:
        build_cover(cover, show_name, cover_date, title, style=resolve_cover_style(manifest))
    mark_stage(workdir, "cover")

    # 5: timeline + description
    timeline, description = build_timeline_and_description(
        segments,
        seg_paths,
        silences_ms,
        summary,
        episode_mp3,
        footer_html=resolve_description_footer(manifest),
    )
    timeline_path = workdir / "timeline.json"
    timeline_path.write_text(json.dumps(timeline, indent=2))
    (workdir / "description.html").write_text(description)
    mark_stage(workdir, "timeline", chapters=sum(1 for it in timeline["items"] if "chapter" in it))

    # 5b: artifact gate. Pre-flight runs before the mp3 exists, so the checks that
    # need the finished bytes land here. Deliberately BEFORE the --dry-run return:
    # every check is local (ffprobe + a hash), so a dry run should exercise the same
    # gate the real run does — that is the whole point of rehearsing with --dry-run.
    episode_duration_ms = mp3_duration_ms(episode_mp3)
    # Bank the odd-sounding segments BEFORE the gate decides this run's fate (#169).
    # A speech-rate rejection's documented recovery deletes the offending seg_NN.mp3,
    # and a stale workdir empties itself within days, so anything measured after the
    # die() below is already unrecoverable. Best-effort: never changes the exit code.
    record["bloopers_captured"] = len(
        capture_rate_bloopers(
            segments,
            seg_paths,
            dry_run=args.dry_run,
            run_date=manifest.get("date"),
            title=manifest.get("title"),
            workdir=str(workdir),
        )
    )
    artifact_errors = verify_artifact(
        episode_mp3,
        timeline,
        duration_ms=episode_duration_ms,
        profile=probe_audio_profile(episode_mp3),
        segments=segments,
        seg_paths=seg_paths,
    )
    if artifact_errors:
        die("artifact gate failed: " + "; ".join(artifact_errors))
    log("artifact gate: PASS")
    mark_stage(workdir, "artifact_gate")

    log(f"\nartifacts in {workdir}:")
    for f in sorted(workdir.iterdir()):
        log(f"  {f.name}: {f.stat().st_size} bytes")

    if args.dry_run:
        # Preview where R2 publish *would* have gone, without uploading anything.
        r2_cfg = load_r2_config(config)
        if r2_cfg:
            r2_would_publish = r2_episode_mp3_url(r2_cfg, manifest)
            log(
                f"[r2] dry-run: would publish {r2_would_publish} + manifest entry "
                f"to bucket {r2_cfg['bucket']}"
            )
        else:
            r2_would_publish = None
            log("[r2] dry-run: not configured, would skip")
        chapter_count = sum(1 for it in timeline["items"] if "chapter" in it)
        duration_s = episode_duration_ms / 1000
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

    # 6w: web-only ship (#155). The R2 publish replaces upload/set_timeline/poll_ready
    # entirely for an RSS-first show, and covered.json moves behind it. Everything
    # above this line — render, cover, timeline, artifact gate — is shared verbatim.
    if web_only:
        rc = _ship_web_only(
            config,
            manifest=manifest,
            workdir=workdir,
            episode_mp3=episode_mp3,
            cover=cover,
            timeline=timeline,
            description=description,
            segments=segments,
            title=title,
            voice=voice,
            voice_mode=voice_mode,
            loudnorm=loudnorm,
            episode_duration_ms=episode_duration_ms,
            record=record,
        )
        write_run_log(record)
        _cleanup_auto_workdir(workdir, auto_workdir and not args.keep_workdir)
        return rc

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
    mark_stage(workdir, "upload", episode_uri=episode_uri)
    set_timeline(episode_id, timeline_path)
    mark_stage(workdir, "set_timeline")
    log("timeline set; polling for READY...")
    poll_ready(episode_id, show_id=show_id, config=config)
    mark_stage(workdir, "poll_ready", readiness="READY")

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

    mark_stage(workdir, "r2", status=r2_status)

    # 8: dedup log update (only after READY, regardless of R2 outcome)
    _save_dedup(segments, episode_uri)
    mark_stage(workdir, "dedup", urls=len(_segment_urls(segments)))
    # URLs are durably covered now, so the in-flight log has done its job — clear it
    # LAST, after dedup, so a crash anywhere above leaves it for the next run.
    _clear_inflight()

    chapter_count = sum(1 for it in timeline["items"] if "chapter" in it)
    duration_s = episode_duration_ms / 1000
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

    # 9: workdir hygiene (#21).
    _cleanup_auto_workdir(workdir, auto_workdir and not args.keep_workdir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
