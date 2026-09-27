"""Show Your Work gather layer (spec §4.2). Fixtures in tests/data/syw were
captured 2026-09-26 from the live sources; assertions name what was captured."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

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


def test_normalize_url_strips_a_trailing_index_html():
    # transformer-circuits.pub's feed links `.../nla/index.html`; Redwood links
    # `.../nla/`. Both must be one identity or the check never matches.
    assert (
        g.normalize_url("https://transformer-circuits.pub/2026/nla/index.html")
        == "https://transformer-circuits.pub/2026/nla"
    )
    assert g.normalize_url("https://metr.org/index.html") == "https://metr.org/"
    # Only a whole trailing path segment: a page NAMED like it is left alone.
    assert g.normalize_url("https://a.test/xindex.html") == "https://a.test/xindex.html"


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
    # The feed links `.../index.html`; identity drops it (I6).
    assert items[0].url == (
        "https://transformer-circuits.pub/2026/interference_effectiveness_helpfulness"
    )
    assert items[0].date == "2026-08-21" and items[0].lab == "anthropic"


def test_openai_alignment_unions_rss_with_the_index():
    pages = _pages(
        (g.OPENAI_ALIGNMENT_RSS, "openai-alignment.xml"),
        (g.OPENAI_ALIGNMENT_INDEX, "openai-alignment.html"),
    )
    urls = {i.url for i in g.parse_openai_alignment(pages)}
    assert len(urls) == 20
    # The on-site Metagaming post is on the index only; the RSS omits it (recon §2.1).
    assert "https://alignment.openai.com/metagaming" in urls


def test_openai_alignment_drops_writer_unreadable_openai_com_cross_posts():
    """#245: the writers read only through WebFetch, which gets 403 on every
    openai.com article page, so a planned cross-post is a refused brief or a failed
    week. The exclusion is logged, never silent."""
    pages = _pages(
        (g.OPENAI_ALIGNMENT_RSS, "openai-alignment.xml"),
        (g.OPENAI_ALIGNMENT_INDEX, "openai-alignment.html"),
    )
    urls = {i.url for i in g.parse_openai_alignment(pages)}
    assert not {u for u in urls if urlsplit(u).hostname in {"openai.com", "www.openai.com"}}
    assert "https://openai.com/index/an-alien-mind" not in urls
    assert "https://alignment.openai.com/metagaming" in urls
    rows = [json.loads(ln) for ln in g.dropped_log_path().read_text().splitlines()]
    assert len(rows) == 7
    assert "https://openai.com/index/an-alien-mind" in {r["url"] for r in rows}
    assert all(
        (r["stage"], r["adapter"]) == ("gather", "openai-alignment") and "#245" in r["reason"]
        for r in rows
    )


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
    assert "https://alignment.openai.com/rss-only" in urls and len(urls) == 21


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


def test_a_check_linking_a_directory_matches_a_lead_listed_as_index_html():
    """The captured Redwood post links `transformer-circuits.pub/2026/nla/`; the
    transformer-circuits feed lists the same paper as `.../nla/index.html`."""
    pairs = g.parse_check_feed((DATA / "redwood.xml").read_text(), "redwood")
    [latent] = [
        c
        for c in g.attach_mentions(pairs, _lead_items())
        if "latent-reasoning-architectures" in c.url
    ]
    assert "https://transformer-circuits.pub/2026/nla" in latent.mentions


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
# What assemble wrote to aired.json: everything planned aired here.
AIRED = {
    "feature": "https://a.test/f",
    "briefs": ["https://a.test/b", "https://a.test/c1", "https://a.test/c2"],
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


@pytest.mark.parametrize("value", ['["metr", "metr-typo"]', '"metr"', "[1]"])
def test_load_config_refuses_an_unknown_or_malformed_adapter_list(value):
    # A typo'd name used to enable nothing for that source, silently.
    g.config_path().parent.mkdir(parents=True, exist_ok=True)
    g.config_path().write_text(f'{{"adapters": {value}}}')
    with pytest.raises(g.ConfigError, match="adapters"):
        g.load_config()


def test_load_config_accepts_null_or_known_adapter_names():
    g.config_path().parent.mkdir(parents=True, exist_ok=True)
    g.config_path().write_text('{"adapters": null}')
    assert g.load_config()["adapters"] is None
    g.config_path().write_text('{"adapters": ["metr", "gdm-safety"]}')
    assert g.load_config()["adapters"] == ["metr", "gdm-safety"]


def test_gather_marks_new_items_and_records_first_observation(offline):
    out = g.gather(g.load_config(), "2026-09-26")
    assert out["errors"] == []
    # 142 parsed; alignment.anthropic.com/2025/activation-oracles is listed by both
    # anthropic-alignment and transformer-circuits, and gather dedupes by URL.
    assert len(out["lead"]) == 141
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
    g.seen_path().write_text("{}")  # seeded: the CLI refuses an unseeded host (C1)
    rc = g.main(["gather", "--date", "2026-09-26", "--out", str(tmp_path / "c.json")])
    assert rc == 1
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("GATHER FAILED every lead")


def test_gather_cli_reports_a_missing_config_on_its_line(capsys, tmp_path):
    rc = g.main(["gather", "--date", "2026-09-26", "--out", str(tmp_path / "c.json")])
    assert rc == 1
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("GATHER FAILED")


def test_the_gather_cli_refuses_until_seeded(offline, capsys, tmp_path):
    """C1: an unseeded host would plan from the whole back catalogue."""
    out = tmp_path / "c.json"
    rc = g.main(["gather", "--date", "2026-09-26", "--out", str(out)])
    assert rc == 1
    assert capsys.readouterr().out.strip().splitlines()[-1] == (
        "GATHER FAILED no seen.json — run seed first (SKILL.md Setup)"
    )
    assert not out.exists()
    g.seen_path().write_text("{}")
    assert g.main(["gather", "--date", "2026-09-26", "--out", str(out)]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("GATHER ok lead=141 ")


def test_seed_refuses_when_any_lead_adapter_failed(offline, capsys):
    """C1: a seed missing one lead source leaves that source's whole back catalogue
    unseen, and next week plans from it."""
    offline.add(g.ANTHROPIC_ALIGNMENT_URL)
    assert g.main(["seed", "--date", "2026-09-26"]) == 1
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("SEED FAILED ") and "anthropic-alignment" in last
    assert not g.seen_path().exists()


def test_seed_marks_everything_seen(offline, capsys):
    assert g.main(["seed", "--date", "2026-09-26"]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "SEED ok marked=141"
    out = g.gather(g.load_config(), "2026-09-26")
    assert all(it["seen"] for it in out["lead"])


def test_commit_parses_pretty_printed_render_output():
    assert g.commit(PLAN, RENDER_OK, AIRED) == 4
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
    g.commit(PLAN, RENDER_OK, AIRED)
    g.commit(PLAN, RENDER_OK, AIRED)
    assert len(g.load_features()) == 1


def test_commit_marks_exactly_what_aired():
    """I1: a planned brief that was refused (c2's digest failed, so it never aired)
    and a leftover must return to the pool, not be marked covered forever."""
    aired = {"feature": "https://a.test/f", "briefs": ["https://a.test/b", "https://a.test/c1"]}
    assert g.commit(PLAN, RENDER_OK, aired) == 3
    seen = json.loads(g.seen_path().read_text())
    assert set(seen) == {"https://a.test/f", "https://a.test/b", "https://a.test/c1"}
    assert "https://a.test/c2" not in seen and "https://a.test/left" not in seen
    assert seen["https://a.test/b"]["role"] == "brief"


def test_commit_refuses_an_aired_feature_that_is_not_the_plans():
    with pytest.raises(g.CommitRefused, match="aired.json"):
        g.commit(PLAN, RENDER_OK, {**AIRED, "feature": "https://a.test/other"})
    assert not g.seen_path().exists()


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
        g.commit(PLAN, output, AIRED)
    assert not g.seen_path().exists()
    assert not g.features_path().exists()


def test_a_corrupt_seen_ledger_refuses_rather_than_resetting(offline):
    g.seen_path().write_text("{not json")
    with pytest.raises(g.ConfigError, match="seen.json"):
        g.load_seen()


def test_gather_cli_reports_a_calendar_invalid_date_on_its_line(offline, capsys, tmp_path):
    """#236 item 8: config and seen.json exist, so the refusal can only come from
    _date() — without them the old test passed on the missing config. An invalid
    date that got through would be written into observed.json for every URL."""
    g.seen_path().write_text("{}")
    out = tmp_path / "c.json"
    rc = g.main(["gather", "--date", "2026-13-45", "--out", str(out)])
    assert rc == 1
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("GATHER FAILED")
    assert not out.exists() and not g.observed_path().exists()


def test_commit_cli_reports_a_missing_plan_file_on_its_line(capsys, tmp_path):
    rc = g.main(
        [
            "commit",
            "--plan",
            str(tmp_path / "missing-plan.json"),
            "--render-output",
            str(tmp_path / "missing-render-output.txt"),
        ]
    )
    assert rc == 1
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("COMMIT FAILED")


def test_commit_cli_reads_aired_json_beside_the_plan(capsys, tmp_path):
    (tmp_path / "plan.json").write_text(json.dumps(PLAN))
    (tmp_path / "render.log").write_text(RENDER_OK)
    argv = [
        "commit", "--plan", str(tmp_path / "plan.json"),
        "--render-output", str(tmp_path / "render.log"),
    ]  # fmt: skip
    assert g.main(argv) == 1
    assert (
        capsys.readouterr().out.strip().splitlines()[-1].startswith("COMMIT FAILED no aired.json")
    )
    assert not g.seen_path().exists()
    (tmp_path / "aired.json").write_text(json.dumps(AIRED))
    assert g.main(argv) == 0
    assert capsys.readouterr().out.strip() == "COMMIT ok urls=4"


@pytest.mark.parametrize("name", ["aired.json", "plan.json"])
def test_commit_cli_reports_a_non_object_json_file_on_its_line(capsys, tmp_path, name):
    """#236 item 3: a hand-edited aired.json (or plan.json) holding a list must be
    the COMMIT line, not an AttributeError traceback."""
    (tmp_path / "plan.json").write_text(json.dumps(PLAN))
    (tmp_path / "aired.json").write_text(json.dumps(AIRED))
    (tmp_path / name).write_text('["not", "an", "object"]')
    (tmp_path / "render.log").write_text(RENDER_OK)
    argv = [
        "commit", "--plan", str(tmp_path / "plan.json"),
        "--render-output", str(tmp_path / "render.log"),
    ]  # fmt: skip
    assert g.main(argv) == 1
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("COMMIT FAILED") and name in last
    assert not g.seen_path().exists()
