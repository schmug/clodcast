"""Maintenance CLI for the bloopers bin (#169).

Nothing in a run calls this. `render.py` fills the bin automatically on four
triggers — a gate rejection, a near-miss that shipped, a failed run's leftover
segments, and a take the transcript check re-rolled (#202) — and this covers the
fifth: a clip you heard yourself.

That fourth trigger is the one that matters most in practice. The 2026-08-17
"birdsbirdsbirds" chapter tripped no gate (the gate did not exist yet), its workdir
emptied within four days, and the only surviving copy is inside the published
episode. `mark` trims any audio file, so it reaches a workdir segment, a whole
episode.mp3, or a feed episode downloaded by hand — without teaching a maintenance
CLI to fetch things off the network.

    python3 bloopers.py mark --from episode.mp3 --start 4:12 --end 4:58 \
        --note "birdsbirdsbirds"

Reading the bin back is deliberately not implemented: it is one append-only JSONL
file, and `jq` already does this better than a flag would.

    jq -r 'select(.reason!="run-failed") | [.reason,.segment,.note] | @tsv' \
        ~/.config/daily-podcast/bloopers/index.jsonl
    jq -s 'map(.duration_ms // 0) | add / 1000' \
        ~/.config/daily-podcast/bloopers/index.jsonl   # seconds banked so far
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import render  # noqa: E402  (must follow the sys.path insert above)


def parse_timecode(text: str) -> float:
    """Parse `SS`, `SS.s`, `MM:SS` or `HH:MM:SS` into seconds.

    These are the shapes a player shows you, which is where a blooper's timestamps
    actually come from. Negative values and junk raise rather than silently becoming
    a trim of the wrong part of the episode."""
    parts = str(text).strip().split(":")
    if not 1 <= len(parts) <= 3 or any(not p.strip() for p in parts):
        raise ValueError(f"bad timecode {text!r}: expected SS, MM:SS or HH:MM:SS")
    try:
        values = [float(p) for p in parts]
    except ValueError as e:
        raise ValueError(f"bad timecode {text!r}: expected SS, MM:SS or HH:MM:SS") from e
    if any(v < 0 for v in values):
        raise ValueError(f"bad timecode {text!r}: negative")
    seconds = 0.0
    for value in values:
        seconds = seconds * 60 + value
    return seconds


def mark(
    source: Path | str,
    *,
    start: str,
    end: str,
    note: str | None = None,
    **ctx: Any,
) -> dict[str, Any] | None:
    """Trim [start, end) out of `source` and bank it as a `manual` blooper.

    Unlike the automatic captures this one raises: it is an interactive command, and
    a typo'd timecode or a missing file should stop and say so rather than quietly
    bank nothing. The best-effort contract applies to runs, not to a human at a
    prompt."""
    src = Path(source)
    if not src.is_file():
        raise FileNotFoundError(f"no such audio file: {src}")
    begin = parse_timecode(start)
    finish = parse_timecode(end)
    if finish <= begin:
        raise ValueError(f"--end ({end}) must come after --start ({start})")

    with tempfile.TemporaryDirectory() as tmp:
        clip = Path(tmp) / "clip.mp3"
        # -t (duration) rather than -to (absolute end): -to's meaning depends on
        # whether it is read before or after -ss, and that has bitten enough people
        # that a duration is simply less to get wrong.
        # mono 44.1k is re-asserted here as it is at every other ffmpeg call site —
        # a banked clip is input to a future episode, and concat-protocol is fragile
        # across mismatched rates.
        render.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(begin),
                "-t",
                str(round(finish - begin, 3)),
                "-i",
                str(src),
                "-ar",
                str(render.AUDIO_SAMPLE_RATE),
                "-ac",
                str(render.AUDIO_CHANNELS),
                "-c:a",
                render.AUDIO_CODEC,
                "-b:a",
                render.AUDIO_BITRATE,
                str(clip),
            ]
        )
        record = render.bank_blooper(
            clip,
            reason="manual",
            note=note,
            duration_ms=int(round((finish - begin) * 1000)),
            **ctx,
        )

    if record is None:
        render.log("nothing banked (already in the bin, or the bin is not writable)")
        return None
    # bank_blooper records where it copied FROM, which was the temp trim. The useful
    # provenance is the file a human pointed at.
    record["source"] = str(src)
    _rewrite_last_index_row(record)
    render.log(f"banked {record['clip']} ({record['duration_ms'] / 1000:.1f}s)")
    return record


def _rewrite_last_index_row(record: dict[str, Any]) -> None:
    """Correct the row bank_blooper just appended, in place.

    Only the final line is touched and only when it is the row we just wrote, so this
    cannot disturb the archive behind it."""
    import json

    path = render.BLOOPER_DIR / "index.jsonl"
    try:
        lines = path.read_text().splitlines()
        if not lines or json.loads(lines[-1]).get("sha256") != record["sha256"]:
            return
        lines[-1] = json.dumps(record)
        path.write_text("\n".join(lines) + "\n")
    except (OSError, ValueError) as e:
        render.log(f"warn: could not update {path}: {e}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    m = sub.add_parser("mark", help="trim a clip out of an audio file into the bin")
    m.add_argument("--from", dest="source", required=True, type=Path)
    m.add_argument("--start", required=True, help="SS, MM:SS or HH:MM:SS")
    m.add_argument("--end", required=True, help="SS, MM:SS or HH:MM:SS")
    m.add_argument("--note", help="why this one is worth keeping")
    m.add_argument("--title", help="episode it came from, if you know it")
    args = parser.parse_args()

    try:
        mark(args.source, start=args.start, end=args.end, note=args.note, title=args.title)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
