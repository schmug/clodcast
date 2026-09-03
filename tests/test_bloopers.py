"""Tests for the bloopers capture bin.

The 2026-08-17 TTS degeneration (incidents/tts-degeneration.md) produced the only
genuinely funny audio this pipeline has ever made, and the documented recovery for
it — "delete that seg_NN.mp3 from the workdir and re-run" — destroyed it. Workdirs
evaporate on their own too: /tmp/daily-podcast-2026-08-1{6,7,8,9} were all empty
four days later.

So the bin is an archive written as a side effect of paths that already exist. Four
triggers, every record tagged with which one fired:

  * `gate`       — a segment the artifact gate is about to reject (< 0.75x median)
  * `near-miss`  — a segment that PASSED but reads slow (0.75-0.90x median), which
                   is where a garbled phrase too short to move the rate hides
  * `run-failed` — every seg_*.mp3 of a run that died, swept from its workdir
  * `manual`     — an ffmpeg trim of any audio file, via bloopers.py mark

Two properties carry the weight:

  * capture is best-effort and NEVER changes a run's exit code (the `write_run_log`
    / incident-report contract), and
  * `text` and `duration_ms` are on every record regardless of trigger — the planned
    meta-episode narrates over these clips in the house voice, which needs to know
    what the segment should have said and how much material is banked.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import bloopers
import pytest

import render

# --- helpers ---------------------------------------------------------------

# The real 08-17 population: segment 5 (1-based 6 once the intro is prepended) read
# at 10.9 c/s against an 18.4 median — 0.59x, well under the 0.75x floor.
_INCIDENT_BODY_RATES = [18.2, 17.3, 18.1, 19.5, 10.9, 18.6, 18.5, 18.8, 18.5, 18.4]
_CLEAN_BODY_RATES = [18.2, 17.3, 18.1, 19.5, 18.5, 18.6, 18.5, 18.8, 18.5, 18.4]


def _bin(monkeypatch, tmp_path: Path) -> Path:
    """Point the bin at a throwaway dir and return it."""
    binned = tmp_path / "bloopers"
    monkeypatch.setattr(render, "BLOOPER_DIR", binned)
    return binned


def _rate_fixture(
    tmp_path: Path,
    monkeypatch,
    body_rates: list[float],
    *,
    intro_rate: float = 16.5,
    signoff_rate: float = 16.9,
    chars: int = 1000,
) -> tuple[list[dict], list[Path]]:
    """Build (segments, seg_paths) whose measured chars/sec match `body_rates`.

    Each segment's mp3 gets distinct bytes so content-addressing can tell them
    apart, exactly as real renders would.
    """
    segments: list[dict] = []
    paths: list[Path] = []
    durations: dict[Path, int] = {}

    def add(rate: float, url: str | None) -> None:
        n = len(segments) + 1
        path = tmp_path / f"wd/seg_{n:02d}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"ID3fake-audio-%02d" % n)
        durations[path] = int(round(chars / rate * 1000))
        segments.append({"text": "x" * chars, "source_url": url})
        paths.append(path)

    add(intro_rate, None)
    for i, rate in enumerate(body_rates):
        add(rate, f"https://example.com/{i}")
    add(signoff_rate, None)

    monkeypatch.setattr(render, "mp3_duration_ms", lambda p: durations[Path(p)])
    return segments, paths


def _index(binned: Path) -> list[dict]:
    path = binned / "index.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# --- the gate trigger ------------------------------------------------------


def test_gate_outlier_is_banked_with_its_rate_evidence(monkeypatch, tmp_path):
    """The 08-17 clip: banked, tagged `gate`, carrying the numbers that condemned it."""
    binned = _bin(monkeypatch, tmp_path)
    segments, seg_paths = _rate_fixture(tmp_path, monkeypatch, _INCIDENT_BODY_RATES)

    banked = render.capture_rate_bloopers(segments, seg_paths)

    assert len(banked) == 1, banked
    rec = banked[0]
    assert rec["reason"] == "gate"
    assert rec["segment"] == 6  # 1-based, matching the render log and the gate message
    assert rec["rate"] == pytest.approx(10.9, abs=0.1)
    assert rec["median"] == pytest.approx(18.4, abs=0.1)
    assert rec["ratio"] == pytest.approx(0.59, abs=0.01)
    assert _index(binned) == banked


def test_a_clean_episode_banks_nothing(monkeypatch, tmp_path):
    """No outlier, no near-miss: the bin stays empty and no index file is created."""
    binned = _bin(monkeypatch, tmp_path)
    segments, seg_paths = _rate_fixture(tmp_path, monkeypatch, _CLEAN_BODY_RATES)

    assert render.capture_rate_bloopers(segments, seg_paths) == []
    assert _index(binned) == []


def test_the_rejection_message_says_the_clip_is_already_banked(monkeypatch, tmp_path):
    """The operator reads this line at the exact moment they are about to delete the
    only funny audio the pipeline makes. Telling them it is already saved is the
    difference between a recovery and a loss."""
    segments, seg_paths = _rate_fixture(tmp_path, monkeypatch, _INCIDENT_BODY_RATES)

    problems = render.speech_rate_problems(segments, seg_paths)

    assert len(problems) == 1, problems
    assert "banked" in problems[0], problems[0]
    # ...without disturbing what the message already had to carry.
    assert "segment 6" in problems[0]
    assert "10.9" in problems[0] and "18.4" in problems[0]
    assert render.classify_incident("artifact gate failed: " + problems[0]) == "tts-degeneration"


# --- the near-miss trigger -------------------------------------------------


def test_near_miss_segment_is_banked_on_an_otherwise_passing_run(monkeypatch, tmp_path):
    """0.85x the median clears the 0.75x gate, so this episode ships — but a phrase
    that garbled without moving the rate far enough to fail is exactly the comedy
    the gate is blind to."""
    binned = _bin(monkeypatch, tmp_path)
    rates = list(_CLEAN_BODY_RATES)
    rates[4] = 18.4 * 0.85
    segments, seg_paths = _rate_fixture(tmp_path, monkeypatch, rates)

    # It really does pass the gate — otherwise this is just the `gate` test again.
    assert render.speech_rate_problems(segments, seg_paths) == []

    banked = render.capture_rate_bloopers(segments, seg_paths)

    assert [r["reason"] for r in banked] == ["near-miss"]
    assert banked[0]["segment"] == 6
    assert _index(binned) == banked


def test_the_near_miss_band_stops_at_the_clean_population(monkeypatch, tmp_path):
    """0.94x was the slowest CLEAN segment on 08-17. Banking that would bank most
    of every episode, so the band's ceiling must sit below it."""
    assert render.NEAR_MISS_RATE_RATIO < 0.94
    binned = _bin(monkeypatch, tmp_path)
    rates = list(_CLEAN_BODY_RATES)
    rates[4] = 18.4 * 0.94
    segments, seg_paths = _rate_fixture(tmp_path, monkeypatch, rates)

    assert render.capture_rate_bloopers(segments, seg_paths) == []
    assert _index(binned) == []


def test_intro_and_signoff_are_never_banked(monkeypatch, tmp_path):
    """They are legitimately slower than the body and are not judged by its median —
    the same exclusion the gate makes, for the same reason."""
    binned = _bin(monkeypatch, tmp_path)
    segments, seg_paths = _rate_fixture(
        tmp_path, monkeypatch, _CLEAN_BODY_RATES, intro_rate=11.0, signoff_rate=11.0
    )

    assert render.capture_rate_bloopers(segments, seg_paths) == []
    assert _index(binned) == []


def test_capture_is_skipped_when_the_population_is_too_small(monkeypatch, tmp_path):
    """Below MIN_RATE_SAMPLE_SEGMENTS one bad render IS the median, so there is no
    evidence of a defect to bank — the gate skips here and so must the bin."""
    binned = _bin(monkeypatch, tmp_path)
    rates = [18.5] * (render.MIN_RATE_SAMPLE_SEGMENTS - 1)
    rates[0] = 5.0
    segments, seg_paths = _rate_fixture(tmp_path, monkeypatch, rates)

    assert render.capture_rate_bloopers(segments, seg_paths) == []
    assert _index(binned) == []


# --- what a record has to carry -------------------------------------------


def test_every_record_carries_the_script_text_and_duration(monkeypatch, tmp_path):
    """The meta-episode narrates OVER these clips in the house voice: it needs what
    the segment should have said, and `jq 'map(.duration_ms)|add'` is how "do we
    have enough for a half-episode yet?" gets answered."""
    _bin(monkeypatch, tmp_path)
    segments, seg_paths = _rate_fixture(tmp_path, monkeypatch, _INCIDENT_BODY_RATES)

    rec = render.capture_rate_bloopers(segments, seg_paths)[0]

    assert rec["text"] == segments[5]["text"]
    assert rec["chars"] == 1000
    assert rec["duration_ms"] == pytest.approx(1000 / 10.9 * 1000, rel=0.01)
    assert rec["source_url"] == "https://example.com/4"


def test_records_carry_the_full_field_set_so_the_index_parses_line_by_line(monkeypatch, tmp_path):
    """Same contract as runs.jsonl: missing values are null, never absent, so jq and
    pandas can read the file without a per-line schema check."""
    _bin(monkeypatch, tmp_path)
    segments, seg_paths = _rate_fixture(tmp_path, monkeypatch, _INCIDENT_BODY_RATES)

    rec = render.capture_rate_bloopers(segments, seg_paths)[0]

    assert set(rec) == set(render.BLOOPER_FIELDS)
    assert rec["note"] is None  # unset on an automatic capture, present as a key


def test_the_clip_is_copied_out_of_the_workdir_content_addressed(monkeypatch, tmp_path):
    """The workdir is what disappears — a record pointing into it would rot. The clip
    is copied, and named by its own hash so identical bytes can only land once."""
    binned = _bin(monkeypatch, tmp_path)
    segments, seg_paths = _rate_fixture(tmp_path, monkeypatch, _INCIDENT_BODY_RATES)
    original = seg_paths[5].read_bytes()

    rec = render.capture_rate_bloopers(segments, seg_paths)[0]

    clip = binned / "clips" / Path(rec["clip"]).name
    assert clip.read_bytes() == original
    assert rec["sha256"] == hashlib.sha256(original).hexdigest()
    assert clip.stem == rec["sha256"][:16]


# --- dedupe ----------------------------------------------------------------


def test_recapturing_identical_bytes_banks_one_clip_and_one_row(monkeypatch, tmp_path):
    """A same-day resume re-renders nothing (the TTS cache is a hit) and re-runs the
    gate, so the same segment arrives twice. Content-addressing makes that a no-op
    rather than a duplicate."""
    binned = _bin(monkeypatch, tmp_path)
    segments, seg_paths = _rate_fixture(tmp_path, monkeypatch, _INCIDENT_BODY_RATES)

    first = render.capture_rate_bloopers(segments, seg_paths)
    second = render.capture_rate_bloopers(segments, seg_paths)

    assert len(first) == 1
    assert second == []  # nothing new banked
    assert len(_index(binned)) == 1
    assert len(list((binned / "clips").iterdir())) == 1


# --- the run-failed sweep --------------------------------------------------


def test_a_failed_run_banks_every_segment_in_its_workdir(monkeypatch, tmp_path):
    """Most failures are upload/poll problems whose audio is fine, so this fills the
    bin with non-bloopers by design — the `reason` tag is what makes them siftable."""
    binned = _bin(monkeypatch, tmp_path)
    wd = tmp_path / "wd"
    wd.mkdir()
    for i in (1, 2, 3):
        (wd / f"seg_{i:02d}.mp3").write_bytes(b"ID3seg-%d" % i)
    (wd / "episode.mp3").write_bytes(b"ID3whole-episode")

    banked = render.capture_workdir_segments(wd)

    assert [r["reason"] for r in banked] == ["run-failed"] * 3
    assert [Path(r["source"]).name for r in banked] == ["seg_01.mp3", "seg_02.mp3", "seg_03.mp3"]
    assert len(_index(binned)) == 3


def test_the_sweep_is_suppressed_when_the_gate_already_banked_the_offender(monkeypatch, tmp_path):
    """A speech-rate rejection has already banked the precise segment with its rate
    evidence. Sweeping the workdir too would bank the eleven clean segments beside
    it, burying the one clip that is actually funny."""
    binned = _bin(monkeypatch, tmp_path)
    wd = tmp_path / "wd"
    wd.mkdir()
    for i in (1, 2, 3):
        (wd / f"seg_{i:02d}.mp3").write_bytes(b"ID3seg-%d" % i)
    error = (
        "artifact gate failed: segment 6 speech rate 10.9 chars/sec is 0.59x the "
        "18.4 chars/sec median (floor 0.75x)"
    )

    assert render.capture_workdir_segments(wd, error_message=error) == []
    assert _index(binned) == []


def test_the_sweep_still_runs_for_an_unrelated_gate_failure(monkeypatch, tmp_path):
    """Only a speech-rate rejection identifies a segment. A chapter-gap or profile
    failure names none, so the sweep is the only thing that would save the audio."""
    _bin(monkeypatch, tmp_path)
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "seg_01.mp3").write_bytes(b"ID3seg-1")

    banked = render.capture_workdir_segments(
        wd, error_message="artifact gate failed: chapter 3 starts 2100ms after chapter 2"
    )

    assert [r["reason"] for r in banked] == ["run-failed"]


def test_the_sweep_recovers_script_text_from_the_workdir_manifest(monkeypatch, tmp_path):
    """The manifest is sitting right there next to the segments, and the meta-episode
    needs to know what each clip was supposed to say. A sweep record without it is a
    sound with no story."""
    _bin(monkeypatch, tmp_path)
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "seg_01.mp3").write_bytes(b"ID3seg-1")
    (wd / "seg_02.mp3").write_bytes(b"ID3seg-2")
    (wd / "manifest.json").write_text(
        json.dumps(
            {
                "title": "Daily Digest - August 17, 2026",
                "segments": [
                    {"text": "the intro", "source_url": None},
                    {"text": "the first story", "source_url": "https://example.com/1"},
                ],
            }
        )
    )
    monkeypatch.setattr(render, "mp3_duration_ms", lambda p: 4200)

    banked = render.capture_workdir_segments(wd)

    assert [r["text"] for r in banked] == ["the intro", "the first story"]
    assert banked[1]["source_url"] == "https://example.com/1"
    assert [r["duration_ms"] for r in banked] == [4200, 4200]
    assert banked[0]["title"] == "Daily Digest - August 17, 2026"


def test_the_sweep_survives_an_unreadable_manifest_and_unmeasurable_audio(monkeypatch, tmp_path):
    """ffprobe is a hard runtime dep but the sweep runs on the failure path, where
    assuming anything works is how a recovery turns into a second crash."""
    _bin(monkeypatch, tmp_path)
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "seg_01.mp3").write_bytes(b"ID3seg-1")
    (wd / "manifest.json").write_text("{ this is not json")

    def explode(path):
        raise OSError("ffprobe not on PATH")

    monkeypatch.setattr(render, "mp3_duration_ms", explode)

    banked = render.capture_workdir_segments(wd)

    assert [r["reason"] for r in banked] == ["run-failed"]
    assert banked[0]["duration_ms"] is None
    assert banked[0]["text"] is None


def test_a_workdir_with_no_segments_banks_nothing(monkeypatch, tmp_path):
    """A pre-flight failure dies before TTS, so there is nothing to sweep."""
    binned = _bin(monkeypatch, tmp_path)
    wd = tmp_path / "wd"
    wd.mkdir()

    assert render.capture_workdir_segments(wd) == []
    assert _index(binned) == []


def test_a_missing_workdir_banks_nothing_and_does_not_raise(monkeypatch, tmp_path):
    """`workdir` is null in the run record on the paths that die before creating one."""
    _bin(monkeypatch, tmp_path)

    assert render.capture_workdir_segments(tmp_path / "nope") == []
    assert render.capture_workdir_segments(None) == []


# --- best-effort contract --------------------------------------------------


def test_an_unwritable_bin_never_sinks_the_run(monkeypatch, tmp_path):
    """Same contract as write_run_log and the incident reports: observability must
    never change a run's exit code. A full disk loses a joke, not an episode."""
    binned = _bin(monkeypatch, tmp_path)
    binned.parent.mkdir(parents=True, exist_ok=True)
    binned.write_text("not a directory")  # every mkdir under it now raises
    segments, seg_paths = _rate_fixture(tmp_path, monkeypatch, _INCIDENT_BODY_RATES)

    assert render.capture_rate_bloopers(segments, seg_paths) == []


def test_a_vanished_segment_file_is_skipped_not_fatal(monkeypatch, tmp_path):
    """The gate measured it, then something removed it. Bank what is still there."""
    _bin(monkeypatch, tmp_path)
    segments, seg_paths = _rate_fixture(tmp_path, monkeypatch, _INCIDENT_BODY_RATES)
    seg_paths[5].unlink()

    assert render.capture_rate_bloopers(segments, seg_paths) == []


def test_dry_run_banks_nothing(monkeypatch, tmp_path):
    """A rehearsal must not mutate user state — the same posture that keeps --dry-run
    out of covered.json and stops --prune-workdirs deleting anything."""
    binned = _bin(monkeypatch, tmp_path)
    segments, seg_paths = _rate_fixture(tmp_path, monkeypatch, _INCIDENT_BODY_RATES)

    assert render.capture_rate_bloopers(segments, seg_paths, dry_run=True) == []
    assert not binned.exists()


# --- run-log integration ---------------------------------------------------


def test_bloopers_captured_is_a_run_log_field(monkeypatch, tmp_path):
    """Appended to RUN_LOG_FIELDS (never added ad hoc at one call site) so every
    line keeps the full key set."""
    assert "bloopers_captured" in render.RUN_LOG_FIELDS
    assert render._new_run_record()["bloopers_captured"] is None


# --- the manual mark CLI ---------------------------------------------------
#
# The path that would actually have rescued the 08-17 clip: it tripped no gate (the
# gate did not exist yet) and its workdir is long gone, so the only surviving copy is
# inside the published episode. `mark` trims any audio file, which covers a workdir
# segment, a full episode.mp3, and a feed episode you downloaded yourself.


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("90", 90.0), ("90.5", 90.5), ("4:12", 252.0), ("1:02:03", 3723.0), ("0:00", 0.0)],
)
def test_parse_timecode_accepts_the_shapes_a_player_shows_you(text, seconds):
    assert bloopers.parse_timecode(text) == seconds


@pytest.mark.parametrize("text", ["", "abc", "4:xx", "1:2:3:4", "-5"])
def test_parse_timecode_refuses_junk(text):
    with pytest.raises(ValueError):
        bloopers.parse_timecode(text)


def test_mark_trims_the_range_and_banks_it_as_manual(monkeypatch, tmp_path):
    binned = _bin(monkeypatch, tmp_path)
    src = tmp_path / "episode.mp3"
    src.write_bytes(b"ID3whole-episode")
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"ID3trimmed-clip")

    monkeypatch.setattr(render, "run", fake_run)

    rec = bloopers.mark(src, start="4:12", end="4:58", note="birdsbirdsbirds")

    assert rec["reason"] == "manual"
    assert rec["note"] == "birdsbirdsbirds"
    assert rec["duration_ms"] == 46_000
    assert rec["source"] == str(src)
    assert (binned / "clips" / f"{rec['sha256'][:16]}.mp3").read_bytes() == b"ID3trimmed-clip"
    assert _index(binned) == [rec]


def test_mark_re_asserts_mono_44_1k_on_the_trimmed_clip(monkeypatch, tmp_path):
    """Every ffmpeg invocation in this repo re-asserts the format; concat-protocol is
    fragile across mismatched rates and a banked clip is future episode input."""
    _bin(monkeypatch, tmp_path)
    src = tmp_path / "episode.mp3"
    src.write_bytes(b"ID3whole-episode")
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"ID3trimmed-clip")

    monkeypatch.setattr(render, "run", fake_run)

    bloopers.mark(src, start="4:12", end="4:58")

    cmd = calls[0]
    assert cmd[0] == "ffmpeg"
    assert "252.0" in cmd and "46.0" in cmd  # -ss start, -t duration (not -to)
    assert cmd[cmd.index("-ar") + 1] == str(render.AUDIO_SAMPLE_RATE)
    assert cmd[cmd.index("-ac") + 1] == str(render.AUDIO_CHANNELS)


def test_mark_refuses_a_range_that_ends_before_it_starts(monkeypatch, tmp_path):
    _bin(monkeypatch, tmp_path)
    src = tmp_path / "episode.mp3"
    src.write_bytes(b"ID3whole-episode")

    with pytest.raises(ValueError):
        bloopers.mark(src, start="4:58", end="4:12")


def test_mark_refuses_a_source_that_does_not_exist(monkeypatch, tmp_path):
    _bin(monkeypatch, tmp_path)

    with pytest.raises(FileNotFoundError):
        bloopers.mark(tmp_path / "nope.mp3", start="0:01", end="0:02")


# --- state isolation -------------------------------------------------------


def test_conftest_isolates_the_bin_from_real_user_state():
    """The suite drives failure paths constantly; without this the developer's real
    bin fills with fixture bytes. tests/conftest.py's guard covers it only if the
    attribute is in _WRITABLE_STATE_ATTRS."""
    real = Path.home() / ".config" / "daily-podcast"
    value = Path(render.BLOOPER_DIR)
    assert real not in value.parents and value != real


def test_the_sweep_survives_the_context_its_only_caller_passes(monkeypatch, tmp_path):
    """Every other sweep test calls capture_workdir_segments bare, which is exactly
    how this stayed hidden: _sweep_bloopers_on_failure passes run_date AND title in
    **ctx, and the sweep then passed title= explicitly as well — so bank_blooper got
    two values for it, raised TypeError, and the run-failed trigger banked nothing
    from any real failed run since #169."""
    binned = _bin(monkeypatch, tmp_path)
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "seg_01.mp3").write_bytes(b"ID3seg-1")
    (wd / "manifest.json").write_text(
        json.dumps({"title": "the manifest title", "segments": [{"text": "the intro"}]})
    )

    banked = render.capture_workdir_segments(
        wd, run_date="2026-09-02", title="the run record title"
    )

    assert [r["reason"] for r in banked] == ["run-failed"]
    assert banked[0]["run_date"] == "2026-09-02"
    # The caller's title wins: it is the run record's, and the record is what the
    # weekly review reads. The manifest stays the fallback (tested above).
    assert banked[0]["title"] == "the run record title"
    assert len(_index(binned)) == 1
