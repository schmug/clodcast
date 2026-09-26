# Show Your Work Weekly Run — this is a stub

> **This prompt does not carry the procedure, and never will.** The unattended
> weekly run lives in one place: the **"Unattended weekly run"** section of
> [`../SKILL.md`](../SKILL.md).

## Why this file is a stub

The daily show learned this the hard way: three copies of its run procedure
drifted, and production silently ran a months-old fork. So the procedure has
exactly one home, this file points at it, and `tests/test_syw_skill_md.py` goes
red if the procedure ever grows back here.

## If you are a scheduler

Invoke the `show-your-work` skill and follow its **"Unattended weekly run"**
section. A scheduler's prompt should be a trigger, not a specification:

```markdown
You are an unattended invocation. Invoke the `show-your-work` skill via the
Skill tool, then follow its "Unattended weekly run" section exactly, end to end.
Report its single SHIPPED/SKIPPED/FAILED line to stdout and exit.
```
