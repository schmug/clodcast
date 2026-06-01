"""
Tests for the R2 publish path in render.py (issue #33).

Covers the pure logic (slug, chapter reconstruction, manifest entry shape,
upsert/cap/order, config resolution) and the publish orchestration against a
fake S3 client — no network, no boto3 round-trip, no save-to-spotify. The audio
seam (`mp3_duration_ms`) is monkeypatched, as elsewhere in the suite.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import re

import pytest
from botocore.exceptions import ClientError

import render

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


# --- slugify ---------------------------------------------------------------


def test_slugify_basic():
    assert render.slugify("Daily Digest - May 22, 2026", "2026-05-22") == "daily-digest-may-22-2026"


def test_slugify_collapses_and_trims_punctuation():
    assert render.slugify("  Hello, World!!  ", "2026-01-01") == "hello-world"


def test_slugify_empty_falls_back_to_date():
    assert render.slugify("!!!", "2026-06-01") == "episode-2026-06-01"


def test_slugify_matches_consumer_regex():
    slug = render.slugify("Aé/B  C—D", "2026-06-01")
    assert re.fullmatch(r"[a-z0-9-]+", slug)


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


def test_build_manifest_entry_omits_empty_optionals():
    entry = render.build_manifest_entry(
        slug="s", title="t", description="d", pubdate="2026-06-01T12:00:00+00:00",
        mp3_url="https://a.test/s.mp3", mp3_bytes=1, duration_s=1.0, chapters=[],
        spotify_uri=None, cover_url=None,
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
    for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ACCOUNT_ID",
              "R2_BUCKET", "R2_PUBLIC_BASE_URL", "PAGES_DEPLOY_HOOK_URL"):
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
    cfg = render.load_r2_config({
        "r2_bucket": "clodcast", "r2_public_base_url": "https://audio.cortech.online",
    })
    assert cfg == {
        "account_id": "acct", "access_key": "ak", "secret_key": "sk",
        "bucket": "clodcast", "public_base_url": "https://audio.cortech.online",
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
    (tmp_path / "secrets.json").write_text(json.dumps({
        "R2_ACCESS_KEY_ID": "ak", "R2_SECRET_ACCESS_KEY": "sk", "R2_ACCOUNT_ID": "acct",
    }))
    cfg = render.load_r2_config({"r2_bucket": "b", "r2_public_base_url": "https://a.test"})
    assert cfg["access_key"] == "ak" and cfg["account_id"] == "acct"


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
        "manifest": {"title": "Daily X", "date": "2026-06-01"},
        "description": "<p>hi</p>",
        "episode_uri": "spotify:episode:abc",
    }


def test_publish_not_configured(monkeypatch, tmp_path, capsys):
    _clear_r2_env(monkeypatch)
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    assert render.maybe_publish_r2({}, **_publish_kwargs(tmp_path)) is False
    assert "not configured" in capsys.readouterr().err


def test_publish_happy_path(monkeypatch, tmp_path):
    s3 = FakeS3()
    _configured(monkeypatch, tmp_path, s3)
    cfg_config = {"r2_bucket": "clodcast", "r2_public_base_url": "https://audio.cortech.online/"}

    ok = render.maybe_publish_r2(cfg_config, **_publish_kwargs(tmp_path))

    assert ok is True
    # mp3 + cover + manifest all uploaded; mp3 before manifest.
    assert "daily-x.mp3" in s3.objects
    assert "daily-x.jpg" in s3.objects
    assert "manifest.json" in s3.objects
    assert s3.put_order.index("daily-x.mp3") < s3.put_order.index("manifest.json")

    manifest = json.loads(s3.objects["manifest.json"])
    assert len(manifest) == 1
    e = manifest[0]
    assert e["slug"] == "daily-x"
    assert e["mp3_url"] == "https://audio.cortech.online/daily-x.mp3"  # trailing slash stripped
    assert e["cover_url"] == "https://audio.cortech.online/daily-x.jpg"
    assert e["mp3_bytes"] == len(b"AUDIODATA")
    assert e["duration_s"] == 123.0
    assert e["spotify_uri"] == "spotify:episode:abc"
    assert e["pubDate"].startswith("2026-06-01")


def test_publish_appends_to_existing_manifest(monkeypatch, tmp_path):
    s3 = FakeS3(manifest=[{"slug": "older", "pubDate": "2026-05-01T12:00:00+00:00",
                           "title": "Older"}])
    _configured(monkeypatch, tmp_path, s3)
    ok = render.maybe_publish_r2(
        {"r2_bucket": "b", "r2_public_base_url": "https://a.test"},
        **_publish_kwargs(tmp_path),
    )
    assert ok is True
    manifest = json.loads(s3.objects["manifest.json"])
    assert [e["slug"] for e in manifest] == ["daily-x", "older"]


def test_publish_mp3_failure_warns_and_returns_false(monkeypatch, tmp_path, capsys):
    s3 = FakeS3()
    s3.fail_suffix = ".mp3"
    _configured(monkeypatch, tmp_path, s3)
    ok = render.maybe_publish_r2(
        {"r2_bucket": "b", "r2_public_base_url": "https://a.test"},
        **_publish_kwargs(tmp_path),
    )
    assert ok is False
    assert "manifest.json" not in s3.objects  # never wrote a manifest on failure
    assert "publish failed" in capsys.readouterr().err


def test_publish_cover_failure_is_nonfatal(monkeypatch, tmp_path):
    s3 = FakeS3()
    s3.fail_suffix = ".jpg"
    _configured(monkeypatch, tmp_path, s3)
    ok = render.maybe_publish_r2(
        {"r2_bucket": "b", "r2_public_base_url": "https://a.test"},
        **_publish_kwargs(tmp_path),
    )
    assert ok is True
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


def test_publish_fires_pages_hook_from_secrets_file(monkeypatch, tmp_path):
    s3 = FakeS3()
    _configured(monkeypatch, tmp_path, s3)
    (tmp_path / "secrets.json").write_text(json.dumps({
        "PAGES_DEPLOY_HOOK_URL": "https://hook.test/from-secrets",
    }))
    fired = []
    monkeypatch.setattr(render, "fire_pages_hook", lambda url: fired.append(url))
    render.maybe_publish_r2(
        {"r2_bucket": "b", "r2_public_base_url": "https://a.test"},
        **_publish_kwargs(tmp_path),
    )
    assert fired == ["https://hook.test/from-secrets"]


def test_publish_fires_pages_hook_from_config(monkeypatch, tmp_path):
    s3 = FakeS3()
    _configured(monkeypatch, tmp_path, s3)
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


def test_pages_hook_env_wins_over_secrets_and_config(monkeypatch, tmp_path):
    s3 = FakeS3()
    _configured(monkeypatch, tmp_path, s3)
    monkeypatch.setenv("PAGES_DEPLOY_HOOK_URL", "https://hook.test/from-env")
    (tmp_path / "secrets.json").write_text(json.dumps({
        "PAGES_DEPLOY_HOOK_URL": "https://hook.test/from-secrets",
    }))
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
    assert fired == ["https://hook.test/from-env"]


def test_publish_without_pages_hook_succeeds_without_firing(monkeypatch, tmp_path):
    s3 = FakeS3()
    _configured(monkeypatch, tmp_path, s3)
    fired = []
    monkeypatch.setattr(render, "fire_pages_hook", lambda url: fired.append(url))
    ok = render.maybe_publish_r2(
        {"r2_bucket": "b", "r2_public_base_url": "https://a.test"},
        **_publish_kwargs(tmp_path),
    )
    assert ok is True
    assert fired == []


# --- consumer-schema conformance ------------------------------------------
#
# The manifest entry is the contract with cortech.online. Its consumer does
# `manifestSchema.safeParse(raw)` and returns [] (empties the WHOLE feed) on any
# validation miss — a silent, total failure. So encode the exact Zod constraints
# from src/lib/episodes.ts (schmug/cortech.online@main, verified against the raw
# file) here, asserted against a real generated entry, to catch drift on our side.


def test_manifest_entry_conforms_to_consumer_episode_schema():
    entry = render.build_manifest_entry(
        slug=render.slugify("Daily Digest - June 1, 2026", "2026-06-01"),
        title="Daily Digest - June 1, 2026",
        description="<p>hook</p><p>(0:00) - Intro</p>",
        pubdate=render.resolve_pubdate({"date": "2026-06-01"}),
        mp3_url="https://audio.cortech.online/daily-digest-june-1-2026.mp3",
        mp3_bytes=272134,
        duration_s=611.311,
        chapters=render.chapters_from_timeline({
            "items": [
                {"chapter": {"title": "Intro", "start_time_ms": 0}},
                {"chapter": {"title": "Story", "start_time_ms": 30000}},
                {"link": {"start_time_ms": 31000, "duration_ms": 4000,
                          "url": "https://x.test/a"}},
            ]
        }),
        spotify_uri="spotify:episode:abc",
        cover_url="https://audio.cortech.online/daily-digest-june-1-2026.jpg",
    )

    # episodeSchema (constraint in the trailing comment):
    assert re.fullmatch(r"[a-z0-9-]+", entry["slug"])  # slug: z.string().regex(^[a-z0-9-]+$)
    assert isinstance(entry["title"], str)  # title: z.string()
    assert isinstance(entry["description"], str)  # description: z.string()
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
