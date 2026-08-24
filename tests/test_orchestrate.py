# tests/test_orchestrate.py
from __future__ import annotations

import datetime as dt
import inspect
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import orchestrate

NOW = dt.datetime(2026, 6, 4, 12, 0, tzinfo=dt.timezone.utc)


def test_extract_last_json_single_line():
    assert orchestrate.extract_last_json('noise\n{"ok": true, "segment": "x"}') == {
        "ok": True,
        "segment": "x",
    }


def test_extract_last_json_multiline_fallback():
    out = orchestrate.extract_last_json('pre\n{\n  "ok": false,\n  "reason": "no"\n}\n')
    assert out == {"ok": False, "reason": "no"}


def test_extract_last_json_none_when_absent():
    assert orchestrate.extract_last_json("just prose, no json") is None
    assert orchestrate.extract_last_json("") is None


def test_extract_last_json_indented_with_nested_object():
    """render.py prints its real result via json.dumps(indent=2), and that object nests
    a `loudnorm` sub-object. The fallback must return the OUTER object — regression: the
    old rfind('{') fallback grabbed the inner loudnorm brace, yielding a malformed span
    that parsed to None and made a successful real ship report FAILED."""
    result = {
        "status": "ready",
        "episode_uri": "spotify:episode:4zuLE5DOQlHvJylHXOQn2z",
        "duration_s": 412.3,
        "loudnorm": {"input_i": -22.5, "output_i": -24.0, "input_tp": -3.1},
        "r2_status": "published",
        "resumed": False,
    }
    assert orchestrate.extract_last_json(json.dumps(result, indent=2)) == result


def test_extract_last_json_returns_last_of_several_nested():
    """When several pretty-printed objects (each with a nested sub-object) appear,
    return the LAST top-level object, not an earlier one or an inner brace."""
    first = json.dumps({"status": "old", "loudnorm": {"input_i": -1.0}}, indent=2)
    second = json.dumps({"status": "new", "loudnorm": {"input_i": -2.0}}, indent=2)
    out = orchestrate.extract_last_json(f"log line\n{first}\nmore log\n{second}\n")
    assert out == {"status": "new", "loudnorm": {"input_i": -2.0}}


def test_parse_opml(tmp_path):
    opml = tmp_path / "feeds.opml"
    opml.write_text(
        '<?xml version="1.0"?><opml><body>'
        '<outline text="Group">'
        '<outline type="rss" text="Feed A" xmlUrl="https://a.example/rss" category="/x" />'
        '<outline type="rss" title="Feed B" xmlUrl="https://b.example/rss" />'
        '<outline text="Not a feed" />'
        "</outline></body></opml>"
    )
    feeds = orchestrate.parse_opml(opml)
    assert feeds == [
        {"feed_name": "Feed A", "xml_url": "https://a.example/rss", "category": "/x"},
        {"feed_name": "Feed B", "xml_url": "https://b.example/rss", "category": ""},
    ]


def test_parse_opml_missing_file_returns_empty(tmp_path):
    assert orchestrate.parse_opml(tmp_path / "nope.opml") == []


def test_parse_opml_rejects_entity_expansion(tmp_path):
    # defusedxml forbids DTD/entity definitions -> parse_opml logs and returns []
    evil = tmp_path / "evil.opml"
    evil.write_text(
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "AAAA">]>'
        '<opml><body><outline type="rss" text="&a;" xmlUrl="https://a/rss"/></body></opml>'
    )
    assert orchestrate.parse_opml(evil) == []


def test_source_tier_score():
    assert orchestrate.source_tier_score("Simon Willison") == 1.0
    assert orchestrate.source_tier_score("Hacker News") == 0.2
    assert orchestrate.source_tier_score("Some Unknown Feed") == 0.5  # DEFAULT_TIER


def test_recency_score():
    assert orchestrate.recency_score(NOW, NOW, 24) == 1.0  # brand new
    old = NOW - dt.timedelta(hours=24)
    assert orchestrate.recency_score(old, NOW, 24) == 0.0  # window edge
    assert orchestrate.recency_score(None, NOW, 24) == 0.3  # unknown date


def test_concreteness_score():
    assert orchestrate.concreteness_score("Patch for CVE-2026-1234", "") == 0.2
    assert orchestrate.concreteness_score("A vague think piece", "no specifics") == 0.0


def test_variety_penalty():
    usage = {"Feed A": "2026-06-03"}  # used yesterday
    assert orchestrate.variety_penalty("Feed A", usage, NOW) == orchestrate.VARIETY_PENALTY
    assert orchestrate.variety_penalty("Feed A", {"Feed A": "2026-05-01"}, NOW) == 0.0
    assert orchestrate.variety_penalty("Feed Z", usage, NOW) == 0.0


def _cand(feed, title="t", summary="", published=NOW):
    return {
        "feed_name": feed,
        "title": title,
        "summary": summary,
        "published": published,
        "url": f"https://x/{feed}/{title}",
        "category": "",
    }


def test_rank_orders_by_score_and_caps_per_feed():
    cands = [
        _cand("Hacker News", "agg"),  # tier 3, low
        _cand("Simon Willison", "orig CVE-2026-1"),  # tier 1 + concrete, high
        _cand("Ars Technica", "news 4.2"),  # tier 2 + concrete
    ]
    ranked = orchestrate.rank_candidates(cands, {}, NOW, 24, target=2, buffer=0)
    assert [c["feed_name"] for c in ranked] == ["Simon Willison", "Ars Technica"]


def test_rank_per_feed_cap():
    cands = [_cand("Ars Technica", f"n{i}") for i in range(5)]
    ranked = orchestrate.rank_candidates(cands, {}, NOW, 24, target=10, buffer=0, per_feed_cap=2)
    assert len(ranked) == 2  # capped to 2 from the same feed


def test_gather_filters_lookback_dedup_and_clean(tmp_path):
    opml = tmp_path / "f.opml"
    opml.write_text(
        '<opml><body><outline type="rss" text="Feed A" xmlUrl="https://a/rss"/></body></opml>'
    )
    fresh = (2026, 6, 4, 9, 0, 0, 0, 0, 0)  # 3h before NOW
    stale = (2026, 6, 2, 9, 0, 0, 0, 0, 0)  # >24h before NOW

    def fake_parse(url):
        return {
            "entries": [
                {
                    "title": "Fresh <b>x</b>",
                    "link": "https://a/1",
                    "summary": "<p>body</p>",
                    "published_parsed": fresh,
                },
                {
                    "title": "Stale",
                    "link": "https://a/2",
                    "summary": "old",
                    "published_parsed": stale,
                },
                {
                    "title": "Dup",
                    "link": "https://a/covered",
                    "summary": "s",
                    "published_parsed": fresh,
                },
            ]
        }

    out = orchestrate.gather_candidates(
        [str(opml)], 24, {"https://a/covered": {}}, NOW, parse=fake_parse
    )
    assert len(out) == 1
    assert out[0]["url"] == "https://a/1"
    assert out[0]["title"] == "Fresh x"  # tags stripped
    assert out[0]["summary"] == "body"
    assert out[0]["feed_name"] == "Feed A"


def test_gather_feed_exception_is_skipped(tmp_path):
    opml = tmp_path / "f.opml"
    opml.write_text(
        '<opml><body><outline type="rss" text="A" xmlUrl="https://a/rss"/></body></opml>'
    )

    def boom(url):
        raise OSError("timeout")

    assert orchestrate.gather_candidates([str(opml)], 24, {}, NOW, parse=boom) == []


def test_gather_drops_non_http_links(tmp_path):
    opml = tmp_path / "f.opml"
    opml.write_text(
        '<opml><body><outline type="rss" text="Feed A" xmlUrl="https://a/rss"/></body></opml>'
    )
    fresh = (2026, 6, 4, 9, 0, 0, 0, 0, 0)

    def fake_parse(url):
        entries = [
            {"title": "Rel", "link": "/x/1", "summary": "s", "published_parsed": fresh},
            {"title": "Mail", "link": "mailto:a@b", "summary": "s", "published_parsed": fresh},
            {"title": "Good", "link": "https://a/ok", "summary": "s", "published_parsed": fresh},
        ]
        return {"entries": entries}

    out = orchestrate.gather_candidates([str(opml)], 24, {}, NOW, parse=fake_parse)
    assert [c["url"] for c in out] == ["https://a/ok"]  # non-http links dropped at gather


def test_classify_ok():
    seg = "x" * 600
    out = json.dumps({"ok": True, "segment": seg, "source_url": "u"})
    r = orchestrate.classify_output(out, "", 0)
    assert r["outcome"] == "OK" and r["segment"] == seg


def test_classify_short_segment_is_refused():
    # Too-short (sub-MIN_SEGMENT_CHARS) segment drops the one item rather than
    # shipping a stub chapter. Editorial floor, not a platform limit.
    out = json.dumps({"ok": True, "segment": "x" * 200})
    r = orchestrate.classify_output(out, "", 0)
    assert r["outcome"] == "REFUSED" and "too short" in r["detail"]


def test_classify_refused():
    r = orchestrate.classify_output('{"ok": false, "reason": "not news"}', "", 0)
    assert r["outcome"] == "REFUSED" and "not news" in r["detail"]


def test_classify_blocked_on_policy_marker():
    r = orchestrate.classify_output("", "API Error ... violative cyber content ... Usage Policy", 1)
    assert r["outcome"] == "BLOCKED"


def test_classify_error_when_garbage():
    r = orchestrate.classify_output("blah no json", "", 1)
    assert r["outcome"] == "ERROR"


def test_classify_auth_failure_is_distinct():
    # Under the scheduled-task harness a child `claude -p` starts with NO credentials and
    # prints a 401 auth error with no parseable JSON. That's SYSTEMIC (every item fails the
    # same way), not a per-item error — classify it distinctly so the run can fail fast.
    stderr = (
        'API Error: 401 {"type":"error","error":{"type":"authentication_error",'
        '"message":"Invalid authentication credentials"}}'
    )
    assert orchestrate.classify_output("", stderr, 1)["outcome"] == "AUTH"


def test_classify_transient_errors_stay_error():
    # Rate-limit / overload / connection failures are NOT auth — they must stay ERROR so a
    # bad-feed night never misfires the credentials diagnostic.
    for msg in ("API Error: 429 rate_limit_error", "Overloaded (529)", "connection error"):
        assert orchestrate.classify_output("", msg, 1)["outcome"] == "ERROR"


def test_classify_ok_requires_nonempty_segment():
    r = orchestrate.classify_output('{"ok": true, "segment": "   "}', "", 0)
    assert r["outcome"] == "ERROR"  # empty segment is not a usable success


ITEM = {"title": "T", "url": "https://x/1", "feed_name": "Feed A"}
TPL = "title=<<TITLE>> url=<<URL>> feed=<<FEED>>"


def test_summarize_item_ok():
    captured = {}

    def runner(cmd, **kw):
        captured["cmd"] = cmd
        return SimpleNamespace(
            stdout=json.dumps({"ok": True, "segment": "x" * 600, "source_url": "ignored"}),
            stderr="",
            returncode=0,
        )

    r = orchestrate.summarize_item(ITEM, TPL, runner=runner)
    assert r["outcome"] == "OK"
    assert r["source_url"] == "https://x/1"  # forced to the item url
    assert r["feed_name"] == "Feed A" and r["url"] == "https://x/1"
    assert "title=T url=https://x/1 feed=Feed A" in captured["cmd"][2]  # template filled


def test_summarize_item_timeout():
    def runner(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    r = orchestrate.summarize_item(ITEM, TPL, timeout=1, runner=runner)
    assert r["outcome"] == "TIMEOUT"


def test_fan_out_keeps_survivors_in_order_and_logs_drops():
    ranked = [
        {"title": "A", "url": "u/a", "feed_name": "F1"},
        {"title": "B", "url": "u/b", "feed_name": "F2"},  # will be blocked
        {"title": "C", "url": "u/c", "feed_name": "F3"},
    ]

    def fake_summarize(item, tpl, **kw):
        base = orchestrate._drop_fields(item)
        if item["title"] == "B":
            return {
                **base,
                "outcome": "BLOCKED",
                "segment": None,
                "source_url": None,
                "detail": "usage-policy classifier",
            }
        return {
            **base,
            "outcome": "OK",
            "segment": f"seg-{item['title']}",
            "source_url": item["url"],
            "detail": "",
        }

    survivors, dropped = orchestrate.fan_out(
        ranked, "tpl", target=10, concurrency=2, summarize=fake_summarize
    )
    assert [s["title"] for s in survivors] == ["A", "C"]
    assert survivors[0]["feed_name"] == "F1"
    assert len(dropped) == 1 and dropped[0]["reason"] == "blocked" and dropped[0]["url"] == "u/b"


def test_fan_out_tags_auth_drop_reason():
    # Lock the classify->fan_out seam: an AUTH outcome must surface as reason "auth" so
    # main()'s fail-fast diagnostic (which keys on d["reason"] == "auth") actually fires.
    ranked = [{"title": "A", "url": "u/a", "feed_name": "F"}]

    def auth_summarize(item, tpl, **kw):
        return {
            **orchestrate._drop_fields(item),
            "outcome": "AUTH",
            "segment": None,
            "source_url": None,
            "detail": "401 / no usable credentials",
        }

    survivors, dropped = orchestrate.fan_out(ranked, "tpl", target=10, summarize=auth_summarize)
    assert survivors == []
    assert len(dropped) == 1 and dropped[0]["reason"] == "auth"


def test_fan_out_respects_target_cap():
    ranked = [{"title": f"T{i}", "url": f"u/{i}", "feed_name": "F"} for i in range(5)]

    def ok(item, tpl, **kw):
        return {
            **orchestrate._drop_fields(item),
            "outcome": "OK",
            "segment": "s",
            "source_url": item["url"],
            "detail": "",
        }

    survivors, dropped = orchestrate.fan_out(ranked, "tpl", target=3, summarize=ok)
    assert len(survivors) == 3 and dropped == []  # extras beyond target are unused, not dropped


def test_fallback_intro_outro_pluralization():
    one = orchestrate.fallback_intro_outro("June 4, 2026", 1)
    assert "1 story today" in one["intro"]
    many = orchestrate.fallback_intro_outro("June 4, 2026", 3)
    assert "3 stories today" in many["intro"]
    assert many["outro"] and many["summary"]


def test_make_intro_outro_uses_llm_json():
    def runner(cmd, **kw):
        return SimpleNamespace(
            stdout='{"intro": "I", "outro": "O", "summary": "S"}', stderr="", returncode=0
        )

    out = orchestrate.make_intro_outro(["A", "B"], "June 4, 2026", runner=runner)
    # `topics` (the episode title's material, #139) is always present; a reply that
    # omits it yields [] rather than a missing key, so callers never have to guard.
    assert out == {"intro": "I", "outro": "O", "summary": "S", "topics": []}


def test_make_intro_outro_falls_back_on_garbage():
    def runner(cmd, **kw):
        return SimpleNamespace(stdout="no json here", stderr="", returncode=0)

    out = orchestrate.make_intro_outro(["A", "B"], "June 4, 2026", runner=runner)
    assert "2 stories today" in out["intro"]  # deterministic fallback


def test_make_intro_outro_falls_back_on_timeout():
    def runner(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kw["timeout"])

    out = orchestrate.make_intro_outro(["A", "B"], "June 4, 2026", runner=runner)
    assert out == orchestrate.fallback_intro_outro("June 4, 2026", 2)


def test_assemble_manifest_shape():
    survivors = [
        {"title": "A", "segment": "seg a", "source_url": "u/a", "feed_name": "F1"},
        {"title": "B", "segment": "seg b", "source_url": "u/b", "feed_name": "F2"},
    ]
    io = {"intro": "I", "outro": "O", "summary": "S"}
    m = orchestrate.assemble_manifest("June 4, 2026", "2026-06-04", survivors, io)
    assert m["title"] == "Daily Digest - June 4, 2026"
    assert m["summary"] == "S" and m["voice"] == "house" and m["date"] == "2026-06-04"
    assert [s["text"] for s in m["segments"]] == ["I", "seg a", "seg b", "O"]
    assert m["segments"][0]["source_url"] is None  # intro
    assert m["segments"][1]["source_url"] == "u/a"  # 1:1 mapping
    assert m["segments"][-1]["title"] == "Sign-off"


def test_load_covered_malformed_is_empty(tmp_path, monkeypatch):
    p = tmp_path / "covered.json"
    p.write_text("{ not json")
    monkeypatch.setattr(orchestrate, "COVERED_PATH", p)
    assert orchestrate.load_covered() == {}


def test_update_feed_usage_merges(tmp_path):
    p = tmp_path / "feed_usage.json"
    p.write_text('{"Old Feed": "2026-01-01"}')
    orchestrate.update_feed_usage(["Feed A", "Feed B"], "2026-06-04", path=p)
    data = orchestrate.json.loads(p.read_text())
    assert data["Feed A"] == "2026-06-04" and data["Old Feed"] == "2026-01-01"


def test_write_dropped_log_appends_jsonl(tmp_path):
    p = tmp_path / "dropped.jsonl"
    dropped = [{"feed_name": "F", "url": "u", "reason": "blocked", "detail": "x"}]
    orchestrate.write_dropped_log(dropped, "2026-06-04", path=p)
    orchestrate.write_dropped_log(dropped, "2026-06-04", path=p)
    lines = p.read_text().strip().splitlines()
    assert len(lines) == 2
    rec = orchestrate.json.loads(lines[0])
    assert rec["reason"] == "blocked" and rec["run_date"] == "2026-06-04" and rec["url"] == "u"


def test_run_render_parses_result(tmp_path):
    def runner(cmd, **kw):
        assert "--dry-run" not in cmd
        return SimpleNamespace(
            stdout='{\n  "status": "ready",\n  "episode_uri": "spotify:episode:1",\n'
            '  "title": "Daily Digest - June 4, 2026",\n  "chapter_count": 5,\n'
            '  "duration_s": 412.3,\n  "r2_status": "published"\n}',
            stderr="",
            returncode=0,
        )

    res = orchestrate.run_render(tmp_path / "m.json", tmp_path, dry_run=False, runner=runner)
    assert res["episode_uri"] == "spotify:episode:1" and res["chapter_count"] == 5


def test_run_render_forwards_dry_run(tmp_path):
    def runner(cmd, **kw):
        assert "--dry-run" in cmd
        return SimpleNamespace(
            stdout='{"status": "dry-run", "title": "Daily Digest - June 4, 2026"}',
            stderr="",
            returncode=0,
        )

    res = orchestrate.run_render(tmp_path / "m.json", tmp_path, dry_run=True, runner=runner)
    assert res["status"] == "dry-run"


def test_run_render_parses_real_nested_loudnorm_result(tmp_path):
    """A successful REAL (non-dry-run) render prints a result whose `loudnorm` value is a
    nested object. run_render must parse it to a dict and return it — not raise RenderError
    (which main() would surface as FAILED even though the episode shipped). This mirrors
    render.py's actual json.dumps(indent=2) output shape."""
    real_result = {
        "status": "ready",
        "episode_uri": "spotify:episode:4zuLE5DOQlHvJylHXOQn2z",
        "title": "Daily Digest - June 5, 2026",
        "voice": "house",
        "voice_mode": "ref_audio",
        "chapter_count": 6,
        "duration_s": 503.7,
        "loudnorm": {"input_i": -22.5, "output_i": -24.0, "input_tp": -3.1},
        "r2_status": "published",
        "resumed": False,
    }

    def runner(cmd, **kw):
        assert "--dry-run" not in cmd
        return SimpleNamespace(stdout=json.dumps(real_result, indent=2), stderr="", returncode=0)

    res = orchestrate.run_render(tmp_path / "m.json", tmp_path, dry_run=False, runner=runner)
    assert res == real_result


def test_run_render_raises_on_failure(tmp_path):
    def runner(cmd, **kw):
        return SimpleNamespace(stdout="", stderr="boom: ffmpeg missing", returncode=1)

    with pytest.raises(orchestrate.RenderError, match="ffmpeg missing"):
        orchestrate.run_render(tmp_path / "m.json", tmp_path, dry_run=False, runner=runner)


def test_build_report_shipped_and_dryrun():
    ready = {
        "status": "ready",
        "episode_uri": "spotify:episode:1",
        "title": "T",
        "chapter_count": 5,
        "duration_s": 412.3,
        "r2_status": "published",
    }
    line = orchestrate.build_report(ready)
    assert line == "SHIPPED spotify:episode:1 - T - 5 chapters - 412.3s - r2=ok"
    dry = {
        "status": "dry-run",
        "title": "T",
        "chapter_count": 5,
        "duration_s": 412.3,
        "r2_status": None,
    }
    assert orchestrate.build_report(dry).startswith("DRY-RUN ok - T - 5 chapters")


def test_main_happy_path(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate, "CONFIG_PATH", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(
        '{"opml_files": ["/x.opml"], "lookback_hours": 24, "target_item_count": 2}'
    )
    monkeypatch.setattr(orchestrate, "COVERED_PATH", tmp_path / "covered.json")
    monkeypatch.setattr(orchestrate, "FEED_USAGE_PATH", tmp_path / "feed_usage.json")
    monkeypatch.setattr(orchestrate, "DROPPED_LOG_PATH", tmp_path / "dropped.jsonl")
    monkeypatch.setattr(orchestrate, "SUMMARIZE_PROMPT_PATH", tmp_path / "p.md")
    (tmp_path / "p.md").write_text("PROMPT <<TITLE>>")

    monkeypatch.setattr(
        orchestrate,
        "gather_candidates",
        lambda *a, **k: [
            {
                "title": "A",
                "url": "u/a",
                "feed_name": "F1",
                "summary": "",
                "published": None,
                "category": "",
            }
        ],
    )
    monkeypatch.setattr(
        orchestrate,
        "fan_out",
        lambda *a, **k: (
            [{"title": "A", "segment": "seg a", "source_url": "u/a", "feed_name": "F1"}],
            [],
        ),
    )
    monkeypatch.setattr(
        orchestrate,
        "make_intro_outro",
        lambda *a, **k: {"intro": "I", "outro": "O", "summary": "S"},
    )
    captured = {}

    def fake_render(manifest_path, workdir, dry_run, runner=None):
        captured["manifest"] = orchestrate.json.loads(Path(manifest_path).read_text())
        return {
            "status": "ready",
            "episode_uri": "spotify:episode:9",
            "title": "T",
            "chapter_count": 3,
            "duration_s": 100.0,
            "r2_status": "skipped",
        }

    monkeypatch.setattr(orchestrate, "run_render", fake_render)
    rc = orchestrate.main(["--workdir", str(tmp_path / "wd")])
    assert rc == 0
    # 1:1 mapping survived into the manifest (intro + 1 story + outro)
    assert len(captured["manifest"]["segments"]) == 3
    assert (tmp_path / "feed_usage.json").exists()  # updated on ready


def test_main_dry_run_skips_feed_usage_update(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate, "CONFIG_PATH", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(
        '{"opml_files": ["/x.opml"], "lookback_hours": 24, "target_item_count": 2}'
    )
    monkeypatch.setattr(orchestrate, "COVERED_PATH", tmp_path / "covered.json")
    monkeypatch.setattr(orchestrate, "FEED_USAGE_PATH", tmp_path / "feed_usage.json")
    initial_feed_usage = '{"F1": "2026-01-01"}'
    (tmp_path / "feed_usage.json").write_text(initial_feed_usage)
    monkeypatch.setattr(orchestrate, "DROPPED_LOG_PATH", tmp_path / "dropped.jsonl")
    monkeypatch.setattr(orchestrate, "SUMMARIZE_PROMPT_PATH", tmp_path / "p.md")
    (tmp_path / "p.md").write_text("PROMPT <<TITLE>>")

    monkeypatch.setattr(
        orchestrate,
        "gather_candidates",
        lambda *a, **k: [
            {
                "title": "A",
                "url": "u/a",
                "feed_name": "F1",
                "summary": "",
                "published": None,
                "category": "",
            }
        ],
    )
    monkeypatch.setattr(
        orchestrate,
        "fan_out",
        lambda *a, **k: (
            [{"title": "A", "segment": "seg a", "source_url": "u/a", "feed_name": "F1"}],
            [],
        ),
    )
    monkeypatch.setattr(
        orchestrate,
        "make_intro_outro",
        lambda *a, **k: {"intro": "I", "outro": "O", "summary": "S"},
    )
    monkeypatch.setattr(
        orchestrate,
        "run_render",
        lambda *a, **k: {
            "status": "ready",
            "title": "T",
            "chapter_count": 3,
            "duration_s": 100.0,
            "r2_status": "skipped",
        },
    )

    rc = orchestrate.main(["--dry-run", "--workdir", str(tmp_path / "wd")])
    assert rc == 0
    assert (tmp_path / "feed_usage.json").read_text() == initial_feed_usage


def test_main_no_survivors_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate, "CONFIG_PATH", tmp_path / "config.json")
    (tmp_path / "config.json").write_text('{"opml_files": ["/x.opml"]}')
    monkeypatch.setattr(orchestrate, "COVERED_PATH", tmp_path / "c.json")
    monkeypatch.setattr(orchestrate, "SUMMARIZE_PROMPT_PATH", tmp_path / "p.md")
    (tmp_path / "p.md").write_text("P")
    monkeypatch.setattr(
        orchestrate,
        "gather_candidates",
        lambda *a, **k: [
            {
                "title": "A",
                "url": "u/a",
                "feed_name": "F",
                "summary": "",
                "published": None,
                "category": "",
            }
        ],
    )
    monkeypatch.setattr(
        orchestrate,
        "fan_out",
        lambda *a, **k: (
            [],
            [{"feed_name": "F", "url": "u/a", "reason": "blocked", "detail": "x"}],
        ),
    )
    monkeypatch.setattr(orchestrate, "DROPPED_LOG_PATH", tmp_path / "d.jsonl")
    rc = orchestrate.main(["--workdir", str(tmp_path / "wd")])
    assert rc == 1


def test_main_auth_failure_fails_fast_with_actionable_message(tmp_path, monkeypatch, capsys):
    # When every item drops on a 401 (scheduled harness, no creds for child claude -p), the
    # run must fail fast with an actionable single-line message instead of silently degrading
    # to the generic "no viable items". See SKILL.md "Unattended runs need durable credentials".
    monkeypatch.setattr(orchestrate, "CONFIG_PATH", tmp_path / "config.json")
    (tmp_path / "config.json").write_text('{"opml_files": ["/x.opml"]}')
    monkeypatch.setattr(orchestrate, "COVERED_PATH", tmp_path / "c.json")
    monkeypatch.setattr(orchestrate, "SUMMARIZE_PROMPT_PATH", tmp_path / "p.md")
    (tmp_path / "p.md").write_text("P")
    monkeypatch.setattr(
        orchestrate,
        "gather_candidates",
        lambda *a, **k: [
            {
                "title": "A",
                "url": "u/a",
                "feed_name": "F",
                "summary": "",
                "published": None,
                "category": "",
            }
        ],
    )
    monkeypatch.setattr(
        orchestrate,
        "fan_out",
        lambda *a, **k: (
            [],
            [{"feed_name": "F", "url": "u/a", "reason": "auth", "detail": "401 ..."}],
        ),
    )
    monkeypatch.setattr(orchestrate, "DROPPED_LOG_PATH", tmp_path / "d.jsonl")
    rc = orchestrate.main(["--workdir", str(tmp_path / "wd")])
    assert rc == 1
    out = capsys.readouterr().out
    assert out.startswith("FAILED ")  # single-line scheduler contract preserved
    assert "401" in out and "credential" in out.lower()  # actionable, not generic
    assert "(all dropped/blocked)" not in out  # distinct from the generic-drop message
    assert "SKILL.md" in out  # points at the operator-setup section that must exist
    assert "\n" not in out.strip()  # stays one line


# --- Script variety: date-seeded rotation -----------------------------------
#
# 76 shipped episodes opened with the same literal sentence and every segment had
# the same silhouette. Variety has to be ASSIGNED, not requested: a model told to
# "be varied" regresses to the mean, and in the fan-out each segment is written by
# an isolated `claude -p` that cannot see its neighbours to differ from them. These
# tests lock the rotation's guarantees — a cycle covers the bank, consecutive days
# never open the same way, and the length rhythm can't drop below the drop floor.


def test_day_index_is_day_of_year():
    assert orchestrate.day_index("2026-01-01") == 1
    assert orchestrate.day_index("2026-12-31") == 365


def test_segment_shape_covers_every_shape_within_one_cycle():
    n = len(orchestrate.SEGMENT_SHAPES)
    for day in range(1, 367):
        shapes = [orchestrate.segment_shape(day, i) for i in range(n)]
        assert set(shapes) == set(orchestrate.SEGMENT_SHAPES), f"day {day} repeats within a cycle"


def test_segment_shape_opens_differently_on_consecutive_days():
    for day in range(1, 366):
        assert orchestrate.segment_shape(day, 0) != orchestrate.segment_shape(day + 1, 0)


def test_segment_shape_reorders_adjacencies_across_days():
    # A plain rotation only shifts the bank's phase, so `stakes-first` would follow
    # `plain-lede` in every episode forever. Test that property directly rather than
    # counting orderings: normalise each day's ordering by its own first element and
    # require more than one signature to survive. A pure rotation collapses to one.
    names = list(orchestrate.SEGMENT_SHAPES)
    n = len(names)
    sigs = set()
    for day in range(1, 21):
        order = [names.index(orchestrate.segment_shape(day, i)) for i in range(n)]
        sigs.add(tuple((x - order[0]) % n for x in order))
    assert len(sigs) > 1, "orderings are mutual rotations - adjacency never changes"


def test_intro_mode_rotates_daily_and_keeps_the_classic_open():
    # The classic rundown line stays in the bank: the show keeps a recognizable
    # open roughly one day in five instead of losing its signature entirely.
    assert "classic" in orchestrate.INTRO_MODES
    for day in range(1, 366):
        assert orchestrate.intro_mode(day) != orchestrate.intro_mode(day + 1)
    cycle = {orchestrate.intro_mode(d) for d in range(1, 1 + len(orchestrate.INTRO_MODES))}
    assert cycle == set(orchestrate.INTRO_MODES)


def test_outro_mode_rotates_daily():
    for day in range(1, 366):
        assert orchestrate.outro_mode(day) != orchestrate.outro_mode(day + 1)
    cycle = {orchestrate.outro_mode(d) for d in range(1, 1 + len(orchestrate.OUTRO_MODES))}
    assert cycle == set(orchestrate.OUTRO_MODES)


def test_short_take_band_never_trips_the_drop_floor():
    # A short take under MIN_SEGMENT_CHARS is classified REFUSED and its item is
    # dropped, so a too-low floor would silently shorten every episode.
    for day in range(1, 366):
        for pos in range(15):
            lo, hi = orchestrate.segment_length_band(day, pos)
            assert lo >= orchestrate.MIN_SEGMENT_CHARS
            assert lo < hi


def test_lead_segment_gets_the_longest_band():
    for day in range(1, 366):
        assert orchestrate.segment_length_band(day, 0) == orchestrate.LEAD_BAND
        assert all(
            orchestrate.segment_length_band(day, p)[1] <= orchestrate.LEAD_BAND[1]
            for p in range(1, 15)
        )


def test_episode_mixes_short_takes_with_body_segments():
    for day in range(1, 366):
        bands = {orchestrate.segment_length_band(day, p) for p in range(1, 12)}
        assert orchestrate.SHORT_BAND in bands and orchestrate.BODY_BAND in bands


def test_fill_prompt_substitutes_shape_and_length_band():
    tpl = "t=<<TITLE>> shape=<<SHAPE>> min=<<MIN_CHARS>> max=<<MAX_CHARS>>"
    out = orchestrate.fill_prompt(tpl, ITEM, shape="scene", length_band=(500, 650))
    assert "<<" not in out
    assert orchestrate.SEGMENT_SHAPES["scene"] in out
    assert "min=500 max=650" in out


def test_fill_prompt_never_leaves_the_shape_instruction_blank():
    # An unknown or omitted shape must degrade to a real instruction, not an empty
    # line that reads to the model as "no guidance here".
    assert orchestrate.fill_prompt("shape=<<SHAPE>>", ITEM).strip() != "shape="
    assert orchestrate.fill_prompt("shape=<<SHAPE>>", ITEM, shape="nonesuch").strip() != "shape="


def test_fan_out_assigns_a_distinct_shape_per_position():
    ranked = [{"title": f"T{i}", "url": f"u/{i}", "feed_name": "F"} for i in range(5)]
    seen = {}

    def capture(item, tpl, **kw):
        seen[item["title"]] = kw["shape"]
        return {
            **orchestrate._drop_fields(item),
            "outcome": "OK",
            "segment": "s",
            "source_url": item["url"],
            "detail": "",
        }

    orchestrate.fan_out(ranked, "tpl", target=5, summarize=capture, day_idx=7)
    assert len(set(seen.values())) == 5


def test_make_intro_outro_prompt_carries_the_days_open_and_close_modes():
    captured = {}

    def runner(cmd, **kw):
        captured["prompt"] = cmd[2]
        return SimpleNamespace(
            stdout='{"intro":"i","outro":"o","summary":"s"}', stderr="", returncode=0
        )

    orchestrate.make_intro_outro(["A", "B"], "June 4, 2026", runner=runner, day_idx=2)
    assert orchestrate.INTRO_MODES[orchestrate.intro_mode(2)] in captured["prompt"]
    assert orchestrate.OUTRO_MODES[orchestrate.outro_mode(2)] in captured["prompt"]


def test_summarize_prompt_declares_the_variety_placeholders():
    # The prompt file and fill_prompt are one contract; a renamed placeholder on
    # either side silently ships segments with a literal "<<SHAPE>>" in them.
    tpl = orchestrate.SUMMARIZE_PROMPT_PATH.read_text()
    for marker in ("<<SHAPE>>", "<<MIN_CHARS>>", "<<MAX_CHARS>>"):
        assert marker in tpl, f"prompts/summarize_item.md dropped {marker}"


def test_skill_md_documents_every_shape_and_mode():
    # SKILL.md is the production path (the cron follows it, not orchestrate.py), so
    # a shape that exists only in code never reaches a real episode.
    skill = (orchestrate.SKILL_DIR / "SKILL.md").read_text()
    for name in (*orchestrate.SEGMENT_SHAPES, *orchestrate.INTRO_MODES, *orchestrate.OUTRO_MODES):
        assert name in skill, f"SKILL.md never mentions {name!r}"


def test_no_position_holds_its_shape_two_days_running():
    """The yearly-coverage test was too weak: with an arithmetic stride, position 4
    was PINNED to one shape for four consecutive days (and so were 9 and 14, i.e.
    two slots of a twelve-segment episode). `(1 + p) % 5 == 0` cancelled the
    day-varying term, leaving only day // 4. Caught by a smoke test, not by unit
    tests that only checked coverage across a whole year."""
    for day in range(1, 366):
        for pos in range(15):
            today = orchestrate.segment_shape(day, pos)
            assert today != orchestrate.segment_shape(day + 1, pos), (
                f"position {pos} repeats {today!r} on days {day}/{day + 1}"
            )


def test_every_position_sees_every_shape_within_one_bank_cycle():
    # Fairness: no slot may be starved of a shape. A Latin square gives this for
    # free - each column holds each shape exactly once - and it is what rules out
    # the "pinned for four days" failure above by construction.
    n = len(orchestrate.SEGMENT_SHAPES)
    for start in range(1, 60):
        for pos in range(n):
            seen = {orchestrate.segment_shape(start + k, pos) for k in range(n)}
            assert seen == set(orchestrate.SEGMENT_SHAPES), (
                f"position {pos} starved from day {start}"
            )


def test_skill_md_shape_table_matches_the_code():
    """SKILL.md carries the shape table as prose because the scheduled run is a
    `claude -p` following it, not orchestrate.py. Name-coverage alone would not
    notice a reordered row, and a wrong row silently ships the wrong rotation."""
    names = list(orchestrate.SEGMENT_SHAPES)
    lines = (orchestrate.SKILL_DIR / "SKILL.md").read_text().splitlines()
    # Anchor on the table's own header - SKILL.md has several `| 0 |` rows, and an
    # unanchored match reads the cold-open table instead.
    header = next((i for i, ln in enumerate(lines) if "| pos 0 |" in ln), None)
    assert header is not None, "SKILL.md lost the per-position shape table"
    body = lines[header + 2 : header + 2 + len(orchestrate.SHAPE_ORDERS)]
    for row, (order, line) in enumerate(zip(orchestrate.SHAPE_ORDERS, body, strict=True)):
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        assert cells[0] == str(row), f"shape table row {row} is out of order: {line!r}"
        assert cells[1:] == [names[i] for i in order], (
            f"row {row}: SKILL.md says {cells[1:]}, code says {[names[i] for i in order]}"
        )


# --- Segues: assigned, like everything else --------------------------------
#
# The first pass at this left transitions as a REQUEST ("vary the connective
# tissue"), which is the exact thing the rest of this design argues does not
# work. It also could not apply to the orchestrator at all: a per-item writer is
# isolated and has never seen the previous story, so it cannot write a segue.
# Transitions are therefore assigned from the running order of TITLES - context
# `make_intro_outro` is already allowed to have, so the one-body-per-request
# invariant is untouched.


def test_transition_moves_include_a_hard_cut():
    # `cold` is what makes "not every segment needs a segue" mechanical rather
    # than a hope. Without it, every junction gets connective tissue.
    assert "cold" in orchestrate.TRANSITION_MOVES
    assert len(orchestrate.TRANSITION_MOVES) == len(orchestrate.SEGMENT_SHAPES)


def test_lead_story_has_no_incoming_segue():
    for day in range(1, 366):
        assert orchestrate.segment_transition(day, 0) is None


def test_transitions_do_not_pin_a_junction_two_days_running():
    for day in range(1, 366):
        for pos in range(1, 15):
            today = orchestrate.segment_transition(day, pos)
            assert today != orchestrate.segment_transition(day + 1, pos), (
                f"junction {pos} repeats {today!r} on days {day}/{day + 1}"
            )


def test_every_junction_sees_every_move_within_one_bank_cycle():
    n = len(orchestrate.TRANSITION_MOVES)
    for start in range(1, 60):
        for pos in range(1, n + 1):
            seen = {orchestrate.segment_transition(start + k, pos) for k in range(n)}
            assert seen == set(orchestrate.TRANSITION_MOVES), f"junction {pos} starved"


def test_transitions_are_not_locked_to_the_shape_rotation():
    """Segues and shapes both walk SHAPE_ORDERS. If they walked the SAME row, a
    given shape would carry the same segue forever, collapsing two independent
    axes of variety into one."""
    shapes = list(orchestrate.SEGMENT_SHAPES)
    moves = list(orchestrate.TRANSITION_MOVES)
    for day in range(1, 366):
        shape_row = [shapes.index(orchestrate.segment_shape(day, p)) for p in range(5)]
        move_row = [moves.index(orchestrate.segment_transition(day, p + 1)) for p in range(5)]
        assert shape_row != move_row, f"day {day}: segues walk the same row as shapes"


def test_make_transitions_falls_back_to_hard_cuts(monkeypatch):
    # Same posture as make_intro_outro: a failure here must cost the episode its
    # segues, never the run.
    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    out = orchestrate.make_transitions(["A", "B", "C"], day_idx=3, runner=boom)
    assert out == ["", "", ""]


def test_make_transitions_returns_one_entry_per_story_and_never_leads():
    def runner(cmd, **kw):
        return SimpleNamespace(
            stdout='{"transitions": ["IGNORED", "Then the other side of that.", "Elsewhere."]}',
            stderr="",
            returncode=0,
        )

    out = orchestrate.make_transitions(["A", "B", "C"], day_idx=3, runner=runner)
    assert len(out) == 3
    assert out[0] == ""  # the lead story never gets an incoming segue
    assert out[1] and out[2]


def test_assemble_manifest_prepends_segues_to_the_right_segments():
    survivors = [
        {
            "title": f"T{i}",
            "url": f"u/{i}",
            "source_url": f"u/{i}",
            "segment": f"body{i}",
            "feed_name": "F",
        }
        for i in range(3)
    ]
    io = {"intro": "i", "outro": "o", "summary": "s"}
    m = orchestrate.assemble_manifest(
        "June 4, 2026", "2026-06-04", survivors, io, transitions=["", "Meanwhile.", ""]
    )
    texts = [s["text"] for s in m["segments"]]
    assert texts[0] == "i" and texts[-1] == "o"  # intro/outro untouched
    assert texts[1] == "body0"  # lead: no segue
    assert texts[2] == "Meanwhile. body1"  # segue prepended, single space
    assert texts[3] == "body2"  # `cold` junction stays a hard cut


def test_assemble_manifest_without_transitions_is_unchanged():
    # Back-compat: the fallback path passes nothing and must behave as before.
    survivors = [{"title": "T", "url": "u", "source_url": "u", "segment": "body", "feed_name": "F"}]
    io = {"intro": "i", "outro": "o", "summary": "s"}
    m = orchestrate.assemble_manifest("June 4, 2026", "2026-06-04", survivors, io)
    assert [s["text"] for s in m["segments"]] == ["i", "body", "o"]


def test_skill_md_documents_every_transition_move():
    skill = (orchestrate.SKILL_DIR / "SKILL.md").read_text()
    for name in orchestrate.TRANSITION_MOVES:
        assert name in skill, f"SKILL.md never mentions the {name!r} segue"


# --- Episode titles: topics first, date last (#139) ------------------------
#
# All 75 published episodes were titled `Daily Digest - <date>`, so nothing told a
# browsing listener what any episode was about and the first ~30 characters of every
# title were identical. #128 decoupled the slug from the title, which is what makes
# retitling guid-neutral and therefore safe.
#
# The format is CODE, the content is the model — the same split the shape rotation
# uses. `episode_title` composes; `make_intro_outro` supplies the topic phrases from
# the running order of TITLES only, so the one-body-per-request invariant is untouched.


def test_episode_title_leads_with_topics_and_ends_with_the_date():
    assert (
        orchestrate.episode_title(
            ["Salt Typhoon", "the CareCloud breach", "Siemens PLC warnings"], "August 20, 2026"
        )
        == "Salt Typhoon, the CareCloud breach, Siemens PLC warnings - August 20, 2026"
    )


def test_episode_title_front_loads_the_topics_so_truncation_costs_least():
    """Spotify states NO hard cap for an episode <title> and truncates per device
    instead (Podcast Delivery Specification v1.9 §4.3), so the distinguishing words
    have to come first. The date-only title spent that budget on 30 identical
    characters. The date stays - a daily show's listener orients by it - but last,
    where truncation costs the least."""
    t = orchestrate.episode_title(["Mojo goes open source"], "August 19, 2026")
    assert not t.startswith(orchestrate.LEGACY_TITLE_PREFIX)
    assert t.index("Mojo") < t.index("August 19, 2026")
    assert t.endswith("August 19, 2026")


def test_episode_title_uses_at_most_the_topic_count():
    assert orchestrate.episode_title(["A", "B", "C", "D", "E"], "June 4, 2026") == (
        "A, B, C - June 4, 2026"
    )


def test_episode_title_drops_trailing_topics_rather_than_cutting_mid_word():
    """The cap is a runaway guard, not a platform limit, so it must never leave a
    half-word or a dangling comma in a title Spotify freezes at creation."""
    overlong = "an overlong third topic that on its own pushes this title past the cap"
    t = orchestrate.episode_title(["Salt Typhoon", "CareCloud", overlong], "June 4, 2026")
    assert len(t) <= orchestrate.TITLE_MAX_CHARS
    assert t == "Salt Typhoon, CareCloud - June 4, 2026"


def test_episode_title_falls_back_to_the_legacy_date_only_title():
    """Same posture as make_transitions degrading to hard cuts: a title that cannot be
    built degrades to the known-good date-only one. It can never be empty -
    validate_manifest dies on a blank title, which would cost the whole episode."""
    legacy = "Daily Digest - June 4, 2026"
    assert orchestrate.episode_title([], "June 4, 2026") == legacy
    assert orchestrate.episode_title(None, "June 4, 2026") == legacy
    assert orchestrate.episode_title(["  ", "", None, 7], "June 4, 2026") == legacy
    assert orchestrate.episode_title(["z" * 200], "June 4, 2026") == legacy


def test_episode_title_normalizes_dashes_that_render_inconsistently():
    """cortech.online dropped the em dash from the public show title because special
    characters render inconsistently across directories (docs/podcast-metadata.md).
    Topic text is feed-derived, so enforce it here rather than asking for it."""
    t = orchestrate.episode_title(["Citrix — an emergency patch"], "June 4, 2026")
    assert "—" not in t and "–" not in t
    assert t == "Citrix - an emergency patch - June 4, 2026"


def test_episode_title_format_is_fixed_not_date_seeded():
    """Unlike the cold open, the sign-off and the segment shapes, the title format is
    deliberately NOT rotated. A browsing listener needs one recognizable shape; a churn
    of title formats reads worse than one adequate format held consistently. Every
    episode is frozen under whatever format shipped it, so the shape must not vary."""
    params = inspect.signature(orchestrate.episode_title).parameters
    assert "day_idx" not in params and "day" not in params


def test_make_intro_outro_returns_the_title_topics():
    def runner(cmd, **kw):
        return SimpleNamespace(
            stdout=(
                '{"intro": "I", "outro": "O", "summary": "S", '
                '"topics": ["Salt Typhoon", "CareCloud", "Siemens PLCs"]}'
            ),
            stderr="",
            returncode=0,
        )

    out = orchestrate.make_intro_outro(["A", "B"], "June 4, 2026", runner=runner)
    assert out["topics"] == ["Salt Typhoon", "CareCloud", "Siemens PLCs"]
    assert out["intro"] == "I"


def test_make_intro_outro_topics_are_additive_so_a_partial_reply_still_ships():
    """The three prose fields are the contract; topics ride along. A reply without them
    must still be used - throwing a good intro away over a missing title would cost the
    episode far more than the date-only title fallback does."""

    def runner(cmd, **kw):
        return SimpleNamespace(
            stdout='{"intro": "I", "outro": "O", "summary": "S"}', stderr="", returncode=0
        )

    out = orchestrate.make_intro_outro(["A", "B"], "June 4, 2026", runner=runner)
    assert out["intro"] == "I"
    assert out["topics"] == []


def test_make_intro_outro_prompt_asks_for_the_title_topics():
    captured = {}

    def runner(cmd, **kw):
        captured["prompt"] = cmd[2]
        return SimpleNamespace(stdout="{}", stderr="", returncode=0)

    orchestrate.make_intro_outro(["A", "B"], "June 4, 2026", runner=runner)
    assert "TOPICS" in captured["prompt"]
    assert str(orchestrate.TITLE_TOPIC_COUNT) in captured["prompt"]
    # The word band is the knob that decides whether all three topics survive the cap,
    # so it must reach the model rather than living only in SKILL.md.
    assert f"{orchestrate.TITLE_TOPIC_WORDS} words" in captured["prompt"]


def test_fallback_intro_outro_carries_no_topics():
    assert orchestrate.fallback_intro_outro("June 4, 2026", 2)["topics"] == []


def test_assemble_manifest_titles_the_episode_from_the_topics():
    survivors = [{"title": "A", "segment": "seg a", "source_url": "u/a", "feed_name": "F1"}]
    io = {
        "intro": "I",
        "outro": "O",
        "summary": "S",
        "topics": ["Salt Typhoon", "CareCloud", "Siemens PLCs"],
    }
    m = orchestrate.assemble_manifest("June 4, 2026", "2026-06-04", survivors, io)
    assert m["title"] == "Salt Typhoon, CareCloud, Siemens PLCs - June 4, 2026"
    # The slug keys on `date`, never the title - retitling must stay guid-neutral.
    assert m["date"] == "2026-06-04"


def test_skill_md_states_the_episode_title_format():
    """SKILL.md is the production path - the scheduled run is a `claude -p` following
    it, not orchestrate.py - so a title format stated only in code never reaches a real
    episode. Pin the worked example, the fallback and the numbers so the two can't drift."""
    skill = (orchestrate.SKILL_DIR / "SKILL.md").read_text()
    example = orchestrate.episode_title(
        ["Salt Typhoon", "the CareCloud breach", "Siemens PLC warnings"], "August 20, 2026"
    )
    assert example in skill, f"SKILL.md lost the worked title example {example!r}"
    assert orchestrate.episode_title([], "August 20, 2026") in skill, (
        "SKILL.md never states the date-only fallback title"
    )
    assert f"{orchestrate.TITLE_TOPIC_COUNT} topics" in skill
    assert f"{orchestrate.TITLE_MAX_CHARS} characters" in skill
    assert f"{orchestrate.TITLE_TOPIC_WORDS} words" in skill
