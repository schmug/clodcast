"""Drift tests tying skills/surface-tension/SKILL.md to the code it documents.

SKILL.md is the PRODUCTION path: the scheduled weekly run is a Claude invocation
following its "Unattended weekly run" section, so a role, bit or manifest key
that exists only in st_script_plan / st_write never reaches a real episode, and a
table transcription slip ships a rotation the property tests never verified.
Same discipline as test_fc_skill_md.py, whose helpers this mirrors.

Tables are CELL-parsed and compared for EQUALITY, never membership: a phantom row
someone added by hand has to go red just as loudly as a deleted one (#141).
"""

import re
from pathlib import Path

import render
import st_gather
import st_script_plan as sp
import st_write

ST_DIR = Path(__file__).resolve().parent.parent / "skills" / "surface-tension"


def _skill_text() -> str:
    return (ST_DIR / "SKILL.md").read_text()


def _section(heading: str) -> str:
    text = _skill_text()
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start : end if end != -1 else len(text)]


def _first_table_after(marker: str) -> list[list[str]]:
    lines = _skill_text().splitlines()
    start = next((i for i, ln in enumerate(lines) if marker in ln), None)
    assert start is not None, f"SKILL.md lost the {marker!r} section"
    i = start + 1
    while i < len(lines) and not lines[i].lstrip().startswith("|"):
        i += 1
    assert i < len(lines), f"SKILL.md has no table after {marker!r}"
    rows = []
    while i < len(lines) and lines[i].lstrip().startswith("|"):
        cells = [c.strip().strip("`").strip() for c in lines[i].strip().strip("|").split("|")]
        rows.append(cells)
        i += 1
    return rows[2:]


def test_skill_md_role_table_matches_the_code():
    rows = _first_table_after("### The roles")
    assert [r[0] for r in rows] == list(sp.ROLES_ST), (
        "the role table drifted from st_script_plan.ROLES_ST (a missing OR phantom row)"
    )
    for row in rows:
        turns = sp.ROLES_ST[row[0]]["turns"]
        assert row[-1] == f"{turns[0]}-{turns[1]}", f"turn budget for {row[0]} drifted"


def test_skill_md_bits_table_matches_the_code():
    rows = _first_table_after("### The recurring bits")
    assert [r[0] for r in rows] == list(sp.BITS_ST)


def test_skill_md_cast_table_matches_the_code():
    rows = _first_table_after("### The cast")
    assert [r[0] for r in rows] == list(sp.VOICES_ST)


def test_skill_md_role_square_matches_the_code():
    rows = _first_table_after("### The panel square")
    names = list(sp.ROLES_ST)
    assert len(rows) == len(sp.ROLE_ORDERS_ST), "the panel square gained or lost a row"
    for row, order in zip(rows, sp.ROLE_ORDERS_ST, strict=True):
        chairs = [sp.VOICES_ST[c] if c < len(sp.VOICES_ST) else "-" for c in order]
        assert row[1:] == chairs, f"panel row {row[0]} drifted from ROLE_ORDERS_ST"
    assert [r[0] for r in rows] == [str(i) for i in range(len(sp.ROLE_ORDERS_ST))]
    # The header must name the roles in bank order, or the rows below mean nothing.
    header = _skill_text().splitlines()
    start = next(i for i, ln in enumerate(header) if "### The panel square" in ln)
    head_row = next(ln for ln in header[start:] if ln.lstrip().startswith("|"))
    cells = [c.strip().strip("`") for c in head_row.strip().strip("|").split("|")]
    assert cells[1:] == names


def test_skill_md_stance_square_matches_the_code():
    rows = _first_table_after("### The stance square")
    assert len(rows) == len(sp.STANCE_ORDERS_ST), "the stance square gained or lost a row"
    for row, order in zip(rows, sp.STANCE_ORDERS_ST, strict=True):
        assert row[1:] == [sp.VOICES_ST[c] for c in order]


def test_skill_md_documents_every_config_key_and_no_phantom_ones():
    setup = _section("## Setup")
    documented = set(re.findall(r'^\s{1,3}"(\w+)"\s*:', setup, re.M))
    expected = set(st_gather.DEFAULT_CONFIG)
    assert not expected - documented, f"Setup omits config keys: {sorted(expected - documented)}"
    assert not documented - expected, (
        f"Setup documents keys st_gather does not read: {sorted(documented - expected)}"
    )


def test_skill_md_manifest_pins_match_the_assembler():
    manifest = _section("## Manifest")
    for key, value in (
        ("ship_mode", "web"),
        ("show_name", st_write.SHOW_NAME),
        ("r2_manifest_name", st_write.R2_MANIFEST_NAME),
        ("r2_key_prefix", st_write.R2_KEY_PREFIX),
        ("slug_prefix", st_write.SLUG_PREFIX),
    ):
        assert f'"{key}": "{value}"' in manifest, (
            f"SKILL.md's manifest example must pin {key}={value!r}; st_write assembles it"
        )
    assert '"description_footer_text"' in manifest


def test_skill_md_manifest_documents_the_shows_own_art():
    # The key SKILL.md said was "not yet" through Phase 2 (#177). Pinned on the path
    # tail rather than the literal, because st_write emits an absolute path resolved
    # off its own file and SKILL.md writes it against `<root>`.
    manifest = _section("## Manifest")
    tail = "skills/surface-tension/refs/cover.jpg"
    assert str(st_write.COVER_IMAGE).endswith(tail)
    assert f'"cover_image": "<root>/{tail}"' in manifest


def test_skill_md_manifest_shows_the_cast_as_recorded_clips():
    # A cast documented as bare preset names would send the next author to write
    # `{"Ryan": "Ryan"}` by hand — which render.py accepts, and which silently ships
    # the episode in four voices nobody chose.
    manifest = _section("## Manifest")
    for field in render.CAST_CLIP_FIELDS:
        assert f'"{field}"' in manifest, f"the documented cast must carry {field}"


def test_skill_md_scene_band_matches_the_code():
    lo, hi = st_write.SCENE_BAND
    assert f"{lo}-{hi}" in _skill_text(), "the documented scene band drifted from SCENE_BAND"


def test_skill_md_states_the_hard_rule_about_callers():
    text = _skill_text()
    assert "no `caller`" in text or "no caller" in text.lower()
    assert "handle" in text.lower(), "SKILL.md must carry the host-never-handle rule"


def test_weekly_run_section_ships_to_the_web_and_never_polls_spotify():
    section = _section("## Unattended weekly run")
    assert "r2=ok" in section
    for stale in ("READY", "save-to-spotify", "spotify:show:"):
        assert stale not in section, f"the weekly run still references {stale!r}"


def test_skill_md_relative_paths_resolve():
    referenced = set(re.findall(r"`\./([^`]+)`", _skill_text()))
    required = {
        "st_gather.py",
        "st_script_plan.py",
        "st_write.py",
        "prompts/weekly.md",
        "prompts/write_scene.md",
    }
    assert not required - referenced, f"SKILL.md's Layout lost: {sorted(required - referenced)}"
    for rel in sorted(referenced):
        assert (ST_DIR / rel).exists(), f"SKILL.md references ./{rel} but it does not exist"


def test_weekly_prompt_stays_a_stub():
    stub = (ST_DIR / "prompts" / "weekly.md").read_text()
    assert "SKILL.md" in stub
    for marker in ("st_gather.py gather", "Assemble", "rapid-fire"):
        assert marker not in stub, f"prompts/weekly.md re-inlined the procedure: {marker!r}"
    assert len(stub.splitlines()) < 40


def test_skill_md_carries_the_unarguable_post_gate():
    # The curation step is where this is cheapest to enforce: a post that must
    # not be argued about should never reach a writer with a stance attached.
    text = _skill_text().lower()
    assert "not every post is arguable" in text
    for cue in ("grief", "illness"):
        assert cue in text, f"the unarguable-post gate must name {cue}"
