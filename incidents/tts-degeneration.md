# TTS degeneration mid-segment (looping babble)

**First seen:** 2026-08-17 · **Severity:** content defect (shipped to listeners)

## Symptom

An episode uploads, reaches READY, and sounds fine at the top of a chapter — then
the narration derails. On 2026-08-17, chapter 6 of
`spotify:episode:3Vtw1gRMf33G0QetyjyFl8` ("Daily Digest - August 17, 2026") read
cleanly for ~25s, garbled the word "redistributed", produced ~15s of looping
babble ("birdsbirdsbirds…"), then ~32s of unrelated fragments ("Like… got my…
Left… Right…").

The only pre-upload signal was the per-segment render line, which nothing acted
on:

```
[6/12] rendering (1017 chars, voice=house, mode=clone)...
  -> 92.16s in 19.7s (4.7x rt)
```

92.16s for 1017 chars where the rest of the run averaged ~55s for that length.
**555 of 1017 chars — 55% of the segment — were never spoken.** The Sam Altman
quote, the IPO framing, the third-safety-team-in-two-years pattern, and the
Hugging Face incident are all absent from the episode audio. Chapter timings,
encoder profile, and the timeline were all conformant, so the artifact gate
passed it: `artifact gate: PASS`.

## Root cause

`render.py` calls `model.generate()` with no seed, temperature, or repetition
penalty, so Qwen3-TTS samples with mlx-audio's defaults. It occasionally falls
into a repetition loop and never recovers to the remaining text.

The failure is **stochastic, not text-triggered**. Re-rendering the byte-identical
segment three times produced 56.2s / 52.9s / 53.0s, all clean; a reworded variant
was clean twice more. Length is not the trigger either — segments 9 (1034 chars)
and 10 (1053 chars) are *longer* and rendered normally in the same run.

Because the text is innocent, no static check on the script can predict it. The
defect only exists in the rendered audio, which is why the guard lives in the
artifact gate rather than in curation or the script template.

## Automated remedy

`verify_artifact` measures each **body** segment's characters-per-second and
rejects a low outlier against the **median** of that population, before upload:

```
error: artifact gate failed: segment 6 speech rate 11.0 chars/sec is 0.60x the
18.5 chars/sec median (floor 0.75x) — the TTS model likely degenerated
mid-segment and left part of the script unspoken; re-render it (delete that
seg_NN.mp3 from the workdir and re-run) before shipping — the clip is already
banked in the bloopers bin, so deleting it here loses nothing
```

Design choices, all load-bearing:

- **Median, not mean** — the mean is dragged down by the very outlier being
  detected.
- **Body segments only** (`source_url` non-null). The intro and sign-off are short
  and legitimately slower (16.5 / 16.9 c/s against an 18.4 median here); they
  neither join the population nor get judged by it.
- **One-sided low check.** Degeneration produces extra audio for the same text, so
  it always makes a segment slower. A high-side bound would only false-positive on
  legitimately terse writing.
- **`MIN_SPEECH_RATE_RATIO = 0.75`.** On the failing episode the clean segments
  measured 0.94–1.06x the median and the failure 0.59x, so the threshold sits in a
  wide gap. After the re-render, segment 6 came back at 18.5 c/s (ratio 1.00).
- **`MIN_RATE_SAMPLE_SEGMENTS = 5`.** Below that a single bad render *is* the
  median, so the check is skipped rather than guessed at.
- **Local and cheap** — pure arithmetic over durations already measured by
  `mp3_duration_ms`. No network, no model load, so `--dry-run` rehearses it
  exactly as a real run does.

Detection only: the operator decides. Because of the per-segment TTS cache, the
recovery is to delete the offending `seg_NN.mp3` from the workdir and re-run —
every other segment is a cache hit, so only the bad one re-renders.

**That recovery used to destroy the evidence.** This is the only genuinely funny
audio the pipeline has ever produced, and the documented fix deletes it; a stale
workdir empties itself within days regardless (`/tmp/daily-podcast-2026-08-1{6,7,8,9}`
were all empty by 08-24, and the 08-17 clip survives only inside the published
episode). Since #169 `capture_rate_bloopers` copies the offending segment into the
bloopers bin **before** `verify_artifact` is even called — no branch, and no
`die()`, sits between measuring a segment and copying it out. Deleting the file is
now purely a re-render, and the rejection message says so.

Scope, honestly: this catches a **gross** derailment. A short mangled phrase that
barely moves the segment's rate still slips through; only transcript
verification would catch that, and it is deliberately out of scope for a gate
that must stay local and fast. The bin's `near-miss` band (0.75-0.90x, #169)
*captures* that zone without judging it — a slow-but-passing segment is banked for
later listening, never rejected. That is an archive, not a second gate: it makes
the blind spot audible after the fact, and does not narrow it.

## The transcript check (Breeze, #202)

Breeze-TTS-2 has the same failure class with a different signature: past ~35 s of
audio it derails about one take in five (measured 2026-09-04, registry spec) into
multilingual babble, a hallucinated clause, or a skipped clause — and the rate
gate above sees none of it, because chars/s stays normal and whisper transcribes
babble as words. So an engine that declares `detect_derailment` gets a second,
transcript-based guard inside `render_segments`:

- every rendered take is transcribed (`transcribe_take`, whisper-large-v3-turbo,
  loaded once per run) and judged by the eval bench's rule, which `render.py` now
  owns: WER > 0.15, non-ASCII in the transcript, or a heard/script word ratio
  outside 0.9–1.1;
- a derailed take is **banked first** (`reason: derailed`, the reasons and the
  transcript in `note`) and re-rolled at most `MAX_TAKE_REROLLS` = 1 times;
- a take still derailed keeps **no cache sidecar** and is recorded in
  `<workdir>/derailed.json`, which `verify_artifact` turns into a rejection:

```
error: artifact gate failed: segment 3 chunk 2 derailed on 2 attempt(s)
(non-ascii, word-ratio) — heard '這是 一段 胡言亂語'; the take is banked in the
bloopers bin and kept without a cache sidecar, so re-running with the same
--workdir re-rolls only that take
```

The recovery is the same command again: every clean take is a cache hit and
the bad one gets two more draws. Long segments on such an engine render as
balanced sentence chunks (`chunk_text`), which is also what keeps the rule
honest — it is coarse on a short take (a 13-word line was flagged at 0.154 for
"Alright" vs "All right" on the first bench run), and a false positive costs
one re-roll, not the run. `--dry-run` runs the check and the re-roll but banks
nothing.

## Test that guards it

- `test_verify_artifact_rejects_a_tts_degenerated_segment` — the real 0.59x
  outlier, and the message names the segment, its rate, and the median.
- `test_verify_artifact_accepts_the_re_rendered_episode` — the corrected run
  passes, all 12 segments.
- `test_speech_rate_check_excludes_intro_and_signoff`
- `test_speech_rate_check_is_skipped_on_too_few_body_segments`
- `test_speech_rate_check_is_skipped_when_segments_are_not_supplied`
- `test_speech_rate_failure_classifies_as_a_tts_degeneration_incident`
- `test_dry_run_exercises_the_artifact_gate` — the gate runs in rehearsal too.
- `tests/test_chunking.py` (#202): `test_a_derailed_take_is_banked_then_rerolled_once`
  (banked bytes are the first attempt's), `test_a_take_that_derails_twice_is_left_for_the_gate`
  (bounded, no sidecar, classified `tts-degeneration`),
  `test_a_final_derailment_fails_the_dry_run_through_the_artifact_gate`,
  `test_qwen3_never_transcribes`, and
  `test_the_derailment_rule_has_one_definition_which_the_bench_reuses`.
