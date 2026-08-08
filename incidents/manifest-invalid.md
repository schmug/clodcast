# Invalid manifest

**Severity:** low — fails before any cost

## Symptom

```
error: manifest not found: /path/to/manifest.json
error: manifest is not valid JSON: …
error: segment 3: text is required
```

## Root cause

The manifest is the contract between the skill prose (which writes the script)
and `render.py` (which renders it). A malformed one means the upstream curation
or summarization step produced something the renderer cannot consume — most often
a segment missing `text`, or a truncated write.

## Automated remedy

`validate_manifest` runs before the workdir is created and before pre-flight, so
an invalid manifest costs nothing. There is no auto-repair, and there should not
be: guessing at a missing segment body would put invented content into a
published episode.

The orchestrator's own protection is upstream and separate — a per-item block,
timeout, or error drops **only that item** to `dropped.jsonl` and the remaining
items still ship, so one bad summarization cannot produce a malformed manifest
for the whole run.

## Test that guards it

- Pre-existing `validate_manifest` tests in `tests/test_render.py`.
- `test_main_writes_an_incident_on_non_clean_exit` uses this path to prove the
  post-run hook fires.
