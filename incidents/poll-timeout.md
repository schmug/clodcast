# Readiness-poll timeout

**First seen:** 2026-07-28 · **Severity:** false failure (episode was healthy)

## Symptom

A wall of `NOT_READY` followed by:

```
error: episode not READY after 600s
```

The episode was fine. It uploaded at 11:13:29Z, was still `PROCESSING` when the
poll expired, and reached `READY` roughly **16 minutes** after upload.

## Root cause

Two separate defects.

**1. The window was too short.** `poll_ready` gave up after 600 s. Spotify's
processing legitimately exceeds that.

**2. "Not in the listing" was read as terminal.** When polling via the show
listing, that listing intermittently returns a set that does *not* contain the
just-uploaded episode. A loop that exits on it reports a phantom disappearance —
the episode was present and `PROCESSING` on the very next query.

The timeout left the episode live on Spotify with no timeline, no R2 publish, and
an `inflight.json`. Reflexively treating it as a failed ship, or reflexively
`rm`-ing `inflight.json`, both did the wrong thing.

## Automated remedy

- Default window raised to **1800 s** (`DEFAULT_POLL_TIMEOUT_S`), configurable via
  `poll_timeout_s` in `config.json`. An unparseable or non-positive value falls
  back to the default rather than turning the poll into an instant failure.
- `episode_status` returns `None` for "cannot determine right now" — explicitly
  *not* "gone". A `None` is waited through.
- It falls back to the listing form (`episodes --show-id <id>`) when
  `episodes status <id>` errors. (`episodes status <id> --show-id <show>` rejects
  the flag — the listing is the only other way to read server state.)
- `wait_for_readiness` returns a status instead of exiting, so callers that must
  survive a terminal state can branch. `poll_ready` keeps the die-on-`FAILED`
  contract for the fresh path.
- The timeout message now names the correct next step instead of implying a dead
  episode: check the listing, resume with the same `--workdir`, don't re-render.

## Test that guards it

- `test_poll_timeout_defaults_to_1800s`
- `test_poll_ready_keeps_waiting_through_processing` — including a transient
  `None` mid-sequence.
- `test_episode_missing_from_listing_is_transient_not_terminal`
- `test_episode_status_falls_back_to_the_show_listing`
- `test_poll_ready_dies_on_failed` — `FAILED` is still terminal.
