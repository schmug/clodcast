# Mid-run connection drop / process kill

**First seen:** 2026-07-01 (SIGTERM at the 10-minute Bash cap) · **Severity:** lost work

## Symptom

The process dies mid-run — exit 143 from a SIGTERM, a dropped connection, a
crashed harness. Everything from that point is gone, and if the run had no
explicit `--workdir` there is no way to name the directory the work landed in.

## Root cause

Three gaps, in increasing order of cost:

1. **The auto workdir was `tempfile.mkdtemp()`** — a random suffix. Resume only
   ever triggered with an *explicit* `--workdir`, so an unattended run that
   forgot the flag simply lost the render. There was nothing to resume *into*.
2. **No record of progress.** `uploaded.json` marked the upload, and the
   per-segment TTS cache made re-rendering cheap, but nothing recorded which
   stages had completed.
3. **A ~12-chapter episode routinely exceeds a 10-minute foreground cap** — TTS
   (~4–5 min) plus upload plus several `NOT_READY` poll cycles.

## Automated remedy

**Deterministic auto workdir.** `default_workdir()` returns
`<tmp>/daily-podcast-<date>`, so a bare re-invocation with no arguments lands in
the same directory and reuses its TTS cache, artifacts, and state. Resume is no
longer opt-in.

**Durable stage checkpoints.** `<workdir>/state.json` records each completed
stage and its metadata, written atomically after the stage succeeds:

```json
{"stages": {"preflight": {"checks": 7, "completed_at": "..."},
            "segments": {"count": 12, "completed_at": "..."},
            "concat": {"loudnorm": {...}}, "cover": {}, "timeline": {"chapters": 12},
            "artifact_gate": {}, "upload": {"episode_uri": "..."}, "poll_ready": {},
            "r2": {"status": "published"}, "dedup": {"urls": 12}}}
```

Best-effort by contract: a corrupt or unwritable state file degrades to "nothing
completed" (the run redoes work) rather than wedging future runs. It supersedes
nothing — `uploaded.json` remains the authoritative upload marker, and
`covered.json` remains the sole dedup source of truth.

Preserved guarantees: auto-delete-on-*fresh-success* only; an explicit
`--workdir` is never auto-deleted; a **failed run always keeps its workdir**,
which is exactly when resume matters.

## Still operator-facing

Backgrounding is still the right call for a long in-session render (`nohup … &`
plus a log tail), because a 10-minute foreground cap is a property of the harness,
not of this pipeline. The difference is that hitting it now costs a re-invocation
rather than a re-render.

## Test that guards it

- `test_auto_workdir_is_deterministic_per_date`
- `test_state_starts_empty_and_marks_stages`
- `test_state_survives_reload_and_is_append_only_across_stages`
- `test_state_treats_corrupt_file_as_empty`
- Pre-existing: `test_successful_auto_workdir_is_deleted`,
  `test_keep_workdir_preserves_auto_workdir`,
  `test_explicit_workdir_never_auto_deleted`, and the per-segment TTS cache tests.
