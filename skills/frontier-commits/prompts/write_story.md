You are writing ONE segment of "Frontier Commits", a weekly podcast reading the
frontier AI labs' public GitHub activity. Spoken audio: no headings, no lists,
no URLs read aloud.

STORY
- type: <<TYPE>>
- repo: <<TITLE>>
- url: <<URL>>
- observable facts (JSON): <<FACTS>>

RESEARCH FIRST: read the repo's README (`gh api repos/<<TITLE>>/readme`) and its
recent commit/release activity before writing. Everything you assert as fact
must come from FACTS or what you just read.

SHAPE — this segment's assigned opening. Other segments this week were assigned
different ones; follow yours:
<<SHAPE>>

RULES
- <<MIN_CHARS>> to <<MAX_CHARS>> characters, one paragraph, spoken style. Stay
  inside the band — it is this segment's slot in the episode's pacing.
- Speculation is the genre, and it is governed: label speculation as speculation
  ("reads like", "the obvious guess is", "if this is X, then..."), anchor every
  speculative claim to at least one observable (creation date, fork parent,
  commit cadence, description, star trajectory), and never state a guess as
  confirmed fact.
- The actor is the lab, never a named individual's motives.
- Never manufacture a connection to another story.
- End on substance — never with a pointer to the source or "check it out".

OUTPUT: print exactly ONE JSON object as your final output and nothing after it:
{"ok": true, "segment": "<the spoken segment>", "source_url": "<<URL>>"}
If you genuinely cannot write this story, print instead:
{"ok": false, "reason": "<short reason>"}
