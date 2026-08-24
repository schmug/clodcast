"""
Tests for retitle.py — the back-fill that rewrites `title` on already-published R2
manifest entries (issue #144).

The risk this file exists to contain is not a broken build: it is a silently
duplicated or emptied PUBLIC feed. cortech.online republishes /podcast/<slug>/ as an
isPermaLink <guid>, so a slug that moves duplicates a published episode on Spotify,
and a manifest that fails the consumer's episodeSchema empties the whole feed. So the
guards below are the contract:

  - only `title` may differ, for every entry, and the slug sequence may not move;
  - the composed title comes from orchestrate.episode_title — the SAME function new
    episodes use (#139) — never a second format invented here;
  - the shipped topic table covers exactly the published slugs, and every topic
    survives the TTS-artifact screen (letter-spaced acronyms, spelled-out numerals)
    that the raw chapter titles are full of;
  - applying the table twice produces the same manifest.

No network: the R2 seam is the same FakeS3 shape test_r2.py uses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import orchestrate
import retitle

DATA = Path(__file__).parent / "data"

# slug -> episode date, from the same pinned capture test_r2.py uses. Pairing the
# topic table against THIS file is what proves the back-fill covers the live feed and
# nothing else — a slug typo in the table would otherwise pass silently.
PUBLISHED = dict(
    tuple(reversed(line.split("\t")))
    for line in (DATA / "published_slugs.tsv").read_text().splitlines()
    if line and not line.startswith("#")
)


# --- fakes -----------------------------------------------------------------


class FakeS3:
    """Minimal stand-in for the boto3 S3 client, mirroring tests/test_r2.py."""

    def __init__(self, manifest: list | None = None, key: str = "manifest.json"):
        self.objects: dict[str, bytes] = {}
        if manifest is not None:
            self.objects[key] = json.dumps(manifest).encode()
        self.put_order: list[str] = []
        self.put_kwargs: list[dict] = []

    def put_object(self, Bucket, Key, Body, ContentType=None, CacheControl=None):
        self.objects[Key] = Body if isinstance(Body, bytes) else Body.read()
        self.put_order.append(Key)
        self.put_kwargs.append({"ContentType": ContentType, "CacheControl": CacheControl})
        return {}

    def get_object(self, Bucket, Key):
        from botocore.exceptions import ClientError

        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        import io

        return {"Body": io.BytesIO(self.objects[Key])}


def entry(slug: str, **over) -> dict:
    """A manifest entry with every field build_manifest_entry emits, so a test that
    asserts "only title changed" is asserting over the real shape."""
    date = PUBLISHED.get(slug, "2026-08-20")
    base = {
        "slug": slug,
        "title": f"Daily Digest - {retitle.date_long(date)}",
        "description": "<p>summary</p><p>(0:00) - Intro</p>",
        "summary": "summary",
        "pubDate": f"{date}T12:00:00+00:00",
        "mp3_url": f"https://clodcast.cortech.online/{slug}.mp3",
        "mp3_bytes": 1234,
        "duration_s": 12.5,
        "chapters": [{"title": "Intro", "start_ms": 0, "source_url": None}],
        "explicit": False,
        "spotify_uri": "spotify:episode:abc",
        "cover_url": f"https://clodcast.cortech.online/{slug}.jpg",
    }
    base.update(over)
    return base


# --- the shipped topic table ----------------------------------------------


def test_topic_table_covers_exactly_the_published_slugs():
    """The back-fill's scope, pinned. A slug in the table that isn't live would write
    a title nobody reads; a live slug missing from the table would silently keep its
    date-only title, which is the whole defect this issue closes."""
    topics = retitle.load_topics()
    assert set(topics) == set(PUBLISHED), {
        "table_only": sorted(set(topics) - set(PUBLISHED)),
        "feed_only": sorted(set(PUBLISHED) - set(topics)),
    }


@pytest.mark.parametrize("slug", sorted(PUBLISHED))
def test_every_shipped_topic_set_is_publishable(slug):
    """Every screen the format states, applied to the copy actually being published.
    The raw material these were written from is TTS text — 16 of 75 entries carry
    letter-spaced acronyms ("I P O", "A I") in exactly the fields you'd mine, plus
    spelled-out numerals — so this runs on all 75, not a sample."""
    assert retitle.check_topics(retitle.load_topics()[slug]) == []


@pytest.mark.parametrize("slug", sorted(PUBLISHED))
def test_every_shipped_title_composes_and_keeps_every_topic(slug):
    """compose_title is fail-closed: it raises rather than let episode_title's
    over-cap path quietly drop a trailing topic, which would lose a searchable
    keyword from a title Spotify then freezes."""
    title = retitle.compose_title(PUBLISHED[slug], retitle.load_topics()[slug])
    assert len(title) <= orchestrate.TITLE_MAX_CHARS
    assert title.endswith(f" - {retitle.date_long(PUBLISHED[slug])}")
    for topic in retitle.load_topics()[slug]:
        assert topic in title


def test_composed_titles_are_the_orchestrator_format_not_a_second_one():
    """#144 must apply #139's format, not invent one. Delegation, asserted directly:
    the title is byte-identical to what a new episode's orchestrate.episode_title
    would produce from the same topics."""
    slug = "daily-digest-august-20-2026"
    topics = retitle.load_topics()[slug]
    expected = orchestrate.episode_title(topics, retitle.date_long(PUBLISHED[slug]))
    assert retitle.compose_title(PUBLISHED[slug], topics) == expected


def test_the_worked_example_in_skill_md_is_what_that_episode_gets():
    """SKILL.md's Episode title section documents 2026-08-20 in full. The back-fill
    publishing something else for that exact date would make the documented example
    fiction on the very feed it describes."""
    slug = "daily-digest-august-20-2026"
    assert (
        retitle.compose_title(PUBLISHED[slug], retitle.load_topics()[slug])
        == "Salt Typhoon, the CareCloud breach, Siemens PLC warnings - August 20, 2026"
    )


def test_no_two_episodes_get_the_same_topics():
    """Seventy-five interchangeable date stamps is the defect. Two episodes sharing a
    topic list would reproduce it in miniature — and it is the likeliest copy/paste
    slip in a 75-row hand-written table."""
    topics = retitle.load_topics()
    seen: dict[tuple[str, ...], str] = {}
    for slug, phrases in sorted(topics.items()):
        key = tuple(phrases)
        assert key not in seen, f"{slug} repeats the topics of {seen.get(key)}"
        seen[key] = slug


# --- the screens themselves ------------------------------------------------


@pytest.mark.parametrize(
    "topics,expect",
    [
        (["Anthropic's I P O", "the MCP roadmap", "rogue containment"], "letter-spaced"),
        (["three open letters", "Lean 4 soundness", "OpenAI's proofs"], "number word"),
        (["Salt Typhoon", "CareCloud — breached", "Siemens warnings"], "non-ASCII"),
        (["Salt Typhoon", "the CareCloud breach, again", "Siemens PLCs"], "comma"),
        (["Salt Typhoon", "a breach at CareCloud today", "Siemens PLCs"], "words"),
        (["Typhoon", "the CareCloud breach", "Siemens PLCs"], "words"),
        (["the CareCloud breach", "Salt Typhoon", "Siemens PLCs"], "capital"),
        (["Salt Typhoon", "the CareCloud breach"], "exactly"),
        ([], "exactly"),
    ],
)
def test_check_topics_rejects(topics, expect):
    problems = " ".join(retitle.check_topics(topics))
    assert expect in problems, problems


def test_compose_title_refuses_to_drop_a_topic():
    """episode_title degrades by dropping whole trailing topics; for a live run that
    is the right posture, but a back-fill dropping one silently publishes a title
    missing a story. Fail instead."""
    huge = ["Salt Typhoon", "the CareCloud breach", "S" * orchestrate.TITLE_MAX_CHARS]
    with pytest.raises(ValueError):
        retitle.compose_title("2026-08-20", huge)


def test_date_long_matches_the_month_the_slug_encodes():
    """The date tail must name the same month the published slug does — both come
    from render's literal month table rather than strftime("%B"), which is
    LC_TIME-dependent."""
    for slug, date in PUBLISHED.items():
        month = retitle.date_long(date).split()[0]
        assert month.lower() in slug


# --- the rewrite -----------------------------------------------------------


def test_rewrite_changes_title_and_nothing_else():
    before = [entry(s) for s in ("daily-digest-august-20-2026", "daily-digest-august-21-2026")]
    after = retitle.retitle_entries(before, retitle.load_topics())
    assert retitle.changed_fields(before, after) == {
        "daily-digest-august-20-2026": ["title"],
        "daily-digest-august-21-2026": ["title"],
    }
    for old, new in zip(before, after, strict=True):
        assert new["title"] != old["title"]


def test_rewrite_does_not_mutate_the_input():
    """The caller diffs before against after; sharing entry dicts would make that
    diff compare an object with itself and pass vacuously."""
    before = [entry("daily-digest-august-20-2026")]
    original = json.loads(json.dumps(before))
    retitle.retitle_entries(before, retitle.load_topics())
    assert before == original


def test_rewrite_is_idempotent():
    before = [entry(s) for s in ("daily-digest-august-20-2026", "daily-digest-june-1-2026")]
    once = retitle.retitle_entries(before, retitle.load_topics())
    twice = retitle.retitle_entries(once, retitle.load_topics())
    assert twice == once


def test_only_limits_the_rewrite_to_the_canary():
    """The canary step: rewrite exactly one episode, watch the public show update it
    in place, and only then touch the other 74."""
    slugs = ("daily-digest-august-20-2026", "daily-digest-august-21-2026")
    before = [entry(s) for s in slugs]
    after = retitle.retitle_entries(before, retitle.load_topics(), only=[slugs[0]])
    assert retitle.changed_fields(before, after) == {slugs[0]: ["title"]}


def test_an_entry_with_no_topics_is_left_untouched():
    """Forward compatibility: episodes published after this back-fill already carry a
    topical title from orchestrate.py, and a re-run must not disturb them."""
    before = [entry("daily-digest-august-24-2026", title="Something topical - August 24, 2026")]
    after = retitle.retitle_entries(before, retitle.load_topics())
    assert after == before


def test_rewrite_refuses_an_entry_whose_date_does_not_key_its_slug():
    """The retitle is guid-neutral only because the slug is keyed on the date (#128).
    A pubDate that doesn't reproduce the slug means that assumption is broken for
    this entry, so stop rather than stamp a date tail naming a different day."""
    bad = [entry("daily-digest-august-20-2026", pubDate="2026-08-19T12:00:00+00:00")]
    with pytest.raises(ValueError, match="slug"):
        retitle.retitle_entries(bad, retitle.load_topics())


# --- the guard that makes the write safe -----------------------------------


def test_changed_fields_catches_a_non_title_change():
    before = [entry("daily-digest-august-20-2026")]
    after = [entry("daily-digest-august-20-2026", mp3_bytes=999, title="new - August 20, 2026")]
    assert retitle.changed_fields(before, after) == {
        "daily-digest-august-20-2026": ["mp3_bytes", "title"]
    }


def test_changed_fields_catches_an_added_or_dropped_field():
    before = [entry("daily-digest-august-20-2026")]
    after = [entry("daily-digest-august-20-2026")]
    del after[0]["spotify_uri"]
    assert retitle.changed_fields(before, after) == {"daily-digest-august-20-2026": ["spotify_uri"]}


@pytest.mark.parametrize(
    "after",
    [
        [],
        [entry("daily-digest-august-21-2026"), entry("daily-digest-august-20-2026")],
        [entry("daily-digest-august-20-2026"), entry("daily-digest-august-21-2026")] * 1
        + [entry("daily-digest-june-1-2026")],
    ],
    ids=["dropped", "reordered", "appended"],
)
def test_changed_fields_refuses_a_moved_slug_sequence(after):
    before = [entry("daily-digest-august-20-2026"), entry("daily-digest-august-21-2026")]
    with pytest.raises(ValueError, match="slug"):
        retitle.changed_fields(before, after)


def test_assert_title_only_raises_on_anything_else():
    before = [entry("daily-digest-august-20-2026")]
    after = [entry("daily-digest-august-20-2026", duration_s=99.0)]
    with pytest.raises(ValueError, match="duration_s"):
        retitle.assert_title_only(before, after)
    retitle.assert_title_only(before, [entry("daily-digest-august-20-2026", title="x - y")])


# --- the CLI ---------------------------------------------------------------


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Point the CLI at a fake bucket holding two live-shaped entries."""
    live = [entry(s) for s in ("daily-digest-august-20-2026", "daily-digest-august-21-2026")]
    fake = FakeS3(live)
    fired: list[str] = []
    monkeypatch.setattr(retitle.render, "load_config", lambda: {"r2_bucket": "b"})
    monkeypatch.setattr(
        retitle.render,
        "load_r2_config",
        lambda cfg: {
            "account_id": "a",
            "access_key": "k",
            "secret_key": "s",
            "bucket": "b",
            "public_base_url": "https://clodcast.cortech.online",
        },
    )
    monkeypatch.setattr(retitle.render, "r2_client", lambda cfg: fake)
    monkeypatch.setattr(retitle.render, "resolve_pages_hook_url", lambda cfg: "https://hook")
    monkeypatch.setattr(retitle.render, "fire_pages_hook", lambda url: fired.append(url))
    monkeypatch.setattr(retitle, "BACKUP_DIR", tmp_path / "backups")
    return fake, fired, live


def test_dry_run_writes_nothing_and_fires_no_hook(wired, capsys):
    fake, fired, live = wired
    assert retitle.main([]) == 0
    assert fake.put_order == []
    assert fired == []
    assert json.loads(fake.objects["manifest.json"]) == live
    assert "dry run" in capsys.readouterr().out.lower()


def test_apply_writes_the_manifest_backs_it_up_and_fires_the_hook(wired, tmp_path):
    fake, fired, live = wired
    assert retitle.main(["--apply"]) == 0
    assert fake.put_order == ["manifest.json"]
    assert fake.put_kwargs[0] == {"ContentType": "application/json", "CacheControl": "no-cache"}
    written = json.loads(fake.objects["manifest.json"])
    assert retitle.changed_fields(live, written) == {
        "daily-digest-august-20-2026": ["title"],
        "daily-digest-august-21-2026": ["title"],
    }
    assert fired == ["https://hook"]
    backups = list((tmp_path / "backups").glob("*.json"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text()) == live


def test_apply_backs_up_before_it_writes(wired, tmp_path, monkeypatch):
    """Ordering, not existence: a backup written after a failed PUT is no backup."""
    fake, _, _ = wired

    def explode(*a, **kw):
        raise RuntimeError("PUT failed")

    monkeypatch.setattr(fake, "put_object", explode)
    assert retitle.main(["--apply"]) != 0
    assert list((tmp_path / "backups").glob("*.json"))


def test_only_flag_writes_a_single_retitled_entry(wired):
    fake, fired, live = wired
    assert retitle.main(["--apply", "--only", "daily-digest-august-20-2026"]) == 0
    written = json.loads(fake.objects["manifest.json"])
    assert retitle.changed_fields(live, written) == {"daily-digest-august-20-2026": ["title"]}
    assert fired == ["https://hook"]


def test_no_hook_skips_the_rebuild(wired):
    fake, fired, _ = wired
    assert retitle.main(["--apply", "--no-hook"]) == 0
    assert fake.put_order == ["manifest.json"]
    assert fired == []


def test_source_file_cannot_be_applied(tmp_path):
    """--source reads a manifest off disk so the 75 titles can be reviewed without
    credentials. Letting it --apply would PUT a stale local copy over the live one."""
    src = tmp_path / "manifest.json"
    src.write_text(json.dumps([entry("daily-digest-august-20-2026")]))
    assert retitle.main(["--source", str(src)]) == 0
    assert retitle.main(["--source", str(src), "--apply"]) != 0


def test_unconfigured_r2_fails_loudly(monkeypatch):
    monkeypatch.setattr(retitle.render, "load_config", lambda: {})
    monkeypatch.setattr(retitle.render, "load_r2_config", lambda cfg: None)
    assert retitle.main([]) != 0


def test_a_second_apply_writes_nothing(wired):
    """Idempotence where it costs something: the second run composes identical titles,
    so it must not PUT again or fire another site rebuild."""
    fake, fired, _ = wired
    assert retitle.main(["--apply"]) == 0
    assert retitle.main(["--apply"]) == 0
    assert fake.put_order == ["manifest.json"]
    assert fired == ["https://hook"]


def test_a_typoed_only_slug_fails_instead_of_no_opping(wired):
    """On the canary step a silent no-op is the worst outcome — you spend the window
    watching a feed that was never asked to change."""
    fake, fired, _ = wired
    assert retitle.main(["--apply", "--only", "daily-digest-augst-20-2026"]) != 0
    assert fake.put_order == []
    assert fired == []
