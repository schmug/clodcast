# R2 web-feed publish silently skipped on resume

**First seen:** 2026-07-28 · **Severity:** silent partial ship

## Symptom

A run recovered via the `--workdir` resume path reports success, but the episode
never appears on the website / RSS feed. The only trace is one field:

```json
{"status": "ready", "resumed": true, "r2_status": "skipped"}
```

Spotify (canonical) is fine. Only the web feed is missing — typically discovered
days later.

## Root cause

The resume path was *deliberately* config-free: it resolved R2 settings from env
and `secrets.json` only, and an explicit test pinned that `_resume` never calls
`load_config`. But `r2_bucket` and `r2_public_base_url` live in **`config.json`**,
while only the three credentials live in `secrets.json`. So on resume the bucket
and base URL resolved from nothing, `load_r2_config` returned `None`, and the
publish no-oped with `[r2] not configured, skipping`.

The invariant that was supposed to keep resume simple was the bug.

This is exactly the wrong moment to lose the publish: a failed-run recovery
(poll timeout, transient flake, mid-ingest drop) is *precisely* when you reach for
resume.

## Automated remedy

Two changes.

**The invariant was retired deliberately.** `_render` now resolves the config once
and passes it into `_resume`, so the recovery path sees `r2_bucket` /
`r2_public_base_url` like the fresh path does. `_resume` still never calls
`load_config` itself — it remains a pure function of its arguments and still works
with `config=None` (env-only) for a bare recovery. The config load on the resume
branch is deliberately **tolerant**: a missing `config.json` degrades to `{}`
rather than dying mid-recovery, so a recovery still works on a box without one.

**Pre-flight fails a half-configured R2.** `check_r2_credentials` is three-state:

| State | Meaning | Verdict |
|---|---|---|
| `configured` | all five settings resolve | PASS |
| `absent` | none of them resolve | **PASS** — the web feed is optional |
| `partial` | some but not all | **FAIL** |

The asymmetry is the point: `absent` is a legitimate configuration, `partial` is
the shape of this incident and is caught before the render instead of after the ship.

## Test that guards it

- `test_r2_credentials_partial_fails` — asserts the missing key names are reported.
- `test_r2_credentials_absent_is_a_pass_not_a_failure`
- `test_r2_credentials_complete_passes`
- `test_resume_skips_upload_and_runs_idempotent_tail` — updated: it now asserts
  `load_config` is not called *inside* `_resume`, rather than not called at all.
