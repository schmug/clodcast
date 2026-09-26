# Show Your Work — write the casebook brief

"From the casebook" is a short segment that walks through recent entries in
OpenAI's public casebook of model misalignment incidents.

**Do not fetch any URL.** Work only from the fact sheets below. Each one was made
from a single report by a separate reader, and they are all you may use.

<<DIGESTS>>

## Shape

- <<MIN_CHARS>>–<<MAX_CHARS>> characters of spoken text in total.
- Spoken by the **explainer**, with at most ONE **skeptic** line.
- Take each entry in turn: what the model did, in what setting, and what OpenAI
  says it is doing about it. Tell each one as what the lab says happened; add no
  drama the fact sheet does not contain.
- Never point at a link or the show notes. Plain spoken sentences only.

## Visual beats (optional, up to three)

Each beat attaches to a line (`"line"`, a 0-based index) and a `"cue"` (a few
words copied from that line). `transcript` beats may use only the verbatim
`transcript_excerpts` above.

<<BEATS>>

## Output

Return ONE line of JSON and nothing after it:

```
{"ok": true, "lines": [{"speaker": "explainer", "text": "..."}], "beats": []}
```

If you cannot write it, return `{"ok": false, "reason": "<why>"}`.
