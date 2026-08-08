# WebFetch-blocked source

**First seen:** 2026-06-21 (WIRED) · **Recurred:** 2026-07-31 (CISA), 2026-08-07
(hackread, The Register) · **Severity:** silent content loss

## Symptom

During in-session curation, fetching an article body fails:

- Hard `unable to fetch`: `wired.com`, `theverge.com`, `arstechnica.com`, `vox.com`
- `403 Forbidden`: `darkreading.com`, `databreaches.net`, `thehill.com`,
  `cisa.gov`, `hackread.com`
- `theregister.com` **404s on the URL shape WebSearch returns** (a trailing
  numeric segment), and trimming it 404s too.

A blocked fetch means no segment body. In the orchestrator path the item is
dropped to `dropped.jsonl` and the episode ships one story lighter — quietly.

## Root cause

The outlets block automated fetching. The orchestrator's deterministic curation
is metadata-only (feedparser titles, dates, summaries) so it never hits this; the
**in-session** path, which fetches article bodies, does.

The compounding problem was that this knowledge lived only in an operator's
memory. Nothing in the repo recorded which outlets were unfetchable, so each
recurrence was rediscovered from scratch.

## Automated remedy — partial, and deliberately so

The registry now ships with the skill at
[`skills/daily-podcast/blocked_sources.json`](../skills/daily-podcast/blocked_sources.json),
consumed by `load_blocked_sources()` / `is_blocked_domain()`. Each entry carries
the reason, the date confirmed, a recovery `strategy`, and a `substitute`. It also
records `preferred` (outlets that fetch clean) and `non_article_hosts` (YouTube,
Reddit, HN permalinks — never valid as a segment `source_url`).

Matching is **host-based, never substring**: `notwired.com` does not match
`wired.com`, and a path containing a blocked domain is not itself blocked.

**What is not automated:** choosing a replacement article. That is a curation
judgement — which story to tell and from which outlet — and silently
substituting one would change the episode's content without anyone deciding to.
The registry makes the data available at the moment of the decision; a human or
the curating agent still makes it.

Recovery strategies that have worked:

- **primary-source** beats alt-outlet when the blocked outlet is *reporting on* an
  announcement — fetch the project's own blog and keep the blocked outlet as
  `source_url`. (Worked 2026-07-31: Ars blocked, `blog.modelcontextprotocol.io`
  fetched clean with more detail.)
- **alt-outlet**: WebSearch the topic, fetch a non-blocked outlet covering the
  same story, and use **that** URL as `source_url` — honest, because it is what
  was actually read.
- **feed-summary**: CISA's feed summaries are unusually detailed and usually enough.

## Test that guards it

- `test_blocked_sources_registry_ships_in_the_skill` — the registry exists, parses,
  covers the known outlets, and every entry carries a reason.
- `test_is_blocked_domain_matches_subdomains_not_substrings`
