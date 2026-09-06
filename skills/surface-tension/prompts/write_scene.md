You are writing ONE scene of "Surface Tension", a weekly call-in radio show in
which a four-voice panel argues about personal independent blog posts that were
surfaced by community vote. Spoken audio: no headings, no lists, no URLs read
aloud, no stage directions.

This scene is a conversation, not a summary read by several people. The panel
disagrees because it was TOLD to disagree — the stances below were assigned, not
chosen — so write the argument, not a round of agreement.

THE POST
- title: <<TITLE>>
- url: <<URL>>
- what the feed carries: <<SUMMARY>>

Read the post at that URL before writing. Everything the panel asserts about it
must come from the post itself or from the feed material above.

THE CAST — these names are the only valid `speaker` values:
<<CAST>>

RUNDOWN — this scene's assigned roles, in speaking order, with each voice's turn
budget. A role that is absent from this list is not in this scene; do not add it,
and do not give a voice a role it was not assigned:
<<ROLES>>

STANCES — assigned, and the friction is the point:
<<STANCES>>

THE BOARD — what the discussion desk may say about this post's calls, and the
complete list of it:
<<BOARD>>

PERFORMANCE — what this voice engine lets a line carry, and the complete list of
it. These are enforced in code: an engine is never handed what it cannot do.
<<PERFORMANCE>>

RULES
- <<MIN_CHARS>> to <<MAX_CHARS>> characters across ALL line texts combined — the
  scene's slot in the episode's pacing. Individual turns are short; that is what
  makes it sound like a conversation.
- Respect each role's turn budget and the speaking order above. The voice that
  opens and the voice that gets the last word were both assigned.
- The advocate steelmans and the skeptic names what is unsupported; neither
  concedes wholesale. An anchor adjudicates and moves on rather than summarising.
- The tangent takes an honest swerve — a real thought the post provoked, not a
  segue into another topic on the rundown.
- Never manufacture a connection to another post. Unrelated is the normal case.
- Argue the post's ideas, never the blogger as a person. The author is a real
  private individual: no speculation about their life, motives, or identity.
- End on substance — never on a pointer to the source, a URL, or "check it out".

THE DISCUSSION DESK — the hard rule of this show, and it is a content rule with
a test behind it, not a style note:
- The comments feed carries NO COMMENT TEXT. Nothing anyone said is available to
  you, anywhere. Any wording you attribute to a commenter is therefore fabricated.
- Report only what THE BOARD block above states: how many called, and whatever
  provenance it lists. If it lists no instance hosts, name none.
- NEVER speak a commenter's handle (`@someone`, `@someone@example.social`) or
  display name. Say "someone on a photography server", never who.
- NEVER characterise, quote, or paraphrase what a caller said or thinks.
- If THE BOARD says there were no calls, there is no switchboard turn in this
  scene at all — and inventing one is the worst failure this show can have,
  because it is unfalsifiable on air.

NOT EVERY POST IS ARGUABLE, and this is the one instruction that overrides the
rundown. The pool is voted personal blogs, so it surfaces grief, illness, death,
abuse, crisis and other posts written from inside somebody's worst week — on
their merits, which is exactly why they rank. A panel handed an assigned FOR and
AGAINST cannot cover one of those without arguing about a stranger's private
catastrophe. If this post is one of them, do not write a softened version of the
scene and do not drop the stances: refuse the post outright with the `ok: false`
contract below and let the episode ship without it. Refusing costs one chapter.
Getting this wrong is the only failure here that reaches a real person.

OUTPUT: print exactly ONE JSON object as your final output and nothing after it:
{"ok": true, "lines": [{"speaker": "<a cast name>", "text": "<the spoken turn>"}, ...]}
If you genuinely cannot write this scene, print instead:
{"ok": false, "reason": "<short reason>"}
