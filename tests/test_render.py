"""
Invariant tests for skills/daily-podcast/render.py.

These cover the pure-Python logic — chapter-rule enforcement, voice precedence,
timeline math, dedup-log handling — without requiring MLX, ffmpeg, or the
save-to-spotify CLI. The audio I/O seam (`mp3_duration_ms`) is monkeypatched.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

import render

# --- plan_silences --------------------------------------------------------


def _patch_durations(monkeypatch, durations_by_path: dict[Path, int]) -> None:
    monkeypatch.setattr(render, "mp3_duration_ms", lambda p: durations_by_path[Path(p)])


def _paths(tmp_path: Path, n: int) -> list[Path]:
    return [tmp_path / f"seg_{i:02d}.mp3" for i in range(1, n + 1)]


def test_plan_silences_no_padding_when_all_chapters_long(tmp_path, monkeypatch):
    paths = _paths(tmp_path, 3)
    _patch_durations(monkeypatch, {p: 35_000 for p in paths})

    silences = render.plan_silences(paths)

    assert silences == [
        render.DEFAULT_SILENCE_MS,
        render.DEFAULT_SILENCE_MS,
        render.LAST_SILENCE_MS,
    ]


def test_plan_silences_exactly_three_shorts_no_padding(tmp_path, monkeypatch):
    # Three short chapters is exactly at the cap; the last one counts as short
    # per plan_silences's docstring. No padding should happen.
    paths = _paths(tmp_path, 3)
    _patch_durations(monkeypatch, {p: 20_000 for p in paths})

    silences = render.plan_silences(paths)

    assert silences == [
        render.DEFAULT_SILENCE_MS,
        render.DEFAULT_SILENCE_MS,
        render.LAST_SILENCE_MS,
    ]


def test_plan_silences_pads_when_over_budget_with_room(tmp_path, monkeypatch):
    # Four short chapters, each long enough that a single pad clears it to
    # TARGET_CHAPTER_MS (30_500). Loop should pad exactly one chapter.
    paths = _paths(tmp_path, 4)
    _patch_durations(monkeypatch, {p: 20_000 for p in paths})

    silences = render.plan_silences(paths)

    # Last segment never gets trailing silence.
    assert silences[-1] == render.LAST_SILENCE_MS
    # Exactly one of the non-last silences was bumped past the default.
    padded = [i for i in range(3) if silences[i] > render.DEFAULT_SILENCE_MS]
    assert len(padded) == 1
    i = padded[0]
    # The padded chapter clears the 30s short threshold.
    assert 20_000 + silences[i] >= 30_000
    # And the chapter doesn't exceed the silence cap.
    assert silences[i] <= 12_000


def test_plan_silences_dies_when_no_room_to_pad(tmp_path, monkeypatch):
    # Four chapters so tiny that even MAX_SILENCE_MS (12_000) can't clear them.
    # All non-last slots will max out, then candidates becomes empty → die.
    paths = _paths(tmp_path, 4)
    _patch_durations(monkeypatch, {p: 5_000 for p in paths})

    with pytest.raises(SystemExit):
        render.plan_silences(paths)


# --- resolve_voice --------------------------------------------------------


@pytest.fixture
def house_voice_files(tmp_path, monkeypatch):
    """Point USER_HOUSE_AUDIO / USER_HOUSE_TEXT at real files in tmp_path so the
    house-voice branch doesn't touch the real ~/.config or the bundled refs."""
    audio = tmp_path / "house.wav"
    text = tmp_path / "house.txt"
    audio.write_bytes(b"RIFF")  # contents irrelevant; resolver only checks existence
    text.write_text("the quick brown fox  \n")
    monkeypatch.setattr(render, "USER_HOUSE_AUDIO", audio)
    monkeypatch.setattr(render, "USER_HOUSE_TEXT", text)
    return audio, text


def test_resolve_voice_house_is_default(house_voice_files):
    audio, _ = house_voice_files
    voice, voice_instruct, ref_audio, ref_text = render.resolve_voice({})

    assert voice == "house"
    assert voice_instruct is None
    assert ref_audio == str(audio)
    assert ref_text == "the quick brown fox"  # stripped


def test_resolve_voice_voice_instruct_overrides_house():
    voice, voice_instruct, ref_audio, ref_text = render.resolve_voice(
        {"voice_instruct": "a calm narrator"}
    )

    # voice_instruct + default "house" request collapses the label to "custom"
    assert voice == "custom"
    assert voice_instruct == "a calm narrator"
    assert ref_audio is None
    assert ref_text is None


def test_resolve_voice_voice_instruct_with_explicit_preset_keeps_label():
    voice, voice_instruct, *_ = render.resolve_voice(
        {"voice_instruct": "a calm narrator", "voice": "Ryan"}
    )
    assert voice == "Ryan"
    assert voice_instruct == "a calm narrator"


def test_resolve_voice_random_returns_preset(monkeypatch):
    # Determinism: stub random.choice so we can assert the call as well as the result.
    monkeypatch.setattr(render.random, "choice", lambda xs: xs[1])
    voice, voice_instruct, ref_audio, ref_text = render.resolve_voice({"voice": "random"})

    assert voice in render.VOICES
    assert voice == render.VOICES[1]
    assert voice_instruct is None
    assert ref_audio is None
    assert ref_text is None


def test_resolve_voice_explicit_preset_passes_through():
    voice, _, ref_audio, ref_text = render.resolve_voice({"voice": "Ryan"})
    assert voice == "Ryan"
    assert ref_audio is None
    assert ref_text is None


def test_resolve_voice_unknown_name_fails():
    with pytest.raises(SystemExit):
        render.resolve_voice({"voice": "Nonexistent"})


# --- resolve_voice_mode ---------------------------------------------------


def test_resolve_voice_mode_clone_when_ref_audio():
    assert render.resolve_voice_mode(None, "/x/house.wav") == "clone"


def test_resolve_voice_mode_design_when_instruct_only():
    assert render.resolve_voice_mode("a calm narrator", None) == "design"


def test_resolve_voice_mode_clone_wins_over_instruct():
    # render_segments clones when ref_audio is set even if instruct is present.
    assert render.resolve_voice_mode("a calm narrator", "/x/house.wav") == "clone"


def test_resolve_voice_mode_preset_when_neither():
    assert render.resolve_voice_mode(None, None) == "preset"


# The four acceptance scenarios from issue #12: the `voice` label is unchanged
# (backwards-compatible), but voice_mode now disambiguates what actually ran.


def test_voice_label_and_mode_preset_plus_instruct(house_voice_files):
    voice, voice_instruct, ref_audio, _ = render.resolve_voice(
        {"voice": "Ryan", "voice_instruct": "a calm narrator"}
    )
    assert voice == "Ryan"  # label preserved
    assert render.resolve_voice_mode(voice_instruct, ref_audio) == "design"


def test_voice_label_and_mode_house_plus_instruct():
    voice, voice_instruct, ref_audio, _ = render.resolve_voice(
        {"voice": "house", "voice_instruct": "a calm narrator"}
    )
    assert voice == "custom"  # existing behaviour preserved
    assert render.resolve_voice_mode(voice_instruct, ref_audio) == "design"


def test_voice_label_and_mode_house_default(house_voice_files):
    voice, voice_instruct, ref_audio, _ = render.resolve_voice({})
    assert voice == "house"
    assert render.resolve_voice_mode(voice_instruct, ref_audio) == "clone"


def test_voice_label_and_mode_explicit_preset():
    voice, voice_instruct, ref_audio, _ = render.resolve_voice({"voice": "Ryan"})
    assert voice == "Ryan"
    assert render.resolve_voice_mode(voice_instruct, ref_audio) == "preset"


# --- resolve_font ---------------------------------------------------------


def test_resolve_font_env_override_wins(tmp_path, monkeypatch):
    # The env override is tried first, ahead of the macOS Futura path that exists
    # on the dev machine — so it must win even when Futura is present.
    fake = tmp_path / "myfont.ttf"
    fake.write_bytes(b"\x00")  # resolve_font only checks existence
    monkeypatch.setenv("DAILY_PODCAST_FONT", str(fake))

    assert render.resolve_font() == str(fake)


def test_resolve_font_dies_when_none_found(monkeypatch):
    monkeypatch.delenv("DAILY_PODCAST_FONT", raising=False)
    # Make every candidate path appear absent.
    monkeypatch.setattr(render.Path, "exists", lambda self: False)

    with pytest.raises(SystemExit):
        render.resolve_font()


# --- build_timeline_and_description --------------------------------------


def test_build_timeline_one_to_one_chapter_source_mapping(tmp_path, monkeypatch):
    segments = [
        {"title": "First", "source_url": "https://example.com/a"},
        {"title": "Second"},  # no source_url
        {"title": "Third", "source_url": "https://example.com/c"},
    ]
    paths = _paths(tmp_path, 3)
    episode = tmp_path / "episode.mp3"
    durations = {p: 30_000 for p in paths}
    durations[episode] = 95_000  # > sum of chapter starts
    _patch_durations(monkeypatch, durations)

    timeline, _ = render.build_timeline_and_description(
        segments,
        paths,
        silences_ms=[800, 800, 0],
        summary="s",
        episode_mp3=episode,
    )

    chapters = [it["chapter"] for it in timeline["items"] if "chapter" in it]
    links = [it["link"] for it in timeline["items"] if "link" in it]
    assert len(chapters) == len(segments)
    assert len(links) == sum(1 for s in segments if s.get("source_url"))
    assert [c["title"] for c in chapters] == ["First", "Second", "Third"]
    assert [link["url"] for link in links] == [
        "https://example.com/a",
        "https://example.com/c",
    ]


def test_build_timeline_link_companion_bounds(tmp_path, monkeypatch):
    segments = [{"title": "Only", "source_url": "https://example.com/x"}]
    paths = _paths(tmp_path, 1)
    episode = tmp_path / "episode.mp3"
    durations = {paths[0]: 40_000, episode: 40_000}
    _patch_durations(monkeypatch, durations)

    timeline, _ = render.build_timeline_and_description(
        segments,
        paths,
        silences_ms=[0],
        summary="s",
        episode_mp3=episode,
    )

    chapter = timeline["items"][0]["chapter"]
    link = timeline["items"][1]["link"]
    # link_start = cursor + max(1000, int(dur * 0.40))
    assert link["start_time_ms"] - chapter["start_time_ms"] >= 1000
    # link_dur = min(6000, max(2000, dur - 2000))
    assert 2000 <= link["duration_ms"] <= 6000


def test_description_escapes_title_special_chars(tmp_path, monkeypatch):
    segments = [{"title": 'She said "hi" & left'}]
    paths = _paths(tmp_path, 1)
    episode = tmp_path / "episode.mp3"
    _patch_durations(monkeypatch, {paths[0]: 40_000, episode: 40_000})

    _, description = render.build_timeline_and_description(
        segments,
        paths,
        silences_ms=[0],
        summary="s",
        episode_mp3=episode,
    )

    assert "She said &quot;hi&quot; &amp; left" in description
    assert 'She said "hi"' not in description


def test_description_escapes_url_ampersand_and_quote(tmp_path, monkeypatch):
    segments = [{"title": "T", "source_url": "https://x.com/p?a=1&b=2'q"}]
    paths = _paths(tmp_path, 1)
    episode = tmp_path / "episode.mp3"
    _patch_durations(monkeypatch, {paths[0]: 40_000, episode: 40_000})

    timeline, description = render.build_timeline_and_description(
        segments,
        paths,
        silences_ms=[0],
        summary="s",
        episode_mp3=episode,
    )

    assert "a=1&amp;b=2" in description  # query-string & is escaped
    assert "&#x27;" in description  # single quote escaped — can't close the href
    assert "a=1&b=2'q" not in description  # no raw, href-breaking form survives
    # The timeline carries the RAW url; escaping is description-only.
    assert timeline["items"][1]["link"]["url"] == "https://x.com/p?a=1&b=2'q"


def test_description_summary_passes_through_unescaped(tmp_path, monkeypatch):
    segments = [{"title": "T"}]
    paths = _paths(tmp_path, 1)
    episode = tmp_path / "episode.mp3"
    _patch_durations(monkeypatch, {paths[0]: 40_000, episode: 40_000})

    _, description = render.build_timeline_and_description(
        segments,
        paths,
        silences_ms=[0],
        summary="<b>bold</b> & raw",
        episode_mp3=episode,
    )

    assert "<b>bold</b> & raw" in description  # summary is HTML-by-contract


def test_description_under_cap_is_untruncated(tmp_path, monkeypatch):
    # A small description stays byte-identical to the pre-cap output: every
    # chapter block is present and no truncation log fires.
    segments = [
        {"title": "First", "source_url": "https://example.com/a"},
        {"title": "Second", "source_url": "https://example.com/b"},
    ]
    paths = _paths(tmp_path, 2)
    episode = tmp_path / "episode.mp3"
    durations = {p: 40_000 for p in paths}
    durations[episode] = 95_000
    _patch_durations(monkeypatch, durations)

    _, description = render.build_timeline_and_description(
        segments,
        paths,
        silences_ms=[800, 0],
        summary="hook",
        episode_mp3=episode,
    )

    assert len(description) <= render.SPOTIFY_SUMMARY_MAX_CHARS
    assert description.count("<p>") == 3  # summary + 2 chapters, nothing dropped
    assert "https://example.com/a" in description
    assert "https://example.com/b" in description


def test_description_over_cap_drops_trailing_blocks(tmp_path, monkeypatch, capsys):
    # Many fat chapter titles push the HTML over the cap; the tail blocks are
    # dropped (longest-suffix-first), the summary survives, and the result fits.
    n = 80
    fat = "X" * 200  # each chapter <p> is comfortably > the per-entry overhead
    segments = [{"title": f"{fat}-{i}", "source_url": f"https://example.com/{i}"} for i in range(n)]
    paths = _paths(tmp_path, n)
    episode = tmp_path / "episode.mp3"
    durations = {p: 40_000 for p in paths}
    durations[episode] = n * 41_000  # last chapter starts well before the end
    _patch_durations(monkeypatch, durations)

    _, description = render.build_timeline_and_description(
        segments,
        paths,
        silences_ms=[800] * (n - 1) + [0],
        summary="hook",
        episode_mp3=episode,
    )

    assert len(description) <= render.SPOTIFY_SUMMARY_MAX_CHARS
    # Summary <p> is always preserved; it leads the description.
    assert description.startswith("<p>hook</p>")
    # Truncation dropped from the END: the first chapter survives, the last does not.
    assert "https://example.com/0" in description
    assert f"https://example.com/{n - 1}" not in description
    # No mid-tag cut: the markup ends on a closed </p>.
    assert description.endswith("</p>")
    err = capsys.readouterr().err
    assert "dropped" in err and "trailing chapter block" in err


def test_description_timeline_unaffected_by_truncation(tmp_path, monkeypatch):
    # Even when the show-notes summary is trimmed, the timeline keeps EVERY chapter
    # and link — only the HTML listing shrinks.
    n = 80
    fat = "X" * 200
    segments = [{"title": f"{fat}-{i}", "source_url": f"https://example.com/{i}"} for i in range(n)]
    paths = _paths(tmp_path, n)
    episode = tmp_path / "episode.mp3"
    durations = {p: 40_000 for p in paths}
    durations[episode] = n * 41_000
    _patch_durations(monkeypatch, durations)

    timeline, description = render.build_timeline_and_description(
        segments,
        paths,
        silences_ms=[800] * (n - 1) + [0],
        summary="hook",
        episode_mp3=episode,
    )

    chapters = [it["chapter"] for it in timeline["items"] if "chapter" in it]
    links = [it["link"] for it in timeline["items"] if "link" in it]
    assert len(chapters) == n  # all audio chapters present despite trimmed notes
    assert len(links) == n
    assert len(description) <= render.SPOTIFY_SUMMARY_MAX_CHARS


def test_build_timeline_fatal_when_last_chapter_starts_after_episode_ends(tmp_path, monkeypatch):
    # Second chapter starts at 10_000ms but episode is only 9_000ms long.
    segments = [{"title": "A"}, {"title": "B"}]
    paths = _paths(tmp_path, 2)
    episode = tmp_path / "episode.mp3"
    _patch_durations(monkeypatch, {paths[0]: 10_000, paths[1]: 1_000, episode: 9_000})

    with pytest.raises(SystemExit):
        render.build_timeline_and_description(
            segments,
            paths,
            silences_ms=[0, 0],
            summary="s",
            episode_mp3=episode,
        )


# --- load_covered --------------------------------------------------------


def test_load_covered_returns_empty_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "COVERED_PATH", tmp_path / "nope.json")
    assert render.load_covered() == {}


def test_load_covered_treats_malformed_json_as_empty(tmp_path, monkeypatch):
    path = tmp_path / "covered.json"
    path.write_text("{not valid json")
    monkeypatch.setattr(render, "COVERED_PATH", path)

    assert render.load_covered() == {}


def test_load_covered_treats_non_object_json_as_empty(tmp_path, monkeypatch):
    # A JSON array is parseable but not a dict — still treat as empty rather
    # than crash callers that expect dict semantics.
    path = tmp_path / "covered.json"
    path.write_text("[1, 2, 3]")
    monkeypatch.setattr(render, "COVERED_PATH", path)

    assert render.load_covered() == {}


def test_load_covered_returns_dict_when_well_formed(tmp_path, monkeypatch):
    path = tmp_path / "covered.json"
    path.write_text('{"https://example.com/a": {"date": "2026-01-01"}}')
    monkeypatch.setattr(render, "COVERED_PATH", path)

    assert render.load_covered() == {"https://example.com/a": {"date": "2026-01-01"}}


# --- save_covered (atomic write) -----------------------------------------


def test_save_covered_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(render, "COVERED_PATH", tmp_path / "covered.json")
    data = {"https://example.com/a": {"date": "2026-01-01", "episode_uri": "spotify:episode:x"}}

    render.save_covered(data)

    assert render.load_covered() == data


def test_save_covered_atomic_keeps_prior_on_crash(tmp_path, monkeypatch):
    # A crash AFTER the temp write but BEFORE os.replace must leave the prior
    # covered.json untouched and not promote the partial temp file.
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    covered = tmp_path / "covered.json"
    monkeypatch.setattr(render, "COVERED_PATH", covered)
    prior = {"https://example.com/old": {"date": "2026-01-01"}}
    render.save_covered(prior)

    def boom(_src, _dst):
        raise KeyboardInterrupt

    monkeypatch.setattr(render.os, "replace", boom)
    with pytest.raises(KeyboardInterrupt):
        render.save_covered({"https://example.com/new": {"date": "2026-02-02"}})

    # Prior file intact, new data not promoted, and no temp turds left behind.
    assert json.loads(covered.read_text()) == prior
    leftover = [p.name for p in tmp_path.iterdir() if p.name != "covered.json"]
    assert leftover == [], f"temp files left behind: {leftover}"


def _iso_days_ago(n: int) -> str:
    return (render.dt.date.today() - render.dt.timedelta(days=n)).isoformat()


def test_save_covered_prunes_only_beyond_retention_window(tmp_path, monkeypatch, capsys):
    # 1 day and 179 days ago are inside the 180-day window -> kept.
    # 181 days ago is strictly older than the cutoff -> dropped.
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(render, "COVERED_PATH", tmp_path / "covered.json")
    data = {
        "https://example.com/recent": {"date": _iso_days_ago(1)},
        "https://example.com/edge": {"date": _iso_days_ago(179)},
        "https://example.com/old": {"date": _iso_days_ago(181)},
    }

    render.save_covered(data)

    written = render.load_covered()
    assert "https://example.com/recent" in written
    assert "https://example.com/edge" in written
    assert "https://example.com/old" not in written
    assert "pruned 1 covered.json" in capsys.readouterr().err


def test_save_covered_keeps_boundary_entry_at_exactly_retention_days(tmp_path, monkeypatch):
    # Cutoff is today - 180d; pruning is STRICTLY older, so exactly 180 days is kept.
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(render, "COVERED_PATH", tmp_path / "covered.json")
    data = {"https://example.com/boundary": {"date": _iso_days_ago(render.COVERED_RETENTION_DAYS)}}

    render.save_covered(data)

    assert "https://example.com/boundary" in render.load_covered()


def test_save_covered_keeps_malformed_and_missing_dates(tmp_path, monkeypatch):
    # Schema drift must never lose dedup state: a non-ISO date string and a
    # missing date field are both kept rather than dropped.
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(render, "COVERED_PATH", tmp_path / "covered.json")
    data = {
        "https://example.com/word": {"date": "yesterday"},
        "https://example.com/none": {"episode_uri": "spotify:episode:x"},
        "https://example.com/stale": {"date": _iso_days_ago(400)},
    }

    render.save_covered(data)

    written = render.load_covered()
    assert "https://example.com/word" in written  # malformed date kept
    assert "https://example.com/none" in written  # missing date kept
    assert "https://example.com/stale" not in written  # well-formed + old dropped


def test_save_covered_no_prune_log_when_nothing_dropped(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(render, "COVERED_PATH", tmp_path / "covered.json")
    render.save_covered({"https://example.com/a": {"date": _iso_days_ago(10)}})

    assert "pruned" not in capsys.readouterr().err


# --- resolve_cover_date ---------------------------------------------------


def test_resolve_cover_date_from_manifest():
    # A dated manifest reproduces its own date, not today's.
    assert render.resolve_cover_date({"date": "2026-05-20"}) == "May 20, 2026"


def test_resolve_cover_date_defaults_to_today():
    assert render.resolve_cover_date({}) == render.dt.date.today().strftime("%B %-d, %Y")


def test_resolve_cover_date_bad_value_dies():
    with pytest.raises(SystemExit):
        render.resolve_cover_date({"date": "not-a-date"})


# --- resume (idempotent post-upload) --------------------------------------


def _seed_uploaded_workdir(wd: Path, *, with_artifacts: bool = True) -> None:
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "uploaded.json").write_text(
        json.dumps(
            {
                "episode_uri": "spotify:episode:abc123",
                "title": "T",
                "voice": "house",
                "voice_mode": "clone",
            }
        )
    )
    if with_artifacts:
        (wd / "episode.mp3").write_bytes(b"x")
        (wd / "cover.jpg").write_bytes(b"x")
        (wd / "timeline.json").write_text(
            json.dumps({"items": [{"chapter": {"title": "A", "start_time_ms": 0}}]})
        )


def test_resume_skips_upload_and_runs_idempotent_tail(tmp_path, monkeypatch, capsys):
    wd = tmp_path / "wd"
    _seed_uploaded_workdir(wd)
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps(
            {
                "title": "T",
                "summary": "s",
                "segments": [{"text": "hi", "source_url": "https://example.com/a"}],
            }
        )
    )

    # Prove the upload + config are never touched on resume; record the tail.
    monkeypatch.setattr(
        render, "upload", lambda *a, **k: pytest.fail("upload must not run on resume")
    )
    monkeypatch.setattr(
        render, "load_config", lambda: pytest.fail("load_config must not run on resume")
    )
    calls = []
    monkeypatch.setattr(render, "set_timeline", lambda eid, tp: calls.append(("set_timeline", eid)))
    monkeypatch.setattr(render, "poll_ready", lambda eid: calls.append(("poll_ready", eid)))
    monkeypatch.setattr(render, "mp3_duration_ms", lambda p: 60_000)
    covered_path = tmp_path / "covered.json"
    monkeypatch.setattr(render, "COVERED_PATH", covered_path)
    monkeypatch.setattr(render, "RUN_LOG_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(
        sys, "argv", ["render.py", "--manifest", str(manifest), "--workdir", str(wd)]
    )

    assert render.main() == 0

    assert ("set_timeline", "abc123") in calls
    assert ("poll_ready", "abc123") in calls
    # The previously-orphaned URLs are now marked covered.
    covered = json.loads(covered_path.read_text())
    assert covered["https://example.com/a"]["episode_uri"] == "spotify:episode:abc123"
    out = json.loads(capsys.readouterr().out)
    assert out["resumed"] is True and out["status"] == "ready"


def test_resume_dies_when_artifact_missing(tmp_path, monkeypatch):
    wd = tmp_path / "wd"
    _seed_uploaded_workdir(wd, with_artifacts=False)  # marker only, no episode.mp3
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"title": "T", "summary": "s", "segments": [{"text": "hi"}]}))
    monkeypatch.setattr(render, "upload", lambda *a, **k: pytest.fail("upload must not run"))
    monkeypatch.setattr(render, "RUN_LOG_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(
        sys, "argv", ["render.py", "--manifest", str(manifest), "--workdir", str(wd)]
    )

    with pytest.raises(SystemExit):
        render.main()


# --- validate_manifest ----------------------------------------------------


def _valid_manifest() -> dict:
    return {
        "title": "Daily Digest",
        "summary": "hook",
        "segments": [
            {"text": "intro", "source_url": None},
            {"text": "body", "source_url": "https://example.com/a", "source_title": "A"},
        ],
    }


def test_validate_manifest_accepts_valid():
    render.validate_manifest(_valid_manifest())  # no raise


def test_validate_manifest_accepts_optional_fields():
    m = _valid_manifest()
    m.update(
        {
            "voice": "house",
            "date": "2026-05-20",
            "raw_text": True,
            "show_id": "spotify:show:1",
            "voice_instruct": "calm",
        }
    )
    render.validate_manifest(m)  # no raise


def test_validate_manifest_allows_label_voice_with_voice_instruct():
    # With voice_instruct set, resolve_voice treats `voice` as a free-form label, so
    # a non-preset name must NOT be rejected (regression guard for the A/B workflow).
    m = _valid_manifest()
    m.update({"voice": "NarratorBob", "voice_instruct": "a calm narrator"})
    render.validate_manifest(m)  # no raise


def test_validate_manifest_still_rejects_bad_voice_without_instruct():
    m = _valid_manifest()
    m["voice"] = "NarratorBob"  # no voice_instruct -> must still be a known preset
    with pytest.raises(SystemExit):
        render.validate_manifest(m)


def test_validate_manifest_allows_null_raw_text():
    m = _valid_manifest()
    m["raw_text"] = None  # explicit null behaves like absent
    render.validate_manifest(m)  # no raise


def test_validate_manifest_missing_segment_text(capsys):
    m = _valid_manifest()
    del m["segments"][1]["text"]
    with pytest.raises(SystemExit):
        render.validate_manifest(m)
    err = capsys.readouterr().err
    assert "segment[1]" in err and "text" in err


def test_validate_manifest_rejects_bad_voice(capsys):
    m = _valid_manifest()
    m["voice"] = "Bob"
    with pytest.raises(SystemExit):
        render.validate_manifest(m)
    assert "Bob" in capsys.readouterr().err


def test_validate_manifest_rejects_non_http_source_url():
    m = _valid_manifest()
    m["segments"][1]["source_url"] = "ftp://example.com/a"
    with pytest.raises(SystemExit):
        render.validate_manifest(m)


def test_validate_manifest_rejects_bad_date():
    m = _valid_manifest()
    m["date"] = "2026/05/20"
    with pytest.raises(SystemExit):
        render.validate_manifest(m)


# --- normalize_for_tts ----------------------------------------------------


def test_normalize_em_dash_and_smart_quotes():
    raw = "Hello — world’s “best” CLAUDE.md tip"
    # em dash -> hyphen, smart quotes -> ASCII, identifier left alone
    assert render.normalize_for_tts(raw) == 'Hello - world\'s "best" CLAUDE.md tip'


def test_normalize_strips_code_block_and_inline_backticks():
    out = render.normalize_for_tts("run ```python\nx=1\n``` then `pip install` it")
    assert "`" not in out
    assert "x=1" not in out  # fenced block content removed
    assert "pip install" in out  # inline content kept, backticks gone


def test_normalize_strips_urls_and_headings():
    out = render.normalize_for_tts("## Heading\nsee https://example.com/x for more")
    assert "https://" not in out
    assert not out.startswith("#")
    assert "Heading" in out and "for more" in out


def test_normalize_leaves_clean_text_unchanged():
    clean = "A plain sentence with numbers like 7 and an identifier CLAUDE.md."
    assert render.normalize_for_tts(clean) == clean


def test_normalize_url_flanked_by_em_dash_keeps_next_word():
    # Regression: a boundary-naive URL regex swallowed the word after a dash-flanked URL.
    out = render.normalize_for_tts("The fix—see https://github.com/x/y—landed today.")
    assert "https://" not in out
    assert "landed" in out


def test_normalize_strips_tilde_fence():
    out = render.normalize_for_tts("a ~~~\nx=1\n~~~ b")
    assert "x=1" not in out and "~~~" not in out


def test_normalize_is_idempotent():
    raw = "####### deep — “smart” `code` https://x.com/a — end"
    once = render.normalize_for_tts(raw)
    assert render.normalize_for_tts(once) == once  # second pass is a no-op
    assert "#" not in once  # a 7-hash run is fully stripped in one pass


# --- _prep_segment_text (raw_text bypass) ---------------------------------


def test_prep_segment_text_normalizes_by_default():
    assert render._prep_segment_text("a — b", raw_text=False) == "a - b"


def test_prep_segment_text_raw_bypasses_normalization():
    # raw_text=True keeps the em dash (strip only) for pre-normalized callers.
    assert render._prep_segment_text("  a — b  ", raw_text=True) == "a — b"


# --- segment cache key (#9) ----------------------------------------------


def test_cache_key_changes_with_text():
    a = render._segment_cache_key("hello world", "preset", "Ryan", None, None)
    b = render._segment_cache_key("hello there", "preset", "Ryan", None, None)
    assert a != b


def test_cache_key_changes_with_voice_name():
    a = render._segment_cache_key("hello", "preset", "Ryan", None, None)
    b = render._segment_cache_key("hello", "preset", "Aiden", None, None)
    assert a != b


def test_cache_key_changes_with_mode():
    # Same text + same voice label, but a different rendering engine must not collide.
    clone = render._segment_cache_key("hello", "clone", "house", "deadbeef", "ref")
    design = render._segment_cache_key("hello", "design", "house", None, "an instruct")
    assert clone != design


def test_cache_key_changes_with_ref_audio_fingerprint():
    # Re-recording the house clip (same text, same label) must invalidate.
    a = render._segment_cache_key("hello", "clone", "house", "fp-old", "ref text")
    b = render._segment_cache_key("hello", "clone", "house", "fp-new", "ref text")
    assert a != b


def test_cache_key_changes_with_ref_text():
    a = render._segment_cache_key("hello", "clone", "house", "fp", "transcript one")
    b = render._segment_cache_key("hello", "clone", "house", "fp", "transcript two")
    assert a != b


def test_cache_key_stable_for_same_inputs():
    a = render._segment_cache_key("hello", "clone", "house", "fp", "ref")
    b = render._segment_cache_key("hello", "clone", "house", "fp", "ref")
    assert a == b
    assert len(a) == 64  # sha256 hexdigest


def test_ref_audio_fingerprint_tracks_bytes(tmp_path):
    p = tmp_path / "house.wav"
    p.write_bytes(b"RIFFabc")
    fp1 = render._ref_audio_fingerprint(str(p))
    p.write_bytes(b"RIFFxyz")  # re-record the reference clip
    fp2 = render._ref_audio_fingerprint(str(p))
    assert fp1 != fp2
    assert render._ref_audio_fingerprint(None) is None


# --- render_segments cache behavior (#9) ---------------------------------


class _FakeAudioResult:
    """Mimics one mlx-audio generate() result: a `.audio` array-like."""

    def __init__(self, n: int = 4):
        self.audio = [0.0] * n


@pytest.fixture
def fake_tts(monkeypatch):
    """Install fake numpy / soundfile / mlx_audio modules and stub ffmpeg so
    render_segments runs without MLX, Metal, or a real encoder. The fake model
    records every text it is asked to render so a test can assert cache hits/misses.

    ffmpeg is replaced by a `run` stub that just `touch`es the target mp3, so the
    sidecar + cache-hit logic (which keys on mp3 existence) is exercised faithfully.
    """
    import types

    rendered_texts: list[str] = []
    model_loads: list[str] = []

    class FakeModel:
        def generate(self, text, **kw):
            rendered_texts.append(text)
            return [_FakeAudioResult()]

        def generate_voice_design(self, text, **kw):
            rendered_texts.append(text)
            return [_FakeAudioResult()]

    fake_np = types.ModuleType("numpy")
    fake_np.concatenate = lambda arrs: [x for a in arrs for x in a]
    fake_np.array = lambda x: list(x)
    monkeypatch.setitem(sys.modules, "numpy", fake_np)

    fake_sf = types.ModuleType("soundfile")
    fake_sf.write = lambda path, audio, sr: Path(path).write_bytes(b"\x00")
    monkeypatch.setitem(sys.modules, "soundfile", fake_sf)

    mlx_audio = types.ModuleType("mlx_audio")
    mlx_tts = types.ModuleType("mlx_audio.tts")
    mlx_utils = types.ModuleType("mlx_audio.tts.utils")

    def _load_model(model_id):
        model_loads.append(model_id)
        return FakeModel()

    mlx_utils.load_model = _load_model
    monkeypatch.setitem(sys.modules, "mlx_audio", mlx_audio)
    monkeypatch.setitem(sys.modules, "mlx_audio.tts", mlx_tts)
    monkeypatch.setitem(sys.modules, "mlx_audio.tts.utils", mlx_utils)

    def fake_run(cmd, **kw):
        # ffmpeg -i wav ... mp3 — the mp3 path is the last positional arg.
        Path(cmd[-1]).write_bytes(b"\x00")
        return None

    monkeypatch.setattr(render, "run", fake_run)

    return types.SimpleNamespace(rendered_texts=rendered_texts, model_loads=model_loads)


def _segs(*texts):
    return [{"text": t} for t in texts]


def test_render_segments_writes_sidecar_per_segment(tmp_path, fake_tts):
    segs = _segs("alpha intro", "beta body")
    paths = render.render_segments(segs, "Ryan", tmp_path)

    assert [p.name for p in paths] == ["seg_01.mp3", "seg_02.mp3"]
    for i in (1, 2):
        sidecar = tmp_path / f"seg_{i:02d}.json"
        assert sidecar.exists()
        meta = json.loads(sidecar.read_text())
        assert "key" in meta and len(meta["key"]) == 64
    # Both rendered fresh on a cold workdir.
    assert fake_tts.rendered_texts == ["alpha intro", "beta body"]


def test_render_segments_full_cache_hit_skips_model_load(tmp_path, fake_tts):
    segs = _segs("alpha intro", "beta body")
    render.render_segments(segs, "Ryan", tmp_path)
    assert len(fake_tts.model_loads) == 1  # cold run loaded the model once

    # Re-run with the SAME workdir + manifest: every segment is cached.
    fake_tts.rendered_texts.clear()
    fake_tts.model_loads.clear()
    paths = render.render_segments(segs, "Ryan", tmp_path)

    assert [p.name for p in paths] == ["seg_01.mp3", "seg_02.mp3"]
    assert fake_tts.rendered_texts == []  # nothing re-rendered
    assert fake_tts.model_loads == []  # model load skipped entirely (acceptance)


def test_render_segments_partial_cache_rerenders_only_changed(tmp_path, fake_tts):
    segs = _segs("alpha intro", "beta body", "gamma outro")
    render.render_segments(segs, "Ryan", tmp_path)

    # Change only the middle segment's text; the other two stay cached.
    fake_tts.rendered_texts.clear()
    fake_tts.model_loads.clear()
    segs2 = _segs("alpha intro", "beta body REVISED", "gamma outro")
    render.render_segments(segs2, "Ryan", tmp_path)

    assert fake_tts.rendered_texts == ["beta body REVISED"]  # only the change
    assert len(fake_tts.model_loads) == 1  # partial → model still loads once


def test_render_segments_voice_change_invalidates_all(tmp_path, fake_tts):
    segs = _segs("alpha intro", "beta body")
    render.render_segments(segs, "house", tmp_path, ref_audio=None, ref_text=None)
    # Render under a clone voice (different mode), then switch to a preset.
    fake_tts.rendered_texts.clear()
    render.render_segments(segs, "Ryan", tmp_path)
    # Different voice/mode → all entries invalidated, both re-rendered.
    assert fake_tts.rendered_texts == ["alpha intro", "beta body"]


def test_render_segments_ref_audio_change_invalidates_clone_cache(tmp_path, fake_tts):
    ref = tmp_path / "house.wav"
    ref.write_bytes(b"RIFF-v1")
    segs = _segs("alpha intro", "beta body")
    render.render_segments(segs, "house", tmp_path, ref_audio=str(ref), ref_text="ref")

    # Re-record the house clip; same text + label, but the bytes changed.
    ref.write_bytes(b"RIFF-v2-different")
    fake_tts.rendered_texts.clear()
    render.render_segments(segs, "house", tmp_path, ref_audio=str(ref), ref_text="ref")
    assert fake_tts.rendered_texts == ["alpha intro", "beta body"]  # all re-rendered


def test_render_segments_cache_hit_logs_reuse(tmp_path, fake_tts, capsys):
    segs = _segs("alpha intro")
    render.render_segments(segs, "Ryan", tmp_path)
    capsys.readouterr()  # drop cold-run logs
    render.render_segments(segs, "Ryan", tmp_path)
    err = capsys.readouterr().err
    assert "cache" in err.lower()  # a line distinguishes reuse from a fresh render


def test_render_segments_stale_mp3_without_sidecar_rerenders(tmp_path, fake_tts):
    # A pre-existing seg_01.mp3 with no sidecar (older run / partial write) must not
    # be trusted as a cache hit — content identity is unknown, so re-render.
    (tmp_path / "seg_01.mp3").write_bytes(b"stale")
    segs = _segs("alpha intro")
    render.render_segments(segs, "Ryan", tmp_path)
    assert fake_tts.rendered_texts == ["alpha intro"]


# --- in-flight episode log (#37) -----------------------------------------


def test_inflight_write_and_clear_roundtrip(tmp_path, monkeypatch):
    inflight = tmp_path / "inflight.json"
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(render, "INFLIGHT_PATH", inflight)

    render._write_inflight(
        episode_uri="spotify:episode:abc",
        title="T",
        workdir=tmp_path / "wd",
        source_urls=["https://example.com/a", "https://example.com/b"],
    )
    data = json.loads(inflight.read_text())
    assert data["episode_uri"] == "spotify:episode:abc"
    assert data["source_urls"] == ["https://example.com/a", "https://example.com/b"]
    assert data["title"] == "T"

    render._clear_inflight()
    assert not inflight.exists()


def test_clear_inflight_is_noop_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "INFLIGHT_PATH", tmp_path / "nope.json")
    render._clear_inflight()  # no raise


def test_load_inflight_treats_malformed_as_none(tmp_path, monkeypatch):
    p = tmp_path / "inflight.json"
    p.write_text("{not json")
    monkeypatch.setattr(render, "INFLIGHT_PATH", p)
    assert render._load_inflight() is None


def test_load_inflight_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "INFLIGHT_PATH", tmp_path / "nope.json")
    assert render._load_inflight() is None


def test_recover_inflight_marks_urls_covered_and_clears(tmp_path, monkeypatch):
    # Simulate: a prior run uploaded but died before dedup. A *different* workdir
    # (gone) — recovery must still mark the URLs covered so curation can't re-pick
    # them, then clear the log.
    inflight = tmp_path / "inflight.json"
    covered = tmp_path / "covered.json"
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(render, "INFLIGHT_PATH", inflight)
    monkeypatch.setattr(render, "COVERED_PATH", covered)
    inflight.write_text(
        json.dumps(
            {
                "episode_uri": "spotify:episode:mon",
                "title": "Monday",
                "workdir": str(tmp_path / "gone-workdir"),  # does not exist
                "source_urls": ["https://example.com/mon1", "https://example.com/mon2"],
            }
        )
    )
    # The server tail must NOT be attempted when the workdir/timeline is gone.
    monkeypatch.setattr(
        render, "set_timeline", lambda *a, **k: pytest.fail("no timeline set when workdir gone")
    )
    monkeypatch.setattr(render, "poll_ready", lambda *a, **k: pytest.fail("no poll when gone"))

    render._recover_inflight()

    cov = json.loads(covered.read_text())
    assert cov["https://example.com/mon1"]["episode_uri"] == "spotify:episode:mon"
    assert cov["https://example.com/mon2"]["episode_uri"] == "spotify:episode:mon"
    assert not inflight.exists()  # cleared after dedup


def test_recover_inflight_reruns_tail_when_workdir_present(tmp_path, monkeypatch):
    # Monday's workdir survived: recovery re-runs set_timeline + poll_ready before
    # marking covered, so a genuinely pending episode is finished.
    inflight = tmp_path / "inflight.json"
    covered = tmp_path / "covered.json"
    wd = tmp_path / "mon-wd"
    wd.mkdir()
    (wd / "timeline.json").write_text(json.dumps({"items": []}))
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(render, "INFLIGHT_PATH", inflight)
    monkeypatch.setattr(render, "COVERED_PATH", covered)
    inflight.write_text(
        json.dumps(
            {
                "episode_uri": "spotify:episode:mon",
                "title": "Monday",
                "workdir": str(wd),
                "source_urls": ["https://example.com/mon1"],
            }
        )
    )
    calls = []
    monkeypatch.setattr(render, "set_timeline", lambda eid, tp: calls.append(("set", eid)))
    monkeypatch.setattr(render, "poll_ready", lambda eid: calls.append(("poll", eid)))

    render._recover_inflight()

    assert ("set", "mon") in calls
    assert ("poll", "mon") in calls
    cov = json.loads(covered.read_text())
    assert "https://example.com/mon1" in cov
    assert not inflight.exists()


def test_recover_inflight_keeps_log_when_recovery_crashes(tmp_path, monkeypatch):
    # A crash mid-recovery (poll_ready raises) must leave inflight.json intact for
    # the next attempt, and must NOT have marked covered yet (dedup runs last).
    inflight = tmp_path / "inflight.json"
    covered = tmp_path / "covered.json"
    wd = tmp_path / "mon-wd"
    wd.mkdir()
    (wd / "timeline.json").write_text(json.dumps({"items": []}))
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(render, "INFLIGHT_PATH", inflight)
    monkeypatch.setattr(render, "COVERED_PATH", covered)
    payload = {
        "episode_uri": "spotify:episode:mon",
        "title": "Monday",
        "workdir": str(wd),
        "source_urls": ["https://example.com/mon1"],
    }
    inflight.write_text(json.dumps(payload))
    monkeypatch.setattr(render, "set_timeline", lambda eid, tp: None)

    def boom(eid):
        raise RuntimeError("spotify down")

    monkeypatch.setattr(render, "poll_ready", boom)

    with pytest.raises(RuntimeError):
        render._recover_inflight()

    assert json.loads(inflight.read_text()) == payload  # log intact for retry
    assert not covered.exists()  # dedup never ran


def test_recover_inflight_noop_when_log_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "INFLIGHT_PATH", tmp_path / "nope.json")
    monkeypatch.setattr(render, "set_timeline", lambda *a, **k: pytest.fail("nothing to recover"))
    render._recover_inflight()  # no raise, no work


def test_main_recovers_inflight_before_fresh_render(tmp_path, monkeypatch):
    # End-to-end of the cron gap: Monday's upload is in-flight; Tuesday runs a fresh
    # manifest in a DIFFERENT workdir. Tuesday's run must recover Monday first
    # (Monday's URLs covered) and then proceed to ship Tuesday (its URLs covered too).
    inflight = tmp_path / "inflight.json"
    covered = tmp_path / "covered.json"
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(render, "INFLIGHT_PATH", inflight)
    monkeypatch.setattr(render, "COVERED_PATH", covered)
    monkeypatch.setattr(render, "RUN_LOG_PATH", tmp_path / "runs.jsonl")
    inflight.write_text(
        json.dumps(
            {
                "episode_uri": "spotify:episode:mon",
                "title": "Monday",
                "workdir": str(tmp_path / "gone"),  # Monday's tmp workdir is gone
                "source_urls": ["https://example.com/monday"],
            }
        )
    )

    manifest = tmp_path / "tue.json"
    manifest.write_text(
        json.dumps(
            {
                "title": "Tuesday",
                "summary": "s",
                "show_id": "spotify:show:1",
                "segments": [{"text": "hi", "source_url": "https://example.com/tuesday"}],
            }
        )
    )
    # Stub the whole render+upload tail so the test stays unit-light; the point is
    # the recovery-then-ship ordering and that BOTH days end up covered.
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
        lambda *a, **k: ({"items": [{"chapter": {"title": "A", "start_time_ms": 0}}]}, "<p>d</p>"),
    )
    monkeypatch.setattr(render, "upload", lambda *a, **k: "spotify:episode:tue")
    monkeypatch.setattr(render, "set_timeline", lambda *a, **k: None)
    monkeypatch.setattr(render, "poll_ready", lambda *a, **k: None)
    monkeypatch.setattr(render, "maybe_publish_r2", lambda *a, **k: False)
    monkeypatch.setattr(render, "mp3_duration_ms", lambda p: 60_000)
    monkeypatch.setattr(
        sys,
        "argv",
        ["render.py", "--manifest", str(manifest), "--workdir", str(tmp_path / "tue-wd")],
    )

    assert render.main() == 0

    cov = json.loads(covered.read_text())
    # Monday recovered (no duplicate next-day re-ship) AND Tuesday shipped.
    assert cov["https://example.com/monday"]["episode_uri"] == "spotify:episode:mon"
    assert cov["https://example.com/tuesday"]["episode_uri"] == "spotify:episode:tue"
    # In-flight log cleared after Tuesday's own dedup completed.
    assert not inflight.exists()


# --- parse_loudnorm (#21) -------------------------------------------------

# A realistic ffmpeg loudnorm=print_format=json stderr block (real audio, not silent).
_LOUDNORM_STDERR = """\
Input #0, mp3, from 'episode_raw.mp3':
  Duration: 00:05:00.00, start: 0.025057, bitrate: 192 kb/s
[Parsed_loudnorm_0 @ 0x600003abc000]
{
\t"input_i" : "-19.43",
\t"input_tp" : "-3.21",
\t"input_lra" : "7.10",
\t"input_thresh" : "-29.51",
\t"output_i" : "-16.02",
\t"output_tp" : "-1.49",
\t"output_lra" : "6.90",
\t"output_thresh" : "-26.10",
\t"normalization_type" : "dynamic",
\t"target_offset" : "0.02"
}
size=N/A time=00:05:00.00 bitrate=N/A speed= 50x
"""


def test_parse_loudnorm_extracts_measured_lufs():
    out = render.parse_loudnorm(_LOUDNORM_STDERR)
    assert out is not None
    # output_i is the Spotify-target signal (-16 LUFS mono).
    assert out["output_i"] == -16.02
    assert out["input_i"] == -19.43
    assert out["output_tp"] == -1.49
    assert out["output_lra"] == 6.90


def test_parse_loudnorm_returns_none_when_block_absent():
    assert render.parse_loudnorm("ffmpeg ran but printed no loudnorm json") is None


def test_parse_loudnorm_returns_none_on_empty_stderr():
    assert render.parse_loudnorm("") is None


def test_parse_loudnorm_inf_values_become_null_not_failure():
    # Silent input makes ffmpeg report "-inf"/"inf"; we must not crash, and those
    # fields land null while the dict itself is still returned.
    silent = """[Parsed_loudnorm_0 @ 0x0]
{
\t"input_i" : "-inf",
\t"input_tp" : "-inf",
\t"input_lra" : "0.00",
\t"output_i" : "-inf",
\t"output_tp" : "-inf",
\t"output_lra" : "0.00",
\t"target_offset" : "inf"
}
"""
    out = render.parse_loudnorm(silent)
    assert out is not None
    assert out["output_i"] is None  # "-inf" → null, not a crash
    assert out["output_lra"] == 0.0


def test_parse_loudnorm_picks_last_block_when_multiple_json():
    # A preceding bracketed JSON-ish line must not shadow the real measurement block.
    noisy = '{"unrelated": 1}\n' + _LOUDNORM_STDERR
    out = render.parse_loudnorm(noisy)
    assert out is not None and out["output_i"] == -16.02


# --- prune_workdirs (#21, DESTRUCTIVE) ------------------------------------


def _make_workdir(base: Path, name: str, *, age_days: float, files: int = 1) -> Path:
    wd = base / name
    wd.mkdir(parents=True)
    for i in range(files):
        (wd / f"f{i}.bin").write_bytes(b"x" * 100)
    when = time.time() - age_days * 86400
    os.utime(wd, (when, when))
    return wd


def test_prune_workdirs_deletes_only_old_matching_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "TMP_BASE", tmp_path)
    old = _make_workdir(tmp_path, "daily-podcast-old", age_days=10)
    young = _make_workdir(tmp_path, "daily-podcast-young", age_days=1)
    unrelated = _make_workdir(tmp_path, "some-other-tool-xyz", age_days=30)

    result = render.prune_workdirs(7)

    assert not old.exists()  # older than 7d, matching prefix → deleted
    assert young.exists()  # within window → kept
    assert unrelated.exists()  # wrong prefix → never touched
    assert result["count"] == 1
    assert result["freed_bytes"] == 100


def test_prune_workdirs_excludes_active_workdir(tmp_path, monkeypatch):
    # The active per-date workdir can itself be old and match the glob; it must
    # NEVER be deleted while the run is using it. This is the core safety invariant.
    monkeypatch.setattr(render, "TMP_BASE", tmp_path)
    active = _make_workdir(tmp_path, "daily-podcast-today", age_days=30)
    stale = _make_workdir(tmp_path, "daily-podcast-stale", age_days=30)

    result = render.prune_workdirs(7, exclude=active)

    assert active.exists()  # excluded by resolved path
    assert not stale.exists()
    assert result["count"] == 1


def test_prune_workdirs_refuses_nonpositive_n(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(render, "TMP_BASE", tmp_path)
    old = _make_workdir(tmp_path, "daily-podcast-old", age_days=999)

    assert render.prune_workdirs(0) is None
    assert render.prune_workdirs(-5) is None
    # Nothing deleted — a 0/negative age must never select everything.
    assert old.exists()
    assert "must be a positive day count" in capsys.readouterr().err


def test_prune_workdirs_skips_symlinks(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "TMP_BASE", tmp_path)
    real = _make_workdir(tmp_path, "real-target", age_days=30)
    link = tmp_path / "daily-podcast-link"
    link.symlink_to(real)
    old_when = time.time() - 30 * 86400
    os.utime(link, (old_when, old_when), follow_symlinks=False)

    result = render.prune_workdirs(7)

    # The symlink (even matching prefix + old) is skipped, so its target survives —
    # we never follow a link out of TMP_BASE to delete something.
    assert real.exists()
    assert link.is_symlink()
    assert result["count"] == 0


def test_prune_workdirs_noop_when_base_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "TMP_BASE", tmp_path / "does-not-exist")
    result = render.prune_workdirs(7)
    assert result == {"count": 0, "freed_bytes": 0}


# --- run_selftest (#21) ---------------------------------------------------


def _selftest_env(monkeypatch, tmp_path, *, shows_rc=0, shows_out='{"shows":[]}', config=True):
    """Wire selftest's external seams to a healthy default; callers flip one to fail."""
    monkeypatch.setattr(render.shutil, "which", lambda tool: f"/usr/bin/{tool}")

    def fake_subprocess_run(cmd, **kwargs):
        import subprocess as _sp

        return _sp.CompletedProcess(cmd, shows_rc, stdout=shows_out, stderr="")

    monkeypatch.setattr(render.subprocess, "run", fake_subprocess_run)

    cfg = tmp_path / "config.json"
    if config:
        cfg.write_text(json.dumps({"show_id": "spotify:show:1"}))
    monkeypatch.setattr(render, "CONFIG_PATH", cfg)
    # House voice: point the bundled paths at real files so the check passes.
    audio = tmp_path / "house.wav"
    text = tmp_path / "house.txt"
    audio.write_bytes(b"RIFF")
    text.write_text("hi")
    monkeypatch.setattr(render, "BUNDLED_HOUSE_AUDIO", audio)
    monkeypatch.setattr(render, "BUNDLED_HOUSE_TEXT", text)
    monkeypatch.setattr(render, "USER_HOUSE_AUDIO", tmp_path / "nouser.wav")
    monkeypatch.setattr(render, "USER_HOUSE_TEXT", tmp_path / "nouser.txt")


def test_selftest_all_pass_exits_zero(tmp_path, monkeypatch, capsys):
    _selftest_env(monkeypatch, tmp_path)
    rc = render.run_selftest()
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "ok"
    assert all(c["ok"] for c in summary["checks"])
    # Ordered checks: ffmpeg, ffprobe, auth, config, house-voice.
    names = [c["name"] for c in summary["checks"]]
    assert names == ["ffmpeg", "ffprobe", "save-to-spotify-auth", "config", "house-voice"]


def test_selftest_fails_when_auth_expired(tmp_path, monkeypatch, capsys):
    # save-to-spotify shows exits non-zero (auth expired) → overall failure, exit 1.
    _selftest_env(monkeypatch, tmp_path, shows_rc=1, shows_out="")
    rc = render.run_selftest()
    assert rc == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "failed"
    auth = next(c for c in summary["checks"] if c["name"] == "save-to-spotify-auth")
    assert auth["ok"] is False
    assert "exited 1" in auth["detail"]


def test_selftest_fails_when_config_missing_show_id(tmp_path, monkeypatch, capsys):
    _selftest_env(monkeypatch, tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({}))  # no show_id
    rc = render.run_selftest()
    assert rc == 1
    summary = json.loads(capsys.readouterr().out)
    cfg = next(c for c in summary["checks"] if c["name"] == "config")
    assert cfg["ok"] is False and "show_id" in cfg["detail"]


def test_selftest_fails_when_ffmpeg_missing(tmp_path, monkeypatch, capsys):
    _selftest_env(monkeypatch, tmp_path)
    monkeypatch.setattr(render.shutil, "which", lambda tool: None)  # nothing on PATH
    rc = render.run_selftest()
    assert rc == 1
    summary = json.loads(capsys.readouterr().out)
    ffmpeg = next(c for c in summary["checks"] if c["name"] == "ffmpeg")
    assert ffmpeg["ok"] is False


def test_selftest_does_not_load_model_by_default(tmp_path, monkeypatch, capsys):
    _selftest_env(monkeypatch, tmp_path)
    render.run_selftest()  # load_model defaults False
    summary = json.loads(capsys.readouterr().out)
    # No model-load check unless --load-model is passed (keeps it <5s).
    assert "model-load" not in [c["name"] for c in summary["checks"]]


def test_main_selftest_branch_does_not_require_manifest(tmp_path, monkeypatch):
    # --selftest is mutually exclusive with --manifest and must short-circuit before
    # any manifest/config load. main() returns selftest's exit code directly.
    _selftest_env(monkeypatch, tmp_path)
    monkeypatch.setattr(render, "load_config", lambda: pytest.fail("selftest must not load config"))
    monkeypatch.setattr(sys, "argv", ["render.py", "--selftest"])
    assert render.main() == 0


def test_main_rejects_manifest_and_selftest_together(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["render.py", "--manifest", "m.json", "--selftest"])
    with pytest.raises(SystemExit):
        render.main()  # argparse mutually-exclusive group rejects both


def test_main_requires_one_of_manifest_or_selftest(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["render.py"])
    with pytest.raises(SystemExit):
        render.main()  # the group is required


# --- run log (#18) --------------------------------------------------------


def test_write_run_log_appends_one_parseable_line(tmp_path, monkeypatch):
    log_path = tmp_path / "runs.jsonl"
    monkeypatch.setattr(render, "RUN_LOG_PATH", log_path)

    render.write_run_log({"status": "ready", "title": "A"})
    render.write_run_log({"status": "failed", "title": "B"})

    lines = log_path.read_text().splitlines()
    assert len(lines) == 2  # append-only — never clobbered to a single line
    rec0 = json.loads(lines[0])
    rec1 = json.loads(lines[1])
    assert rec0["status"] == "ready" and rec1["status"] == "failed"
    # timestamp is stamped automatically.
    assert rec0["timestamp"] is not None


def test_write_run_log_swallows_errors(tmp_path, monkeypatch):
    # An unwritable path must not raise — observability can't sink a shipped episode.
    monkeypatch.setattr(render, "RUN_LOG_PATH", tmp_path / "nope" / "x" / "runs.jsonl")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(render.Path, "mkdir", boom)
    render.write_run_log({"status": "ready"})  # no raise


def test_new_run_record_has_full_stable_key_set():
    rec = render._new_run_record()
    assert set(rec) == set(render.RUN_LOG_FIELDS)
    # Every field is null until a caller fills it (null, not absent).
    assert all(v is None for v in rec.values())


def test_resolve_render_sha_returns_string():
    # In this git checkout it should be a short SHA; the contract is just a non-empty str.
    sha = render.resolve_render_sha()
    assert isinstance(sha, str) and sha


def _full_render_manifest(tmp_path) -> Path:
    m = tmp_path / "m.json"
    m.write_text(
        json.dumps(
            {
                "title": "Run Log Episode",
                "summary": "s",
                "show_id": "spotify:show:1",
                "segments": [{"text": "hi", "source_url": "https://example.com/a"}],
            }
        )
    )
    return m


def _stub_full_render(monkeypatch, tmp_path, *, loudnorm=None):
    """Stub the heavy render+upload seams so a main() run exercises only the
    orchestration + run-log plumbing."""
    monkeypatch.setattr(render, "load_config", lambda: {"show_id": "spotify:show:1"})
    monkeypatch.setattr(render, "render_segments", lambda *a, **k: [tmp_path / "seg_01.mp3"])
    monkeypatch.setattr(render, "plan_silences", lambda paths: [0])
    monkeypatch.setattr(
        render, "concat_and_normalize", lambda *a, **k: (tmp_path / "episode.mp3", loudnorm)
    )
    monkeypatch.setattr(render, "build_cover", lambda *a, **k: None)
    monkeypatch.setattr(
        render,
        "build_timeline_and_description",
        lambda *a, **k: ({"items": [{"chapter": {"title": "A", "start_time_ms": 0}}]}, "<p>d</p>"),
    )
    monkeypatch.setattr(render, "upload", lambda *a, **k: "spotify:episode:xyz")
    monkeypatch.setattr(render, "set_timeline", lambda *a, **k: None)
    monkeypatch.setattr(render, "poll_ready", lambda *a, **k: None)
    monkeypatch.setattr(render, "maybe_publish_r2", lambda *a, **k: False)
    monkeypatch.setattr(render, "mp3_duration_ms", lambda p: 60_000)


def test_successful_run_appends_ready_record_with_loudnorm(tmp_path, monkeypatch):
    log_path = tmp_path / "runs.jsonl"
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(render, "RUN_LOG_PATH", log_path)
    monkeypatch.setattr(render, "COVERED_PATH", tmp_path / "covered.json")
    monkeypatch.setattr(render, "INFLIGHT_PATH", tmp_path / "inflight.json")
    lufs = {"input_i": -19.4, "output_i": -16.0, "output_tp": -1.5, "output_lra": 6.9}
    _stub_full_render(monkeypatch, tmp_path, loudnorm=lufs)
    manifest = _full_render_manifest(tmp_path)
    wd = tmp_path / "wd"  # explicit workdir so it isn't auto-deleted mid-test
    monkeypatch.setattr(
        sys, "argv", ["render.py", "--manifest", str(manifest), "--workdir", str(wd)]
    )

    assert render.main() == 0

    line = log_path.read_text().splitlines()[-1]
    rec = json.loads(line)
    assert rec["status"] == "ready"
    assert rec["episode_uri"] == "spotify:episode:xyz"
    assert rec["title"] == "Run Log Episode"
    assert rec["loudnorm"]["output_i"] == -16.0  # LUFS landed in the run log (#21)
    assert rec["segment_count"] == 1
    assert rec["resumed"] is False
    assert set(rec) == set(render.RUN_LOG_FIELDS)  # full stable schema


def test_dry_run_appends_dry_run_record(tmp_path, monkeypatch):
    log_path = tmp_path / "runs.jsonl"
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(render, "RUN_LOG_PATH", log_path)
    _stub_full_render(monkeypatch, tmp_path, loudnorm=None)
    # dry-run must NOT upload; prove it by making upload fail loudly.
    monkeypatch.setattr(render, "upload", lambda *a, **k: pytest.fail("dry-run must not upload"))
    manifest = _full_render_manifest(tmp_path)
    monkeypatch.setattr(sys, "argv", ["render.py", "--manifest", str(manifest), "--dry-run"])

    assert render.main() == 0

    rec = json.loads(log_path.read_text().splitlines()[-1])
    assert rec["status"] == "dry-run"
    assert rec["episode_uri"] is None
    assert rec["title"] == "Run Log Episode"


def test_failed_run_appends_failed_record_with_error(tmp_path, monkeypatch):
    log_path = tmp_path / "runs.jsonl"
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(render, "RUN_LOG_PATH", log_path)
    monkeypatch.setattr(render, "load_config", lambda: {"show_id": "spotify:show:1"})
    monkeypatch.setattr(render, "render_segments", lambda *a, **k: [tmp_path / "seg_01.mp3"])
    monkeypatch.setattr(render, "plan_silences", lambda paths: [0])
    # Blow up inside the render with a die() so the failure path captures the message.
    monkeypatch.setattr(
        render, "concat_and_normalize", lambda *a, **k: render.die("ffmpeg exploded")
    )
    monkeypatch.setattr(render, "mp3_duration_ms", lambda p: 60_000)
    manifest = _full_render_manifest(tmp_path)
    monkeypatch.setattr(sys, "argv", ["render.py", "--manifest", str(manifest)])

    with pytest.raises(SystemExit):
        render.main()

    rec = json.loads(log_path.read_text().splitlines()[-1])
    assert rec["status"] == "failed"
    assert rec["error_message"] == "ffmpeg exploded"  # die()'s message, not just exit code
    assert rec["title"] == "Run Log Episode"  # fields learned before the crash persist


def test_run_context_cleared_after_run(tmp_path, monkeypatch):
    # _RUN_CTX must not leak between runs — a later direct die() in a test (no active
    # run) must not write to runs.jsonl.
    log_path = tmp_path / "runs.jsonl"
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(render, "RUN_LOG_PATH", log_path)
    _stub_full_render(monkeypatch, tmp_path)
    manifest = _full_render_manifest(tmp_path)
    wd = tmp_path / "wd"
    monkeypatch.setattr(
        sys, "argv", ["render.py", "--manifest", str(manifest), "--workdir", str(wd)]
    )
    render.main()
    assert render._RUN_CTX is None


def test_successful_auto_workdir_is_deleted(tmp_path, monkeypatch):
    # Default (no --keep-workdir, auto workdir): the workdir is removed on success.
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(render, "RUN_LOG_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(render, "COVERED_PATH", tmp_path / "covered.json")
    monkeypatch.setattr(render, "INFLIGHT_PATH", tmp_path / "inflight.json")

    created: list[Path] = []
    real_mkdtemp = render.tempfile.mkdtemp

    def tracking_mkdtemp(*a, **k):
        k.setdefault("dir", str(tmp_path))
        d = real_mkdtemp(*a, **k)
        created.append(Path(d))
        return d

    monkeypatch.setattr(render.tempfile, "mkdtemp", tracking_mkdtemp)
    _stub_full_render(monkeypatch, tmp_path)
    manifest = _full_render_manifest(tmp_path)
    monkeypatch.setattr(sys, "argv", ["render.py", "--manifest", str(manifest)])

    assert render.main() == 0
    assert created and not created[0].exists()  # auto workdir deleted on success


def test_keep_workdir_preserves_auto_workdir(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(render, "RUN_LOG_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(render, "COVERED_PATH", tmp_path / "covered.json")
    monkeypatch.setattr(render, "INFLIGHT_PATH", tmp_path / "inflight.json")

    created: list[Path] = []
    real_mkdtemp = render.tempfile.mkdtemp

    def tracking_mkdtemp(*a, **k):
        k.setdefault("dir", str(tmp_path))
        d = real_mkdtemp(*a, **k)
        created.append(Path(d))
        return d

    monkeypatch.setattr(render.tempfile, "mkdtemp", tracking_mkdtemp)
    _stub_full_render(monkeypatch, tmp_path)
    manifest = _full_render_manifest(tmp_path)
    monkeypatch.setattr(sys, "argv", ["render.py", "--manifest", str(manifest), "--keep-workdir"])

    assert render.main() == 0
    assert created and created[0].exists()  # --keep-workdir retains it


def test_explicit_workdir_never_auto_deleted(tmp_path, monkeypatch):
    # An explicit --workdir backs the documented resume/no-op path, so it is kept
    # even without --keep-workdir.
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(render, "RUN_LOG_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(render, "COVERED_PATH", tmp_path / "covered.json")
    monkeypatch.setattr(render, "INFLIGHT_PATH", tmp_path / "inflight.json")
    _stub_full_render(monkeypatch, tmp_path)
    manifest = _full_render_manifest(tmp_path)
    wd = tmp_path / "explicit-wd"
    monkeypatch.setattr(
        sys, "argv", ["render.py", "--manifest", str(manifest), "--workdir", str(wd)]
    )

    assert render.main() == 0
    assert wd.exists()  # explicit workdir preserved


def test_prune_workdirs_flag_runs_before_render(tmp_path, monkeypatch):
    # --prune-workdirs N runs before the render and records its result in the run log.
    monkeypatch.setattr(render, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(render, "RUN_LOG_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(render, "COVERED_PATH", tmp_path / "covered.json")
    monkeypatch.setattr(render, "INFLIGHT_PATH", tmp_path / "inflight.json")
    base = tmp_path / "tmpbase"
    base.mkdir()
    monkeypatch.setattr(render, "TMP_BASE", base)
    _make_workdir(base, "daily-podcast-stale", age_days=30)
    _stub_full_render(monkeypatch, tmp_path)
    manifest = _full_render_manifest(tmp_path)
    wd = tmp_path / "wd"
    monkeypatch.setattr(
        sys,
        "argv",
        ["render.py", "--manifest", str(manifest), "--workdir", str(wd), "--prune-workdirs", "7"],
    )

    assert render.main() == 0
    rec = json.loads((tmp_path / "runs.jsonl").read_text().splitlines()[-1])
    assert rec["pruned_workdirs"]["count"] == 1
