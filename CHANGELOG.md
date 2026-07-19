# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).
This file is generated from conventional commits by [git-cliff](https://git-cliff.org).

## [0.1.2](https://github.com/schmug/clodcast/compare/v0.1.1...v0.1.2) (2026-07-19)


### Features

* **render:** auto-prune oldest episodes on cap 429 upload failure ([#78](https://github.com/schmug/clodcast/issues/78)) ([#79](https://github.com/schmug/clodcast/issues/79)) ([4a42afe](https://github.com/schmug/clodcast/commit/4a42afe0e730c424540fede7d8563806fec8a2ba))

## [0.1.1](https://github.com/schmug/clodcast/compare/v0.1.0...v0.1.1) (2026-06-05)


### Features

* per-item orchestrator to contain the cyber-content classifier block ([#63](https://github.com/schmug/clodcast/issues/63)) ([95339c0](https://github.com/schmug/clodcast/commit/95339c07eb43729065c0f86f976abcbc91d4d2f1))


### Bug Fixes

* fail fast when scheduled claude -p cannot authenticate (401) ([#70](https://github.com/schmug/clodcast/issues/70)) ([7055dd5](https://github.com/schmug/clodcast/commit/7055dd5fe3d967e54bbcacee6a6e1a976cb1adee))
* parse nested-loudnorm result so a successful real ship isn't reported FAILED ([#69](https://github.com/schmug/clodcast/issues/69)) ([0b8772a](https://github.com/schmug/clodcast/commit/0b8772a92dc18895fc9cdbbda6ceae77e920b670))

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
