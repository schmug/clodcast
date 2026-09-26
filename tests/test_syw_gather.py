"""Show Your Work gather layer (spec §4.2). Fixtures in tests/data/syw were
captured 2026-09-26 from the live sources; assertions name what was captured."""

from __future__ import annotations

from pathlib import Path

import syw_gather as g

DATA = Path(__file__).resolve().parent / "data" / "syw"


def test_normalize_url_collapses_query_fragment_case_and_trailing_slash():
    assert (
        g.normalize_url("HTTPS://Alignment.OpenAI.com/metagaming/?x=1#top")
        == "https://alignment.openai.com/metagaming"
    )
    # Medium appends a tracking query to every feed link.
    assert (
        g.normalize_url("https://deepmindsafetyresearch.medium.com/p-3368?source=rss-55e0------2")
        == "https://deepmindsafetyresearch.medium.com/p-3368"
    )
    assert g.normalize_url("https://metr.org/") == "https://metr.org/"


def test_decode_body_gunzips_an_unrequested_gzip_body():
    raw = (DATA / "gdm-blog.xml.gz").read_bytes()
    assert raw[:2] == b"\x1f\x8b"
    assert g.decode_body(raw).startswith("<?xml")
    assert g.decode_body(b"plain") == "plain"


def test_date_parsers():
    assert g.parse_human_date("Sep 9, 2026") == "2026-09-09"
    assert g.parse_human_date("September 11, 2026") == "2026-09-11"
    assert g.parse_human_date("soon") == ""
    assert g.parse_month("August 2026") == "2026-08-01"
    assert g.parse_month("Augst 2026") == ""


def test_cap_summary_strips_tags_and_caps():
    s = g.cap_summary("<p>" + "word " * 400 + "</p>")
    assert "<" not in s
    assert len(s) <= g.SUMMARY_MAX_CHARS


def test_page_text_skips_script_style_and_decodes_entities():
    raw = (
        "<html><script>var x=1</script><style>p{}</style>"
        "<p>Hello&nbsp;<b>world</b> &amp; co</p></html>"
    )
    assert g.page_text(raw) == "Hello world & co"


def test_hrefs_absolutizes_and_normalizes():
    got = g.hrefs(
        '<a href="/x/">a</a><a href="https://y.org/z?q=1#f">b</a><a>c</a>', "https://base.org/p/"
    )
    assert got == {"https://base.org/x", "https://y.org/z"}


def test_the_config_dir_is_redirected_away_from_real_state():
    real = Path.home() / ".config" / "show-your-work"
    assert g.CONFIG_DIR != real and real not in g.CONFIG_DIR.parents
    assert g.seen_path().parent == g.CONFIG_DIR


def _pages(*pairs: tuple[str, str]) -> dict[str, str]:
    return {url: (DATA / name).read_text() for url, name in pairs}


def test_anthropic_alignment_index_reads_month_groups():
    items = g.parse_anthropic_alignment(
        _pages((g.ANTHROPIC_ALIGNMENT_URL, "anthropic-alignment.html"))
    )
    assert len(items) == 84
    top = items[0]
    assert top.url == "https://alignment.anthropic.com/2026/reward-seeker"
    assert top.title == "Training a Misaligned Reward Seeker"
    assert (top.date, top.date_precision, top.kind, top.lab, top.role) == (
        "2026-08-01",
        "month",
        "research",
        "anthropic",
        "lead",
    )
    assert all(len(i.summary) <= g.SUMMARY_MAX_CHARS for i in items)


def test_anthropic_research_matches_structure_not_hashed_classes():
    items = g.parse_anthropic_research(
        _pages(
            (g.ANTHROPIC_RESEARCH_URLS[0], "anthropic-research-alignment.html"),
            (g.ANTHROPIC_RESEARCH_URLS[1], "anthropic-research-interpretability.html"),
        )
    )
    # Each page lists 5 cards; each card's href also appears on an image link with
    # no heading, which must not produce a second, untitled item.
    assert len(items) == 10 == len({i.url for i in items})
    assert (items[0].url, items[0].date, items[0].date_precision) == (
        "https://www.anthropic.com/research/alignment-assessment-cybersecurity-incidents",
        "2026-09-09",
        "day",
    )
    assert all(i.title for i in items)


def test_transformer_circuits_feed():
    items = g.parse_transformer_circuits(
        _pages((g.TRANSFORMER_CIRCUITS_URL, "transformer-circuits.xml"))
    )
    assert len(items) == 12
    assert items[0].url == (
        "https://transformer-circuits.pub/2026/interference_effectiveness_helpfulness/index.html"
    )
    assert items[0].date == "2026-08-21" and items[0].lab == "anthropic"


def test_openai_alignment_unions_rss_with_the_index():
    pages = _pages(
        (g.OPENAI_ALIGNMENT_RSS, "openai-alignment.xml"),
        (g.OPENAI_ALIGNMENT_INDEX, "openai-alignment.html"),
    )
    urls = {i.url for i in g.parse_openai_alignment(pages)}
    assert len(urls) == 27
    # openai.com cross-posts are on the index only (the RSS omits them) …
    assert "https://openai.com/index/an-alien-mind" in urls
    # … and so is the on-site Metagaming post (recon §2.1).
    assert "https://alignment.openai.com/metagaming" in urls


def test_openai_alignment_keeps_an_rss_only_post():
    pages = _pages(
        (g.OPENAI_ALIGNMENT_RSS, "openai-alignment.xml"),
        (g.OPENAI_ALIGNMENT_INDEX, "openai-alignment.html"),
    )
    extra = (
        "<item><title>RSS only</title><link>https://alignment.openai.com/rss-only/</link>"
        "<pubDate>Mon, 21 Sep 2026 08:00:00 -0700</pubDate></item>"
    )
    # Insert before </channel>: the captured feed has a literal "<item>" inside an XML
    # comment near the top, so splicing at the first "<item>" would land in the comment.
    full_extra = extra + "</channel>"
    pages[g.OPENAI_ALIGNMENT_RSS] = pages[g.OPENAI_ALIGNMENT_RSS].replace(
        "</channel>", full_extra, 1
    )
    urls = {i.url for i in g.parse_openai_alignment(pages)}
    assert "https://alignment.openai.com/rss-only" in urls and len(urls) == 28


def test_openai_misalignment_reports_and_notices():
    items = g.parse_openai_misalignment(
        _pages((g.OPENAI_MISALIGNMENT_URL, "openai-misalignment.html"))
    )
    kinds = [i.kind for i in items]
    assert kinds.count("incident") == 9 and kinds.count("notice") == 3
    top = items[0]
    assert top.url == (
        "https://alignment.openai.com/misalignment-reports/self-replicating-prompt-injections-exist"
    )
    assert (top.date, top.title) == ("2026-09-25", "Self-replicating prompt injections exist")


def test_notices_keep_distinct_fragment_identities():
    items = g.parse_openai_misalignment(
        _pages((g.OPENAI_MISALIGNMENT_URL, "openai-misalignment.html"))
    )
    notices = {i.url: i for i in items if i.kind == "notice"}
    assert set(notices) == {
        "https://alignment.openai.com/misalignment-reports#notice-rubygems",
        "https://alignment.openai.com/misalignment-reports#notice-dsewiki",
        "https://alignment.openai.com/misalignment-reports#notice-hugging-face",
    }
    assert notices["https://alignment.openai.com/misalignment-reports#notice-rubygems"].date == (
        "2026-09-11"
    )


def test_gdm_keeps_medium_and_keyword_matched_blog_posts_only():
    pages = _pages((g.GDM_MEDIUM_URL, "gdm-medium.xml"), (g.GDM_BLOG_URL, "gdm-blog.xml"))
    items = g.parse_gdm(pages)
    # The captured 25 blog posts are all product/science posts; none is a safety post.
    assert len(items) == 4
    assert all(i.url.startswith("https://deepmindsafetyresearch.medium.com/") for i in items)
    assert all("?" not in i.url for i in items)
    feed = (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>b</title>'
        "<item><title>Strengthening our Frontier Safety Framework</title>"
        "<link>https://deepmind.google/blog/fsf/</link></item>"
        "<item><title>Gemini makes better pancakes</title>"
        "<link>https://deepmind.google/blog/pancakes/</link></item>"
        "</channel></rss>"
    )
    pages[g.GDM_BLOG_URL] = feed
    urls = {i.url for i in g.parse_gdm(pages)}
    assert "https://deepmind.google/blog/fsf" in urls
    assert "https://deepmind.google/blog/pancakes" not in urls


def _lead_items() -> list:
    pages = _pages(
        (g.ANTHROPIC_ALIGNMENT_URL, "anthropic-alignment.html"),
        (g.ANTHROPIC_RESEARCH_URLS[0], "anthropic-research-alignment.html"),
        (g.ANTHROPIC_RESEARCH_URLS[1], "anthropic-research-interpretability.html"),
        (g.TRANSFORMER_CIRCUITS_URL, "transformer-circuits.xml"),
        (g.OPENAI_ALIGNMENT_RSS, "openai-alignment.xml"),
        (g.OPENAI_ALIGNMENT_INDEX, "openai-alignment.html"),
    )
    return (
        g.parse_anthropic_alignment(pages)
        + g.parse_anthropic_research(pages)
        + g.parse_transformer_circuits(pages)
        + g.parse_openai_alignment(pages)
    )


def test_metr_translations_are_not_separate_checks():
    pairs = g.parse_check_feed((DATA / "metr.xml").read_text(), "metr")
    urls = [item.url for item, _ in pairs]
    assert len(urls) == 4
    assert not any("/zh-Hans/" in u or "/es/" in u for u in urls)
    assert all(item.role == "check" and item.lab == "metr" for item, _ in pairs)


def test_mentions_come_from_links_in_the_check_content():
    pairs = g.parse_check_feed((DATA / "redwood.xml").read_text(), "redwood")
    pairs += g.parse_check_feed((DATA / "alignment-forum.xml").read_text(), "alignment-forum")
    checks = {c.title[:30]: c for c in g.attach_mentions(pairs, _lead_items())}
    redwood = next(c for t, c in checks.items() if t.startswith("Latent reasoning"))
    assert (
        "https://www.anthropic.com/research/alignment-assessment-cybersecurity-incidents"
        in redwood.mentions
    )
    af = next(c for t, c in checks.items() if t.startswith("Four LLM loss"))
    assert af.mentions == ["https://alignment.anthropic.com/2026/psm"]


def test_mentions_match_a_verbatim_title_without_a_link():
    lead = [
        g.Item(
            "https://lab.test/p",
            "s",
            "anthropic",
            "research",
            "Training a Misaligned Reward Seeker",
            "",
            "",
            "day",
            "lead",
        ),
        g.Item(
            "https://lab.test/q",
            "s",
            "anthropic",
            "research",
            "Teaching Claude Why",
            "",
            "",
            "day",
            "lead",
        ),
    ]
    check = g.Item(
        "https://metr.org/x", "metr", "metr", "research", "t", "", "2026-09-20", "day", "check"
    )
    content = (
        "<p>Anthropic's <em>Training a misaligned reward-seeker</em> post "
        "and teaching Claude why.</p>"
    )
    [out] = g.attach_mentions([(check, content)], lead)
    # 5 words: matched despite case and punctuation. 3 words: too short to trust.
    assert out.mentions == ["https://lab.test/p"]


def test_check_items_do_not_carry_their_content():
    pairs = g.parse_check_feed((DATA / "redwood.xml").read_text(), "redwood")
    [first_check, *_] = g.attach_mentions(pairs, [])
    assert set(first_check.to_dict()) == {
        "url", "source", "lab", "kind", "title", "summary",
        "date", "date_precision", "role", "mentions",
    }  # fmt: skip
    assert len(first_check.summary) <= g.SUMMARY_MAX_CHARS


def test_registry_has_six_lead_and_four_check_adapters():
    roles = [a.role for a in g.ADAPTERS]
    assert roles.count("lead") == 6 and roles.count("check") == 4
    assert len({a.name for a in g.ADAPTERS}) == 10
    assert all(a.parse is not None for a in g.ADAPTERS if a.role == "lead")
    assert all(a.check_source for a in g.ADAPTERS if a.role == "check")
