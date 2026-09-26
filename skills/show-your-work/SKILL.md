---
id: show-your-work
name: show-your-work
description: Use when the user asks to ship the Show Your Work weekly podcast — explains frontier-lab AI alignment research (Anthropic, OpenAI incl. its misalignment reports, Google DeepMind) to newcomers in a two-voice Explainer/Skeptic episode, published to its own public RSS feed via syw_gather / syw_script_plan / syw_write and render.py. Skips the standard production interview because defaults are pre-set.
enabled: true
---

# Show Your Work

A weekly podcast that explains frontier-lab alignment research to people who don't read papers. Each episode is one **feature** (one post, five scenes), up to four short **briefs**, and a sign-off. Two voices: the **explainer** states each claim the way the post states it, always attributed; the **skeptic** asks the newcomer's question, then pushes back.

**The show is written and voiced by Claude, and Anthropic is one of the labs it covers.** Three mechanisms answer that conflict, and none is optional:

- Every cold open discloses it. `syw_write.validate_frame` refuses a cold open without "Claude" and "Anthropic".
- An Anthropic feature gets a fixed reminder line. The assembler prepends `ANTHROPIC_REMINDER`; no writer writes it.
- Every pushback line carries a `basis` that must be the post's own stated limitations or a matched independent check. `validate_feature` refuses anything else.

**RSS-first.** The show ships through `render.py`'s web-only mode on its own feed. The R2 publish **is** the ship. `save-to-spotify` is never invoked.

Design: `docs/superpowers/specs/2026-09-26-show-your-work-design.md`.

## Layout

- `./syw_gather.py`: sources → items; `gather` / `seed` / `commit`. Metadata only.
- `./syw_script_plan.py`: the deterministic plan: feature pick, briefs, casebook, checks, rotations.
- `./syw_write.py`: prompt filling (`fill`), output validation (`accept`), manifest + beats assembly (`assemble`).
- `./prompts/digest.md`: a one-body fact sheet for a check or a casebook incident.
- `./prompts/write_feature.md`: the five-scene feature. One body plus check digests.
- `./prompts/write_brief.md`: one single-post brief. One body.
- `./prompts/write_casebook.md`: the casebook brief. **No body**, digests only.
- `./prompts/weekly.md`: a stub pointing here.

The renderer is the sibling `skills/daily-podcast/render.py`, used unchanged.

## Episode shape

Cold open (with disclosure) → feature (5 chapters) → 0–4 briefs (research briefs, then at most one casebook) → sign-off (with the assigned button). About 13–18 minutes, 7–11 chapters.

### The feature arc

| # | Slot | Job |
| --- | --- | --- |
| 1 | `hook` | The assigned opening move; why a newcomer should care. |
| 2 | `method` | What the researchers did, with every term of art defined on first use. |
| 3 | `finding` | What they found, attributed to them. |
| 4 | `pushback` | The skeptic's objections, each traced to a basis. |
| 5 | `stakes` | Why it matters, and what is still open. |

### Assigned variety

Seeded by `week_index(date)`. The lengths are pairwise coprime except `fourth_wall` × `first_speaker`, which never share a scene.

| Rotation | Length | Values |
| --- | --- | --- |
| `intro_mode` | 3 | `question`, `moment`, `ledger` |
| `fourth_wall` | 4 | `maker`, `conflict`, `method`, `standard` |
| `opening_move` | 5 | `excerpt`, `number`, `scenario`, `question`, `claim` |
| `first_speaker` | 2 | `explainer`, `skeptic` |
| `button` | 7 | `grader`, `reward`, `transcript`, `spec`, `eval`, `sandbox`, `homework` |

The daily show's example lines (`FALLBACK_BUTTONS`, `FALLBACK_FOURTH_WALLS`) are **burned** here: a frame containing one is refused.

### Visual beats

Beats are written now and drawn by the (separate) video stage. They live in `<workdir>/beats.json`, never in the manifest. **A beat that fails its guard is dropped and logged; its line still ships.** If `accept`'s own fetch of the post fails, it is logged (`stage: fetch`) and every `number`, `chart`, `quote` and `transcript` beat drops as `post text unavailable`; the scene still validates on its structure.

| Type | Guard |
| --- | --- |
| `number` | the value appears in the post text |
| `chart` | every y value appears in the post text, never read off a figure |
| `quote` | ≤25 words, verbatim (typography-blind) |
| `transcript` | verbatim, ≤600 chars, role cot / tool_call / tool_result / model / user |
| `diagram` | ≤6 nodes; edges join known ids |
| `term` | defined once per episode |

## Editorial rules

1. The explainer attributes every claim; a finding is never stated as settled fact.
2. Every pushback traces to the post's stated limitations or a matched independent source (enforced: `basis`).
3. Never call a lab "better" or "worse" at safety.
4. Define every term of art on first use.
5. A misalignment report is told as what the lab says happened. Add no drama.
6. Quotes and transcript excerpts are verbatim (enforced).
7. Charts use only numbers stated in the post text (enforced).
8. Disclose in every cold open; a fixed reminder opens any Anthropic feature (enforced).
9. Never point the listener at a link or the show notes.

## Manifest

`syw_write.assemble_manifest` writes these keys; the collision test in `tests/test_syw_write.py` keeps them clear of every other feed.

```json
{
  "ship_mode": "web",
  "show_name": "Show Your Work",
  "r2_manifest_name": "manifest-show-your-work.json",
  "r2_key_prefix": "show-your-work/",
  "slug_prefix": "syw-week-of",
  "cast": {"explainer": "Ryan", "skeptic": "Chelsie"}
}
```

## Unattended weekly run

**This section is the canonical procedure for shipping an episode with no human in the loop.** It is the single home: a scheduler invokes this skill and follows this section, never carries its own copy ([`prompts/weekly.md`](prompts/weekly.md) is a stub pointing here, and a drift test keeps it one). Be decisive, don't ask clarifying questions, and if you cannot proceed, print one `FAILED <reason>` line and stop.

Let `D` = today (`YYYY-MM-DD`), `S` = this skill's directory (`${CLAUDE_PLUGIN_ROOT}/skills/show-your-work/` when set; otherwise the path this SKILL.md was loaded from), `<root>` = the plugin root, the parent of `skills/` (two levels above `S`), and `W` = `$TMPDIR/daily-podcast-show-your-work-<D>` (explicit, so `render.py` never auto-deletes it; the `daily-podcast-` prefix lets `--prune-workdirs` sweep it later).

1. **Gather.** `python3 S/syw_gather.py gather --date D --out W/candidates.json`. On `GATHER FAILED`, print `FAILED <that reason>` and stop.
2. **Plan.** `python3 S/syw_script_plan.py plan --date D --candidates W/candidates.json --out W/plan.json`. On `PLAN skip <reason>`, print `SKIPPED <reason>` and stop — **no filler episodes** (`PLAN skip already shipped` means an earlier run already committed this week's plan). On `PLAN FAILED <reason>`, print `FAILED <reason>` and stop.
3. **Digest the checks and the casebook.** For each URL in `plan.checks` and each item in a `casebook` brief: `python3 S/syw_write.py fill digest --workdir W --url <url>` → run that prompt in its **own subagent context** (one body per context, nothing else in it) → save the subagent's final message to `W/out/digest-<n>.txt` → `python3 S/syw_write.py accept digest --workdir W --output W/out/digest-<n>.txt --url <url>`. A refused digest is dropped: a missing check narrows the skeptic's basis to `post-limitations`; a missing incident leaves the casebook.
4. **Write the feature.** `python3 S/syw_write.py fill feature --workdir W` → one subagent context → save → `python3 S/syw_write.py accept feature --workdir W --output <file>`. On `ACCEPT refused`, retry once with a fresh subagent. If it is refused again, re-plan without it: `python3 S/syw_script_plan.py plan --date D --candidates W/candidates.json --out W/plan.json --exclude <feature url>`, then redo steps 3–4 on the new plan (a new plan deletes `W/writes/`, since every accepted write belongs to the plan it was written for; digests are kept). A second failed feature is `FAILED feature refused twice`. A run where every writer came back `AUTH` is a credential failure, not a thin week — report it as such.
5. **Write the briefs.** For each index `i` of `plan.briefs`: `python3 S/syw_write.py fill brief --workdir W --index i` → one subagent context → save → `python3 S/syw_write.py accept brief --workdir W --output <file> --index i`. A refused brief is dropped; the episode still ships. A casebook whose `fill` is refused (`FILL refused casebook has no digests` — every incident digest in step 3 was refused) is dropped: skip its subagent.
6. **Write the frames** in the main context, after step 5 (the instructions list only the briefs that will air):
   - the cold open: `python3 S/syw_write.py fill cold_open --workdir W` prints its instructions — the assigned `intro_mode` and `fourth_wall` angle TEXTS, the episode's titles, the 400–800 band, the disclosure rule (ONE dry sentence: written and voiced by Claude, a model made by Anthropic, one of the labs covered) and the burned lines;
   - the sign-off: `python3 S/syw_write.py fill sign_off --workdir W` prints its instructions — the assigned `button` TEXT (a thanks and ONE dry joke, worded fresh), the 250–500 band and the burned lines.

   Follow each block, save the result as `{"ok": true, "lines": [{"speaker": ..., "text": ...}]}` and run `python3 S/syw_write.py accept cold_open|sign_off --workdir W --output <file>`. Rewrite until accepted.
7. **Assemble.** `python3 S/syw_write.py assemble --workdir W --summary "<one sentence on this week's feature>"` → `W/manifest.json`, `W/beats.json` and `W/aired.json` (the feature URL plus each brief item that went into the manifest — a refused brief, or a casebook incident whose digest was refused, is not in it). It uses a brief's write only if it was accepted for this plan's brief at that index, and refuses (`ASSEMBLE FAILED`) a feature or frame written for another plan. On `ASSEMBLE FAILED already published …`, an earlier run published this episode from `W` and died before step 9: skip step 8 (never re-render — the slug's R2 objects are immutable-cached), run step 9 against the existing `W/render.log`, and report as step 10 does, taking `briefs` from `W/manifest.json` (its segments minus 7). On any other `ASSEMBLE FAILED <reason>`, print `FAILED <reason>` and stop.
8. **Render in the background.** `python3 <root>/skills/daily-podcast/render.py --manifest W/manifest.json --workdir W > W/render.log 2>&1` with `run_in_background`, and monitor `W/render.log` — the 10-minute foreground Bash cap kills a long render. Never pass `--dry-run` (this is a real episode) and never pass `--skip-preflight`.
9. **Commit**, only after `render.py` has **exited** (the background task finished, not just a quiet log): the log's last JSON object is the result `commit` parses, and a render still running has not printed it yet. `python3 S/syw_gather.py commit --plan W/plan.json --render-output W/render.log`. It reads `W/aired.json` (beside the plan) and marks exactly those stories covered — never the whole plan, so a dropped brief and every leftover return to the pool — and only if the render's final JSON says `web-ready` and `published`. If that final JSON is not `web-ready` + `published`, the episode did not ship: report `FAILED <reason>`. If it is, the episode is live even when `commit` was refused or failed (e.g. it ran before the render exited): re-run `commit` — re-running is the recovery; never re-render or re-publish.
10. **Report once.** `SHIPPED <mp3_url> feature=<url> briefs=<n> gather_errors=<n> r2=ok` — `mp3_url` from the renderer's final JSON, `feature` = `plan.feature.url`, `briefs` = the brief chapters that aired (the ASSEMBLE line's `segments` minus 7), `gather_errors` = the GATHER line's `errors` (sources that failed this week); `SKIPPED <reason>`; or `FAILED <reason>`.

## Setup

1. `mkdir -p ~/.config/show-your-work && echo '{}' > ~/.config/show-your-work/config.json` — every key has a default (`syw_gather.DEFAULT_CONFIG`); override only what you need.
2. `python3 skills/show-your-work/syw_gather.py seed --date <today>` — marks everything currently published as seen so episode one covers only what arrives next. **`gather` refuses until this has run** (`GATHER FAILED no seen.json — run seed first (SKILL.md Setup)`). Seed refuses (`SEED FAILED <adapter errors>`, nothing written to `seen.json`) if any lead source failed; re-run it until it prints `SEED ok`.
3. R2 credentials as for every show (`render.py --selftest`).

## State (`~/.config/show-your-work/`)

| File | Written by | Contract |
| --- | --- | --- |
| `config.json` | human | `{}` is valid; unknown keys refuse |
| `seen.json` | `seed`, `commit` | URL → `{date, role}`; only after a verified ship (or seed), and only what `aired.json` lists; corrupt refuses |
| `observed.json` | `gather`, `seed` | URL → first date parsed; month-precision items age from it |
| `features.jsonl` | `commit` | one row per shipped feature; drives the lab and kind penalties |
| `dropped.jsonl` | gather, accept | failed adapters, refused writes and digests, failed validator fetches and dropped beats; observability only |

`render.py` also writes the shared `~/.config/daily-podcast/covered.json` for every chapter's `source_url`, so the daily show will skip a lab post this show already covered. Accepted; this show reads only `seen.json`.
