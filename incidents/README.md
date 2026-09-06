# Incidents

One file per failure mode this pipeline has actually hit in production. Each has
the same four sections:

- **Symptom** — what an operator sees, verbatim where possible
- **Root cause** — why it happens
- **Automated remedy** — what the pipeline now does on its own
- **Test that guards it** — the test that fails if the remedy regresses

Everything here traces to a real run. Nothing is hypothetical.

## The failure modes

| File | Failure mode | Autonomous? |
|---|---|---|
| [processing-failed.md](processing-failed.md) | Spotify rejects the episode server-side after a successful upload | ✅ recovers |
| [episode-cap.md](episode-cap.md) | Show hits the 60-episode cap; upload 429s | ✅ prevented |
| [poll-timeout.md](poll-timeout.md) | Readiness poll expires while Spotify is still processing | ✅ absorbed |
| [transient-upload-failure.md](transient-upload-failure.md) | `save-to-spotify upload` fails once with empty stderr | ✅ retried |
| [connection-drop.md](connection-drop.md) | Run dies mid-flight (SIGTERM, dropped connection, crash) | ✅ resumable |
| [r2-skip-on-resume.md](r2-skip-on-resume.md) | Recovered episode silently misses the web feed | ✅ prevented |
| [rejected-artifact.md](rejected-artifact.md) | A known-bad mp3 is re-uploaded and rejected again | ✅ blocked |
| [tts-degeneration.md](tts-degeneration.md) | A segment derails into looping babble; most of its script is never spoken | ⚠️ blocked pre-upload |
| [date-format-crash.md](date-format-crash.md) | A date parser raises in the art layer, after a full episode of TTS | ⚠️ classified |
| [webfetch-blocked-source.md](webfetch-blocked-source.md) | An outlet can't be fetched for an article body | ⚠️ surfaced |
| [auth-failure.md](auth-failure.md) | Child `claude -p` has no credential under a scheduler | ❌ human |

## The queue: `new/` in, `handled/` out

On any non-clean exit the pipeline writes a structured report (markdown + a JSON
sidecar) so a failure is never just a scrollback buffer. Reports land in
`~/.config/daily-podcast/incidents/new/`, **not** in this directory — the
scheduled run executes from the version-keyed plugin cache, where a repo-relative
write would be invisible and wiped by the next release. Override with
`DAILY_PODCAST_INCIDENT_DIR`; both halves follow the override together.

A report tagged `unclassified` is the interesting one: it means a failure mode
nobody has written up yet. Codify it as a new file here, add a guarding test, and
add its signature to `_INCIDENT_SIGNATURES` in `render.py`.

Then **drain it**, so `new/` keeps meaning "failures nobody has dealt with yet"
rather than "every failure since the feature shipped":

```bash
# What is actually open? Each report is re-classified against TODAY's signatures,
# so one recorded `unclassified` before its playbook existed says so.
python3 skills/daily-podcast/triage.py list

# Handled. Move it to incidents/handled/ (a move — never `rm`).
python3 skills/daily-podcast/triage.py resolve 20260903T014105Z-unclassified \
    --note "playbook landed in #205"
```

**Move, never delete.** A report is the evidence behind every file in this
directory — "everything here traces to a real run" is the claim the whole
directory makes — so the bloopers-bin rule applies: an archive that deletes its
oldest material defeats its own purpose. But a handled report is queue noise,
exactly like a finished workdir. Moving settles both. `handled/` therefore has no
retention window and no prune flag; it grows at most one small pair of files per
failed run, and `handled/index.jsonl` is an append-only row per resolution. The
reasoning lives beside `HANDLED_INCIDENT_DIRNAME` in `render.py`, next to the two
precedents that disagree.

A resolve never rewrites the report. Its recorded `kind` is what the run actually
observed; a later signature table can *explain* it, which is what `list` shows,
but it cannot retroactively change what happened.

```bash
ls ~/.config/daily-podcast/incidents/handled/
jq -r '[.resolved_at,.stem,.note] | @tsv' \
    ~/.config/daily-podcast/incidents/handled/index.jsonl
```
