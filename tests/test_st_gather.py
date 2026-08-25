"""Invariant tests for `st_gather.py` — Surface Tension's two-source candidate gather (#175).

Three properties here are load-bearing and each exists because of a specific
failure that has already happened somewhere in this repo:

- **Category filtering fails silently in two directions.** A nested OPML export
  stamps no `category=` on its leaves, and Feedly writes `/user/<id>/category/Blogs`
  rather than a bare `Blogs`. Either one yields an empty pool that reads as a thin
  week rather than a config error, so both are tested and the empty case dies loudly.
- **The variety penalty must not be inert.** #95 found the daily show's
  `feed_usage.json` penalty dead since 2026-06-05, because its only writer stopped
  being the path that ships. Here the penalty reads `covered.json` — written by
  `render.py` on every successful ship and already loaded for dedup — so the tests
  drive it through the same map dedup uses, and go red if that wiring is cut.
- **The summary field is the whole post body.** Atom `content` on bubbles.town is
  the entire post and feedparser mirrors it into `summary` (spec 2.6). Only an
  explicit cap keeps the deterministic ranking metadata-only.

The bubbles fixtures under `tests/data/` are the real 2026-08-25 captures from
#173; nothing here touches the network.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib

import pytest

import st_gather

NOW = dt.datetime(2026, 8, 25, 12, 0, tzinfo=dt.timezone.utc)


def write_opml(tmp_path, body: str, name: str = "feeds.opml"):
    path = tmp_path / name
    path.write_text(f'<?xml version="1.0"?><opml version="2.0"><body>{body}</body></opml>')
    return path


# --------------------------------------------------------------------------
# OPML: parsing and the two category hazards
# --------------------------------------------------------------------------


def test_parse_opml_reads_the_category_attribute_off_the_leaf(tmp_path):
    """The flat, Feedly-shaped export: every rss leaf carries its own category."""
    path = write_opml(
        tmp_path,
        '<outline text="Group">'
        '<outline type="rss" text="Feed A" xmlUrl="https://a.example/rss" '
        'category="/user/9/category/Blogs" />'
        '<outline text="Not a feed" />'
        "</outline>",
    )
    feeds = st_gather.parse_opml(path)
    assert feeds == [
        {
            "feed_name": "Feed A",
            "xml_url": "https://a.example/rss",
            "category": "/user/9/category/Blogs",
            "category_inherited": False,
        }
    ]


def test_parse_opml_inherits_the_category_from_the_enclosing_folder(tmp_path):
    """The nested export: the folder outline IS the category, and no leaf says so.

    `orchestrate.parse_opml` walks `root.iter("outline")` flat and reads `category=`
    off the leaf, so this shape yields `""` for every feed — and a category filter
    then matches nothing and produces an empty pool that looks like a thin week.
    Recovering the folder name fixes the hazard instead of only reporting it, and
    `category_inherited` is what makes the recovery visible rather than silent.
    """
    path = write_opml(
        tmp_path,
        '<outline text="Blogs">'
        '<outline type="rss" text="Feed A" xmlUrl="https://a.example/rss" />'
        "</outline>",
    )
    assert st_gather.parse_opml(path) == [
        {
            "feed_name": "Feed A",
            "xml_url": "https://a.example/rss",
            "category": "Blogs",
            "category_inherited": True,
        }
    ]


def test_parse_opml_nests_folder_names_into_a_path(tmp_path):
    path = write_opml(
        tmp_path,
        '<outline text="Blogs"><outline text="Personal">'
        '<outline type="rss" title="Feed B" xmlUrl="https://b.example/rss" />'
        "</outline></outline>",
    )
    assert st_gather.parse_opml(path)[0]["category"] == "Blogs/Personal"


def test_parse_opml_prefers_an_explicit_category_over_the_folder(tmp_path):
    path = write_opml(
        tmp_path,
        '<outline text="Blogs">'
        '<outline type="rss" text="A" xmlUrl="https://a.example/rss" category="News" />'
        "</outline>",
    )
    feed = st_gather.parse_opml(path)[0]
    assert feed["category"] == "News"
    assert feed["category_inherited"] is False


def test_parse_opml_rejects_entity_expansion(tmp_path):
    """OPML is untrusted input (shared feed-reader exports); stdlib xml.etree is
    XXE / billion-laughs vulnerable, so this parser uses defusedxml like
    `orchestrate.parse_opml` does. A forbidden entity logs and yields []."""
    evil = tmp_path / "evil.opml"
    evil.write_text(
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "AAAA">]>'
        '<opml><body><outline type="rss" text="&a;" xmlUrl="https://a/rss"/></body></opml>'
    )
    assert st_gather.parse_opml(evil) == []


def test_parse_opml_missing_file_returns_empty(tmp_path):
    assert st_gather.parse_opml(tmp_path / "nope.opml") == []


@pytest.mark.parametrize(
    "value",
    [
        "Blogs",  # a bare name
        "/user/9000/category/Blogs",  # what Feedly actually writes
        "blogs",  # case-insensitive
        "/Blogs",  # a leading-slash path
        "News,/user/9/category/Blogs",  # OPML 2.0: comma-separated list of paths
    ],
)
def test_category_matches_every_shape_a_real_export_writes(value):
    assert st_gather.category_matches(value, "Blogs") is True


@pytest.mark.parametrize("value", ["", "Newsletters", "/user/9/category/Bloggers"])
def test_category_does_not_match_a_different_category(value):
    assert st_gather.category_matches(value, "Blogs") is False


def test_select_feeds_keeps_only_the_named_categories():
    feeds = [
        {"feed_name": "A", "xml_url": "https://a/rss", "category": "/user/9/category/Blogs"},
        {"feed_name": "B", "xml_url": "https://b/rss", "category": "Newsletters"},
    ]
    assert [f["feed_name"] for f in st_gather.select_feeds(feeds, ["Blogs"])] == ["A"]


def test_select_feeds_without_categories_keeps_everything():
    feeds = [{"feed_name": "A", "xml_url": "https://a/rss", "category": "Newsletters"}]
    assert st_gather.select_feeds(feeds, []) == feeds


def test_select_feeds_dies_naming_the_categories_actually_present():
    """A category matching zero feeds is a config error, not a thin day."""
    feeds = [
        {"feed_name": "A", "xml_url": "https://a/rss", "category": "/user/9/category/Newsletters"},
        {"feed_name": "B", "xml_url": "https://b/rss", "category": "Podcasts"},
    ]
    with pytest.raises(SystemExit):
        st_gather.select_feeds(feeds, ["Blogs"])


def test_select_feeds_death_message_lists_the_categories(capsys):
    feeds = [
        {"feed_name": "A", "xml_url": "https://a/rss", "category": "/user/9/category/Newsletters"},
        {"feed_name": "B", "xml_url": "https://b/rss", "category": "Podcasts"},
    ]
    with pytest.raises(SystemExit):
        st_gather.select_feeds(feeds, ["Blogs"])
    out = capsys.readouterr().out
    assert "Newsletters" in out and "Podcasts" in out


def test_select_feeds_names_the_uncategorised_export_hazard(capsys):
    """No category anywhere — neither a leaf attribute nor an enclosing folder.

    This is the residue of the nested-export hazard after the folder fallback:
    the file genuinely carries no category, and the message has to say so rather
    than print an empty list of "categories present" and leave the reader guessing.
    """
    feeds = [{"feed_name": "A", "xml_url": "https://a/rss", "category": ""}]
    with pytest.raises(SystemExit):
        st_gather.select_feeds(feeds, ["Blogs"])
    out = capsys.readouterr().out.lower()
    assert "no category" in out
    assert "category=" in out or "folder" in out


# --------------------------------------------------------------------------
# Candidates: one schema from two sources
# --------------------------------------------------------------------------


def entry(
    url="https://blog.example/post-1",
    title="A post",
    votes="7",
    comments="0",
    summary="A blurb.",
    published=NOW,
):
    """A feedparser-shaped entry. `media_statistics` (not `media_community`) is
    where feedparser lands the vote count — see test_bubbles_fixtures.py."""
    e = {
        "title": title,
        "link": url,
        "summary": summary,
        "media_statistics": {"favorites": votes},
        "slash_comments": comments,
    }
    if published is not None:
        e["published_parsed"] = published.timetuple()
    return e


def feed(*entries):
    return {"entries": list(entries)}


def spec(url="https://bubbles.town/feed", name="feed", source="bubbles", **kw):
    return {"url": url, "name": name, "source": source, "category": "", **kw}


def test_entry_votes_reads_the_flattened_media_statistics():
    """feedparser flattens media:community > media:statistics, so the votes are on
    `media_statistics`; an implementer walking the XML nesting finds an empty
    string. The value is an attribute STRING and needs int() coercion."""
    assert st_gather.entry_votes({"media_statistics": {"favorites": "39"}}) == 39


def test_entry_votes_defaults_to_zero_when_the_feed_carries_none():
    """An OPML blog feed has no vote signal at all — that is a 0, not a crash."""
    assert st_gather.entry_votes({}) == 0
    assert st_gather.entry_votes({"media_statistics": {}}) == 0
    assert st_gather.entry_votes({"media_statistics": {"favorites": "junk"}}) == 0


def test_entry_comment_count_reads_slash_comments():
    """`slash:comments` is the cheap "does this post have discussion" test — a
    rel=replies link appears exactly when it is non-zero (147/147 live entries),
    so the switchboard turn is gated from the post feed with no second fetch."""
    assert st_gather.entry_comments({"slash_comments": "4"}) == 4
    assert st_gather.entry_comments({}) == 0


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://WWW.Blog.Example/post", "blog.example"),
        ("https://blog.example/post", "blog.example"),
        ("not a url", ""),
    ],
)
def test_domain_identifies_the_blog_behind_a_url(url, expected):
    """The domain is the blog's identity, and it is what both the per-feed cap and
    the variety penalty key on — a bubbles candidate's `feed_name` is "/feed" or
    "/feed/hot", which would cap nothing."""
    assert st_gather.domain_for(url) == expected


def test_gather_candidates_produces_one_schema_from_both_sources():
    feeds = {
        "https://bubbles.town/feed": feed(entry(url="https://a.example/p", votes="12")),
        "https://blog.example/rss": feed(entry(url="https://blog.example/p", votes="0")),
    }
    out = st_gather.gather_candidates(
        [
            spec(url="https://bubbles.town/feed", name="bubbles /feed"),
            spec(url="https://blog.example/rss", name="Blog", source="opml", category="Blogs"),
        ],
        covered={},
        now=NOW,
        default_lookback_hours=168,
        parse=lambda u: feeds[u],
    )
    assert [c["source"] for c in out] == ["bubbles", "opml"]
    assert [c["votes"] for c in out] == [12, 0]
    assert [c["domain"] for c in out] == ["a.example", "blog.example"]
    assert out[1]["category"] == "Blogs"
    assert all(set(c) == set(st_gather.CANDIDATE_FIELDS) for c in out)


def test_gather_candidates_caps_the_summary_that_is_really_a_post_body():
    """Spec 2.6, and it is not hypothetical: Atom `content` on bubbles.town is the
    ENTIRE post and feedparser mirrors it into `summary`. Without a cap of its own
    this function walks article bodies into the deterministic ranking path."""
    body = "<p>" + ("word " * 5000) + "</p>"
    out = st_gather.gather_candidates(
        [spec()], {}, NOW, 168, parse=lambda u: feed(entry(summary=body))
    )
    assert len(out[0]["summary"]) <= st_gather.SUMMARY_MAX_CHARS
    assert "<p>" not in out[0]["summary"]


def test_gather_candidates_skips_a_feed_that_errors_and_keeps_the_rest():
    """One bad feed must not kill the run — log, record the drop, carry on."""

    def parse(url):
        if "bad" in url:
            raise RuntimeError("connection reset")
        return feed(entry())

    drops: list[dict] = []
    out = st_gather.gather_candidates(
        [spec(url="https://bad.example/rss", name="Bad"), spec(url="https://bubbles.town/feed")],
        {},
        NOW,
        168,
        parse=parse,
        drops=drops,
    )
    assert len(out) == 1
    assert [d["reason"] for d in drops] == ["feed_error"]
    assert "connection reset" in drops[0]["detail"]


def test_gather_candidates_drops_a_non_http_link():
    """A relative / mailto: / tag: link would reach render.validate_manifest as a
    source_url and fail the whole run. All 197 live bubbles entries were https, so
    this guard drops nothing there — it is the OPML side it protects."""
    drops: list[dict] = []
    out = st_gather.gather_candidates(
        [spec()], {}, NOW, 168, parse=lambda u: feed(entry(url="mailto:x@y.z")), drops=drops
    )
    assert out == []
    assert [d["reason"] for d in drops] == ["bad_link"]


def test_gather_candidates_excludes_already_covered_urls():
    covered = {"https://a.example/p": {"date": "2026-08-01", "mp3_url": "https://x/e.mp3"}}
    out = st_gather.gather_candidates(
        [spec()],
        covered,
        NOW,
        168,
        parse=lambda u: feed(entry(url="https://a.example/p"), entry(url="https://b.example/p")),
    )
    assert [c["url"] for c in out] == ["https://b.example/p"]


def test_gather_candidates_deduplicates_a_url_seen_on_two_feeds():
    """/feed and /feed/hot overlap by design (6 shared URLs in the capture)."""
    out = st_gather.gather_candidates(
        [spec(url="https://bubbles.town/feed"), spec(url="https://bubbles.town/feed/hot")],
        {},
        NOW,
        168,
        parse=lambda u: feed(entry(url="https://a.example/p")),
    )
    assert len(out) == 1


def test_gather_candidates_applies_the_feed_lookback_window():
    old = NOW - dt.timedelta(hours=200)
    out = st_gather.gather_candidates(
        [spec(lookback_hours=168)],
        {},
        NOW,
        168,
        parse=lambda u: feed(entry(url="https://a.example/p", published=old)),
    )
    assert out == []


def test_an_unbounded_feed_lookback_keeps_the_posts_hot_exists_to_surface():
    """Recon finding 2: /feed/hot is ranked by DISCUSSION, not recency — its oldest
    entry was ~16 weeks old and only 12 of 17 fell inside a 168 h window. A uniform
    lookback would discard exactly the posts that feed exists to surface."""
    ancient = NOW - dt.timedelta(hours=2682)
    out = st_gather.gather_candidates(
        [spec(url="https://bubbles.town/feed/hot", lookback_hours=None)],
        {},
        NOW,
        168,
        parse=lambda u: feed(entry(url="https://a.example/p", published=ancient, votes="46")),
    )
    assert [c["votes"] for c in out] == [46]


def test_feed_specs_ships_hot_unbounded_and_accepts_a_plain_string():
    specs = st_gather.feed_specs(st_gather.DEFAULT_CONFIG)
    by_url = {s["url"]: s for s in specs}
    assert (
        by_url["https://bubbles.town/feed"]["lookback_hours"]
        == st_gather.DEFAULT_CONFIG["lookback_hours"]
    )
    assert by_url["https://bubbles.town/feed/hot"]["lookback_hours"] is None
    assert all(s["source"] == "bubbles" for s in specs)


# --------------------------------------------------------------------------
# Ranking: votes, recency, and a variety penalty that is not inert
# --------------------------------------------------------------------------


def cand(url="https://a.example/p", votes=0, source="bubbles", published=NOW, title="t"):
    return {
        "title": title,
        "url": url,
        "published": published,
        "summary": "",
        "votes": votes,
        "comment_count": 0,
        "source": source,
        "feed_name": "feed" if source == "bubbles" else "Blog",
        "domain": st_gather.domain_for(url),
        "category": "",
    }


def covered_entry(days_ago: int):
    return {"date": (NOW - dt.timedelta(days=days_ago)).date().isoformat(), "mp3_url": "https://x"}


def test_vote_score_rises_with_votes_and_saturates():
    assert st_gather.vote_score(0) == 0.0
    assert st_gather.vote_score(1) < st_gather.vote_score(10) < st_gather.vote_score(40)
    assert st_gather.vote_score(200) == 1.0  # a runaway post can't drown the field


def test_recency_score_matches_the_window():
    assert st_gather.recency_score(NOW, NOW, 168) == 1.0
    assert st_gather.recency_score(NOW - dt.timedelta(hours=168), NOW, 168) == 0.0
    assert st_gather.recency_score(None, NOW, 168) == 0.3  # unknown date


def test_recent_domains_reads_the_covered_log():
    """The #95 wiring, stated as an equality: the variety penalty's ONLY input is
    the same covered.json the dedup uses. Nothing else has to be kept up to date."""
    covered = {
        "https://a.example/old": covered_entry(3),
        "https://WWW.b.example/x": covered_entry(1),
    }
    assert st_gather.recent_domains(covered, NOW, 21) == {"a.example", "b.example"}


def test_recent_domains_ignores_a_ship_outside_the_window():
    assert st_gather.recent_domains({"https://a.example/old": covered_entry(60)}, NOW, 21) == set()


def test_recent_domains_tolerates_a_malformed_covered_entry():
    """Same no-data-loss posture as covered.json's own pruning: a missing or
    non-ISO date means "no information", never a crash and never a penalty."""
    covered = {"https://a.example/x": {"date": "not-a-date"}, "https://b.example/x": "junk"}
    assert st_gather.recent_domains(covered, NOW, 21) == set()


def test_ranking_prefers_the_more_voted_post():
    """The whole point of this show's curation: a human quality signal that is
    already metadata, rather than a keyword heuristic standing in for one."""
    ranked = st_gather.rank_candidates(
        [cand(url="https://a.example/p", votes=1), cand(url="https://b.example/p", votes=30)],
        covered={},
        now=NOW,
    )
    assert [c["domain"] for c in ranked] == ["b.example", "a.example"]


def test_the_variety_penalty_demotes_a_domain_covered_recently():
    """The test that fails if the penalty is inert (#95).

    Two candidates identical in votes and recency; one blog shipped in last week's
    episode. Drop the penalty term — or feed it from a state file nobody writes —
    and the ranking is unchanged, which is exactly the failure #95 documents.
    """
    cands = [
        cand(url="https://a.example/new", votes=10),
        cand(url="https://b.example/new", votes=10),
    ]
    assert [c["domain"] for c in st_gather.rank_candidates(cands, {}, NOW)] == [
        "a.example",
        "b.example",
    ]
    covered = {"https://a.example/last-week": covered_entry(7)}
    assert [c["domain"] for c in st_gather.rank_candidates(cands, covered, NOW)] == [
        "b.example",
        "a.example",
    ]


def test_the_variety_penalty_expires_with_the_window():
    cands = [
        cand(url="https://a.example/new", votes=10),
        cand(url="https://b.example/new", votes=10),
    ]
    covered = {"https://a.example/ages-ago": covered_entry(90)}
    assert [c["domain"] for c in st_gather.rank_candidates(cands, covered, NOW)] == [
        "a.example",
        "b.example",
    ]


def test_the_module_keeps_no_second_state_file_for_variety():
    """#95's root cause was a second state file whose only writer stopped being the
    path that ships. This module has exactly three paths, and the variety penalty
    reads one that `render.py` rewrites on every successful ship."""
    assert {n for n in dir(st_gather) if n.endswith("_path")} == {
        "config_path",
        "covered_path",
        "dropped_log_path",
    }


def test_the_per_domain_cap_stops_one_blog_owning_the_episode():
    """Vote ranking without a cap hands the episode to whichever blog the community
    happens to be voting up this week."""
    cands = [cand(url=f"https://a.example/{i}", votes=40 - i) for i in range(5)]
    ranked = st_gather.rank_candidates(cands, {}, NOW, per_domain_cap=2)
    assert len(ranked) == 2
    assert [c["url"] for c in ranked] == ["https://a.example/0", "https://a.example/1"]


def test_the_cap_counts_domains_not_feeds():
    """Every bubbles candidate arrives through /feed or /feed/hot, so a cap keyed on
    the feed name would never bind — the blog is the thing that needs limiting."""
    cands = [cand(url=f"https://a.example/{i}", votes=40) for i in range(3)]
    assert len({c["feed_name"] for c in cands}) == 1
    assert len(st_gather.rank_candidates(cands, {}, NOW, per_domain_cap=2)) == 2


def test_opml_posts_survive_a_ranking_built_on_votes_they_cannot_have():
    """The two-source hazard: an OPML blog has no vote signal, so one global sort on
    votes buries every OPML candidate below every bubbles candidate and the second
    source becomes dead weight. Slots are reserved instead."""
    bubbles = [cand(url=f"https://b{i}.example/p", votes=30 - i) for i in range(10)]
    opml = [cand(url=f"https://o{i}.example/p", votes=0, source="opml") for i in range(3)]
    ranked = st_gather.rank_candidates(bubbles + opml, {}, NOW, target=4, buffer=4)
    assert len(ranked) == 8
    assert sum(1 for c in ranked if c["source"] == "opml") == 2  # 25% of 8


def test_the_opml_share_places_its_slots_deterministically():
    bubbles = [cand(url=f"https://b{i}.example/p", votes=30 - i) for i in range(10)]
    opml = [cand(url=f"https://o{i}.example/p", votes=0, source="opml") for i in range(3)]
    ranked = st_gather.rank_candidates(bubbles + opml, {}, NOW, target=4, buffer=4)
    assert [c["source"] for c in ranked] == [
        "bubbles",
        "bubbles",
        "bubbles",
        "opml",
        "bubbles",
        "bubbles",
        "bubbles",
        "opml",
    ]


def test_a_thin_opml_side_does_not_shrink_the_episode():
    """A reserved slot is a floor for the OPML source, never a hole in the running
    order: with nothing to put in it, the slot goes back to the voted pool."""
    bubbles = [cand(url=f"https://b{i}.example/p", votes=30 - i) for i in range(10)]
    ranked = st_gather.rank_candidates(bubbles, {}, NOW, target=4, buffer=4)
    assert len(ranked) == 8
    assert all(c["source"] == "bubbles" for c in ranked)


def test_ranking_is_deterministic():
    cands = [cand(url=f"https://b{i}.example/p", votes=30 - i) for i in range(10)]
    first = st_gather.rank_candidates(cands, {}, NOW)
    assert [c["url"] for c in first] == [
        c["url"] for c in st_gather.rank_candidates(cands, {}, NOW)
    ]


# --------------------------------------------------------------------------
# gather(): both sources, both pools, one payload
# --------------------------------------------------------------------------

DATA = pathlib.Path(__file__).resolve().parent / "data"


def st_config(tmp_path, **overrides):
    cfg = {
        **st_gather.DEFAULT_CONFIG,
        "bubbles_feeds": ["https://bubbles.town/feed"],
        "rapid_fire_feed": "https://bubbles.town/feed/new",
        "opml_files": [],
        "opml_categories": [],
        **overrides,
    }
    return cfg


def test_gather_returns_ranked_posts_and_an_unranked_rapid_fire_pool(tmp_path):
    main_feed = feed(*[entry(url=f"https://b{i}.example/p", votes=str(30 - i)) for i in range(6)])
    new_feed = feed(*[entry(url=f"https://n{i}.example/p", votes="0") for i in range(9)])
    parse = {"https://bubbles.town/feed": main_feed, "https://bubbles.town/feed/new": new_feed}
    out = st_gather.gather(
        st_config(tmp_path, target_post_count=2, buffer=2, rapid_fire_count=3),
        NOW,
        covered={},
        parse=lambda u: parse[u],
    )
    assert [c["url"] for c in out["posts"]] == [f"https://b{i}.example/p" for i in range(4)]
    # "unranked from /feed/new": feed order preserved, capped at rapid_fire_count.
    assert [c["url"] for c in out["rapid_fire"]] == [f"https://n{i}.example/p" for i in range(3)]
    assert out["run_date"] == "2026-08-25"


def test_gather_never_puts_the_same_post_in_both_pools(tmp_path):
    """/feed and /feed/new overlap (9 shared URLs in the capture)."""
    shared = "https://b0.example/p"
    parse = {
        "https://bubbles.town/feed": feed(entry(url=shared, votes="30")),
        "https://bubbles.town/feed/new": feed(entry(url=shared), entry(url="https://n1.example/p")),
    }
    out = st_gather.gather(st_config(tmp_path), NOW, covered={}, parse=lambda u: parse[u])
    assert [c["url"] for c in out["posts"]] == [shared]
    assert [c["url"] for c in out["rapid_fire"]] == ["https://n1.example/p"]


def test_gather_serialises_published_for_the_json_boundary(tmp_path):
    parse = {
        "https://bubbles.town/feed": feed(entry()),
        "https://bubbles.town/feed/new": feed(),
    }
    out = st_gather.gather(st_config(tmp_path), NOW, covered={}, parse=lambda u: parse[u])
    assert out["posts"][0]["published"] == NOW.replace(microsecond=0).isoformat()
    json.dumps(out)  # the payload is the CLI's product; it has to serialise


def test_gather_wires_the_variety_penalty_to_this_shows_covered_log(tmp_path):
    """End-to-end liveness for #95: the covered.json this show excludes URLs with
    is the same map that demotes a blog it shipped last week. There is no separate
    file to fall out of date, and no second wiring step to forget."""
    parse = {
        "https://bubbles.town/feed": feed(
            entry(url="https://a.example/new", votes="10"),
            entry(url="https://b.example/new", votes="10"),
        ),
        "https://bubbles.town/feed/new": feed(),
    }
    cfg = st_config(tmp_path)
    plain = st_gather.gather(cfg, NOW, covered={}, parse=lambda u: parse[u])
    assert [c["domain"] for c in plain["posts"]] == ["a.example", "b.example"]

    covered = {"https://a.example/shipped-last-week": covered_entry(7)}
    penalised = st_gather.gather(cfg, NOW, covered=covered, parse=lambda u: parse[u])
    assert [c["domain"] for c in penalised["posts"]] == ["b.example", "a.example"]


def test_gather_excludes_already_covered_urls_from_both_pools(tmp_path):
    parse = {
        "https://bubbles.town/feed": feed(entry(url="https://a.example/p", votes="30")),
        "https://bubbles.town/feed/new": feed(entry(url="https://n.example/p")),
    }
    covered = {"https://a.example/p": covered_entry(30), "https://n.example/p": covered_entry(30)}
    out = st_gather.gather(st_config(tmp_path), NOW, covered=covered, parse=lambda u: parse[u])
    assert out["posts"] == [] and out["rapid_fire"] == []


def test_gather_reads_the_opml_side_through_the_category_filter(tmp_path):
    opml = write_opml(
        tmp_path,
        '<outline text="Blogs">'
        '<outline type="rss" text="Kept" xmlUrl="https://kept.example/rss" />'
        "</outline>"
        '<outline text="Newsletters">'
        '<outline type="rss" text="Dropped" xmlUrl="https://dropped.example/rss" />'
        "</outline>",
    )
    parse = {
        "https://bubbles.town/feed": feed(),
        "https://bubbles.town/feed/new": feed(),
        "https://kept.example/rss": feed(entry(url="https://kept.example/p")),
        "https://dropped.example/rss": feed(entry(url="https://dropped.example/p")),
    }
    cfg = st_config(tmp_path, opml_files=[str(opml)], opml_categories=["Blogs"])
    out = st_gather.gather(cfg, NOW, covered={}, parse=lambda u: parse[u])
    assert [c["url"] for c in out["posts"]] == ["https://kept.example/p"]
    assert out["posts"][0]["source"] == "opml"


def test_gather_collects_drops_for_the_observability_log(tmp_path):
    def parse(url):
        if url.endswith("/feed"):
            raise RuntimeError("timed out")
        return feed()

    out = st_gather.gather(st_config(tmp_path), NOW, covered={}, parse=parse)
    assert [d["reason"] for d in out["dropped"]] == ["feed_error"]
    assert out["dropped"][0]["run_date"] == "2026-08-25"


# --------------------------------------------------------------------------
# The real 2026-08-25 captures
# --------------------------------------------------------------------------


def test_gather_survives_the_real_bubbles_capture():
    """Driven through the actual fixtures rather than a hand-written feed: the vote
    counts, the https links and the six-hour /feed/new window are all real."""
    feedparser = pytest.importorskip("feedparser")
    parsed = {
        "https://bubbles.town/feed": feedparser.parse((DATA / "bubbles_feed.xml").read_bytes()),
        "https://bubbles.town/feed/hot": feedparser.parse(
            (DATA / "bubbles_feed_hot.xml").read_bytes()
        ),
        "https://bubbles.town/feed/new": feedparser.parse(
            (DATA / "bubbles_feed_new.xml").read_bytes()
        ),
    }
    cfg = {**st_gather.DEFAULT_CONFIG, "opml_files": [], "opml_categories": []}
    out = st_gather.gather(cfg, NOW, covered={}, parse=lambda u: parsed[u])

    assert out["posts"], "the real capture produced no posts"
    assert max(c["votes"] for c in out["posts"]) > 0, "votes did not survive the gather"
    assert all(c["url"].startswith("https://") for c in out["posts"])
    assert all("bubbles.town" not in c["url"] for c in out["posts"])
    # Ranked on votes: the top post outscores the last one on the vote term alone.
    assert out["posts"][0]["votes"] >= out["posts"][-1]["votes"]


def test_the_capture_mirrors_the_post_body_into_summary():
    """Why the cap exists, shown on the real data.

    The committed fixtures are TRIMMED, so they cannot themselves carry a 20 KB
    body — what they can show is the mirroring that makes one reachable: on this
    source `summary` is the post `content`, not a blurb, so the field a gather
    reads for a one-line summary is the article. The cap's behaviour on a
    full-length body is proven synthetically in
    `test_gather_candidates_caps_the_summary_that_is_really_a_post_body`.
    """
    feedparser = pytest.importorskip("feedparser")
    parsed = feedparser.parse((DATA / "bubbles_feed.xml").read_bytes())
    for e in parsed.entries:
        assert e.summary == e.content[0].value
    out = st_gather.gather_candidates([spec()], {}, NOW, 168, parse=lambda u: parsed)
    assert all(len(c["summary"]) <= st_gather.SUMMARY_MAX_CHARS for c in out)


def test_the_shipped_hot_spec_is_exempt_from_the_show_lookback():
    """Recon finding 2, exercised on the real capture.

    The live /feed/hot spanned ~16 weeks and only 12 of 17 entries fell inside a
    168 h window, so a uniform lookback would discard exactly the posts that feed
    exists to surface. The committed fixture is trimmed to entries hours old, so
    the window here is tightened to make the same point against real data: the
    shipped spec keeps everything, an otherwise-identical bounded spec does not.
    """
    feedparser = pytest.importorskip("feedparser")
    hot = feedparser.parse((DATA / "bubbles_feed_hot.xml").read_bytes())
    hot_spec = next(
        s for s in st_gather.feed_specs(st_gather.DEFAULT_CONFIG) if s["url"].endswith("/hot")
    )
    assert hot_spec["lookback_hours"] is None, "the shipped /feed/hot spec must stay unbounded"
    unbounded = st_gather.gather_candidates([hot_spec], {}, NOW, 2, parse=lambda u: hot)
    bounded = st_gather.gather_candidates(
        [{**hot_spec, "lookback_hours": 2}], {}, NOW, 2, parse=lambda u: hot
    )
    assert len(unbounded) == len(hot.entries)
    assert len(bounded) < len(unbounded)


# --------------------------------------------------------------------------
# Config, the drop log, and the CLI
# --------------------------------------------------------------------------


def test_load_covered_tolerates_a_corrupt_dedup_log(tmp_path, monkeypatch):
    monkeypatch.setattr(st_gather, "CONFIG_DIR", tmp_path)
    (tmp_path / "covered.json").write_text("{not json")
    assert st_gather.load_covered() == {}


def test_load_config_dies_on_a_zero_per_domain_cap(tmp_path, monkeypatch):
    """`<= 0` is refused rather than read as "no limit" — the same posture
    render.py's --prune-workdirs takes, for the same reason."""
    monkeypatch.setattr(st_gather, "CONFIG_DIR", tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"per_domain_cap": 0}))
    with pytest.raises(SystemExit):
        st_gather.load_config()


def test_write_dropped_appends_one_line_per_record(tmp_path, monkeypatch):
    monkeypatch.setattr(st_gather, "CONFIG_DIR", tmp_path)
    st_gather.write_dropped([{"url": "a", "reason": "feed_error"}])
    st_gather.write_dropped([{"url": "b", "reason": "bad_link"}])
    lines = (tmp_path / "dropped.jsonl").read_text().strip().splitlines()
    assert [json.loads(x)["url"] for x in lines] == ["a", "b"]


def test_write_dropped_never_raises(tmp_path, monkeypatch):
    """Observability must never be able to sink a run (write_run_log's contract)."""
    monkeypatch.setattr(st_gather, "CONFIG_DIR", tmp_path / "not" / "a" / "dir")
    (tmp_path / "not").write_text("a file, not a directory")
    st_gather.write_dropped([{"url": "a"}])


def test_main_prints_one_json_object_as_its_final_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(st_gather, "CONFIG_DIR", tmp_path)
    (tmp_path / "config.json").write_text(
        json.dumps({"bubbles_feeds": ["https://bubbles.town/feed"], "opml_categories": []})
    )
    parse = {
        "https://bubbles.town/feed": feed(entry(url="https://a.example/p", votes="9")),
        "https://bubbles.town/feed/new": feed(),
    }
    assert st_gather.main(["gather", "--date", "2026-08-25"], parse=lambda u: parse[u]) == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["run_date"] == "2026-08-25"
    assert [c["votes"] for c in payload["posts"]] == [9]


def test_main_rejects_a_malformed_date(tmp_path, monkeypatch):
    monkeypatch.setattr(st_gather, "CONFIG_DIR", tmp_path)
    (tmp_path / "config.json").write_text("{}")
    with pytest.raises(SystemExit):
        st_gather.main(["gather", "--date", "25-08-2026"], parse=lambda u: feed())


def test_no_opml_files_configured_is_not_a_category_error(tmp_path):
    """A bubbles-only config is legitimate, and the shipped default is exactly that
    (`opml_files: []` alongside a non-empty `opml_categories`). The category guard
    must fire on a filter that matched nothing, not on a source nobody enabled."""
    cfg = {**st_gather.DEFAULT_CONFIG, "opml_files": []}
    assert cfg["opml_categories"], "the default carries a category to filter on"
    assert st_gather.opml_specs(cfg) == []


def test_a_configured_opml_that_yields_no_feeds_dies_naming_the_file(tmp_path, capsys):
    """The other end of the same hazard: files ARE configured and produce nothing,
    so the pool is empty for a reason that has nothing to do with categories."""
    empty = write_opml(tmp_path, '<outline text="Blogs" />', name="empty.opml")
    with pytest.raises(SystemExit):
        st_gather.opml_specs({**st_gather.DEFAULT_CONFIG, "opml_files": [str(empty)]})
    assert "empty.opml" in capsys.readouterr().out
