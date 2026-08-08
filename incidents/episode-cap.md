# Episode-cap 429

**First seen:** 2026-07-17 · **Blocked runs:** 07-17 → 07-20 · **Severity:** was pipeline-blocking

## Symptom

`save-to-spotify upload` fails with an **empty stderr** — the exact surface
signature of a [transient flake](transient-upload-failure.md), which is what made
this expensive. The real error is in the CLI's *stdout*, doubly wrapped:

```json
{"error":"API error (429): {\"error_code\":\"RATE_LIMIT_EXCEEDED\",\"reason\":\"capacity\",
 \"message\":\"You've reached the episode limit. Delete existing episodes to create new ones.\"}"}
```

Re-running reproduces it forever and burns a full render each time.

## Root cause

A Spotify show holds at most **60 episodes** (confirmed hard). A daily cadence
against a fixed cap means every run past day 60 is blocked by default. A `FAILED`
episode still counts against the cap.

## Automated remedy

Two layers:

**Reactive** (`upload`, shipped in #79): on a *confirmed* cap 429 — gated on the
parsed inner `error_code == "RATE_LIMIT_EXCEEDED"` **and** `reason == "capacity"`,
never a substring of the human message — prune the oldest episode(s) and retry the
upload once. Opt-in via `auto_prune_episodes`, bounded by `max_prune_per_run`,
scoped to the configured `show_id`, prefers `FAILED` episodes explicitly (never
"anything != READY", so a concurrent run's `NOT_READY` episode is safe), skips
unparseable `created_at`, and logs every deletion into `pruned_episodes`.

**Preventive** (`preflight_capacity`, new): the count is checked **before** the
render. At the cap, the slot is reclaimed up front, so the 429 costs nothing
instead of costing a ~5-minute TTS render that was always going to fail. With
`auto_prune_episodes` disabled, pre-flight *refuses the run* rather than silently
deleting an episode — opt-in stays opt-in. `--dry-run` never prunes.

> **This is a standing rolling delete, not invisible maintenance.** The show sits
> at 60/60 permanently: every run permanently destroys the then-oldest published
> episode. The lever is `max_prune_per_run` / turning the key off — not a code change.

## Test that guards it

- `test_preflight_capacity_pre_prunes_at_cap_before_any_render`
- `test_preflight_capacity_fails_at_cap_when_auto_prune_disabled`
- `test_preflight_capacity_never_prunes_on_dry_run`
- `test_preflight_capacity_passes_when_below_cap`
- `test_cap_429_does_not_consume_the_transient_retry` — the cap path and the new
  transient retry must never compound into repeated destructive prunes.
- Pre-existing: `test_upload_cap_with_auto_prune_deletes_and_retries`,
  `test_upload_cap_retry_also_429_fails_without_second_prune`,
  `select_episodes_to_prune` tiering tests.

## Note

A fix in the repo is not a fix in the run. This cap stayed "unsolved" for a day
*after* the fix merged and released, because the scheduled run executes from the
version-keyed plugin cache (`~/.claude/plugins/cache/clodcast/clodcast/<version>/`),
which still held the old version. Before concluding a known-fixed bug regressed,
grep the **cached** `render.py`, not the repo copy.
