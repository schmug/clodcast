"""Show Your Work write layer (spec §4.4-§4.6)."""

from __future__ import annotations

import json
import re

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


def test_a_refused_feature_does_not_consume_a_terms_first_use():
    term = {"type": "term", "line": 0, "cue": None, "term": "Reward hacking", "definition": "d"}
    invented = _feature(
        hook={"beats": [term]},
        pushback={"lines": [L("explainer", LONG), L("skeptic", LONG, basis="common sense")]},
    )
    seen: set = set()
    v = w.validate_feature(invented, PLAN, POST, seen)
    assert not v["ok"] and seen == set()
    valid = _feature(hook={"beats": [term]})
    seen2: set = set()
    assert w.validate_feature(valid, PLAN, POST, seen2)["ok"]
    assert "reward hacking" in seen2


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


from pathlib import Path  # noqa: E402

import render  # noqa: E402
import st_write  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
COLD = [
    L(
        "explainer",
        "This is Show Your Work, written and voiced by Claude, a model made by Anthropic. " + LONG,
    )
]
SIGN = [
    L("skeptic", "That is the episode. The homework has been shown; partial credit is pending.")
]


def _assemble(plan=PLAN, briefs=(), **kw):
    scenes = w.validate_feature(_feature(), plan, POST, set())["scenes"]
    return w.assemble_manifest("2026-09-27", w.episode_title(plan), "One line.", plan, COLD, scenes,
                               list(briefs), SIGN, allow_missing_cover=True, **kw)  # fmt: skip


def test_the_assembled_manifest_is_one_render_accepts():
    manifest, beats = _assemble()
    render.validate_manifest(manifest)
    assert [s.get("role") for s in manifest["segments"]][0::6] == ["intro", "outro"]
    assert manifest["segments"][1]["title"] == PLAN["feature"]["title"]
    assert manifest["segments"][1]["source_url"] == PLAN["feature"]["url"]
    assert all(s["source_url"] is None for s in manifest["segments"][2:6])
    assert beats == {"version": 1, "segments": {}}


def test_manifest_lines_carry_only_speaker_and_text():
    manifest, _ = _assemble()
    for seg in manifest["segments"]:
        assert all(set(ln) == {"speaker", "text"} for ln in seg["lines"])


def test_an_anthropic_feature_gets_the_fixed_reminder_and_beats_shift():
    plan = {**PLAN, "feature": {**PLAN["feature"], "lab": "anthropic"}}
    scenes = w.validate_feature(_feature(), plan, POST, set())["scenes"]
    scenes[0]["beats"] = [{"type": "term", "line": 0, "cue": None, "term": "RL", "definition": "d"}]
    manifest, beats = w.assemble_manifest(
        "2026-09-27", "t", "s", plan, COLD, scenes, [], SIGN, allow_missing_cover=True
    )
    hook = manifest["segments"][1]["lines"]
    assert hook[0] == {"speaker": "explainer", "text": w.ANTHROPIC_REMINDER}
    assert beats["segments"]["1"][0]["line"] == 1


def test_briefs_and_the_casebook_link_one_source_each():
    briefs = [
        {
            "kind": "single",
            "item": {"url": "https://a.test/b", "title": "B"},
            "lines": [L("explainer", LONG)],
            "beats": [],
        },
        {"kind": "casebook", "item": None, "lines": [L("explainer", LONG)], "beats": []},
    ]
    manifest, _ = _assemble(briefs=briefs)
    render.validate_manifest(manifest)
    assert [(s["title"], s["source_url"]) for s in manifest["segments"][6:8]] == [
        ("B", "https://a.test/b"),
        (w.CASEBOOK_TITLE, w.CASEBOOK_URL),
    ]


def test_a_live_assembly_needs_the_cover(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "COVER_IMAGE", tmp_path / "missing.jpg")
    scenes = w.validate_feature(_feature(), PLAN, POST, set())["scenes"]
    with pytest.raises(SystemExit):
        w.assemble_manifest("2026-09-27", "t", "s", PLAN, COLD, scenes, [], SIGN)


def test_episode_title():
    assert w.episode_title(PLAN) == (
        "An agent used DNS to reach an external chatbot - week of September 27, 2026"
    )


def _frontier_manifest() -> dict:
    text = (REPO / "skills" / "frontier-commits" / "SKILL.md").read_text()
    return json.loads(re.search(r"```json\n(\{.*?\n\})\n```", text, re.S).group(1))


def test_the_show_cannot_collide_with_any_other_feed():
    """The slug is the permalink AND the isPermaLink guid; the manifest object and
    key prefix hold the feed entry and the mp3/cover. Each must differ from the
    daily show, Frontier Commits, Surface Tension and the sandbox."""
    manifest, _ = _assemble()
    sandbox = json.loads((REPO / "tests" / "data" / "sandbox_manifest.json").read_text())
    st = {"slug_prefix": st_write.SLUG_PREFIX, "r2_manifest_name": st_write.R2_MANIFEST_NAME,
          "r2_key_prefix": st_write.R2_KEY_PREFIX}  # fmt: skip
    others = [_frontier_manifest(), st, sandbox]
    day = "2026-09-27"
    mine = render.slug_for_date(day, render.resolve_slug_prefix(manifest))
    assert mine == "syw-week-of-september-27-2026"
    slugs = {render.slug_for_date(day, render.DEFAULT_SLUG_PREFIX)}
    slugs |= {render.slug_for_date(day, render.resolve_slug_prefix(o)) for o in others}
    assert mine not in slugs
    assert manifest["r2_manifest_name"] not in {"manifest.json"} | {
        o["r2_manifest_name"] for o in others
    }
    assert render._r2_key_prefix(manifest) not in {render._r2_key_prefix(o) for o in others}
    assert manifest["ship_mode"] == render.SHIP_MODE_WEB


def test_cli_accept_and_assemble_round_trip(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(w, "fetch_post_text", lambda url: POST)
    wd = tmp_path / "wd"
    wd.mkdir()
    plan = {
        **PLAN,
        "briefs": [
            {
                "kind": "single",
                "items": [
                    {"url": "https://a.test/b", "title": "B", "lab": "gdm", "kind": "research"}
                ],
            }
        ],
    }
    (wd / "plan.json").write_text(json.dumps(plan))

    def accept(what, obj, *extra):
        out = tmp_path / f"{what}.out"
        out.write_text("chatter\n" + json.dumps(obj) + "\n")
        return w.main(["accept", what, "--workdir", str(wd), "--output", str(out), *extra])

    assert accept("feature", _feature()) == 0
    assert (
        accept("brief", {"ok": True, "lines": [L("explainer", LONG)], "beats": []}, "--index", "0")
        == 0
    )
    assert accept("cold_open", {"ok": True, "lines": COLD}) == 0
    assert accept("sign_off", {"ok": True, "lines": SIGN}) == 0
    assert (
        accept(
            "sign_off", {"ok": True, "lines": [L("skeptic", "Still a robot. See you tomorrow.")]}
        )
        == 2
    )
    capsys.readouterr()
    assert (
        w.main(
            ["assemble", "--workdir", str(wd), "--summary", "One line.", "--allow-missing-cover"]
        )
        == 0
    )
    assert capsys.readouterr().out.strip() == "ASSEMBLE ok segments=8 beats=0"
    render.validate_manifest(json.loads((wd / "manifest.json").read_text()))


def test_cli_fill_feature_uses_accepted_digests(tmp_path, capsys):
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "plan.json").write_text(json.dumps(PLAN))
    url = PLAN["checks"][0]["url"]
    digest = {
        "url": url,
        "claims": ["METR says so"],
        "numbers": [],
        "limitations": [],
        "transcript_excerpts": [],
    }
    out = tmp_path / "d.out"
    out.write_text(json.dumps({"ok": True, **digest}))
    assert (
        w.main(["accept", "digest", "--workdir", str(wd), "--output", str(out), "--url", url]) == 0
    )
    capsys.readouterr()
    assert w.main(["fill", "feature", "--workdir", str(wd)]) == 0
    assert "METR says so" in capsys.readouterr().out


# --- I2: a write is used only for the plan it was accepted under ----------------

F1 = "https://alignment.openai.com/f1"
F2 = "https://alignment.openai.com/f2"
X1 = "https://deepmindsafetyresearch.medium.com/x1"


def _lead(url, kind="research", lab="openai"):
    return {"url": url, "title": url.rsplit("/", 1)[-1].upper(), "lab": lab, "kind": kind,
            "summary": "s"}  # fmt: skip


def _workdir(tmp_path, plan) -> Path:
    wd = tmp_path / "wd"
    (wd / "writes").mkdir(parents=True)
    (wd / "plan.json").write_text(json.dumps(plan))
    return wd


def _put(wd, name, obj):
    (wd / "writes" / name).write_text(json.dumps(obj))


def _brief_write(urls, lab="openai"):
    return {"kind": "single", "item": _lead(urls[0], lab=lab), "urls": urls,
            "lines": [L("explainer", LONG)], "beats": []}  # fmt: skip


def _frames(wd, feature_url):
    scenes = w.validate_feature(_feature(), PLAN, POST, set())["scenes"]
    _put(wd, "feature.json", {"url": feature_url, "scenes": scenes})
    _put(wd, "cold_open.json", {"lines": COLD})
    _put(wd, "sign_off.json", {"lines": SIGN})


def _assemble_cli(wd):
    return w.main(["assemble", "--workdir", str(wd), "--summary", "s", "--allow-missing-cover"])


def test_accept_records_what_each_write_is_for(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "fetch_post_text", lambda url: POST)
    plan = {**PLAN, "briefs": [{"kind": "single", "items": [_lead(X1)]}]}
    wd = _workdir(tmp_path, plan)
    out = tmp_path / "o.txt"
    out.write_text(json.dumps(_feature()))
    assert w.main(["accept", "feature", "--workdir", str(wd), "--output", str(out)]) == 0
    assert json.loads((wd / "writes" / "feature.json").read_text())["url"] == PLAN["feature"]["url"]
    out.write_text(json.dumps({"ok": True, "lines": [L("explainer", LONG)], "beats": []}))
    argv = ["accept", "brief", "--workdir", str(wd), "--output", str(out), "--index", "0"]
    assert w.main(argv) == 0
    assert json.loads((wd / "writes" / "brief_00.json").read_text())["urls"] == [X1]


def test_assemble_skips_writes_left_by_an_earlier_plan(tmp_path, capsys):
    """The review's probe: plan 1 = feature F1, briefs [F2, X1], both accepted;
    `plan --exclude F1` makes plan 2 = feature F2, briefs [X1]; plan 2's brief 0 is
    refused. The episode must not carry F2 as the feature AND as a brief."""
    plan2 = {**PLAN, "feature": _lead(F2), "briefs": [{"kind": "single", "items": [_lead(X1)]}]}
    wd = _workdir(tmp_path, plan2)
    _frames(wd, F2)
    _put(wd, "brief_00.json", _brief_write([F2]))  # plan 1's brief 0
    _put(wd, "brief_01.json", _brief_write([X1]))  # plan 1's brief 1: no such index now
    assert _assemble_cli(wd) == 0
    assert capsys.readouterr().out.strip() == "ASSEMBLE ok segments=7 beats=0"
    urls = [s["source_url"] for s in json.loads((wd / "manifest.json").read_text())["segments"]]
    assert urls.count(F2) == 1 and X1 not in urls


def test_assemble_refuses_a_feature_written_for_another_plan(tmp_path, capsys):
    wd = _workdir(tmp_path, {**PLAN, "feature": _lead(F2)})
    _frames(wd, F1)
    assert _assemble_cli(wd) == 1
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("ASSEMBLE FAILED") and F1 in last
    assert not (wd / "manifest.json").exists()


# --- I1: aired.json names exactly what went into the manifest -------------------


def test_assemble_writes_aired_json_listing_only_what_aired(tmp_path):
    c1, c2 = "https://alignment.openai.com/r/c1", "https://alignment.openai.com/r/c2"
    x2 = "https://deepmindsafetyresearch.medium.com/x2"
    plan = {
        **PLAN,
        "briefs": [
            {"kind": "single", "items": [_lead(X1)]},
            {"kind": "single", "items": [_lead(x2)]},  # refused: no write
            {"kind": "casebook", "items": [_lead(c1, "incident"), _lead(c2, "incident")]},
        ],
    }
    wd = _workdir(tmp_path, plan)
    _frames(wd, PLAN["feature"]["url"])
    _put(wd, "brief_00.json", _brief_write([X1]))
    _put(wd, "brief_02.json", {**_brief_write([c1, c2]), "kind": "casebook", "item": None})
    # c2's digest was refused, so the casebook was written without it.
    w.digest_path(wd, c1).parent.mkdir(parents=True)
    w.digest_path(wd, c1).write_text("{}")
    assert _assemble_cli(wd) == 0
    assert json.loads((wd / "aired.json").read_text()) == {
        "feature": PLAN["feature"]["url"],
        "briefs": [X1, c1],
    }


# --- I5: a casebook with no accepted digests has nothing to be written from ------

CASEBOOK_PLAN = {
    **PLAN,
    "briefs": [
        {
            "kind": "casebook",
            "items": [_lead("https://alignment.openai.com/r/c1", "incident")],
        }
    ],
}


def test_fill_refuses_a_casebook_with_no_digests(tmp_path, capsys):
    wd = _workdir(tmp_path, CASEBOOK_PLAN)
    assert w.main(["fill", "brief", "--workdir", str(wd), "--index", "0"]) == 2
    assert capsys.readouterr().out.strip() == "FILL refused casebook has no digests"


def test_accept_refuses_a_casebook_with_no_digests(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(w, "fetch_post_text", lambda url: POST)
    wd = _workdir(tmp_path, CASEBOOK_PLAN)
    out = tmp_path / "o.txt"
    out.write_text(json.dumps({"ok": True, "lines": [L("explainer", LONG * 2)], "beats": []}))
    argv = ["accept", "brief", "--workdir", str(wd), "--output", str(out), "--index", "0"]
    assert w.main(argv) == 2
    assert capsys.readouterr().out.strip() == "ACCEPT refused casebook has no digests"
    assert not (wd / "writes" / "brief_00.json").exists()
