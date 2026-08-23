# Frontier Commits — design spec

**Date:** 2026-08-23
**Status:** Approved design, pre-implementation
**Decisions locked by Cory:** weekly cadence · orgs = anthropics, openai, xai-org, google (filtered) + google-deepmind · /labs/ page v1 = dashboard only · show name = **Frontier Commits** · Approach A (second skill in clodcast, reusing render.py)

## 1. What this is

A second podcast, sibling to Claude Code Field Notes, whose beat is the frontier
labs' public GitHub activity: new repos (and speculation about what they mean),
notable releases, archivals, staleness, and star-velocity trends. Ships weekly
(Monday morning) to its own Spotify show via the existing `render.py`. A
companion `/labs/` dashboard on cortech.online visualizes the same underlying
data.

The genre is *informed speculation*: "OpenAI forked git and it's actively
pushed — probably a staging fork for upstream patches, but if it's more, here's
what that would mean." Speculation is a feature, not a bug — but it is always
labeled as speculation and anchored to observable facts.

## 2. Grounding data (recon, 2026-08-23, via authenticated `gh api`)

| Org | Public repos | New non-fork repos / 90d | Pushed last 30d | Notes |
|---|---|---|---|---|
| anthropics | 102 | 11 | 44 | ~25–33% forks/mirrors; needs light filter |
| openai | 268 | 12 | 40 | 136 archived legacy; `gym` recently archived |
| xai-org | 9 | 2 | 6 | Tiny, every repo a headline; ~1 event/month |
| google | 2,893 | 18 | 432 | AI is minority share; hard filter mandatory |
| google-deepmind | 400 | — (not yet surveyed in depth) | — | Dense AI signal; watched unfiltered |

≈3–4 new repos/week across orgs, plus releases/archivals/velocity → weekly
cadence. API cost of a full daily sweep: **< 150 requests/day** against a
5,000/hr authenticated limit.

Feasibility facts that shaped the design (verified with real calls):

- `GET /orgs/{org}/repos` includes `topics`, `created_at`, `pushed_at`,
  `archived`, `stargazers_count`, `fork` with no extra calls.
- **Star history is gone upstream** — per-repo stargazer enumeration 404s.
  Velocity requires our own daily snapshots. This makes the snapshot store the
  foundation of both the podcast and the dashboard.
- `orgs/{org}/events` is capped at 300 events (~3 hours of history for google)
  — unusable for detection. New-repo detection uses the search API
  (`org:X created:>DATE`, one call per org; **counts forks — filter them**).
- Org-wide release detection: call `/repos/{o}/{r}/releases?per_page=5` only
  for repos whose `pushed_at` moved in the window.
- `render.py` already honors a manifest-level `show_id` override (render.py
  `main()`), so the whole TTS/upload/timeline/reliability stack is reusable.

## 3. Architecture

```
 launchd (daily) ──► snapshot.py ──► ~/.config/frontier-commits/snapshots/YYYY-MM-DD.json
                          │
                          ├──► labs.json → R2 (clodcast bucket) ──► POST Pages deploy hook
                          │                                                │
 Claude routine           ▼                                                ▼
 (weekly, Mon) ──► SKILL.md "Unattended weekly run"              cortech.online /labs/
                          │                                      (build-time fetch, static)
                          ├── stories.py → story candidates
                          ├── per-story research (isolated subagent context)
                          ├── manifest (show_id = Frontier Commits, voice = house)
                          ▼
                     render.py (unchanged except r2_manifest_name)
                          │
                          ├──► Spotify (second show)
                          └──► R2: mp3/cover + manifest-frontier-commits.json
```

Two schedules, deliberately different mechanisms:

- **Daily snapshot: launchd, pure Python.** Needs only a gh token — no Claude
  credential — so it is immune to the `claude -p` 401 class of scheduler
  failures documented in `incidents/auth-failure.md`.
- **Weekly episode: Claude routine** following the new skill's *"Unattended
  weekly run"* section, same mechanism as the existing daily show. The
  procedure has exactly one home (SKILL.md), same rule as daily-podcast.

## 4. Components

### 4.1 Config — `~/.config/frontier-commits/config.json`

New config dir, sibling to `~/.config/daily-podcast/`. Keys:

```json
{
  "show_id": "spotify:show:<created at setup>",
  "show_name": "Frontier Commits",
  "host_name": "Cory",
  "orgs": [
    {"name": "anthropics", "filter": "none"},
    {"name": "openai", "filter": "none"},
    {"name": "xai-org", "filter": "none"},
    {"name": "google", "filter": "ai"},
    {"name": "google-deepmind", "filter": "none"}
  ],
  "ai_topics": ["ai", "machine-learning", "llm", "agentic-ai", "mcp", "..."],
  "allowlist": {"google": ["langextract", "adk-python", "..."]},
  "denylist": {},
  "min_stories_per_episode": 2,
  "target_stories_per_episode": 6
}
```

Adding zai/baidu later is appending to `orgs`. The `"ai"` filter admits a repo
if topics intersect `ai_topics`, OR description matches an AI regex, OR the
repo is allowlisted; denylist always wins. anthropics/openai keep `"none"` at
the org level — their fork/mirror noise is handled by story-type rules
(§4.3), not org filtering, so a surprising non-AI cluster (e.g. anthropics'
EDA repos) still surfaces as a story.

### 4.2 `snapshot.py` — daily collector (pure Python, no LLM)

- Sweeps each configured org: full paginated repo listing + targeted
  `/releases` calls for repos whose `pushed_at` falls inside the last 48h.
- Writes `snapshots/YYYY-MM-DD.json` atomically. Per-repo record: `stars`,
  `forks`, `open_issues`, `created_at`, `pushed_at`, `archived`, `fork`,
  `topics`, `description`, `language`. Per-org: `fetched_at`, `releases`
  (`repo`, `tag`, `published_at`).
- Aggregates `labs.json` (schema below) and publishes it to the existing R2
  bucket, then POSTs the Pages deploy hook **only when content materially
  changed** (hash comparison), so the site rebuilds at most daily.
- **Degradation:** a per-org fetch failure keeps that org's data out of the
  day's snapshot but never kills the sweep; the story detector tolerates
  gaps by diffing against the most recent available snapshot within lookback.
  A malformed prior snapshot is treated as absent. labs.json publish failure
  is logged and non-fatal (web page is optional — same posture as the R2
  three-state rule in render.py).
- Snapshots are retained 400 days (enough for year-over-year trend plots),
  pruned on write, same no-data-loss posture as `covered.json` pruning.

### 4.3 `stories.py` — deterministic story detector

Pure function of (snapshots, reported.json, config). No network, no LLM.
Emits scored story candidates:

| Type | Trigger | Notes |
|---|---|---|
| `NEW_REPO` | non-fork repo appears (created_at in window) | the headline genre |
| `NOTABLE_FORK` | fork appears AND (upstream ≥ 10k stars OR actively pushed) | catches `openai/git` |
| `RELEASE` | release published in window on a repo with stars ≥ bar or allowlisted | |
| `ARCHIVED` | `archived` flips false → true | the `gym` genre |
| `GOING_STALE` | stars ≥ 1,000 AND days-since-push crosses a stage boundary (180, 365) | stage encoded in story key |
| `STAR_SURGE` | Δstars/7d ≥ max(500, 20% of base) | thresholds are config, tune with real data |

- **Mention-once is mechanical:** `reported.json` maps
  `"{type}:{org}/{repo}:{stage}"` → `{date, episode_uri}`. A reported story
  key never re-emits; a stale repo resurfaces only when its *stage* changes
  (stale-180 → stale-365 → archived). Malformed `reported.json` degrades to
  `{}` (worst case: a repeated mention one week — mirrors `covered.json`
  posture).
- **Scoring:** type priority (NEW_REPO/ARCHIVED high, STAR_SURGE low) +
  log-stars + recency, with a per-org cap so one lab can't fill the episode
  (variety posture, like `feed_usage.json`). Target `target_stories_per_episode`,
  floor `min_stories_per_episode`.

### 4.4 `skills/frontier-commits/SKILL.md` — the weekly episode

- **Unattended weekly run** section is the single home of the procedure
  (scheduler prompt is a trigger, never a copy — same rule and same
  drift-test pattern as daily-podcast).
- Flow: run `stories.py` → for each selected story, research in an isolated
  per-story subagent context (README, repo page, recent commits — one story's
  material per context, preserving the one-body-per-request philosophy) →
  write segments → assemble manifest → invoke `render.py` (background, per
  the 10-min Bash cap memory).
- **Speculation rules (hard):** every speculative claim is (a) framed as
  speculation in the prose itself, (b) anchored to at least one observable
  (creation date, fork parent, commit cadence, description, topics), and
  (c) never presented as confirmed fact or attributed to individuals. Never
  manufacture a connection between unrelated stories (inherited rule).
- **Script template:** its own **ISO-week-seeded** rotation — fewer, longer
  segments than the daily show (6 stories ≈ 8–12 min), a cold open, a
  recurring "trend watch" close that reads the week's velocity data. It does
  NOT import daily-podcast's `SHAPE_ORDERS`; a weekly speculation show has
  different shapes. Same principle though: variety is assigned by the seed,
  not requested from the model. Drift tests tie SKILL.md's documented shapes
  to the code's tables from day one.
- If fewer than `min_stories_per_episode` stories: the run reports
  `SKIPPED thin-week (<n> stories)` and ships nothing — no filler episodes.
  Final-line stdout contract mirrors the daily show (`SHIPPED <uri> ...` /
  `SKIPPED <reason>` / `FAILED <reason>`).
- Voice: house voice (default `voice: "house"`). No new voice modes.
- `reported.json` is updated by the skill procedure **only after render.py
  exits successfully** (post-READY). `covered.json` (shared) independently
  dedups exact repo URLs via render.py's existing post-READY write; the URL
  spaces (GitHub repos vs news articles) don't collide.

### 4.5 `render.py` — one bounded change

New optional manifest key `r2_manifest_name` (default `"manifest.json"`,
validated `^[A-Za-z0-9._-]+\.json$`). Frontier Commits manifests set
`"r2_manifest_name": "manifest-frontier-commits.json"` so its episodes never
leak into the Field Notes `/podcast/` page. Everything else — preflight,
artifact gate, capacity prune (scoped to the manifest's `show_id`), run log,
incidents, resume — is untouched. Default behavior is byte-identical for the
daily show.

### 4.6 cortech.online `/labs/` page (separate PR in schmug/cortech.online)

- Work from a **freshened clone** — the local checkout at
  `/Users/cory/portfolio` is stale (2026-05-16); the podcast section exists
  only on remote main.
- Follows the site's existing pattern exactly: `src/lib/labs.ts` build-time
  fetch of `LABS_MANIFEST_URL` (R2 `labs.json`), zod-validated,
  warn-and-empty on failure; static Astro page over `Base.astro`; inline SVG
  for charts (no charting dep in v1). CSP stays untouched — data is
  prerendered, never client-fetched.
- **v1 dashboard:** per-lab new-repo timeline, star-velocity movers,
  staleness/archival watch, latest Frontier Commits episodes (from
  `manifest-frontier-commits.json`, same `episodeSchema`).
- `labs.json` is schema-versioned (`schema_version: 1`); the site tolerates
  unknown extra fields so pipeline and site can deploy independently.

`labs.json` shape:

```json
{
  "schema_version": 1,
  "generated_at": "2026-08-23T12:00:00Z",
  "labs": [{
    "org": "anthropics", "display": "Anthropic",
    "totals": {"repos": 102, "active_30d": 44, "stars": 512345},
    "new_repos": [{"name": "...", "created_at": "...", "stars": 0, "description": "..."}],
    "movers": [{"name": "...", "stars": 0, "delta_7d": 0}],
    "stale_watch": [{"name": "...", "stars": 0, "days_since_push": 0}],
    "archived_recent": ["..."]
  }]
}
```

## 5. State inventory (all under `~/.config/frontier-commits/`)

| File | Writer | Purpose | Corruption posture |
|---|---|---|---|
| `config.json` | human | orgs, filters, show, thresholds | missing keys → defaults; missing show_id → die at preflight |
| `snapshots/*.json` | snapshot.py | daily org state; the star-history substitute | malformed day treated as absent |
| `reported.json` | weekly skill (post-READY) | story-level mention-once | malformed → `{}` (benign repeat) |
| `runs.jsonl` (shared, daily-podcast dir) | render.py | run log — unchanged, records carry the show's manifest | existing posture |
| `covered.json` (shared) | render.py | URL dedup — unchanged | existing posture |

Tests must redirect ALL new paths per-test and assert none point at the real
config dir — extend `tests/conftest.py`'s existing guard to the
frontier-commits dir.

## 6. Testing

- **`tests/test_stories.py`** — the core suite. Synthetic snapshot pairs for
  every story type; mention-once (a reported key never re-emits); stage
  transitions (stale-180 → stale-365 → archived each re-admit exactly once);
  per-org cap; fork handling (`openai/git` fixture: excluded from NEW_REPO,
  admitted as NOTABLE_FORK); malformed reported.json; gap tolerance (missing
  snapshot days).
- **`tests/test_snapshot.py`** — recorded `gh api` fixtures; google AI filter
  (topics hit, description hit, allowlist hit, denylist veto); atomic write;
  retention pruning; per-org failure isolation; labs.json aggregation +
  changed-content deploy-hook gating.
- **`tests/test_render.py` additions** — `r2_manifest_name` default,
  override, validation rejection.
- **Drift tests from day one** — SKILL.md documents every story type and
  script shape the code defines (mirror `test_skill_md_documents_every_shape_and_mode`);
  the scheduler prompt stub stays a stub.
- Site repo: vitest for `labs.ts` (schema, empty-on-failure), e2e smoke for
  `/labs/` — in that repo's PR, per its conventions.

## 7. Phasing

| Phase | Deliverable | Acceptance |
|---|---|---|
| **P1** | `snapshot.py` + `stories.py` + tests + launchd job | snapshots accumulate daily unattended; stories.py emits sane candidates on real data; suite green. Ship P1 first — snapshot history is the long pole for every trend feature. Verify gh token availability under launchd as the first task. |
| **P2** | `skills/frontier-commits/SKILL.md` + script template + drift tests | full `--dry-run` episode (mp3 + timeline + cover) from real story candidates, locally reviewed |
| **P3** | show creation, `r2_manifest_name` change, first real ship, weekly routine | `SHIPPED` line; episode READY on Spotify; manifest-frontier-commits.json on R2; routine scheduled Mondays |
| **P4** | `/labs/` dashboard PR in schmug/cortech.online | page live with real snapshot data; Field Notes `/podcast/` page unchanged |

## 8. Out of scope (v1)

- Magic quadrant and semantic/vector cloud visualizations (follow-on, once
  weeks of snapshot history exist — noted as the explicit next step for the
  /labs/ page).
- zai, baidu, or other orgs (config-ready; not enabled).
- A dedicated RSS feed / `/podcast/`-style page for Frontier Commits on the
  site (episodes are listed on `/labs/` in v1; full feed is a follow-up in
  the site repo).
- Any behavior change to the daily show beyond the inert `r2_manifest_name`
  default.
- Embedding/LLM-based curation — curation stays deterministic metadata-only,
  same as the daily show's core invariant.
