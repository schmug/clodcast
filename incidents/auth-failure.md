# Child `claude -p` cannot authenticate under a scheduler

**First seen:** 2026-06-05 · **Severity:** total run failure · **Status: still human-gated**

## Symptom

Every ranked item drops, and the run ends with:

```
FAILED no viable items; at least one item reported a 401 authentication error -
under a scheduler, child `claude -p` likely has no credentials.
See SKILL.md "Unattended runs need durable credentials".
```

`dropped.jsonl` tags every item `reason: "auth"`.

## Root cause

`orchestrate.py` summarizes each item in its own `claude -p` **subprocess**. Those
children authenticate on their own — they read whatever credential is on disk or
in their environment, **not** the parent's in-memory session login. Under a
scheduler that injects a session-scoped credential the parent holds only in
memory, there is no `~/.claude/.credentials.json` and no `ANTHROPIC_API_KEY`, so
every child 401s.

In an interactive session this is invisible, because a persistent OAuth credential
already exists on disk for the children to use.

## Current handling — detection only

`classify_output` maps a cold-credential 401 to a distinct `AUTH` outcome
(`AUTH_RE`, anchored to auth strings only, so transient rate-limit / overload
errors stay `ERROR`). When a run ends with **zero survivors and any `auth` drop**,
`main()` fails fast with the actionable line above instead of degrading to the
generic `no viable items`.

Detection is post-fan-out: a 401 returns fast, so there is no happy-path cost and
no preflight probe.

## Why this is NOT automated

This is an environment and credential problem, not a code problem. The pipeline
cannot mint itself a credential, and any code that tried would be doing something
worse than failing. The fix is one of:

- a **persistent on-disk credential** readable by the user the job runs as, or
- **`ANTHROPIC_API_KEY` in the scheduler's own environment** (the launchd plist /
  cron environment — a scheduled job does not inherit your interactive shell env,
  the same constraint that pushes R2 secrets into `secrets.json`), or
- driving the run with **in-session subagents**, which share the parent's working
  auth and still give each item an isolated one-article context.

**Verify it in the actual scheduler, not a terminal** — auth in the scheduled
harness is exactly the non-obvious part:

```bash
claude -p 'reply with the single word OK' || echo "child claude -p cannot authenticate here"
```

## Test that guards it

Pre-existing, in `tests/test_orchestrate.py`: `AUTH_RE` classification tests and
the zero-survivors fail-fast diagnostic. This file exists to record that the
failure is **known and deliberately human-gated**, so a future audit does not
mistake it for an unexamined gap.
