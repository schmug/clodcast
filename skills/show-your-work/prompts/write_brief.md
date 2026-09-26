# Show Your Work — write one brief

Show Your Work explains frontier-lab AI alignment research to newcomers. A brief
is a short explainer segment on one post: what it is, what it found, why a
newcomer might care.

## The post: the only article you may read

- Title: <<TITLE>>
- Lab: <<LAB>>
- Feed summary: <<SUMMARY>>
- URL: <<URL>>

Read the post at the URL with WebFetch. Read nothing else.

## Shape

- <<MIN_CHARS>>–<<MAX_CHARS>> characters of spoken text in total.
- Spoken by the **explainer**, with at most ONE **skeptic** line (a question or
  a caveat the post itself states).
- Attribute every claim. Define any term of art. Never point at a link or the
  show notes; end on substance. Plain spoken sentences only.

## Visual beats (optional, up to two)

Each beat attaches to a line (`"line"`, a 0-based index) and a `"cue"` (a few
words copied from that line):

<<BEATS>>

## Output

Return ONE line of JSON and nothing after it:

```
{"ok": true, "lines": [{"speaker": "explainer", "text": "..."}], "beats": []}
```

If you cannot write it, return `{"ok": false, "reason": "<why>"}`.
