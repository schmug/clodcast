<!--
Title: use a Conventional Commit prefix —
feat: / fix: / refactor: / test: / chore: / docs: / security:
-->

## Summary

<!-- What changed and why, in a few sentences. -->

## Related issue

<!-- Closes #<n>  (or "Refs #<n>" if it doesn't fully close it). -->

Closes #

## Acceptance criteria

<!-- Copy the issue's acceptance criteria and check off what this PR delivers. -->

- [ ]

## Testing

<!--
CI can't run TTS, ffmpeg, or the save-to-spotify upload, so behavior changes
are verified locally. Per CLAUDE.md, always dry-run before a real run.
-->

- [ ] `ruff check .` is green
- [ ] `pytest` is green (paste counts, e.g. "23 passed")
- [ ] Dry-run exercised (if the renderer/manifest path changed):
      `python3 skills/daily-podcast/render.py --manifest /tmp/manifest.json --dry-run`
- [ ] Manual notes (anything CI can't cover — produced mp3/cover/timeline, Spotify state):

## Risk / blast radius

<!--
What could this break? Call out any renderer invariants touched (see CLAUDE.md:
1:1 segment↔source mapping, max-3-short-chapters, covered.json write ordering,
last-chapter math, mono 44.1k). Note "none — pure new files / docs only" if so.
-->

## Docs kept in sync

<!-- Check if the manifest schema or script template changed. -->

- [ ] N/A — no schema/template change
- [ ] Updated `skills/daily-podcast/SKILL.md` and `skills/daily-podcast/prompts/daily.md` together
