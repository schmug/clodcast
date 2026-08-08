# Pre-flight refused the run

**Severity:** none — this is the system working

## Symptom

```
preflight: FAIL (5/6)
error: preflight failed (r2-credentials); nothing was rendered or uploaded
```

## Root cause

Not a failure mode of its own — it is the gate catching one of the others before
it costs anything. Read the failing check name and go to the matching file:

| Failing check | Meaning | See |
|---|---|---|
| `ffmpeg` / `ffprobe` | not on `PATH` | install ffmpeg |
| `encoder-profile` | encoder constants drifted from mono/44.1 kHz | [rejected-artifact.md](rejected-artifact.md) |
| `house-voice` | ref clip or transcript missing | `refs/house_voice.wav` |
| `tts-module` | `mlx_audio` not importable | `pip install -r requirements.txt` |
| `show-id` | no `show_id` in manifest or `config.json` | `~/.config/daily-podcast/config.json` |
| `r2-credentials` | R2 **partially** configured | [r2-skip-on-resume.md](r2-skip-on-resume.md) |
| `save-to-spotify-auth` | CLI missing or not authenticated | `save-to-spotify auth login` |
| `episode-capacity` | at the cap, nothing prunable | [episode-cap.md](episode-cap.md) |

## Automated remedy

The gate itself *is* the remedy: it runs before TTS, so a failure costs seconds
instead of a full render (and, at the cap, instead of a destructive prune).

`--dry-run` runs the local subset only — it never calls Spotify and never prunes.
`--skip-preflight` bypasses the gate when a check is wrong and you need to ship
anyway; you own the outcome.

## Test that guards it

- `test_preflight_aborts_render_before_tts` — asserts TTS does **not** run after a
  failed pre-flight.
- `test_skip_preflight_flag_bypasses_the_gate`
- `test_preflight_dry_run_skips_spotify_calls`
- `test_preflight_records_checks_into_the_run_record`
- `test_preflight_gates_on_the_tts_module_being_importable`
