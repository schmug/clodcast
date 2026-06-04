# Contributing to clodcast

Thanks for being here — first-time contributors are welcome. clodcast is a small,
single-skill Claude Code plugin that turns saved articles into a produced Spotify
episode (see the [README](README.md) for what it does and how to run it). This guide
covers how to set up, what to read before changing behavior, and how to get a PR merged.

By participating you agree to our [Code of Conduct](CODE_OF_CONDUCT.md).

## Ways to contribute

- **Report a bug or request a feature** — open an issue. The
  [bug report](.github/ISSUE_TEMPLATE/bug-report.md) and
  [feature request](.github/ISSUE_TEMPLATE/feature-request.md) templates are shaped as
  Claude Code prompts, so a filled-in issue can be picked up and implemented directly.
- **Send a pull request** — fixes, docs, and small features all welcome. For anything
  larger than a bug fix, open an issue first so we can agree on the approach before you
  write code.

## Dev setup

The heavy runtime deps (`mlx-audio`) are Apple-Silicon-only, so install only what you
need:

```bash
# Tooling only (lint + tests) — works on any platform, no MLX:
pip install ruff==0.14.10 "pytest>=8.0" boto3

# Or, for a full editable env on an Apple Silicon Mac:
pip install -e ".[dev]"
```

The test suite is invariant-only by design — the heavy MLX/ffmpeg imports are
function-local, so `pytest` runs on Linux/CI without TTS, ffmpeg, or the
`save-to-spotify` CLI installed.

## The golden rule: always `--dry-run` first

A real run **uploads to Spotify and mutates your `~/.config/daily-podcast/covered.json`
dedup log.** When developing, always exercise changes with `--dry-run`, which produces
the mp3, cover, and `timeline.json` locally and prints their paths while skipping the
upload and timeline calls:

```bash
python3 skills/daily-podcast/render.py --manifest /tmp/manifest.json --dry-run
```

## Read before you change behavior

Three documents are load-bearing — read the relevant ones before changing how an
episode is produced:

1. [`skills/daily-podcast/SKILL.md`](skills/daily-podcast/SKILL.md) — script template,
   voice rules, manifest schema.
2. [`skills/daily-podcast/render.py`](skills/daily-podcast/render.py) — the
   manifest → episode driver (single file, intentionally not split into modules).
3. [`skills/daily-podcast/prompts/daily.md`](skills/daily-podcast/prompts/daily.md) —
   the headless `claude -p` prompt for unattended runs.

Two rules that trip people up:

- **Keep SKILL.md and `prompts/daily.md` in sync.** The headless prompt repeats the
  script template inline because it runs without re-reading the skill, but SKILL.md is
  the source of truth — any divergence is a bug.
- **Don't relax the renderer invariants.** They're subtle (1:1 segment↔source mapping,
  max-3-short-chapters, `covered.json` write ordering, last-chapter math, mono 44.1k,
  the run-log schema) and a violation gets the episode rejected by Spotify or sounds
  wrong. They're documented in [CLAUDE.md](CLAUDE.md) under *"Invariants the renderer
  enforces"*. If you think one needs to change, say so in the issue/PR — don't quietly
  loosen a cap to "make it work."

## Before you push

```bash
ruff check .
ruff format --check .   # enforced — CI fails on any format diff (run `ruff format .` to fix)
pytest                  # paste the counts in your PR, e.g. "23 passed"
```

If you touched the renderer or manifest path, also exercise a `--dry-run` and note what
you produced. Optionally install the pre-commit hooks so this runs automatically:

```bash
pip install pre-commit && pre-commit install
```

## Commits and pull requests

- **Use [Conventional Commit](https://www.conventionalcommits.org/) prefixes** on commits
  and PR titles: `feat:` / `fix:` / `refactor:` / `test:` / `chore:` / `docs:` /
  `security:`. The CHANGELOG and the next version number are generated from these by
  [release-please](https://github.com/googleapis/release-please), so the prefix is load-bearing.
- **Branch and open a PR — `main` is protected**, so it doesn't accept direct pushes.
- Fill in the [PR template](.github/pull_request_template.md): summary, linked issue,
  acceptance criteria, testing, and any renderer invariants touched.
- **CI must be green** (`ruff check`, `ruff format --check`, and `pytest` across Python
  3.10–3.12). Heads-up for first-time contributors: GitHub doesn't run your PR's CI until
  a maintainer approves the workflow run, so the checks can look stuck until then — that's
  expected, not a failure.
- The maintainer ([@schmug](https://github.com/schmug), via
  [CODEOWNERS](.github/CODEOWNERS)) is auto-requested as reviewer, and review threads must
  be resolved before merge.

## Questions

Read the [README](README.md) first for install, config, and usage. Still stuck? Ask in
[Discussions](https://github.com/schmug/clodcast/discussions) (the **Q&A** category) —
open an issue only for a confirmed bug or a concrete feature request.
