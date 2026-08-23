"""Drift tests tying skills/frontier-commits/SKILL.md to the code it documents.

SKILL.md is the PRODUCTION path — the scheduled weekly run is a Claude
invocation following its "Unattended weekly run" and "Script template"
sections, so a shape or story type that exists only in fc_script_plan /
fc_stories never reaches a real episode, and a table transcription slip ships
a different rotation than the one the property tests verified. Same
discipline as the daily show's test_skill_md_* suite in test_reliability.py.
"""

from pathlib import Path

import fc_script_plan as sp
import fc_stories

FC_DIR = Path(__file__).resolve().parent.parent / "skills" / "frontier-commits"


def _skill_text():
    return (FC_DIR / "SKILL.md").read_text()


def test_skill_md_shape_table_matches_the_code():
    names = list(sp.STORY_SHAPES_W)
    lines = _skill_text().splitlines()
    header = next((i for i, ln in enumerate(lines) if "| pos 0 |" in ln), None)
    assert header is not None, "SKILL.md lost the per-position shape table"
    body = lines[header + 2 : header + 2 + len(sp.SHAPE_ORDERS_W)]
    for row, (order, line) in enumerate(zip(sp.SHAPE_ORDERS_W, body, strict=True)):
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        assert cells[0] == str(row)
        assert cells[1:] == [names[i] for i in order]


def test_skill_md_documents_every_shape_mode_move_and_story_type():
    skill = _skill_text()
    for name in (*sp.STORY_SHAPES_W, *sp.INTRO_MODES_W, *sp.OUTRO_MODES_W, *sp.MOVES_W):
        assert name in skill, f"SKILL.md never mentions {name!r}"
    for t in fc_stories.TYPE_PRIORITY:
        assert t in skill, f"SKILL.md never mentions story type {t!r}"


def test_weekly_prompt_stays_a_stub():
    stub = (FC_DIR / "prompts" / "weekly.md").read_text()
    assert "SKILL.md" in stub
    for marker in ("fc_stories.py detect", "Assemble", "trend-watch"):
        assert marker not in stub, f"prompts/weekly.md re-inlined the procedure: {marker!r}"
    assert len(stub.splitlines()) < 40


def test_write_story_prompt_declares_the_placeholders():
    text = (FC_DIR / "prompts" / "write_story.md").read_text()
    for ph in (
        "<<TYPE>>",
        "<<TITLE>>",
        "<<URL>>",
        "<<FACTS>>",
        "<<SHAPE>>",
        "<<MIN_CHARS>>",
        "<<MAX_CHARS>>",
    ):
        assert ph in text


def test_skill_md_pins_the_frontier_manifest_name():
    assert "manifest-frontier-commits.json" in _skill_text()
