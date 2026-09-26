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


import json  # noqa: E402

import pytest  # noqa: E402

FIXTURE_FOR_URL = {
    g.ANTHROPIC_ALIGNMENT_URL: "anthropic-alignment.html",
    g.ANTHROPIC_RESEARCH_URLS[0]: "anthropic-research-alignment.html",
    g.ANTHROPIC_RESEARCH_URLS[1]: "anthropic-research-interpretability.html",
    g.TRANSFORMER_CIRCUITS_URL: "transformer-circuits.xml",
    g.OPENAI_ALIGNMENT_RSS: "openai-alignment.xml",
    g.OPENAI_ALIGNMENT_INDEX: "openai-alignment.html",
    g.OPENAI_MISALIGNMENT_URL: "openai-misalignment.html",
    g.GDM_MEDIUM_URL: "gdm-medium.xml",
    g.GDM_BLOG_URL: "gdm-blog.xml",
    g.CHECK_FEEDS["metr"]: "metr.xml",
    g.CHECK_FEEDS["redwood"]: "redwood.xml",
    g.CHECK_FEEDS["goodfire"]: "goodfire.xml",
    g.CHECK_FEEDS["alignment-forum"]: "alignment-forum.xml",
}


@pytest.fixture
def offline(monkeypatch):
    """Serve every source from its fixture; `broken` names URLs that raise."""
    broken: set[str] = set()

    def fake_fetch(url: str) -> str:
        if url in broken:
            raise OSError(f"boom {url}")
        return (DATA / FIXTURE_FOR_URL[url]).read_text()

    monkeypatch.setattr(g, "fetch_text", fake_fetch)
    g.config_path().parent.mkdir(parents=True, exist_ok=True)
    g.config_path().write_text("{}")
    return broken


RENDER_OK = """[render] publishing…
{
  "status": "web-ready",
  "mp3_url": "https://clodcast.cortech.online/show-your-work/syw-week-of-september-27-2026.mp3",
  "loudnorm": {"input_i": -24.1},
  "r2_status": "published",
  "resumed": false
}
"""
PLAN = {
    "date": "2026-09-27",
    "feature": {"url": "https://a.test/f", "lab": "openai", "kind": "incident"},
    "briefs": [
        {"kind": "single", "items": [{"url": "https://a.test/b"}]},
        {"kind": "casebook", "items": [{"url": "https://a.test/c1"}, {"url": "https://a.test/c2"}]},
    ],
    "leftover": ["https://a.test/left"],
}


def test_load_config_refuses_missing_and_unknown_keys():
    with pytest.raises(g.ConfigError, match="missing"):
        g.load_config()
    g.config_path().parent.mkdir(parents=True, exist_ok=True)
    g.config_path().write_text('{"max_brief": 3}')
    with pytest.raises(g.ConfigError, match="unknown"):
        g.load_config()
    g.config_path().write_text('{"max_briefs": 3}')
    assert g.load_config()["max_briefs"] == 3


def test_gather_marks_new_items_and_records_first_observation(offline):
    out = g.gather(g.load_config(), "2026-09-26")
    assert out["errors"] == []
    # 149 parsed; alignment.anthropic.com/2025/activation-oracles is listed by both
    # anthropic-alignment and transformer-circuits, and gather dedupes by URL.
    assert len(out["lead"]) == 148
    assert not any(it["seen"] for it in out["lead"])
    assert {it["first_observed"] for it in out["lead"]} == {"2026-09-26"}
    # A later gather never moves a first observation forward.
    out2 = g.gather(g.load_config(), "2026-10-03")
    assert {it["first_observed"] for it in out2["lead"]} == {"2026-09-26"}
    assert any(c["mentions"] for c in out["check"])


def test_a_failing_adapter_is_isolated_and_logged(offline):
    offline.add(g.OPENAI_MISALIGNMENT_URL)
    out = g.gather(g.load_config(), "2026-09-26")
    assert [e["adapter"] for e in out["errors"]] == ["openai-misalignment"]
    assert not any(it["source"] == "openai-misalignment" for it in out["lead"])
    rows = [json.loads(ln) for ln in g.dropped_log_path().read_text().splitlines()]
    assert rows[-1]["adapter"] == "openai-misalignment"


def test_an_adapter_that_parses_zero_items_has_failed(monkeypatch, offline):
    real = g.fetch_text

    def changed_markup(url):
        return (
            "<html><body><p>redesigned</p></body></html>"
            if url == g.ANTHROPIC_ALIGNMENT_URL
            else real(url)
        )

    monkeypatch.setattr(g, "fetch_text", changed_markup)
    out = g.gather(g.load_config(), "2026-09-26")
    [err] = out["errors"]
    assert err["adapter"] == "anthropic-alignment" and "zero items" in err["error"]


def test_gather_fails_when_every_lead_adapter_fails(offline, capsys, tmp_path):
    offline.update(u for u in FIXTURE_FOR_URL if u not in g.CHECK_FEEDS.values())
    rc = g.main(["gather", "--date", "2026-09-26", "--out", str(tmp_path / "c.json")])
    assert rc == 1
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("GATHER FAILED every lead")


def test_gather_cli_reports_a_missing_config_on_its_line(capsys, tmp_path):
    rc = g.main(["gather", "--date", "2026-09-26", "--out", str(tmp_path / "c.json")])
    assert rc == 1
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("GATHER FAILED")


def test_seed_marks_everything_seen(offline, capsys):
    assert g.main(["seed", "--date", "2026-09-26"]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "SEED ok marked=148"
    out = g.gather(g.load_config(), "2026-09-26")
    assert all(it["seen"] for it in out["lead"])


def test_commit_parses_pretty_printed_render_output():
    assert g.commit(PLAN, RENDER_OK) == 4
    seen = json.loads(g.seen_path().read_text())
    assert set(seen) == {
        "https://a.test/f",
        "https://a.test/b",
        "https://a.test/c1",
        "https://a.test/c2",
    }
    assert seen["https://a.test/f"] == {"date": "2026-09-27", "role": "feature"}
    [row] = g.load_features()
    assert (row["feature_url"], row["lab"], row["kind"]) == (
        "https://a.test/f",
        "openai",
        "incident",
    )


def test_commit_is_idempotent():
    g.commit(PLAN, RENDER_OK)
    g.commit(PLAN, RENDER_OK)
    assert len(g.load_features()) == 1


@pytest.mark.parametrize(
    "output, why",
    [
        (RENDER_OK.replace('"published"', '"failed"'), "r2_status"),
        (RENDER_OK.replace('"web-ready"', '"dry-run"'), "status"),
        ("[render] error: TTS died\n", "no JSON"),
    ],
)
def test_commit_refuses_a_failed_publish(output, why):
    with pytest.raises(g.CommitRefused, match=why):
        g.commit(PLAN, output)
    assert not g.seen_path().exists()
    assert not g.features_path().exists()


def test_a_corrupt_seen_ledger_refuses_rather_than_resetting(offline):
    g.seen_path().write_text("{not json")
    with pytest.raises(g.ConfigError, match="seen.json"):
        g.load_seen()
