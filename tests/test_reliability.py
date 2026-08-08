"""
Invariant tests for the reliability layer added to skills/daily-podcast/render.py.

One test per hardened failure mode from the production audit (see
docs/superpowers/specs/2026-08-08-pipeline-reliability-design.md and incidents/).
Everything here is pure-Python: the ffprobe, save-to-spotify, and network seams
are monkeypatched, so the suite stays fast and runs on CI without credentials.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

import render

# --- helpers ---------------------------------------------------------------


def _isolate_config(monkeypatch, tmp_path: Path) -> Path:
    """Point every user-level state path at a tmp dir so tests never touch
    ~/.config/daily-podcast."""
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(render, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(render, "COVERED_PATH", tmp_path / "covered.json")
    monkeypatch.setattr(render, "INFLIGHT_PATH", tmp_path / "inflight.json")
    monkeypatch.setattr(render, "RUN_LOG_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(render, "REJECTIONS_PATH", tmp_path / "rejections.jsonl")
    monkeypatch.setattr(render, "INCIDENT_DIR", tmp_path / "incidents" / "new")
    return tmp_path


def _mp3(path: Path, body: bytes = b"ID3fake-audio") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def _timeline(chapter_starts_ms: list[int]) -> dict:
    return {
        "items": [
            {"chapter": {"start_ms": s, "title": f"c{i}"}} for i, s in enumerate(chapter_starts_ms)
        ]
    }


# --- durable run state (failure mode 5) ------------------------------------


def test_state_starts_empty_and_marks_stages(tmp_path):
    wd = tmp_path / "wd"
    wd.mkdir()

    assert render.load_state(wd) == {"stages": {}}
    assert render.stage_done(render.load_state(wd), "segments") is False

    render.mark_stage(wd, "segments", count=12)
    state = render.load_state(wd)

    assert render.stage_done(state, "segments") is True
    assert state["stages"]["segments"]["count"] == 12
    assert state["stages"]["segments"]["completed_at"]


def test_state_survives_reload_and_is_append_only_across_stages(tmp_path):
    wd = tmp_path / "wd"
    wd.mkdir()
    render.mark_stage(wd, "segments", count=3)
    render.mark_stage(wd, "concat", duration_s=120.5)

    state = render.load_state(wd)

    assert render.stage_done(state, "segments") and render.stage_done(state, "concat")
    assert state["stages"]["segments"]["count"] == 3


def test_state_treats_corrupt_file_as_empty(tmp_path):
    """A corrupt state file must not wedge every future run — mirrors the
    best-effort contract of load_covered / _load_inflight."""
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / render.STATE_FILENAME).write_text("{not json")

    assert render.load_state(wd) == {"stages": {}}


def test_auto_workdir_is_deterministic_per_date(monkeypatch, tmp_path):
    """A random mkdtemp() cannot be resumed, so a dropped connection loses the
    whole render. The auto workdir is now per-date and therefore resumable."""
    monkeypatch.setattr(render, "TMP_BASE", tmp_path)

    first = render.default_workdir(dt.date(2026, 8, 8))
    second = render.default_workdir(dt.date(2026, 8, 8))

    assert first == second
    assert first.name == f"{render.WORKDIR_PREFIX}2026-08-08"
    assert first.parent == tmp_path


# --- pre-flight: capacity (failure mode 2) ---------------------------------


def _episodes(n: int, status: str = "READY") -> list[dict]:
    return [
        {
            "episode_uri": f"spotify:episode:e{i}",
            "status": status,
            "created_at": f"2026-01-{(i % 28) + 1:02d}T00:00:00Z",
            "title": f"ep{i}",
        }
        for i in range(n)
    ]


def test_preflight_capacity_passes_when_below_cap(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(render, "_list_episodes", lambda show: _episodes(40))
    monkeypatch.setattr(
        render, "prune_episodes_for_capacity", lambda *a, **k: pytest.fail("must not prune")
    )

    result = render.preflight_capacity("spotify:show:x", {}, dry_run=False)

    assert result["ok"] is True
    assert result["count"] == 40
    assert result["pruned"] == 0


def test_preflight_capacity_pre_prunes_at_cap_before_any_render(monkeypatch, tmp_path):
    """The whole point: the 429 used to surface only AFTER a ~5-minute TTS
    render was already spent. Pre-flight reclaims the slot first."""
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(render, "_list_episodes", lambda show: _episodes(60))
    pruned_calls = []

    def fake_prune(show_id, config, *, dry_run, record=None, now=None):
        pruned_calls.append(show_id)
        return 1

    monkeypatch.setattr(render, "prune_episodes_for_capacity", fake_prune)
    cfg = {"auto_prune_episodes": True, "max_prune_per_run": 1}

    result = render.preflight_capacity("spotify:show:x", cfg, dry_run=False)

    assert pruned_calls == ["spotify:show:x"]
    assert result["ok"] is True
    assert result["pruned"] == 1


def test_preflight_capacity_fails_at_cap_when_auto_prune_disabled(monkeypatch, tmp_path):
    """Opt-in stays opt-in: with auto_prune_episodes off, pre-flight refuses the
    run rather than silently deleting an episode."""
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(render, "_list_episodes", lambda show: _episodes(60))

    result = render.preflight_capacity("spotify:show:x", {}, dry_run=False)

    assert result["ok"] is False
    assert result["pruned"] == 0
    assert "auto_prune_episodes" in result["detail"]


def test_preflight_capacity_never_prunes_on_dry_run(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(render, "_list_episodes", lambda show: _episodes(60))
    monkeypatch.setattr(
        render, "prune_episodes_for_capacity", lambda *a, **k: pytest.fail("dry-run must not prune")
    )

    result = render.preflight_capacity(
        "spotify:show:x", {"auto_prune_episodes": True}, dry_run=True
    )

    assert result["pruned"] == 0
    assert result["ok"] is True


# --- pre-flight: R2 credentials (failure mode 7) ---------------------------


def test_r2_credentials_complete_passes(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ACCOUNT_ID"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("R2_BUCKET", "b")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://example.com")

    result = render.check_r2_credentials({})

    assert result["ok"] is True
    assert result["state"] == "configured"


def test_r2_credentials_absent_is_a_pass_not_a_failure(monkeypatch, tmp_path):
    """R2 is optional — a show with no web feed must still ship."""
    _isolate_config(monkeypatch, tmp_path)
    for k in (
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_ACCOUNT_ID",
        "R2_BUCKET",
        "R2_PUBLIC_BASE_URL",
    ):
        monkeypatch.delenv(k, raising=False)

    result = render.check_r2_credentials({})

    assert result["ok"] is True
    assert result["state"] == "absent"


def test_r2_credentials_partial_fails(monkeypatch, tmp_path):
    """The 07-28 silent-web-feed-miss shape: bucket configured, credentials not.
    Half-configured is a config bug, so pre-flight refuses rather than skipping."""
    _isolate_config(monkeypatch, tmp_path)
    for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ACCOUNT_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("R2_BUCKET", raising=False)
    monkeypatch.delenv("R2_PUBLIC_BASE_URL", raising=False)

    result = render.check_r2_credentials({"r2_bucket": "b", "r2_public_base_url": "https://x.dev"})

    assert result["ok"] is False
    assert result["state"] == "partial"
    assert "R2_ACCESS_KEY_ID" in result["detail"]


# --- pre-flight: aggregation + gating --------------------------------------


def test_preflight_aborts_render_before_tts(monkeypatch, tmp_path):
    """The gate must fire before the expensive stage, not after it."""
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(
        render,
        "preflight",
        lambda *a, **k: (False, [{"name": "save-to-spotify-auth", "ok": False, "detail": "401"}]),
    )
    monkeypatch.setattr(
        render,
        "render_segments",
        lambda *a, **k: pytest.fail("TTS must not run after a failed preflight"),
    )
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({"title": "T", "summary": "s", "segments": [{"text": "hi", "source_url": None}]})
    )
    (tmp_path / "config.json").write_text(json.dumps({"show_id": "spotify:show:x"}))
    monkeypatch.setattr(
        sys, "argv", ["render.py", "--manifest", str(manifest), "--workdir", str(tmp_path / "wd")]
    )

    with pytest.raises(SystemExit) as e:
        render.main()

    assert e.value.code != 0


def test_skip_preflight_flag_bypasses_the_gate(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    called = []
    monkeypatch.setattr(render, "preflight", lambda *a, **k: called.append(1) or (True, []))

    args = render._parse_args(["--manifest", "m.json", "--skip-preflight"])

    assert args.skip_preflight is True
    assert called == []


def test_preflight_records_checks_into_the_run_record(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(render, "shutil", render.shutil)
    monkeypatch.setattr(render.shutil, "which", lambda t: f"/usr/bin/{t}")
    monkeypatch.setattr(
        render, "check_r2_credentials", lambda c: {"ok": True, "state": "absent", "detail": "n/a"}
    )
    monkeypatch.setattr(
        render,
        "preflight_capacity",
        lambda *a, **k: {"ok": True, "count": 1, "pruned": 0, "detail": "1/60"},
    )
    monkeypatch.setattr(
        render, "_tts_module_check", lambda: {"name": "tts-module", "ok": True, "detail": "stub"}
    )
    (tmp_path / "config.json").write_text(json.dumps({"show_id": "spotify:show:x"}))
    record = render._new_run_record()

    ok, checks = render.preflight(
        {"show_id": "spotify:show:x"}, show_id="spotify:show:x", dry_run=True, record=record
    )

    assert ok is True
    assert record["preflight"] is not None
    assert any(c["name"] == "encoder-profile" for c in checks)


def test_preflight_dry_run_skips_spotify_calls(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(render.shutil, "which", lambda t: f"/usr/bin/{t}")
    monkeypatch.setattr(
        render, "_list_episodes", lambda show: pytest.fail("dry-run must not call Spotify")
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: pytest.fail("dry-run must not shell out to save-to-spotify"),
    )
    monkeypatch.setattr(
        render, "_tts_module_check", lambda: {"name": "tts-module", "ok": True, "detail": "stub"}
    )
    (tmp_path / "config.json").write_text(json.dumps({"show_id": "spotify:show:x"}))

    ok, checks = render.preflight(
        {"show_id": "spotify:show:x"}, show_id="spotify:show:x", dry_run=True
    )

    names = {c["name"] for c in checks}
    assert "save-to-spotify-auth" not in names
    assert "episode-capacity" not in names
    assert ok is True


# --- artifact gate (failure mode 1) ----------------------------------------


def test_artifact_fingerprint_is_content_addressed(tmp_path):
    a = _mp3(tmp_path / "a.mp3", b"same-bytes")
    b = _mp3(tmp_path / "b.mp3", b"same-bytes")
    c = _mp3(tmp_path / "c.mp3", b"other-bytes")

    assert render.artifact_fingerprint(a) == render.artifact_fingerprint(b)
    assert render.artifact_fingerprint(a) != render.artifact_fingerprint(c)
    assert render.artifact_fingerprint(a) == hashlib.sha256(b"same-bytes").hexdigest()


def test_rejected_fingerprint_blocks_a_byte_identical_reupload(monkeypatch, tmp_path):
    """The 08-08 incident: the SAME mp3 was uploaded twice and rejected both
    times, each attempt permanently deleting an episode to free a cap slot."""
    _isolate_config(monkeypatch, tmp_path)
    mp3 = _mp3(tmp_path / "episode.mp3", b"cursed")
    render.record_rejection(
        mp3, episode_uri="spotify:episode:dead", profile={"channels": 1}, reason="processing FAILED"
    )

    assert render.artifact_fingerprint(mp3) in render.load_rejected_fingerprints()

    errors = render.verify_artifact(
        mp3,
        _timeline([0, 40_000]),
        duration_ms=80_000,
        profile={"codec_name": "mp3", "channels": 1, "sample_rate": 44100},
    )

    assert any("previously rejected" in e for e in errors)


def test_rejections_log_is_append_only(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    a = _mp3(tmp_path / "a.mp3", b"one")
    b = _mp3(tmp_path / "b.mp3", b"two")
    render.record_rejection(a, episode_uri="spotify:episode:1", profile={}, reason="x")
    render.record_rejection(b, episode_uri="spotify:episode:2", profile={}, reason="y")

    lines = render.REJECTIONS_PATH.read_text().strip().splitlines()

    assert len(lines) == 2
    assert {json.loads(line)["episode_uri"] for line in lines} == {
        "spotify:episode:1",
        "spotify:episode:2",
    }
    assert len(render.load_rejected_fingerprints()) == 2


def test_rejections_log_tolerates_corrupt_lines(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    render.REJECTIONS_PATH.write_text('{"sha256": "aaa"}\nnot-json\n{"sha256": "bbb"}\n')

    assert render.load_rejected_fingerprints() == {"aaa", "bbb"}


def test_verify_artifact_accepts_a_conformant_episode(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    mp3 = _mp3(tmp_path / "episode.mp3")

    errors = render.verify_artifact(
        mp3,
        _timeline([0, 40_000, 80_000]),
        duration_ms=120_000,
        profile={"codec_name": "mp3", "channels": 1, "sample_rate": 44100},
    )

    assert errors == []


def test_verify_artifact_rejects_wrong_channel_count(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    mp3 = _mp3(tmp_path / "episode.mp3")

    errors = render.verify_artifact(
        mp3,
        _timeline([0, 40_000]),
        duration_ms=80_000,
        profile={"codec_name": "mp3", "channels": 2, "sample_rate": 44100},
    )

    assert any("channels" in e for e in errors)


def test_verify_artifact_rejects_wrong_sample_rate(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    mp3 = _mp3(tmp_path / "episode.mp3")

    errors = render.verify_artifact(
        mp3,
        _timeline([0, 40_000]),
        duration_ms=80_000,
        profile={"codec_name": "mp3", "channels": 1, "sample_rate": 48000},
    )

    assert any("sample_rate" in e for e in errors)


def test_verify_artifact_rejects_last_chapter_at_or_past_duration(monkeypatch, tmp_path):
    """`last_chapter_start_ms >= episode_duration_ms` is fatal on Spotify."""
    _isolate_config(monkeypatch, tmp_path)
    mp3 = _mp3(tmp_path / "episode.mp3")

    errors = render.verify_artifact(
        mp3,
        _timeline([0, 40_000, 80_000]),
        duration_ms=80_000,
        profile={"codec_name": "mp3", "channels": 1, "sample_rate": 44100},
    )

    assert any("last chapter" in e for e in errors)


def test_verify_artifact_rejects_non_monotonic_chapters(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    mp3 = _mp3(tmp_path / "episode.mp3")

    errors = render.verify_artifact(
        mp3,
        _timeline([0, 80_000, 40_000]),
        duration_ms=120_000,
        profile={"codec_name": "mp3", "channels": 1, "sample_rate": 44100},
    )

    assert any("monotonic" in e for e in errors)


def test_verify_artifact_rejects_too_many_short_chapters(monkeypatch, tmp_path):
    """Spotify rejects timelines with more than MAX_SHORT_CHAPTERS sub-30s chapters."""
    _isolate_config(monkeypatch, tmp_path)
    mp3 = _mp3(tmp_path / "episode.mp3")
    starts = [0, 5_000, 10_000, 15_000, 20_000, 100_000]

    errors = render.verify_artifact(
        mp3,
        _timeline(starts),
        duration_ms=130_000,
        profile={"codec_name": "mp3", "channels": 1, "sample_rate": 44100},
    )

    assert any("short chapter" in e for e in errors)


# --- poll policy (failure mode 4) ------------------------------------------


def test_poll_timeout_defaults_to_1800s(monkeypatch, tmp_path):
    """600s expired while Spotify was legitimately still PROCESSING (07-28);
    the observed settle time was ~16 minutes."""
    assert render.resolve_poll_timeout({}) == 1800
    assert render.resolve_poll_timeout({"poll_timeout_s": 300}) == 300
    assert render.resolve_poll_timeout({"poll_timeout_s": "nope"}) == 1800


def test_episode_status_reads_readiness(monkeypatch):
    monkeypatch.setattr(
        render,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, '{"readiness":"READY"}', ""),
    )

    assert render.episode_status("abc") == "READY"


def test_episode_status_falls_back_to_the_show_listing(monkeypatch):
    """`episodes status` intermittently errors; the listing form is the documented
    fallback (`episodes --show-id <id>`)."""

    def fake_run(cmd, **kw):
        if "status" in cmd:
            raise SystemExit(1)
        return subprocess.CompletedProcess(
            cmd,
            0,
            json.dumps(
                {"episodes": [{"episode_uri": "spotify:episode:abc", "status": "PROCESSING"}]}
            ),
            "",
        )

    monkeypatch.setattr(render, "run", fake_run)

    assert render.episode_status("abc", show_id="spotify:show:x") == "PROCESSING"


def test_episode_missing_from_listing_is_transient_not_terminal(monkeypatch):
    """A listing that omits the just-uploaded episode must NOT be read as a
    phantom disappearance — it was present and PROCESSING on the next query."""

    def fake_run(cmd, **kw):
        if "status" in cmd:
            raise SystemExit(1)
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"episodes": []}), "")

    monkeypatch.setattr(render, "run", fake_run)

    assert render.episode_status("abc", show_id="spotify:show:x") is None


def test_poll_ready_returns_on_ready(monkeypatch):
    monkeypatch.setattr(render, "episode_status", lambda eid, show_id=None: "READY")

    assert render.poll_ready("abc", timeout_s=5) == "READY"


def test_poll_ready_keeps_waiting_through_processing(monkeypatch):
    seen = iter(["PROCESSING", None, "PROCESSING", "READY"])
    monkeypatch.setattr(render, "episode_status", lambda eid, show_id=None: next(seen))
    monkeypatch.setattr(render.time, "sleep", lambda s: None)

    assert render.poll_ready("abc", timeout_s=600) == "READY"


def test_poll_ready_dies_on_failed(monkeypatch):
    monkeypatch.setattr(render, "episode_status", lambda eid, show_id=None: "FAILED")

    with pytest.raises(SystemExit):
        render.poll_ready("abc", timeout_s=5)


# --- poison pill: in-flight give-up (failure mode 1) -----------------------


def test_recover_inflight_abandons_a_failed_episode_and_unblocks_the_run(monkeypatch, tmp_path):
    """The poison pill. A FAILED leftover used to kill EVERY later run before it
    rendered. Recovery now abandons the record and lets today's episode ship."""
    cfg = _isolate_config(monkeypatch, tmp_path)
    wd = tmp_path / "old-wd"
    wd.mkdir()
    (wd / "timeline.json").write_text(json.dumps(_timeline([0])))
    render.INFLIGHT_PATH.write_text(
        json.dumps(
            {
                "episode_uri": "spotify:episode:dead",
                "title": "T",
                "workdir": str(wd),
                "source_urls": ["https://example.com/a"],
            }
        )
    )
    monkeypatch.setattr(render, "set_timeline", lambda eid, tp: None)
    monkeypatch.setattr(render, "episode_status", lambda eid, show_id=None: "FAILED")
    monkeypatch.setattr(render.time, "sleep", lambda s: None)

    render._recover_inflight()  # must NOT raise

    assert not render.INFLIGHT_PATH.exists(), "poison pill must be cleared"
    covered = json.loads(render.COVERED_PATH.read_text()) if render.COVERED_PATH.exists() else {}
    assert "https://example.com/a" not in covered, "abandoned URLs must return to the pool"
    incidents = list((cfg / "incidents" / "new").glob("*.md"))
    assert incidents, "abandoning an episode must leave an incident report"


def test_recover_inflight_still_completes_a_healthy_episode(monkeypatch, tmp_path):
    """The give-up path must not weaken the normal cross-day recovery."""
    _isolate_config(monkeypatch, tmp_path)
    wd = tmp_path / "old-wd"
    wd.mkdir()
    (wd / "timeline.json").write_text(json.dumps(_timeline([0])))
    render.INFLIGHT_PATH.write_text(
        json.dumps(
            {
                "episode_uri": "spotify:episode:live",
                "title": "T",
                "workdir": str(wd),
                "source_urls": ["https://example.com/a"],
            }
        )
    )
    monkeypatch.setattr(render, "set_timeline", lambda eid, tp: None)
    monkeypatch.setattr(render, "episode_status", lambda eid, show_id=None: "READY")

    render._recover_inflight()

    assert not render.INFLIGHT_PATH.exists()
    covered = json.loads(render.COVERED_PATH.read_text())
    assert covered["https://example.com/a"]["episode_uri"] == "spotify:episode:live"


def test_abandoned_episode_fingerprint_is_recorded_when_artifact_survives(monkeypatch, tmp_path):
    """So a later run cannot re-upload the same cursed bytes."""
    _isolate_config(monkeypatch, tmp_path)
    wd = tmp_path / "old-wd"
    wd.mkdir()
    (wd / "timeline.json").write_text(json.dumps(_timeline([0])))
    mp3 = _mp3(wd / "episode.mp3", b"cursed-bytes")
    render.INFLIGHT_PATH.write_text(
        json.dumps(
            {
                "episode_uri": "spotify:episode:dead",
                "title": "T",
                "workdir": str(wd),
                "source_urls": [],
            }
        )
    )
    monkeypatch.setattr(render, "set_timeline", lambda eid, tp: None)
    monkeypatch.setattr(render, "episode_status", lambda eid, show_id=None: "FAILED")

    render._recover_inflight()

    assert render.artifact_fingerprint(mp3) in render.load_rejected_fingerprints()


# --- transient upload retry (failure mode 6) -------------------------------


def _cpe(stdout: str = "") -> subprocess.CalledProcessError:
    e = subprocess.CalledProcessError(1, ["save-to-spotify"])
    e.stdout = stdout
    e.stderr = ""
    return e


def test_upload_retries_once_on_a_transient_failure(monkeypatch, tmp_path):
    """06-07: the upload failed once with empty stderr and succeeded on an
    immediate re-run. That retry is now automatic."""
    attempts = []

    def fake_run(cmd, **kw):
        attempts.append(1)
        if len(attempts) == 1:
            raise _cpe("")
        return subprocess.CompletedProcess(cmd, 0, '{"episode_uri":"spotify:episode:ok"}', "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(render.time, "sleep", lambda s: None)

    uri = render.upload(
        tmp_path / "e.mp3", "T", "d", tmp_path / "c.jpg", "spotify:show:x", config={}
    )

    assert uri == "spotify:episode:ok"
    assert len(attempts) == 2


def test_upload_dies_after_the_single_transient_retry(monkeypatch, tmp_path):
    attempts = []

    def fake_run(cmd, **kw):
        attempts.append(1)
        raise _cpe("")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(render.time, "sleep", lambda s: None)

    with pytest.raises(SystemExit):
        render.upload(tmp_path / "e.mp3", "T", "d", tmp_path / "c.jpg", "spotify:show:x", config={})

    assert len(attempts) == 2, "exactly one retry — never a loop"


def test_cap_429_does_not_consume_the_transient_retry(monkeypatch, tmp_path):
    """A cap 429 keeps its prune-then-retry-once path; the two retry budgets
    must not compound into repeated destructive prunes."""
    cap_stdout = json.dumps(
        {
            "error": 'API error (429): {"error_code":"RATE_LIMIT_EXCEEDED","reason":"capacity",'
            '"message":"You\'ve reached the episode limit."}'
        }
    )
    attempts = []
    prunes = []

    def fake_run(cmd, **kw):
        attempts.append(1)
        raise _cpe(cap_stdout)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(render.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        render,
        "prune_episodes_for_capacity",
        lambda *a, **k: prunes.append(1) or 1,
    )

    with pytest.raises(SystemExit):
        render.upload(
            tmp_path / "e.mp3",
            "T",
            "d",
            tmp_path / "c.jpg",
            "spotify:show:x",
            config={"auto_prune_episodes": True},
        )

    assert len(prunes) == 1, "never a second prune"
    assert len(attempts) == 2, "cap path is attempt + one post-prune retry"


# --- incident capture (institutionalize) -----------------------------------


def test_incident_written_on_a_failed_run(monkeypatch, tmp_path):
    cfg = _isolate_config(monkeypatch, tmp_path)
    record = render._new_run_record()
    record["status"] = "failed"
    record["error_message"] = "episode processing FAILED"

    path = render.write_incident(
        record, kind="processing-failed", message="episode processing FAILED"
    )

    assert path is not None and path.exists()
    body = path.read_text()
    assert "episode processing FAILED" in body
    assert "processing-failed" in body
    sidecar = path.with_suffix(".json")
    assert json.loads(sidecar.read_text())["kind"] == "processing-failed"
    assert (cfg / "incidents" / "new") == path.parent


def test_incident_dir_is_env_overridable(monkeypatch, tmp_path):
    monkeypatch.setenv("DAILY_PODCAST_INCIDENT_DIR", str(tmp_path / "custom"))

    assert render.incident_dir() == tmp_path / "custom"


def test_incident_write_failure_never_sinks_the_run(monkeypatch, tmp_path):
    """Best-effort, exactly like write_run_log."""
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(
        render, "incident_dir", lambda: (_ for _ in ()).throw(OSError("read-only fs"))
    )

    assert render.write_incident(render._new_run_record(), kind="x", message="y") is None


def test_classify_incident_maps_known_signatures():
    assert render.classify_incident("episode processing FAILED") == "processing-failed"
    assert render.classify_incident("episode not READY after 1800s") == "poll-timeout"
    assert (
        render.classify_incident('API error (429): {"error_code":"RATE_LIMIT_EXCEEDED"}')
        == "episode-cap"
    )
    assert render.classify_incident("something nobody has seen") == "unclassified"


def test_main_writes_an_incident_on_non_clean_exit(monkeypatch, tmp_path):
    cfg = _isolate_config(monkeypatch, tmp_path)
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"title": "T", "summary": "s", "segments": []}))
    monkeypatch.setattr(sys, "argv", ["render.py", "--manifest", str(manifest)])

    with pytest.raises(SystemExit):
        render.main()

    assert list((cfg / "incidents" / "new").glob("*.md")), "a failed run must leave an incident"


def test_main_writes_no_incident_on_success(monkeypatch, tmp_path):
    cfg = _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(render, "_render", lambda a, r: 0)
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"title": "T", "summary": "s", "segments": []}))
    monkeypatch.setattr(sys, "argv", ["render.py", "--manifest", str(manifest)])

    assert render.main() == 0
    assert not list((cfg / "incidents" / "new").glob("*.md"))


# --- blocked-source registry (failure mode 3) ------------------------------


def test_blocked_sources_registry_ships_in_the_skill():
    blocked = render.load_blocked_sources()

    assert "wired.com" in blocked
    assert "arstechnica.com" in blocked
    for domain, entry in blocked.items():
        assert entry.get("reason"), f"{domain} needs a reason"


def test_is_blocked_domain_matches_subdomains_not_substrings():
    blocked = {"wired.com": {"reason": "webfetch-blocked"}}

    assert render.is_blocked_domain("https://www.wired.com/story/x", blocked) == "wired.com"
    assert render.is_blocked_domain("https://wired.com/x", blocked) == "wired.com"
    assert render.is_blocked_domain("https://notwired.com/x", blocked) is None
    assert render.is_blocked_domain("https://example.com/wired.com", blocked) is None
    assert render.is_blocked_domain("not a url", blocked) is None


# --- dry-run rehearses the real gates --------------------------------------


def test_dry_run_exercises_the_artifact_gate(monkeypatch, tmp_path):
    """A dry run must rehearse every LOCAL gate the real run applies, or it is not
    a rehearsal. The artifact gate is ffprobe + a hash — no Spotify, no network —
    so it runs before the dry-run report, not after the upload it guards."""
    _isolate_config(monkeypatch, tmp_path)
    called = {}

    def fake_verify(mp3, timeline, *, duration_ms, profile):
        called["ran"] = True
        return ["synthetic gate failure"]

    monkeypatch.setattr(render, "verify_artifact", fake_verify)
    monkeypatch.setattr(render, "probe_audio_profile", lambda p: {})
    monkeypatch.setattr(render, "preflight", lambda *a, **k: (True, []))
    monkeypatch.setattr(render, "load_config", lambda: {"show_id": "spotify:show:1"})
    monkeypatch.setattr(render, "render_segments", lambda *a, **k: [tmp_path / "seg_01.mp3"])
    monkeypatch.setattr(render, "plan_silences", lambda paths: [0])
    monkeypatch.setattr(
        render, "concat_and_normalize", lambda *a, **k: (tmp_path / "episode.mp3", None)
    )
    monkeypatch.setattr(render, "build_cover", lambda *a, **k: None)
    monkeypatch.setattr(
        render,
        "build_timeline_and_description",
        lambda *a, **k: ({"items": [{"chapter": {"title": "A", "start_ms": 0}}]}, "<p>d</p>"),
    )
    monkeypatch.setattr(render, "mp3_duration_ms", lambda p: 60_000)
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({"title": "T", "summary": "s", "segments": [{"text": "hi", "source_url": None}]})
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["render.py", "--manifest", str(manifest), "--dry-run", "--workdir", str(tmp_path / "wd")],
    )

    with pytest.raises(SystemExit) as e:
        render.main()

    assert called.get("ran") is True
    assert e.value.code != 0


def test_preflight_gates_on_the_tts_module_being_importable(monkeypatch):
    """A dry run still renders audio, so a missing TTS backend must fail the gate
    rather than surfacing at the model load minutes later."""
    monkeypatch.setattr(render.importlib.util, "find_spec", lambda name: None)
    assert render._tts_module_check()["ok"] is False

    monkeypatch.setattr(render.importlib.util, "find_spec", lambda name: object())
    assert render._tts_module_check()["ok"] is True


def test_tts_module_check_survives_a_broken_import_system(monkeypatch):
    def boom(name):
        raise ValueError("no parent package")

    monkeypatch.setattr(render.importlib.util, "find_spec", boom)

    assert render._tts_module_check()["ok"] is False


def test_every_incident_signature_has_a_playbook_file():
    """A generated report tells the reader to "see incidents/<kind>.md". That
    pointer must resolve, or the post-run hook is sending people to a 404."""
    repo_root = Path(render.__file__).resolve().parent.parent.parent
    incidents = repo_root / "incidents"
    assert incidents.is_dir(), f"missing {incidents}"

    for _needle, slug in render._INCIDENT_SIGNATURES:
        assert (incidents / f"{slug}.md").exists(), f"incidents/{slug}.md is referenced but missing"


def test_incident_playbooks_have_the_four_required_sections():
    repo_root = Path(render.__file__).resolve().parent.parent.parent
    required = ("## Symptom", "## Root cause", "## Test that guards it")
    for path in sorted((repo_root / "incidents").glob("*.md")):
        if path.name == "README.md":
            continue
        body = path.read_text()
        for heading in required:
            assert heading in body, f"{path.name} is missing '{heading}'"
