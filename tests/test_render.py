"""
Invariant tests for skills/daily-podcast/render.py.

These cover the pure-Python logic — chapter-rule enforcement, voice precedence,
timeline math, dedup-log handling — without requiring MLX, ffmpeg, or the
save-to-spotify CLI. The audio I/O seam (`mp3_duration_ms`) is monkeypatched.
"""
from __future__ import annotations

import json
import sys
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


def test_description_escapes_title_special_chars(tmp_path, monkeypatch):
    segments = [{"title": 'She said "hi" & left'}]
    paths = _paths(tmp_path, 1)
    episode = tmp_path / "episode.mp3"
    _patch_durations(monkeypatch, {paths[0]: 40_000, episode: 40_000})

    _, description = render.build_timeline_and_description(
        segments, paths, silences_ms=[0], summary="s", episode_mp3=episode,
    )

    assert "She said &quot;hi&quot; &amp; left" in description
    assert 'She said "hi"' not in description


def test_description_escapes_url_ampersand_and_quote(tmp_path, monkeypatch):
    segments = [{"title": "T", "source_url": "https://x.com/p?a=1&b=2'q"}]
    paths = _paths(tmp_path, 1)
    episode = tmp_path / "episode.mp3"
    _patch_durations(monkeypatch, {paths[0]: 40_000, episode: 40_000})

    timeline, description = render.build_timeline_and_description(
        segments, paths, silences_ms=[0], summary="s", episode_mp3=episode,
    )

    assert "a=1&amp;b=2" in description       # query-string & is escaped
    assert "&#x27;" in description            # single quote escaped — can't close the href
    assert "a=1&b=2'q" not in description     # no raw, href-breaking form survives
    # The timeline carries the RAW url; escaping is description-only.
    assert timeline["items"][1]["link"]["url"] == "https://x.com/p?a=1&b=2'q"


def test_description_summary_passes_through_unescaped(tmp_path, monkeypatch):
    segments = [{"title": "T"}]
    paths = _paths(tmp_path, 1)
    episode = tmp_path / "episode.mp3"
    _patch_durations(monkeypatch, {paths[0]: 40_000, episode: 40_000})

    _, description = render.build_timeline_and_description(
        segments, paths, silences_ms=[0], summary="<b>bold</b> & raw", episode_mp3=episode,
    )

    assert "<b>bold</b> & raw" in description  # summary is HTML-by-contract


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
    (wd / "uploaded.json").write_text(json.dumps({
        "episode_uri": "spotify:episode:abc123",
        "title": "T",
        "voice": "house",
        "voice_mode": "clone",
    }))
    if with_artifacts:
        (wd / "episode.mp3").write_bytes(b"x")
        (wd / "cover.jpg").write_bytes(b"x")
        (wd / "timeline.json").write_text(json.dumps(
            {"items": [{"chapter": {"title": "A", "start_time_ms": 0}}]}
        ))


def test_resume_skips_upload_and_runs_idempotent_tail(tmp_path, monkeypatch, capsys):
    wd = tmp_path / "wd"
    _seed_uploaded_workdir(wd)
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({
        "title": "T", "summary": "s",
        "segments": [{"text": "hi", "source_url": "https://example.com/a"}],
    }))

    # Prove the upload + config are never touched on resume; record the tail.
    monkeypatch.setattr(render, "upload",
                        lambda *a, **k: pytest.fail("upload must not run on resume"))
    monkeypatch.setattr(render, "load_config",
                        lambda: pytest.fail("load_config must not run on resume"))
    calls = []
    monkeypatch.setattr(render, "set_timeline", lambda eid, tp: calls.append(("set_timeline", eid)))
    monkeypatch.setattr(render, "poll_ready", lambda eid: calls.append(("poll_ready", eid)))
    monkeypatch.setattr(render, "mp3_duration_ms", lambda p: 60_000)
    covered_path = tmp_path / "covered.json"
    monkeypatch.setattr(render, "COVERED_PATH", covered_path)
    monkeypatch.setattr(sys, "argv",
                        ["render.py", "--manifest", str(manifest), "--workdir", str(wd)])

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
    monkeypatch.setattr(sys, "argv",
                        ["render.py", "--manifest", str(manifest), "--workdir", str(wd)])

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
    m.update({"voice": "house", "date": "2026-05-20", "raw_text": True,
              "show_id": "spotify:show:1", "voice_instruct": "calm"})
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
    assert "x=1" not in out          # fenced block content removed
    assert "pip install" in out      # inline content kept, backticks gone


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
