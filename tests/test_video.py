"""Tests for the episode-video step (skills/daily-podcast/video.py).

Everything here runs without numpy / OpenCV / a transcriber / the network: the
frame renderer, the transcriber, the feed fetch and YouTube's HTTP are all seams
(`renderer=`, `transcriber=`, `fetch=`, `YouTubeClient(opener=)`). One smoke test
at the bottom renders real frames and is importorskip-gated on the `video` extra.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import re
import shutil
import subprocess
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

import render
import video

SKILL_DIR = Path(video.__file__).resolve().parent


# --- fixtures ------------------------------------------------------------------


def _entry(slug="daily-digest-september-23-2026", pub="2026-09-23", **kw):
    e = {
        "slug": slug,
        "title": "Opus 5.5 vs GPT-6, the EvilTokens takedown, ShinyHunters' FBI claim"
        " - September 23, 2026",
        "summary": "Anthropic and OpenAI open a price war.",
        "pubDate": f"{pub}T12:00:00+00:00",
        "mp3_url": f"https://audio.example/{slug}.mp3",
        "duration_s": 519.079,
        "chapters": [
            {"title": "Intro", "start_ms": 0, "source_url": None},
            {
                "title": "Claude Opus 5.5 and a new price war",
                "start_ms": 26786,
                "source_url": "https://simonwillison.net/2026/Sep/22/x/",
            },
            {
                "title": "Microsoft disrupts EvilTokens",
                "start_ms": 88000,
                "source_url": "https://www.helpnetsecurity.com/2026/09/23/y/",
            },
            {"title": "Sign-off", "start_ms": 495000, "source_url": None},
        ],
    }
    e.update(kw)
    return e


def _vcfg(**kw):
    raw = {"enabled": True, "youtube": {"enabled": True}}
    raw.update(kw)
    return video.resolve_video_config(raw)


def _words(*triples):
    return [{"w": w, "s": s, "e": e, "sp": True} for w, s, e in triples]


# --- config ----------------------------------------------------------------------


def test_absent_or_disabled_video_config_is_none():
    assert video.resolve_video_config(None) is None
    assert video.resolve_video_config({}) is None
    assert video.resolve_video_config({"enabled": False}) is None


def test_enabled_config_is_fully_defaulted_and_private_by_default():
    cfg = video.resolve_video_config({"enabled": True})
    assert set(cfg) == set(video.VIDEO_DEFAULTS)
    assert cfg["youtube"]["privacy_status"] == "private"
    assert cfg["youtube"]["enabled"] is False  # rendering never implies publishing


@pytest.mark.parametrize(
    "raw, needle",
    [
        ({"enabled": True, "privacy": "public"}, "unknown key"),
        ({"enabled": True, "youtube": {"privacy": "public"}}, "unknown key"),
        ({"enabled": True, "youtube": {"privacy_status": "Public"}}, "privacy_status"),
        ({"enabled": True, "lookback_days": 0}, "lookback_days"),
        ({"enabled": True, "max_per_run": True}, "max_per_run"),
        ({"enabled": True, "jobs": -1}, "jobs"),
        ("yes", "must be an object"),
    ],
)
def test_config_is_a_closed_whitelist_and_typos_die(raw, needle):
    with pytest.raises(video.VideoError, match=needle):
        video.resolve_video_config(raw)


# --- ledger ------------------------------------------------------------------------


def test_ledger_lives_under_the_sandboxed_config_dir():
    assert video.ledger_path().parent == render.CONFIG_DIR


def test_ledger_is_append_only_with_a_full_key_set_and_survives_a_torn_line():
    video.append_ledger({"slug": "a", "status": "failed"})
    with video.ledger_path().open("a") as f:
        f.write('{"slug": "torn"')  # a crash mid-write
    video.append_ledger({"slug": "b", "status": "uploaded", "youtube_id": "X"})
    rows = video.load_ledger()
    assert [r["slug"] for r in rows] == ["a"]  # the torn line swallowed "b"'s newline
    lines = video.ledger_path().read_text().splitlines()
    assert json.loads(lines[0]).keys() == set(video.LEDGER_FIELDS)


def test_ledger_rows_each_carry_every_field():
    video.append_ledger({"slug": "a", "status": "rendered"})
    video.append_ledger({"slug": "b", "status": "uploaded"})
    for line in video.ledger_path().read_text().splitlines():
        assert set(json.loads(line)) == set(video.LEDGER_FIELDS)


# --- pending selection -----------------------------------------------------------


def test_pending_is_newest_first_bounded_and_skips_only_uploaded():
    entries = [_entry(f"ep-{d}", f"2026-09-{d:02d}") for d in (19, 20, 21, 22, 23)]
    rows = [
        {"slug": "ep-23", "status": "uploaded"},
        {"slug": "ep-22", "status": "failed"},  # a failure must not block a retry
        {"slug": "ep-21", "status": "rendered"},  # nor a render-only run
        {"slug": "ep-21", "status": "extras"},
    ]
    got = video.select_pending(
        entries, rows, today=dt.date(2026, 9, 23), lookback_days=3, max_per_run=5
    )
    assert [e["slug"] for e in got] == ["ep-22", "ep-21", "ep-20"]
    got = video.select_pending(
        entries, rows, today=dt.date(2026, 9, 23), lookback_days=3, max_per_run=1
    )
    assert [e["slug"] for e in got] == ["ep-22"]


def test_pending_never_guesses_a_date_and_never_reaches_the_back_catalogue():
    entries = [
        _entry("bad", pubDate="soon"),
        _entry("future", "2026-09-30"),
        _entry("old", "2026-06-01"),
    ]
    assert (
        video.select_pending(
            entries, [], today=dt.date(2026, 9, 23), lookback_days=3, max_per_run=10
        )
        == []
    )


# --- chapters, title, description ---------------------------------------------------


def test_parse_chapters_converts_ms_and_inserts_an_opening_chapter_when_missing():
    ch = video.parse_chapters({"chapters": [{"title": "A", "start_ms": 5000}]})
    assert ch[0] == {"t": 0.0, "title": "Intro", "source_url": None}
    assert ch[1]["t"] == 5.0


def test_youtube_chapters_follow_youtubes_rendering_rules():
    chapters = [
        {"t": 0.4, "title": "Intro"},  # first chapter is pinned to 0:00 regardless
        {"t": 26.8, "title": "One"},
        {"t": 30.0, "title": "Too close"},  # < 10 s after "One": dropped
        {"t": 88.0, "title": "Two"},
        {"t": 515.0, "title": "Tail"},  # < 10 s before the end: dropped
    ]
    assert video.youtube_chapters(chapters, 519.0) == [(0, "Intro"), (26, "One"), (88, "Two")]
    assert video.youtube_chapters(chapters[:2], 519.0) == []  # fewer than three renders nothing


def test_title_fits_youtube_and_keeps_the_date():
    long = _entry(title="Word " * 40 + "- September 23, 2026")
    t = video.build_youtube_title(long)
    assert len(t) <= video.YT_TITLE_MAX
    assert t.endswith(" - September 23, 2026")
    assert "<" not in video.build_youtube_title(_entry(title="a <b> c"))


def test_description_carries_chapters_page_and_disclosure_within_5000_bytes():
    vcfg = _vcfg()
    e = _entry()
    desc = video.build_youtube_description(e, video.parse_chapters(e), vcfg)
    lines = desc.splitlines()
    assert "0:00 Intro" in lines
    assert "0:26 Claude Opus 5.5 and a new price war" in lines
    assert f"https://cortech.online/podcast/{e['slug']}/" in desc
    assert "synthetic" in desc
    assert "<" not in desc and ">" not in desc


def test_description_trims_sources_not_chapters_when_over_the_byte_cap():
    chapters = [{"title": "Intro", "start_ms": 0, "source_url": None}] + [
        {
            "title": f"Story {i} " + "é" * 60,  # multi-byte: the cap is BYTES
            "start_ms": 20000 * (i + 1),
            "source_url": "https://example.com/" + "p" * 200,
        }
        for i in range(24)
    ]
    e = _entry(chapters=chapters, duration_s=600.0)
    desc = video.build_youtube_description(e, video.parse_chapters(e), _vcfg())
    assert len(desc.encode()) <= video.YT_DESCRIPTION_MAX_BYTES
    assert "0:00 Intro" in desc and "8:00 Story 23" in desc  # every chapter line survived


def test_tags_respect_the_500_character_budget():
    tags = video.build_tags([f"tag number {i}" for i in range(100)])
    assert sum(len(t) + 4 for t in tags) <= video.YT_TAGS_MAX_CHARS


def test_metadata_discloses_synthetic_media_and_uses_the_configured_privacy():
    meta = video.build_video_metadata(
        _entry(), video.parse_chapters(_entry()), _vcfg(youtube={"privacy_status": "unlisted"})
    )
    assert meta["status"]["containsSyntheticMedia"] is True
    assert meta["status"]["privacyStatus"] == "unlisted"
    assert meta["status"]["selfDeclaredMadeForKids"] is False


# --- captions -------------------------------------------------------------------------


def test_snap_lands_the_cut_in_the_silence_before_the_first_word():
    words = _words(("end.", 24.0, 24.9), ("Anthropic", 27.2, 27.8))
    ch = [{"t": 0.0}, {"t": 26.786}]
    video.snap_chapter_starts(ch, words)
    assert 24.9 < ch[1]["t"] < 27.2
    assert ch[1]["t"] == pytest.approx(26.8)


def test_snap_never_cuts_over_the_previous_word_and_trusts_the_feed_without_speech():
    words = _words(("still", 26.5, 26.95), ("Next", 27.0, 27.4))
    ch = [{"t": 0.0}, {"t": 26.9}, {"t": 100.0}]
    video.snap_chapter_starts(ch, words)
    assert ch[1]["t"] >= 26.95
    assert ch[2]["t"] == 100.0  # no speech anywhere near: unchanged
    assert ch[0]["t"] == 0.0


def test_phrases_break_on_chapter_bounds_and_pauses_and_never_overlap():
    words = _words(
        ("one", 0.0, 0.3),
        ("two", 0.35, 0.6),
        ("three", 1.5, 1.8),  # 0.9 s pause
        ("four", 1.85, 2.0),
        ("five", 2.05, 2.3),  # chapter boundary at 2.02 sits between four and five
    )
    ph = video.group_phrases(words, [2.02])
    assert [[w["w"] for w in p[2]] for p in ph] == [["one", "two"], ["three", "four"], ["five"]]
    for (_, e1, _), (s2, _, _) in zip(ph, ph[1:], strict=False):
        assert e1 <= s2


def test_tokens_without_a_leading_space_join_their_neighbour():
    ws = [
        {"w": "$2", "s": 0, "e": 0.2, "sp": True},
        {"w": ".56.", "s": 0.2, "e": 0.4, "sp": False},
        {"w": "60", "s": 0.5, "e": 0.6, "sp": True},
        {"w": "%", "s": 0.6, "e": 0.7, "sp": False},
    ]
    assert video.join_words(ws) == "$2.56. 60%"


def test_srt_is_numbered_and_timestamped():
    srt = video.build_srt([(1.234, 3.5, _words(("Hello", 1.3, 1.6), ("there", 1.7, 2.0)))])
    assert srt == "1\n00:00:01,234 --> 00:00:03,500\nHello there\n"


# --- YouTube client ---------------------------------------------------------------------


class _Resp:
    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    def read(self):
        return self._body


def _http_error(code, headers=None):
    msg = Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return urllib.error.HTTPError("https://x", code, "err", msg, io.BytesIO(b"{}"))


class _FakeYouTube:
    """Scripted opener: token, resumable init, then the queued PUT outcomes."""

    def __init__(self, puts, caption_error=None):
        self.puts = list(puts)
        self.calls = []
        self.caption_error = caption_error

    def __call__(self, req, timeout):
        url = req.full_url
        self.calls.append((req.get_method(), url, dict(req.header_items())))
        if url == video.TOKEN_URL:
            return _Resp(body=json.dumps({"access_token": "tok", "expires_in": 3600}).encode())
        if "uploadType=resumable" in url:
            return _Resp(headers={"Location": "https://upload.example/session"})
        if url == "https://upload.example/session":
            out = self.puts.pop(0)
            if isinstance(out, Exception):
                raise out
            return out
        if "/captions" in url and self.caption_error:
            raise self.caption_error
        return _Resp(body=b"{}")


SECRETS = {k: "x" for k in video.YT_SECRET_KEYS}


def test_resumable_upload_walks_308s_and_returns_the_video_id(tmp_path, monkeypatch):
    monkeypatch.setattr(video, "UPLOAD_CHUNK", 256 * 1024)
    f = tmp_path / "v.mp4"
    f.write_bytes(b"\0" * (256 * 1024 * 2 + 10))
    fake = _FakeYouTube(
        [
            _http_error(308, {"Range": "bytes=0-262143"}),
            _Resp(status=308, headers={"Range": "bytes=0-524287"}),
            _Resp(status=201, body=b'{"id": "VID123"}'),
        ]
    )
    vid = video.YouTubeClient(SECRETS, opener=fake).upload_video(f, {"snippet": {}})
    assert vid == "VID123"
    ranges = [h.get("Content-range") for m, u, h in fake.calls if u.endswith("/session")]
    assert ranges == [
        "bytes 0-262143/524298",
        "bytes 262144-524287/524298",
        "bytes 524288-524297/524298",
    ]


def test_upload_resumes_from_the_servers_offset_after_a_5xx(tmp_path, monkeypatch):
    monkeypatch.setattr(video, "UPLOAD_CHUNK", 256 * 1024)
    monkeypatch.setattr(video.time, "sleep", lambda s: None)
    f = tmp_path / "v.mp4"
    f.write_bytes(b"\0" * (256 * 1024 + 5))
    fake = _FakeYouTube(
        [
            _http_error(503),
            _http_error(308, {"Range": "bytes=0-262143"}),  # the status probe
            _Resp(status=201, body=b'{"id": "V"}'),
        ]
    )
    assert video.YouTubeClient(SECRETS, opener=fake).upload_video(f, {}) == "V"
    ranges = [h.get("Content-range") for m, u, h in fake.calls if u.endswith("/session")]
    assert ranges == ["bytes 0-262143/262149", "bytes */262149", "bytes 262144-262148/262149"]


def test_missing_credentials_fail_before_any_request():
    with pytest.raises(video.VideoError, match="YOUTUBE_REFRESH_TOKEN"):
        video.YouTubeClient({"YOUTUBE_CLIENT_ID": "a", "YOUTUBE_CLIENT_SECRET": "b"})


def test_secrets_resolve_env_first_then_secrets_json(monkeypatch):
    (render.CONFIG_DIR / "secrets.json").write_text(
        json.dumps({k: "file" for k in video.YT_SECRET_KEYS})
    )
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "env")
    got = video.load_youtube_secrets()
    assert got["YOUTUBE_CLIENT_ID"] == "env"
    assert got["YOUTUBE_REFRESH_TOKEN"] == "file"


# --- one episode ---------------------------------------------------------------------------


def _fake_renderer(calls):
    def renderer(plan, out, *, workdir, jobs, thumb):
        calls.append(plan)
        out.write_bytes(b"mp4")
        thumb.write_bytes(b"jpg")
        return {"duration_s": 519.0}

    return renderer


def _run(upload=True, client=None, renderer=None, vcfg=None, **kw):
    calls = []
    rec = video.process_episode(
        _entry(),
        {"show_name": "Cortech Daily"},
        vcfg or _vcfg(),
        upload=upload,
        fetch=lambda url: b"mp3",
        client=client,
        renderer=renderer or _fake_renderer(calls),
        transcriber=lambda p: _words(("Hello", 1.0, 1.4)),
        **kw,
    )
    return rec, calls


def test_upload_is_ledgered_before_the_extras_run(monkeypatch):
    seen_at_captions = []

    class Client(video.YouTubeClient):
        def upload_video(self, path, metadata):
            return "VID"

        def insert_captions(self, vid, srt):
            seen_at_captions.append([r["status"] for r in video.load_ledger()])
            raise RuntimeError("quota")

    rec, _ = _run(client=Client(SECRETS, opener=_FakeYouTube([])))
    assert seen_at_captions == [["uploaded"]]
    assert rec["status"] == "uploaded"
    assert rec["extras"]["caption_track"] == "failed"  # an extra's failure is not the video's
    assert [r["status"] for r in video.load_ledger()] == ["uploaded", "extras"]
    assert video.uploaded_slugs(video.load_ledger()) == {rec["slug"]}


def test_a_failed_render_is_ledgered_keeps_its_workdir_and_never_calls_youtube():
    def boom(*a, **k):
        raise RuntimeError("ffmpeg died")

    fake = _FakeYouTube([])
    rec, _ = _run(client=video.YouTubeClient(SECRETS, opener=fake), renderer=boom)
    assert rec["status"] == "failed" and "ffmpeg died" in rec["error_message"]
    assert fake.calls == []
    kept = list(render.TMP_BASE.glob(f"{video.VIDEO_WORKDIR_PREFIX}*"))
    assert len(kept) == 1


def test_a_successful_upload_cleans_its_workdir():
    class Client(video.YouTubeClient):
        def upload_video(self, path, metadata):
            return "VID"

    _run(client=Client(SECRETS, opener=_FakeYouTube([])))
    assert list(render.TMP_BASE.glob(f"{video.VIDEO_WORKDIR_PREFIX}*")) == []


def test_render_only_keeps_the_video_and_never_counts_as_uploaded():
    rec, calls = _run(upload=False)
    assert rec["status"] == "rendered"
    assert Path(rec["output_path"]).exists()
    assert video.uploaded_slugs(video.load_ledger()) == set()
    plan = calls[0]
    assert plan["teaser"].startswith("Opus 5.5 vs GPT-6")
    assert plan["date_long"] == "Wednesday · September 23, 2026"
    assert plan["chapters"][1]["source"] == "simonwillison.net"
    assert plan["monogram"] == "CT"


def test_video_workdirs_stay_out_of_render_prune_namespace():
    assert not video.VIDEO_WORKDIR_PREFIX.startswith(render.WORKDIR_PREFIX)


# --- CLI ---------------------------------------------------------------------------------------


def _config(tmp, video_block):
    render.CONFIG_PATH.write_text(
        json.dumps({"r2_public_base_url": "https://audio.example", "video": video_block})
    )


def test_pending_with_video_off_is_a_quiet_no_op(capsys):
    _config(None, None)
    assert video.main(["--pending"]) == 0
    assert capsys.readouterr().out.startswith("VIDEO disabled")


def test_a_slug_already_on_youtube_is_not_uploaded_again_without_force(capsys, monkeypatch):
    _config(None, {"enabled": True, "youtube": {"enabled": True}})
    monkeypatch.setattr(video, "preflight", lambda vcfg, upload: [])
    monkeypatch.setattr(video, "fetch_feed_manifest", lambda cfg: [_entry()])
    video.append_ledger({"slug": _entry()["slug"], "status": "uploaded"})
    monkeypatch.setattr(video, "process_episode", lambda *a, **k: pytest.fail("re-uploaded"))
    assert video.main(["--slug", _entry()["slug"]]) == 0
    assert "already uploaded" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [["--pending", "--slug", "x"], ["--pending", "--out", "a.mp4"], ["--pending", "--force"]],
)
def test_cli_refuses_ambiguous_invocations(argv):
    with pytest.raises(SystemExit):
        video.main(argv)


# --- assets -----------------------------------------------------------------------------------


def test_fonts_are_bundled_with_their_licenses_and_match_the_renderer():
    for f in video.FONT_FILES:
        assert (video.FONT_DIR / f).is_file()
    assert (video.FONT_DIR / "OFL-SpaceGrotesk.txt").is_file()
    assert (video.FONT_DIR / "OFL-JetBrainsMono.txt").is_file()
    frames_src = (SKILL_DIR / "video_frames.py").read_text()
    assert set(re.findall(r'"([a-z-]+-\d00\.ttf)"', frames_src)) == set(video.FONT_FILES)


def test_video_module_stays_importable_without_the_render_extras():
    src = (SKILL_DIR / "video.py").read_text()
    top = src.split("\ndef ", 1)[0]
    for heavy in ("numpy", "cv2", "PIL", "video_frames", "mlx_whisper"):
        assert not re.search(rf"^(import|from) {heavy}\b", top, re.M), heavy


# --- real frames (the `video` extra) ------------------------------------------------------------


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_render_video_smoke(tmp_path):
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    import video_frames

    audio = tmp_path / "a.mp3"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=4", "-ac", "1", str(audio)],
        check=True,
    )
    plan = {
        "audio": str(audio),
        "show_name": "Cortech Daily",
        "monogram": "CT",
        "date_long": "Wednesday · September 23, 2026",
        "teaser": "A test",
        "outro_url": "cortech.online/podcast",
        "chapters": [
            {"t": 0.0, "title": "Intro", "source": ""},
            {"t": 1.0, "title": "A story — with “quotes” and ünïcode ✓", "source": "example.com"},
            {"t": 3.0, "title": "Sign-off", "source": ""},
        ],
        "phrases": [{"s": 0.1, "e": 0.9, "words": _words(("Hello", 0.1, 0.5))}],
    }
    out, thumb = tmp_path / "v.mp4", tmp_path / "t.jpg"
    info = video_frames.render_video(plan, out, workdir=tmp_path / "w", jobs=2, thumb=thumb)
    assert info["duration_s"] == pytest.approx(4.0, abs=0.1)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height,channels"]
        + ["-of", "json", str(out)],
        capture_output=True,
        text=True,
        check=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    assert {"codec_name": "h264", "width": 1920, "height": 1080} in [
        {k: s.get(k) for k in ("codec_name", "width", "height")} for s in streams
    ]
    assert any(s.get("codec_name") == "aac" and s.get("channels") == 2 for s in streams)
    assert thumb.stat().st_size > 10_000
