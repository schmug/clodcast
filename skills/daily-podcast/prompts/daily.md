# Daily Podcast Run — moved

> **This prompt no longer carries the procedure.** The unattended daily run lives
> in one place now: the **"Unattended daily run"** section of
> [`../SKILL.md`](../SKILL.md).

## Why this file is a stub

This prompt used to inline the whole run procedure — gather, curate, fetch, write,
manifest, render, report. Two other copies existed: the plugin skill and,
critically, whatever prompt a scheduler carried. They drifted, and the drift was
invisible: a scheduled routine ran a months-old fork that was missing the
content-policy guidance, the `${CLAUDE_PLUGIN_ROOT}` pinning, the `r2=` reporting
field, and the resume-after-upload-failure rule. Nothing surfaced the divergence
because each copy worked on its own terms.

Duplicating a prompt is the same mistake as duplicating code, and it fails the
same way. So the procedure has one home, and this file points at it.

## If you are a scheduler

Invoke the `daily-podcast` skill and follow its **"Unattended daily run"**
section. Do not copy those steps into your own prompt — that recreates the fork.
A scheduler's prompt should be a trigger, not a specification:

```markdown
You are an unattended invocation. Invoke the `daily-podcast` skill via the Skill
tool, then follow its "Unattended daily run" section exactly, end to end. Report
its single SHIPPED/FAILED line to stdout and exit.
```

## If you want a self-contained shell entry point

Use [`../orchestrate.py`](../orchestrate.py) instead — it gathers and curates
deterministically in pure Python and summarizes each item in its own isolated
`claude -p`, so a per-item block drops only that item.

**Caveat:** those child `claude -p` subprocesses authenticate from disk/env, not
from a parent session. Under a scheduler that injects a session-scoped credential,
every item 401s and the run fails fast. See
[`incidents/auth-failure.md`](../../../incidents/auth-failure.md). If your
scheduler is a Claude routine, use the skill path above instead.
