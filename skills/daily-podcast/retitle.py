#!/usr/bin/env python3
"""
retitle.py — back-fill topical titles onto already-published R2 manifest entries (#144).

Seventy-five episodes shipped as `Daily Digest - <date>`, so the public show's back
catalogue is 75 interchangeable date stamps. #139 gave NEW episodes a topical title;
this applies that same format retroactively, to the one Spotify surface that is
mutable at all:

  Public show (RSS-ingested from cortech.online) <- `title` in the R2 manifest entry
    -> ordinary data, and guid-neutral since #128, so it can be rewritten.
  Private Save-to-Spotify show                   <- `upload --title`
    -> immutable after creation, on a show at its 60-episode cap. Nothing to do.

Why this is safe, and exactly how far that safety reaches: cortech.online publishes
/podcast/<slug>/ as an isPermaLink <guid>, and Spotify treats a CHANGED GUID AS A
BRAND-NEW EPISODE. The slug comes from `slug_for_date(<date>)` and cannot see the
title by construction (#128), so a retitle updates each episode in place. That
property is the whole basis of this tool, so it is re-proved per entry rather than
assumed: `retitle_entries` rebuilds the slug from the entry's own pubDate and refuses
any entry where the two disagree.

Design, and what each piece is guarding against:

  - The topic phrases are DATA, checked in at backfill_topics.json. That is what makes
    a re-run idempotent (a pure function of a fixed table, not a fresh generation) and
    what makes the copy reviewable by eye before it reaches a public feed.
  - The title is composed by orchestrate.episode_title -- the same function new
    episodes use. #144 applies #139's format; it must not invent a second one.
  - The raw material this table was written from is TTS narration text, and that is
    the main trap: 16 of the 75 entries carry letter-spaced acronyms ("Anthropic's two
    trillion dollar I P O path") and spelled-out numerals in exactly the fields you
    would mine for topics. check_topics screens for both, plus non-ASCII (em dashes,
    smart quotes, emoji render inconsistently across podcast directories).
  - assert_title_only is the gate on the write: a manifest that fails the consumer's
    episodeSchema empties the ENTIRE public feed, silently. Only `title` may differ,
    and the slug sequence may not move.
  - It deliberately does NOT route through render.upsert_manifest: that re-sorts and
    caps to 200 entries (#124), which is right for adding one episode and wrong for
    rewriting in place.
  - Dry run is the DEFAULT. `--apply` is the opt-in, `--only` is the canary.

Covers are deliberately NOT regenerated. build_cover renders `title[:48]` as a small
hint line under the date, so all 75 published covers read "Daily Digest - <date>"
while their feed titles become topical. Regenerating them would mean re-PUTting each
<slug>.jpg under the `immutable, max-age=31536000` cache-control the publish sets, at
an unchanged URL -- so CDN and Spotify copies would keep the old art anyway -- and the
hard 48-character cut would replace a correct-if-generic line with a mid-word chop.
The cover's primary subtitle (the date) stays correct either way. See #144.

    python3 skills/daily-podcast/retitle.py                          # review all 75
    python3 skills/daily-podcast/retitle.py --only <slug> --apply    # canary
    python3 skills/daily-podcast/retitle.py --apply                  # the rest
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import orchestrate
import render

SKILL_DIR = Path(__file__).resolve().parent
TOPICS_PATH = SKILL_DIR / "backfill_topics.json"
# Alongside the rest of the user-level state, so tests/conftest.py's redirect of
# render.CONFIG_DIR keeps a test run out of the real directory.
BACKUP_DIR = render.CONFIG_DIR / "manifest-backups"

# The month name in the title's date tail must be the SAME one the published slug
# encodes. render mints slugs from a literal table rather than strftime("%B"), which
# is LC_TIME-dependent; borrowing it here rather than minting a second table is what
# keeps "August 20, 2026" and `daily-digest-august-20-2026` from drifting apart on a
# non-English box. retitle_entries re-proves the agreement per entry anyway.
MONTHS = render._LEGACY_TITLE_MONTHS

# The 2-3 band comes from orchestrate, parsed rather than restated so the two cannot
# drift (#139 measured it: at 2-4 words the third topic was routinely dropped).
TOPIC_WORDS_MIN, TOPIC_WORDS_MAX = (int(n) for n in orchestrate.TITLE_TOPIC_WORDS.split("-"))

# "I P O", "A I", "S B fifty three" -- the narration convention for spelling an
# acronym aloud. Harmless in audio, unreadable in a title.
LETTER_SPACED = re.compile(r"\b(?:[A-Z] ){1,}[A-Z]\b")
# Narration spells numbers out ("three point seven five million patients"); a title is
# read on a screen, where digits scan better (SKILL.md, "Episode title"). Magnitudes
# are in the list too: write "$2T", not "two trillion".
NUMBER_WORDS = frozenset(
    """zero one two three four five six seven eight nine ten eleven twelve thirteen
    fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty
    sixty seventy eighty ninety hundred thousand million billion trillion""".split()
)
# Security idioms where the number word is part of a fixed compound rather than a
# quantity: "zero-day" is not a spelled-out numeral and rewriting it around the screen
# would cost the most searchable keyword in a breach story. Removed before the scan,
# not added to it, so "one" on its own is still caught.
NUMBER_WORD_COMPOUNDS = re.compile(r"\b(?:zero|one)-(?:day|click|trust|shot)s?\b", re.I)
_WORD = re.compile(r"[a-z]+")


def load_topics(path: Path | None = None) -> dict[str, list[str]]:
    """slug -> topic phrases. The file wraps the map in a `topics` key so it can carry
    a `note` explaining itself; JSON has no comments and this table is public copy."""
    data = json.loads((path or TOPICS_PATH).read_text())
    return data["topics"]


def date_long(date_iso: str) -> str:
    """`2026-08-20` -> `August 20, 2026`, the tail #139's format ends on."""
    d = dt.date.fromisoformat(date_iso)
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


def check_topics(topics: Any) -> list[str]:
    """Every screen the published copy has to pass, as a list of problems (empty = ok).

    Stricter than orchestrate's live path on purpose. There, a degraded reply falls
    back to the known-good date-only title and the episode still ships; here the
    phrases are hand-written, reviewed once, and then frozen onto a public feed, so
    anything questionable should stop the run rather than quietly publish."""
    problems: list[str] = []
    if not isinstance(topics, list) or len(topics) != orchestrate.TITLE_TOPIC_COUNT:
        return [f"expected exactly {orchestrate.TITLE_TOPIC_COUNT} topics, got {topics!r}"]
    for topic in topics:
        if not isinstance(topic, str) or not topic.strip():
            problems.append(f"empty topic in {topics!r}")
            continue
        words = topic.split()
        if not TOPIC_WORDS_MIN <= len(words) <= TOPIC_WORDS_MAX:
            problems.append(f"{topic!r}: {len(words)} words, want {orchestrate.TITLE_TOPIC_WORDS}")
        if not topic.isascii():
            problems.append(f"{topic!r}: non-ASCII (em dash, smart quote or emoji)")
        if orchestrate.TITLE_TOPIC_JOIN.strip() in topic:
            problems.append(f"{topic!r}: contains a comma, which is the topic separator")
        if LETTER_SPACED.search(topic):
            problems.append(f"{topic!r}: letter-spaced acronym, a narration artifact")
        scannable = NUMBER_WORD_COMPOUNDS.sub(" ", topic).lower()
        spelled = sorted({w for w in _WORD.findall(scannable) if w in NUMBER_WORDS})
        if spelled:
            problems.append(f"{topic!r}: spelled-out number word {spelled}, use digits")
    if topics and isinstance(topics[0], str) and topics[0][:1].islower():
        # The first topic opens the title; a leading lowercase article reads as a
        # truncated string in a Spotify list view.
        problems.append(f"{topics[0]!r}: leads the title, so it needs a capital")
    return problems


def compose_title(date_iso: str, topics: Sequence[str]) -> str:
    """#139's format, composed by #139's function. Raises rather than degrade.

    orchestrate.episode_title drops whole trailing topics when the result would
    overrun the cap, and falls back to the date-only title with nothing left. That is
    the right posture for a live run -- a cosmetic field must never cost an episode --
    but for a back-fill it would silently publish a title missing a story, so any
    dropped topic is an error here instead."""
    problems = check_topics(list(topics))
    if problems:
        raise ValueError("; ".join(problems))
    title = orchestrate.episode_title(list(topics), date_long(date_iso))
    missing = [t for t in topics if t not in title]
    if missing:
        raise ValueError(f"episode_title dropped {missing} from {title!r} (over the cap)")
    return title


def retitle_entries(
    entries: Sequence[dict[str, Any]],
    topics_by_slug: dict[str, list[str]],
    only: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """A copy of `entries` with `title` replaced wherever the table has topics for the
    slug, in place: same order, same count, every other field untouched.

    An entry with no topics is left alone, which is what keeps this re-runnable after
    new episodes land -- those already carry a topical title from orchestrate.py.
    Copies each entry, because the caller diffs before against after and shared dicts
    would make that comparison pass vacuously."""
    out: list[dict[str, Any]] = []
    for e in entries:
        new = dict(e)
        slug = e.get("slug")
        topics = topics_by_slug.get(slug) if (only is None or slug in only) else None
        if topics:
            date_iso = str(e.get("pubDate", ""))[:10]
            # The retitle is guid-neutral only because the slug is keyed on the date
            # (#128). If this entry's own date doesn't reproduce its slug, that
            # assumption doesn't hold here -- stop rather than stamp a date tail
            # naming a different day than the permalink does.
            if render.slug_for_date(date_iso) != slug:
                raise ValueError(
                    f"{slug}: pubDate {e.get('pubDate')!r} does not key this slug "
                    f"(slug_for_date -> {render.slug_for_date(date_iso)!r})"
                )
            new["title"] = compose_title(date_iso, topics)
        out.append(new)
    return out


def changed_fields(
    before: Sequence[dict[str, Any]], after: Sequence[dict[str, Any]]
) -> dict[str, list[str]]:
    """slug -> the fields that differ. Raises if the slug sequence moved at all.

    Order and count are checked first and hard: a dropped entry unpublishes an
    episode, and a reordered one would make the per-index field comparison below
    compare two different episodes."""
    slugs_before = [e.get("slug") for e in before]
    slugs_after = [e.get("slug") for e in after]
    if slugs_before != slugs_after:
        raise ValueError(
            f"slug sequence moved: {len(slugs_before)} entries -> {len(slugs_after)}; "
            f"first difference at index {_first_difference(slugs_before, slugs_after)}"
        )
    out: dict[str, list[str]] = {}
    for old, new in zip(before, after, strict=True):
        fields = sorted(
            f for f in set(old) | set(new) if old.get(f, _MISSING) != new.get(f, _MISSING)
        )
        if fields:
            out[old["slug"]] = fields
    return out


_MISSING = object()


def _first_difference(a: list[Any], b: list[Any]) -> int:
    for i, (x, y) in enumerate(zip(a, b, strict=False)):
        if x != y:
            return i
    return min(len(a), len(b))


def assert_title_only(
    before: Sequence[dict[str, Any]], after: Sequence[dict[str, Any]]
) -> dict[str, list[str]]:
    """The gate on every write. `slug`, `pubDate`, `mp3_url`, `mp3_bytes`,
    `duration_s`, `chapters`, `spotify_uri`, `cover_url` and `explicit` stay
    byte-identical; only `title` may move. The consumer's episodeSchema empties the
    WHOLE feed on a validation miss, so a surprise field change is not a warning."""
    changed = changed_fields(before, after)
    offenders = {slug: fields for slug, fields in changed.items() if fields != ["title"]}
    if offenders:
        raise ValueError(f"non-title fields changed: {offenders}")
    return changed


# --- CLI -------------------------------------------------------------------


def _fail(msg: str) -> int:
    render.log(f"error: {msg}")
    return 2


def _read_manifest(args) -> tuple[list[dict[str, Any]], Any, str, dict[str, Any]]:
    """(entries, client, bucket, config). `client` is None for a --source read."""
    if args.source:
        return json.loads(Path(args.source).read_text()), None, "", {}
    config = render.load_config()
    cfg = render.load_r2_config(config)
    if cfg is None:
        raise RuntimeError(
            "R2 is not configured (need R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / "
            "R2_SECRET_ACCESS_KEY in env or secrets.json, plus r2_bucket and "
            "r2_public_base_url in config.json)"
        )
    client = render.r2_client(cfg)
    return (
        render._r2_get_manifest(client, cfg["bucket"], args.manifest_name),
        client,
        cfg["bucket"],
        config,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--topics", type=Path, default=None, help="topic table (default: bundled)")
    p.add_argument(
        "--only",
        action="append",
        metavar="SLUG",
        help="retitle only these slugs (repeatable) — the canary path",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="actually write the manifest back to R2 (default: dry run)",
    )
    p.add_argument("--no-hook", action="store_true", help="skip the Pages deploy hook")
    p.add_argument("--manifest-name", default="manifest.json", help="manifest object key")
    p.add_argument(
        "--source",
        type=Path,
        default=None,
        help="read the manifest from a local file instead of R2 (review only)",
    )
    args = p.parse_args(argv)

    if args.source and args.apply:
        return _fail("--source is review-only; --apply would PUT a stale local copy live")

    try:
        topics = load_topics(args.topics)
    except (OSError, ValueError, KeyError) as e:
        return _fail(f"topic table unreadable: {e}")

    try:
        before, client, bucket, config = _read_manifest(args)
    except Exception as e:
        return _fail(f"could not read the manifest: {e}")

    # A typo'd --only slug would otherwise retitle nothing and still report success,
    # which is the worst outcome on the canary step: you spend the window watching a
    # feed that was never asked to change.
    absent = sorted(set(args.only or ()) - {e.get("slug") for e in before})
    if absent:
        return _fail(f"--only named slugs that are not in this manifest: {absent}")

    try:
        after = retitle_entries(before, topics, only=args.only)
        changed = assert_title_only(before, after)
    except ValueError as e:
        return _fail(str(e))

    for e in after:
        if e["slug"] in changed:
            print(f"{e['slug']}\n    {e['title']}")
    covered = {e.get("slug") for e in before} & set(topics)
    print(
        f"\n{len(changed)} of {len(before)} entries retitled "
        f"({len(set(topics) - covered)} table slugs not in this manifest)"
    )

    if not args.apply:
        print("dry run — nothing written. Re-run with --apply to publish.")
        return 0

    # The idempotent case, made explicit: a second --apply composes the same titles
    # from the same table, so there is nothing to write and no reason to trigger a
    # site rebuild. Returning here is what keeps re-running cheap and side-effect-free.
    if not changed:
        print("every entry already carries its title — nothing to write.")
        return 0

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = BACKUP_DIR / f"{args.manifest_name.removesuffix('.json')}-{stamp}.json"
    backup.write_text(json.dumps(before, indent=2))
    print(f"backed up the pre-rewrite manifest to {backup}")

    try:
        render._r2_put(
            client,
            bucket,
            args.manifest_name,
            json.dumps(after, indent=2).encode(),
            "application/json",
            # Same headers maybe_publish_r2 uses: no-cache keeps the consumer's
            # build-time fetch off a stale CDN copy right after the rebuild fires.
            cache_control="no-cache",
        )
    except Exception as e:
        return _fail(f"manifest PUT failed, nothing published (backup at {backup}): {e}")
    print(f"published {args.manifest_name} ({len(after)} entries)")

    # Without this the rewrite is invisible: cortech.online is a static Astro build
    # that reads the manifest at BUILD time, and nothing on its side rebuilds on a
    # schedule -- the daily episode only appears because render.py fires this hook.
    if args.no_hook:
        print("skipped the Pages deploy hook — the site rebuilds on the next episode")
        return 0
    hook = render.resolve_pages_hook_url(config)
    if not hook:
        render.log("warning: no Pages deploy hook configured; the site will not rebuild yet")
        return 0
    render.fire_pages_hook(hook)
    return 0


if __name__ == "__main__":
    sys.exit(main())
