# Surface Tension Weekly Run — this is a stub

> **This prompt does not carry the procedure, and never will.** The unattended
> weekly run lives in one place: the **"Unattended weekly run"** section of
> [`../SKILL.md`](../SKILL.md).

## Why this file is a stub

The daily show learned this the hard way: three copies of its run procedure
drifted, and production silently ran a months-old fork missing content-policy
guidance and reporting fields. Duplicating a prompt is the same mistake as
duplicating code, and it fails the same way. So the weekly procedure has exactly
one home, this file points at it, and a drift test
(`tests/test_st_skill_md.py`) goes red if the procedure ever grows back here.

## If you are a scheduler

Invoke the `surface-tension` skill and follow its **"Unattended weekly run"**
section. Do not copy those steps into your own prompt — that recreates the fork.
A scheduler's prompt should be a trigger, not a specification:

```markdown
You are an unattended invocation. Invoke the `surface-tension` skill via the
Skill tool, then follow its "Unattended weekly run" section exactly, end to end.
Report its single SHIPPED/SKIPPED/FAILED line to stdout and exit.
```
