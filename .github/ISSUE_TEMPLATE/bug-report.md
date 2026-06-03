---
name: Bug report
about: Something the renderer or skill does wrong. Structured as a Claude Code prompt so it can be picked up directly.
title: ""
labels: bug
assignees: ""
---

## Task

<!-- One or two sentences: what should happen instead. Lead with the fix, not the symptom. -->

## Context

<!-- Why this matters / what surfaced it. Link the episode, manifest, or run that hit it. -->

## Reproduction

<!--
Smallest repro. Prefer a dry-run:
    python3 skills/daily-podcast/render.py --manifest /tmp/manifest.json --dry-run

- Command(s) run:
- Expected:
- Actual (paste the error / the wrong output):
- Host: macOS version, Apple Silicon? `save-to-spotify` / `ffmpeg` versions if relevant.
-->

## Pointers

<!-- File:line references to the code involved, e.g. skills/daily-podcast/render.py:187 -->

## Constraints

<!-- Invariants the fix must not break — see CLAUDE.md ("Invariants the renderer enforces"). -->

## Acceptance criteria

<!-- Checklist a reviewer can verify. -->

- [ ]

## Out of scope

<!-- What this issue deliberately does NOT cover. -->
