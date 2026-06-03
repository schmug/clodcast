# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).
This file is generated from conventional commits by [git-cliff](https://git-cliff.org).

## [0.1.0] - 2026-06-03

### Features

- Initial commit — daily-podcast skill
- Resolve house voice from ~/.config/daily-podcast/voices/ (#23)
- Add marketplace manifest so clodcast is installable as a plugin (#31)
- Source cover date from manifest and make post-upload idempotent (#38)
- Add manifest schema validation + pre-TTS text normalization (#39)
- Publish episode mp3 + manifest to Cloudflare R2 after Spotify (#33) (#41)
- Resolve PAGES_DEPLOY_HOOK_URL from secrets.json/config, not env-only (#44)

### Bug Fixes

- Remove verbal show-notes closer from script template (#29)
- Pin headless render.py to ${CLAUDE_PLUGIN_ROOT} and drop Skill-tool step (#32)
- Renderer quick-win hardening (#6, #8, #10, #12) (#34)

### Documentation

- Add durable-voices guide
- Add CLAUDE.md with architecture + invariants (#22)
- Add MIT LICENSE file (#24)
- Capture save-to-spotify 0.1.1 diagnostic quirks (#27)

### Testing

- Add pytest suite for render.py invariants (#30)

### Styling

- Apply ruff format

### Miscellaneous

- Project tooling foundation — deps, ruff, CI, pre-commit (#1, #2, #14, #15) (#35)
- Enforce ruff format as a blocking gate
