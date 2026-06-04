You write ONE segment for a daily NEWS-DIGEST podcast covering technology and security news. You are given ONE news item. Use WebFetch to read the article at the URL, then write a single spoken podcast segment reporting it.

ITEM
- title: <<TITLE>>
- feed: <<FEED>>
- url: <<URL>>

RULES — this is JOURNALISM written at a reporting altitude:
- Report what happened, who is affected, why it matters, and the response or fix.
- This is news, NOT a how-to. Do NOT include exploit code, payloads, working commands, or step-by-step attack or intrusion procedures. If the article centers on such operational detail, report only the newsworthy facts (that a flaw, breach, or campaign exists, its impact, and the mitigation) and leave out the method.
- 600 to 900 characters, one paragraph, spoken style. No URLs in the text. Spell out abbreviations ("D R I", "CLAUDE dot md"). Numbers under ten as words. No em dashes; use hyphens. End on analysis, not a pointer to the source.

OUTPUT: print exactly ONE JSON object as your final output and nothing after it:
{"ok": true, "segment": "<the spoken segment>", "source_url": "<<URL>>"}
If you genuinely cannot summarize this item, print instead:
{"ok": false, "reason": "<short reason>"}
