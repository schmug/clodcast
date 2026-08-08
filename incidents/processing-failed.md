# Spotify processing rejection (the poison pill)

**First seen:** 2026-06-29 · **Recurred:** 2026-08-08 · **Severity:** was pipeline-fatal

## Symptom

The render completes normally, the upload succeeds, `timeline set` succeeds, then
readiness sits at `NOT_READY` for many minutes before flipping to a **stable**
`FAILED`:

```
  status: NOT_READY
  status: NOT_READY
  status: FAILED
error: episode processing FAILED
```

The worse symptom is the day *after*. Every subsequent run died before rendering
anything:

```
in-flight recovery: found leftover episode spotify:episode:… (11 url(s))
in-flight recovery: re-running timeline set + poll for spotify:episode:…
  status: FAILED
error: episode processing FAILED
```

## Root cause

Two distinct things, and conflating them is what made this so costly.

**1. The rejection itself is server-side and not locally diagnosable.** The
2026-08-08 artifact passed every documented constraint — decoded clean, mono
44.1 kHz / 192 kbps / 575 s, monotonic timeline, last chapter at 559 s inside a
575 s episode, only 2 sub-30 s chapters against a max of 3. Two independent
uploads of the byte-identical mp3 both went `NOT_READY → FAILED`, so the reject
follows the *artifact*, not luck. There is no local check that predicts it. Do
not go hunting for a render bug; there wasn't one.

**2. The pipeline-fatal part was ours.** `upload()` writes
`~/.config/daily-podcast/inflight.json` so a crash before dedup can be recovered
cross-day. `_recover_inflight` ran *before* rendering a new episode and called
`poll_ready`, which `die()`s on `FAILED`. So recovery crashed — and because a
crash during recovery deliberately leaves `inflight.json` intact, it re-poisoned
the next run, and the next. One rejected episode disabled the pipeline
indefinitely. The manual remedy was `rm ~/.config/daily-podcast/inflight.json`.

## Automated remedy

`_recover_inflight` now polls with `wait_for_readiness`, which **returns** a
status instead of exiting, and branches:

- `READY` → finish the tail, mark URLs covered, clear the log (unchanged).
- `TIMEOUT` → leave `inflight.json` in place and stop, preserving the
  crash-safety guarantee. It may still be processing; see
  [poll-timeout.md](poll-timeout.md).
- `FAILED` → **abandon** it (`_abandon_inflight`): write an incident, record the
  artifact's sha256 in `rejections.jsonl`, clear `inflight.json`, and let today's
  episode render.

Two deliberate non-actions:

- **`covered.json` is not written.** Those URLs never shipped, so they return to
  the curation pool. This is exactly why the manual `rm` was safe.
- **The dead episode is not deleted from Spotify.** Deleting a published episode
  is irreversible and stays human-gated. The capacity prune already prefers
  `FAILED` episodes, so it is reclaimed at the next prune anyway.

Recording the fingerprint matters because auto-prune is on: every retry of a
known-dead artifact permanently deletes a published episode to free a cap slot.
See [rejected-artifact.md](rejected-artifact.md).

## Test that guards it

- `test_recover_inflight_abandons_a_failed_episode_and_unblocks_the_run` — asserts
  the run does not raise, `inflight.json` is cleared, the URLs are **not** in
  `covered.json`, and an incident is left behind.
- `test_recover_inflight_still_completes_a_healthy_episode` — the give-up path
  must not weaken normal cross-day recovery.
- `test_abandoned_episode_fingerprint_is_recorded_when_artifact_survives`
- `test_poll_ready_dies_on_failed` — the fresh path still treats `FAILED` as terminal.
