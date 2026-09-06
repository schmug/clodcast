"""Tests for the web-only ship mode (issue #155).

Frontier Commits is RSS-first: its canonical channel is the public feed on
cortech.online, and its save-to-spotify show is deprecated. `"ship_mode": "web"`
makes the R2 publish *the ship* — render -> artifact gate -> R2 -> covered.json
-> exit — with save-to-spotify never invoked at all.

Two properties carry the weight here:

  * the daily show's default path is untouched (the `_default_*` tests are the
    lock, not the decoration), and
  * covered.json keeps its only-after-success posture — a failed R2 publish must
    leave those URLs in the pool for the next run, exactly as a failed Spotify
    upload does.

The runner seam (`render.run`) is stubbed to explode on any save-to-spotify
invocation, so "never talks to Spotify" is asserted mechanically rather than
inferred from the absence of a mock.
"""

from __future__ import annotations

import io
import json
import sys

import pytest
from botocore.exceptions import ClientError

import render

# --- fakes -----------------------------------------------------------------


class FakeS3:
    """Same shape as test_r2.py's fake: records PUT order, can fail one suffix."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.put_order: list[str] = []
        self.fail_suffix: str | None = None

    def put_object(self, Bucket, Key, Body, ContentType=None, CacheControl=None):
        if self.fail_suffix and Key.endswith(self.fail_suffix):
            raise RuntimeError(f"simulated PUT failure on {Key}")
        self.objects[Key] = Body if isinstance(Body, bytes) else Body.read()
        self.put_order.append(Key)
        return {}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "x"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}


# --- ship_mode manifest key -------------------------------------------------
#
# Mode lives on the MANIFEST, not on the command line: the show's distribution
# channel is a property of the show, and a re-run of the same manifest must ship
# the same way. A flag could go missing on one invocation and silently upload a
# deprecated show's episode to Spotify.


def test_ship_mode_defaults_to_spotify_when_the_key_is_absent():
    assert render.resolve_ship_mode({}) == render.SHIP_MODE_SPOTIFY
    assert render.is_web_only({}) is False


def test_ship_mode_web_selects_the_web_only_path():
    assert render.resolve_ship_mode({"ship_mode": "web"}) == render.SHIP_MODE_WEB
    assert render.is_web_only({"ship_mode": "web"}) is True


@pytest.mark.parametrize("bad", ["webb", "WEB", "", 3, [], "spotify "])
def test_validate_manifest_rejects_an_unknown_ship_mode(bad):
    manifest = {
        "title": "T",
        "summary": "S",
        "ship_mode": bad,
        "segments": [{"text": "hi"}],
    }
    with pytest.raises(SystemExit):
        render.validate_manifest(manifest)


def test_validate_manifest_accepts_both_documented_ship_modes():
    for mode in ("spotify", "web"):
        render.validate_manifest(
            {"title": "T", "summary": "S", "ship_mode": mode, "segments": [{"text": "hi"}]}
        )


# --- pre-flight -------------------------------------------------------------


def _no_r2(monkeypatch, tmp_path):
    """No R2 anywhere: env cleared and CONFIG_DIR pointed at an empty dir."""
    for k in (
        "R2_ACCOUNT_ID",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET",
        "R2_PUBLIC_BASE_URL",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)


def _with_r2(monkeypatch, tmp_path):
    _no_r2(monkeypatch, tmp_path)
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("R2_BUCKET", "clodcast")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://audio.example")


def _explode_on_save_to_spotify(monkeypatch):
    """The runner seam, wired to fail loudly. Any save-to-spotify shell-out on
    the web-only path is a bug, not a degraded mode."""

    def _boom(cmd, **kw):
        raise AssertionError(f"web-only run invoked save-to-spotify: {cmd}")

    monkeypatch.setattr(render, "run", _boom)
    monkeypatch.setattr(
        render, "_spotify_auth_check", lambda: pytest.fail("web-only run probed Spotify auth")
    )
    monkeypatch.setattr(
        render, "_list_episodes", lambda show_id: pytest.fail("web-only run listed episodes")
    )


def _local_checks_pass(monkeypatch):
    """Make the host-dependent local pre-flight checks deterministic."""
    monkeypatch.setattr(render.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(
        render, "_tts_module_check", lambda: render._check("tts-module", True, "stubbed")
    )


def test_web_only_preflight_drops_the_show_id_and_spotify_checks(monkeypatch, tmp_path):
    _local_checks_pass(monkeypatch)
    _explode_on_save_to_spotify(monkeypatch)
    _with_r2(monkeypatch, tmp_path)

    ok, checks = render.preflight({}, show_id=None, dry_run=False, web_only=True)

    names = [c["name"] for c in checks]
    assert ok, checks
    assert "show-id" not in names
    assert "save-to-spotify-auth" not in names
    assert "episode-capacity" not in names
    assert "r2-credentials" in names


def test_web_only_preflight_fails_when_r2_is_absent(monkeypatch, tmp_path):
    # Absent R2 is a PASS on the default path (the web feed is optional there).
    # Here publishing IS the ship, so absent must fail — otherwise a misconfigured
    # host renders a full episode and ships it precisely nowhere.
    _local_checks_pass(monkeypatch)
    _explode_on_save_to_spotify(monkeypatch)
    _no_r2(monkeypatch, tmp_path)

    ok, checks = render.preflight({}, show_id=None, dry_run=False, web_only=True)

    r2 = next(c for c in checks if c["name"] == "r2-credentials")
    assert not ok
    assert not r2["ok"]
    assert "web-only" in r2["detail"]


def test_default_preflight_still_treats_absent_r2_as_a_pass(monkeypatch, tmp_path):
    # The lock on the daily show: no R2 configured is still a clean run.
    _local_checks_pass(monkeypatch)
    _no_r2(monkeypatch, tmp_path)
    assert render.check_r2_credentials({})["ok"] is True
    assert render.check_r2_credentials({})["state"] == "absent"

    ok, checks = render.preflight({}, show_id="spotify:show:1", dry_run=True)

    assert ok, checks
    assert [c["name"] for c in checks if c["name"] == "show-id"] == ["show-id"]


def test_partial_r2_still_fails_in_web_only_mode(monkeypatch, tmp_path):
    _no_r2(monkeypatch, tmp_path)
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    result = render.check_r2_credentials({}, required=True)
    assert not result["ok"]
    assert result["state"] == "partial"


# --- the web-only ship ------------------------------------------------------
#
# End-to-end through main(): the heavy seams (TTS, ffmpeg, ffprobe) are stubbed,
# but maybe_publish_r2 runs for real against FakeS3 — the publish IS the ship in
# this mode, so stubbing it would leave the interesting half untested.

WEB_MANIFEST = {
    "title": "Frontier Commits — Week of August 24, 2026",
    "summary": "This week's hook.",
    "date": "2026-08-24",
    "ship_mode": "web",
    "r2_manifest_name": "manifest-frontier-commits.json",
    "r2_key_prefix": "frontier-commits/",
    "segments": [
        {"text": "Cold open", "source_url": None, "title": "Cold open"},
        {"text": "Lead story", "source_url": "https://github.com/openai/git"},
    ],
}

SPOTIFY_MANIFEST = {
    "title": "Daily Digest",
    "summary": "Today's hook.",
    "date": "2026-08-24",
    "show_id": "spotify:show:1",
    "segments": [{"text": "Story", "source_url": "https://example.com/a"}],
}


def _write_manifest(tmp_path, manifest) -> str:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return str(path)


def _stub_render_seams(monkeypatch, tmp_path):
    """Stub the heavy render seams so main() exercises orchestration only."""
    mp3 = tmp_path / "episode.mp3"
    mp3.write_bytes(b"AUDIODATA")
    cover_src = tmp_path / "cover-src.jpg"
    cover_src.write_bytes(b"IMG")
    monkeypatch.setattr(render, "load_config", lambda: {})
    monkeypatch.setattr(render, "verify_artifact", lambda *a, **k: [])
    monkeypatch.setattr(render, "probe_audio_profile", lambda p: {})
    monkeypatch.setattr(render, "render_segments", lambda *a, **k: [tmp_path / "seg_01.mp3"])
    monkeypatch.setattr(render, "plan_silences", lambda paths: [0])
    monkeypatch.setattr(render, "concat_and_normalize", lambda *a, **k: (mp3, None))
    monkeypatch.setattr(render, "build_cover", lambda out, *a, **k: out.write_bytes(b"IMG"))
    monkeypatch.setattr(
        render,
        "build_timeline_and_description",
        lambda *a, **k: (
            {
                "items": [
                    {"chapter": {"title": "Cold open", "start_time_ms": 0}},
                    {"chapter": {"title": "Lead story", "start_time_ms": 30_000}},
                    {"link": {"url": "https://github.com/openai/git", "start_time_ms": 30_000}},
                ]
            },
            "<p>d</p>",
        ),
    )
    monkeypatch.setattr(render, "mp3_duration_ms", lambda p: 480_000)
    return mp3


def _drive(monkeypatch, tmp_path, manifest, *, argv_extra=(), s3=None, preflight_ok=True):
    """Run main() over `manifest` with every external seam faked. Returns the
    FakeS3 the publish wrote through."""
    s3 = s3 if s3 is not None else FakeS3()
    _with_r2(monkeypatch, tmp_path)
    _explode_on_save_to_spotify(monkeypatch)
    _stub_render_seams(monkeypatch, tmp_path)
    monkeypatch.setattr(render, "r2_client", lambda cfg: s3)
    monkeypatch.setattr(render, "fire_pages_hook", lambda url: None)
    monkeypatch.setattr(render, "resolve_pages_hook_url", lambda cfg: None)
    monkeypatch.setattr(render, "preflight", lambda *a, **k: (preflight_ok, []))
    for seam in ("upload", "set_timeline", "poll_ready", "_recover_inflight"):
        monkeypatch.setattr(
            render, seam, lambda *a, _s=seam, **k: pytest.fail(f"web-only run called {_s}()")
        )
    workdir = tmp_path / "wd"
    argv = [
        "render.py",
        "--manifest",
        _write_manifest(tmp_path, manifest),
        "--workdir",
        str(workdir),
        *argv_extra,
    ]
    monkeypatch.setattr(sys, "argv", argv)
    return s3, workdir


def test_web_only_run_publishes_mp3_cover_and_manifest_without_touching_spotify(
    monkeypatch, tmp_path, capsys
):
    s3, _ = _drive(monkeypatch, tmp_path, WEB_MANIFEST)

    assert render.main() == 0

    # #142's key prefix namespaces the episode objects; #118's manifest name keeps
    # the web feed out of the daily show's manifest.json. Both exercised here.
    assert s3.put_order == [
        "frontier-commits/daily-digest-august-24-2026.mp3",
        "frontier-commits/daily-digest-august-24-2026.jpg",
        "manifest-frontier-commits.json",
    ]
    assert "manifest.json" not in s3.objects
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "web-ready"
    assert out["r2_status"] == render.R2_PUBLISHED


def test_web_only_final_json_carries_the_mp3_url_for_the_shipped_line(
    monkeypatch, tmp_path, capsys
):
    # SKILL.md's report line is SHIPPED <mp3_url> - ... - r2=ok, so the URL has to
    # come off render.py's own output rather than being reassembled by the caller.
    _drive(monkeypatch, tmp_path, WEB_MANIFEST)

    assert render.main() == 0

    out = json.loads(capsys.readouterr().out)
    assert (
        out["mp3_url"] == "https://audio.example/frontier-commits/daily-digest-august-24-2026.mp3"
    )
    assert out["chapter_count"] == 2
    assert out["duration_s"] == 480.0
    assert out["title"] == WEB_MANIFEST["title"]
    assert out["tts_engine"] == "qwen3"  # absent key -> default engine, reported truthfully


def test_web_only_manifest_entry_omits_the_spotify_uri(monkeypatch, tmp_path):
    s3, _ = _drive(monkeypatch, tmp_path, WEB_MANIFEST)

    assert render.main() == 0

    entries = json.loads(s3.objects["manifest-frontier-commits.json"])
    assert len(entries) == 1
    assert "spotify_uri" not in entries[0]
    assert entries[0]["slug"] == "daily-digest-august-24-2026"
    assert entries[0]["chapters"][1]["source_url"] == "https://github.com/openai/git"


def test_web_only_run_writes_covered_json_after_a_successful_publish(monkeypatch, tmp_path):
    _drive(monkeypatch, tmp_path, WEB_MANIFEST)

    assert render.main() == 0

    covered = json.loads(render.COVERED_PATH.read_text())
    assert list(covered) == ["https://github.com/openai/git"]
    # The published mp3 URL stands in for the episode identity — there is no
    # Spotify episode in this mode, and a bare null would lose the trail.
    assert covered["https://github.com/openai/git"]["episode_uri"] == (
        "https://audio.example/frontier-commits/daily-digest-august-24-2026.mp3"
    )


def test_a_failed_publish_leaves_covered_json_untouched_and_exits_nonzero(monkeypatch, tmp_path):
    # The only-after-success posture, ported: a failed publish must leave those
    # URLs in the pool so the next run re-selects them.
    s3 = FakeS3()
    s3.fail_suffix = ".mp3"
    _drive(monkeypatch, tmp_path, WEB_MANIFEST, s3=s3)

    with pytest.raises(SystemExit) as excinfo:
        render.main()

    assert excinfo.value.code != 0
    assert not render.COVERED_PATH.exists()
    record = json.loads(render.RUN_LOG_PATH.read_text().splitlines()[-1])
    assert record["status"] == "failed"
    assert record["r2_status"] == render.R2_FAILED


def test_a_failed_publish_does_not_claim_a_spotify_episode_is_live(monkeypatch, tmp_path, capsys):
    # The publisher's failure log used to reassure the reader that "the Spotify
    # episode is live" — true on the additive path, a flat lie here, and read at
    # exactly the moment an operator is deciding whether to panic.
    s3 = FakeS3()
    s3.fail_suffix = ".mp3"
    _drive(monkeypatch, tmp_path, WEB_MANIFEST, s3=s3)

    with pytest.raises(SystemExit):
        render.main()

    err = capsys.readouterr().err
    assert "[r2] publish failed" in err
    assert "Spotify episode is live" not in err


def test_web_only_run_log_record_uses_the_web_ready_status_and_keeps_every_field(
    monkeypatch, tmp_path
):
    _drive(monkeypatch, tmp_path, WEB_MANIFEST)

    assert render.main() == 0

    record = json.loads(render.RUN_LOG_PATH.read_text().splitlines()[-1])
    assert record["status"] == "web-ready"
    assert set(record) == set(render.RUN_LOG_FIELDS)
    assert record["mp3_url"] == (
        "https://audio.example/frontier-commits/daily-digest-august-24-2026.mp3"
    )
    assert record["r2_status"] == render.R2_PUBLISHED
    assert record["episode_uri"] is None  # there is no Spotify episode in this mode
    assert record["chapter_count"] == 2
    assert record["resumed"] is False


def test_web_only_run_writes_no_upload_marker_and_no_inflight_record(monkeypatch, tmp_path):
    # Both markers are Spotify-upload recovery state. Writing either would arm the
    # resume path, which set_timeline/poll_ready — i.e. save-to-spotify.
    _, workdir = _drive(monkeypatch, tmp_path, WEB_MANIFEST)

    assert render.main() == 0

    assert not (workdir / "uploaded.json").exists()
    assert not render.INFLIGHT_PATH.exists()


def test_web_only_run_does_not_take_the_spotify_resume_path(monkeypatch, tmp_path):
    # A workdir left over from a Spotify-mode run must not send a web-only manifest
    # into _resume(), which unconditionally calls set_timeline + poll_ready.
    s3, workdir = _drive(monkeypatch, tmp_path, WEB_MANIFEST)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "uploaded.json").write_text(json.dumps({"episode_uri": "spotify:episode:stale"}))
    monkeypatch.setattr(render, "_resume", lambda *a, **k: pytest.fail("web-only run resumed"))

    assert render.main() == 0

    assert any(k.endswith(".mp3") for k in s3.put_order)


def test_web_only_run_still_gates_on_the_artifact_check_before_publishing(monkeypatch, tmp_path):
    s3, _ = _drive(monkeypatch, tmp_path, WEB_MANIFEST)
    monkeypatch.setattr(render, "verify_artifact", lambda *a, **k: ["chapter gap too small"])

    with pytest.raises(SystemExit) as excinfo:
        render.main()

    assert excinfo.value.code != 0
    assert s3.put_order == []  # nothing reached the bucket


def test_web_only_dry_run_publishes_nothing(monkeypatch, tmp_path, capsys):
    s3, _ = _drive(monkeypatch, tmp_path, WEB_MANIFEST, argv_extra=["--dry-run"])

    assert render.main() == 0

    assert s3.put_order == []
    assert not render.COVERED_PATH.exists()
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "dry-run"
    assert out["r2_would_publish"] == (
        "https://audio.example/frontier-commits/daily-digest-august-24-2026.mp3"
    )


def test_web_only_passes_the_mode_through_to_preflight(monkeypatch, tmp_path):
    seen = {}

    def _record_preflight(config, **kw):
        seen.update(kw)
        return True, []

    _drive(monkeypatch, tmp_path, WEB_MANIFEST)
    monkeypatch.setattr(render, "preflight", _record_preflight)

    assert render.main() == 0

    assert seen["web_only"] is True
    assert seen["show_id"] is None


def test_web_only_without_r2_config_fails_even_when_preflight_is_skipped(monkeypatch, tmp_path):
    # --skip-preflight is the documented escape hatch, but it must not turn a
    # web-only run into a render that ships nowhere and reports success.
    s3, _ = _drive(monkeypatch, tmp_path, WEB_MANIFEST, argv_extra=["--skip-preflight"])
    _no_r2(monkeypatch, tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        render.main()

    assert excinfo.value.code != 0
    assert s3.put_order == []
    assert not render.COVERED_PATH.exists()


def test_web_only_run_fires_the_pages_deploy_hook(monkeypatch, tmp_path):
    # cortech.online is a static build that reads the manifest at build time;
    # without the hook the episode is in the bucket but not on the site.
    fired = []
    _drive(monkeypatch, tmp_path, WEB_MANIFEST)
    monkeypatch.setattr(render, "resolve_pages_hook_url", lambda cfg: "https://hook.test/deploy")
    monkeypatch.setattr(render, "fire_pages_hook", lambda url: fired.append(url))

    assert render.main() == 0

    assert fired == ["https://hook.test/deploy"]


def test_a_default_manifest_still_ships_through_spotify(monkeypatch, tmp_path, capsys):
    """The lock on the daily show: no ship_mode key means upload + timeline + poll,
    R2 stays additive, and the run-log status is still `ready`."""
    calls = []
    _with_r2(monkeypatch, tmp_path)
    _stub_render_seams(monkeypatch, tmp_path)
    monkeypatch.setattr(render, "load_config", lambda: {"show_id": "spotify:show:1"})
    monkeypatch.setattr(render, "preflight", lambda *a, **k: (True, []))
    monkeypatch.setattr(render, "_recover_inflight", lambda: None)
    monkeypatch.setattr(render, "r2_client", lambda cfg: FakeS3())
    monkeypatch.setattr(render, "resolve_pages_hook_url", lambda cfg: None)
    monkeypatch.setattr(
        render, "upload", lambda *a, **k: calls.append("upload") or "spotify:episode:xyz"
    )
    monkeypatch.setattr(render, "set_timeline", lambda *a, **k: calls.append("set_timeline"))
    monkeypatch.setattr(render, "poll_ready", lambda *a, **k: calls.append("poll_ready"))
    workdir = tmp_path / "wd"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "render.py",
            "--manifest",
            _write_manifest(tmp_path, SPOTIFY_MANIFEST),
            "--workdir",
            str(workdir),
        ],
    )

    assert render.main() == 0

    assert calls == ["upload", "set_timeline", "poll_ready"]
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ready"
    assert out["episode_uri"] == "spotify:episode:xyz"
    assert "mp3_url" not in out  # the web-only field never leaks into the daily shape
    assert (workdir / "uploaded.json").exists()
    record = json.loads(render.RUN_LOG_PATH.read_text().splitlines()[-1])
    assert record["status"] == "ready"
    assert record["mp3_url"] is None
