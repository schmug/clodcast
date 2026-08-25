"""Shape tests for the captured bubbles.town feed fixtures (#173).

The fixtures under `tests/data/bubbles_*.xml` are REAL responses, fetched once on
2026-08-25 and trimmed. They exist because the Surface Tension design spec's
grounding section was written from search results — the authoring session had the
host blocked by an egress proxy — and everything downstream was therefore built on
a described format rather than an observed one.

These tests are the executable half of that recon: every structural claim in
`docs/superpowers/specs/2026-08-24-surface-tension-design.md` section 2 is asserted
here, so `st_gather.py` can be written against a format someone has actually seen,
and so a later re-capture that changes shape goes red instead of silently
invalidating the spec.

The load-bearing one is `test_comments_feed_carries_no_comment_body`. The caller
premise in section 4.4 assumed `/feed/comments` carried comment text; it does not,
and that is a property of the feed, not of the trimming (the comments fixture is
verbatim). If a future capture ever *does* carry bodies, that test goes red and the
spec's downgraded caller role can be revisited deliberately.

XML is parsed with defusedxml, matching `orchestrate.parse_opml`: a third-party
feed is untrusted input and stdlib xml.etree is XXE / billion-laughs vulnerable.
"""

import re
from pathlib import Path

import pytest
from defusedxml.ElementTree import parse as xml_parse

import orchestrate

DATA = Path(__file__).resolve().parent / "data"

ATOM = "{http://www.w3.org/2005/Atom}"
MEDIA = "{http://search.yahoo.com/mrss/}"
SLASH = "{http://purl.org/rss/1.0/modules/slash/}"

POST_FEEDS = ("bubbles_feed.xml", "bubbles_feed_hot.xml", "bubbles_feed_new.xml")
ALL_FEEDS = POST_FEEDS + ("bubbles_feed_comments.xml",)

# https://bubbles.town/entry/<numeric id> — the site's own permalink for a post.
BUBBLES_ENTRY = re.compile(r"https://bubbles\.town/entry/(\d+)")
# A /feed/comments entry id: the parent post permalink plus a comment ordinal.
COMMENT_ID = re.compile(r"^https://bubbles\.town/entry/(\d+)#comment-(\d+)$")
COMMENT_TITLE = re.compile(r"^New comment on: (.+) \((\d+)(?:st|nd|rd|th), (\d+) total\)$")


def entries(name: str) -> list:
    root = xml_parse(DATA / name).getroot()
    assert root.tag == ATOM + "feed", f"{name} is not an Atom feed"
    ents = root.findall(ATOM + "entry")
    assert ents, f"{name} has no entries"
    return ents


def text(entry, tag: str) -> str:
    return (entry.findtext(ATOM + tag) or "").strip()


def links(entry) -> list:
    return entry.findall(ATOM + "link")


def content_html(entry) -> str:
    return entry.findtext(ATOM + "content") or ""


def strip_tags(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


# --------------------------------------------------------------------------
# Every feed: the Atom envelope and the fields gather_candidates reads
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ALL_FEEDS)
def test_every_entry_has_the_core_atom_fields(name):
    """title / link / id / published / updated / author / content on every entry.

    These are exactly the fields `gather_candidates` reads, so a feed missing one
    silently yields a blank candidate rather than an error.
    """
    for e in entries(name):
        assert text(e, "title"), f"{name}: entry with no title"
        assert links(e), f"{name}: entry with no link"
        assert text(e, "id"), f"{name}: entry with no id"
        assert text(e, "published"), f"{name}: entry with no published"
        assert text(e, "updated"), f"{name}: entry with no updated"
        assert e.find(ATOM + "author") is not None, f"{name}: entry with no author"
        assert content_html(e), f"{name}: entry with no content"


@pytest.mark.parametrize("name", ALL_FEEDS)
def test_every_link_is_http_and_survives_the_gather_guard(name):
    """No relative / mailto: / tag: links anywhere.

    `gather_candidates` drops non-http(s) links at gather time because they would
    reach `validate_manifest` as a `source_url` and fail the whole run
    (orchestrate.py:449). A feed full of them would silently gather nothing; a feed
    with none passes through untouched. 197 live entries had zero — this pins it.
    """
    for e in entries(name):
        href = links(e)[0].get("href", "")
        assert href.startswith(("http://", "https://")), f"{name}: non-http link {href!r}"


@pytest.mark.parametrize("name", ALL_FEEDS)
def test_published_is_iso_utc_and_parses(name):
    """`_entry_published` builds a UTC datetime from `published_parsed`; the wire
    format it comes from is RFC-3339 with a `Z` offset, not an RFC-822 date."""
    for e in entries(name):
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", text(e, "published"))


# --------------------------------------------------------------------------
# Post feeds: votes inline, and what `link` actually points at
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", POST_FEEDS)
def test_vote_count_is_inline_on_every_post_entry(name):
    """Votes ride the feed as media:community/media:statistics/@favorites.

    This is the finding that makes `/api/vote-count?url=` optional rather than a
    per-candidate round trip: ranking never has to leave the feed it already
    fetched. The value is an attribute string and needs int() coercion.
    """
    for e in entries(name):
        community = e.find(MEDIA + "community")
        assert community is not None, f"{name}: no media:community"
        stats = community.find(MEDIA + "statistics")
        assert stats is not None, f"{name}: no media:statistics under media:community"
        favorites = stats.get("favorites")
        assert favorites is not None, f"{name}: media:statistics has no @favorites"
        assert int(favorites) >= 0


@pytest.mark.parametrize("name", POST_FEEDS)
def test_post_link_points_at_the_blog_not_at_bubbles(name):
    """`link` is the ORIGINAL blog post, not a bubbles.town permalink.

    This is what makes the show's dedup and `covered.json` interoperate with the
    daily digest at all: both key on the publisher's own URL. A bubbles permalink
    here would make the same post look like two different URLs.
    """
    for e in entries(name):
        alternates = [ln for ln in links(e) if ln.get("rel") == "alternate"]
        assert len(alternates) == 1, f"{name}: expected exactly one rel=alternate"
        href = alternates[0].get("href", "")
        assert "bubbles.town" not in href, f"{name}: rel=alternate is a bubbles link: {href}"
        # The Atom id mirrors the blog URL, so `guidislink` dedup keys on it too.
        assert text(e, "id") == href


@pytest.mark.parametrize("name", POST_FEEDS)
def test_bubbles_permalink_is_recoverable_from_content(name):
    """Every post body is prefixed with an "Open on Bubbles" anchor.

    That anchor is the only place a post entry carries its bubbles.town entry id,
    and the id is what ties a post to its comments (whose ids are
    `entry/<id>#comment-N`). Without it there is no join key between the two feeds.
    """
    for e in entries(name):
        assert BUBBLES_ENTRY.search(content_html(e)), f"{name}: no bubbles entry id in content"


@pytest.mark.parametrize("name", POST_FEEDS)
def test_replies_link_appears_exactly_when_there_are_comments(name):
    """slash:comments is the count; a rel=replies link appears iff it is non-zero.

    Checked live across 147 post entries with zero mismatches. This is the cheap
    test for "does this post have discussion" — no second fetch required, which is
    what lets the plan gate a `switchboard` turn from the post feed alone.
    """
    for e in entries(name):
        count = int(e.findtext(SLASH + "comments") or "0")
        replies = [ln for ln in links(e) if ln.get("rel") == "replies"]
        assert bool(replies) == (count > 0), f"{name}: replies/slash:comments disagree"
        for ln in replies:
            href = ln.get("href", "")
            assert BUBBLES_ENTRY.match(href), f"replies link is not a bubbles entry: {href}"


def test_fixtures_cover_the_structural_variants():
    """The fixtures were picked to span the variants, not taken as the first N.

    A fixture set where every entry looks identical would let a parser pass while
    mishandling thumbnails, replies links, or zero-vote entries.
    """
    seen = {"thumbnail": False, "replies": False, "zero_votes": False, "many_votes": False}
    for name in POST_FEEDS:
        for e in entries(name):
            if e.find(MEDIA + "thumbnail") is not None:
                seen["thumbnail"] = True
            if any(ln.get("rel") == "replies" for ln in links(e)):
                seen["replies"] = True
            votes = int(e.find(MEDIA + "community").find(MEDIA + "statistics").get("favorites"))
            seen["zero_votes"] |= votes == 0
            seen["many_votes"] |= votes > 1
    assert all(seen.values()), f"fixture variant coverage regressed: {seen}"


# --------------------------------------------------------------------------
# /feed/comments — the answer that reshaped section 4.4
# --------------------------------------------------------------------------


def test_comments_feed_carries_no_comment_body():
    """THE finding: a comments entry carries navigation links and nothing else.

    Its content is a <ul> of anchors — "<post> on Bubbles", "Full discussion on
    Fediverse", and optionally "Earlier comments: #1". Strip the anchors and no
    prose remains. There is no comment text anywhere in the feed, which is why the
    spec's caller role is vote-and-context rather than real quotes on air.

    The comments fixture is stored VERBATIM (untrimmed) precisely so this test
    cannot be satisfied by an artifact of truncation.
    """
    for e in entries("bubbles_feed_comments.xml"):
        html = content_html(e)
        assert "TRIMMED" not in html, "comments fixture must stay verbatim to mean anything"

        # Everything OUTSIDE an anchor must be pure scaffolding.
        outside = strip_tags(re.sub(r"<a\b[^>]*>.*?</a>", "", html, flags=re.S))
        assert outside in ("", "Earlier comments:"), f"prose outside anchors: {outside!r}"

        # And every anchor's own text is scaffolding too — never a comment.
        for anchor in re.findall(r"<a\b[^>]*>(.*?)</a>", html, flags=re.S):
            label = strip_tags(anchor)
            is_scaffolding = (
                label.endswith(" on Bubbles")  # "<post title> on Bubbles"
                or label == "Full discussion on Fediverse"
                or re.fullmatch(r"#\d+", label)  # "Earlier comments: #1"
            )
            assert is_scaffolding, f"unexpected anchor text (a comment body?): {label!r}"


def test_comment_ties_back_to_its_parent_post():
    """`id` is `https://bubbles.town/entry/<post id>#comment-<n>`.

    So a comment CAN be joined to the post it belongs to, without a second fetch —
    the one part of the original caller design that survives intact.
    """
    for e in entries("bubbles_feed_comments.xml"):
        m = COMMENT_ID.match(text(e, "id"))
        assert m, f"comment id does not tie back: {text(e, 'id')!r}"
        # The same post id appears as the first anchor in the body.
        assert m.group(1) == BUBBLES_ENTRY.search(content_html(e)).group(1)


def test_comment_title_names_the_post_and_the_thread_position():
    """ "New comment on: <post title> (Nth, M total)" — matched live on 50/50.

    The post TITLE is therefore available from the comments feed alone, which is
    what lets a vote-and-context caller segment say which post is being discussed
    without joining against a post feed.
    """
    for e in entries("bubbles_feed_comments.xml"):
        m = COMMENT_TITLE.match(text(e, "title"))
        assert m, f"unexpected comment title shape: {text(e, 'title')!r}"
        assert int(m.group(2)) <= int(m.group(3))


def test_comment_link_is_a_third_party_fediverse_permalink():
    """`link` is the comment's permalink on its home instance, not on bubbles.town.

    Note what this means for section 4.4's hard rule: the handle is embedded in that
    URL path, so the ONLY personal identifier the feed exposes is exactly the one
    the spec forbids putting on air. The feed offers no separate author field —
    `author` is the site itself on every entry.
    """
    for e in entries("bubbles_feed_comments.xml"):
        href = links(e)[0].get("href", "")
        assert "bubbles.town" not in href, f"expected a third-party permalink, got {href}"
        author = e.find(ATOM + "author").findtext(ATOM + "name")
        assert author == "bubbles.town", f"expected the site as author, got {author!r}"


# --------------------------------------------------------------------------
# feedparser: the consumer orchestrate.py actually uses
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ALL_FEEDS)
def test_feedparser_parses_every_fixture_without_loss(name):
    """bozo=False, atom10, and the namespaced extras survive the mapping.

    `gather_candidates` consumes feeds through feedparser, so "well-formed Atom"
    is necessary but not sufficient — the vote count has to survive feedparser's
    namespace flattening too.
    """
    feedparser = pytest.importorskip("feedparser")
    d = feedparser.parse((DATA / name).read_bytes())
    assert not d.bozo, f"{name}: feedparser reported {d.get('bozo_exception')!r}"
    assert d.version == "atom10"
    assert len(d.entries) == len(entries(name))
    for e in d.entries:
        assert e.get("title") and e.get("link") and e.get("published_parsed")
        assert e.get("content") and e.content[0].value


def test_feedparser_flattens_media_community_to_media_statistics():
    """The gotcha worth writing down: feedparser does NOT nest these.

    The wire format is media:community > media:statistics/@favorites, but feedparser
    flattens it — `entry.media_community` is the empty string and the attributes
    land on a top-level `entry.media_statistics` dict. An implementer walking the
    XML nesting through feedparser finds nothing.
    """
    feedparser = pytest.importorskip("feedparser")
    d = feedparser.parse((DATA / "bubbles_feed.xml").read_bytes())
    for e in d.entries:
        assert e.get("media_community") == "", "feedparser stopped flattening media:community"
        assert "favorites" in e.get("media_statistics", {})
        assert int(e["media_statistics"]["favorites"]) >= 0
        assert e.get("slash_comments") is not None


def test_gather_candidates_survives_a_real_bubbles_feed(tmp_path):
    """End-to-end through the actual consumer (orchestrate.py:420).

    The fixtures are only useful if the code that will read them tolerates the
    shape. This drives `gather_candidates` with the real capture and asserts it
    yields candidates whose url/title/summary are populated — proving the feed
    survives the http(s) guard, the lookback filter and the dedup set.
    """
    feedparser = pytest.importorskip("feedparser")
    import datetime as dt

    opml = tmp_path / "bubbles.opml"
    opml.write_text(
        '<opml><body><outline type="rss" text="Bubbles" '
        'xmlUrl="https://bubbles.town/feed" category="Blogs"/></body></opml>'
    )
    parsed = feedparser.parse((DATA / "bubbles_feed.xml").read_bytes())

    # The capture is dated 2026-08-25; anchor "now" to it so the lookback window
    # is meaningful rather than dependent on when the suite runs.
    now = dt.datetime(2026, 8, 25, 12, 0, tzinfo=dt.timezone.utc)
    out = orchestrate.gather_candidates([str(opml)], 48, {}, now, parse=lambda url: parsed)

    assert len(out) == len(entries("bubbles_feed.xml"))
    for c in out:
        assert c["url"].startswith("https://")
        assert "bubbles.town" not in c["url"]
        assert c["title"] and c["summary"]
        assert c["feed_name"] == "Bubbles"
        assert c["category"] == "Blogs"


def test_gather_candidates_summary_is_the_full_post_body():
    """A trap for the metadata-only invariant, and it is not hypothetical.

    Atom `content` here is the ENTIRE post, and feedparser mirrors it into
    `summary` — so `_clean(entry.get("summary") or ...)` in `gather_candidates`
    picks up article text, not a blurb. `_clean`'s truncation is what keeps the
    deterministic ranking metadata-only in practice, so `st_gather` must keep a
    cap of its own rather than assuming this field is short.
    """
    feedparser = pytest.importorskip("feedparser")
    d = feedparser.parse((DATA / "bubbles_feed.xml").read_bytes())
    for e in d.entries:
        assert e["summary"] == e.content[0].value, "summary/content diverged from the capture"
