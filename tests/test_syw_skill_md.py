"""Drift tests tying skills/show-your-work/SKILL.md to the code it documents.

SKILL.md is the PRODUCTION path — the weekly run follows its "Unattended weekly
run" section — so a table that drifts from the code ships behaviour no test
verified. Tables are cell-parsed and compared for EQUALITY (test_st_skill_md.py)."""

import json
import re
from pathlib import Path

import syw_script_plan as sp
import syw_write as w

SYW_DIR = Path(__file__).resolve().parent.parent / "skills" / "show-your-work"


def _text() -> str:
    return (SYW_DIR / "SKILL.md").read_text()


def _table_after(marker: str) -> list[list[str]]:
    lines = _text().splitlines()
    i = next(i for i, ln in enumerate(lines) if marker in ln) + 1
    while not lines[i].lstrip().startswith("|"):
        i += 1
    rows = []
    while i < len(lines) and lines[i].lstrip().startswith("|"):
        rows.append([c.strip().strip("`").strip() for c in lines[i].strip().strip("|").split("|")])
        i += 1
    return rows[2:]


def _section(heading: str) -> str:
    text = _text()
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start : end if end != -1 else len(text)]


def test_arc_table_matches_the_code():
    assert [r[1] for r in _table_after("### The feature arc")] == list(sp.ARC)


def test_beat_table_matches_the_validator():
    assert [r[0] for r in _table_after("### Visual beats")] == list(w.BEAT_FIELDS)


def test_rotation_table_matches_the_banks():
    rows = _table_after("### Assigned variety")
    got = {r[0]: [k.strip().strip("`") for k in r[2].split(",")] for r in rows}
    assert got == {name: list(bank) for name, bank in sp.ROTATION_BANKS.items()}


def test_manifest_block_matches_the_assembler():
    block = re.search(r"```json\n(\{.*?\n\})\n```", _text(), re.S)
    m = json.loads(block.group(1))
    assert (m["ship_mode"], m["show_name"], m["slug_prefix"]) == ("web", w.SHOW_NAME, w.SLUG_PREFIX)
    assert (m["r2_manifest_name"], m["r2_key_prefix"]) == (w.R2_MANIFEST_NAME, w.R2_KEY_PREFIX)
    assert m["cast"] == w.CAST


def test_the_unattended_procedure_names_every_cli_step():
    proc = _section("## Unattended weekly run")
    for step in (
        "syw_gather.py gather", "syw_script_plan.py plan", "syw_write.py fill",
        "syw_write.py accept", "syw_write.py assemble", "render.py", "syw_gather.py commit",
    ):  # fmt: skip
        assert step in proc, f"the procedure lost {step!r}"
    assert "Never pass `--dry-run`" in proc


def _step(n: int) -> str:
    proc = _section("## Unattended weekly run")
    return next(ln for ln in proc.splitlines() if ln.startswith(f"{n}. "))


def test_commit_waits_for_the_render_to_exit_and_rerunning_it_is_the_recovery():
    """M3: a commit run while render.py is still going parses no final JSON and is
    refused; after a successful publish the episode is live, so re-running commit
    (never re-rendering) is the recovery."""
    step = _step(9)
    assert "syw_gather.py commit" in step and "**exited**" in step
    assert "re-run `commit`" in step


def test_the_report_line_matches_the_spec_and_surfaces_gather_errors():
    # M4: spec §4.8's line, plus the count of failed gather adapters.
    assert "`SHIPPED <mp3_url> feature=<url> briefs=<n> gather_errors=<n> r2=ok`" in _step(10)


def test_weekly_prompt_stays_a_stub():
    stub = (SYW_DIR / "prompts" / "weekly.md").read_text()
    assert "Unattended weekly run" in stub
    assert not re.search(r"^\s*\d+\.\s", stub, re.M), "the stub grew numbered procedure steps"
    for cmd in ("syw_gather.py", "syw_write.py", "render.py"):
        assert cmd not in stub


def test_every_procedure_placeholder_is_defined():
    """#236: step 8 used `<root>` without defining it beside D, S and W."""
    proc = _section("## Unattended weekly run")
    lets = next(ln for ln in proc.splitlines() if ln.startswith("Let `D`"))
    for name in ("`D`", "`S`", "`W`", "`<root>`"):
        assert f"{name} =" in lets, f"{name} is not defined"


def test_an_already_published_workdir_goes_to_commit_never_render():
    """#236 item 7: assemble refuses a workdir whose render.log already published;
    the procedure must send that run to commit, not stop it as a plain failure."""
    step = _step(7)
    assert "ASSEMBLE FAILED already published" in step
    assert "step 9" in step and "never re-render" in step
