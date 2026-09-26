# Show Your Work — write the feature

Show Your Work explains frontier-lab AI alignment research to people who do not
read papers. It is written and voiced by Claude. Two voices:

- **explainer**: states each claim the way the post states it, always attributed.
- **skeptic**: asks the question a newcomer would ask, then pushes back.

## The post: the only article you may read

- Title: <<TITLE>>
- Lab: <<LAB>>
- Feed summary: <<SUMMARY>>
- URL: <<URL>>

Read the post at the URL with WebFetch. Read nothing else: no other page, no
search, no link from inside the post.

## Independent sources (already digested; do not fetch them)

<<CHECKS>>

## The arc: exactly five scenes, in this order

<<ARC>>

Each scene is <<SCENE_MIN>>–<<SCENE_MAX>> characters of spoken text in total,
across 4–10 turns.

## Assigned; do not change

- Opening move: <<OPENING_MOVE>>
- The first line of the `hook` scene is spoken by the **<<FIRST_SPEAKER>>**.

## Rules

1. Attribute every claim ("OpenAI reports…", "the Anthropic team found…"). Never
   state a finding as settled fact.
2. In the `pushback` scene every skeptic line carries `"basis"`: either
   `"post-limitations"` (a limitation the post itself states) or the exact URL of
   one independent source above. Never invent an objection.
3. Never call a lab better or worse at safety.
4. Define every term of art the first time it is spoken.
5. A misalignment report is told as what the lab says happened. Add no drama the
   report does not contain.
6. Never point the listener at a link or the show notes; end each scene on
   substance.
7. Plain spoken sentences only: no markdown, no stage directions, no sound-effect
   markers.

## Visual beats

Each scene may carry up to three beats for the video. A beat attaches to one line
(`"line"`, a 0-based index into that scene's lines) and a `"cue"` (a few words
copied from that line, where the visual should appear). The beat types:

<<BEATS>>

A beat that breaks its rule is dropped; the audio is unaffected, so leave a beat
out rather than guess.

## Output

Return ONE line of JSON and nothing after it:

```
{"ok": true, "scenes": [{"slot": "hook", "lines": [{"speaker": "explainer", "text": "..."}], "beats": []}, {"slot": "method", ...}, {"slot": "finding", ...}, {"slot": "pushback", "lines": [{"speaker": "skeptic", "text": "...", "basis": "post-limitations"}], ...}, {"slot": "stakes", ...}]}
```

If you cannot write this feature (the post is unreachable, or not about AI
alignment or safety), return `{"ok": false, "reason": "<why>"}`.
