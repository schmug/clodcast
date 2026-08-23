"""
Tests for the R2 publish path in render.py (issue #33).

Covers the pure logic (slug, chapter reconstruction, manifest entry shape,
upsert/cap/order, config resolution) and the publish orchestration against a
fake S3 client — no network, no boto3 round-trip, no save-to-spotify. The audio
seam (`mp3_duration_ms`) is monkeypatched, as elsewhere in the suite.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import inspect
import io
import json
import locale
import re
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

import render


@contextlib.contextmanager
def _lc_time(name: str):
    """Run a block under a different LC_TIME, skipping where that locale isn't built."""
    previous = locale.setlocale(locale.LC_TIME)
    try:
        locale.setlocale(locale.LC_TIME, name)
    except locale.Error:
        pytest.skip(f"locale {name} not available on this host")
    try:
        yield
    finally:
        locale.setlocale(locale.LC_TIME, previous)


# --- fake S3 ---------------------------------------------------------------


class FakeS3:
    """Minimal stand-in for a boto3 S3 client against R2. Records PUT order so
    tests can assert the mp3 lands before the manifest that references it."""

    def __init__(self, manifest: list | None = None):
        self.objects: dict[str, bytes] = {}
        if manifest is not None:
            self.objects["manifest.json"] = json.dumps(manifest).encode()
        self.put_order: list[str] = []
        self.fail_suffix: str | None = None  # raise on PUT of a key ending with this

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


# --- slug_for_date ---------------------------------------------------------
#
# The slug keys the R2 object AND the /podcast/<slug>/ permalink that cortech.online
# publishes as an isPermaLink <guid>. Spotify treats a changed guid as a brand-new
# episode, so these identifiers are immutable in practice. #128 decoupled the slug
# from the title so titles became free display text; these tests are the contract
# that made that safe.

PUBLISHED_SLUGS = [
    tuple(line.split("\t"))
    for line in (Path(__file__).parent / "data" / "published_slugs.tsv").read_text().splitlines()
    if line and not line.startswith("#")
]


def test_published_slug_fixture_is_the_whole_live_feed():
    """Guards the fixture itself: a truncated capture would make the compatibility
    test below pass vacuously for the dates it silently dropped."""
    assert len(PUBLISHED_SLUGS) == 75


@pytest.mark.parametrize("date,published", PUBLISHED_SLUGS)
def test_slug_for_date_reproduces_every_published_slug(date, published):
    """Every slug already live on cortech.online must survive byte-for-byte. Any
    change here orphans a published episode and duplicates it on Spotify."""
    assert render.slug_for_date(date) == published


def test_slug_for_date_ignores_the_title():
    """The property this decoupling exists to establish: slug_for_date takes a date
    and nothing else, so no title can reach it."""
    assert render.slug_for_date("2026-05-22") == "daily-digest-may-22-2026"
    assert "title" not in inspect.signature(render.slug_for_date).parameters


def test_slug_for_date_does_not_zero_pad_the_day():
    """The historical titles used %-d, so single-digit days have no leading zero."""
    assert render.slug_for_date("2026-06-01") == "daily-digest-june-1-2026"


def test_slug_for_date_month_names_survive_a_non_english_locale():
    """Month names are spelled out rather than taken from strftime("%B"), which is
    LC_TIME-dependent. A run on a non-English box must not mint a new permalink."""
    with _lc_time("de_DE.UTF-8"):
        assert render.slug_for_date("2026-08-23") == "daily-digest-august-23-2026"


def test_slug_for_date_matches_consumer_regex_and_cap():
    for date in ("2026-09-30", "2026-12-25", "not-a-date", ""):
        slug = render.slug_for_date(date)
        assert re.fullmatch(r"[a-z0-9-]+", slug), date  # slug: z.string().regex(^[a-z0-9-]+$)
        assert len(slug) <= 80, date


def test_slug_for_date_falls_back_on_an_unparseable_date():
    """validate_manifest never checked `date`, so a malformed one must still yield a
    deterministic schema-valid slug instead of crashing the publish."""
    assert render.slug_for_date("22 May 2026") == "episode-22-may-2026"
    assert render.slug_for_date(None) == "episode-none"


def test_resolve_slug_date_prefers_an_explicit_manifest_date():
    """Mirrors resolve_pubdate: a back-fill or archive re-render reproduces its
    historical slug rather than stamping the day it happened to be re-rendered."""
    assert render.resolve_slug_date({"date": "2026-06-01"}) == "2026-06-01"
    assert render.resolve_slug_date({}) == dt.date.today().isoformat()


# --- chapters_from_timeline ------------------------------------------------


def test_chapters_from_timeline_pairs_links_to_chapters():
    timeline = {
        "items": [
            {"chapter": {"title": "Intro", "start_time_ms": 0}},
            {"chapter": {"title": "Story", "start_time_ms": 30000}},
            {"link": {"start_time_ms": 31000, "duration_ms": 4000, "url": "https://x.test/a"}},
            {"chapter": {"title": "Outro", "start_time_ms": 70000}},
        ]
    }
    assert render.chapters_from_timeline(timeline) == [
        {"title": "Intro", "start_ms": 0, "source_url": None},
        {"title": "Story", "start_ms": 30000, "source_url": "https://x.test/a"},
        {"title": "Outro", "start_ms": 70000, "source_url": None},
    ]


def test_chapters_from_timeline_empty():
    assert render.chapters_from_timeline({"items": []}) == []
    assert render.chapters_from_timeline({}) == []


# --- build_manifest_entry --------------------------------------------------


def test_build_manifest_entry_shape():
    entry = render.build_manifest_entry(
        slug="daily-x",
        title="Daily X",
        description="<p>hi</p>",
        summary="the clean hook",
        pubdate="2026-06-01T12:00:00+00:00",
        mp3_url="https://audio.test/daily-x.mp3",
        mp3_bytes=12345,
        duration_s=123.456789,
        chapters=[{"title": "Intro", "start_ms": 0, "source_url": None}],
        spotify_uri="spotify:episode:abc",
        cover_url="https://audio.test/daily-x.jpg",
    )
    assert entry["slug"] == "daily-x"
    assert entry["mp3_bytes"] == 12345 and isinstance(entry["mp3_bytes"], int)
    assert entry["duration_s"] == 123.457  # rounded to 3 dp
    assert entry["spotify_uri"] == "spotify:episode:abc"
    assert entry["cover_url"] == "https://audio.test/daily-x.jpg"
    assert entry["explicit"] is False
    assert entry["chapters"][0]["start_ms"] == 0


def test_build_manifest_entry_includes_summary():
    """#45: the entry carries the clean plain-text summary, distinct from the HTML
    description, and the addition does not perturb description or chapters[]."""
    entry = render.build_manifest_entry(
        slug="daily-x",
        title="Daily X",
        description="<p>the clean hook</p><p>(0:00) - Intro</p>",
        summary="the clean hook",
        pubdate="2026-06-01T12:00:00+00:00",
        mp3_url="https://audio.test/daily-x.mp3",
        mp3_bytes=12345,
        duration_s=123.0,
        chapters=[{"title": "Intro", "start_ms": 0, "source_url": None}],
    )
    assert entry["summary"] == "the clean hook"  # reflects manifest["summary"]
    # description (HTML) and chapters[] are byte-for-byte the caller's input.
    assert entry["description"] == "<p>the clean hook</p><p>(0:00) - Intro</p>"
    assert entry["chapters"] == [{"title": "Intro", "start_ms": 0, "source_url": None}]


def test_build_manifest_entry_omits_empty_optionals():
    entry = render.build_manifest_entry(
        slug="s",
        title="t",
        description="d",
        summary="sum",
        pubdate="2026-06-01T12:00:00+00:00",
        mp3_url="https://a.test/s.mp3",
        mp3_bytes=1,
        duration_s=1.0,
        chapters=[],
        spotify_uri=None,
        cover_url=None,
    )
    assert "spotify_uri" not in entry
    assert "cover_url" not in entry


# --- upsert_manifest -------------------------------------------------------


def _entry(slug: str, pubdate: str) -> dict:
    return {"slug": slug, "pubDate": pubdate, "title": slug}


def test_upsert_prepends_and_sorts_newest_first():
    existing = [_entry("old", "2026-05-01T12:00:00+00:00")]
    out = render.upsert_manifest(existing, _entry("new", "2026-06-01T12:00:00+00:00"))
    assert [e["slug"] for e in out] == ["new", "old"]


def test_upsert_replaces_same_slug():
    existing = [_entry("dup", "2026-05-01T12:00:00+00:00")]
    out = render.upsert_manifest(existing, _entry("dup", "2026-06-01T12:00:00+00:00"))
    assert len(out) == 1
    assert out[0]["pubDate"] == "2026-06-01T12:00:00+00:00"


def test_upsert_caps_to_most_recent():
    existing = [_entry(f"e{i:03d}", f"2026-01-01T00:00:{i % 60:02d}+00:00") for i in range(250)]
    out = render.upsert_manifest(existing, _entry("newest", "2027-01-01T00:00:00+00:00"), cap=200)
    assert len(out) == 200
    assert out[0]["slug"] == "newest"


def test_upsert_tolerates_bad_pubdate():
    existing = [_entry("bad", "not-a-date"), {"junk": True}]
    out = render.upsert_manifest(existing, _entry("good", "2026-06-01T12:00:00+00:00"))
    assert out[0]["slug"] == "good"  # valid date sorts above the unparseable one


# --- load_r2_config --------------------------------------------------------


def _clear_r2_env(monkeypatch):
    for k in (
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_ACCOUNT_ID",
        "R2_BUCKET",
        "R2_PUBLIC_BASE_URL",
    ):
        monkeypatch.delenv(k, raising=False)


def test_load_r2_config_none_when_unset(monkeypatch, tmp_path):
    _clear_r2_env(monkeypatch)
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)  # no secrets.json here
    assert render.load_r2_config({}) is None


def test_load_r2_config_from_env(monkeypatch, tmp_path):
    _clear_r2_env(monkeypatch)
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    cfg = render.load_r2_config(
        {
            "r2_bucket": "clodcast",
            "r2_public_base_url": "https://audio.cortech.online",
        }
    )
    assert cfg == {
        "account_id": "acct",
        "access_key": "ak",
        "secret_key": "sk",
        "bucket": "clodcast",
        "public_base_url": "https://audio.cortech.online",
    }


def test_load_r2_config_missing_bucket_returns_none(monkeypatch, tmp_path):
    _clear_r2_env(monkeypatch)
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    assert render.load_r2_config({"r2_public_base_url": "https://a.test"}) is None


def test_load_r2_config_secrets_file_fallback(monkeypatch, tmp_path):
    _clear_r2_env(monkeypatch)
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    (tmp_path / "secrets.json").write_text(
        json.dumps(
            {
                "R2_ACCESS_KEY_ID": "ak",
                "R2_SECRET_ACCESS_KEY": "sk",
                "R2_ACCOUNT_ID": "acct",
            }
        )
    )
    cfg = render.load_r2_config({"r2_bucket": "b", "r2_public_base_url": "https://a.test"})
    assert cfg["access_key"] == "ak" and cfg["account_id"] == "acct"


# --- resolve_pages_hook_url (issue #42) ------------------------------------
#
# The deploy-hook URL must resolve the same cron-friendly way the R2 credentials
# do — env first, then secrets.json, then config.json — so the scheduled
# (launchd/cron) run, which never inherits the interactive shell env, can still
# rebuild cortech.online after publishing to R2.


def test_resolve_hook_none_when_all_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("PAGES_DEPLOY_HOOK_URL", raising=False)
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)  # no secrets.json here
    assert render.resolve_pages_hook_url({}) is None


def test_resolve_hook_from_env(monkeypatch, tmp_path):
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setenv("PAGES_DEPLOY_HOOK_URL", "https://hook.test/env")
    assert render.resolve_pages_hook_url({}) == "https://hook.test/env"


def test_resolve_hook_from_secrets(monkeypatch, tmp_path):
    monkeypatch.delenv("PAGES_DEPLOY_HOOK_URL", raising=False)
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    (tmp_path / "secrets.json").write_text(
        json.dumps({"PAGES_DEPLOY_HOOK_URL": "https://hook.test/secrets"})
    )
    assert render.resolve_pages_hook_url({}) == "https://hook.test/secrets"


def test_resolve_hook_from_config(monkeypatch, tmp_path):
    monkeypatch.delenv("PAGES_DEPLOY_HOOK_URL", raising=False)
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)  # no secrets.json here
    assert (
        render.resolve_pages_hook_url({"pages_deploy_hook_url": "https://hook.test/config"})
        == "https://hook.test/config"
    )


def test_resolve_hook_env_wins_over_secrets_and_config(monkeypatch, tmp_path):
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    (tmp_path / "secrets.json").write_text(
        json.dumps({"PAGES_DEPLOY_HOOK_URL": "https://hook.test/secrets"})
    )
    monkeypatch.setenv("PAGES_DEPLOY_HOOK_URL", "https://hook.test/env")
    assert (
        render.resolve_pages_hook_url({"pages_deploy_hook_url": "https://hook.test/config"})
        == "https://hook.test/env"
    )


def test_resolve_hook_secrets_wins_over_config(monkeypatch, tmp_path):
    """The discriminating tier 'env wins' doesn't exercise: env unset, BOTH files
    present -> the 0600 secrets.json shadows the shareable config.json."""
    monkeypatch.delenv("PAGES_DEPLOY_HOOK_URL", raising=False)
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    (tmp_path / "secrets.json").write_text(
        json.dumps({"PAGES_DEPLOY_HOOK_URL": "https://hook.test/secrets"})
    )
    assert (
        render.resolve_pages_hook_url({"pages_deploy_hook_url": "https://hook.test/config"})
        == "https://hook.test/secrets"
    )


def test_resolve_hook_empty_string_falls_through(monkeypatch, tmp_path):
    """Empty string is 'unset' at every tier — first *non-empty* wins."""
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setenv("PAGES_DEPLOY_HOOK_URL", "")
    (tmp_path / "secrets.json").write_text(json.dumps({"PAGES_DEPLOY_HOOK_URL": ""}))
    assert render.resolve_pages_hook_url({"pages_deploy_hook_url": ""}) is None


def test_resolve_hook_unreadable_secrets_warns_and_falls_through(monkeypatch, tmp_path, capsys):
    """A malformed secrets.json must not raise — warn-and-continue, then fall
    through to config.json (best-effort contract preserved)."""
    monkeypatch.delenv("PAGES_DEPLOY_HOOK_URL", raising=False)
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    (tmp_path / "secrets.json").write_text("{not json")
    assert (
        render.resolve_pages_hook_url({"pages_deploy_hook_url": "https://hook.test/config"})
        == "https://hook.test/config"
    )
    assert "unreadable" in capsys.readouterr().err


# --- _r2_get_manifest ------------------------------------------------------


def test_get_manifest_missing_key_returns_empty():
    assert render._r2_get_manifest(FakeS3(), "b") == []


def test_get_manifest_existing():
    s3 = FakeS3(manifest=[{"slug": "a"}])
    assert render._r2_get_manifest(s3, "b") == [{"slug": "a"}]


def test_get_manifest_malformed_returns_empty():
    s3 = FakeS3()
    s3.objects["manifest.json"] = b"{not json"
    assert render._r2_get_manifest(s3, "b") == []


def test_get_manifest_reraises_non_missing_error():
    class Boom(FakeS3):
        def get_object(self, Bucket, Key):
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")

    with pytest.raises(ClientError):
        render._r2_get_manifest(Boom(), "b")


# --- maybe_publish_r2 (orchestration) --------------------------------------


def _configured(monkeypatch, tmp_path, s3):
    _clear_r2_env(monkeypatch)
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    monkeypatch.delenv("PAGES_DEPLOY_HOOK_URL", raising=False)
    monkeypatch.setattr(render, "r2_client", lambda cfg: s3)
    monkeypatch.setattr(render, "mp3_duration_ms", lambda p: 123_000)


def _publish_kwargs(tmp_path):
    mp3 = tmp_path / "episode.mp3"
    mp3.write_bytes(b"AUDIODATA")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"IMG")
    return {
        "episode_mp3": mp3,
        "cover": cover,
        "timeline": {"items": [{"chapter": {"title": "Intro", "start_time_ms": 0}}]},
        "manifest": {"title": "Daily X", "date": "2026-06-01", "summary": "the clean hook"},
        "description": "<p>hi</p>",
        "episode_uri": "spotify:episode:abc",
    }


def test_publish_not_configured(monkeypatch, tmp_path, capsys):
    _clear_r2_env(monkeypatch)
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    # Not configured is a benign no-op, distinct from a real failure (#48).
    assert render.maybe_publish_r2({}, **_publish_kwargs(tmp_path)) == render.R2_SKIPPED
    assert "not configured" in capsys.readouterr().err


def test_publish_happy_path(monkeypatch, tmp_path):
    s3 = FakeS3()
    _configured(monkeypatch, tmp_path, s3)
    cfg_config = {"r2_bucket": "clodcast", "r2_public_base_url": "https://audio.cortech.online/"}

    status = render.maybe_publish_r2(cfg_config, **_publish_kwargs(tmp_path))

    assert status == render.R2_PUBLISHED
    # mp3 + cover + manifest all uploaded; mp3 before manifest.
    assert "daily-digest-june-1-2026.mp3" in s3.objects
    assert "daily-digest-june-1-2026.jpg" in s3.objects
    assert "manifest.json" in s3.objects
    assert s3.put_order.index("daily-digest-june-1-2026.mp3") < s3.put_order.index("manifest.json")

    manifest = json.loads(s3.objects["manifest.json"])
    assert len(manifest) == 1
    e = manifest[0]
    # Slug is keyed on the manifest date, never the title "Daily X" (#128).
    assert e["slug"] == "daily-digest-june-1-2026"
    base = "https://audio.cortech.online"  # trailing slash stripped
    assert e["mp3_url"] == f"{base}/daily-digest-june-1-2026.mp3"
    assert e["cover_url"] == f"{base}/daily-digest-june-1-2026.jpg"
    assert e["mp3_bytes"] == len(b"AUDIODATA")
    assert e["duration_s"] == 123.0
    assert e["spotify_uri"] == "spotify:episode:abc"
    assert e["pubDate"].startswith("2026-06-01")
    # #45: the clean plain-text summary travels alongside the HTML description.
    assert e["summary"] == "the clean hook"
    # #45: description (HTML) and chapters[] are unchanged by the summary addition.
    assert e["description"] == "<p>hi</p>"
    assert e["chapters"] == [{"title": "Intro", "start_ms": 0, "source_url": None}]


def test_publish_appends_to_existing_manifest(monkeypatch, tmp_path):
    s3 = FakeS3(
        manifest=[{"slug": "older", "pubDate": "2026-05-01T12:00:00+00:00", "title": "Older"}]
    )
    _configured(monkeypatch, tmp_path, s3)
    status = render.maybe_publish_r2(
        {"r2_bucket": "b", "r2_public_base_url": "https://a.test"},
        **_publish_kwargs(tmp_path),
    )
    assert status == render.R2_PUBLISHED
    manifest = json.loads(s3.objects["manifest.json"])
    assert [e["slug"] for e in manifest] == ["daily-digest-june-1-2026", "older"]


def test_publish_key_is_unchanged_when_the_title_changes(monkeypatch, tmp_path):
    """The property #128 exists to establish: enriching an episode's title must not
    move the R2 object or the permalink guid derived from it. Re-publishing the same
    date upserts the single entry rather than minting a second one."""
    s3 = FakeS3()
    _configured(monkeypatch, tmp_path, s3)
    cfg_config = {"r2_bucket": "b", "r2_public_base_url": "https://a.test"}
    kwargs = _publish_kwargs(tmp_path)

    assert render.maybe_publish_r2(cfg_config, **kwargs) == render.R2_PUBLISHED
    kwargs["manifest"] = {**kwargs["manifest"], "title": "Anthropic's IPO path, an MCP roadmap"}
    assert render.maybe_publish_r2(cfg_config, **kwargs) == render.R2_PUBLISHED

    assert [k for k in s3.objects if k.endswith(".mp3")] == ["daily-digest-june-1-2026.mp3"]
    entries = json.loads(s3.objects["manifest.json"])
    assert [e["slug"] for e in entries] == ["daily-digest-june-1-2026"]  # upsert, not a 2nd entry
    assert entries[0]["title"] == "Anthropic's IPO path, an MCP roadmap"  # display text did move


def test_dry_run_preview_url_matches_the_real_publish_url(monkeypatch, tmp_path):
    """--dry-run must advertise exactly the URL a real publish would write; both
    resolve it through r2_episode_mp3_url, so the rehearsal cannot drift from it."""
    s3 = FakeS3()
    _configured(monkeypatch, tmp_path, s3)
    cfg_config = {"r2_bucket": "b", "r2_public_base_url": "https://audio.cortech.online/"}
    kwargs = _publish_kwargs(tmp_path)

    preview = render.r2_episode_mp3_url(render.load_r2_config(cfg_config), kwargs["manifest"])

    assert render.maybe_publish_r2(cfg_config, **kwargs) == render.R2_PUBLISHED
    published = json.loads(s3.objects["manifest.json"])[0]["mp3_url"]
    assert preview == published == "https://audio.cortech.online/daily-digest-june-1-2026.mp3"


def test_publish_mp3_failure_reports_failed_distinct_from_skipped(monkeypatch, tmp_path, capsys):
    """A configured-but-errored publish surfaces R2_FAILED — distinct from the benign
    R2_SKIPPED no-op (#48) — so an operator can spot a silent web-feed miss. The run
    must still not raise (Spotify stays canonical)."""
    s3 = FakeS3()
    s3.fail_suffix = ".mp3"
    _configured(monkeypatch, tmp_path, s3)
    status = render.maybe_publish_r2(
        {"r2_bucket": "b", "r2_public_base_url": "https://a.test"},
        **_publish_kwargs(tmp_path),
    )
    assert status == render.R2_FAILED
    assert status != render.R2_SKIPPED  # the distinction that #48 adds
    assert "manifest.json" not in s3.objects  # never wrote a manifest on failure
    assert "publish failed" in capsys.readouterr().err


def test_publish_cover_failure_is_nonfatal(monkeypatch, tmp_path):
    s3 = FakeS3()
    s3.fail_suffix = ".jpg"
    _configured(monkeypatch, tmp_path, s3)
    status = render.maybe_publish_r2(
        {"r2_bucket": "b", "r2_public_base_url": "https://a.test"},
        **_publish_kwargs(tmp_path),
    )
    assert status == render.R2_PUBLISHED
    manifest = json.loads(s3.objects["manifest.json"])
    assert "cover_url" not in manifest[0]  # cover failed -> omitted, episode still published


def test_publish_fires_pages_hook(monkeypatch, tmp_path):
    s3 = FakeS3()
    _configured(monkeypatch, tmp_path, s3)
    monkeypatch.setenv("PAGES_DEPLOY_HOOK_URL", "https://hook.test/deploy")
    fired = []
    monkeypatch.setattr(render, "fire_pages_hook", lambda url: fired.append(url))
    render.maybe_publish_r2(
        {"r2_bucket": "b", "r2_public_base_url": "https://a.test"},
        **_publish_kwargs(tmp_path),
    )
    assert fired == ["https://hook.test/deploy"]


def test_publish_fires_pages_hook_from_secrets(monkeypatch, tmp_path):
    """The cron-friendly home: a scheduled run has no env var, but the hook in
    secrets.json still fires after a successful publish (issue #42)."""
    s3 = FakeS3()
    _configured(monkeypatch, tmp_path, s3)  # clears the env var; CONFIG_DIR -> tmp_path
    (tmp_path / "secrets.json").write_text(
        json.dumps(
            {
                "PAGES_DEPLOY_HOOK_URL": "https://hook.test/from-secrets",
            }
        )
    )
    fired = []
    monkeypatch.setattr(render, "fire_pages_hook", lambda url: fired.append(url))
    render.maybe_publish_r2(
        {"r2_bucket": "b", "r2_public_base_url": "https://a.test"},
        **_publish_kwargs(tmp_path),
    )
    assert fired == ["https://hook.test/from-secrets"]


def test_publish_fires_pages_hook_from_config(monkeypatch, tmp_path):
    """The convenience home: config.json's pages_deploy_hook_url fires when env
    and secrets.json are both absent."""
    s3 = FakeS3()
    _configured(monkeypatch, tmp_path, s3)  # no secrets.json written
    fired = []
    monkeypatch.setattr(render, "fire_pages_hook", lambda url: fired.append(url))
    render.maybe_publish_r2(
        {
            "r2_bucket": "b",
            "r2_public_base_url": "https://a.test",
            "pages_deploy_hook_url": "https://hook.test/from-config",
        },
        **_publish_kwargs(tmp_path),
    )
    assert fired == ["https://hook.test/from-config"]


def test_publish_no_hook_when_unset(monkeypatch, tmp_path):
    """All three sources unset -> no hook fired, publish still succeeds (the
    original env-only behaviour preserved)."""
    s3 = FakeS3()
    _configured(monkeypatch, tmp_path, s3)  # no secrets.json, no env var
    fired = []
    monkeypatch.setattr(render, "fire_pages_hook", lambda url: fired.append(url))
    status = render.maybe_publish_r2(
        {"r2_bucket": "b", "r2_public_base_url": "https://a.test"},
        **_publish_kwargs(tmp_path),
    )
    assert status == render.R2_PUBLISHED
    assert fired == []


# --- resume-path R2 back-fill (#40) ----------------------------------------
#
# The --workdir resume tail (_resume) must also publish to R2, mirroring the fresh
# path, WITHOUT calling load_config (the resume-config-free invariant pinned by
# test_resume_skips_upload_and_runs_idempotent_tail). R2 config is resolved env-only
# (maybe_publish_r2({}, ...)). These tests seed an "uploaded" workdir and drive
# _resume directly against the FakeS3, with set_timeline/poll_ready stubbed.


def _seed_resume_workdir(wd, *, with_description=True):
    """An uploaded.json workdir as the fresh path leaves it before its failure-prone
    tail: episode.mp3, cover.jpg, timeline.json, and (current renderer) description.html."""
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "uploaded.json").write_text(
        json.dumps({"episode_uri": "spotify:episode:abc123", "title": "Daily X"})
    )
    (wd / "episode.mp3").write_bytes(b"AUDIODATA")
    (wd / "cover.jpg").write_bytes(b"IMG")
    (wd / "timeline.json").write_text(
        json.dumps({"items": [{"chapter": {"title": "Intro", "start_time_ms": 0}}]})
    )
    if with_description:
        (wd / "description.html").write_text("<p>resumed hook</p>")


def _stub_resume_tail(monkeypatch):
    """Stub the Spotify tail + dedup so _resume exercises only the R2 back-fill."""
    monkeypatch.setattr(render, "set_timeline", lambda *a, **k: None)
    monkeypatch.setattr(render, "poll_ready", lambda *a, **k: None)
    monkeypatch.setattr(render, "_save_dedup", lambda *a, **k: None)
    monkeypatch.setattr(render, "_clear_inflight", lambda *a, **k: None)
    monkeypatch.setattr(render, "mp3_duration_ms", lambda p: 123_000)
    # load_config must NEVER run on the resume path (resume-config-free invariant).
    monkeypatch.setattr(
        render, "load_config", lambda: pytest.fail("load_config must not run on resume")
    )


def test_resume_publishes_to_r2_when_env_configured(monkeypatch, tmp_path, capsys):
    """A resumed run with R2 env vars set back-fills the mp3 + manifest entry to R2
    and reports r2_status=published in the resume JSON — closing the #40 gap where a
    poll_ready-recovered episode never reached the web feed."""
    wd = tmp_path / "wd"
    _seed_resume_workdir(wd)
    s3 = FakeS3()
    _configured(monkeypatch, tmp_path, s3)  # sets R2 env, patches r2_client -> s3
    monkeypatch.setenv("R2_BUCKET", "clodcast")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://audio.cortech.online")
    _stub_resume_tail(monkeypatch)

    manifest = {"title": "Daily X", "date": "2026-06-01", "summary": "the clean hook"}
    rc = render._resume(
        wd, wd / "uploaded.json", [{"source_url": "https://x.test/a"}], "Daily X", manifest
    )

    assert rc == 0
    assert "daily-digest-june-1-2026.mp3" in s3.objects
    assert "manifest.json" in s3.objects
    e = json.loads(s3.objects["manifest.json"])[0]
    assert e["spotify_uri"] == "spotify:episode:abc123"
    assert e["summary"] == "the clean hook"  # #45 field rides the resume path too
    assert e["description"] == "<p>resumed hook</p>"  # read from workdir description.html
    out = json.loads(capsys.readouterr().out)
    assert out["resumed"] is True
    assert out["r2_status"] == render.R2_PUBLISHED


def test_resume_skips_r2_when_unconfigured(monkeypatch, tmp_path, capsys):
    """Resume with R2 unset behaves exactly as before: no publish, r2_status=skipped,
    the run still succeeds. (Mirrors the fresh-path benign no-op.)"""
    wd = tmp_path / "wd"
    _seed_resume_workdir(wd)
    _clear_r2_env(monkeypatch)
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)  # no secrets.json here
    _stub_resume_tail(monkeypatch)

    manifest = {"title": "Daily X", "date": "2026-06-01", "summary": "hook"}
    rc = render._resume(
        wd, wd / "uploaded.json", [{"source_url": "https://x.test/a"}], "Daily X", manifest
    )

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["r2_status"] == render.R2_SKIPPED


def test_resume_r2_failure_is_nonfatal_and_reports_failed(monkeypatch, tmp_path, capsys):
    """A configured-but-failed R2 publish on resume must NOT fail the run: the resume
    still returns 0 and reports r2_status=failed (#48 surfaced through the resume
    path, #40's non-fatal invariant preserved)."""
    wd = tmp_path / "wd"
    _seed_resume_workdir(wd)
    s3 = FakeS3()
    s3.fail_suffix = ".mp3"
    _configured(monkeypatch, tmp_path, s3)
    monkeypatch.setenv("R2_BUCKET", "clodcast")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://audio.cortech.online")
    dedup_called = []
    _stub_resume_tail(monkeypatch)
    monkeypatch.setattr(render, "_save_dedup", lambda *a, **k: dedup_called.append(True))

    manifest = {"title": "Daily X", "date": "2026-06-01", "summary": "hook"}
    rc = render._resume(
        wd, wd / "uploaded.json", [{"source_url": "https://x.test/a"}], "Daily X", manifest
    )

    assert rc == 0  # non-fatal: the run still succeeds
    assert dedup_called == [True]  # dedup still ran despite the R2 failure
    assert "manifest.json" not in s3.objects  # no manifest written on failure
    out = json.loads(capsys.readouterr().out)
    assert out["r2_status"] == render.R2_FAILED


def test_resume_without_description_html_skips_r2(monkeypatch, tmp_path, capsys):
    """An older workdir predating description.html degrades to a skipped back-fill
    rather than aborting the already-live episode's idempotent tail."""
    wd = tmp_path / "wd"
    _seed_resume_workdir(wd, with_description=False)
    s3 = FakeS3()
    _configured(monkeypatch, tmp_path, s3)
    monkeypatch.setenv("R2_BUCKET", "clodcast")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://audio.cortech.online")
    _stub_resume_tail(monkeypatch)

    manifest = {"title": "Daily X", "date": "2026-06-01", "summary": "hook"}
    rc = render._resume(
        wd, wd / "uploaded.json", [{"source_url": "https://x.test/a"}], "Daily X", manifest
    )

    assert rc == 0
    assert "manifest.json" not in s3.objects  # nothing published without a description
    out = json.loads(capsys.readouterr().out)
    assert out["r2_status"] == render.R2_SKIPPED


# --- consumer-schema conformance ------------------------------------------
#
# The manifest entry is the contract with cortech.online. Its consumer does
# `manifestSchema.safeParse(raw)` and returns [] (empties the WHOLE feed) on any
# validation miss — a silent, total failure. So encode the exact Zod constraints
# from src/lib/episodes.ts (schmug/cortech.online@main, verified against the raw
# file) here, asserted against a real generated entry, to catch drift on our side.


def test_manifest_entry_conforms_to_consumer_episode_schema():
    entry = render.build_manifest_entry(
        slug=render.slug_for_date("2026-06-01"),
        title="Daily Digest - June 1, 2026",
        description="<p>hook</p><p>(0:00) - Intro</p>",
        summary="hook",
        pubdate=render.resolve_pubdate({"date": "2026-06-01"}),
        mp3_url="https://audio.cortech.online/daily-digest-june-1-2026.mp3",
        mp3_bytes=272134,
        duration_s=611.311,
        chapters=render.chapters_from_timeline(
            {
                "items": [
                    {"chapter": {"title": "Intro", "start_time_ms": 0}},
                    {"chapter": {"title": "Story", "start_time_ms": 30000}},
                    {
                        "link": {
                            "start_time_ms": 31000,
                            "duration_ms": 4000,
                            "url": "https://x.test/a",
                        }
                    },
                ]
            }
        ),
        spotify_uri="spotify:episode:abc",
        cover_url="https://audio.cortech.online/daily-digest-june-1-2026.jpg",
    )

    # episodeSchema (constraint in the trailing comment):
    assert re.fullmatch(r"[a-z0-9-]+", entry["slug"])  # slug: z.string().regex(^[a-z0-9-]+$)
    assert isinstance(entry["title"], str)  # title: z.string()
    assert isinstance(entry["description"], str)  # description: z.string()
    # summary: z.string().optional() on the consumer (#45) — additive, HTML-by-contract.
    assert isinstance(entry["summary"], str)
    dt.datetime.fromisoformat(entry["pubDate"])  # pubDate: z.coerce.date()
    assert re.match(r"https?://", entry["mp3_url"])  # mp3_url: z.url()
    # mp3_bytes: z.number().int().positive()
    assert isinstance(entry["mp3_bytes"], int) and entry["mp3_bytes"] > 0
    # duration_s: z.number().positive()
    assert isinstance(entry["duration_s"], (int, float)) and entry["duration_s"] > 0
    assert isinstance(entry["chapters"], list)  # chapters: z.array(chapterSchema).default([])
    assert entry["explicit"] is False  # explicit: z.boolean().default(false)
    assert isinstance(entry["spotify_uri"], str)  # spotify_uri: z.string().nullable().optional()
    assert re.match(r"https?://", entry["cover_url"])  # cover_url: z.url().nullable().optional()

    # chapterSchema:
    for c in entry["chapters"]:
        assert isinstance(c["title"], str)  # title: z.string()
        # start_ms: z.number().int().nonnegative()
        assert isinstance(c["start_ms"], int) and c["start_ms"] >= 0
        # source_url: z.url().nullable().optional()
        assert c["source_url"] is None or re.match(r"https?://", c["source_url"])
