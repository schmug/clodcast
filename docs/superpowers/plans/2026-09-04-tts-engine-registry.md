# TTS Engine Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `render.py` renders an episode on the engine its manifest names (`qwen3`, the default, or `breeze`), refuses before any model load whatever that engine cannot do, and keys every cached take on the engine and model that produced it.

**Architecture:** A frozen `EngineSpec` per engine in a module-level `ENGINES` table inside `render.py` (single-file rule; the eval bench in slice 2 imports `render` the way `bloopers.py` does). A closed-whitelist `tts_engine` manifest key mirrors `ship_mode`. `validate_manifest` gates voice modes and take length on the spec's capabilities; `_render_take` dispatches per engine; the cache key, pre-flight, run log and bloopers index learn the engine name. Qwen3's code path is byte-for-byte what it is today.

**Tech Stack:** Python 3.10+, mlx-audio ≥ 0.5.1 (Apple Silicon), pytest, ruff 0.14.10.

**Spec:** `docs/superpowers/specs/2026-09-04-tts-engine-registry-design.md`

**Follow-ups filed (not this plan):** bench skill https://github.com/schmug/clodcast/issues/200 · script features https://github.com/schmug/clodcast/issues/201 · chunking / verify-and-retry https://github.com/schmug/clodcast/issues/202 · Surface Tension flip https://github.com/schmug/clodcast/issues/203

## Global Constraints

- `render.py` stays single-file. No sibling module is imported by it.
- `tts_engine` lives on the manifest only: no CLI flag, no `config.json` default. Whitelist `("qwen3", "breeze")`; absent → `qwen3`; any other value dies.
- Breeze: `max_take_chars = 500`, `max_tokens = 750`, `min_mlx_audio = "0.5.1"`, `cfg_scale = 4.0` for design, license string `BreezeBlue Research and Non-Commercial`. Qwen3: no ceiling, no `max_tokens`, `min_mlx_audio = "0.4.3"`, license `Apache 2.0`.
- The universal rule "`voice_instruct` + `lines` cast dies" stays universal on both engines.
- Cache key includes `engine` and `model_id` unconditionally.
- `RUN_LOG_FIELDS` / `BLOOPER_FIELDS`: new field appended LAST, null on every path that does not set it. Never reorder.
- The `SHIPPED` line contract parsed by schedulers (`orchestrate.py`) is untouched; only the JSON payloads and SKILL.md's prose report line gain the engine.
- `events` and `direction` are declared on the Breeze spec and read by nothing.
- No show switches engine in this slice: every assembler (`orchestrate.py`, `fc_*`, `st_write.py`) keeps emitting no `tts_engine`.
- Tests never load a real model and never touch `~/.config/daily-podcast` (conftest enforces the latter).
- Every commit uses a conventional prefix and ends with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Line numbers below are as of commit `3130f79`; re-grep after each task.

---

## File structure

| File | Responsibility in this plan |
|---|---|
| `pyproject.toml`, `requirements.txt`, `README.md` | mlx-audio floor bumped to ≥ 0.5.1 (Task 0) |
| `skills/daily-podcast/render.py` | `EngineSpec`/`ENGINES`/`resolve_tts_engine` (Task 1); capability + ceiling validation (Task 2); cache key (Task 3); per-engine dispatch and model selection (Task 4); pre-flight check (Task 5); run log / bloopers / JSON payloads (Task 6) |
| `tests/test_tts_engines.py` (new) | Every test this plan adds except the two one-line extensions to existing tests |
| `tests/test_web_only.py` | one assertion added (Task 6) |
| `skills/daily-podcast/SKILL.md` | manifest schema line + `### TTS engines` table + report line (Task 7) |
| `docs/durable-voices.md`, `CLAUDE.md`, `skills/surface-tension/SKILL.md` | one section / paragraph / bullet each (Task 7) |

---

### Task 0: Raise the mlx-audio floor to 0.5.1 and prove Qwen3 still renders

**Files:**
- Modify: `pyproject.toml:13` (`"mlx-audio",` inside `dependencies`)
- Modify: `requirements.txt` (the `mlx-audio` line)
- Modify: `README.md:81`

**Interfaces:**
- Produces: a host whose `importlib.metadata.version("mlx-audio")` is `0.5.1`, which Task 5's check and Task 8's Breeze dry-run rely on.

Breeze support merged into mlx-audio on 2026-08-26 (PR 911) and shipped in 0.5.1 on 2026-08-31. Qwen3's `Model.generate`, `Model.generate_voice_design` and `mlx_audio.tts.utils.load_model` signatures are identical in 0.4.3 and 0.5.1 (checked 2026-09-04), so this is expected to be a no-op for the three shows; the dry-runs below are the evidence, not the assumption.

- [ ] **Step 1: Pin the floor in the three files**

`pyproject.toml` line 13: `"mlx-audio",` → `"mlx-audio>=0.5.1",`
`requirements.txt`: `mlx-audio` → `mlx-audio>=0.5.1`
`README.md` line 81: `(`mlx-audio`, ` → `(`mlx-audio>=0.5.1`, `

- [ ] **Step 2: Upgrade the global install (this is production state; the spec approved it)**

Run:
```bash
python3 -m pip install --user --upgrade "mlx-audio>=0.5.1" && python3 -c "import importlib.metadata as m; print(m.version('mlx-audio'), m.version('mlx'))" && python3 -m pip check
```
Expected: `0.5.1 0.32.2` (or later) and `No broken requirements found.` If `pip check` names `mlx-whisper`, upgrade it too (`python3 -m pip install --user --upgrade mlx-whisper`) and re-run.

- [ ] **Step 3: Write four rehearsal manifests to the scratchpad**

`$SCRATCH` is this session's scratchpad directory. `$REPO` is the worktree root.

```bash
mkdir -p $SCRATCH/step0 && cd $SCRATCH/step0
REFS=$REPO/skills/surface-tension/refs
cat > daily.json <<'EOF'
{"title": "Rehearsal, two stories - September 4, 2026", "summary": "Step-0 rehearsal.", "date": "2026-09-04",
 "voice": "house",
 "segments": [{"text": "Good morning. Two short stories today, and both are about the same thing: what a tool costs once you stop counting the price."},
              {"text": "First, a company said its new model is faster. It is, on the benchmark they picked. The benchmark they picked is the one it was built to win, and that is the whole story.", "source_url": "https://example.com/a"}]}
EOF
cat > fc.json <<'EOF'
{"title": "Rehearsal - Week of August 31, 2026", "summary": "Step-0 rehearsal, web mode.", "date": "2026-09-04",
 "voice": "house", "ship_mode": "web", "slug_prefix": "frontier-commits", "r2_key_prefix": "frontier-commits/", "r2_manifest_name": "manifest-frontier-commits.json", "show_name": "Frontier Commits",
 "segments": [{"text": "This week the labs shipped quietly. One repository gained a directory nobody has explained, and I am going to explain it anyway."},
              {"text": "The directory is called evals. It has three files. Two of them are empty, which tells you it was created on a Friday.", "source_url": "https://github.com/example/repo"}]}
EOF
python3 - "$REFS" <<'EOF'
import json, sys
from pathlib import Path
refs = Path(sys.argv[1])
cast = {v: {"ref_audio": str(refs / f"{v.lower()}.wav"), "ref_text": (refs / f"{v.lower()}.txt").read_text().strip()} for v in ("Ryan", "Aiden", "Ethan", "Chelsie")}
m = {"title": "Rehearsal - Week of August 31, 2026", "summary": "Step-0 rehearsal, lines cast.", "date": "2026-09-04",
     "voice": "Ryan", "ship_mode": "web", "show_name": "Surface Tension", "slug_prefix": "surface-tension", "r2_key_prefix": "surface-tension/", "r2_manifest_name": "manifest-surface-tension.json",
     "cover_image": str(refs / "cover.jpg"), "cast": cast,
     "segments": [{"title": "Cold open", "source_url": None, "lines": [{"speaker": "Ryan", "text": "Next one. Go."}, {"speaker": "Aiden", "text": "A post about keeping software small. He says every feature after the second one is a mistake."}, {"speaker": "Ethan", "text": "Every feature is a strong phrase. Does he define small?"}, {"speaker": "Chelsie", "text": "Moving on before Ethan defines it for us."}]},
                  {"title": "Sign-off", "source_url": None, "lines": [{"speaker": "Chelsie", "text": "That is the show. Argue with us next week."}, {"speaker": "Ryan", "text": "Or do not. Either way we will be here."}]}]}
Path("st.json").write_text(json.dumps(m, indent=1))
EOF
cat > design.json <<'EOF'
{"title": "Rehearsal, design mode - September 4, 2026", "summary": "Step-0 rehearsal, VoiceDesign.", "date": "2026-09-04",
 "voice": "custom", "voice_instruct": "A calm man in his forties, even tone, no broadcast inflection.",
 "segments": [{"text": "This is a design-mode rehearsal. It exists so the second model gets loaded once under the new library."},
              {"text": "If you can hear this sentence in a voice that was described rather than recorded, the design path still works.", "source_url": "https://example.com/b"}]}
EOF
ls
```
Expected: `daily.json fc.json st.json design.json`.

- [ ] **Step 4: Dry-run all four on Qwen3 with explicit workdirs**

Run (each takes 1–3 minutes of TTS; run them one after another, not in parallel, so the GPU is not shared):
```bash
cd $REPO && for m in daily fc st design; do python3 skills/daily-podcast/render.py --manifest $SCRATCH/step0/$m.json --workdir $SCRATCH/step0/wd-$m --dry-run 2>&1 | tail -25; done
```
Expected: four JSON blocks with `"status": "dry-run"`, no `error:` lines, and `preflight: PASS` in each log. Then:
```bash
tail -4 ~/.config/daily-podcast/runs.jsonl | python3 -c "import sys,json; [print(r['status'], r['voice_mode'], r['segment_count'], r['title'][:30]) for r in map(json.loads, sys.stdin)]"
```
Expected: four lines, `dry-run clone 2`, `dry-run clone 2`, `dry-run clone 2`, `dry-run design 2`. Save this output; it goes in the PR body.

- [ ] **Step 5: Lint and commit**

Run: `ruff check . && ruff format --check .`
Expected: `All checks passed!` and no files would be reformatted.

```bash
git add pyproject.toml requirements.txt README.md
git commit -m "chore(deps): require mlx-audio >= 0.5.1

Breeze-TTS-2 support landed in mlx-audio 0.5.1 (2026-08-31). Qwen3's generate,
generate_voice_design and load_model signatures are unchanged from 0.4.3; four
--dry-run rehearsals (daily/spotify, frontier-commits/web, surface-tension
lines cast, voice_instruct design mode) rendered clean on the upgraded host.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 1: `EngineSpec`, the `ENGINES` table, and the `tts_engine` manifest key

**Files:**
- Modify: `skills/daily-podcast/render.py:6-19` (imports), `:61-68` (constants), `:1046-1053` (validate_manifest's ship_mode check — the engine check goes just before it)
- Create: `tests/test_tts_engines.py`

**Interfaces:**
- Produces: `render.EngineSpec` (frozen dataclass with `.has(cap)`), `render.ENGINES: dict[str, EngineSpec]`, `render.TTS_ENGINES`, `render.TTS_ENGINE_QWEN3`, `render.TTS_ENGINE_BREEZE`, `render.ENGINE_CAPABILITIES`, `render.resolve_tts_engine(manifest) -> str`, `render.engine_spec(manifest) -> EngineSpec`. `render.MODEL_ID`, `render.VOICE_DESIGN_MODEL_ID`, `render.VOICES` keep their names and values.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tts_engines.py`:

```python
"""The TTS engine registry (spec docs/superpowers/specs/2026-09-04-tts-engine-registry-design.md).

Engine choice is a manifest property with a closed whitelist, the ship_mode posture;
each engine declares what it can do and render.py refuses the rest before any model
load. Qwen3's path is byte-identical to the single-engine renderer it replaces."""

import pytest
import render


def _manifest(**over):
    m = {"title": "T", "summary": "S", "segments": [{"text": "hello there"}]}
    m.update(over)
    return m


def test_tts_engine_defaults_to_qwen3_when_the_key_is_absent():
    assert render.resolve_tts_engine(_manifest()) == "qwen3"
    assert render.engine_spec(_manifest()) is render.ENGINES["qwen3"]


@pytest.mark.parametrize("name", ["qwen3", "breeze"])
def test_tts_engine_accepts_both_documented_engines(name):
    render.validate_manifest(_manifest(tts_engine=name))
    assert render.resolve_tts_engine(_manifest(tts_engine=name)) == name


@pytest.mark.parametrize("bad", ["Breeze", "qwen", "", "mlx-community/Breeze-TTS-2-mlx-8bit"])
def test_validate_manifest_rejects_an_unknown_engine(bad):
    with pytest.raises(SystemExit):
        render.validate_manifest(_manifest(tts_engine=bad))


def test_the_engine_table_is_internally_consistent():
    assert tuple(render.ENGINES) == render.TTS_ENGINES == ("qwen3", "breeze")
    for name, spec in render.ENGINES.items():
        assert spec.name == name
        assert spec.capabilities <= render.ENGINE_CAPABILITIES
        assert bool(spec.presets) == spec.has("preset")
        assert spec.has("clone") and spec.has("design")
    breeze = render.ENGINES["breeze"]
    assert breeze.design_model_id is None
    assert breeze.max_take_chars == 500 and breeze.max_tokens == 750
    assert breeze.min_mlx_audio == "0.5.1"
    assert breeze.has("events") and breeze.has("direction")


def test_the_daily_shows_constants_alias_the_qwen3_entry():
    q = render.ENGINES["qwen3"]
    assert render.MODEL_ID == q.base_model_id == "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"
    assert render.VOICE_DESIGN_MODEL_ID == q.design_model_id
    assert render.VOICES == list(q.presets) == ["Ryan", "Aiden", "Ethan", "Chelsie"]
    assert q.max_take_chars is None and q.max_tokens is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_tts_engines.py -v`
Expected: FAIL with `AttributeError: module 'render' has no attribute 'resolve_tts_engine'` (and `ENGINES`).

- [ ] **Step 3: Add the imports**

In `render.py`, after `import argparse` (line 7) add `from dataclasses import dataclass` — keep the import block sorted: it goes between `import datetime as dt` and `import hashlib` as a `from` import? No: ruff's isort puts `from dataclasses import dataclass` after all `import x` lines and before `from pathlib import Path`. Place it directly above `from pathlib import Path` (line 55). Also add `import importlib.metadata` directly under `import importlib.util` (line 11); Task 5 uses it.

- [ ] **Step 4: Replace the constants with the registry**

Replace lines 61–68 of `render.py` (from `VOICES = [...]` through `VOICE_DESIGN_MODEL_ID = ...`, keeping the `CAST_CLIP_FIELDS` comment and line in place) with:

```python
# The two halves of a recorded cast clip (#177) — a manifest `cast` value is either
# a preset name from VOICES or exactly these two keys. Both are needed: the clip is
# what the model imitates, the transcript is what it believes the clip says, and a
# clone rendered against the wrong transcript drifts audibly.
CAST_CLIP_FIELDS = ("ref_audio", "ref_text")

# --- TTS engines ------------------------------------------------------------
#
# Which model renders an episode is a property of the SHOW, chosen by the manifest's
# `tts_engine` key — a closed whitelist, default "qwen3" — the ship_mode posture for
# the ship_mode reason: a re-run must render the way it rendered before, and a flag
# that can go missing on one invocation would silently render a different voice.
# Every engine declares what it can do, and validate_manifest refuses a voice mode
# the chosen engine lacks BEFORE the model load; the alternative — passing a Qwen3
# preset name through as a Breeze speaker tag — renders a stranger with no error,
# the silent wrong-voice class #177 closed. Design: docs/superpowers/specs/
# 2026-09-04-tts-engine-registry-design.md.
TTS_ENGINE_QWEN3 = "qwen3"
TTS_ENGINE_BREEZE = "breeze"
TTS_ENGINES = (TTS_ENGINE_QWEN3, TTS_ENGINE_BREEZE)
ENGINE_CAPABILITIES = frozenset({"preset", "clone", "design", "events", "direction"})


@dataclass(frozen=True)
class EngineSpec:
    """One TTS engine: the models it loads, what it can do, the limits the renderer
    enforces on its behalf, and what an operator must know before shipping on it.

    `events` and `direction` are DECLARED, not consumed: nothing in render.py reads
    them yet. They exist for the eval bench and for capability-gated script
    features (spec slices 2 and 3)."""

    name: str
    label: str
    base_model_id: str
    design_model_id: str | None  # None: voice design runs on the base model
    capabilities: frozenset[str]
    presets: tuple[str, ...]
    max_take_chars: int | None  # None: no observed ceiling
    max_tokens: int | None  # None: never passed to generate()
    min_mlx_audio: str
    license: str

    def has(self, capability: str) -> bool:
        return capability in self.capabilities


ENGINES: dict[str, EngineSpec] = {
    TTS_ENGINE_QWEN3: EngineSpec(
        name=TTS_ENGINE_QWEN3,
        label="Qwen3-TTS 1.7B",
        base_model_id="mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
        design_model_id="mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-bf16",
        capabilities=frozenset({"preset", "clone", "design"}),
        presets=("Ryan", "Aiden", "Ethan", "Chelsie"),
        max_take_chars=None,
        max_tokens=None,
        min_mlx_audio="0.4.3",
        license="Apache 2.0",
    ),
    TTS_ENGINE_BREEZE: EngineSpec(
        name=TTS_ENGINE_BREEZE,
        label="Breeze-TTS-2 3B",
        base_model_id="mlx-community/Breeze-TTS-2-mlx-8bit",
        design_model_id=None,
        capabilities=frozenset({"clone", "design", "events", "direction"}),
        presets=(),
        # Measured 2026-09-04 (see the spec): 0 of 24 takes <= 533 chars derailed;
        # 1 in 5 did at 592, 839 and 1000 chars. The derailment is the model's own
        # past ~35 s of audio, not the token cap, and the speech-rate gate cannot see
        # it — the rate stays normal and whisper transcribes babble as words.
        max_take_chars=500,
        # Explicit rather than the library default: 500 chars is ~440 frames at
        # 12.5/s, and the cap bounds a derailed take at 60 s instead of letting it run.
        max_tokens=750,
        min_mlx_audio="0.5.1",
        license="BreezeBlue Research and Non-Commercial",
    ),
}

# The daily show's constants are aliases into the registry so nothing else moves.
VOICES = list(ENGINES[TTS_ENGINE_QWEN3].presets)
MODEL_ID = ENGINES[TTS_ENGINE_QWEN3].base_model_id
VOICE_DESIGN_MODEL_ID = ENGINES[TTS_ENGINE_QWEN3].design_model_id


def resolve_tts_engine(manifest: dict[str, Any]) -> str:
    """The engine an episode renders on: the manifest's `tts_engine`, or "qwen3"
    when absent. A falsy value is "absent" here and a whitelist miss in
    validate_manifest, exactly like resolve_ship_mode / SHIP_MODES."""
    return manifest.get("tts_engine") or TTS_ENGINE_QWEN3


def engine_spec(manifest: dict[str, Any]) -> EngineSpec:
    return ENGINES[resolve_tts_engine(manifest)]
```

The old `VOICES = [...]` line and the comment above `CAST_CLIP_FIELDS` must not be duplicated: the block above *includes* the `CAST_CLIP_FIELDS` comment and line, so delete the originals (lines 61–68) entirely. `SAMPLE_RATE = 24000` (line 69) stays where it is.

- [ ] **Step 5: Add the whitelist check to `validate_manifest`**

In `validate_manifest`, directly above the `# Ship mode is a closed set (#155)` comment (line 1046), insert:

```python
    # Engine is a closed set for the same reason as ship_mode: a typo must die rather
    # than fall back to Qwen3 and quietly render a show on the wrong model. Checked
    # here, after the voice checks, so a bad engine name is named before its
    # capabilities are consulted (Task 2 hangs the capability checks off it).
    engine = manifest.get("tts_engine")
    if engine is not None and engine not in TTS_ENGINES:
        shown = "{" + ", ".join(f'"{e}"' for e in TTS_ENGINES) + "}"
        die(f"manifest 'tts_engine' must be one of {shown} or unset (got {engine!r})")
```

- [ ] **Step 6: Run the tests**

Run: `pytest tests/test_tts_engines.py -v`
Expected: 9 passed (defaults 1 + accepts 2 + rejects 4 + consistent 1 + aliases 1).

Then the full suite, because `VOICES` and `MODEL_ID` moved: `pytest -q`
Expected: `1225 passed` — the baseline on this branch at `3130f79` (confirm with `pytest -q | tail -1` before starting).

- [ ] **Step 7: Commit**

```bash
git add skills/daily-podcast/render.py tests/test_tts_engines.py
git commit -m "feat(render): tts_engine manifest key and the engine registry

EngineSpec + ENGINES inside render.py, closed whitelist (qwen3 default, breeze),
resolve_tts_engine mirrors resolve_ship_mode. MODEL_ID / VOICE_DESIGN_MODEL_ID /
VOICES are aliases into the qwen3 entry, so nothing downstream moves yet.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Capability gating and the take ceiling in `validate_manifest`

**Files:**
- Modify: `skills/daily-podcast/render.py` — two new helpers directly above `def validate_manifest` (line 883), two calls inside it after the `for field in ("voice_instruct", "show_id")` loop (line 963–965)
- Test: `tests/test_tts_engines.py`

**Interfaces:**
- Consumes: `EngineSpec.has`, `engine_spec`, `VOICES`, `segment_lines(seg) -> list | None` (render.py:780).
- Produces: `render._validate_engine_capabilities(manifest, spec)`, `render._validate_take_lengths(segments, spec)`; both `die()` with messages containing `has no presets` / `renders at most N per take`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tts_engines.py`:

```python
# --- capabilities gate the voice modes; the ceiling gates the text (spec §3) ------


@pytest.mark.parametrize("voice", ["random", "Ryan", "Chelsie"])
def test_breeze_refuses_a_preset_episode_voice(voice, capsys):
    with pytest.raises(SystemExit):
        render.validate_manifest(_manifest(tts_engine="breeze", voice=voice))
    assert "breeze has no presets" in capsys.readouterr().err


def test_breeze_refuses_a_preset_cast_entry(capsys):
    m = _manifest(
        tts_engine="breeze",
        cast={"anchor": "Ryan"},
        segments=[{"lines": [{"speaker": "anchor", "text": "hi"}]}],
    )
    with pytest.raises(SystemExit):
        render.validate_manifest(m)
    assert "cast['anchor']" in capsys.readouterr().err


def test_breeze_accepts_clones_and_a_designed_voice():
    clip = {"ref_audio": "/tmp/ryan.wav", "ref_text": "a transcript"}
    render.validate_manifest(_manifest(tts_engine="breeze"))  # voice defaults to "house"
    render.validate_manifest(
        _manifest(tts_engine="breeze", cast={"a": clip}, segments=[{"lines": [{"speaker": "a", "text": "hi"}]}])
    )
    render.validate_manifest(_manifest(tts_engine="breeze", voice="custom", voice_instruct="a calm man"))


@pytest.mark.parametrize("engine", ["qwen3", "breeze"])
def test_voice_instruct_with_a_cast_still_dies_on_every_engine(engine):
    clip = {"ref_audio": "/tmp/ryan.wav", "ref_text": "a transcript"}
    m = _manifest(
        tts_engine=engine,
        voice_instruct="a calm man",
        cast={"a": clip},
        segments=[{"lines": [{"speaker": "a", "text": "hi"}]}],
    )
    with pytest.raises(SystemExit):
        render.validate_manifest(m)


def test_qwen3_still_accepts_random_and_presets():
    for voice in ("random", "Ryan", "house"):
        render.validate_manifest(_manifest(voice=voice))


def test_breeze_refuses_a_segment_over_its_take_ceiling(capsys):
    with pytest.raises(SystemExit):
        render.validate_manifest(_manifest(tts_engine="breeze", segments=[{"text": "x" * 501}]))
    assert "renders at most 500 per take" in capsys.readouterr().err
    render.validate_manifest(_manifest(tts_engine="breeze", segments=[{"text": "x" * 500}]))


def test_breeze_refuses_a_scene_line_over_its_take_ceiling(capsys):
    clip = {"ref_audio": "/tmp/ryan.wav", "ref_text": "a transcript"}
    m = _manifest(
        tts_engine="breeze",
        cast={"a": clip},
        segments=[{"lines": [{"speaker": "a", "text": "ok"}, {"speaker": "a", "text": "y" * 501}]}],
    )
    with pytest.raises(SystemExit):
        render.validate_manifest(m)
    assert "segment[0] line 1 is 501 chars" in capsys.readouterr().err


def test_qwen3_has_no_take_ceiling():
    render.validate_manifest(_manifest(segments=[{"text": "x" * 3000}]))
```

`die()` logs `error: <msg>` through `log()`, which prints to `sys.stderr` (render.py `def log`), hence `capsys.readouterr().err`. `_prep_segment_text` runs `normalize_for_tts`, which leaves `hello there`, `one`, `two` and `x`-runs unchanged (checked), so the dispatch tests in Task 4 can assert on the literal text.

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_tts_engines.py -v -k "refuses or accepts or ceiling or still"`
Expected: the `breeze_refuses_*` tests FAIL with `DID NOT RAISE SystemExit`; the accept tests may already pass.

- [ ] **Step 3: Add the two helpers above `validate_manifest`**

Insert directly above `def validate_manifest(` (line 883):

```python
def _validate_engine_capabilities(manifest: dict[str, Any], spec: EngineSpec) -> None:
    """Refuse, BEFORE the model load, any voice mode the chosen engine lacks, naming
    the engine and the capability. Breeze has no presets: without this, `voice:
    "Ryan"` would pass validation (Ryan is a Qwen3 preset), reach the Breeze adapter
    as a speaker tag, and render a stranger with no error anywhere. Pure."""
    no_presets = f"engine {spec.name} has no presets"
    instruct = manifest.get("voice_instruct")
    voice = manifest.get("voice", "house")
    if instruct:
        if not spec.has("design"):
            die(f"manifest sets 'voice_instruct', but engine {spec.name} cannot design a voice")
    elif voice == "house":
        if not spec.has("clone"):
            die(f'manifest voice "house" is a clip clone, but engine {spec.name} cannot clone')
    elif voice == "random" or voice in VOICES:
        if not spec.has("preset"):
            die(f"manifest voice {voice!r} is a preset, but {no_presets}")
    for speaker, cast_voice in (manifest.get("cast") or {}).items():
        if isinstance(cast_voice, dict):
            if not spec.has("clone"):
                die(f"manifest cast[{speaker!r}] is a clip, but engine {spec.name} cannot clone")
        elif not spec.has("preset"):
            die(f"manifest cast[{speaker!r}] = {cast_voice!r} is a preset, but {no_presets}")


def _validate_take_lengths(segments: list[dict], spec: EngineSpec) -> None:
    """Refuse any take longer than the engine's measured ceiling BEFORE the render.
    This is not a cosmetic limit: past it Breeze derails into babble about one take
    in five, and the speech-rate gate cannot see that (the rate stays normal and
    whisper transcribes babble as words). Pure."""
    cap = spec.max_take_chars
    if cap is None:
        return
    for i, seg in enumerate(segments):
        lines = segment_lines(seg)
        if lines is None:
            n = len(seg["text"])
            if n > cap:
                die(f"manifest segment[{i}] is {n} chars; engine {spec.name} renders at most {cap} per take")
            continue
        for j, line in enumerate(lines):
            n = len(line.get("text") or "") if isinstance(line, dict) else 0
            if n > cap:
                die(
                    f"manifest segment[{i}] line {j} is {n} chars; "
                    f"engine {spec.name} renders at most {cap} per take"
                )
```

- [ ] **Step 4: Call them from `validate_manifest`**

The engine whitelist check from Task 1 sits above the ship_mode check. Move it so it runs right after the `title`/`summary` loop (line ~896, before `cast = manifest.get("cast")`), and bind the spec there:

```python
    engine = manifest.get("tts_engine")
    if engine is not None and engine not in TTS_ENGINES:
        shown = "{" + ", ".join(f'"{e}"' for e in TTS_ENGINES) + "}"
        die(f"manifest 'tts_engine' must be one of {shown} or unset (got {engine!r})")
    spec = engine_spec(manifest)
```

(Remove the copy above the ship_mode check so there is exactly one.) Then, directly after the loop
```python
    for field in ("voice_instruct", "show_id"):
        if manifest.get(field) is not None and not isinstance(manifest[field], str):
            die(f"manifest '{field}' must be a string")
```
add:
```python
    _validate_engine_capabilities(manifest, spec)
    _validate_take_lengths(segments, spec)
```
Both run after the segments loop has already proved every plain segment has a string `text` and every scene's lines are well-formed, so the helpers can index without re-checking.

- [ ] **Step 5: Run the tests, then the full suite**

Run: `pytest tests/test_tts_engines.py -v`
Expected: all pass (9 from Task 1 + 11 here = 20).
Run: `pytest -q`
Expected: no regressions.

- [ ] **Step 6: Commit**

```bash
git add skills/daily-podcast/render.py tests/test_tts_engines.py
git commit -m "feat(render): refuse voice modes and take lengths the engine cannot render

Capabilities gate the episode voice, voice_instruct and cast entries per engine;
Breeze's 500-char take ceiling is enforced before the model load because the
speech-rate gate cannot see a derailed take.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Engine and model id in the per-take cache key

**Files:**
- Modify: `skills/daily-podcast/render.py:1168-1195` (`_segment_cache_key`) and its two call sites inside `render_segments` (lines ~1370 and ~1400)
- Test: `tests/test_tts_engines.py`

**Interfaces:**
- Produces: `render._segment_cache_key(text, voice_mode, voice, ref_fingerprint, ref_text, *, engine: str, model_id: str) -> str`.
- Consumes (Task 4 supplies these at the call sites): `engine` (the name) and `model_id` (the id `render_segments` will actually load).

- [ ] **Step 1: Write the failing test**

Append:

```python
# --- cache key (spec §5) -------------------------------------------------------


def test_cache_key_changes_with_the_engine_and_the_model_id():
    base = dict(text="t", voice_mode="clone", voice="house", ref_fingerprint="abc", ref_text="r")
    k = render._segment_cache_key(**base, engine="qwen3", model_id="m1")
    assert k == render._segment_cache_key(**base, engine="qwen3", model_id="m1")
    assert k != render._segment_cache_key(**base, engine="breeze", model_id="m1")
    assert k != render._segment_cache_key(**base, engine="qwen3", model_id="m2")
    # Still sensitive to everything it was sensitive to before.
    assert k != render._segment_cache_key(**{**base, "text": "u"}, engine="qwen3", model_id="m1")
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_tts_engines.py -v -k cache_key`
Expected: FAIL with `TypeError: _segment_cache_key() got an unexpected keyword argument 'engine'`.

- [ ] **Step 3: Extend the key**

Replace `_segment_cache_key` (lines 1168–1195) with:

```python
def _segment_cache_key(
    text: str,
    voice_mode: str,
    voice: str,
    ref_fingerprint: str | None,
    ref_text: str | None,
    *,
    engine: str,
    model_id: str,
) -> str:
    """Content hash identifying one rendered segment. Any input that changes the
    audio the model would produce changes the key:
      - `text`        : the (already prepped/normalized) spoken text
      - `voice_mode`  : clone / design / preset — the engine actually used
      - `voice`       : preset name, or the VoiceDesign instruct in design mode
      - `ref_fingerprint` : hash of the ref-audio bytes (clone mode only)
      - `ref_text`    : the clone transcript (clone mode only)
      - `engine`, `model_id` : which model rendered it. Unconditional: a key that
        omits the model is the silent-replay class #177 closed, one level up — a
        workdir rendered under Qwen3 and re-run under Breeze would replay Qwen3's
        audio under the new engine's name with no error. Every sidecar written
        before this field existed misses once; auto workdirs are per-date and
        deleted on success, so that is at most one same-day resume.
    Serialized through json so field boundaries can't collide (e.g. "a"+"bc" vs
    "ab"+"c"). Pure; no I/O."""
    payload = json.dumps(
        {
            "text": text,
            "mode": voice_mode,
            "voice": voice,
            "ref_fingerprint": ref_fingerprint,
            "ref_text": ref_text,
            "engine": engine,
            "model_id": model_id,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Update the two call sites in `render_segments` provisionally**

Both calls (`_segment_cache_key(text, mode, key_voice or "", ref_fingerprint, ref_text)` and the per-line `_segment_cache_key(text, spec["mode"], spec["voice"], spec["ref_fingerprint"], spec["ref_text"])`) gain `engine=TTS_ENGINE_QWEN3, model_id=VOICE_DESIGN_MODEL_ID if use_design else MODEL_ID`. Task 4 replaces these with the resolved engine; this keeps the suite green in between.

Then check no test calls the old positional form: `grep -rn "_segment_cache_key(" tests/` — for any hit, add `engine="qwen3", model_id=render.MODEL_ID`.

- [ ] **Step 5: Run the tests and the suite**

Run: `pytest tests/test_tts_engines.py -v -k cache_key && pytest -q`
Expected: pass; no regressions. `tests/test_cast_clips.py::test_a_preset_cast_members_key_is_unchanged_by_the_clone_path` asserts the preset key is stable *within* a run, not against a literal, so it stays green — if it compares against a pinned hex string, update the string and say so in the commit body.

- [ ] **Step 6: Commit**

```bash
git add skills/daily-podcast/render.py tests/test_tts_engines.py
git commit -m "feat(render): fold the engine and model id into the take cache key

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Per-engine dispatch and model selection

**Files:**
- Modify: `skills/daily-podcast/render.py:1228-1297` (`_render_take`), `:1299-1484` (`render_segments`), `:4840-4866` (`_render`'s voice resolution and `render_segments` call)
- Test: `tests/test_tts_engines.py`

**Interfaces:**
- Produces: `render.BREEZE_CFG_SCALE = 4.0`; `render._generate_qwen3(model, spec, *, text, mode, voice, voice_instruct, ref_audio, ref_text) -> list`; `render._generate_breeze(...)` same signature; `render._render_take(model, *, spec: EngineSpec, text, mode, voice, voice_instruct, ref_audio, ref_text, mp3) -> float`; `render.render_segments(..., engine: str = TTS_ENGINE_QWEN3)`.
- Consumes: Task 3's key signature; `ENGINES`.

- [ ] **Step 1: Write the failing tests**

Append (the fixture is a copy of `tests/test_lines.py`'s `fake_tts`, recording the full kwargs and the method name because this task is *about* the kwargs):

```python
# --- dispatch (spec §4) --------------------------------------------------------

import sys
import types
from pathlib import Path


class _FakeAudioResult:
    def __init__(self, n: int = 4):
        self.audio = [0.0] * n


@pytest.fixture
def fake_tts(monkeypatch):
    calls: list[dict] = []
    model_loads: list[str] = []

    class FakeModel:
        def generate(self, text, **kw):
            calls.append({"method": "generate", "text": text, **kw})
            return [_FakeAudioResult()]

        def generate_voice_design(self, text, **kw):
            calls.append({"method": "generate_voice_design", "text": text, **kw})
            return [_FakeAudioResult()]

    fake_np = types.ModuleType("numpy")
    fake_np.concatenate = lambda arrs: [x for a in arrs for x in a]
    fake_np.array = lambda x: list(x)
    monkeypatch.setitem(sys.modules, "numpy", fake_np)
    fake_sf = types.ModuleType("soundfile")
    fake_sf.write = lambda path, audio, sr: Path(path).write_bytes(b"\x00")
    monkeypatch.setitem(sys.modules, "soundfile", fake_sf)
    mlx_audio = types.ModuleType("mlx_audio")
    mlx_tts = types.ModuleType("mlx_audio.tts")
    mlx_utils = types.ModuleType("mlx_audio.tts.utils")

    def _load_model(model_id):
        model_loads.append(model_id)
        return FakeModel()

    mlx_utils.load_model = _load_model
    monkeypatch.setitem(sys.modules, "mlx_audio", mlx_audio)
    monkeypatch.setitem(sys.modules, "mlx_audio.tts", mlx_tts)
    monkeypatch.setitem(sys.modules, "mlx_audio.tts.utils", mlx_utils)

    def fake_run(cmd, **kw):
        Path(cmd[-1]).write_bytes(b"\x00")  # ffmpeg writes its output last
        return None

    monkeypatch.setattr(render, "run", fake_run)
    return types.SimpleNamespace(calls=calls, model_loads=model_loads)


def _clip(tmp_path):
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFF")
    return str(ref)


def test_qwen3_clone_kwargs_are_byte_identical_to_the_single_engine_renderer(tmp_path, fake_tts):
    ref = _clip(tmp_path)
    render.render_segments([{"text": "hello there"}], "house", tmp_path, ref_audio=ref, ref_text="hi", engine="qwen3")
    assert fake_tts.model_loads == [render.MODEL_ID]
    assert fake_tts.calls == [
        {"method": "generate", "text": "hello there", "language": "English", "ref_audio": ref, "ref_text": "hi"}
    ]


def test_qwen3_preset_kwargs_are_unchanged(tmp_path, fake_tts):
    render.render_segments([{"text": "hello there"}], "Ryan", tmp_path, engine="qwen3")
    assert fake_tts.calls == [{"method": "generate", "text": "hello there", "voice": "Ryan", "language": "English"}]


def test_qwen3_design_still_loads_the_second_model(tmp_path, fake_tts):
    render.render_segments([{"text": "hello there"}], "custom", tmp_path, voice_instruct="a calm man", engine="qwen3")
    assert fake_tts.model_loads == [render.VOICE_DESIGN_MODEL_ID]
    assert fake_tts.calls == [
        {"method": "generate_voice_design", "text": "hello there", "language": "English", "instruct": "a calm man"}
    ]


def test_breeze_clone_passes_the_cap_and_no_language(tmp_path, fake_tts):
    ref = _clip(tmp_path)
    render.render_segments([{"text": "hello there"}], "house", tmp_path, ref_audio=ref, ref_text="hi", engine="breeze")
    assert fake_tts.model_loads == [render.ENGINES["breeze"].base_model_id]
    assert fake_tts.calls == [
        {"method": "generate", "text": "hello there", "ref_audio": ref, "ref_text": "hi", "max_tokens": 750}
    ]


def test_breeze_design_runs_on_the_base_model(tmp_path, fake_tts):
    render.render_segments([{"text": "hello there"}], "custom", tmp_path, voice_instruct="a calm man", engine="breeze")
    assert fake_tts.model_loads == [render.ENGINES["breeze"].base_model_id]
    assert fake_tts.calls == [
        {"method": "generate", "text": "hello there", "instruct": "a calm man", "cfg_scale": 4.0, "max_tokens": 750}
    ]


def test_breeze_cast_clip_lines_render_through_the_clone_form(tmp_path, fake_tts):
    ref = _clip(tmp_path)
    seg = {"lines": [{"speaker": "a", "text": "one"}, {"speaker": "a", "text": "two"}]}
    render.render_segments([seg], "Ryan", tmp_path, cast={"a": {"ref_audio": ref, "ref_text": "hi"}}, engine="breeze")
    assert fake_tts.model_loads == [render.ENGINES["breeze"].base_model_id]
    assert [c["text"] for c in fake_tts.calls] == ["one", "two"]
    assert all(c["max_tokens"] == 750 and "language" not in c for c in fake_tts.calls)


def test_a_breeze_preset_take_dies_rather_than_rendering_a_stranger(tmp_path):
    with pytest.raises(SystemExit):
        render._render_take(
            object(), spec=render.ENGINES["breeze"], text="x", mode="preset", voice="Ryan",
            voice_instruct=None, ref_audio=None, ref_text=None, mp3=tmp_path / "a.mp3",
        )


def test_the_take_key_records_the_engine_that_rendered_it(tmp_path, fake_tts):
    import json as _json
    ref = _clip(tmp_path)
    render.render_segments([{"text": "hello there"}], "house", tmp_path, ref_audio=ref, ref_text="hi", engine="qwen3")
    k_qwen = _json.loads((tmp_path / "seg_01.json").read_text())["key"]
    render.render_segments([{"text": "hello there"}], "house", tmp_path, ref_audio=ref, ref_text="hi", engine="breeze")
    k_breeze = _json.loads((tmp_path / "seg_01.json").read_text())["key"]
    assert k_qwen != k_breeze
    assert len(fake_tts.calls) == 2  # the second engine did not reuse the first's take
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_tts_engines.py -v -k "qwen3_clone or qwen3_preset or qwen3_design or breeze_clone or breeze_design or cast_clip or stranger or records_the_engine"`
Expected: FAIL — `render_segments() got an unexpected keyword argument 'engine'` and `_render_take() got an unexpected keyword argument 'spec'`.

- [ ] **Step 3: Split `_render_take` into per-engine generators**

Directly above `def _render_take(` (line 1228) add:

```python
# Voice design on Breeze is classifier-free guidance over the instruction; 4.0 is
# the publisher's documented default and the value the 2026-09-04 eval used.
BREEZE_CFG_SCALE = 4.0


def _generate_qwen3(
    model: Any,
    spec: EngineSpec,
    *,
    text: str,
    mode: str,
    voice: str,
    voice_instruct: str | None,
    ref_audio: str | None,
    ref_text: str | None,
) -> list[Any]:
    """The single-engine renderer's three branches, kwargs byte-for-byte."""
    if mode == "clone":
        return list(model.generate(text=text, language="English", ref_audio=ref_audio, ref_text=ref_text))
    if mode == "design":
        return list(model.generate_voice_design(text=text, language="English", instruct=voice_instruct))
    return list(model.generate(text=text, voice=voice, language="English"))


def _generate_breeze(
    model: Any,
    spec: EngineSpec,
    *,
    text: str,
    mode: str,
    voice: str,
    voice_instruct: str | None,
    ref_audio: str | None,
    ref_text: str | None,
) -> list[Any]:
    """Breeze clones and designs on ONE model through generate(); the cap is passed
    explicitly (spec §2). No preset branch: validate_manifest refuses presets on
    this engine, and a direct caller that reaches here dies rather than handing a
    Qwen3 preset name to Breeze as a speaker tag."""
    if mode == "clone":
        return list(
            model.generate(text=text, ref_audio=ref_audio, ref_text=ref_text, max_tokens=spec.max_tokens)
        )
    if mode == "design":
        return list(
            model.generate(
                text=text, instruct=voice_instruct, cfg_scale=BREEZE_CFG_SCALE, max_tokens=spec.max_tokens
            )
        )
    die(f"engine {spec.name} has no presets; refusing to render mode {mode!r} (voice {voice!r})")
    return []  # unreachable; die() exits


_ENGINE_GENERATORS = {
    TTS_ENGINE_QWEN3: _generate_qwen3,
    TTS_ENGINE_BREEZE: _generate_breeze,
}
```

Then change `_render_take`'s signature to `def _render_take(model: Any, *, spec: EngineSpec, text: str, mode: str, voice: str, voice_instruct: str | None, ref_audio: str | None, ref_text: str | None, mp3: Path) -> float:` and replace its `if mode == "clone": ... else: ...` block (everything from `if mode == "clone":` through the preset `results = list(model.generate(...))`) with:

```python
    results = _ENGINE_GENERATORS[spec.name](
        model,
        spec,
        text=text,
        mode=mode,
        voice=voice,
        voice_instruct=voice_instruct,
        ref_audio=ref_audio,
        ref_text=ref_text,
    )
```
Everything after it (`audio = np.concatenate(...)`, the wav write, the ffmpeg mono-44.1k re-assertion, the return) is unchanged.

- [ ] **Step 4: Thread the engine through `render_segments`**

Add `engine: str = TTS_ENGINE_QWEN3,` as the last keyword parameter of `render_segments` (after `cast`). At the top of the body, right after `mode = "clone" if use_clone else (...)` (line ~1338), add:

```python
    spec = ENGINES[engine]
    # Which weights this run loads. Only an engine with a SEPARATE design model
    # switches for voice_instruct; Breeze designs on its base model and so always
    # pays one load (spec §4). Resolved here, before the plans, because the id is
    # part of every take's key.
    model_id = spec.design_model_id if (use_design and spec.design_model_id) else spec.base_model_id
```
Replace Task 3's provisional `engine=TTS_ENGINE_QWEN3, model_id=...` at both `_segment_cache_key` calls with `engine=engine, model_id=model_id`. In the load block replace
```python
        model_id = VOICE_DESIGN_MODEL_ID if use_design else MODEL_ID
        log(f"loading {model_id}...")
```
with
```python
        log(f"loading {model_id} ({engine})...")
```
and add `spec=spec,` as the first keyword to the `_render_take(` call in the render loop (line ~1473).

- [ ] **Step 5: Resolve the engine in `_render` and pass it down**

In `_render`, directly after `validate_manifest(manifest)` (line ~4758) add:
```python
    engine = resolve_tts_engine(manifest)
```
After `log(f"workdir: {workdir}")` (line ~4846) add `log(f"tts_engine: {engine} ({ENGINES[engine].label})")`. Add `engine=engine,` to the `render_segments(` call (line ~4857, after `cast=manifest.get("cast"),`).

- [ ] **Step 6: Run the tests and the suite**

Run: `pytest tests/test_tts_engines.py -v && pytest -q`
Expected: all pass. `tests/test_lines.py`, `tests/test_cast_clips.py` and `tests/test_render.py`'s fake-model tests assert on `calls[...]["voice"]`/`["ref_audio"]` and on `model_loads`; none should move because the qwen3 kwargs are unchanged. If one asserts `model_loads == [render.MODEL_ID]` in design mode, it was already asserting the VoiceDesign id — read it before touching it.

- [ ] **Step 7: Commit**

```bash
git add skills/daily-podcast/render.py tests/test_tts_engines.py
git commit -m "feat(render): dispatch takes per engine; Breeze designs on its base model

_render_take routes through _ENGINE_GENERATORS; Qwen3's three branches are
byte-identical. render_segments picks the design model only when the engine has
one, and keys every take on the engine + model id it loads.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Pre-flight `tts-engine` check

**Files:**
- Modify: `skills/daily-podcast/render.py:4348-4420` (`preflight` signature and check list), new helpers directly below `_tts_module_check` (line ~4445), `_render`'s `preflight(` call (line ~4829)
- Test: `tests/test_tts_engines.py`

**Interfaces:**
- Produces: `render._installed_mlx_audio_version() -> str | None` (the test seam), `render._version_tuple(str) -> tuple[int, ...]`, `render._tts_engine_check(spec) -> dict` (a `_check(...)` record named `tts-engine`), `render.preflight(..., engine: str = TTS_ENGINE_QWEN3)`.

CI runs on Ubuntu with no mlx-audio installed and stubs `_tts_module_check`; so **absence** of the package is that check's finding, and this one passes with a note when the distribution is missing. It fails only when the installed version is below the engine's floor.

- [ ] **Step 1: Write the failing tests**

Append:

```python
# --- pre-flight (spec §6) ------------------------------------------------------


def test_engine_check_fails_when_mlx_audio_is_too_old(monkeypatch):
    monkeypatch.setattr(render, "_installed_mlx_audio_version", lambda: "0.4.9")
    c = render._tts_engine_check(render.ENGINES["breeze"])
    assert c["name"] == "tts-engine" and c["ok"] is False
    assert "needs mlx-audio >= 0.5.1" in c["detail"] and "0.4.9" in c["detail"]


@pytest.mark.parametrize("installed", ["0.5.1", "0.5.10", "1.0.0"])
def test_engine_check_passes_at_or_above_the_floor_and_names_the_license(monkeypatch, installed):
    monkeypatch.setattr(render, "_installed_mlx_audio_version", lambda: installed)
    c = render._tts_engine_check(render.ENGINES["breeze"])
    assert c["ok"] is True
    assert "BreezeBlue Research and Non-Commercial" in c["detail"]
    assert "Breeze-TTS-2 3B" in c["detail"]


def test_engine_check_leaves_an_absent_package_to_the_module_check(monkeypatch):
    monkeypatch.setattr(render, "_installed_mlx_audio_version", lambda: None)
    c = render._tts_engine_check(render.ENGINES["qwen3"])
    assert c["ok"] is True and "tts-module" in c["detail"]


def test_version_tuple_compares_dotted_strings_numerically():
    assert render._version_tuple("0.5.10") > render._version_tuple("0.5.9")
    assert render._version_tuple("0.4.3") == (0, 4, 3)
    assert render._version_tuple("1.2.3.dev4") == (1, 2, 3)


def test_preflight_runs_the_engine_check_under_dry_run(monkeypatch):
    monkeypatch.setattr(render.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(render, "_tts_module_check", lambda: render._check("tts-module", True, "stub"))
    monkeypatch.setattr(render, "check_r2_credentials", lambda cfg, required=False: {"ok": True, "detail": "stub"})
    monkeypatch.setattr(render, "_installed_mlx_audio_version", lambda: "0.4.3")
    ok, checks = render.preflight({}, show_id="spotify:show:x", dry_run=True, engine="breeze")
    names = [c["name"] for c in checks]
    assert "tts-engine" in names and names.index("tts-engine") == names.index("tts-module") + 1
    assert ok is False
    assert next(c for c in checks if c["name"] == "tts-engine")["ok"] is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_tts_engines.py -v -k "engine_check or version_tuple or preflight_runs"`
Expected: FAIL with `AttributeError: ... has no attribute '_installed_mlx_audio_version'`.

- [ ] **Step 3: Add the helpers below `_tts_module_check`**

```python
def _installed_mlx_audio_version() -> str | None:
    """The installed mlx-audio distribution version, None when absent. Metadata
    only — no import of mlx_audio, same posture as _tts_module_check. A seam so
    tests never depend on what the host has installed."""
    try:
        return importlib.metadata.version("mlx-audio")
    except importlib.metadata.PackageNotFoundError:
        return None


def _version_tuple(version: str) -> tuple[int, ...]:
    """"0.5.10" -> (0, 5, 10). Numeric, so 0.5.10 > 0.5.9; a dev/rc suffix is
    ignored. Deliberately not `packaging` — one more runtime dependency for one
    comparison."""
    return tuple(int(p) for p in re.findall(r"\d+", version)[:3])


def _tts_engine_check(spec: EngineSpec) -> dict[str, Any]:
    """Is the installed mlx-audio new enough for this engine, and does the operator
    know what they are shipping on? The PASS line carries the label and the
    LICENSE, because pre-flight is exactly the moment someone is deciding. Absence
    of the package is tts-module's finding, not this check's: CI has no mlx-audio
    and stubs that check, and on a real host tts-module already fails the run."""
    installed = _installed_mlx_audio_version()
    who = f"{spec.name} ({spec.label}; {spec.license})"
    if installed is None:
        return _check("tts-engine", True, f"{who}; mlx-audio not installed — see tts-module")
    if _version_tuple(installed) >= _version_tuple(spec.min_mlx_audio):
        return _check("tts-engine", True, f"{who} on mlx-audio {installed}")
    return _check(
        "tts-engine",
        False,
        f"{spec.name} needs mlx-audio >= {spec.min_mlx_audio}; installed {installed} "
        f"(python3 -m pip install --user --upgrade 'mlx-audio>={spec.min_mlx_audio}')",
    )
```

- [ ] **Step 4: Wire it into `preflight` and `_render`**

Add `engine: str = TTS_ENGINE_QWEN3,` to `preflight`'s keyword parameters (after `cover_image`). Directly after `checks.append(_tts_module_check())` add `checks.append(_tts_engine_check(ENGINES[engine]))`. In `_render`'s `preflight(` call add `engine=engine,` (the variable Task 4 bound after `validate_manifest`; move that binding above the pre-flight block if it is not already — it must be resolved before pre-flight).

- [ ] **Step 5: Run the tests and the suite**

Run: `pytest tests/test_tts_engines.py -v && pytest -q`
Expected: all pass. `tests/test_reliability.py::test_preflight_records_checks_into_the_run_record` may assert an exact check count — if it does, the count grows by one; update the literal and say so in the commit body.

- [ ] **Step 6: Commit**

```bash
git add skills/daily-podcast/render.py tests/test_tts_engines.py
git commit -m "feat(render): pre-flight tts-engine check (mlx-audio floor + license line)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `tts_engine` in the run log, bloopers index, resume marker and every JSON payload

**Files:**
- Modify: `skills/daily-podcast/render.py:303-321` (`BLOOPER_FIELDS`), `:537-561` (`RUN_LOG_FIELDS`), `:2958-2985` (`_resume` record + print), `:2993-3010` and `:3077` (`_ship_web_only` signature + print), `:4840-4845` (record), `:4908-4916` (`capture_rate_bloopers` ctx), `:4955-4970` (dry-run JSON), `:4980-4995` (`_ship_web_only` call), `:5012-5024` (uploaded marker), `:5084` (ready JSON), and the run-failed sweep's caller (see step 4)
- Test: `tests/test_tts_engines.py`, `tests/test_web_only.py:307-323`

**Interfaces:**
- Produces: `"tts_engine"` as the LAST entry of `RUN_LOG_FIELDS` and of `BLOOPER_FIELDS`; `_ship_web_only(..., tts_engine: str, ...)`; every final JSON (`dry-run`, `ready`, `web-ready`, resumed `ready`) and `uploaded.json` carry `"tts_engine"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tts_engines.py`:

```python
# --- run log, bloopers, payloads (spec §7) -------------------------------------


def test_tts_engine_is_appended_last_to_both_field_sets():
    assert render.RUN_LOG_FIELDS[-1] == "tts_engine"
    assert render.RUN_LOG_FIELDS[-2] == "bloopers_captured"  # nothing reordered
    assert render.BLOOPER_FIELDS[-1] == "tts_engine"
    assert render.BLOOPER_FIELDS[-2] == "workdir"
    assert render._new_run_record()["tts_engine"] is None
```

And in `tests/test_web_only.py`, inside `test_web_only_final_json_carries_the_mp3_url_for_the_shipped_line` after `assert out["title"] == WEB_MANIFEST["title"]` (line ~322) add:

```python
    assert out["tts_engine"] == "qwen3"  # absent key -> default engine, reported truthfully
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_tts_engines.py -k appended_last -v && pytest tests/test_web_only.py -k final_json -v`
Expected: both FAIL (`AssertionError` on `RUN_LOG_FIELDS[-1]`; `KeyError: 'tts_engine'`).

- [ ] **Step 3: Append the fields**

At the end of `RUN_LOG_FIELDS` (after `"bloopers_captured",  # ...` line 560) add:
```python
    "tts_engine",  # engine name from the manifest (spec 2026-09-04); null before it is resolved
```
At the end of `BLOOPER_FIELDS` (after `"workdir",` line 320) add:
```python
    "tts_engine",  # which engine produced the clip; null for rows written before it existed
```

- [ ] **Step 4: Set it on every payload**

1. `_render`, right after `record["voice_mode"] = voice_mode` (line ~4844): `record["tts_engine"] = engine`.
2. Dry-run JSON (line ~4964): after `"voice_mode": voice_mode,` add `"tts_engine": engine,`.
3. `_ship_web_only`: add `tts_engine: str,` after `voice_mode: str,` in the signature; in its `print(json.dumps({...}))` add `"tts_engine": tts_engine,` after `"voice_mode": voice_mode,` (line ~3077). At the call in `_render` (line ~4990) add `tts_engine=engine,` after `voice_mode=voice_mode,`.
4. Uploaded marker (line ~5020): add `"tts_engine": engine,` after `"voice_mode": voice_mode,`.
5. Ready JSON (line ~5084): add `"tts_engine": engine,` after `"voice_mode": voice_mode,`.
6. `_resume` (lines ~2966 and ~2981): add `tts_engine=data.get("tts_engine"),` to `record.update(...)` and `"tts_engine": data.get("tts_engine"),` to the printed dict — a marker written before this field existed resumes with null, never a guess.
7. `capture_rate_bloopers(` call in `_render` (line ~4908–4916): add `tts_engine=engine,` beside `title=manifest.get("title"),`. `bank_blooper` already filters `**fields` through `BLOOPER_FIELDS`, so the row picks it up.
8. The run-failed sweep: `grep -n "bank_blooper(" skills/daily-podcast/render.py` shows a second call (line ~4015) inside the sweep function; find that function's caller in `main()`'s failure branch (`grep -n "run-failed" skills/daily-podcast/render.py`) and add `tts_engine=record.get("tts_engine"),` beside the `title=` it already passes. The record is the run record `main()` owns, so this is null when the run failed before the engine was resolved — correct, not a guess.

Verify nothing was missed: `grep -n '"voice_mode"' skills/daily-podcast/render.py` — every hit except the `RUN_LOG_FIELDS` entry must have a `"tts_engine"` line beside it.

- [ ] **Step 5: Run the tests and the suite**

Run: `pytest tests/test_tts_engines.py tests/test_web_only.py -v && pytest -q`
Expected: all pass. `tests/test_bloopers.py` asserts every index row carries the full `BLOOPER_FIELDS` key set — it should keep passing because rows are built from the tuple; if a test pins a literal row, extend it.

- [ ] **Step 6: Commit**

```bash
git add skills/daily-podcast/render.py tests/test_tts_engines.py tests/test_web_only.py
git commit -m "feat(render): record tts_engine in the run log, bloopers index and payloads

Appended last on both field sets, null on every path that never resolves an
engine (failed-before-manifest, pre-existing resume markers).

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Docs, the SKILL.md engines table, and its drift test

**Files:**
- Modify: `skills/daily-podcast/SKILL.md:60` (manifest schema), `:352-354` (new `### TTS engines` subsection between the end of *Voice selection* and `### Multi-voice scenes`), `:736` (report line)
- Modify: `docs/durable-voices.md` (end of *Maintaining multiple voices*, after the `"cast": {...}` block that ends ~line 178)
- Modify: `CLAUDE.md:95` (new `###` before *The reliability layer*)
- Modify: `skills/surface-tension/SKILL.md:183` (one bullet after the `ship_mode` bullet)
- Test: `tests/test_tts_engines.py`

**Interfaces:**
- Consumes: `render.ENGINES` (insertion order qwen3, breeze), `render.SCRIPT_DIR`.

- [ ] **Step 1: Write the failing drift test**

Append:

```python
# --- docs drift (spec §9) ------------------------------------------------------


def test_skill_md_engine_table_matches_the_code():
    """SKILL.md is the production path (a claude -p follows it), so the engines
    table is pinned to ENGINES the way the shape table is pinned to SHAPE_ORDERS."""
    lines = (render.SCRIPT_DIR / "SKILL.md").read_text().splitlines()
    header = next((i for i, ln in enumerate(lines) if ln.startswith("| engine | model |")), None)
    assert header is not None, "SKILL.md lost the TTS engines table"
    body = lines[header + 2 : header + 2 + len(render.ENGINES)]
    for (name, spec), line in zip(render.ENGINES.items(), body, strict=True):
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        assert cells[0] == name
        assert cells[1] == spec.base_model_id
        assert cells[2] == ", ".join(sorted(spec.capabilities))
        assert cells[3] == ("none" if spec.max_take_chars is None else f"{spec.max_take_chars} chars")
        assert cells[4] == spec.min_mlx_audio
        assert cells[5] == spec.license


def test_skill_md_manifest_schema_documents_tts_engine():
    text = (render.SCRIPT_DIR / "SKILL.md").read_text()
    assert '"tts_engine": "qwen3"' in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_tts_engines.py -k skill_md -v`
Expected: FAIL `SKILL.md lost the TTS engines table`.

- [ ] **Step 3: SKILL.md (daily)**

After the `"ship_mode": "spotify", ...` line (60) in the manifest schema add:
```
  "tts_engine": "qwen3",                   // optional; "qwen3" (default) or "breeze". Closed whitelist that lives on the manifest like ship_mode — see "TTS engines"
```

After the paragraph `Report the voice in the final summary so the user knows which one ran.` (line 352) and before `### Multi-voice scenes` (354) insert:

```markdown
### TTS engines

The engine is a property of the show, chosen by the manifest's `tts_engine` key (default `qwen3`); a typo dies rather than falling back, the `ship_mode` posture. The four voice modes above are unchanged by the engine — it is an orthogonal axis, not a fifth mode — but an engine only renders the modes it declares, and `render.py` refuses the rest before the model load. Pre-flight prints the engine and its license on every run. This table is pinned to `render.ENGINES` by a test.

| engine | model | capabilities | take ceiling | min mlx-audio | license |
| --- | --- | --- | --- | --- | --- |
| `qwen3` | `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit` | clone, design, preset | none | 0.4.3 | Apache 2.0 |
| `breeze` | `mlx-community/Breeze-TTS-2-mlx-8bit` | clone, design, direction, events | 500 chars | 0.5.1 | BreezeBlue Research and Non-Commercial |

- `qwen3` designs on a second model (`VoiceDesign-bf16`); `breeze` designs on the same model it clones with, so a Breeze episode always pays one load.
- `breeze` has no presets: `voice: "random"`, a preset name, or a preset cast entry dies naming the engine. Clones (`house`, cast clips) and `voice_instruct` work.
- **The 500-character ceiling is Breeze's own, not the token cap.** Measured 2026-09-04: 0 of 24 takes at or under 533 characters derailed; 1 in 5 did at 592–1000. A plain-text segment or a scene line over it dies before the render, because the speech-rate gate cannot see a derailment (the rate stays normal and whisper hears babble as words). Every band this show writes exceeds it, so the daily show cannot select `breeze` as-is; Surface Tension's lines can.
- `events` and `direction` are declared for the eval bench and future script features; nothing in `render.py` reads them yet. Paralinguistic markers still do not work on `qwen3`.
- Breeze's weights are non-commercial with no creator or monetization exception: no sponsor reads or paid tiers on a show that renders with it.
```

Line 736's report line `SHIPPED <episode_uri> - <title> - <chapter_count> chapters - <duration_s>s - r2=ok` becomes `SHIPPED <episode_uri> - <title> - <chapter_count> chapters - <duration_s>s - r2=ok - engine=<tts_engine>` (trailing, so any reader of the prefix is unaffected; `orchestrate.py`'s parsed contract is untouched).

- [ ] **Step 4: durable-voices.md**

After the `"cast": {...}` code block that closes *Maintaining multiple voices* add:

```markdown
### The engine is a separate axis

`tts_engine` on the manifest (default `qwen3`) picks the model; it does not add a fifth voice mode. Every mode above means the same thing on every engine that declares it, and `render.py` refuses a mode the engine lacks before loading anything (the daily skill's *TTS engines* table lists who declares what). `breeze` (Breeze-TTS-2) has no presets and clones and designs on one model — so its VoiceDesign still drifts run to run, and the locked-clip rule in this document applies to it unchanged.
```

- [ ] **Step 5: CLAUDE.md**

Directly above `### The reliability layer (pre-flight, artifact gate, durable state, incidents)` (line 95) insert:

```markdown
### One renderer, two engines (`tts_engine`)

`render.py` renders on the engine its manifest names — `"qwen3"` (the default when absent) or `"breeze"` — through a frozen `EngineSpec` per engine in `ENGINES` (design: [docs/superpowers/specs/2026-09-04-tts-engine-registry-design.md](docs/superpowers/specs/2026-09-04-tts-engine-registry-design.md)). Five things are load-bearing.

- **The engine lives on the MANIFEST, closed whitelist, same posture as `ship_mode`.** A re-run must render the way it rendered before, and a flag that can go missing on one invocation would silently render a different voice. No CLI flag, no `config.json` default; a typo dies.
- **Capabilities gate validation, before the model load.** `_validate_engine_capabilities` refuses `voice: "random"`, a preset name, or a preset cast entry on an engine without `preset`, and `voice_instruct` on one without `design`. The alternative on Breeze is passing a Qwen3 preset name through as a speaker tag and rendering a stranger with no error — the silent wrong-voice class #177 closed. `events` / `direction` are declared on the Breeze spec and read by nothing yet.
- **The take ceiling is enforced before the render because the gate cannot see a derailment.** Breeze derails about one take in five past ~35 s of audio regardless of the token cap (measured 2026-09-04, in the spec); `_validate_take_lengths` refuses anything over `max_take_chars` (500 for Breeze, none for Qwen3), and `max_tokens` is passed explicitly to bound a derailed take. The speech-rate gate stays normal on a derailed take and whisper transcribes babble as words, so nothing downstream would catch it. Every daily-show band exceeds Breeze's ceiling by design until chunked rendering or verify-and-retry exists.
- **The engine and the loaded model id are in every take's cache key, unconditionally.** A key without them replays Qwen3's banked audio under Breeze's name on a re-run — #177 one level up. Every sidecar written before this field existed misses once.
- **Qwen3's path is byte-identical.** `_generate_qwen3` carries the three original branches with their original kwargs; `MODEL_ID` / `VOICE_DESIGN_MODEL_ID` / `VOICES` are aliases into the qwen3 entry. Only an engine with a separate `design_model_id` switches models for `voice_instruct`; Breeze designs on its base model and always pays one load. The universal "`voice_instruct` + `lines` cast dies" rule stays universal until per-line direction lands.

Pre-flight's `tts-engine` check fails when the installed mlx-audio is below the engine's floor and prints the engine's license on every run; an absent package is `tts-module`'s finding. `tts_engine` is appended LAST to `RUN_LOG_FIELDS` and `BLOOPER_FIELDS`, null on paths that never resolve one. No show sets the key yet; switching one is a one-line assembler change made deliberately.
```

- [ ] **Step 6: Surface Tension SKILL.md and README**

After the `"ship_mode": "web"` bullet (line 183) add:
```markdown
- `"tts_engine"` is deliberately absent, so the show renders on `qwen3`. Moving it to another engine is this one key (the daily skill's *TTS engines* table lists them). Breeze's 500-character take ceiling fits every line this show writes; its weights are non-commercial, so a show on it carries no sponsor reads.
```
README line 81 was updated in Task 0; confirm it reads `mlx-audio>=0.5.1`.

- [ ] **Step 7: Run the drift tests, the existing doc tests, then the suite**

Run: `pytest tests/test_tts_engines.py tests/test_st_skill_md.py tests/test_fc_skill_md.py tests/test_orchestrate.py -q && pytest -q`
Expected: all pass. `tests/test_orchestrate.py::test_skill_md_documents_every_shape_and_mode` and the ST manifest-pin test read SKILL.md by anchor, not by line number, so the insertions must not disturb them; if one fails, its anchor moved — fix the anchor text, not the test.

- [ ] **Step 8: Commit**

```bash
git add skills/daily-podcast/SKILL.md docs/durable-voices.md CLAUDE.md skills/surface-tension/SKILL.md tests/test_tts_engines.py
git commit -m "docs: tts_engine on the manifest, the engines table, and its drift test

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Verification, a Breeze dry-run, and the PR

**Files:** none modified (evidence only).

- [ ] **Step 1: Lint**

Run: `ruff check . && ruff format --check .`
Expected: `All checks passed!`; nothing to reformat. If `ruff format --check` lists files, run `ruff format .` and commit as `style: ruff format`.

- [ ] **Step 2: Full suite with counts**

Run: `pytest -q 2>&1 | tail -3`
Expected: `1264 passed` (the 1225 baseline + 39 new tests: 9 + 11 + 1 + 8 + 7 + 1 + 2), `0 failed`. Record the exact line.

- [ ] **Step 3: Render the Surface Tension rehearsal on Breeze, then on Qwen3, same workdir shape**

```bash
cd $SCRATCH/step0 && python3 - <<'EOF'
import json
m = json.load(open("st.json")); m["tts_engine"] = "breeze"; json.dump(m, open("st-breeze.json", "w"), indent=1)
EOF
cd $REPO && python3 skills/daily-podcast/render.py --manifest $SCRATCH/step0/st-breeze.json --workdir $SCRATCH/step0/wd-st-breeze --dry-run 2>&1 | tail -30
```
Expected: `[PASS] tts-engine: breeze (Breeze-TTS-2 3B; BreezeBlue Research and Non-Commercial) on mlx-audio 0.5.1`, `loading mlx-community/Breeze-TTS-2-mlx-8bit (breeze)...`, six line takes rendered at roughly 0.7–1.1× realtime, a `"status": "dry-run"` JSON with `"tts_engine": "breeze"`, and the artifact gate passing.

Then prove the refusal path on the daily manifest (the heredoc is quoted, so `cd` first and use relative names):
```bash
cd $SCRATCH/step0 && python3 - <<'EOF'
import json
m = json.load(open("daily.json")); m["tts_engine"] = "breeze"; m["segments"][1]["text"] = m["segments"][1]["text"] * 4
json.dump(m, open("daily-breeze.json", "w"), indent=1)
EOF
cd $REPO && python3 skills/daily-podcast/render.py --manifest $SCRATCH/step0/daily-breeze.json --workdir $SCRATCH/step0/wd-daily-breeze --dry-run 2>&1 | tail -3
```
Expected: `error: manifest segment[1] is 6xx chars; engine breeze renders at most 500 per take` and exit 1, before any `loading ...` line.

Then the run log:
```bash
tail -2 ~/.config/daily-podcast/runs.jsonl | python3 -c "import sys,json; [print(r['status'], r['tts_engine'], r['error_message']) for r in map(json.loads, sys.stdin)]"
```
Expected: `dry-run breeze None` then `failed breeze manifest segment[1] is ...`.

- [ ] **Step 4: Open the PR**

```bash
git push -u origin claude/breeze-tts-2-eval-9bc908
gh pr create --title "feat(render): tts_engine registry with Breeze-TTS-2 as engine two" --body "$(cat <<'EOF'
## Summary

- `tts_engine` manifest key (closed whitelist, default `qwen3`) and an `EngineSpec` registry inside `render.py`; Breeze-TTS-2 registered as engine two. No show switches engine.
- Per-engine validation before the model load (Breeze has no presets; 500-char take ceiling because Breeze derails ~1 in 5 past 35 s and the rate gate cannot see it), per-engine dispatch (Qwen3 byte-identical), engine + model id in the take cache key, a pre-flight version/license check, `tts_engine` in the run log / bloopers / payloads.
- mlx-audio floor raised to 0.5.1 (Breeze support shipped 2026-08-31).

Spec: `docs/superpowers/specs/2026-09-04-tts-engine-registry-design.md`. Plan: `docs/superpowers/plans/2026-09-04-tts-engine-registry.md`.

## Evidence

<paste: ruff output, the `pytest -q` tail line, the four step-0 run-log lines, the Breeze dry-run tail and its run-log line, the refusal line>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Merge through the gate**

`main` has a server-side ruleset with required CI checks (ruleset 20893395; no admin bypass). Once every check is green:
```bash
gh pr checks --watch && gh pr merge --squash --delete-branch
```
If a required check is red, fix it on the branch; never bypass. Do not enable auto-merge before every fix is pushed.
