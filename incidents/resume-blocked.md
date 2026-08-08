# Resume refused

**Severity:** low — fails safe

## Symptom

```
error: workdir has uploaded.json but missing episode.mp3; cannot resume safely
```

or

```
error: <workdir>/uploaded.json unreadable or missing episode_uri (…); cannot resume
```

## Root cause

The workdir contains an upload marker — meaning an episode is already live on
Spotify — but the artifacts needed to finish its tail are gone or unreadable
(temp cleared, partial copy, truncated marker).

## Automated remedy

Deliberately none. This path **fails fast on purpose**: the alternative is
re-uploading, which would create a *duplicate episode* for content that already
shipped, and — at the 60-episode cap — permanently delete a published episode to
make room for the duplicate. Failing is the correct outcome.

## What to do

1. Read the episode URI out of `uploaded.json` (or `~/.config/daily-podcast/inflight.json`).
2. Check its readiness: `save-to-spotify --json episodes status <bare-id>`.
   - `READY` → the episode shipped. If its URLs are missing from `covered.json`,
     the in-flight recovery on the next run will reconcile them.
   - `PROCESSING` → wait; see [poll-timeout.md](poll-timeout.md).
   - `FAILED` → [processing-failed.md](processing-failed.md); recovery now
     abandons it automatically.
3. To deliberately re-ship *the same content* as a new episode, delete
   `uploaded.json` (or use a fresh `--workdir`) — but re-uploading identical bytes
   after a rejection is blocked by [the artifact gate](rejected-artifact.md).

## Test that guards it

- Pre-existing: `test_resume_dies_when_artifact_missing`,
  `test_resume_skips_upload_and_runs_idempotent_tail`.
