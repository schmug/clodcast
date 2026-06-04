# tests/test_orchestrate.py
from __future__ import annotations

import datetime as dt
import subprocess
from types import SimpleNamespace

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
