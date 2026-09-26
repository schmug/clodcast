"""Show Your Work write layer (spec §4.4-§4.6)."""

from __future__ import annotations

import pytest

import syw_script_plan as sp
import syw_write as w

PLAN = {
    "date": "2026-09-27",
    "feature": {
        "url": "https://alignment.openai.com/misalignment-reports/an-agent-used-dns",
        "title": "An agent used DNS to reach an external chatbot",
        "lab": "openai",
        "kind": "incident",
        "summary": "An agent queried a public chatbot through a DNS gap.",
    },
    "briefs": [],
    "checks": [{"url": "https://metr.org/blog/x", "title": "METR on it"}],
    "rotation": sp.rotation("2026-09-27"),
}


def _template(name: str) -> str:
    return (w.PROMPTS_DIR / name).read_text()


@pytest.mark.parametrize(
    "stdout, stderr, outcome",
    [
        ('noise\n{"ok": true, "scenes": []}\n', "", "OK"),
        ('{"ok": false, "reason": "post is a job ad"}', "", "REFUSED"),
        ("", "Error: 401 Unauthorized - invalid x-api-key", "AUTH"),
        ("I'm unable to respond to this request", "", "BLOCKED"),
        ("", "Traceback: boom", "ERROR"),
    ],
)
def test_classify_output(stdout, stderr, outcome):
    assert w.classify_output(stdout, stderr, 0)["outcome"] == outcome


def test_validate_digest():
    url = "https://metr.org/blog/x"
    good = {
        "url": url,
        "claims": ["c"],
        "numbers": [],
        "limitations": [],
        "transcript_excerpts": [],
    }
    assert w.validate_digest(good, url) == (good, "")
    assert w.validate_digest({**good, "url": "https://else"}, url)[0] is None
    assert w.validate_digest({**good, "claims": "c"}, url)[0] is None
    huge = {**good, "claims": ["x" * (w.DIGEST_MAX_CHARS + 1)]}
    assert "over" in w.validate_digest(huge, url)[1]


def test_the_feature_prompt_reads_exactly_one_body():
    tpl = _template("write_feature.md")
    assert tpl.count("<<URL>>") == 1
    filled = w.fill_feature(tpl, PLAN, digests=[])
    assert "<<" not in filled
    assert PLAN["feature"]["url"] in filled
    # Every beat type the validator accepts is offered, and no other.
    for kind in w.BEAT_FIELDS:
        assert f"`{kind}`" in filled
    assert sp.OPENING_MOVES[PLAN["rotation"]["opening_move"]] in filled


def test_the_casebook_prompt_holds_no_body():
    tpl = _template("write_casebook.md")
    assert "<<URL>>" not in tpl
    assert "Do not fetch" in tpl
    digest = {
        "url": "https://a.test/r",
        "claims": ["x"],
        "numbers": [],
        "limitations": [],
        "transcript_excerpts": [],
    }
    filled = w.fill_casebook(tpl, [digest])
    assert "<<" not in filled and "https://a.test/r" in filled


def test_brief_and_digest_prompts_fill_completely():
    it = {
        "url": "https://a.test/p",
        "title": "T",
        "lab": "anthropic",
        "kind": "research",
        "summary": "S",
    }
    assert "<<" not in w.fill_brief(_template("write_brief.md"), it)
    assert "<<" not in w.fill_digest(_template("digest.md"), it)
    assert _template("write_brief.md").count("<<URL>>") == 1
    # The digest template names the URL twice — the page to read, and the value to
    # echo back so validate_digest can match it — but it is still one page.
    assert "https://a.test/p" in w.fill_digest(_template("digest.md"), it)


def test_feature_prompt_without_checks_says_so():
    filled = w.fill_feature(_template("write_feature.md"), {**PLAN, "checks": []}, digests=[])
    assert "No independent source has responded" in filled


def test_beat_rules_cover_exactly_the_beat_types():
    assert set(w.BEAT_RULES) == set(w.BEAT_FIELDS)


def test_the_disclosure_footer_names_claude_and_anthropic():
    assert "Claude" in w.DESCRIPTION_FOOTER and "Anthropic" in w.DESCRIPTION_FOOTER
    assert "Claude" in w.ANTHROPIC_REMINDER and "Anthropic" in w.ANTHROPIC_REMINDER
