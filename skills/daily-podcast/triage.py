"""Maintenance CLI for the incident queue (#207).

`render.py` writes a structured report into `~/.config/daily-podcast/incidents/new/`
on any non-clean exit, and until this existed nothing ever took one out. The word
`new/` implied a sibling state that had never been built, so the "intake queue" was
really every failure since the feature shipped — a handled report sat there
byte-identical to an untouched one, and `ls` stopped ranking anything.

This is the other half. A report you have dealt with MOVES to `incidents/handled/`;
it is never deleted and never rewritten (the reasoning for move-not-delete lives
beside `HANDLED_INCIDENT_DIRNAME` in render.py, next to the two precedents that
disagree). Nothing in a run calls this — same posture as `bloopers.py` and
`retitle.py`, and it is why the best-effort contract does NOT apply here: this is a
human at a prompt, so a bad name stops and says so rather than quietly moving
nothing.

    python3 triage.py list
    python3 triage.py resolve 20260903T014105Z-unclassified --note "playbook in #205"

`list` re-classifies each report's stored message against *today's*
`_INCIDENT_SIGNATURES` and shows that beside the kind the run recorded. That
comparison is the whole point of the view: a report written `unclassified` before
anyone wrote up its failure mode is exactly the one that is already handled, and
nothing else on disk says so. It is a display, not a rewrite — the recorded kind is
what the run actually observed and stays untouched, in the queue and in the archive.

Reading the archive back is deliberately not implemented; it is a directory plus one
append-only JSONL file, and `ls` and `jq` already do that better than a flag would.

    ls ~/.config/daily-podcast/incidents/handled/
    jq -r '[.resolved_at,.stem,.note] | @tsv' \
        ~/.config/daily-podcast/incidents/handled/index.jsonl
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import render  # noqa: E402  (must follow the sys.path insert above)

# One row per resolution, appended to handled/index.jsonl. Full key set on every row
# (RUN_LOG_FIELDS / BLOOPER_FIELDS posture): a missing value is null, never absent,
# so the file parses line-by-line in jq.
RESOLUTION_FIELDS = ("resolved_at", "stem", "recorded_kind", "current_kind", "note", "files")

LEDGER_FILENAME = "index.jsonl"

REPORT_SUFFIXES = (".md", ".json")


def _stem_in_queue(name: str, queue: Path) -> str:
    """Normalize an operator's argument into a report stem, refusing anything that
    points outside the queue.

    A bare stem, either filename of the pair, and a tab-completed path into the queue
    all resolve to the same report. A path anywhere else is REFUSED rather than
    silently reinterpreted as a bare name — prune_workdirs' posture: never act
    outside your own tree, and never let a wrong argument mean something plausible."""
    text = str(name).strip()
    if not text:
        raise ValueError("empty report name")
    candidate = Path(text)
    stem = candidate.name
    for suffix in REPORT_SUFFIXES:
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    if not stem or stem in (".", ".."):
        raise ValueError(f"not a report name: {name!r}")
    if str(candidate.parent) != "." and candidate.parent.resolve() != queue.resolve():
        raise ValueError(f"{name!r} is not in the incident queue ({queue})")
    return stem


def _read_entry(queue: Path, stem: str) -> dict[str, Any]:
    """One queue row: what the run recorded, and what today's signatures make of it."""
    sidecar = queue / f"{stem}.json"
    data: dict[str, Any] = {}
    if sidecar.is_file():
        try:
            loaded = json.loads(sidecar.read_text())
        except (OSError, json.JSONDecodeError):
            loaded = None
        if isinstance(loaded, dict):
            data = loaded

    recorded = data.get("kind")
    if not isinstance(recorded, str) or not recorded:
        # No readable sidecar (write_incident writes the markdown first, so a crash
        # between the two leaves one). The stem is `<stamp>-<slug>` and the stamp
        # carries no hyphen, so the kind is still recoverable from the filename.
        recorded = stem.split("-", 1)[1] if "-" in stem else "unclassified"

    message = data.get("message")
    # With no message there is no evidence of a change, so the recorded kind stands.
    # Re-classifying an empty string would report a spurious downgrade to
    # "unclassified" for every report whose sidecar is missing.
    has_message = isinstance(message, str) and bool(message)
    current = render.classify_incident(message) if has_message else recorded

    return {
        "stem": stem,
        "recorded_kind": recorded,
        "current_kind": current,
        "reclassified": current != recorded,
        "when": data.get("timestamp") or "",
        "files": sorted(p.name for p in _report_files(queue, stem)),
    }


def _report_files(directory: Path, stem: str) -> list[Path]:
    return [p for p in (directory / f"{stem}{s}" for s in REPORT_SUFFIXES) if p.exists()]


def queue_entries(queue: Path | str | None = None) -> list[dict[str, Any]]:
    """Every report in the intake queue, oldest first (the stem starts with a
    sortable UTC stamp). Read-only: nothing here writes to the queue."""
    directory = Path(queue) if queue is not None else render.incident_dir()
    if not directory.is_dir():
        return []
    stems = sorted(
        {p.stem for p in directory.iterdir() if p.is_file() and p.suffix in REPORT_SUFFIXES}
    )
    return [_read_entry(directory, stem) for stem in stems]


def format_queue(
    entries: list[dict[str, Any]], queue: Path | None = None, *, program: str | None = None
) -> str:
    """The triage view. Ends on the drain: an operator who can see the queue has to
    be able to see how to shrink it, or this is another read-only command.

    `program` is how the caller was invoked (main() passes argv[0]) so the hint is
    copy-pasteable from wherever they ran it — the skill lives at a different path
    in the repo, a checkout, and the version-keyed plugin cache."""
    directory = queue if queue is not None else render.incident_dir()
    if not entries:
        return f"incident queue is empty ({directory})"
    width = max(len(e["stem"]) for e in entries)
    lines = [f"{len(entries)} report(s) in {directory}", ""]
    for entry in entries:
        note = entry["recorded_kind"]
        if entry["reclassified"]:
            if entry["current_kind"] == "unclassified":
                note += " -> unclassified (no playbook matches it any more)"
            else:
                note += f" -> {entry['current_kind']} (a playbook now covers this)"
        lines.append(f"  {entry['stem']:<{width}}  {note}")
    lines += [
        "",
        "Handled one? Move it out of the queue (it is archived, never deleted):",
        f"  python3 {program or Path(__file__).name} resolve {entries[0]['stem']} --note '<why>'",
    ]
    return "\n".join(lines)


def _append_ledger(archive: Path, record: dict[str, Any]) -> None:
    """Append one resolution row. Append-only — never _atomic_write_text, which would
    clobber the history to a single line (runs.jsonl / bloopers-index posture)."""
    row = {key: record.get(key) for key in RESOLUTION_FIELDS}
    with open(archive / LEDGER_FILENAME, "a") as f:
        f.write(json.dumps(row, default=str) + "\n")


def resolve_incident(name: str, *, note: str | None = None) -> dict[str, Any]:
    """Move one handled report out of the intake queue into the archive.

    A move, never a delete: the report is the evidence behind whichever playbook
    covers it, and `rm` was the only thing that used to clear the queue. Returns the
    ledger record. Raises on anything unexpected — a missing report, a name pointing
    outside the queue, a symlink, a collision in the archive — rather than degrading:
    the best-effort contract protects runs, and nothing in a run calls this."""
    queue = render.incident_dir()
    archive = render.handled_incident_dir()
    stem = _stem_in_queue(name, queue)

    sources = _report_files(queue, stem)
    if not sources:
        raise FileNotFoundError(f"no report named {stem!r} in {queue}")
    for src in sources:
        if src.is_symlink() or not src.is_file():
            raise ValueError(f"{src} is not a regular file; refusing to move it")

    # Resolve every destination BEFORE moving any of them: a collision must not be
    # able to leave the markdown archived and its sidecar behind in the queue. The
    # archive is evidence, so a same-named report there is never overwritten.
    moves = [(src, archive / src.name) for src in sources]
    for _src, dest in moves:
        if dest.exists():
            raise FileExistsError(f"{dest} is already archived; refusing to overwrite it")

    entry = _read_entry(queue, stem)
    archive.mkdir(parents=True, exist_ok=True)
    for src, dest in moves:
        shutil.move(str(src), str(dest))

    record = {
        "resolved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "stem": stem,
        "recorded_kind": entry["recorded_kind"],
        "current_kind": entry["current_kind"],
        "note": note,
        "files": [dest.name for _src, dest in moves],
    }
    _append_ledger(archive, record)
    render.log(f"resolved incident {stem} -> {archive}")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Triage the incident queue: see what is open, archive what is handled.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show the queue, re-classified against today's signatures")
    r = sub.add_parser("resolve", help="move handled report(s) into incidents/handled/")
    r.add_argument("names", nargs="+", metavar="REPORT", help="report stem, filename or path")
    r.add_argument("--note", help="why it is handled (e.g. the PR that landed the playbook)")
    args = parser.parse_args()

    if args.command == "list":
        print(format_queue(queue_entries(), program=sys.argv[0] or None))
        return 0

    # One bad name must not strand the reports named after it: report it and keep
    # draining, then exit non-zero so a script still notices.
    failures = 0
    for name in args.names:
        try:
            resolve_incident(name, note=args.note)
        except (FileNotFoundError, FileExistsError, ValueError, OSError) as e:
            print(f"error: {e}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
