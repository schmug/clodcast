"""
Invariant tests for the optional intro/outro music layer in
skills/daily-podcast/render.py.

The pure half — config resolution, the mix plan, the filter graph, manifest
validation, the timeline offset, provenance invalidation — needs neither ffmpeg
nor MLX and runs everywhere. The one real-ffmpeg test at the bottom is guarded
by shutil.which and skips on a host without it (CI installs no ffmpeg).

The load-bearing claim this file exists to hold: a manifest with no `music` key
renders EXACTLY as it did before. Every other show shares this renderer.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import render

# --- helpers ---------------------------------------------------------------

MUSIC_MIN = {"enabled": True, "asset": "skills/daily-podcast/assets/music/pixel-window.flac"}


def _music(**over) -> dict:
    return render.resolve_music_config({**MUSIC_MIN, **over})


def _segments(n: int) -> list[dict]:
    """A music-shaped manifest body: intro, n-2 stories, sign-off."""
    segs = [{"title": "Intro", "text": "Welcome.", "role": "intro", "source_url": None}]
    for i in range(n - 2):
        segs.append({"title": f"Story {i}", "text": "Body.", "source_url": f"https://x.test/{i}"})
    segs.append({"title": "Sign-off", "text": "Goodbye.", "role": "outro", "source_url": None})
    return segs


def _manifest(**over) -> dict:
    base = {
        "title": "T",
        "summary": "S",
        "ship_mode": "web",
        "segments": _segments(4),
    }
    base.update(over)
    return base


# --- resolve_music_config --------------------------------------------------


def test_absent_music_resolves_to_none():
    """No `music` key is the default for every show, and it must stay falsy all
    the way down rather than resolving to a disabled-but-present object."""
    assert render.resolve_music_config(None) is None
    assert render.resolve_music_config({}) is None


def test_disabled_music_resolves_to_none():
    assert render.resolve_music_config({"enabled": False, "asset": "a.wav"}) is None


def test_resolve_fills_every_default_and_keeps_overrides():
    cfg = render.resolve_music_config({**MUSIC_MIN, "duck_db": -12.0})

    assert cfg["enabled"] is True
    assert cfg["asset"] == "skills/daily-podcast/assets/music/pixel-window.flac"
    assert cfg["duck_db"] == -12.0
    for key, default in render.MUSIC_DEFAULTS.items():
        assert key in cfg, f"resolved music config is missing {key}"
        if key != "duck_db":
            assert cfg[key] == default


def test_resolve_preserves_unknown_keys_so_validation_can_reject_them():
    """Silently dropping a typo'd key would render with a default the operator
    thinks they overrode. Carry it through to validate_manifest, which dies."""
    cfg = render.resolve_music_config({**MUSIC_MIN, "duck_dB": -12.0})
    assert cfg["duck_dB"] == -12.0


def test_defaults_are_the_reference_mix():
    """The audition Cory heard is 78 BPM: a one-bar lead and a two-bar tail."""
    bar = 240 / 78
    assert render.MUSIC_DEFAULTS["lead_seconds"] == pytest.approx(bar)
    assert render.MUSIC_DEFAULTS["tail_seconds"] == pytest.approx(2 * bar)
    assert render.MUSIC_DEFAULTS["duck_db"] == -18.0
    assert render.MUSIC_DEFAULTS["output_lufs"] == -19.0


# --- plan_music_mix --------------------------------------------------------


def _plan(seg_ms=None, silences_ms=None, speech_ms=None, **over):
    seg_ms = seg_ms or [20_000, 60_000, 60_000, 15_000]
    silences_ms = silences_ms if silences_ms is not None else [800, 800, 800, 0]
    speech_ms = speech_ms if speech_ms is not None else sum(seg_ms) + sum(silences_ms)
    return render.plan_music_mix(
        _music(**over), seg_ms=seg_ms, silences_ms=silences_ms, speech_ms=speech_ms
    )


def test_plan_places_the_bookends_on_the_timeline_geometry():
    """intro music ends at the FIRST STORY boundary and the sign-off music starts
    at the LAST chapter — both read off the same cursor build_timeline_and_
    description walks, so audio and published chapter marks cannot disagree."""
    plan = _plan()

    assert plan.lead_ms == 3077  # one bar at 78 BPM, quantised to ms
    assert plan.tail_ms == 6154
    # chapter 1 starts at seg0 + silence0 = 20800 pre-music
    assert plan.intro_end_ms == 3077 + 20_800
    # last chapter starts at 20800 + 60800 + 60800 = 142400 pre-music
    assert plan.outro_start_ms == 3077 + 142_400
    assert plan.total_ms == 3077 + plan.speech_ms + 6154


def test_plan_total_uses_the_MEASURED_speech_not_the_summed_geometry():
    """mp3 concat re-encodes, so the assembled file is a few hundred ms shorter
    than the sum of its parts. Planning the total off the sum would leave dead
    air after the tail fade and make the duration gate's tolerance meaningless."""
    plan = _plan(speech_ms=156_000)  # summed geometry is 158_400

    assert plan.speech_ms == 156_000
    assert plan.total_ms == 3077 + 156_000 + 6154


def test_plan_is_pure_and_repeatable():
    """Same config, same geometry, same plan — that is what makes a re-run in the
    same workdir reproduce the mix instead of drifting."""
    assert _plan() == _plan()
    assert _plan().to_dict() == _plan().to_dict()
    assert _plan(duck_db=-12.0) != _plan()


def test_duck_gain_is_the_dB_the_config_asks_for():
    assert _plan(duck_db=-18.0).duck_gain == pytest.approx(10 ** (-18 / 20), abs=1e-6)
    assert _plan(duck_db=0.0).duck_gain == pytest.approx(1.0)


def test_duck_ramp_and_rise_slopes_land_on_the_reference_numbers():
    """The prototype's magic 1.748 / 0.437 are (1 - duck) / ramp and / rise. They
    are derived here so a changed duck_db cannot leave a stale slope behind."""
    plan = _plan()

    assert plan.duck_slope == pytest.approx(1.748, abs=0.001)
    assert plan.rise_slope == pytest.approx(0.437, abs=0.001)


def test_tiny_intro_clamps_the_fade_out_into_the_ducked_region():
    """A 3s fade under a 2s intro segment would start before the lead ended and
    pull down the loud opening bar. The fade must live entirely under speech."""
    plan = _plan(seg_ms=[2_000, 60_000, 60_000, 15_000])

    assert plan.intro_fade_out_s == pytest.approx(2.8, abs=0.001)  # 2000+800 ms of speech
    assert plan.intro_fade_out_at_s >= plan.lead_ms / 1000
    assert plan.intro_fade_out_at_s + plan.intro_fade_out_s == pytest.approx(
        plan.intro_end_ms / 1000
    )


def test_a_zero_length_intro_drops_the_fade_out_entirely():
    plan = _plan(seg_ms=[0, 60_000, 60_000, 15_000], silences_ms=[0, 800, 800, 0])

    assert plan.intro_end_ms == plan.lead_ms
    assert plan.intro_fade_out_s == 0.0


def test_short_lead_clamps_the_duck_ramp():
    """The ramp ends where speech starts; it cannot begin before t=0."""
    plan = _plan(lead_seconds=0.2)

    assert plan.duck_ramp_s == pytest.approx(0.2)
    assert plan.duck_ramp_at_s == pytest.approx(0.0)


def test_a_short_signoff_still_gets_a_valid_outro_envelope():
    plan = _plan(seg_ms=[20_000, 60_000, 60_000, 1_200])

    assert plan.outro_duration_s > 0
    assert 0 <= plan.outro_fade_in_s <= plan.outro_duration_s
    assert 0 <= plan.outro_fade_out_s <= plan.outro_duration_s
    assert plan.outro_fade_out_at_s + plan.outro_fade_out_s == pytest.approx(plan.outro_duration_s)
    # The rise starts when the speech ends, never before the clip does.
    assert plan.outro_rise_at_s >= 0


def test_a_zero_tail_keeps_every_envelope_inside_the_signoff():
    plan = _plan(tail_seconds=0.0)

    assert plan.tail_ms == 0
    assert plan.total_ms == plan.lead_ms + plan.speech_ms
    assert plan.outro_fade_out_at_s >= 0
    assert plan.outro_fade_in_s <= plan.outro_duration_s


def test_plan_refuses_geometry_it_cannot_bookend():
    """Two segments means the intro IS the sign-off; there is no story region to
    leave clean. Better a refusal than a mix nobody can reason about."""
    with pytest.raises(SystemExit):
        _plan(seg_ms=[20_000, 15_000], silences_ms=[800, 0])


# --- music_filter_graph ----------------------------------------------------


def test_filter_graph_delays_speech_and_mixes_three_streams():
    graph = render.music_filter_graph(_plan())

    assert "adelay=3077:all=1" in graph
    assert "amix=inputs=3" in graph
    assert "normalize=0" in graph
    assert "loudnorm=I=-19" in graph
    assert "TP=-2" in graph
    assert "print_format=json" in graph


def test_filter_graph_never_interpolates_a_shell_metacharacter():
    """Every ffmpeg call in this file is an argument list, and the graph is one
    argument — but a stray quote in it would still corrupt the filter parse."""
    graph = render.music_filter_graph(_plan(duck_db=-13.5, lead_seconds=1.25))

    assert '"' not in graph
    assert "`" not in graph
    assert "$" not in graph


def test_filter_graph_drops_a_degenerate_fade_instead_of_emitting_zero():
    """afade with d=0 is a filter error, not a no-op."""
    graph = render.music_filter_graph(
        _plan(seg_ms=[0, 60_000, 60_000, 15_000], silences_ms=[0, 800, 800, 0])
    )

    assert "afade=t=out:st=" not in graph.split("[intro]")[0]


# --- manifest validation ---------------------------------------------------


def test_manifest_without_music_is_unchanged():
    render.validate_manifest(_manifest())  # no music key at all


def test_roles_alone_are_accepted_without_music():
    """Newly assembled manifests always carry roles; music is separate."""
    render.validate_manifest(_manifest(segments=_segments(5)))


def test_an_unknown_role_dies():
    segs = _segments(4)
    segs[0]["role"] = "preamble"
    with pytest.raises(SystemExit):
        render.validate_manifest(_manifest(segments=segs))


def test_music_requires_bookend_roles():
    segs = _segments(4)
    del segs[0]["role"]
    with pytest.raises(SystemExit):
        render.validate_manifest(_manifest(segments=segs, music=_music()))


def test_music_refuses_a_role_in_the_wrong_position():
    """Do not guess from titles, and do not accept an ordering the mix plan
    cannot express: the intro is first and the sign-off is last."""
    segs = _segments(4)
    segs[0]["role"] = "outro"
    segs[-1]["role"] = "intro"
    with pytest.raises(SystemExit):
        render.validate_manifest(_manifest(segments=segs, music=_music()))


def test_music_refuses_duplicate_roles():
    segs = _segments(4)
    segs[1]["role"] = "intro"
    with pytest.raises(SystemExit):
        render.validate_manifest(_manifest(segments=segs, music=_music()))


def test_music_refuses_a_two_segment_episode():
    with pytest.raises(SystemExit):
        render.validate_manifest(_manifest(segments=_segments(2), music=_music()))


def test_music_requires_an_asset():
    with pytest.raises(SystemExit):
        render.validate_manifest(_manifest(music={"enabled": True}))


def test_music_rejects_an_unknown_key():
    with pytest.raises(SystemExit):
        render.validate_manifest(_manifest(music=_music(duck_dB=-12.0)))


@pytest.mark.parametrize(
    "field,value",
    [
        ("lead_seconds", 0),
        ("lead_seconds", -1),
        ("lead_seconds", 3600),
        ("lead_seconds", float("inf")),
        ("lead_seconds", float("nan")),
        ("lead_seconds", "3"),
        ("tail_seconds", -0.5),
        ("duck_db", 6.0),
        ("duck_db", -120.0),
        ("intro_fade_seconds", -1),
        ("outro_fade_seconds", 999),
        ("music_lufs", -100.0),
        ("output_lufs", 0.0),
        ("true_peak_db", 3.0),
        ("true_peak_db", -30.0),
    ],
)
def test_music_numeric_ranges_are_validated(field, value):
    with pytest.raises(SystemExit):
        render.validate_manifest(_manifest(music=_music(**{field: value})))


def test_music_sha256_must_be_hex():
    with pytest.raises(SystemExit):
        render.validate_manifest(_manifest(music=_music(asset_sha256="not-a-hash")))


def test_a_valid_music_manifest_passes():
    render.validate_manifest(_manifest(music=_music(asset_sha256="a" * 64)))


# --- asset resolution + pre-flight ----------------------------------------


def test_relative_assets_resolve_against_the_plugin_root_not_the_cwd(monkeypatch, tmp_path):
    """The scheduler's CWD is not a stable base; the plugin root is. It is the root
    rather than the skill dir because each SHOW owns its music: Frontier Commits'
    sting must not have to live in the daily show's asset folder."""
    monkeypatch.chdir(tmp_path)
    resolved = render.resolve_music_asset(
        _music(asset="skills/daily-podcast/assets/music/pixel-window.flac")
    )

    assert resolved == render.SCRIPT_DIR / "assets" / "music" / "pixel-window.flac"
    assert render.MUSIC_ASSET_BASE == render.SCRIPT_DIR.parent.parent


def test_absolute_assets_are_used_as_given(tmp_path):
    p = tmp_path / "x.wav"
    assert render.resolve_music_asset(_music(asset=str(p))) == p


def test_the_bundled_asset_exists_and_matches_its_recorded_hash():
    """The WAV ships in the plugin; a truncated checkout must not reach a render."""
    asset = render.SCRIPT_DIR / "assets" / "music" / "pixel-window.flac"
    assert asset.exists(), "bundled music asset is missing"
    provenance = json.loads((asset.parent / "PROVENANCE.json").read_text())
    assert render.artifact_fingerprint(asset) == provenance["sha256"]


def test_preflight_music_check_fails_on_a_missing_asset(tmp_path):
    check = render._music_asset_check(_music(asset=str(tmp_path / "nope.wav")))
    assert check["ok"] is False
    assert "nope.wav" in check["detail"]


def test_preflight_music_check_fails_on_a_hash_mismatch(tmp_path):
    asset = tmp_path / "m.wav"
    asset.write_bytes(b"RIFFfake")
    check = render._music_asset_check(_music(asset=str(asset), asset_sha256="b" * 64))

    assert check["ok"] is False
    assert "sha256" in check["detail"]


def test_preflight_music_check_fails_on_undecodable_audio(tmp_path, monkeypatch):
    asset = tmp_path / "m.wav"
    asset.write_bytes(b"not audio at all")
    monkeypatch.setattr(render, "_probe_duration_s", lambda p: None)
    check = render._music_asset_check(_music(asset=str(asset)))

    assert check["ok"] is False
    assert "decode" in check["detail"]


def test_preflight_runs_the_music_check_only_when_music_is_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(render, "_tts_module_check", lambda: render._check("tts-module", True, ""))
    monkeypatch.setattr(
        render, "_tts_engine_check", lambda spec: render._check("tts-engine", True, "")
    )
    monkeypatch.setattr(
        render, "check_r2_credentials", lambda *a, **k: {"ok": True, "detail": "ok"}
    )

    _, without = render.preflight({}, show_id=None, dry_run=True, web_only=True)
    assert not any(c["name"] == "music-asset" for c in without)

    asset = tmp_path / "m.wav"
    asset.write_bytes(b"RIFFfake")
    monkeypatch.setattr(render, "_probe_duration_s", lambda p: 24.6)
    _, with_music = render.preflight(
        {}, show_id=None, dry_run=True, web_only=True, music=_music(asset=str(asset))
    )
    assert any(c["name"] == "music-asset" and c["ok"] for c in with_music)


# --- timeline offset -------------------------------------------------------


def _tl(monkeypatch, tmp_path, durations, silences, lead_ms, segments=None):
    paths = [tmp_path / f"seg_{i:02d}.mp3" for i in range(1, len(durations) + 1)]
    ep = tmp_path / "episode.mp3"
    sizes = dict(zip(paths, durations, strict=True))
    sizes[ep] = sum(durations) + sum(silences) + lead_ms + 6154
    monkeypatch.setattr(render, "mp3_duration_ms", lambda p: sizes[Path(p)])
    segs = segments or [
        {"title": "Intro", "source_url": None},
        {"title": "Story", "source_url": "https://x.test/a"},
        {"title": "Sign-off", "source_url": None},
    ]
    return render.build_timeline_and_description(segs, paths, silences, "sum", ep, lead_ms=lead_ms)


def test_lead_ms_zero_is_todays_timeline(monkeypatch, tmp_path):
    timeline, _ = _tl(monkeypatch, tmp_path, [20_000, 60_000, 15_000], [800, 800, 0], 0)
    starts = [i["chapter"]["start_time_ms"] for i in timeline["items"] if "chapter" in i]

    assert starts == [0, 20_800, 81_600]


def test_the_first_chapter_stays_at_zero_and_the_rest_shift_by_the_lead(monkeypatch, tmp_path):
    """Chapter one must include the theme, so it starts at 0 — everything after it
    moves by exactly the lead."""
    timeline, _ = _tl(monkeypatch, tmp_path, [20_000, 60_000, 15_000], [800, 800, 0], 3077)
    starts = [i["chapter"]["start_time_ms"] for i in timeline["items"] if "chapter" in i]

    assert starts == [0, 3077 + 20_800, 3077 + 81_600]


def test_source_links_shift_by_the_lead_too(monkeypatch, tmp_path):
    base, _ = _tl(monkeypatch, tmp_path, [20_000, 60_000, 15_000], [800, 800, 0], 0)
    moved, _ = _tl(monkeypatch, tmp_path, [20_000, 60_000, 15_000], [800, 800, 0], 3077)

    def links(t):
        return [i["link"] for i in t["items"] if "link" in i]

    assert len(links(moved)) == 1
    for before, after in zip(links(base), links(moved), strict=True):
        assert after["start_time_ms"] == before["start_time_ms"] + 3077
        # Duration and source association are preserved, not recomputed.
        assert after["duration_ms"] == before["duration_ms"]
        assert after["url"] == before["url"]


def test_a_first_segment_link_shifts_even_though_its_chapter_pins_to_zero(monkeypatch, tmp_path):
    segs = [
        {"title": "Intro", "source_url": "https://x.test/intro"},
        {"title": "Story", "source_url": None},
        {"title": "Sign-off", "source_url": None},
    ]
    timeline, _ = _tl(
        monkeypatch, tmp_path, [20_000, 60_000, 15_000], [800, 800, 0], 3077, segments=segs
    )
    chapter0 = timeline["items"][0]["chapter"]
    link0 = timeline["items"][1]["link"]

    assert chapter0["start_time_ms"] == 0
    assert link0["start_time_ms"] == 3077 + 8000  # 40% of a 20s segment, shifted


def test_description_timestamps_come_from_the_shifted_plan(monkeypatch, tmp_path):
    _, description = _tl(monkeypatch, tmp_path, [20_000, 60_000, 15_000], [800, 800, 0], 3077)

    assert "(0:00) - Intro" in description
    assert "(0:23) - Story" in description  # 3077 + 20800 = 23877ms


# --- the artifact gate actually sees the timeline --------------------------


def test_verify_artifact_reads_the_schema_build_timeline_actually_emits(tmp_path):
    """REGRESSION: the gate read `start_ms` while the timeline has always carried
    `start_time_ms`, so `starts` was empty on every real run and the monotonic /
    5s-gap checks were vacuous. A music mix that shifted chapters wrongly would
    have sailed straight through."""
    mp3 = tmp_path / "e.mp3"
    mp3.write_bytes(b"ID3fake")
    timeline = {
        "items": [
            {"chapter": {"title": "a", "start_time_ms": 0}},
            {"chapter": {"title": "b", "start_time_ms": 1_000}},  # 1s apart: illegal
        ]
    }

    errors = render.verify_artifact(mp3, timeline, duration_ms=60_000, profile={})

    assert any("5000ms apart" in e or "5s apart" in e for e in errors), errors


def test_verify_artifact_rejects_a_duration_that_misses_the_mix_plan(tmp_path, monkeypatch):
    mp3 = tmp_path / "e.mp3"
    mp3.write_bytes(b"ID3fake")
    plan = _plan()
    timeline = {"items": [{"chapter": {"title": "a", "start_time_ms": 0}}]}

    ok = render.verify_artifact(
        mp3, timeline, duration_ms=plan.total_ms, profile={}, music_plan=plan
    )
    assert ok == []

    off = render.verify_artifact(
        mp3,
        timeline,
        duration_ms=plan.total_ms + render.MUSIC_DURATION_TOLERANCE_MS + 1,
        profile={},
        music_plan=plan,
    )
    assert any("music mix" in e for e in off), off


def test_verify_artifact_tolerates_encoder_drift_inside_the_documented_window(tmp_path):
    mp3 = tmp_path / "e.mp3"
    mp3.write_bytes(b"ID3fake")
    plan = _plan()
    timeline = {"items": [{"chapter": {"title": "a", "start_time_ms": 0}}]}

    assert (
        render.verify_artifact(
            mp3,
            timeline,
            duration_ms=plan.total_ms - render.MUSIC_DURATION_TOLERANCE_MS,
            profile={},
            music_plan=plan,
        )
        == []
    )


# --- provenance + invalidation --------------------------------------------


def _prov(tmp_path, **over):
    asset = tmp_path / "m.wav"
    asset.write_bytes(over.pop("bytes", b"RIFFfake"))
    return render.music_provenance(_music(asset=str(asset), **over), asset)


def _stale(workdir: Path) -> list[Path]:
    """Everything a settings change must invalidate, plus the speech it must not."""
    for name in (
        "music_bed.wav",
        "episode_raw.mp3",
        "episode.mp3",
        "timeline.json",
        "description.html",
        "seg_01.mp3",
        "seg_01.json",
    ):
        (workdir / name).write_text("x")
    render.mark_stage(workdir, "segments", count=3)
    render.mark_stage(workdir, "concat")
    render.mark_stage(workdir, "timeline")
    render.mark_stage(workdir, "artifact_gate")
    return [workdir / "seg_01.mp3", workdir / "seg_01.json"]


def test_first_run_writes_provenance_and_invalidates_nothing(tmp_path):
    wd = tmp_path / "wd"
    wd.mkdir()
    assert render.sync_music_provenance(wd, _prov(tmp_path)) is False
    assert json.loads((wd / "music.json").read_text())["asset_sha256"]


def test_an_unchanged_asset_and_config_reuses_everything(tmp_path):
    wd = tmp_path / "wd"
    wd.mkdir()
    prov = _prov(tmp_path)
    render.sync_music_provenance(wd, prov)
    _stale(wd)

    assert render.sync_music_provenance(wd, prov) is False
    assert (wd / "music_bed.wav").exists()
    assert render.stage_done(render.load_state(wd), "concat") is True


def test_a_changed_asset_hash_invalidates_the_mix_but_keeps_the_speech(tmp_path):
    wd = tmp_path / "wd"
    wd.mkdir()
    render.sync_music_provenance(wd, _prov(tmp_path))
    speech = _stale(wd)

    assert render.sync_music_provenance(wd, _prov(tmp_path, bytes=b"RIFFdifferent")) is True

    for gone in ("music_bed.wav", "episode_raw.mp3", "episode.mp3", "timeline.json"):
        assert not (wd / gone).exists(), f"{gone} survived a changed asset"
    for kept in speech:
        assert kept.exists(), f"{kept.name} was destroyed; TTS would be re-rendered"
    state = render.load_state(wd)
    assert render.stage_done(state, "segments") is True
    for dropped in ("concat", "timeline", "artifact_gate"):
        assert render.stage_done(state, dropped) is False


def test_a_changed_mix_parameter_invalidates_the_mix(tmp_path):
    wd = tmp_path / "wd"
    wd.mkdir()
    render.sync_music_provenance(wd, _prov(tmp_path))
    _stale(wd)

    assert render.sync_music_provenance(wd, _prov(tmp_path, duck_db=-12.0)) is True
    assert not (wd / "episode.mp3").exists()


def test_turning_music_off_invalidates_the_music_mixed_audio(tmp_path):
    """A stale concat marker from a music run must not survive into a run that
    was asked for no music."""
    wd = tmp_path / "wd"
    wd.mkdir()
    render.sync_music_provenance(wd, _prov(tmp_path))
    _stale(wd)

    assert render.sync_music_provenance(wd, None) is True
    assert not (wd / "episode.mp3").exists()
    assert not (wd / "music.json").exists()


def test_no_music_before_and_after_is_a_no_op(tmp_path):
    wd = tmp_path / "wd"
    wd.mkdir()
    _stale(wd)

    assert render.sync_music_provenance(wd, None) is False
    assert (wd / "episode.mp3").exists()


# --- the disabled path is byte-identical -----------------------------------


def test_concat_and_normalize_without_music_uses_the_original_loudnorm(monkeypatch, tmp_path):
    """Default loudnorm (-24 LUFS) is what every other show renders at. The
    audition's -19 is local to enabled music and must not leak."""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        render,
        "run",
        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    monkeypatch.setattr(render, "mp3_duration_ms", lambda p: 1000)
    seg = tmp_path / "seg_01.mp3"
    seg.write_bytes(b"x")

    render.concat_and_normalize([seg], [0], tmp_path)

    joined = " ".join(" ".join(c) for c in calls)
    assert "loudnorm=print_format=json" in joined
    assert "I=-19" not in joined
    assert "amix" not in joined
    assert "-filter_complex" not in joined


# --- real ffmpeg -----------------------------------------------------------

pytestmark_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="needs ffmpeg + ffprobe on PATH (CI installs neither)",
)


def _tone(path: Path, seconds: float, freq: int = 220) -> Path:
    """Stand-in for a speech segment: same encoder profile the renderer uses."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
            "-ar", "44100", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "192k", str(path),
        ],
        check=True,
    )  # fmt: skip
    return path


def _music_fixture(path: Path, seconds: float = 4.0) -> Path:
    """A small, loud, obviously-non-silent bed. Not the shipped WAV: a fixture
    must stay a fixture (the real episode audio in the handoff is not one)."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"sine=frequency=880:duration={seconds}",
            "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(path),
        ],
        check=True,
    )  # fmt: skip
    return path


def _rms_db(path: Path, start: float, duration: float) -> float:
    """Mean volume of one window of a rendered file, in dBFS."""
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
            "-i", str(path), "-af", "volumedetect", "-f", "null", "-",
        ],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    for line in proc.stderr.splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].strip().split()[0])
    raise AssertionError(f"no mean_volume in ffmpeg output for {path}")


@pytestmark_ffmpeg
def test_real_mix_lands_the_planned_duration_profile_and_envelopes(tmp_path):
    """The end-to-end claim, against real ffmpeg and real decoded audio: the right
    encoder profile, the planned duration, a non-silent lead and tail, NO music in
    the story region, and the -19 LUFS target reaching only the music render."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    seg_paths = [
        _tone(workdir / "seg_01.mp3", 8.0, 220),  # intro
        _tone(workdir / "seg_02.mp3", 14.0, 300),  # story
        _tone(workdir / "seg_03.mp3", 14.0, 330),  # story
        _tone(workdir / "seg_04.mp3", 7.0, 260),  # sign-off
    ]
    silences = [800, 800, 800, 0]
    music = _music(asset=str(_music_fixture(tmp_path / "bed.wav")), tail_seconds=4.0)

    raw = render.concat_segments(seg_paths, silences, workdir)
    seg_ms = [render.mp3_duration_ms(p) for p in seg_paths]
    plan = render.plan_music_mix(
        music, seg_ms=seg_ms, silences_ms=silences, speech_ms=render.mp3_duration_ms(raw)
    )
    bed = render.build_music_bed(render.resolve_music_asset(music), workdir, plan)
    final, loudnorm = render.normalize_episode(
        raw, workdir, music=render.MusicMix(plan=plan, bed=bed)
    )

    # 1. encoder profile is untouched by the mix (compared the way the gate does:
    #    ffprobe returns sample_rate as a string)
    profile = render.probe_audio_profile(final)
    for key, expected in render.ENCODER_PROFILE.items():
        assert str(profile[key]) == str(expected), f"{key}: {profile[key]!r}"

    # 2. duration matches the plan inside the documented tolerance, and the gate
    #    that checks it agrees.
    measured = render.mp3_duration_ms(final)
    assert abs(measured - plan.total_ms) <= render.MUSIC_DURATION_TOLERANCE_MS, (
        f"measured {measured}ms vs planned {plan.total_ms}ms"
    )
    assert (
        render.verify_artifact(
            final,
            {"items": [{"chapter": {"title": "a", "start_time_ms": 0}}]},
            duration_ms=measured,
            profile=profile,
            music_plan=plan,
        )
        == []
    )

    # 3. the lead is music alone and is not silent
    lead_db = _rms_db(final, 0.2, plan.lead_ms / 1000 - 0.4)
    assert lead_db > -45, f"opening bar is silent ({lead_db} dBFS)"

    # 4. the tail is music alone and is not silent
    tail_db = _rms_db(final, plan.total_ms / 1000 - plan.tail_ms / 1000 + 0.2, 1.0)
    assert tail_db > -45, f"tail is silent ({tail_db} dBFS)"

    # 5. NO music in the story region. The 800ms of planned silence BETWEEN the two
    #    stories is digital silence in the speech, so it is silent in the mix if and
    #    only if nothing was laid over it. This is the unambiguous form of the
    #    claim — comparing loudness against the no-music render only shows that the
    #    region did not get much louder.
    story_gap_start = (plan.lead_ms + seg_ms[0] + silences[0] + seg_ms[1]) / 1000
    gap_db = _rms_db(final, story_gap_start + 0.15, 0.5)
    assert gap_db < -50, f"music is playing between the stories ({gap_db} dBFS)"

    # 6. the -19 LUFS target is a property of the music config and reaches nothing
    #    else: the same speech with no music normalises to ffmpeg's -24 default.
    assert loudnorm is not None and loudnorm["output_i"] == pytest.approx(-19, abs=1.5)
    _, plain = render.normalize_episode(raw, workdir / "plain", music=None)
    assert plain is not None and plain["output_i"] == pytest.approx(-24, abs=1.5)


@pytestmark_ffmpeg
def test_real_mix_fades_the_intro_bed_out_by_the_first_story(tmp_path):
    """The bed must be gone — not merely ducked — once the stories start."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    seg_paths = [
        _tone(workdir / "seg_01.mp3", 10.0, 220),
        _tone(workdir / "seg_02.mp3", 20.0, 300),
        _tone(workdir / "seg_03.mp3", 7.0, 260),
    ]
    silences = [800, 800, 0]
    music = _music(asset=str(_music_fixture(tmp_path / "bed.wav")), tail_seconds=4.0)

    raw = render.concat_segments(seg_paths, silences, workdir)
    plan = render.plan_music_mix(
        music,
        seg_ms=[render.mp3_duration_ms(p) for p in seg_paths],
        silences_ms=silences,
        speech_ms=render.mp3_duration_ms(raw),
    )
    bed = render.build_music_bed(render.resolve_music_asset(music), workdir, plan)
    final, _ = render.normalize_episode(raw, workdir, music=render.MusicMix(plan=plan, bed=bed))

    # The 800ms of planned silence right before the first story is bed-only in the
    # intro region and speech-free — after the fade it must be near-silent.
    gap_start = plan.intro_end_ms / 1000 - 0.6
    assert _rms_db(final, gap_start, 0.4) < -50, "intro bed is still audible at the first story"


# --- end to end, through main() -------------------------------------------


def _music_manifest(tmp_path, **music_over) -> dict:
    return {
        "title": "Daily Digest - September 10, 2026",
        "summary": "Today's hook.",
        "date": "2026-09-10",
        "ship_mode": "web",
        "voice": "house",
        "music": _music(asset=str(_music_fixture(tmp_path / "bed.wav")), **music_over),
        "segments": [
            {"title": "Intro", "text": "Welcome in.", "source_url": None, "role": "intro"},
            {"title": "Story one", "text": "First.", "source_url": "https://x.test/a"},
            {"title": "Story two", "text": "Second.", "source_url": "https://x.test/b"},
            {"title": "Sign-off", "text": "Thanks.", "source_url": None, "role": "outro"},
        ],
    }


def _drive_music_dry_run(monkeypatch, tmp_path, workdir, manifest, seconds=(8, 14, 14, 7)):
    """main() --dry-run over a music manifest with real ffmpeg and stubbed TTS.

    Nothing here publishes: --dry-run in web mode never touches R2 or the network,
    and the Spotify seams are absent from this mode entirely."""
    import sys

    workdir.mkdir(parents=True, exist_ok=True)

    def fake_render_segments(segments, *a, **k):
        return [
            _tone(workdir / f"seg_{i + 1:02d}.mp3", seconds[i], 220 + i * 40)
            for i in range(len(segments))
        ]

    monkeypatch.setattr(render, "render_segments", fake_render_segments)
    monkeypatch.setattr(render, "load_config", lambda: {})
    monkeypatch.setattr(render, "load_r2_config", lambda cfg: None)
    monkeypatch.setattr(render, "_tts_module_check", lambda: render._check("tts-module", True, ""))
    monkeypatch.setattr(
        render, "_tts_engine_check", lambda spec: render._check("tts-engine", True, "")
    )
    monkeypatch.setattr(
        render, "check_r2_credentials", lambda *a, **k: {"ok": True, "detail": "stubbed"}
    )
    monkeypatch.setattr(render, "build_cover", lambda out, *a, **k: out.write_bytes(b"IMG"))
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(
        sys, "argv", ["render.py", "--manifest", str(path), "--workdir", str(workdir), "--dry-run"]
    )
    return render.main()


@pytestmark_ffmpeg
def test_a_music_dry_run_mixes_shifts_the_timeline_and_passes_the_gate(
    monkeypatch, tmp_path, capsys
):
    """The whole path, with the artifact gate live: pre-flight checks the asset,
    the mix runs, the timeline is shifted by the lead, and the gate — which now
    actually reads the chapters — passes."""
    workdir = tmp_path / "wd"
    manifest = _music_manifest(tmp_path, tail_seconds=4.0)

    assert _drive_music_dry_run(monkeypatch, tmp_path, workdir, manifest) == 0
    captured = capsys.readouterr()
    assert '"status": "dry-run"' in captured.out
    assert "artifact gate: PASS" in captured.err
    assert "music-asset" in captured.err  # pre-flight gated the asset under --dry-run

    episode = workdir / "episode.mp3"
    assert episode.exists()
    plan = json.loads((workdir / "music.json").read_text())["plan"]
    measured = render.mp3_duration_ms(episode)
    assert abs(measured - plan["total_ms"]) <= render.MUSIC_DURATION_TOLERANCE_MS

    seg_ms = [render.mp3_duration_ms(workdir / f"seg_{i:02d}.mp3") for i in range(1, 5)]
    timeline = json.loads((workdir / "timeline.json").read_text())
    starts = [i["chapter"]["start_time_ms"] for i in timeline["items"] if "chapter" in i]

    assert starts[0] == 0, "chapter one must contain the theme"
    # Every later chapter is its pre-music position plus exactly the lead: the lead
    # shifts the tail of the episode by one constant, it does not restretch it.
    pre_music, cursor = [], 0
    for i, dur in enumerate(seg_ms):
        pre_music.append(cursor)
        cursor += dur + (800 if i < len(seg_ms) - 1 else 0)
    assert starts[1:] == [p + plan["lead_ms"] for p in pre_music[1:]]

    # The bookends the mix used are the same numbers this timeline walked.
    assert plan["intro_end_ms"] == starts[1]
    assert plan["outro_start_ms"] == pre_music[-1] + plan["lead_ms"]

    # Source links moved with their chapters and kept their associations.
    links = [i["link"] for i in timeline["items"] if "link" in i]
    assert [x["url"] for x in links] == ["https://x.test/a", "https://x.test/b"]
    assert all(x["start_time_ms"] > plan["lead_ms"] for x in links)


@pytestmark_ffmpeg
def test_a_music_dry_run_records_the_mix_in_the_run_log(monkeypatch, tmp_path):
    workdir = tmp_path / "wd"
    runs = tmp_path / "runs.jsonl"
    monkeypatch.setattr(render, "RUN_LOG_PATH", runs)

    assert (
        _drive_music_dry_run(
            monkeypatch, tmp_path, workdir, _music_manifest(tmp_path, tail_seconds=4.0)
        )
        == 0
    )

    record = json.loads(runs.read_text().splitlines()[-1])
    assert set(record) == set(render.RUN_LOG_FIELDS)
    assert record["music"]["asset"] == "bed.wav"
    assert record["music"]["lead_ms"] == 3077
    assert record["music"]["output_lufs"] == -19.0


@pytestmark_ffmpeg
def test_a_music_free_dry_run_leaves_the_run_log_music_field_null(monkeypatch, tmp_path):
    workdir = tmp_path / "wd"
    runs = tmp_path / "runs.jsonl"
    monkeypatch.setattr(render, "RUN_LOG_PATH", runs)
    manifest = _music_manifest(tmp_path)
    del manifest["music"]

    assert _drive_music_dry_run(monkeypatch, tmp_path, workdir, manifest) == 0

    assert json.loads(runs.read_text().splitlines()[-1])["music"] is None


@pytestmark_ffmpeg
def test_a_rerun_with_changed_music_remixes_without_re_rendering_the_speech(monkeypatch, tmp_path):
    """The resume case the handoff calls out: changing the mix must invalidate the
    assembled audio and everything downstream of it, and must NOT throw away the
    TTS takes — which cost minutes and are valid whatever the music does."""
    workdir = tmp_path / "wd"
    assert (
        _drive_music_dry_run(
            monkeypatch, tmp_path, workdir, _music_manifest(tmp_path, tail_seconds=4.0)
        )
        == 0
    )
    first_episode = (workdir / "episode.mp3").read_bytes()
    speech = {p.name: p.read_bytes() for p in workdir.glob("seg_*.mp3")}
    bed = (workdir / "music_bed.wav").read_bytes()

    # The second run re-renders nothing: render_segments would raise if it were the
    # thing producing the segments, so pin the existing files instead.
    def reuse(segments, *a, **k):
        return [workdir / f"seg_{i + 1:02d}.mp3" for i in range(len(segments))]

    monkeypatch.setattr(render, "render_segments", reuse)
    changed = _music_manifest(tmp_path, tail_seconds=4.0, duck_db=-6.0)
    assert _drive_music_dry_run(monkeypatch, tmp_path, workdir, changed) == 0

    assert {p.name: p.read_bytes() for p in workdir.glob("seg_*.mp3")} == speech
    assert (workdir / "episode.mp3").read_bytes() != first_episode
    assert (workdir / "music_bed.wav").read_bytes() == bed  # same asset + targets


@pytestmark_ffmpeg
def test_a_broken_music_asset_fails_the_run_instead_of_publishing_without_music(
    monkeypatch, tmp_path
):
    workdir = tmp_path / "wd"
    manifest = _music_manifest(tmp_path, tail_seconds=4.0)
    manifest["music"]["asset"] = str(tmp_path / "gone.wav")

    with pytest.raises(SystemExit) as exc:
        _drive_music_dry_run(monkeypatch, tmp_path, workdir, manifest)

    assert exc.value.code != 0
    assert not (workdir / "episode.mp3").exists()


# --- sting mode (Frontier Commits' Midnight Terminal) ----------------------
#
# The second treatment. It is deliberately NOT a second implementation: it resolves
# into the same MusicPlan and renders through the same unbranched filter graph, so
# the tests below are about WHERE the music is allowed to be, not about a new path.

STING_MIN = {
    "enabled": True,
    "mode": "sting",
    "asset": "skills/frontier-commits/assets/music/midnight-terminal.wav",
}
STING_SECONDS = 2.307688  # one bar at 104 BPM — the measured asset


def _sting(**over) -> dict:
    return render.resolve_music_config({**STING_MIN, **over})


def _sting_plan(seg_ms=None, silences_ms=None, speech_ms=None, asset_seconds=None, **over):
    seg_ms = seg_ms or [30_000, 90_000, 90_000, 40_000, 20_000]
    silences_ms = silences_ms if silences_ms is not None else [800] * (len(seg_ms) - 1) + [0]
    speech_ms = speech_ms if speech_ms is not None else sum(seg_ms) + sum(silences_ms)
    return render.plan_music_mix(
        _sting(**over),
        seg_ms=seg_ms,
        silences_ms=silences_ms,
        speech_ms=speech_ms,
        asset_seconds=STING_SECONDS if asset_seconds is None else asset_seconds,
    )


def test_sting_defaults_never_duck_and_never_re_master_the_show():
    """A sting sits BESIDE the narration, not under it. There is nothing to duck,
    and no reason to move a show's loudness to play one — -24 LUFS is ffmpeg's
    loudnorm default, i.e. exactly what this show already renders at."""
    cfg = _sting()

    assert cfg["duck_db"] == 0.0
    assert cfg["output_lufs"] == -24.0
    assert cfg["lead_seconds"] is None and cfg["tail_seconds"] is None


def test_bed_defaults_are_untouched_by_the_second_mode():
    """Adding sting mode must not move Pixel Window a single decibel."""
    bed = _music()

    assert bed["mode"] == "bed"
    assert bed["duck_db"] == -18.0
    assert bed["output_lufs"] == -19.0
    assert bed["lead_seconds"] == pytest.approx(240 / 78)


def test_a_sting_lead_and_tail_are_the_MEASURED_asset_not_a_round_number():
    """2.307688s, not 2.0 and not 2.31: rounding a one-bar export clips its last
    beat, and the handoff calls this out by name."""
    plan = _sting_plan()

    assert plan.lead_ms == 2308
    assert plan.tail_ms == 2308
    assert plan.total_ms == 2308 + plan.speech_ms + 2308


def test_an_explicit_sting_length_still_wins_over_the_asset():
    plan = _sting_plan(lead_seconds=1.0, tail_seconds=4.0)

    assert (plan.lead_ms, plan.tail_ms) == (1000, 4000)


def test_a_sting_never_overlaps_speech():
    """The whole point: music stops where narration starts and resumes where it
    ends. Both bookends are pinned to the speech, not to a chapter boundary."""
    plan = _sting_plan()

    assert plan.intro_end_ms == plan.lead_ms
    assert plan.outro_start_ms == plan.lead_ms + plan.speech_ms
    assert plan.outro_duration_s == pytest.approx(plan.tail_ms / 1000)


def test_a_sting_gets_symmetric_de_click_edge_fades_not_a_three_second_bed_fade():
    plan = _sting_plan()

    assert plan.intro_fade_in_s == pytest.approx(0.03)
    assert plan.intro_fade_out_s == pytest.approx(0.03)
    assert plan.outro_fade_in_s == pytest.approx(0.03)
    assert plan.outro_fade_out_s == pytest.approx(0.03)
    # The bed's 3s exit would swallow a 2.3s sting whole.
    assert plan.intro_fade_out_s < render.MUSIC_FADE_OUT_SECONDS


def test_a_sting_graph_carries_no_gain_envelope_at_all():
    """duck_db 0 makes both envelopes a constant 1.0; emitting them would put a
    per-frame expression eval on a straight line."""
    graph = render.music_filter_graph(_sting_plan())

    assert "volume=" not in graph
    assert "afade=t=in:d=0.030000" in graph
    assert "loudnorm=I=-24" in graph
    # ...while the bed still has its envelope.
    assert "volume=" in render.music_filter_graph(_plan())


def test_a_sting_needs_only_an_intro_and_a_signoff():
    """A bed reads the bookend geometry and needs a story region between them; a
    sting reads none of it."""
    assert _sting_plan(seg_ms=[30_000, 20_000], silences_ms=[800, 0]).lead_ms == 2308
    with pytest.raises(SystemExit):
        _sting_plan(seg_ms=[30_000], silences_ms=[0])


def test_a_null_length_without_a_measured_asset_dies_rather_than_guessing():
    with pytest.raises(SystemExit):
        render.plan_music_mix(
            _sting(), seg_ms=[30_000, 20_000], silences_ms=[800, 0], speech_ms=50_800
        )


def test_an_unknown_mode_dies():
    with pytest.raises(SystemExit):
        render.validate_manifest(_manifest(music=_music(mode="jingle")))


def test_a_null_length_is_refused_in_bed_mode():
    """In a bed, `lead_seconds` is a musical bar — not the whole composition."""
    with pytest.raises(SystemExit):
        render.validate_manifest(_manifest(music=_music(lead_seconds=None)))


def test_a_valid_sting_manifest_passes():
    render.validate_manifest(_manifest(music=_sting()))


def test_the_bundled_sting_exists_and_matches_its_recorded_hash():
    asset = render.MUSIC_ASSET_BASE / STING_MIN["asset"]
    assert asset.exists(), "bundled Frontier Commits sting is missing"
    provenance = json.loads((asset.parent / "PROVENANCE.json").read_text())
    assert render.artifact_fingerprint(asset) == provenance["sha256"]
    # The handoff is explicit that this is ONE BAR; the provenance must say so, and
    # the recorded length must be the one the plan will use.
    assert provenance["duration_s"] == STING_SECONDS
    assert "ONE BAR" in provenance["WHAT_THIS_IS"]


def test_each_show_owns_its_music_under_its_own_skill_dir():
    """One renderer, several shows: the asset base is the plugin root so Frontier
    Commits' sting never has to live in the daily show's folder."""
    assert (render.MUSIC_ASSET_BASE / "skills" / "daily-podcast" / "assets" / "music").is_dir()
    assert (render.MUSIC_ASSET_BASE / "skills" / "frontier-commits" / "assets" / "music").is_dir()


@pytestmark_ffmpeg
def test_real_sting_mix_brackets_the_speech_and_leaves_it_untouched(tmp_path):
    """Real ffmpeg, real decoded audio: the sting plays at both ends, the speech
    region is bit-for-bit what it would be with no music at all, and the profile
    and duration land on the plan."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    seg_paths = [
        _tone(workdir / "seg_01.mp3", 6.0, 220),  # cold open
        _tone(workdir / "seg_02.mp3", 16.0, 300),  # story
        _tone(workdir / "seg_03.mp3", 8.0, 260),  # sign-off
    ]
    silences = [800, 800, 0]
    sting = _sting(asset=str(_music_fixture(tmp_path / "sting.wav", seconds=STING_SECONDS)))

    raw = render.concat_segments(seg_paths, silences, workdir)
    plan = render.plan_music_mix(
        sting,
        seg_ms=[render.mp3_duration_ms(p) for p in seg_paths],
        silences_ms=silences,
        speech_ms=render.mp3_duration_ms(raw),
        asset_seconds=render._probe_duration_s(render.resolve_music_asset(sting)),
    )
    bed = render.build_music_bed(render.resolve_music_asset(sting), workdir, plan)
    final, loudnorm = render.normalize_episode(
        raw, workdir, music=render.MusicMix(plan=plan, bed=bed)
    )

    profile = render.probe_audio_profile(final)
    for key, expected in render.ENCODER_PROFILE.items():
        assert str(profile[key]) == str(expected), f"{key}: {profile[key]!r}"

    measured = render.mp3_duration_ms(final)
    assert abs(measured - plan.total_ms) <= render.MUSIC_DURATION_TOLERANCE_MS
    assert plan.lead_ms == pytest.approx(round(STING_SECONDS * 1000), abs=2)

    # The opening bar is there and is not silent.
    assert _rms_db(final, 0.1, plan.lead_ms / 1000 - 0.2) > -45
    # The closing bar is there and is not silent.
    assert _rms_db(final, plan.outro_start_ms / 1000 + 0.1, plan.tail_ms / 1000 - 0.2) > -45

    # NOTHING between them: every planned inter-chapter silence stays digital
    # silence, at the top of the speech and at the bottom.
    first_gap = (plan.lead_ms + render.mp3_duration_ms(seg_paths[0])) / 1000
    last_gap = first_gap + (800 + render.mp3_duration_ms(seg_paths[1])) / 1000
    for at in (first_gap, last_gap):
        assert _rms_db(final, at + 0.15, 0.4) < -50, f"music leaked into the speech at {at:.2f}s"

    # ...and the narration is not re-mastered to play a sting: same target as a
    # music-free render of the same speech.
    _, plain = render.normalize_episode(raw, workdir / "plain", music=None)
    assert loudnorm["output_i"] == pytest.approx(plain["output_i"], abs=0.5)


@pytestmark_ffmpeg
def test_the_sting_edge_fades_actually_taper(tmp_path):
    """'Short edge fades to avoid abrupt cuts' — asserted on the decoded audio, not
    on the presence of an afade token in the graph."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    seg_paths = [
        _tone(workdir / "seg_01.mp3", 6.0, 220),
        _tone(workdir / "seg_02.mp3", 16.0, 300),
        _tone(workdir / "seg_03.mp3", 8.0, 260),
    ]
    silences = [800, 800, 0]
    # A CONSTANT-amplitude fixture, so any taper at the edges is the fade and
    # nothing else — the real sting's own decay would mask it.
    sting = _sting(
        asset=str(_music_fixture(tmp_path / "sting.wav", seconds=STING_SECONDS)),
        intro_fade_seconds=0.25,
        outro_fade_seconds=0.25,
    )
    raw = render.concat_segments(seg_paths, silences, workdir)
    plan = render.plan_music_mix(
        sting,
        seg_ms=[render.mp3_duration_ms(p) for p in seg_paths],
        silences_ms=silences,
        speech_ms=render.mp3_duration_ms(raw),
        asset_seconds=STING_SECONDS,
    )
    bed = render.build_music_bed(render.resolve_music_asset(sting), workdir, plan)
    final, _ = render.normalize_episode(raw, workdir, music=render.MusicMix(plan=plan, bed=bed))

    body = _rms_db(final, 0.5, 0.5)
    lead_in = _rms_db(final, 0.0, 0.06)
    lead_out = _rms_db(final, plan.lead_ms / 1000 - 0.06, 0.06)

    assert lead_in < body - 6, f"opening edge does not taper ({lead_in} vs {body})"
    assert lead_out < body - 6, f"closing edge does not taper ({lead_out} vs {body})"
