"""The incident queue's other half (#207).

`render.py` writes reports into `incidents/new/` and nothing ever took one out, so
the "intake queue" grew monotonically: every failure since the feature shipped,
handled or not, ranked identically in `ls`. `triage.py` is the drain.

The policy under test is **move, never delete**. A report is the evidence behind
every playbook in the repo's `incidents/` directory, so the bloopers-bin rule
applies (an archive that deletes its oldest material defeats its own purpose) — but
a handled report is queue noise, exactly like a finished workdir. Moving satisfies
both, and these tests hold the line on it: nothing here may pass if a resolve ever
deletes or rewrites a report.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import triage

import render

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_report(kind: str, message: str, *, stamp: str = "20260903T014105Z") -> str:
    """Put one report in the queue the way a real run would, minus the clock."""
    queue = render.incident_dir()
    queue.mkdir(parents=True, exist_ok=True)
    stem = f"{stamp}-{kind}"
    (queue / f"{stem}.md").write_text(f"# Incident: {kind}\n\n```\n{message}\n```\n")
    (queue / f"{stem}.json").write_text(
        json.dumps({"kind": kind, "message": message, "timestamp": "2026-09-03T01:41:05+00:00"})
    )
    return stem


# --- where the two halves live ---------------------------------------------


def test_handled_dir_is_a_sibling_of_the_intake_queue():
    assert render.handled_incident_dir() == render.incident_dir().parent / "handled"


def test_handled_dir_follows_the_env_override(monkeypatch, tmp_path):
    """Both halves move together. An archive derived from CONFIG_DIR instead would
    drain a redirected queue — a test's, or an operator's — into real user state."""
    monkeypatch.setenv("DAILY_PODCAST_INCIDENT_DIR", str(tmp_path / "custom"))

    assert render.incident_dir() == tmp_path / "custom"
    assert render.handled_incident_dir() == tmp_path / "handled"


def test_handled_dir_never_resolves_into_real_user_state():
    real = Path.home() / ".config" / "daily-podcast"
    archive = render.handled_incident_dir()

    assert real not in archive.parents and archive != real


# --- the drain -------------------------------------------------------------


def test_resolve_moves_the_report_out_of_the_queue():
    stem = _write_report("manifest-invalid", "manifest is invalid")
    before = (render.incident_dir() / f"{stem}.md").read_text()

    triage.resolve_incident(stem)

    assert not (render.incident_dir() / f"{stem}.md").exists()
    archived = render.handled_incident_dir() / f"{stem}.md"
    assert archived.read_text() == before, "a resolve must move the bytes, not rewrite them"


def test_resolve_takes_the_json_sidecar_with_it():
    stem = _write_report("tts-degeneration", "speech rate 0.41x median")

    triage.resolve_incident(stem)

    assert not list(render.incident_dir().glob(f"{stem}.*"))
    assert (render.handled_incident_dir() / f"{stem}.json").is_file()


def test_resolve_never_rewrites_the_recorded_kind():
    """The report records what the RUN observed. A later signature table can explain
    it, but it cannot retroactively change what happened."""
    stem = _write_report("unclassified", "ValueError: time data '2026-09-03' does not match format")

    triage.resolve_incident(stem)

    sidecar = json.loads((render.handled_incident_dir() / f"{stem}.json").read_text())
    assert sidecar["kind"] == "unclassified"


def test_resolve_accepts_a_stem_a_filename_or_a_path_in_the_queue():
    for i, name_of in enumerate(
        (
            lambda s: s,
            lambda s: f"{s}.md",
            lambda s: f"{s}.json",
            lambda s: str(render.incident_dir() / f"{s}.md"),
        )
    ):
        stem = _write_report("manifest-invalid", "manifest", stamp=f"2026090{i}T000000Z")
        triage.resolve_incident(name_of(stem))
        assert not list(render.incident_dir().glob(f"{stem}.*")), name_of(stem)


def test_resolve_reports_what_it_moved():
    stem = _write_report("unclassified", "ValueError: unconverted data remains: , 2026")

    record = triage.resolve_incident(stem, note="playbook landed in #205")

    assert record["stem"] == stem
    assert record["recorded_kind"] == "unclassified"
    assert record["current_kind"] == "date-format-crash"
    assert record["note"] == "playbook landed in #205"
    assert sorted(record["files"]) == [f"{stem}.json", f"{stem}.md"]


# --- guards ----------------------------------------------------------------


def test_resolve_refuses_a_path_outside_the_queue(tmp_path):
    """prune_workdirs' posture: never act outside your own tree. A path that points
    somewhere else is refused, never silently reinterpreted as a bare name."""
    stray = tmp_path / "elsewhere" / "20260903T014105Z-unclassified.md"
    stray.parent.mkdir(parents=True)
    stray.write_text("not ours")

    with pytest.raises(ValueError, match="not in the incident queue"):
        triage.resolve_incident(str(stray))

    assert stray.exists(), "a refused resolve must leave the file alone"


def test_resolve_refuses_a_symlink(tmp_path):
    stem = "20260903T014105Z-unclassified"
    real = tmp_path / "outside.md"
    real.write_text("outside the queue")
    queue = render.incident_dir()
    queue.mkdir(parents=True, exist_ok=True)
    (queue / f"{stem}.md").symlink_to(real)

    with pytest.raises(ValueError, match="regular file"):
        triage.resolve_incident(stem)

    assert real.exists()


def test_resolve_refuses_an_unknown_report():
    with pytest.raises(FileNotFoundError):
        triage.resolve_incident("20260101T000000Z-nothing-here")


def test_resolve_refuses_to_overwrite_an_archived_report():
    """The archive is evidence. A same-named report already in handled/ is never
    clobbered — and the collision is detected before ANY file moves, so the pair
    cannot end up half-archived."""
    stem = _write_report("manifest-invalid", "manifest")
    triage.resolve_incident(stem)
    _write_report("manifest-invalid", "a different failure that happened to collide")

    with pytest.raises(FileExistsError):
        triage.resolve_incident(stem)

    assert (render.incident_dir() / f"{stem}.md").exists(), "nothing moves on a collision"
    assert (render.incident_dir() / f"{stem}.json").exists()


def test_resolve_refuses_an_empty_name():
    with pytest.raises(ValueError):
        triage.resolve_incident("   ")


# --- the ledger ------------------------------------------------------------


def test_resolve_appends_one_full_key_set_row_per_resolution():
    first = _write_report("manifest-invalid", "manifest", stamp="20260825T114723Z")
    second = _write_report("tts-degeneration", "speech rate", stamp="20260901T111544Z")

    triage.resolve_incident(first, note="known")
    triage.resolve_incident(second)

    ledger = render.handled_incident_dir() / "index.jsonl"
    rows = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    assert len(rows) == 2, "append-only: a second resolve must not clobber the first"
    for row in rows:
        assert set(row) == set(triage.RESOLUTION_FIELDS)
    assert rows[0]["note"] == "known"
    assert rows[1]["note"] is None, "a missing note is null, never absent"


def test_a_ledger_failure_does_not_undo_the_move(monkeypatch):
    """The files in handled/ are the truth; the ledger is observability. Losing it
    must not leave the report in limbo."""
    stem = _write_report("manifest-invalid", "manifest")

    def boom(*_a, **_k):
        raise OSError("read-only fs")

    monkeypatch.setattr(triage, "_append_ledger", boom)
    with pytest.raises(OSError):
        triage.resolve_incident(stem)

    assert (render.handled_incident_dir() / f"{stem}.md").is_file()


# --- listing ---------------------------------------------------------------


def test_list_reclassifies_against_todays_signature_table():
    """The concrete stuck case: the 2026-09-03 report is recorded `unclassified`,
    but its failure mode has since been written up. The queue view has to SAY so, or
    an operator cannot tell a handled report from an untouched one."""
    stem = _write_report(
        "unclassified", "ValueError: time data '2026-09-03' does not match format '%B %d, %Y'"
    )

    (entry,) = triage.queue_entries()

    assert entry["stem"] == stem
    assert entry["recorded_kind"] == "unclassified"
    assert entry["current_kind"] == "date-format-crash"
    assert entry["reclassified"] is True


def test_list_leaves_the_queue_untouched():
    stem = _write_report("unclassified", "ValueError: unconverted data remains: , 2026")
    before = (render.incident_dir() / f"{stem}.json").read_text()

    triage.queue_entries()

    assert (render.incident_dir() / f"{stem}.json").read_text() == before


def test_list_survives_a_report_with_no_sidecar():
    """write_incident writes the markdown first. A crash between the two leaves a
    report the queue still has to be able to show — and to drain."""
    queue = render.incident_dir()
    queue.mkdir(parents=True, exist_ok=True)
    stem = "20260903T014105Z-date-format-crash"
    (queue / f"{stem}.md").write_text("# Incident: date-format-crash\n")

    (entry,) = triage.queue_entries()

    assert entry["recorded_kind"] == "date-format-crash", "the kind is still in the filename"
    assert entry["reclassified"] is False
    triage.resolve_incident(stem)
    assert (render.handled_incident_dir() / f"{stem}.md").is_file()


def test_list_of_an_empty_or_absent_queue_is_empty():
    assert triage.queue_entries() == []


def test_format_queue_names_every_report_and_the_drain():
    _write_report("unclassified", "ValueError: unconverted data remains: , 2026")

    text = triage.format_queue(triage.queue_entries())

    assert "20260903T014105Z-unclassified" in text
    assert "date-format-crash" in text, "a reclassified report must show its new kind"
    assert "resolve" in text, "the queue view is where an operator learns the drain"


# --- CLI -------------------------------------------------------------------


def test_cli_list_prints_the_queue(monkeypatch, capsys):
    _write_report("manifest-invalid", "manifest")
    monkeypatch.setattr(sys, "argv", ["triage.py", "list"])

    assert triage.main() == 0
    assert "20260903T014105Z-manifest-invalid" in capsys.readouterr().out


def test_cli_resolve_drains_several_reports_at_once(monkeypatch):
    first = _write_report("manifest-invalid", "manifest", stamp="20260825T114723Z")
    second = _write_report("tts-degeneration", "speech rate", stamp="20260901T111544Z")
    monkeypatch.setattr(sys, "argv", ["triage.py", "resolve", first, second, "--note", "swept"])

    assert triage.main() == 0
    assert triage.queue_entries() == []


def test_cli_resolve_reports_a_failure_but_still_drains_the_rest(monkeypatch, capsys):
    good = _write_report("manifest-invalid", "manifest")
    monkeypatch.setattr(sys, "argv", ["triage.py", "resolve", "20260101T000000Z-missing", good])

    assert triage.main() == 1
    assert "error:" in capsys.readouterr().err
    assert (render.handled_incident_dir() / f"{good}.md").is_file()


# --- docs ------------------------------------------------------------------


def test_the_documented_workflow_matches_the_code():
    """incidents/README.md and SKILL.md both promise this workflow. A drain the docs
    do not name is a drain nobody runs (the queue's original failure, one level up)."""
    docs = {
        "incidents/README.md": (REPO_ROOT / "incidents" / "README.md").read_text(),
        "SKILL.md": (REPO_ROOT / "skills" / "daily-podcast" / "SKILL.md").read_text(),
        "README.md": (REPO_ROOT / "README.md").read_text(),
        "CLAUDE.md": (REPO_ROOT / "CLAUDE.md").read_text(),
    }
    for name, body in docs.items():
        assert "triage.py" in body, f"{name} never names the drain"
        assert "incidents/handled/" in body, f"{name} never names the archive"
