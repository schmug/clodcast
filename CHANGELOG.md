# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).
This file is generated from conventional commits by [git-cliff](https://git-cliff.org).

## [0.1.6](https://github.com/schmug/clodcast/compare/v0.1.5...v0.1.6) (2026-08-24)


### Features

* **frontier-commits:** fc_common foundation + test isolation (n1) ([#120](https://github.com/schmug/clodcast/issues/120)) ([a490fd4](https://github.com/schmug/clodcast/commit/a490fd45329d9c1673018100d86212a983b1c9a2))
* **frontier-commits:** labs.json aggregation, hash-gated R2 publish, snapshot CLI ([#136](https://github.com/schmug/clodcast/issues/136)) ([497c25b](https://github.com/schmug/clodcast/commit/497c25bfdc5cd3f0446d79ad3363d60df6941ee6))
* **frontier-commits:** org sweep, AI filter, release detection (n2) ([#122](https://github.com/schmug/clodcast/issues/122)) ([f93d314](https://github.com/schmug/clodcast/commit/f93d3148d54465fe392c17404b2af9db5c5491c8))
* **frontier-commits:** SKILL.md, prompts, drift tests, README (n6) ([#140](https://github.com/schmug/clodcast/issues/140)) ([fa4ebb8](https://github.com/schmug/clodcast/commit/fa4ebb8de4db4b21f44e83d878e2ecaccd54f1a5))
* **frontier-commits:** story detector — six types, mention-once, detect/mark CLI ([#127](https://github.com/schmug/clodcast/issues/127)) ([cdcd096](https://github.com/schmug/clodcast/commit/cdcd0966a6fa180c397e5cf85e5775c44fb93c6b))
* **frontier-commits:** week-seeded rotation with verified 6x6 Latin square ([#138](https://github.com/schmug/clodcast/issues/138)) ([5eb98f3](https://github.com/schmug/clodcast/commit/5eb98f3f1cdb4a0f4ba4f395fee3f5322d63ba5a))
* **render:** credit the source list at the end of every description ([#132](https://github.com/schmug/clodcast/issues/132)) ([fb2c68e](https://github.com/schmug/clodcast/commit/fb2c68e5bd4630d082ac609a055b895f69c12eea)), closes [#130](https://github.com/schmug/clodcast/issues/130)
* **render:** optional r2_key_prefix manifest key namespaces second-show episode/cover objects ([#142](https://github.com/schmug/clodcast/issues/142)) ([#154](https://github.com/schmug/clodcast/issues/154)) ([5dfeaf7](https://github.com/schmug/clodcast/commit/5dfeaf7a1f4479a6b0a5a153d720c0a0e5ba7553))
* **render:** optional r2_manifest_name manifest key for second-show web feeds ([#121](https://github.com/schmug/clodcast/issues/121)) ([8835a46](https://github.com/schmug/clodcast/commit/8835a46a1c83d5ac41ed33eccdda2d7699ff34c6)), closes [#118](https://github.com/schmug/clodcast/issues/118)
* **titles:** back-fill topical titles onto the 75 published episodes ([#144](https://github.com/schmug/clodcast/issues/144)) ([#151](https://github.com/schmug/clodcast/issues/151)) ([17f34bc](https://github.com/schmug/clodcast/commit/17f34bcc55133e4f0924adc1d9c0f2bc7690f4f9))
* **titles:** name the lead stories in episode titles ([#139](https://github.com/schmug/clodcast/issues/139)) ([#147](https://github.com/schmug/clodcast/issues/147)) ([bf29c3e](https://github.com/schmug/clodcast/commit/bf29c3eb817a97b1c0ccec1ba097deaeec37c3d6))


### Bug Fixes

* **config:** credit the host as Schmug, matching the public show ([#134](https://github.com/schmug/clodcast/issues/134)) ([74a5900](https://github.com/schmug/clodcast/commit/74a590016e8bf9ec4e676ab33810444832c028e0)), closes [#129](https://github.com/schmug/clodcast/issues/129)
* **frontier-commits:** collapse a release train to one story per repo ([#149](https://github.com/schmug/clodcast/issues/149)) ([1388130](https://github.com/schmug/clodcast/commit/1388130dceba7781759b2618bc08fe2348156bba)), closes [#148](https://github.com/schmug/clodcast/issues/148)

## [0.1.5](https://github.com/schmug/clodcast/compare/v0.1.4...v0.1.5) (2026-08-23)


### Features

* **script:** assign segues instead of requesting them ([#110](https://github.com/schmug/clodcast/issues/110)) ([d458373](https://github.com/schmug/clodcast/commit/d458373f380634132a48591f3954308c28023006))
* **script:** replace the fixed script template with a date-seeded rotation ([#103](https://github.com/schmug/clodcast/issues/103)) ([fcb9300](https://github.com/schmug/clodcast/commit/fcb93007fcd6a2f53eaa22f4da08cfaf58f922ca))


### Bug Fixes

* **curation:** name the three duplicate-story shapes and drop weekly roundups ([#102](https://github.com/schmug/clodcast/issues/102)) ([b7b6639](https://github.com/schmug/clodcast/commit/b7b6639faa078154469c34d9aa6ca6daf2c9019d))
* **script:** replace the shape stride with a Latin square ([#108](https://github.com/schmug/clodcast/issues/108)) ([f7ff6c3](https://github.com/schmug/clodcast/commit/f7ff6c3817d01a329f78ba051c85648c5d7ebbdf))

## [0.1.4](https://github.com/schmug/clodcast/compare/v0.1.3...v0.1.4) (2026-08-22)


### Features

* **gate:** reject TTS-degenerated segments via speech-rate outlier check ([#90](https://github.com/schmug/clodcast/issues/90)) ([cf7b50e](https://github.com/schmug/clodcast/commit/cf7b50e82ba63c3b95d57fb2e1e1ac65b0d535ba))


### Bug Fixes

* **render:** drop the obsolete sub-30s chapter cap and its silence padding ([#101](https://github.com/schmug/clodcast/issues/101)) ([0abfce0](https://github.com/schmug/clodcast/commit/0abfce08abc2e4606eabccf23591c0f54dabaef2)), closes [#99](https://github.com/schmug/clodcast/issues/99)

## [0.1.3](https://github.com/schmug/clodcast/compare/v0.1.2...v0.1.3) (2026-08-08)


### Features

* **reliability:** pre-flight gate, artifact gate, durable state, incident capture ([#84](https://github.com/schmug/clodcast/issues/84)) ([55333e7](https://github.com/schmug/clodcast/commit/55333e7baccb7853fbf9168d549e3cc47bb66318))

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
