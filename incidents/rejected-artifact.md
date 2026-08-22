# Re-upload of a known-rejected artifact

**First seen:** 2026-08-08 · **Severity:** destructive

## Symptom

An episode is re-uploaded after a [processing rejection](processing-failed.md)
and is rejected again in exactly the same way: `NOT_READY → FAILED`.

## Root cause

On 2026-08-08 two independent uploads of one **byte-identical** mp3
(`4Hjc2Y2sPEkV8fxBxSvzAW`, then `76pqqwISWVROUOSzNjZqf9`) both failed processing.
The rejection follows the artifact, not luck.

What makes this worse than futile is the interaction with
[auto-prune](episode-cap.md). The show sits at 60/60, so **every** upload attempt
hits the cap 429 and permanently deletes a published episode to make room. Two
failed attempts of the same dead artifact cost one real, listenable episode.

The artifact was **not** locally diagnosable — it passed every documented
constraint. So the guard cannot be "detect the bad file"; it can only be "refuse
to try the same bytes twice".

## Automated remedy

Every artifact whose episode is rejected server-side is appended to
`~/.config/daily-podcast/rejections.jsonl` with its sha256 and ffprobe profile —
written by `_abandon_inflight` when in-flight recovery finds a `FAILED` episode
and the workdir artifact survives.

`verify_artifact` runs after the render and **before** the upload, and hard-fails
a byte-identical retry:

```
error: artifact gate failed: artifact was previously rejected by Spotify
(sha256 f3ca05c8cde8…); re-uploading identical bytes reproduces the failure
and costs a pruned episode
```

The log is append-only and tolerates corrupt lines (a malformed entry is skipped,
never blocks a legitimate ship).

The gate also runs a **local conformance check** — encoder profile, monotonic
chapter starts, the 5 s minimum gap between consecutive chapter starts, last
chapter inside the duration. (Until 2026-08-22 it also counted sub-30 s chapters
against a max of 3; upstream removed that platform rule, so the gate no longer
checks it.) To be
explicit about scope: none of those would have caught the 2026-08-08 artifact.
They guard render regressions; only the fingerprint blocklist addresses this
incident, and only by refusing the retry.

To legitimately re-ship the same content, change the artifact (re-render, e.g.
after a script edit) — a different encode produces a different fingerprint.

## Test that guards it

- `test_rejected_fingerprint_blocks_a_byte_identical_reupload`
- `test_rejections_log_is_append_only`
- `test_rejections_log_tolerates_corrupt_lines`
- `test_artifact_fingerprint_is_content_addressed`
- `test_abandoned_episode_fingerprint_is_recorded_when_artifact_survives`
- `test_dry_run_exercises_the_artifact_gate` — the gate runs in rehearsal too.
- Conformance: `test_verify_artifact_rejects_wrong_channel_count`,
  `…_wrong_sample_rate`, `…_last_chapter_at_or_past_duration`,
  `…_non_monotonic_chapters`, `…_too_many_short_chapters`,
  `test_verify_artifact_accepts_a_conformant_episode`.
