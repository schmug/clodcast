# Show Your Work — digest one source

You are making a fact sheet for a podcast writer who will never read this source.

**Source:** <<TITLE>> (<<KIND>>)
**URL:** <<URL>>

Read this ONE page with WebFetch. Read nothing else: no other page, no search. If
the URL has a `#fragment`, read only the section whose id matches it.

Extract, without adding anything the page does not say:

- `claims`: the source's main claims, each one sentence, attributed ("METR finds…").
- `numbers`: every number a listener would care about, as `{"value": "<exactly as
  written>", "context": "<what it measures>"}`.
- `limitations`: limitations or caveats the source itself states.
- `transcript_excerpts`: if the page quotes model transcripts, up to three short
  excerpts copied verbatim, as `{"role": "cot|tool_call|tool_result|model|user",
  "text": "<verbatim>"}`.

The whole JSON must stay under <<MAX_CHARS>> characters; cut the least important
items first.

Return ONE line of JSON and nothing after it:

```
{"ok": true, "url": "<<URL>>", "claims": [...], "numbers": [...], "limitations": [...], "transcript_excerpts": [...]}
```

If the page cannot be read, return `{"ok": false, "reason": "<why>"}`.
