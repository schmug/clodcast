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
  9. Updates ~/.config/daily-podcast/covered.json dedup log

Use --dry-run to skip upload/timeline calls (still writes mp3, cover, timeline.json).
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
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
TARGET_CHAPTER_MS = 30_500          # 30s + buffer; Spotify rejects <30s strict
MAX_SHORT_CHAPTERS = 3
DEFAULT_SILENCE_MS = 800
LAST_SILENCE_MS = 0                 # no silence after the final segment
CONFIG_DIR = Path.home() / ".config" / "daily-podcast"
CONFIG_PATH = CONFIG_DIR / "config.json"
COVERED_PATH = CONFIG_DIR / "covered.json"
VOICES_DIR = CONFIG_DIR / "voices"
USER_HOUSE_AUDIO = VOICES_DIR / "house.wav"
USER_HOUSE_TEXT = VOICES_DIR / "house.txt"

# --- helpers ---------------------------------------------------------------

def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def die(msg: str, code: int = 1) -> None:
    log(f"error: {msg}")
    sys.exit(code)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """Run a command, raising on failure with the command line in the message."""
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)
    except subprocess.CalledProcessError as e:
        die(f"command failed: {' '.join(cmd)}\nstderr: {e.stderr}")


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


def save_covered(data: dict[str, Any]) -> None:
    # Atomic write: a crash mid-write must not truncate the dedup log, or the next
    # run loses its dedup state and re-uploads every URL as a duplicate episode.
    # Write a temp file in the SAME dir (cross-filesystem rename isn't atomic), then
    # os.replace() — consistent on every platform, unlike os.rename which fails on
    # Windows when the target exists. Formatting is preserved (indent=2, sort_keys).
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True)
    fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".covered.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        os.replace(tmp, COVERED_PATH)
    except BaseException:
        # On any failure/interrupt, drop the temp file so it can't masquerade as state.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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
        die(f"unknown voice: {requested}. Expected 'house', 'random', "
            f"one of {VOICES}, or set voice_instruct directly.")
    return voice, voice_instruct, ref_audio, ref_text


# --- audio rendering -------------------------------------------------------

def render_segments(segments: list[dict], voice: str, workdir: Path,
                   voice_instruct: str | None = None,
                   ref_audio: str | None = None,
                   ref_text: str | None = None) -> list[Path]:
    """
    Render each segment text to an mp3 in workdir; return list of mp3 paths.

    Three voice modes:
    - `ref_audio` set (+ `ref_text`): voice cloning via Base model + generate(ref_audio=...)
    - `voice_instruct` set: VoiceDesign model + generate_voice_design(instruct=...)
    - Otherwise: Base model with `voice` as a preset name (Ryan/Aiden/Ethan/Chelsie)

    `ref_audio` takes precedence if both are set.
    """
    use_clone = bool(ref_audio)
    use_design = bool(voice_instruct) and not use_clone
    model_id = VOICE_DESIGN_MODEL_ID if use_design else MODEL_ID
    log(f"loading {model_id}...")
    t0 = time.time()
    from mlx_audio.tts.utils import load_model
    import soundfile as sf
    import numpy as np

    model = load_model(model_id)
    log(f"  model loaded in {time.time() - t0:.1f}s")

    paths: list[Path] = []
    for i, seg in enumerate(segments, start=1):
        text = seg["text"].strip()
        if not text:
            die(f"segment {i} has empty text")
        if use_clone:
            mode = "clone"
        elif use_design:
            mode = "design"
        else:
            mode = "preset"
        log(f"[{i}/{len(segments)}] rendering ({len(text)} chars, voice={voice}, mode={mode})...")
        t0 = time.time()
        if use_clone:
            results = list(model.generate(
                text=text, language="English",
                ref_audio=ref_audio, ref_text=ref_text,
            ))
        elif use_design:
            results = list(model.generate_voice_design(
                text=text, language="English", instruct=voice_instruct,
            ))
        else:
            results = list(model.generate(
                text=text, voice=voice, language="English",
            ))
        audio = np.concatenate([np.array(r.audio) for r in results])
        wav = workdir / f"seg_{i:02d}.wav"
        mp3 = workdir / f"seg_{i:02d}.mp3"
        sf.write(wav, audio, SAMPLE_RATE)
        # convert to mp3 at 44.1k mono so concat is clean
        run([
            "ffmpeg", "-y", "-i", str(wav),
            "-ar", "44100", "-ac", "1",
            "-c:a", "libmp3lame", "-b:a", "192k",
            str(mp3),
        ])
        dur_s = len(audio) / SAMPLE_RATE
        elapsed = time.time() - t0
        log(f"  -> {dur_s:.2f}s in {elapsed:.1f}s ({dur_s/elapsed:.1f}x rt)")
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
        candidates = [
            i for i in short_indices()
            if i < n - 1 and silence[i] < MAX_SILENCE_MS
        ]
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
    run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"anullsrc=r=44100:cl=mono",
        "-t", f"{secs:.3f}",
        "-q:a", "9", "-acodec", "libmp3lame",
        str(p),
    ])
    return p


def concat_and_normalize(seg_paths: list[Path], silences_ms: list[int],
                        workdir: Path) -> Path:
    """Build concat list, encode raw, loudnorm. Return final mp3 path."""
    parts: list[Path] = []
    for i, seg in enumerate(seg_paths):
        parts.append(seg)
        if silences_ms[i] > 0:
            parts.append(write_silence(workdir, silences_ms[i]))

    concat_list = workdir / "concat.txt"
    concat_list.write_text("\n".join(f"file '{p}'" for p in parts) + "\n")

    raw = workdir / "episode_raw.mp3"
    final = workdir / "episode.mp3"
    run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-ar", "44100", "-ac", "1",
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(raw),
    ])
    run([
        "ffmpeg", "-y", "-i", str(raw),
        "-af", "loudnorm",
        "-ar", "44100", "-ac", "1",
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(final),
    ])
    log(f"final episode: {mp3_duration_ms(final)/1000:.1f}s")
    return final


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
        d.line([(0, y), (W, y)], fill=(
            int(top[0] + (bot[0] - top[0]) * t),
            int(top[1] + (bot[1] - top[1]) * t),
            int(top[2] + (bot[2] - top[2]) * t),
        ))

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

def build_timeline_and_description(segments: list[dict], seg_paths: list[Path],
                                   silences_ms: list[int], summary: str,
                                   episode_mp3: Path) -> tuple[dict, str]:
    items: list[dict] = []
    chapters: list[tuple[int, str, str | None]] = []  # (ms, title, url)
    cursor = 0
    for i, seg in enumerate(segments):
        title = seg.get("title") or seg.get("source_title") or f"Segment {i+1}"
        url = seg.get("source_url")
        items.append({"chapter": {"title": title, "start_time_ms": cursor}})
        dur = mp3_duration_ms(seg_paths[i])
        if url:
            link_start = cursor + max(1000, int(dur * 0.40))
            link_dur = min(6000, max(2000, dur - 2000))
            items.append({"link": {
                "start_time_ms": link_start,
                "duration_ms": link_dur,
                "url": url,
            }})
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

    return {"items": items}, description


# --- upload + poll ---------------------------------------------------------

def upload(episode_mp3: Path, title: str, description: str, cover: Path,
          show_id: str) -> str:
    """Return episode_uri."""
    result = run([
        "save-to-spotify", "--json", "upload", str(episode_mp3),
        "--title", title,
        "--summary", description,
        "--show-id", show_id,
        "--image", str(cover),
    ])
    data = json.loads(result.stdout)
    if "error" in data:
        die(f"upload error: {data['error']}")
    return data["episode_uri"]


def set_timeline(episode_id: str, timeline_path: Path) -> None:
    result = run([
        "save-to-spotify", "--json", "timeline", "set",
        "--episode-id", episode_id,
        "--from-file", str(timeline_path),
    ])
    data = json.loads(result.stdout)
    if "error" in data:
        die(f"timeline set error: {data['error']}")


def poll_ready(episode_id: str, timeout_s: int = 600) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        result = run([
            "save-to-spotify", "--json", "episodes", "status", episode_id,
        ])
        data = json.loads(result.stdout)
        r = data.get("readiness", "")
        log(f"  status: {r}")
        if r == "READY":
            return
        if r == "FAILED":
            die("episode processing FAILED")
        time.sleep(15)
    die(f"episode not READY after {timeout_s}s")


# --- main ------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true",
                    help="render audio/cover/timeline locally; skip upload")
    ap.add_argument("--workdir", type=Path, default=None,
                    help="working directory (default: a tmpdir under /tmp)")
    args = ap.parse_args()

    if not args.manifest.exists():
        die(f"manifest not found: {args.manifest}")

    manifest = json.loads(args.manifest.read_text())
    title = manifest.get("title") or die("manifest.title is required")
    summary = manifest.get("summary") or die("manifest.summary is required")
    segments = manifest.get("segments") or die("manifest.segments is required")
    if not isinstance(segments, list) or not segments:
        die("manifest.segments must be a non-empty list")

    config = load_config()
    show_id = manifest.get("show_id") or config.get("show_id")
    if not show_id:
        die("show_id required (in manifest or ~/.config/daily-podcast/config.json)")
    show_name = config.get("show_name") or "Daily Digest"

    voice, voice_instruct, ref_audio, ref_text = resolve_voice(manifest)

    today = dt.date.today().strftime("%B %-d, %Y")

    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="daily-podcast-"))
    workdir.mkdir(parents=True, exist_ok=True)
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
    seg_paths = render_segments(segments, voice, workdir,
                                voice_instruct=voice_instruct,
                                ref_audio=ref_audio, ref_text=ref_text)
    silences_ms = plan_silences(seg_paths)
    episode_mp3 = concat_and_normalize(seg_paths, silences_ms, workdir)

    # 4: cover
    cover = workdir / "cover.jpg"
    build_cover(cover, show_name, today, title)

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
        print(json.dumps({
            "status": "dry-run",
            "workdir": str(workdir),
            "episode_mp3": str(episode_mp3),
            "cover": str(cover),
            "timeline": str(timeline_path),
            "voice": voice,
            "chapter_count": sum(1 for it in timeline["items"] if "chapter" in it),
            "duration_s": mp3_duration_ms(episode_mp3) / 1000,
        }, indent=2))
        return 0

    # 6: upload + timeline + poll
    episode_uri = upload(episode_mp3, title, description, cover, show_id)
    episode_id = episode_uri.removeprefix("spotify:episode:")
    log(f"uploaded: {episode_uri}")
    set_timeline(episode_id, timeline_path)
    log("timeline set; polling for READY...")
    poll_ready(episode_id)

    # 7: dedup log update
    covered = load_covered()
    today_iso = dt.date.today().isoformat()
    for seg in segments:
        url = seg.get("source_url")
        if url:
            covered[url] = {"date": today_iso, "episode_uri": episode_uri}
    save_covered(covered)

    print(json.dumps({
        "status": "ready",
        "episode_uri": episode_uri,
        "title": title,
        "voice": voice,
        "chapter_count": sum(1 for it in timeline["items"] if "chapter" in it),
        "duration_s": mp3_duration_ms(episode_mp3) / 1000,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
