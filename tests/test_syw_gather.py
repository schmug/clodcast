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
