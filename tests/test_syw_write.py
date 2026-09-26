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


POST = (
    "In internal deployment, a highly persistent internal model published a researcher’s "
    "GitHub token in the public openai/codex repository. Monitoring flagged it in 15 "
    "minutes; 1,064 runs were reviewed and 12% were affected — a “worm-like” spread. "
    "We note the sample is small."
)
L = lambda s, t, **kw: {"speaker": s, "text": t, **kw}  # noqa: E731
LONG = "The model did something worth explaining carefully to a newcomer. " * 12


def test_verbatim_guard_is_typography_blind():
    lines = [L("explainer", "It published a researcher's GitHub token.")]
    quote = {"type": "quote", "line": 0, "cue": "GitHub token",
             "text": 'a "worm-like" spread', "attribution": "OpenAI"}  # fmt: skip
    kept, why = w.validate_beat(quote, lines, POST, set())
    assert why == "" and kept["cue"] == "GitHub token"
    apostrophe = {**quote, "text": "published a researcher's GitHub token"}
    assert w.validate_beat(apostrophe, lines, POST, set())[1] == ""


@pytest.mark.parametrize(
    "beat, reason",
    [
        ({"type": "quote", "text": "published an API key", "attribution": "x"}, "not verbatim"),
        ({"type": "number", "value": "99", "unit": "%", "label": "x"}, "not in the post"),
        ({"type": "chart", "kind": "bar", "title": "t", "x_label": "x", "y_label": "y",
          "series": [{"label": "a", "points": [["a", 12], ["b", 13]]}]}, "not in the post"),
        ({"type": "transcript", "role": "cot", "text": "x" * 601}, "over"),
        ({"type": "transcript", "role": "narrator", "text": "GitHub token"}, "role"),
        ({"type": "diagram", "nodes": [{"id": "a", "label": "A"}],
          "edges": [{"from": "a", "to": "z"}]}, "edges"),
        ({"type": "sparkle"}, "unknown beat type"),
        ({"type": "quote", "text": "GitHub token", "attribution": "x", "color": "red"},
         "unknown key"),
        ({"type": "quote", "line": 7, "text": "GitHub token", "attribution": "x"}, "out of range"),
    ],
)  # fmt: skip
def test_bad_beats_are_dropped_with_a_reason(beat, reason):
    beat = {"line": 0, "cue": "token", **beat}
    kept, why = w.validate_beat(beat, [L("explainer", "the token")], POST, set())
    assert kept is None and reason in why


def test_numbers_match_as_written_including_thousands_separators():
    lines = [L("explainer", "a thousand runs")]
    for value in ("1,064", 1064, "12", "12%", 15):
        beat = {
            "type": "number",
            "line": 0,
            "cue": "runs",
            "value": value,
            "unit": "",
            "label": "x",
        }
        assert w.validate_beat(beat, lines, POST, set())[1] == "", value
    chart = {
        "type": "chart", "line": 0, "cue": "runs", "kind": "bar", "title": "t",
        "x_label": "x", "y_label": "y",
        "series": [{"label": "a", "points": [["runs", 1064], ["pct", 12]]}],
    }  # fmt: skip
    assert w.validate_beat(chart, lines, POST, set())[1] == ""


def test_a_mismatched_cue_falls_back_rather_than_dropping():
    beat = {"type": "term", "line": 0, "cue": "not in the line", "term": "RL", "definition": "d"}
    kept, why = w.validate_beat(beat, [L("explainer", "about RL")], POST, set())
    assert why == "" and kept["cue"] is None


def test_a_term_is_defined_once_per_episode():
    seen: set = set()
    beat = {"type": "term", "line": 0, "cue": None, "term": "Reward hacking", "definition": "d"}
    assert w.validate_beat(beat, [L("explainer", "x")], POST, seen)[1] == ""
    again = {**beat, "term": "reward hacking"}
    assert "already defined" in w.validate_beat(again, [L("explainer", "x")], POST, seen)[1]


def _feature(**over):
    scenes = []
    for slot in sp.ARC:
        lines = [L(PLAN["rotation"]["first_speaker"], LONG), L("explainer", LONG)]
        if slot == "pushback":
            lines = [L("explainer", LONG), L("skeptic", LONG, basis="post-limitations")]
        scenes.append({"slot": slot, "lines": lines, "beats": []})
    for slot, patch in over.items():
        next(s for s in scenes if s["slot"] == slot).update(patch)
    return {"ok": True, "scenes": scenes}


def test_a_well_formed_feature_validates():
    v = w.validate_feature(_feature(), PLAN, POST, set())
    assert v["ok"], v["problems"]
    assert [s["slot"] for s in v["scenes"]] == list(sp.ARC)


def test_pushback_basis_must_trace_to_the_post_or_a_matched_check():
    invented = _feature(
        pushback={"lines": [L("explainer", LONG), L("skeptic", LONG, basis="common sense")]}
    )
    v = w.validate_feature(invented, PLAN, POST, set())
    assert not v["ok"] and "basis" in v["problems"][0]
    checked = _feature(
        pushback={
            "lines": [L("explainer", LONG), L("skeptic", LONG, basis="https://metr.org/blog/x")]
        }
    )
    assert w.validate_feature(checked, PLAN, POST, set())["ok"]
    unlisted = _feature(
        pushback={
            "lines": [L("explainer", LONG), L("skeptic", LONG, basis="https://metr.org/other")]
        }
    )
    assert not w.validate_feature(unlisted, PLAN, POST, set())["ok"]


def test_feature_shape_refusals():
    wrong_order = _feature()
    wrong_order["scenes"].reverse()
    assert not w.validate_feature(wrong_order, PLAN, POST, set())["ok"]
    short = _feature(method={"lines": [L("explainer", "Too short.")]})
    assert "too short" in w.validate_feature(short, PLAN, POST, set())["problems"][0]
    runaway = _feature(stakes={"lines": [L("explainer", "x" * 4000)]})
    assert "runaway" in w.validate_feature(runaway, PLAN, POST, set())["problems"][0]
    other = "skeptic" if PLAN["rotation"]["first_speaker"] == "explainer" else "explainer"
    wrong_first = _feature(hook={"lines": [L(other, LONG), L("explainer", LONG)]})
    assert "hook must open" in w.validate_feature(wrong_first, PLAN, POST, set())["problems"][0]
    stranger = _feature(finding={"lines": [L("narrator", LONG)]})
    assert "speaker" in w.validate_feature(stranger, PLAN, POST, set())["problems"][0]


def test_a_bad_beat_is_dropped_but_the_feature_ships():
    bad = {"type": "number", "line": 0, "cue": "x", "value": "99", "unit": "", "label": "x"}
    v = w.validate_feature(_feature(finding={"beats": [bad]}), PLAN, POST, set())
    assert v["ok"] and v["dropped_beats"][0]["where"] == "finding"


def test_briefs_allow_at_most_one_skeptic_line():
    ok = {
        "lines": [L("explainer", LONG), L("skeptic", "But is the sample big enough?")],
        "beats": [],
    }
    assert w.validate_brief(ok, False, POST, set())["ok"]
    two = {"lines": [L("skeptic", LONG), L("skeptic", LONG)], "beats": []}
    assert "at most one skeptic" in w.validate_brief(two, False, POST, set())["problems"][0]


def test_frames_require_disclosure_and_refuse_burned_lines():
    good = [
        L("explainer", "Show Your Work is written and voiced by Claude, a model made by Anthropic.")
    ]
    assert w.validate_frame(good, "cold_open") == []
    assert "disclose" in w.validate_frame([L("explainer", "Welcome back.")], "cold_open")[0]
    burned = [L("skeptic", "That's the week. Same weights, different day.")]
    assert "burned" in w.validate_frame(burned, "sign_off")[0]
