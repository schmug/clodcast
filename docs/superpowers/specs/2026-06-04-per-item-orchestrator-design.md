# Per-item orchestrator — design spec

**Date:** 2026-06-04
**Status:** Approved design, pre-implementation
**Branch:** `claude/determined-antonelli-c8a50e`

## Problem

The unattended daily run (`claude -p "$(cat skills/daily-podcast/prompts/daily.md)"`) is
blocked by an **API-level Usage Policy classifier** on "violative cyber content." The block
is not the model declining — it fires on the content flowing through the request, upstream of
the model's instructions, so skill-prose changes cannot resolve it. Observed verbatim:

> This request triggered restrictions on violative cyber content and was blocked under
> Anthropic's Usage Policy. To request an adjustment pursuant to our Cyber Verification
> Program based on how you use Claude, fill out [form].

The operator is already approved under the Cyber Verification Program and has re-filed; the
block persists. Evidence (no `item_*.md` files produced; ~18 tool uses; died before per-item
fetch) indicates the block fires during **gather/curate or a full-article fetch** — i.e. when
the single agent context pools many security-feed candidates (and/or a heavy article body)
into one request. The feed set is news-legitimate but security-heavy (CISA, darkreading,
Help Net Security, The DFIR Report, oss-sec, 2600, DataBreaches, etc.).

### Feasibility evidence (spike, 2026-06-04)

A schema-free, sequential workflow summarized 5 mixed candidates, each in its own isolated
subagent (separate request): 2 benign (Simon Willison, Ars Technica), 1 security-news
(Help Net Security — the reported trigger), 2 spicy (The DFIR Report ransomware intrusion,
oss-sec Linux-kernel use-after-free). **All 5 returned clean ~1000–1260-char segments at a
reporting altitude; none blocked or refused.** This proves the *mechanism*: when one article
is summarized in isolation, even offensive-security sources clear the classifier. It does
**not** prove curation — the spike took pre-selected items and never selected 10-from-~100.
The design must therefore avoid pooling candidate content at curation time, not just at
summarization time.

## Goals

1. Ship the daily episode despite the per-request cyber-content classifier, by ensuring **no
   LLM context ever holds more than one article's content**.
2. Contain a classifier block (or any per-item failure) to the single offending item: drop it,
   keep the rest, ship a (possibly shorter) episode.
3. Preserve every `render.py` invariant and the produced-episode contract unchanged.
4. Log every dropped item with feed/url/reason — operational observability and concrete
   evidence for the Cyber Verification appeal.

## Non-goals / hard boundary (no evasion)

- **Isolation is at one-article-per-request granularity only.** We never sub-divide a single
  article to slip it under the classifier and reassemble it downstream. A blocked item is
  **excluded** from the episode, never reconstructed. Each item is judged whole, on its own
  merits; we only stop one rejection from poisoning the batch. This is fault isolation, not
  circumvention.
- Not changing `render.py`'s responsibilities (TTS/concat/cover/upload/timeline/poll/dedup/R2).
- Not adding LLM-based curation (deferred; see Approach A in the brainstorming record).
- Not trimming the OPML feed set (the operator chose to handle this at curation, not by
  removing feeds).

## Core invariant

> No LLM request ever contains more than one source article's body. Gathering and curation are
> deterministic Python over **metadata only** (title, feed, date, RSS summary). Each article's
> full text is read and summarized inside its own isolated `claude -p` subprocess, whose
> failure (including a classifier block) is caught and contained.

## Architecture & data flow

New unattended entry point: `skills/daily-podcast/orchestrate.py` (single file, sibling to
`render.py`, invoked by path — never imported). The cron calls it instead of
`claude -p daily.md`.

```
orchestrate.py
  1. load        config.json / covered.json / inflight.json            [Python]
  2. gather      OPML -> feedparser -> entries in lookback window       [Python, no LLM]
                 capture {title, link, published, summary, feed_name}
                 drop links already in covered.json                     -> ~100 candidates
  3. rank        deterministic, METADATA ONLY                           [Python, no LLM]
                 score = w1*source_tier + w2*recency + w3*feed_variety
                         + w4*concreteness ; per-feed cap
                 take top N = target_item_count + BUFFER (=10+6)        -> ~16 ranked
  4. summarize   one isolated `claude -p` per ranked item               [LLM, isolated]
                 (prompts/summarize_item.md), concurrency-capped ~3-4
                 each: WebFetch the one article -> emit one JSON segment
                 classify result: OK | BLOCKED | ERROR | TIMEOUT
                 drop non-OK (log them); keep first target_item_count OKs
  5. intro/outro one isolated `claude -p` over kept TITLES ONLY (benign)
                 deterministic template fallback if it blocks/errors    [LLM, isolated]
  6. assemble    build manifest.json (intro + kept segments + outro)    [Python]
  7. render      python3 render.py --manifest ... --workdir ...         [unchanged]
  8. post        on render success: update feed_usage.json
  9. report      single line SHIPPED/FAILED + dropped-items summary
```

Step 2/3 never read article bodies, so the curation-time block cannot fire. Steps 4/5 each
hold exactly one item per request. `render.py` (step 7) is unchanged and still owns
`covered.json` (written only after READY), `inflight.json` recovery, `runs.jsonl`, and R2.

## Components

### `skills/daily-podcast/orchestrate.py` (new, single file)

Responsibilities: load → gather → rank → fan-out → assemble → invoke render → post-update →
report. Pure orchestration + deterministic curation; the only LLM work is delegated to
isolated `claude -p` subprocesses. Depends on: `feedparser` (already a project dep), stdlib
(`subprocess`, `concurrent.futures`, `json`, `xml`/OPML parse, `urllib`/`datetime`), and
`render.py` by path.

CLI:

| Flag | Purpose |
| --- | --- |
| `--dry-run` | Forward to `render.py --dry-run`; skip `covered.json`/`feed_usage.json` writes. |
| `--workdir PATH` | Stable workdir (enables render.py resume). Default: per-date `/tmp/daily-podcast-<date>`. |
| `--limit N` | Cap candidates fanned out (testing). |
| `--manifest-only` | Stop after assembling the manifest (inspect without rendering). |
| `--concurrency N` | Per-item `claude -p` pool size (default 3). |

### `skills/daily-podcast/prompts/summarize_item.md` (new)

Single-item summarizer prompt, productionized from the validated spike. Inherits SKILL.md's
segment + altitude rules (report what/who/impact/response; never exploit code, payloads,
commands, or step-by-step attack procedure; ≥600 chars, no URLs, abbreviations expanded).
The orchestrator fills `{title}`, `{url}`, `{feed_name}` and passes the result as the
`claude -p` argument.

**Output contract:** the model prints exactly one JSON object as its final output:

```json
{"ok": true,  "segment": "<spoken segment, 600-900 chars>", "source_url": "<the item url>"}
{"ok": false, "reason": "<why not summarizable>"}
```

The orchestrator parses the **last** JSON object in stdout. Classification:

- valid `{"ok":true,...}` with non-empty `segment` → **OK**
- valid `{"ok":false,...}` → **REFUSED** (soft model refusal) → dropped
- no parseable JSON **and** policy markers in stdout/stderr/exit
  (`/usage policy|violative cyber|unable to respond|cyber verification/i`) → **BLOCKED** → dropped
- otherwise (no JSON, non-zero exit, parse failure) → **ERROR** → dropped
- exceeds per-call timeout (default 150 s) → **TIMEOUT** → dropped

### `skills/daily-podcast/prompts/daily.md` (deprecated reference)

Kept as a reference (segment rules, history) with a deprecation banner at the top pointing to
`orchestrate.py`. No longer the cron entry point.

### `skills/daily-podcast/render.py` (unchanged)

Consumes the same manifest shape it does today. All invariants preserved verbatim.

## Deterministic ranking (metadata only)

Score components, combined as a weighted sum, sorted descending:

- **source_tier** — static map feed_name/domain → tier. Tier 1: original reporting/analysis
  (Anthropic blogs, Simon Willison, One Useful Thing, Zvi, etc.); Tier 2: news outlets (Ars,
  TechCrunch, The Verge, WIRED, MIT Tech Review, darkreading, Help Net Security); Tier 3:
  aggregators/community (Hacker News, reddit, Google News). Unknown feeds default to Tier 2.
- **recency** — normalized within the lookback window (newer scores higher).
- **feed_variety** — penalty if the item's feed appears in `feed_usage.json` within the last
  3 days (mirrors daily.md priority 3).
- **concreteness** — small bonus when title/RSS-summary contains digits, version-like tokens,
  a CVE id, or a capitalized product name (concrete > abstract). Metadata-level only.
- **per-feed cap** — at most K (default 3) items from one feed in the ranked top-N, so a
  chatty feed can't dominate.

`N = target_item_count + BUFFER` (BUFFER default 6 → ~16 fanned out to reliably yield 10
survivors). Weights, BUFFER, K, and the tier map are tunable constants at the top of
`orchestrate.py`, not load-bearing for correctness.

## Degradation & error handling

- **Per-item block/error/timeout** → drop that item, log it, continue. The defining behavior.
- Keep the first `target_item_count` (default 10) OK segments in ranked order.
- **5–9 survivors** → ship a shorter episode. **1–4 survivors** → still ship (honor daily.md's
  "ship shorter; do not pad"). **0 survivors** → `FAILED no viable items`.
- **Feed unreachable** during gather → skip after one retry, log, continue (as today).
- **Intro/outro call blocks/errors** → fall back to a deterministic template intro
  ("Today's digest for <date>. <N> stories today. Here's the rundown.") and a fixed sign-off,
  so the run never dies on intro/outro.
- **render.py non-zero** → surface its stderr last line as `FAILED <reason>` (as today).
- Resume/partial-failure recovery stays entirely inside `render.py` (unchanged); re-running
  `orchestrate.py` with the same `--workdir` reuses render.py's resume path.

## Observability

- `~/.config/daily-podcast/dropped.jsonl` (new, append-only): one record per dropped item —
  `{timestamp, feed_name, url, reason: blocked|refused|error|timeout, detail, run_date}`.
  Powers the Cyber Verification appeal (concrete blocked examples) and day-to-day debugging.
- Orchestrator stats to stderr: candidates gathered, ranked N, per-outcome counts, survivors.
- `runs.jsonl` continues to be written **by render.py** with its existing stable schema — the
  orchestrator does **not** mutate that schema (orchestrator stats live in `dropped.jsonl` +
  stderr instead, to keep render.py's RUN_LOG_FIELDS invariant intact).

## New persistent state

- `~/.config/daily-podcast/feed_usage.json` — `{feed_name: last_used_iso_date}`, written by
  `orchestrate.py` after a successful (non-dry-run) render. Read by the feed_variety ranker.
  Orchestrator-owned; never touched by `render.py`. Distinct from `covered.json` (per-URL,
  render-owned).

## What stays unchanged (render.py invariants)

All invariants in the project `CLAUDE.md` remain: strict 1:1 segment↔source mapping; max-3
short-chapters padding; `LAST_SILENCE_MS=0` tail; `covered.json` only after READY + 180-day
prune; `uploaded.json`/`inflight.json` resume + cross-day recovery; mono 44.1k throughout;
`runs.jsonl` append-only stable schema; `--prune-workdirs` guards; R2 publish additive &
non-fatal; the four voice modes. The orchestrator produces a manifest render.py already
accepts; it adds no new render.py behavior.

## Testing strategy

Deterministic, no live LLM:

- **gather** — feed parsing + lookback filter + covered.json dedup against mocked feedparser
  output (including a 404/timeout feed → skipped).
- **rank** — scoring/order/per-feed-cap/feed_variety penalty are pure functions over metadata
  fixtures; assert exact ordering.
- **per-item classification** — given canned `claude -p` stdout/stderr/exit fixtures
  (valid OK JSON; `ok:false`; a verbatim policy-block string; garbage; timeout), assert
  OK/REFUSED/BLOCKED/ERROR/TIMEOUT and that non-OK items are dropped + logged.
- **assembly** — survivors → manifest shape (intro + segments + outro, 1:1 source mapping);
  degradation thresholds (0 → FAILED, <target → shorter).
- **subprocess boundary** — `claude -p` invocation is mocked; we test the orchestrator's
  handling, not the model.
- Existing `render.py` tests (`tests/test_render.py`, `tests/test_r2.py`) remain untouched and
  must stay green. New tests live in `tests/test_orchestrate.py`. CI matrix unchanged (3.10–3.12).

## Risks & validation-before-cutover

- **Scale (primary risk):** the spike was 5 items; production fans out ~16/day with several
  spicy. Classification is per-request, so it *should* scale, but the operator is
  verified-yet-blocked — possible account-level behavior we can't observe. **Mitigation:**
  one full `orchestrate.py --dry-run` at real scale (real feeds, ~16 items) must pass before
  flipping the cron from `daily.md` to `orchestrate.py`. `dropped.jsonl` from that run tells
  us the real block rate.
- **Curation quality:** deterministic ranking loses some editorial nuance vs an LLM. Accepted
  tradeoff (daily.md's priorities are already near-mechanical); revisit only if episode
  quality regresses.
- **`claude -p` per-item cost/latency:** ~16 isolated sessions/day, concurrency-capped at 3
  (per the known fan-out rate-limit "no StructuredOutput" red herring — keep concurrency low).

## Migration / rollout

1. Land `orchestrate.py`, `prompts/summarize_item.md`, `tests/test_orchestrate.py`.
2. Add deprecation banner to `prompts/daily.md`.
3. Update `SKILL.md` (Running the pipeline / Scheduled runs / Architecture; document
   `summarize_item.md`, `feed_usage.json`, `dropped.jsonl`) and `README.md` (cron recipe).
4. Update project `CLAUDE.md` (architecture + invariants: the no-pooling invariant,
   drop-on-block, new state files, `daily.md` deprecation).
5. **Gate:** one real-scale `--dry-run` passes → flip cron to `orchestrate.py`.

## Open questions (tunable, non-blocking)

- Exact tier map, score weights, BUFFER, per-feed cap K, concurrency, per-call timeout — start
  with the defaults above; tune from `dropped.jsonl` + the first real-scale dry run.
- Whether to expose a `--selftest` on `orchestrate.py` mirroring render.py's (nice-to-have).
