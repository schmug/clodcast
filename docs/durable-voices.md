# Creating a Durable Voice

The default `voice: "house"` in clodcast uses **`ref_audio` voice cloning** — every episode is generated from a single reference clip stored at `skills/daily-podcast/refs/house_voice.wav`. This document explains why that matters, how the bundled clip was made, and how to make your own.

## Why "durable" matters

Qwen3-TTS has two ways to produce a custom voice:

| Mode | API | What happens each render |
|---|---|---|
| **VoiceDesign** | `generate_voice_design(instruct="...")` | Model re-synthesizes a voice from your natural-language prompt. Same prompt → similar but **not identical** voice |
| **Voice cloning** | `generate(ref_audio="...", ref_text="...")` | Model regenerates the timbre and prosody of a specific reference clip. Same clip → **stable** voice |

A short test clip with VoiceDesign sounds great. Ship 30 episodes of the same script with the same instruct and you'll notice:

- **Pacing drift** — same text renders at 66.4 s in one run and 68.0 s in another (~2.5% variance)
- **Timbre shift** — a touch brighter or warmer between runs
- **Enunciation creep** — longer scripts trigger more "broadcast" articulation than short ones
- **Personality drift** — listeners notice when "the host sounds different this week"

`ref_audio` cloning fixes all four. The model is locked to a specific waveform's identity instead of re-rolling from a prompt.

The cost: you have to **make one good reference clip** up front. That's what this guide is about.

## The two paths to a reference clip

### Path A: VoiceDesign instruct → render → lock

You describe the voice you want in natural language, render with the VoiceDesign model, listen, iterate the prompt, and save the rendered audio as your reference clip.

**When to use:** you don't have a recording of the voice you want and aren't a voice actor.

This is how the bundled `house_voice.wav` was made. See [the iteration workflow](#voicedesign-iteration-workflow) below.

### Path B: Human or external recording → lock

You record a ~20-30 second clip of someone reading a script (yourself, a friend, a paid voice actor), or extract a clean clip from existing audio you have the right to use.

**When to use:** you already have a voice in mind and access to a recording.

This skips the iteration entirely. Make sure:
- Clean signal, no background noise
- Single speaker, no music or overlapping voices
- Natural delivery (no over-acting — the model will reproduce whatever's in the clip)
- Long enough to capture varied prosody (~20-30 seconds is the sweet spot)
- You own the rights or have permission

## VoiceDesign iteration workflow

The actual loop that produced the bundled house voice.

### 1. Write a candidate prompt

VoiceDesign prompts work best with this structure:

```
A <gender> voice in <age range>, <register/timbre cues>.
<delivery cues>.
<explicit negatives>.
```

Example (the locked house instruct):

```
A female voice in her early forties speaking in an even tone.
Low pitch variation, no host energy, no broadcast inflection,
no dramatic emphasis. Bright but human, unobtrusive, not performative.
Clear and natural. Resonant lower register.
```

### 2. Render 2-3 candidates at a time

Use a single test script (~20s of varied prosody — questions, declarations, transitions) and render the same text with each candidate prompt. Listen with `afplay <file>.mp3` or your audio player.

Render snippet:

```python
from mlx_audio.tts.utils import load_model
import soundfile as sf, numpy as np

model = load_model("mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-bf16")

text = ("Hey, welcome to the show. Today we're talking about something interesting. "
        "First, the headline. Then, why it matters. And finally, what to do about it. "
        "Let's get into it.")

candidates = {
    "A_warm":   "...",
    "B_punchy": "...",
}

for name, instruct in candidates.items():
    results = list(model.generate_voice_design(text=text, language="English", instruct=instruct))
    audio = np.concatenate([np.array(r.audio) for r in results])
    sf.write(f"/tmp/test_{name}.wav", audio, results[0].sample_rate)
```

### 3. Pick the closest and iterate

Pick the one that's closest to what you want, then describe **what's still off** in 1-2 specific words ("too dramatic", "over-enunciating", "too newsreader", "still nasally"). Adjust the prompt and re-render.

### 4. Known failure modes

Iterating the bundled clip surfaced these patterns. Avoid or work around them:

| Symptom | Cause | Fix |
|---|---|---|
| Sounds theatrical / "podcast host" performance | Positive adjectives like *warm*, *conversational*, *casual*, *energetic* | Strip them; describe what you want as *what it's not* |
| Noir / detective-novel weight in silences | Words like *slightly disengaged*, *measured*, *mysterious* | Replace with *natural*, *present*, *matter-of-fact* |
| Whimsical / sing-song delivery | Anchors like *audiobook narrator*, *reading documentation aloud* | Anchor with profession/age instead (*a female voice in her forties*) |
| Over-enunciation | `crisp articulation`, `crisp consonants` | Drop entirely or replace with *soft articulation*, *rounded vowels* |
| "Mystery dinner theater" — implied tension in pauses | Combination of measured pacing + dramatic instruct | Add explicit negatives: *no portentous pauses*, *no narrative weight* |
| All variants sound the same / showy | Prompt over-relies on positive descriptors | Switch to **subtractive framing** ("no X, no Y, no Z") with one or two anchor traits |

The best prompts are short, with concrete anchors (age, register, resonance) and explicit negatives for whatever has been intruding.

### 5. Lock in your favorite render as the reference

Once a render sounds right, save it as `skills/daily-podcast/refs/house_voice.wav`:

```python
sf.write(
    "/path/to/clodcast/skills/daily-podcast/refs/house_voice.wav",
    audio,
    24000,
    subtype="PCM_16",  # smaller file, no quality loss for voice cloning
)
```

And the exact transcript to `skills/daily-podcast/refs/house_voice.txt`:

```
Hey, welcome to the show. Today we're talking about something interesting.
First, the headline. Then, why it matters. And finally, what to do about it.
Let's get into it.
```

The transcript is critical — Qwen3-TTS uses it as the alignment target during cloning. Even small typos hurt the clone quality. Match the audio word-for-word.

That's it. From the next render onward, every `voice: "house"` episode clones this clip.

## Verifying the clip is stable

Render the same script twice with the new reference and compare. Both files should:

- Be within a few hundred milliseconds of each other in duration
- Sound like the same person (you can A/B with `afplay`)
- Not introduce new dialect or inflection patterns the clip didn't have

If they drift noticeably, your reference clip might be too short or have ambiguous prosody. Try a longer clip (25-30s) with cleaner enunciation.

## Maintaining multiple voices

The skill supports four voice modes (set in the manifest):

```jsonc
{
  "voice": "house",          // ref_audio clone from refs/house_voice.wav (default)
  "voice": "random",         // preset rotation over [Ryan, Aiden, Ethan, Chelsie]
  "voice": "Ryan",           // single fixed preset
  "voice_instruct": "..."    // VoiceDesign mode, full natural-language override
}
```

If you want multiple recurring voices (e.g., a different reference clip per show), the cleanest approach is to add a `ref_audio` and `ref_text` field to the manifest schema and extend `render.py` to pass them through. The current default is the single bundled house voice; the multi-voice case is an open extension point.

## When to re-tune

Common triggers and the right response:

| Trigger | Right move |
|---|---|
| "The voice sounds different in long form than the short test" | Expected with VoiceDesign; switch to `ref_audio` cloning (you already have it as the default) |
| "I'm tired of this voice" | Make a new reference clip (Path A or Path B above), swap the `wav` + `txt` in `refs/`, you're done |
| "I want a more energetic intro" | Don't re-tune; use `voice_instruct` on just the intro segment if needed (per-segment voice is an open extension point) |
| "Listeners say it sounds robotic" | The reference clip is probably too short or too uniform in prosody. Re-record with more vocal variety |
| "I want a male voice for one episode" | Use `"voice": "Ryan"` (or another preset) for that single manifest; leaves the house voice intact for everything else |

## TL;DR

1. **Default state:** `voice: "house"` uses `refs/house_voice.wav` (stable across runs)
2. **To change the voice:** replace the `.wav` and `.txt` in `skills/daily-podcast/refs/`
3. **To design a new one:** iterate VoiceDesign prompts using subtractive framing + concrete anchors, then save the rendered audio as your new reference
4. **To verify durability:** render the same script twice with the new reference; durations should match
