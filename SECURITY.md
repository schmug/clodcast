# Security Policy

## Supported versions

clodcast is a single-skill Claude Code plugin released as tagged `vX.Y.Z` versions.
Only the **latest release** (and the `main` branch) is supported — fixes ship forward,
not as backports to older tags.

## Reporting a vulnerability

**Please report security issues privately — do not open a public issue.**

Use GitHub's private vulnerability reporting:
[**Report a vulnerability**](https://github.com/schmug/clodcast/security/advisories/new).
This opens a private advisory visible only to you and the maintainer; nothing is public
until an advisory is published.

If you can't use that, email **clodcast@cortech.online** instead.

This is a solo-maintained project, so responses are best-effort: expect an
acknowledgement within about a week. Please give a reasonable window to ship a fix
before any public disclosure.

## What's in scope

Helpful things to look at, given how the skill works:

- **Credential handling.** R2 credentials and the Pages deploy-hook URL must come from
  the environment or a `0600 ~/.config/daily-podcast/secrets.json` — never from
  `config.json` or anything committed to git. A path that leaks them into logs, the run
  log (`runs.jsonl`), a workdir, or a committed file is in scope.
- **Subprocess boundaries.** The renderer shells out to `ffmpeg`/`ffprobe` and the
  `save-to-spotify` CLI. Argument-injection or unsanitized data reaching a shell is in
  scope.
- **Generated output.** The HTML episode description is generated from item content;
  unescaped, attacker-controllable input reaching that markup is in scope.
- **Destructive operations.** `--prune-workdirs` deletes directories under the temp dir;
  any way to make it delete outside its guarded set (symlink escape, the active workdir,
  `N <= 0`) is in scope.

## Out of scope

- Vulnerabilities in third-party dependencies (`ffmpeg`, the `save-to-spotify` CLI,
  Qwen3-TTS / MLX, Python packages) — report those upstream. We'll still take a fix that
  changes how clodcast *uses* them.
- Anything requiring an already-compromised local machine or a malicious
  `~/.config/daily-podcast/` the operator wrote themselves.
