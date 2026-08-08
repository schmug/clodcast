# Transient upload failure (empty stderr)

**First seen:** 2026-06-07 · **Severity:** false failure

## Symptom

```
error: command failed: save-to-spotify --json upload …
stderr:
```

An empty stderr and nothing else. The same command succeeded on an immediate
re-run — it was transient, not a config or auth problem
(`save-to-spotify auth status` was Authenticated throughout).

## Root cause

Two things.

**The diagnosis was opaque.** `save-to-spotify` returns its real error as JSON on
**stdout**, which the old `run()` helper raised past and never displayed. So every
upload failure looked identical regardless of cause — including the
[episode-cap 429](episode-cap.md), which is *not* transient and never recovers.
`parse_s2s_error` now surfaces the structured stdout error, which is what makes
the two distinguishable at all.

**There was no retry.** A single transient flake failed the whole run, and the
documented remedy was for a human to re-run with the same `--workdir`.

## Automated remedy

`upload()` retries a **non-cap** failure exactly once after a short backoff. A
confirmed cap 429 takes the prune-then-retry-once path instead and never falls
through to the transient retry, so the two retry budgets cannot compound into
repeated destructive prunes.

Exactly one retry, never a loop: a genuinely broken upload path should fail fast
rather than being hammered. If the retry also fails, the run dies with the
structured diagnostic.

A failed upload is clean — no `uploaded.json`, no `inflight.json`, `covered.json`
untouched — so a retry cannot duplicate an episode.

## Test that guards it

- `test_upload_retries_once_on_a_transient_failure`
- `test_upload_dies_after_the_single_transient_retry` — asserts exactly two attempts.
- `test_cap_429_does_not_consume_the_transient_retry` — asserts exactly one prune
  and two attempts on the cap path.
