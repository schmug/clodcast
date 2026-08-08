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
| [webfetch-blocked-source.md](webfetch-blocked-source.md) | An outlet can't be fetched for an article body | ⚠️ surfaced |
| [auth-failure.md](auth-failure.md) | Child `claude -p` has no credential under a scheduler | ❌ human |

## `new/` — the intake queue

On any non-clean exit the pipeline writes a structured report (markdown + a JSON
sidecar) so a failure is never just a scrollback buffer. Reports land in
`~/.config/daily-podcast/incidents/new/`, **not** in this directory — the
scheduled run executes from the version-keyed plugin cache, where a repo-relative
write would be invisible and wiped by the next release. Override with
`DAILY_PODCAST_INCIDENT_DIR`.

A report tagged `unclassified` is the interesting one: it means a failure mode
nobody has written up yet. Codify it as a new file here, add a guarding test, and
add its signature to `_INCIDENT_SIGNATURES` in `render.py`.

```bash
# Triage the queue
ls ~/.config/daily-podcast/incidents/new/
jq -r '.kind' ~/.config/daily-podcast/incidents/new/*.json | sort | uniq -c
```
