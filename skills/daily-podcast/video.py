"""Episode video: render a PUBLISHED episode as a 1080p video and ship it to YouTube.

This is a post-ship step, deliberately outside `render.py`'s run. The audio episode
is the product and R2/RSS is its ship (#218); a video is a derived artifact of an
episode that already exists. So nothing here can fail, delay, or re-shape an audio
run: it reads what the run already published — the public feed manifest (chapters
at millisecond precision, title, summary and the mp3 URL) — and works from
that, on its own schedule (`launchd/com.cortech.clodcast-video.plist`).

    python3 video.py --pending            # every published episode still without a video
    python3 video.py --slug <slug>        # one episode (re-render / back-fill)
    python3 video.py --slug <slug> --no-upload --out ~/Movies/ep.mp4   # render only
    python3 video.py auth --client-secrets client_secret.json          # one-time OAuth

Load-bearing (CLAUDE.md, "Episode video"):

- **The ledger (`videos.jsonl`) is append-only and is the only idempotency source.**
  An `uploaded` row is written the moment YouTube returns a video id — BEFORE the
  best-effort caption / thumbnail / playlist calls — so a crash after the upload
  can never produce a second copy of the episode on the channel.
- **`--pending` is bounded twice** (`lookback_days`, `max_per_run`) so a fresh install
  or a wiped ledger can never walk the whole back catalogue onto YouTube in one run.
  Back-fill is explicit: `--slug`.
- **Stdlib only at import.** The frame renderer (numpy, OpenCV, Pillow) lives in
  `video_frames.py` and the transcriber is imported inside `transcribe`, so CI tests
  every contract here without either.
- **YouTube API text is sanitized, not trusted:** `<` and `>` are refused by the API
  in titles and descriptions, the title caps at 100 characters and the description
  at 5000 BYTES, and chapters only render when the first sits at 0:00, there are at
  least three, and each is at least ten seconds long.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import render

SCRIPT_DIR = Path(__file__).resolve().parent
FONT_DIR = SCRIPT_DIR / "assets" / "fonts"
FONT_FILES = (
    "space-grotesk-400.ttf",
    "space-grotesk-500.ttf",
    "space-grotesk-700.ttf",
    "jetbrains-mono-400.ttf",
    "jetbrains-mono-500.ttf",
    "jetbrains-mono-700.ttf",
)

# Kept out of render.WORKDIR_PREFIX ("daily-podcast-") on purpose: `--prune-workdirs`
# globs that prefix under the same temp dir, and a video workdir is not its to judge.
VIDEO_WORKDIR_PREFIX = "clodcast-video-"

LEDGER_NAME = "videos.jsonl"
LEDGER_FIELDS = (
    "timestamp",
    "slug",
    "status",  # uploaded | extras | rendered | failed
    "youtube_id",
    "youtube_url",
    "privacy_status",
    "duration_s",
    "video_bytes",
    "captions",  # word count transcribed, null when captions were off/unavailable
    "extras",  # on `extras` rows: {caption_track, thumbnail, playlist} -> ok/skipped/failed
    "output_path",
    "error_message",
)

PRIVACY_STATUSES = ("private", "unlisted", "public")
VIDEO_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "captions": True,
    "lookback_days": 3,
    "max_per_run": 1,
    "page_base_url": "https://cortech.online/podcast/",
    "feed_url": "https://cortech.online/podcast/rss.xml",
    "jobs": 0,  # 0 = one worker per CPU, capped at 8
    # The public show's name, as its RSS feed carries it. NOT config.json's
    # `show_name`: that is the legacy cover label, deliberately left unrenamed (#133).
    "show_name": "Cortech Daily",
    # The disc at the centre of the ring. Not the episode cover: that is a text card
    # sized for a podcast directory, and a circle crop cuts its corners off.
    "monogram": "CT",
    "youtube": {
        "enabled": False,
        "privacy_status": "private",
        "category_id": "28",  # Science & Technology
        "tags": ["Cortech Daily", "tech news", "cybersecurity", "AI"],
        "playlist_id": None,
        "caption_track": True,
        "thumbnail": True,
    },
}
_YT_KEYS = frozenset(VIDEO_DEFAULTS["youtube"])
_TOP_KEYS = frozenset(VIDEO_DEFAULTS)

YT_TITLE_MAX = 100
YT_DESCRIPTION_MAX_BYTES = 5000
YT_MIN_CHAPTER_S = 10
YT_MIN_CHAPTERS = 3
YT_TAGS_MAX_CHARS = 500

# Resumable-upload chunks must be a multiple of 256 KiB (except the last).
UPLOAD_CHUNK = 64 * 256 * 1024
UPLOAD_RETRIES = 5
YT_SECRET_KEYS = ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN")
YT_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    # captions.insert, thumbnails.set and playlistItems.insert need the broader scope
    "https://www.googleapis.com/auth/youtube.force-ssl",
)
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3"
API_URL = "https://www.googleapis.com/youtube/v3"

# Literal, like render._LEGACY_TITLE_MONTHS (reused below): strftime follows LC_TIME.
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def log(msg: str) -> None:
    render.log(f"[video] {msg}")


class VideoError(RuntimeError):
    """A failure that ends this episode's attempt (recorded as `failed`)."""


# --- config ------------------------------------------------------------------


def resolve_video_config(raw: Any) -> dict[str, Any] | None:
    """The fully-defaulted `video` block, or None when absent / disabled.

    Closed whitelists, the `music` / `ship_mode` posture: a typo in a key that
    decides whether an episode goes PUBLIC must die, never fall back to a default."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise VideoError("config `video` must be an object")
    unknown = set(raw) - _TOP_KEYS
    if unknown:
        raise VideoError(f"config `video` has unknown key(s): {', '.join(sorted(unknown))}")
    out = {**VIDEO_DEFAULTS, **{k: v for k, v in raw.items() if k != "youtube"}}
    yt_raw = raw.get("youtube") or {}
    if not isinstance(yt_raw, dict):
        raise VideoError("config `video.youtube` must be an object")
    unknown = set(yt_raw) - _YT_KEYS
    if unknown:
        raise VideoError(f"config `video.youtube` has unknown key(s): {', '.join(sorted(unknown))}")
    out["youtube"] = {**VIDEO_DEFAULTS["youtube"], **yt_raw}
    if out["youtube"]["privacy_status"] not in PRIVACY_STATUSES:
        raise VideoError(
            "config `video.youtube.privacy_status` must be one of "
            f"{', '.join(PRIVACY_STATUSES)} (got {out['youtube']['privacy_status']!r})"
        )
    for key in ("lookback_days", "max_per_run"):
        if not isinstance(out[key], int) or isinstance(out[key], bool) or out[key] <= 0:
            raise VideoError(f"config `video.{key}` must be a positive integer")
    if not isinstance(out["jobs"], int) or isinstance(out["jobs"], bool) or out["jobs"] < 0:
        raise VideoError("config `video.jobs` must be an integer >= 0")
    if not out["enabled"]:
        return None
    return out


def resolve_jobs(requested: int) -> int:
    return requested if requested > 0 else max(1, min(8, os.cpu_count() or 1))


# --- ledger ------------------------------------------------------------------


def ledger_path() -> Path:
    # Derived at call time so the test suite's CONFIG_DIR sandbox covers it.
    return render.CONFIG_DIR / LEDGER_NAME


def new_ledger_record(slug: str) -> dict[str, Any]:
    rec: dict[str, Any] = dict.fromkeys(LEDGER_FIELDS)
    rec["timestamp"] = dt.datetime.now(dt.timezone.utc).isoformat()
    rec["slug"] = slug
    return rec


def append_ledger(record: dict[str, Any]) -> None:
    """Append one full-key-set line. Append-only by contract, like runs.jsonl —
    never routed through an atomic replace, which would clobber the history that
    `--pending` reads to decide what is already on the channel."""
    row = {k: record.get(k) for k in LEDGER_FIELDS}
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(row) + "\n")


def load_ledger() -> list[dict[str, Any]]:
    path = ledger_path()
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn last line must not hide every row before it
        if isinstance(row, dict):
            rows.append(row)
    return rows


def uploaded_slugs(rows: list[dict[str, Any]]) -> set[str]:
    return {r["slug"] for r in rows if r.get("status") == "uploaded" and r.get("slug")}


# --- feed --------------------------------------------------------------------


def _http_get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "clodcast-video"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (configured URL)
        return resp.read()


def feed_manifest_url(config: dict[str, Any]) -> str:
    base = os.environ.get("R2_PUBLIC_BASE_URL") or config.get("r2_public_base_url")
    if not base:
        raise VideoError("r2_public_base_url is not configured; nothing to read episodes from")
    # Cache-busted: the bucket sits behind Cloudflare's edge, and a POP still serving
    # yesterday's manifest would make --pending report "none pending" all day.
    return base.rstrip("/") + f"/manifest.json?v={int(time.time())}"


def fetch_feed_manifest(config: dict[str, Any], fetch=_http_get) -> list[dict[str, Any]]:
    data = json.loads(fetch(feed_manifest_url(config)))
    if not isinstance(data, list):
        raise VideoError("feed manifest is not a list")
    return [e for e in data if isinstance(e, dict) and e.get("slug")]


def entry_date(entry: dict[str, Any]) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(entry.get("pubDate", ""))[:10])
    except ValueError:
        return None


def select_pending(
    entries: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    *,
    today: dt.date,
    lookback_days: int,
    max_per_run: int,
) -> list[dict[str, Any]]:
    """Newest-first published episodes with no `uploaded` ledger row, inside the
    lookback window, at most `max_per_run`. A `failed` or render-only row does NOT
    block a retry; an entry with an unparseable pubDate is skipped, never guessed."""
    done = uploaded_slugs(rows)
    oldest = today - dt.timedelta(days=lookback_days)
    cands = []
    for e in entries:
        d = entry_date(e)
        if d is None or d < oldest or d > today or e["slug"] in done:
            continue
        cands.append((d, e))
    cands.sort(key=lambda p: p[0], reverse=True)
    return [e for _, e in cands[:max_per_run]]


def parse_chapters(entry: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for ch in entry.get("chapters") or []:
        if not isinstance(ch, dict):
            continue
        out.append(
            {
                "t": max(0.0, float(ch.get("start_ms") or 0) / 1000.0),
                "title": str(ch.get("title") or "").strip(),
                "source_url": ch.get("source_url") or None,
            }
        )
    out.sort(key=lambda c: c["t"])
    if not out or out[0]["t"] > 0:
        out.insert(0, {"t": 0.0, "title": "Intro", "source_url": None})
    return out


def source_label(url: str | None) -> str:
    if not url:
        return ""
    host = urllib.parse.urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def split_title(title: str) -> tuple[str, str]:
    """("<lead stories>", "<date>") from "<lead stories> - <Month D, YYYY>" (#139)."""
    m = re.match(r"^(.*?)\s+-\s+([A-Z][a-z]+ \d{1,2}, \d{4})$", title.strip())
    if m:
        return m.group(1), m.group(2)
    return title.strip(), ""


def long_date(d: dt.date) -> str:
    month = render._LEGACY_TITLE_MONTHS[d.month - 1]
    return f"{_WEEKDAYS[d.weekday()]} · {month} {d.day}, {d.year}"


# --- YouTube text ------------------------------------------------------------


def sanitize_youtube_text(s: str) -> str:
    """The API refuses < and > anywhere in a title or description."""
    return s.replace("<", "‹").replace(">", "›")


def fmt_timestamp(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def youtube_chapters(chapters: list[dict[str, Any]], duration_s: float) -> list[tuple[int, str]]:
    """Description chapter lines YouTube will actually render: first at 0:00, each at
    least ten seconds long (a too-short one is dropped, its time folded into the one
    before), and at least three — else none, since a partial list renders nothing."""
    kept: list[tuple[int, str]] = []
    for ch in chapters:
        start = 0 if not kept else int(ch["t"])
        if kept and start - kept[-1][0] < YT_MIN_CHAPTER_S:
            continue
        if duration_s - start < YT_MIN_CHAPTER_S and kept:
            continue
        kept.append((start, sanitize_youtube_text(ch["title"]) or "Chapter"))
    return kept if len(kept) >= YT_MIN_CHAPTERS else []


def build_youtube_title(entry: dict[str, Any]) -> str:
    title = sanitize_youtube_text(" ".join(str(entry.get("title") or entry["slug"]).split()))
    if len(title) <= YT_TITLE_MAX:
        return title
    lead, date = split_title(title)
    suffix = f" - {date}" if date else ""
    room = YT_TITLE_MAX - len(suffix) - 1
    cut = lead[:room].rsplit(" ", 1)[0].rstrip(",;:- ")
    return f"{cut}…{suffix}"[:YT_TITLE_MAX]


def _utf8_len(s: str) -> int:
    return len(s.encode("utf-8"))


def build_youtube_description(
    entry: dict[str, Any], chapters: list[dict[str, Any]], vcfg: dict[str, Any]
) -> str:
    """Summary, chapter list, sources, where to subscribe, and the AI disclosure.
    Trimmed from the SOURCES end, never mid-line, to YouTube's 5000-byte cap: the
    chapter list is what builds YouTube's chapter UI, so it is the last thing to go."""
    duration = float(entry.get("duration_s") or 0)
    page = vcfg["page_base_url"].rstrip("/") + f"/{entry['slug']}/"
    head = [str(entry.get("summary") or "").strip(), ""]
    chap = youtube_chapters(chapters, duration)
    if chap:
        head += [f"{fmt_timestamp(t)} {title}" for t, title in chap] + [""]
    tail = [
        f"Episode page: {page}",
        f"Subscribe (RSS): {vcfg['feed_url']}",
        "",
        f"{vcfg['show_name']} is curated, written and voiced by an automated pipeline; "
        "the voice is synthetic.",
    ]
    sources = [f"- {c['title']}: {c['source_url']}" for c in chapters if c.get("source_url")]
    while True:
        body = head + (["Sources:", *sources, ""] if sources else []) + tail
        text = sanitize_youtube_text("\n".join(body).strip())
        if _utf8_len(text) <= YT_DESCRIPTION_MAX_BYTES or not sources:
            break
        sources.pop()
    while _utf8_len(text) > YT_DESCRIPTION_MAX_BYTES:
        text = text[: len(text) - 64].rsplit("\n", 1)[0]
    return text


def build_tags(tags: list[str]) -> list[str]:
    out, total = [], 0
    for t in tags:
        t = sanitize_youtube_text(str(t)).strip()
        cost = len(t) + 2 + (2 if " " in t else 0)  # commas + quotes count toward the cap
        if not t or total + cost > YT_TAGS_MAX_CHARS:
            continue
        out.append(t)
        total += cost
    return out


# --- captions ----------------------------------------------------------------


def snap_chapter_starts(chapters: list[dict[str, Any]], words: list[dict[str, Any]]) -> None:
    """Move each chapter's transition into the silence just before its first word.

    The feed's start_ms is the segment boundary, which sits somewhere inside the
    inter-segment silence. The visual cut (glitch + card) reads best when it lands
    just before speech resumes, and must never land on top of the previous segment's
    last word. Mutates `chapters` in place; the first chapter never moves."""
    for c in chapters[1:]:
        T = c["t"]
        nxt = [w for w in words if w["s"] >= T - 0.25]
        prev = [w for w in words if w["e"] <= T + 0.25]
        if not nxt:
            continue
        first = nxt[0]
        if first["s"] - T > 2.5:
            continue  # no speech near the boundary: trust the feed
        floor = prev[-1]["e"] + 0.05 if prev and prev[-1] is not first else T - 0.25
        c["t"] = max(floor, first["s"] - 0.4)


def group_phrases(
    words: list[dict[str, Any]],
    bounds: list[float],
    *,
    max_chars: int = 58,
    gap_s: float = 0.55,
) -> list[tuple[float, float, list[dict[str, Any]]]]:
    """Caption lines: break on a pause, a sentence end, a chapter boundary, or width.
    Returns (show_from, show_until, words); consecutive lines never overlap."""
    groups: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    for w in words:
        if cur:
            prev = cur[-1]
            width = len(join_words([*cur, w]))
            crosses = any(prev["e"] <= b <= w["s"] + 0.05 for b in bounds)
            sentence = prev["w"][-1:] in ".?!" and len(cur) >= 4
            if w["s"] - prev["e"] > gap_s or width > max_chars or crosses or sentence:
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    out = []
    for i, g in enumerate(groups):
        start = max(0.0, g[0]["s"] - 0.12)
        end = g[-1]["e"] + 0.6
        if i + 1 < len(groups):
            end = min(end, groups[i + 1][0]["s"] - 0.12)
        out.append((start, max(end, start + 0.2), g))
    return out


def _srt_ts(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(phrases: list[tuple[float, float, list[dict[str, Any]]]]) -> str:
    blocks = []
    for i, (s, e, ws) in enumerate(phrases, 1):
        blocks.append(f"{i}\n{_srt_ts(s)} --> {_srt_ts(e)}\n{join_words(ws)}\n")
    return "\n".join(blocks)


def transcribe(audio: Path) -> list[dict[str, Any]] | None:
    """Word-timed transcript, or None when no transcriber is installed (the video
    then renders without captions — a missing caption is not worth a missing video).
    mlx-whisper is the Mac path (the `bench` extra; render.py's derailment detector
    already uses it); openai-whisper is the portable fallback."""
    try:
        import mlx_whisper

        r = mlx_whisper.transcribe(
            str(audio),
            path_or_hf_repo="mlx-community/whisper-small.en-mlx",
            word_timestamps=True,
            language="en",
            condition_on_previous_text=False,
        )
    except ImportError:
        try:
            import whisper
        except ImportError:
            log("no transcriber installed (mlx-whisper / openai-whisper); captions off")
            return None
        model = whisper.load_model("small.en")
        r = model.transcribe(
            str(audio), word_timestamps=True, language="en", condition_on_previous_text=False
        )
    words = []
    for seg in r.get("segments", []):
        for w in seg.get("words", []) or []:
            raw = str(w.get("word", ""))
            if raw.strip():
                words.append(
                    {
                        "w": raw.strip(),
                        # whisper splits "$2.56" into "$2" + ".56": a token with no
                        # leading space joins its neighbour, it is not a new word
                        "sp": raw[:1].isspace() or not words,
                        "s": round(float(w["start"]), 3),
                        "e": round(float(w["end"]), 3),
                    }
                )
    return words


def join_words(ws: list[dict[str, Any]]) -> str:
    out = ""
    for i, w in enumerate(ws):
        out += (" " if i and w.get("sp", True) else "") + w["w"]
    return out


# --- YouTube client ----------------------------------------------------------


def load_youtube_secrets() -> dict[str, str]:
    """env first, then the 0600 secrets.json — the R2 credentials' resolution order
    (render._load_r2_secrets), so a launchd job that never inherits the shell env
    still finds them."""
    out = {k: os.environ[k] for k in YT_SECRET_KEYS if os.environ.get(k)}
    path = render.CONFIG_DIR / "secrets.json"
    if len(out) < len(YT_SECRET_KEYS) and path.exists():
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            log(f"{path} unreadable ({e}); ignoring")
            data = {}
        for k in YT_SECRET_KEYS:
            if k not in out and isinstance(data.get(k), str) and data[k]:
                out[k] = data[k]
    return out


class YouTubeClient:
    """The five YouTube Data API v3 calls this step makes, over urllib.

    `opener` is the seam: tests pass a fake that records requests and replays
    canned responses. It is called as opener(request, timeout) and must return an
    object with .status, .headers (a Mapping) and .read(), or raise
    urllib.error.HTTPError."""

    def __init__(self, secrets: dict[str, str], opener=None):
        missing = [k for k in YT_SECRET_KEYS if not secrets.get(k)]
        if missing:
            raise VideoError(
                f"YouTube credentials missing: {', '.join(missing)} "
                "(run `video.py auth --client-secrets <file>`)"
            )
        self.secrets = secrets
        self._open = opener or (lambda req, timeout: urllib.request.urlopen(req, timeout=timeout))
        self._token: str | None = None
        self._token_exp = 0.0

    # -- plumbing
    def _call(self, req: urllib.request.Request, timeout: int = 120):
        return self._open(req, timeout)

    def token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        body = urllib.parse.urlencode(
            {
                "client_id": self.secrets["YOUTUBE_CLIENT_ID"],
                "client_secret": self.secrets["YOUTUBE_CLIENT_SECRET"],
                "refresh_token": self.secrets["YOUTUBE_REFRESH_TOKEN"],
                "grant_type": "refresh_token",
            }
        ).encode()
        req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            resp = self._call(req, 30)
        except urllib.error.HTTPError as e:
            raise VideoError(f"YouTube token refresh failed: HTTP {e.code} {_err_body(e)}") from e
        data = json.loads(resp.read())
        self._token = data["access_token"]
        self._token_exp = time.time() + float(data.get("expires_in", 3600))
        return self._token

    def _json(self, method: str, url: str, payload: Any | None = None, timeout: int = 60):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token()}")
        if data is not None:
            req.add_header("Content-Type", "application/json; charset=UTF-8")
        resp = self._call(req, timeout)
        raw = resp.read()
        return json.loads(raw) if raw else {}

    # -- videos.insert (resumable)
    def upload_video(self, path: Path, metadata: dict[str, Any]) -> str:
        size = path.stat().st_size
        body = json.dumps(metadata).encode()
        req = urllib.request.Request(
            f"{UPLOAD_URL}/videos?uploadType=resumable&part=snippet,status",
            data=body,
            method="POST",
        )
        req.add_header("Authorization", f"Bearer {self.token()}")
        req.add_header("Content-Type", "application/json; charset=UTF-8")
        req.add_header("X-Upload-Content-Length", str(size))
        req.add_header("X-Upload-Content-Type", "video/mp4")
        try:
            resp = self._call(req, 60)
        except urllib.error.HTTPError as e:
            raise VideoError(f"upload init refused: HTTP {e.code} {_err_body(e)}") from e
        session = resp.headers.get("Location")
        if not session:
            raise VideoError("upload init returned no session Location")
        offset, failures = 0, 0
        with path.open("rb") as f:
            while True:
                f.seek(offset)
                chunk = f.read(UPLOAD_CHUNK)
                end = offset + len(chunk) - 1
                put = urllib.request.Request(session, data=chunk, method="PUT")
                put.add_header("Authorization", f"Bearer {self.token()}")
                put.add_header("Content-Length", str(len(chunk)))
                put.add_header("Content-Range", f"bytes {offset}-{end}/{size}")
                try:
                    resp = self._call(put, 600)
                except urllib.error.HTTPError as e:
                    if e.code == 308:
                        offset = _next_offset(e.headers.get("Range"))
                        failures = 0
                        continue
                    if e.code in (500, 502, 503, 504) and failures < UPLOAD_RETRIES:
                        failures += 1
                        time.sleep(min(60, 2**failures))
                        offset = self._resume_offset(session, size)
                        continue
                    raise VideoError(f"upload failed: HTTP {e.code} {_err_body(e)}") from e
                except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                    if failures >= UPLOAD_RETRIES:
                        raise VideoError(f"upload failed: {e}") from e
                    failures += 1
                    time.sleep(min(60, 2**failures))
                    offset = self._resume_offset(session, size)
                    continue
                if resp.status == 308:
                    offset = _next_offset(resp.headers.get("Range"))
                    failures = 0
                    continue
                data = json.loads(resp.read())
                vid = data.get("id")
                if not vid:
                    raise VideoError(f"upload finished without a video id: {data}")
                return vid

    def _resume_offset(self, session: str, size: int) -> int:
        req = urllib.request.Request(session, data=b"", method="PUT")
        req.add_header("Authorization", f"Bearer {self.token()}")
        req.add_header("Content-Length", "0")
        req.add_header("Content-Range", f"bytes */{size}")
        try:
            resp = self._call(req, 60)
        except urllib.error.HTTPError as e:
            if e.code == 308:
                return _next_offset(e.headers.get("Range"))
            if e.code == 404:
                raise VideoError("upload session expired") from e
            raise VideoError(f"upload status check failed: HTTP {e.code}") from e
        if resp.status == 308:
            return _next_offset(resp.headers.get("Range"))
        return size

    # -- best-effort extras
    def insert_captions(self, video_id: str, srt: str) -> None:
        boundary = "clodcast-caption-boundary"
        meta = json.dumps(
            {
                "snippet": {
                    "videoId": video_id,
                    "language": "en",
                    "name": "English",
                    "isDraft": False,
                }
            }
        )
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
            f"--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n{srt}\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        req = urllib.request.Request(
            f"{UPLOAD_URL}/captions?uploadType=multipart&part=snippet", data=body, method="POST"
        )
        req.add_header("Authorization", f"Bearer {self.token()}")
        req.add_header("Content-Type", f"multipart/related; boundary={boundary}")
        self._call(req, 120).read()

    def set_thumbnail(self, video_id: str, jpeg: Path) -> None:
        req = urllib.request.Request(
            f"{UPLOAD_URL}/thumbnails/set?videoId={urllib.parse.quote(video_id)}&uploadType=media",
            data=jpeg.read_bytes(),
            method="POST",
        )
        req.add_header("Authorization", f"Bearer {self.token()}")
        req.add_header("Content-Type", "image/jpeg")
        self._call(req, 120).read()

    def add_to_playlist(self, video_id: str, playlist_id: str) -> None:
        self._json(
            "POST",
            f"{API_URL}/playlistItems?part=snippet",
            {
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        )


def _next_offset(range_header: str | None) -> int:
    """Byte after the last one YouTube has, from a 308's `Range: bytes=0-N`."""
    if not range_header:
        return 0
    m = re.search(r"-(\d+)\s*$", range_header)
    return int(m.group(1)) + 1 if m else 0


def _err_body(e: urllib.error.HTTPError) -> str:
    try:
        return e.read().decode("utf-8", "replace")[:300]
    except Exception:
        return ""


def build_video_metadata(
    entry: dict[str, Any], chapters: list[dict[str, Any]], vcfg: dict[str, Any]
) -> dict[str, Any]:
    yt = vcfg["youtube"]
    return {
        "snippet": {
            "title": build_youtube_title(entry),
            "description": build_youtube_description(entry, chapters, vcfg),
            "tags": build_tags(list(yt["tags"])),
            "categoryId": str(yt["category_id"]),
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": {
            "privacyStatus": yt["privacy_status"],
            "selfDeclaredMadeForKids": False,
            # The house voice is a realistic synthetic clone (docs/durable-voices.md);
            # YouTube's altered-or-synthetic-content policy asks for exactly this flag.
            "containsSyntheticMedia": True,
            "embeddable": True,
            "license": "youtube",
        },
    }


# --- one episode -------------------------------------------------------------


def preflight(vcfg: dict[str, Any], *, upload: bool) -> list[str]:
    """Problems that would waste a multi-minute render. Empty list = go."""
    problems = []
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            problems.append(f"{tool} not on PATH")
    for f in FONT_FILES:
        if not (FONT_DIR / f).exists():
            problems.append(f"font missing: {FONT_DIR / f}")
    for mod, pkg in (("numpy", "numpy"), ("cv2", "opencv-python-headless"), ("PIL", "Pillow")):
        if importlib.util.find_spec(mod) is None:
            problems.append(f"{mod} not importable (pip install {pkg}; the `video` extra)")
    if upload and vcfg["youtube"]["enabled"]:
        missing = [k for k in YT_SECRET_KEYS if k not in load_youtube_secrets()]
        if missing:
            problems.append(
                f"YouTube credentials missing: {', '.join(missing)} "
                "(run `video.py auth --client-secrets <file>`)"
            )
    return problems


def _display_url(url: str) -> str:
    p = urllib.parse.urlparse(url)
    return (p.netloc + p.path).rstrip("/")


def build_plan(
    entry: dict[str, Any],
    chapters: list[dict[str, Any]],
    words: list[dict[str, Any]] | None,
    *,
    audio: Path,
    vcfg: dict[str, Any],
) -> dict[str, Any]:
    """Everything video_frames needs, as plain JSON (workers rebuild from it)."""
    lead, date_text = split_title(str(entry.get("title") or ""))
    d = entry_date(entry)
    words = words or []
    bounds = [c["t"] for c in chapters[1:]]
    phrases = [
        {"s": s, "e": e, "words": ws} for s, e, ws in group_phrases(words, bounds, max_chars=58)
    ]
    return {
        "audio": str(audio),
        "monogram": str(vcfg["monogram"]),
        "show_name": str(vcfg["show_name"]),
        "date_long": long_date(d) if d else date_text,
        "teaser": html.unescape(lead),
        "outro_url": _display_url(vcfg["page_base_url"]),
        "chapters": [
            {
                "t": c["t"],
                "title": html.unescape(c["title"]),
                "source": source_label(c["source_url"]),
            }
            for c in chapters
        ],
        "phrases": phrases,
    }


def process_episode(
    entry: dict[str, Any],
    vcfg: dict[str, Any],
    *,
    upload: bool,
    out_path: Path | None = None,
    keep: bool = False,
    fetch=_http_get,
    client: YouTubeClient | None = None,
    renderer=None,
    transcriber=transcribe,
) -> dict[str, Any]:
    """Render one published episode and (optionally) upload it. Always returns a
    ledger record and always appends it; never raises for an episode-level failure."""
    slug = entry["slug"]
    rec = new_ledger_record(slug)
    rec["privacy_status"] = vcfg["youtube"]["privacy_status"] if upload else None
    workdir = Path(tempfile.mkdtemp(prefix=f"{VIDEO_WORKDIR_PREFIX}{slug}-", dir=render.TMP_BASE))
    ok = False
    try:
        mp3 = workdir / "episode.mp3"
        mp3.write_bytes(fetch(entry["mp3_url"]))
        chapters = parse_chapters(entry)
        words = transcriber(mp3) if vcfg["captions"] else None
        rec["captions"] = len(words) if words is not None else None
        if words:
            snap_chapter_starts(chapters, words)
        plan = build_plan(entry, chapters, words, audio=mp3, vcfg=vcfg)
        video = out_path or (workdir / f"{slug}.mp4")
        thumb = workdir / "thumbnail.jpg"
        if renderer is None:
            import video_frames  # numpy/OpenCV only when actually rendering

            renderer = video_frames.render_video
        info = renderer(plan, video, workdir=workdir, jobs=resolve_jobs(vcfg["jobs"]), thumb=thumb)
        rec["duration_s"] = info.get("duration_s")
        rec["video_bytes"] = video.stat().st_size
        rec["output_path"] = str(video) if (out_path or keep or not upload) else None
        if not upload:
            rec["status"] = "rendered"
            append_ledger(rec)
            ok = True
            return rec
        client = client or YouTubeClient(load_youtube_secrets())
        vid = client.upload_video(video, build_video_metadata(entry, parse_chapters(entry), vcfg))
        rec["status"] = "uploaded"
        rec["youtube_id"] = vid
        rec["youtube_url"] = f"https://www.youtube.com/watch?v={vid}"
        # Ledger FIRST: from here on the episode is on the channel, and nothing that
        # follows may be able to cause a second upload of it.
        append_ledger(rec)
        ok = True
        extras = _extras(client, vid, vcfg, words, chapters, thumb)
        rec["extras"] = extras
        # A second, informational row: `uploaded_slugs` counts only `uploaded`.
        ext = new_ledger_record(slug)
        ext.update(status="extras", youtube_id=vid, extras=extras)
        append_ledger(ext)
        return rec
    except Exception as e:
        if rec["status"] is None:
            rec["status"] = "failed"
            rec["error_message"] = f"{type(e).__name__}: {e}"
            log(f"{slug}: FAILED {rec['error_message']}")
            append_ledger(rec)
        else:  # the upload landed and is ledgered; only an extra went wrong
            log(f"{slug}: post-upload step failed (non-fatal): {e}")
        return rec
    finally:
        # A render-only run with no --out leaves its mp4 IN the workdir: keep it.
        holds_output = rec["status"] == "rendered" and out_path is None
        if ok and not keep and not holds_output:
            shutil.rmtree(workdir, ignore_errors=True)
        elif not ok:
            log(f"{slug}: workdir kept for debugging: {workdir}")


def _extras(
    client: YouTubeClient,
    vid: str,
    vcfg: dict[str, Any],
    words: list[dict[str, Any]] | None,
    chapters: list[dict[str, Any]],
    thumb: Path,
) -> dict[str, str]:
    """Caption track, thumbnail, playlist. Each is best-effort: the video is already
    up, and e.g. thumbnails.set is refused outright until the channel is verified."""
    yt = vcfg["youtube"]
    out: dict[str, str] = {}

    def attempt(name: str, enabled: bool, fn) -> None:
        if not enabled:
            out[name] = "skipped"
            return
        try:
            fn()
            out[name] = "ok"
        except Exception as e:
            out[name] = "failed"
            log(f"{vid}: {name} failed (non-fatal): {e}")

    srt = build_srt(group_phrases(words or [], [c["t"] for c in chapters[1:]], max_chars=84))
    attempt(
        "caption_track",
        yt["caption_track"] and bool(words),
        lambda: client.insert_captions(vid, srt),
    )
    attempt(
        "thumbnail", yt["thumbnail"] and thumb.exists(), lambda: client.set_thumbnail(vid, thumb)
    )
    attempt(
        "playlist",
        bool(yt["playlist_id"]),
        lambda: client.add_to_playlist(vid, str(yt["playlist_id"])),
    )
    return out


# --- one-time OAuth ----------------------------------------------------------


def youtube_auth(client_secrets: Path, port: int = 8765) -> None:
    """Installed-app OAuth with a loopback redirect; stores the refresh token (and
    the client id/secret it belongs to) in the 0600 secrets.json, merged with what
    is already there. Interactive by nature: a human approves in a browser."""
    import http.server
    import secrets as pysecrets
    import webbrowser

    raw = json.loads(client_secrets.read_text())
    app = raw.get("installed") or raw.get("web") or raw
    cid, csecret = app["client_id"], app["client_secret"]
    redirect = f"http://127.0.0.1:{port}/"
    state = pysecrets.token_urlsafe(16)
    url = (
        AUTH_URL
        + "?"
        + urllib.parse.urlencode(
            {
                "client_id": cid,
                "redirect_uri": redirect,
                "response_type": "code",
                "scope": " ".join(YT_SCOPES),
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
            }
        )
    )
    got: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"clodcast: YouTube authorized. You can close this tab.")

        def log_message(self, *a):
            pass

    print(f"Open this URL to authorize YouTube uploads:\n{url}", flush=True)
    webbrowser.open(url)
    with http.server.HTTPServer(("127.0.0.1", port), Handler) as srv:
        while "code" not in got and "error" not in got:
            srv.handle_request()
    if got.get("state") != state or "code" not in got:
        raise VideoError(f"authorization failed: {got.get('error', 'state mismatch')}")
    body = urllib.parse.urlencode(
        {
            "code": got["code"],
            "client_id": cid,
            "client_secret": csecret,
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
        }
    ).encode()
    with urllib.request.urlopen(  # noqa: S310
        urllib.request.Request(TOKEN_URL, data=body, method="POST"), timeout=30
    ) as resp:
        tok = json.loads(resp.read())
    if "refresh_token" not in tok:
        raise VideoError("no refresh_token returned; revoke the app's access and retry")
    path = render.CONFIG_DIR / "secrets.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data.update(
        {
            "YOUTUBE_CLIENT_ID": cid,
            "YOUTUBE_CLIENT_SECRET": csecret,
            "YOUTUBE_REFRESH_TOKEN": tok["refresh_token"],
        }
    )
    render._atomic_write_text(path, json.dumps(data, indent=2) + "\n")
    os.chmod(path, 0o600)
    print(f"saved YouTube credentials to {path}")


# --- CLI ---------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["auth"]:
        ap = argparse.ArgumentParser(prog="video.py auth")
        ap.add_argument("--client-secrets", type=Path, required=True)
        ap.add_argument("--port", type=int, default=8765)
        ns = ap.parse_args(argv[1:])
        ns.command = "auth"
        return ns
    ap = argparse.ArgumentParser(description="Render published episodes as videos; ship to YouTube")
    pick = ap.add_mutually_exclusive_group(required=True)
    pick.add_argument("--pending", action="store_true", help="recent episodes without a video")
    pick.add_argument("--slug", help="one episode by slug (explicit back-fill / re-render)")
    ap.add_argument("--no-upload", action="store_true", help="render only; never touch YouTube")
    ap.add_argument("--out", type=Path, help="write the mp4 here (implies it is kept)")
    ap.add_argument("--keep", action="store_true", help="keep the workdir after success")
    ap.add_argument(
        "--force", action="store_true", help="with --slug: upload even if already uploaded"
    )
    ns = ap.parse_args(argv)
    if ns.out and ns.pending:
        ap.error("--out needs --slug (one episode, one file)")
    if ns.force and not ns.slug:
        ap.error("--force needs --slug")
    ns.command = "run"
    return ns


def report_line(rec: dict[str, Any]) -> str:
    """One stdout line per episode, the SHIPPED/FAILED posture of the daily run."""
    if rec["status"] == "uploaded":
        extras = " ".join(f"{k}={v}" for k, v in (rec.get("extras") or {}).items())
        return (
            f"VIDEO uploaded {rec['slug']} {rec['youtube_url']} "
            f"privacy={rec['privacy_status']} {extras}".rstrip()
        )
    if rec["status"] == "rendered":
        return f"VIDEO rendered {rec['slug']} {rec.get('output_path') or ''}".rstrip()
    return f"FAILED {rec['slug']} {rec.get('error_message') or ''}".rstrip()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "auth":
        youtube_auth(args.client_secrets, args.port)
        return 0
    config = render.load_config()
    try:
        vcfg = resolve_video_config(config.get("video"))
    except VideoError as e:
        print(f"FAILED {e}")
        return 1
    if vcfg is None:
        if args.pending:
            print("VIDEO disabled (config `video.enabled` is not true)")
            return 0
        vcfg = resolve_video_config({**(config.get("video") or {}), "enabled": True})
    upload = not args.no_upload and vcfg["youtube"]["enabled"]
    problems = preflight(vcfg, upload=upload)
    if problems:
        print(f"FAILED preflight: {'; '.join(problems)}")
        return 1
    try:
        entries = fetch_feed_manifest(config)
    except Exception as e:
        print(f"FAILED feed manifest unreadable: {e}")
        return 1
    rows = load_ledger()
    if args.pending:
        todo = select_pending(
            entries,
            rows,
            today=dt.datetime.now(dt.timezone.utc).date(),
            lookback_days=vcfg["lookback_days"],
            max_per_run=vcfg["max_per_run"],
        )
        if not todo:
            print("VIDEO none pending")
            return 0
    else:
        todo = [e for e in entries if e["slug"] == args.slug]
        if not todo:
            print(f"FAILED no published episode with slug {args.slug!r}")
            return 1
        if upload and args.slug in uploaded_slugs(rows) and not args.force:
            print(f"VIDEO already uploaded {args.slug} (use --force to upload again)")
            return 0
    failed = False
    for entry in todo:
        rec = process_episode(entry, vcfg, upload=upload, out_path=args.out, keep=args.keep)
        print(report_line(rec), flush=True)
        failed = failed or rec["status"] == "failed"
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
