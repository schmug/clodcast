# Pipeline reliability: pre-flight, durable state, incident capture

**Date:** 2026-08-08
**Status:** approved (audit + four design decisions confirmed by Cory)

## Problem

Every expensive stage of the daily-podcast pipeline runs *before* anything is
verified. Nine failure modes have hit production; five of them require a human
to type a recovery command, and three of those (processing rejection, poll
timeout, transient upload flake) present identically in the render log — "render
succeeded, then died at the tail" — while demanding different correct responses.

The worst of them is a **poison pill**: a Spotify-side processing rejection
leaves `inflight.json` behind, and `_recover_inflight` re-polls that dead episode
on every subsequent run, dying before today's episode renders. One bad episode
disables the pipeline indefinitely until someone deletes a file by hand.

## Audit

| # | Failure mode | Detection signal | Current handling | Auto? |
|---|---|---|---|---|
| 1 | Spotify processing rejection | `poll_ready` reads `readiness:"FAILED"` | `die()`; `inflight.json` survives → poison pill | human |
| 2 | Episode-cap 429 | nested stdout JSON `RATE_LIMIT_EXCEEDED`/`capacity` | prune + retry once | auto (reactive) |
| 3 | WebFetch source blocks | `unable to fetch` / 403 / 404 in curation | item dropped to `dropped.jsonl` | partial |
| 4 | Readiness-poll timeout | `episode not READY after 600s` | `die()` | human |
| 5 | Mid-run connection drop | non-zero exit / SIGTERM mid-run | TTS cache + `uploaded.json`; auto workdir unresumable | partial |
| 6 | Transient upload failure | `CalledProcessError`, empty stderr, non-cap | `die()`, no retry | human |
| 7 | R2 skipped on resume | `r2_status:"skipped"` | resume is config-free by design | human |
| 8 | Child `claude -p` 401 | `AUTH_RE` in `classify_output` | fail-fast diagnostic | human |
| 9 | Pre-flight never runs | — | `--selftest` exists, nothing calls it | human |

## Design

### 1. Pre-flight stage (`preflight()`)

Runs inside `_render` before TTS, on every run. Ordered checks, all recorded:

1. `ffmpeg` / `ffprobe` on PATH
2. `save-to-spotify` auth returns valid JSON (skipped on `--dry-run`)
3. `config.json` parses and carries `show_id`
4. House-voice ref clip + transcript resolve
5. **Episode capacity** — list the show, compare against `episode_cap`
   (default 60) and *pre-prune* when at the cap, so the 429 never costs a render
6. **R2 credentials** — three-state. Fully configured passes; fully absent is a
   pass with a note; **partially** configured is a FAIL, because that is exactly
   the silent-web-feed-miss shape of failure mode 7
7. **Encoder profile** — the compiled-in encode settings must match the
   known-good `mono / 44.1 kHz / 192 kbps` profile

A failure aborts before any expensive work. `--skip-preflight` is the escape
hatch; `--dry-run` runs the local subset (no Spotify calls, never prunes).

### 2. Artifact gate (`verify_artifact`) — post-render, pre-upload

Pre-flight cannot see an mp3 that does not exist yet, so artifact-level checks
run immediately before `upload()`:

- **Rejection fingerprint blocklist.** Every artifact whose episode is rejected
  server-side is recorded to `rejections.jsonl` by sha256 + ffprobe profile. A
  byte-identical re-upload is refused. This is the one thing the 08-08 incident
  proves: the same bytes were rejected twice, deterministically.
- **Full local conformance gate.** Channels / sample rate / codec, monotonic
  chapter starts, `MAX_SHORT_CHAPTERS` sub-30 s chapters, last chapter strictly
  inside the episode duration, no timeline item past the end.

The 08-08 note is explicit that the rejected artifact passed every documented
constraint, so the conformance gate is **not** presented as a fix for
server-side rejection — it guards render regressions. Only the fingerprint
blocklist addresses the rejection itself, and only by refusing a known-dead retry.

### 3. Durable run state (`<workdir>/state.json`)

One atomic JSON file recording completed stages plus their outputs. Combined
with a **deterministic per-date auto workdir** (`daily-podcast-<date>` instead of
`mkdtemp()`), every run is resumable — a dropped connection re-enters at the
first incomplete stage rather than re-rendering. `uploaded.json` stays the
authoritative upload marker for backward compatibility; `state.json` supersedes
nothing.

Preserved: auto-delete-on-fresh-success, never-delete-an-explicit-`--workdir`,
failed runs keep their workdir.

### 4. Poison-pill give-up path

`_recover_inflight` gains a terminal-failure branch. On `readiness:"FAILED"` it
**abandons** the record: writes an incident, clears `inflight.json`, and lets the
current run proceed. `covered.json` is deliberately *not* written — those URLs
never shipped, so they must return to the pool. This is the automated form of the
documented `rm ~/.config/daily-podcast/inflight.json` remedy.

The dead episode is not auto-deleted from Spotify. Deleting a published episode
is irreversible and needs a human; the existing prune tier already prefers
`FAILED` episodes, so it is reclaimed on the next capacity prune anyway.

### 5. Poll policy

`poll_timeout_s` (config, default **1800**). `episode_status` distinguishes
`READY` / `FAILED` / `PROCESSING` / unknown, falls back to the show listing when
the status call errors, and treats "episode missing from listing" as transient
rather than terminal (per the 07-28 incident).

### 6. Transient upload retry

A non-cap upload failure retries once after a short backoff before dying. Cap
429s keep their existing prune-then-retry-once path; the two never compound.

### 7. R2 on resume — invariant change

`_resume` previously must not call `load_config`, which is precisely why R2
silently skipped on every recovery. `_render` now loads config once, above the
resume branch, and passes it in. The old invariant is retired deliberately and
CLAUDE.md is updated.

### 8. Blocked-source registry

The WebFetch blocklist moves out of memory and into
`skills/daily-podcast/blocked_sources.json` (domain → reason → substitute), so
curation can consult it instead of relying on operator recall.

### 9. Incidents

- `incidents/<slug>.md` — one codified doc per failure mode: symptom, root
  cause, automated remedy, guarding test.
- Runtime reports land in `~/.config/daily-podcast/incidents/new/`
  (overridable via `DAILY_PODCAST_INCIDENT_DIR`), **not** a repo-relative path:
  the scheduled run executes from the version-keyed plugin cache, where a
  repo-relative write would be invisible and wiped on the next release.
- A post-run hook in `main()` writes a structured `.md` + `.json` pair on any
  non-clean exit. Best-effort: an incident-write failure never changes the exit
  code, mirroring the run-log contract.

## Testing

Every new invariant gets a failing test first, in `tests/test_reliability.py`.
Existing 222 tests must stay green; the one deliberate change is the resume /
`load_config` assertion, updated in place with a comment explaining why.

## Out of scope

- Auto-deleting rejected episodes from Spotify (irreversible; human-gated).
- Fixing the child `claude -p` 401 (failure mode 8) — an environment/credential
  problem, not a code one. Pre-flight surfaces it earlier; it stays human-gated.
- Auto-substituting alternate outlets for blocked sources. The registry makes
  the data available; choosing a replacement article stays a curation decision.
