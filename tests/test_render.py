"""
Invariant tests for skills/daily-podcast/render.py.

These cover the pure-Python logic — chapter-rule enforcement, voice precedence,
timeline math, dedup-log handling — without requiring MLX, ffmpeg, or the
save-to-spotify CLI. The audio I/O seam (`mp3_duration_ms`) is monkeypatched.
"""
from __future__ import annotations

import json
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

    assert silences == [render.DEFAULT_SILENCE_MS, render.DEFAULT_SILENCE_MS, render.LAST_SILENCE_MS]


def test_plan_silences_exactly_three_shorts_no_padding(tmp_path, monkeypatch):
    # Three short chapters is exactly at the cap; the last one counts as short
    # per plan_silences's docstring. No padding should happen.
    paths = _paths(tmp_path, 3)
    _patch_durations(monkeypatch, {p: 20_000 for p in paths})

    silences = render.plan_silences(paths)

    assert silences == [render.DEFAULT_SILENCE_MS, render.DEFAULT_SILENCE_MS, render.LAST_SILENCE_MS]


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
        segments, paths, silences_ms=[800, 800, 0], summary="s", episode_mp3=episode,
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
        segments, paths, silences_ms=[0], summary="s", episode_mp3=episode,
    )

    chapter = timeline["items"][0]["chapter"]
    link = timeline["items"][1]["link"]
    # link_start = cursor + max(1000, int(dur * 0.40))
    assert link["start_time_ms"] - chapter["start_time_ms"] >= 1000
    # link_dur = min(6000, max(2000, dur - 2000))
    assert 2000 <= link["duration_ms"] <= 6000


def test_build_timeline_fatal_when_last_chapter_starts_after_episode_ends(tmp_path, monkeypatch):
    # Second chapter starts at 10_000ms but episode is only 9_000ms long.
    segments = [{"title": "A"}, {"title": "B"}]
    paths = _paths(tmp_path, 2)
    episode = tmp_path / "episode.mp3"
    _patch_durations(monkeypatch, {paths[0]: 10_000, paths[1]: 1_000, episode: 9_000})

    with pytest.raises(SystemExit):
        render.build_timeline_and_description(
            segments, paths, silences_ms=[0, 0], summary="s", episode_mp3=episode,
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

    assert render.load_covered() == {
        "https://example.com/a": {"date": "2026-01-01"}
    }


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
