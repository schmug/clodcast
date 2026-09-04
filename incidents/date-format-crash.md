# A date parser crashes the run after TTS is spent

**First seen:** 2026-09-03 · **Severity:** high — a full episode of TTS is burned, and nothing ships

## Symptom

Pre-flight passes every check, the whole episode renders, and then the run dies in
the art layer with a bare parser error and no stack:

```
error: ValueError: time data 'August 31, 2026' does not match format '%Y-%m-%d'
```

On 2026-09-03 this killed a Frontier Commits run (render.py `9c5dad2`, manifest
title `Pre-flight probe, codex, adk-python - Week of August 31, 2026`). Its
incident sidecar shows the shape exactly: `preflight.ok: true` with 6/6 checks
green, `loudnorm` already recorded — so the mp3 existed — and
`episode_uri: null`. The cost is the whole render; the recovery is a re-run.

The run log line is `status: failed` with the same one-line `error_message`. There
is no traceback anywhere: the post-run hook records `f"{type(e).__name__}: {e}"`,
which is why the parser's own rejection string is all the classifier — or the
operator — ever gets.

## Root cause

Two date forms circulate through `render.py`, and a helper parsed only one of
them.

The manifest carries an ISO `date` (`"2026-08-31"`). Every caller of `build_cover`
hands it the output of `resolve_cover_date`, which returns the **display** form
(`"August 31, 2026"`). `_cover_commit_rail` then passed that straight to
`week_label` / `cover_headline_weekly`, both of which `strptime`d `"%Y-%m-%d"`.
The ISO-only parse could therefore never succeed in production: #192 shipped the
commit-rail cover with a date parse that no real call site could satisfy, and
every weekly cover died.

Two properties make this family expensive rather than merely wrong:

- **It is downstream of TTS.** The cover is generated after the audio, so the
  crash lands after the single most expensive step in the pipeline. A malformed
  *manifest* date is caught before anything is spent — `resolve_cover_date` and
  `validate_manifest` both `die()` with their own diagnostic — which is why this
  is a different failure mode from [manifest-invalid.md](manifest-invalid.md) and
  not a variant of it.
- **Pre-flight cannot see it.** The gate checks tools, credentials and capacity —
  things that are true or false before the run starts. A date form mismatch is a
  property of code paths that only execute later, so no cheap pre-check exists.
  [preflight-failed.md](preflight-failed.md) is the list of what the gate *does*
  cover; this is deliberately not on it.

The generalisable rule is that **the cover is art, not an invariant**. A date
helper on the art path that raises can lose a rendered episode over a subtitle
string; one that degrades prints a slightly wrong label and ships.

## Automated remedy

`WEEK_LABEL_FORMATS` makes the accepted forms data — `("%Y-%m-%d", "%B %d, %Y")` —
and `week_date_display` tries each in turn, **returning an unrecognised string
unchanged** rather than raising. `week_label` and `cover_headline_weekly` are both
built on it, so the string the cover prints is the string the headline strip looks
for; two independent implementations of "the weekly form" would be the same bug in
a new costume. Parsing accepts a zero-padded day and normalises it out (`%-d` on
the way out), so the two forms can never disagree about which one they are.

Shipped in #195 (`02eddf5`, release 0.1.10). There is no auto-repair beyond that
and there should not be: `resolve_cover_date` has already `die()`d on a malformed
manifest date, so anything reaching the art layer is a real date, and the right
behaviour for an unrecognised form is a slightly-wrong label, never a lost render.

What the pipeline does on its own when this recurs elsewhere is **classify it**:
`_INCIDENT_SIGNATURES` maps `strptime`'s two rejection strings
(`does not match format`, `unconverted data remains`) to this file, so the report
names a playbook instead of asking the reader to write one. The signature is the
parser's message rather than the exception type on purpose — `ValueError` alone
would drag every unrelated conversion failure in here.

**Recovery for an operator holding this error:** the workdir survives a failed
run and the per-segment TTS cache is in it, so re-running with the same explicit
`--workdir` re-renders nothing and costs seconds. Nothing shipped, so
`covered.json` is untouched and the URLs return to the pool.

## Test that guards it

The crash itself (#195, `tests/test_cover.py`):

- `test_commit_rail_renders_the_date_build_cover_is_actually_handed` — exercises
  the real `resolve_cover_date` → `build_cover` → `_cover_commit_rail`
  composition, not the pieces. Every earlier test handed the rail an ISO date
  directly, which is what let the production-only form slip through.
- `test_week_label_accepts_the_display_date_resolve_cover_date_returns`
- `test_weekly_headline_strips_its_tail_given_the_display_date`

The classification (`tests/test_reliability.py`):

- `test_date_format_crash_classifies_from_the_message_the_run_hook_builds` — the
  2026-09-03 message verbatim from its sidecar, plus `strptime`'s other rejection.
- `test_date_format_signature_does_not_swallow_unrelated_value_errors` — an int,
  a JSON and a float `ValueError` all stay `unclassified`.
- `test_every_incident_signature_has_a_playbook_file` — fails if this file is
  deleted while the signature remains.
