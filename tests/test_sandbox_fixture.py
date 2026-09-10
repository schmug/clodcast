"""
Invariants for tests/data/sandbox_manifest.json — the disposable publishing target.

The fixture is not consumed by any test run: it is a manifest a human or an agent
hands to `render.py` FOR REAL, to exercise the one part of the pipeline `--dry-run`
cannot rehearse (the R2 PUTs, the manifest upsert, the deploy hook, the dedup
write). Everything above the ship is already covered by a rehearsal.

That makes it the most dangerous file in the repo to let drift. It is edited by
whoever is spiking, it points at the real bucket, and its whole safety property is
that four keys hold it away from two live feeds. This module is what keeps that
true: it re-derives the live shows' namespaces from their own sources and asserts
the sandbox collides with neither. An edit that would have a spike overwrite a
published episode fails here instead of in production.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pytest

import render

FIXTURE = Path(__file__).resolve().parent / "data" / "sandbox_manifest.json"
REPO = Path(__file__).resolve().parent.parent
FRONTIER_SKILL = REPO / "skills" / "frontier-commits" / "SKILL.md"

# The four keys that hold the sandbox away from every live feed. Named once so a
# new isolation key cannot be added to the fixture without landing in these tests.
ISOLATION_KEYS = ("ship_mode", "slug_prefix", "r2_manifest_name", "r2_key_prefix")


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def _frontier_manifest() -> dict:
    """Frontier Commits' documented manifest, read from its SKILL.md.

    Read rather than hardcoded on purpose: if that show ever changes its slug
    prefix, manifest object or key prefix, the collision checks below must move
    with it. A copy here would silently stop protecting anything."""
    block = re.search(r"```json\n(\{.*?\n\})\n```", FRONTIER_SKILL.read_text(), re.S)
    assert block, "frontier-commits SKILL.md no longer contains a ```json manifest block"
    return json.loads(block.group(1))


# --- the fixture is a manifest the renderer will actually accept -------------


def test_the_sandbox_fixture_validates():
    render.validate_manifest(_fixture())


def test_the_sandbox_ships_web_only():
    """A spike must never reach save-to-spotify: that path uploads to a retired
    private show and, at its cap, deletes a published episode to make room."""
    m = _fixture()
    assert m["ship_mode"] == render.SHIP_MODE_WEB
    assert render.is_web_only(m) is True


def test_every_isolation_key_is_present():
    m = _fixture()
    missing = [k for k in ISOLATION_KEYS if not m.get(k)]
    assert not missing, f"the sandbox lost its isolation key(s): {missing}"


# --- ...and cannot collide with either live show -----------------------------


def test_the_sandbox_slug_cannot_collide_with_a_live_show():
    """The slug is the permalink AND the isPermaLink guid, and it is keyed on the
    DATE — so a sandbox run on a day a real show publishes would mint the real
    show's identifier if the prefix ever matched."""
    m = _fixture()
    today = dt.date.today().isoformat()
    sandbox = render.slug_for_date(today, render.resolve_slug_prefix(m))
    daily = render.slug_for_date(today, render.DEFAULT_SLUG_PREFIX)
    frontier = render.slug_for_date(today, render.resolve_slug_prefix(_frontier_manifest()))

    assert sandbox != daily
    assert sandbox != frontier
    assert len({sandbox, daily, frontier}) == 3


def test_the_sandbox_writes_its_feed_entry_to_its_own_object():
    """r2_manifest_name defaults to manifest.json — the DAILY show's feed. A
    sandbox that lost this key would upsert a test episode into the live feed."""
    m = _fixture()
    assert m["r2_manifest_name"] != "manifest.json"
    assert m["r2_manifest_name"] != _frontier_manifest()["r2_manifest_name"]


def test_the_sandbox_namespaces_its_audio_and_cover_objects():
    """Without a key prefix the mp3/cover keys are bare `<slug>.mp3` — and since
    the slug is date-keyed, a same-day sandbox run would overwrite a published
    episode's audio in the shared bucket (#142, one show up)."""
    m = _fixture()
    prefix = render._r2_key_prefix(m)
    assert prefix, "the sandbox lost its r2_key_prefix"
    assert prefix != render._r2_key_prefix(_frontier_manifest())
    assert prefix != render._r2_key_prefix({})  # the daily show's (empty) default


def test_the_sandbox_never_marks_a_real_story_as_covered():
    """covered.json is SHARED with both live shows. A sandbox segment carrying a
    source_url would withhold that URL from the show that actually wanted it."""
    m = _fixture()
    assert render._segment_urls(m["segments"]) == []
    assert all(seg.get("source_url") is None for seg in m["segments"])


# --- reusability -------------------------------------------------------------


def test_the_sandbox_carries_no_date_so_every_run_mints_a_fresh_slug():
    """R2 objects are immutable-cached: re-publishing a slug replaces the origin
    bytes while the edge keeps serving the old ones per POP. A pinned date would
    make every future spike fight that. Absent, the slug follows today."""
    assert "date" not in _fixture()


def test_the_sandbox_exercises_the_music_layer_against_the_bundled_asset():
    """The probe covers the music mix too, so one command exercises more of the
    pipeline. Pinning the hash here means a re-recorded asset fails a unit test
    rather than a live run's pre-flight, mid-spike."""
    music = render.resolve_music_config(_fixture()["music"])
    asset = render.resolve_music_asset(music)

    assert asset.exists()
    assert render.artifact_fingerprint(asset) == music["asset_sha256"]
    assert render._music_asset_check(music)["ok"] is True


def test_the_sandbox_carries_the_bookend_roles_music_requires():
    roles = [seg.get("role") for seg in _fixture()["segments"]]
    assert roles[0] == render.SEGMENT_ROLE_INTRO
    assert roles[-1] == render.SEGMENT_ROLE_OUTRO


@pytest.mark.parametrize("show", ["daily", "frontier"])
def test_the_live_shows_still_look_the_way_these_checks_assume(show):
    """A guard on the guards: if a live show's namespace moves and this file's
    derivation stops finding it, the collision checks above would pass vacuously."""
    if show == "daily":
        assert render.DEFAULT_SLUG_PREFIX == "daily-digest"
        assert render._r2_key_prefix({}) == ""
    else:
        fc = _frontier_manifest()
        assert fc["slug_prefix"] and fc["r2_manifest_name"] and fc["r2_key_prefix"]
        assert fc["ship_mode"] == render.SHIP_MODE_WEB
