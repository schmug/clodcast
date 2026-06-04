# tests/test_orchestrate.py
from __future__ import annotations

import datetime as dt
import subprocess
from types import SimpleNamespace

import orchestrate
import pytest

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


def test_classify_ok():
    r = orchestrate.classify_output('{"ok": true, "segment": "hello", "source_url": "u"}', "", 0)
    assert r["outcome"] == "OK" and r["segment"] == "hello"


def test_classify_refused():
    r = orchestrate.classify_output('{"ok": false, "reason": "not news"}', "", 0)
    assert r["outcome"] == "REFUSED" and "not news" in r["detail"]


def test_classify_blocked_on_policy_marker():
    r = orchestrate.classify_output("", "API Error ... violative cyber content ... Usage Policy", 1)
    assert r["outcome"] == "BLOCKED"


def test_classify_error_when_garbage():
    r = orchestrate.classify_output("blah no json", "", 1)
    assert r["outcome"] == "ERROR"


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
            stdout='{"ok": true, "segment": "seg", "source_url": "ignored"}',
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
    assert out == {"intro": "I", "outro": "O", "summary": "S"}


def test_make_intro_outro_falls_back_on_garbage():
    def runner(cmd, **kw):
        return SimpleNamespace(stdout="no json here", stderr="", returncode=0)

    out = orchestrate.make_intro_outro(["A", "B"], "June 4, 2026", runner=runner)
    assert "2 stories today" in out["intro"]  # deterministic fallback


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
