# Per-item Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `skills/daily-podcast/orchestrate.py`, an unattended driver that gathers + curates feed items deterministically (metadata only), summarizes each ranked item in its own isolated `claude -p` subprocess (so an API-level cyber-content classifier block drops only that item), and hands the surviving segments to the unchanged `render.py`.

**Architecture:** Pure-Python gather (`feedparser`) → deterministic metadata ranking → concurrency-capped fan-out of one `claude -p` per item with drop-on-block → assemble manifest → invoke `render.py`. No LLM request ever holds more than one article's body. See `docs/superpowers/specs/2026-06-04-per-item-orchestrator-design.md`.

**Tech Stack:** Python 3.10+, stdlib (`subprocess`, `concurrent.futures`, `argparse`), `feedparser` (already a dep) for feeds, `defusedxml` (new dep) for safe OPML parsing, `ruff` (lint/format, line-length 100), `pytest`. `render.py` unchanged.

---

## File structure

- **Create** `skills/daily-podcast/orchestrate.py` — single file (sibling to `render.py`, invoked by path, never imported by it). Pure orchestration + deterministic curation; all LLM work delegated to isolated `claude -p` subprocesses.
- **Create** `skills/daily-podcast/prompts/summarize_item.md` — single-item summarizer prompt (sentinel placeholders `<<TITLE>>`/`<<URL>>`/`<<FEED>>`, JSON output contract).
- **Create** `tests/test_orchestrate.py` — deterministic unit tests (feedparser + `subprocess` injected; no network/LLM).
- **Modify** `skills/daily-podcast/prompts/daily.md` — add a deprecation banner pointing to `orchestrate.py`.
- **Modify** `SKILL.md`, `README.md`, project `CLAUDE.md` — document the orchestrator, `summarize_item.md`, and the new state files.

`tests/conftest.py` already puts `skills/daily-podcast/` on `sys.path`, so `import orchestrate` works with no change.

Conventions to match `render.py`: `from __future__ import annotations`, type hints, module-level constants with *why* comments, `log()` to stderr. ruff line-length is 100.

---

### Task 1: Scaffold `orchestrate.py` + `extract_last_json`

**Files:**
- Create: `skills/daily-podcast/orchestrate.py`
- Test: `tests/test_orchestrate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_orchestrate.py
from __future__ import annotations

import orchestrate


def test_extract_last_json_single_line():
    assert orchestrate.extract_last_json('noise\n{"ok": true, "segment": "x"}') == {
        "ok": True, "segment": "x"}


def test_extract_last_json_multiline_fallback():
    out = orchestrate.extract_last_json('pre\n{\n  "ok": false,\n  "reason": "no"\n}\n')
    assert out == {"ok": False, "reason": "no"}


def test_extract_last_json_none_when_absent():
    assert orchestrate.extract_last_json("just prose, no json") is None
    assert orchestrate.extract_last_json("") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_orchestrate.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrate'`.

- [ ] **Step 3: Write minimal implementation**

```python
# skills/daily-podcast/orchestrate.py
#!/usr/bin/env python3
"""
orchestrate.py — unattended daily-podcast driver (per-item isolation).

Gathers + curates feed items DETERMINISTICALLY (metadata only — never article
bodies), then summarizes each ranked item in its OWN isolated `claude -p`
subprocess. A per-item classifier block / error / timeout drops only that item
(logged to dropped.jsonl); the rest still ship. Assembles the surviving segments
into a manifest and hands it to render.py (unchanged).

Why this shape: an API-level Usage Policy classifier blocks the request when one
agent context pools many security-feed candidates into a single request. Isolating
one article per request contains the block. See
docs/superpowers/specs/2026-06-04-per-item-orchestrator-design.md.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

# defusedxml is imported lazily inside parse_opml (keeps module import light for CI,
# which installs only dev tools — mirrors the lazy feedparser import in gather).

# Config dir mirrors render.py; overridable via env so tests never touch the real
# ~/.config/daily-podcast (render.py hardcodes it; the orchestrator needs injection).
CONFIG_DIR = Path(
    os.environ.get("DAILY_PODCAST_CONFIG_DIR", Path.home() / ".config" / "daily-podcast")
)
CONFIG_PATH = CONFIG_DIR / "config.json"
COVERED_PATH = CONFIG_DIR / "covered.json"
FEED_USAGE_PATH = CONFIG_DIR / "feed_usage.json"
DROPPED_LOG_PATH = CONFIG_DIR / "dropped.jsonl"

SKILL_DIR = Path(__file__).resolve().parent
RENDER_PY = SKILL_DIR / "render.py"
SUMMARIZE_PROMPT_PATH = SKILL_DIR / "prompts" / "summarize_item.md"

# Ranking knobs — tunable defaults, not load-bearing for correctness.
TARGET_DEFAULT = 10
BUFFER = 6  # fan out target+BUFFER so per-item drops still leave `target` survivors
PER_FEED_CAP = 3  # at most this many items from one feed in the ranked set
VARIETY_DAYS = 3  # penalize feeds used within this many days (daily.md priority 3)
CONCURRENCY_DEFAULT = 3  # parallel claude -p calls (wide fan-out trips a rate-limit nag)
SUMMARIZE_TIMEOUT_S = 150  # per-item claude -p wall clock

# Source tiers (daily.md priority 1: original reporting/analysis > aggregators).
TIER_SCORE = {1: 1.0, 2: 0.5, 3: 0.2}
DEFAULT_TIER = 2
SOURCE_TIERS = {
    "Anthropic Engineering Blog": 1,
    "Anthropic News": 1,
    "Anthropic Research": 1,
    "Anthropic Frontier Red Team Blog": 1,
    "Simon Willison": 1,
    "One Useful Thing": 1,
    "Don't Worry About the Vase": 1,
    "Hacker News": 3,
    "cybersecurity": 3,
    "Sysadmin": 3,
}

WEIGHT_TIER = 1.0
WEIGHT_RECENCY = 0.6
WEIGHT_CONCRETE = 0.3
VARIETY_PENALTY = 0.5

# A blocked claude -p surfaces these markers in stdout/stderr; used to tag drops.
POLICY_RE = re.compile(r"usage policy|violative cyber|unable to respond|cyber verification", re.I)
# Concreteness: a digit, a CVE id, or a version-like token (daily.md priority 2).
CONCRETE_RE = re.compile(r"\d|\bCVE-\d{4}-\d+\b|\bv?\d+\.\d+\b")
_TAG_RE = re.compile(r"<[^>]+>")


class RenderError(Exception):
    """render.py exited non-zero or printed no parseable result JSON."""


def log(msg: str) -> None:
    print(f"[orchestrate] {msg}", file=sys.stderr, flush=True)


def extract_last_json(text: str) -> dict | None:
    """Return the last JSON object in `text`, or None. Prefers a single-line object
    (what our prompts request); falls back to the last {...} span for pretty-printed
    output (render.py prints its result with indent=2)."""
    if not text:
        return None
    for line in reversed(text.strip().splitlines()):
        s = line.strip().strip("`").strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                continue
    start, end = text.rfind("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_orchestrate.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add skills/daily-podcast/orchestrate.py tests/test_orchestrate.py
git commit -m "feat: scaffold orchestrate.py with extract_last_json"
```

---

### Task 2: `parse_opml` (with `defusedxml`)

**Files:**
- Modify: `skills/daily-podcast/orchestrate.py`
- Modify: `pyproject.toml`, `requirements.txt`, `.github/workflows/ci.yml`
- Test: `tests/test_orchestrate.py`

- [ ] **Step 0: Add the `defusedxml` dependency** (OPML can arrive from shared feed-reader exports → treat as untrusted; stdlib `xml.etree` is XXE / billion-laughs vulnerable)

Add `"defusedxml"` to `[project].dependencies` in `pyproject.toml` and a `defusedxml` line to `requirements.txt`. Extend the CI install step in `.github/workflows/ci.yml` so the OPML-parsing tests have it:

```yaml
      - name: Install dev tools
        run: python -m pip install "ruff==0.14.10" "pytest>=8.0" boto3 defusedxml
```

Install locally: `python3 -m pip install --user defusedxml`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_orchestrate.py::test_parse_opml -q`
Expected: FAIL — `AttributeError: module 'orchestrate' has no attribute 'parse_opml'`.

- [ ] **Step 3: Write minimal implementation** (append to `orchestrate.py`)

```python
def parse_opml(path: Path) -> list[dict]:
    """Extract rss leaf nodes from an OPML file. Returns [{feed_name, xml_url, category}].
    Uses defusedxml (OPML can come from shared feed-reader exports → untrusted; stdlib
    xml.etree is XXE / billion-laughs vulnerable). A parse / IO / forbidden-entity error
    logs and yields [] (one bad OPML must not kill the run)."""
    from xml.etree.ElementTree import ParseError

    from defusedxml.common import DefusedXmlException
    from defusedxml.ElementTree import parse as _xml_parse

    out: list[dict] = []
    try:
        root = _xml_parse(path).getroot()
    except (ParseError, DefusedXmlException, OSError) as e:
        log(f"OPML parse error {path}: {e}")
        return out
    for node in root.iter("outline"):
        if node.get("type") == "rss" and node.get("xmlUrl"):
            out.append(
                {
                    "feed_name": node.get("text") or node.get("title") or node.get("xmlUrl"),
                    "xml_url": node.get("xmlUrl"),
                    "category": node.get("category", ""),
                }
            )
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_orchestrate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/daily-podcast/orchestrate.py tests/test_orchestrate.py
git commit -m "feat: parse_opml in orchestrate.py"
```

---

### Task 3: Ranking primitives

**Files:**
- Modify: `skills/daily-podcast/orchestrate.py`
- Test: `tests/test_orchestrate.py`

- [ ] **Step 1: Write the failing test**

```python
import datetime as dt

NOW = dt.datetime(2026, 6, 4, 12, 0, tzinfo=dt.timezone.utc)


def test_source_tier_score():
    assert orchestrate.source_tier_score("Simon Willison") == 1.0
    assert orchestrate.source_tier_score("Hacker News") == 0.2
    assert orchestrate.source_tier_score("Some Unknown Feed") == 0.5  # DEFAULT_TIER


def test_recency_score():
    assert orchestrate.recency_score(NOW, NOW, 24) == 1.0           # brand new
    old = NOW - dt.timedelta(hours=24)
    assert orchestrate.recency_score(old, NOW, 24) == 0.0           # window edge
    assert orchestrate.recency_score(None, NOW, 24) == 0.3          # unknown date


def test_concreteness_score():
    assert orchestrate.concreteness_score("Patch for CVE-2026-1234", "") == 0.2
    assert orchestrate.concreteness_score("A vague think piece", "no specifics") == 0.0


def test_variety_penalty():
    usage = {"Feed A": "2026-06-03"}  # used yesterday
    assert orchestrate.variety_penalty("Feed A", usage, NOW) == orchestrate.VARIETY_PENALTY
    assert orchestrate.variety_penalty("Feed A", {"Feed A": "2026-05-01"}, NOW) == 0.0
    assert orchestrate.variety_penalty("Feed Z", usage, NOW) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_orchestrate.py -k "tier or recency or concrete or variety" -q`
Expected: FAIL — attributes not defined.

- [ ] **Step 3: Write minimal implementation** (append)

```python
def source_tier_score(feed_name: str) -> float:
    return TIER_SCORE[SOURCE_TIERS.get(feed_name, DEFAULT_TIER)]


def recency_score(published: dt.datetime | None, now: dt.datetime, lookback_hours: int) -> float:
    if published is None:
        return 0.3
    window = max(lookback_hours * 3600, 1)
    age = (now - published).total_seconds()
    return max(0.0, min(1.0, 1.0 - age / window))


def concreteness_score(title: str, summary: str) -> float:
    return 0.2 if CONCRETE_RE.search(f"{title} {summary}") else 0.0


def variety_penalty(
    feed_name: str, feed_usage: dict, now: dt.datetime, days: int = VARIETY_DAYS
) -> float:
    last = feed_usage.get(feed_name)
    if not last:
        return 0.0
    try:
        last_date = dt.date.fromisoformat(last)
    except (ValueError, TypeError):
        return 0.0
    return VARIETY_PENALTY if (now.date() - last_date).days < days else 0.0


def score_candidate(c: dict, feed_usage: dict, now: dt.datetime, lookback_hours: int) -> float:
    return (
        WEIGHT_TIER * source_tier_score(c["feed_name"])
        + WEIGHT_RECENCY * recency_score(c["published"], now, lookback_hours)
        + WEIGHT_CONCRETE * concreteness_score(c["title"], c["summary"])
        - variety_penalty(c["feed_name"], feed_usage, now)
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_orchestrate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/daily-podcast/orchestrate.py tests/test_orchestrate.py
git commit -m "feat: ranking primitives (tier, recency, concreteness, variety)"
```

---

### Task 4: `rank_candidates` (sort + per-feed cap + top-N)

**Files:**
- Modify: `skills/daily-podcast/orchestrate.py`
- Test: `tests/test_orchestrate.py`

- [ ] **Step 1: Write the failing test**

```python
def _cand(feed, title="t", summary="", published=NOW):
    return {"feed_name": feed, "title": title, "summary": summary, "published": published,
            "url": f"https://x/{feed}/{title}", "category": ""}


def test_rank_orders_by_score_and_caps_per_feed():
    cands = [
        _cand("Hacker News", "agg"),               # tier 3, low
        _cand("Simon Willison", "orig CVE-2026-1"),  # tier 1 + concrete, high
        _cand("Ars Technica", "news 4.2"),         # tier 2 + concrete
    ]
    ranked = orchestrate.rank_candidates(cands, {}, NOW, 24, target=2, buffer=0)
    assert [c["feed_name"] for c in ranked] == ["Simon Willison", "Ars Technica"]


def test_rank_per_feed_cap():
    cands = [_cand("Ars Technica", f"n{i}") for i in range(5)]
    ranked = orchestrate.rank_candidates(cands, {}, NOW, 24, target=10, buffer=0,
                                         per_feed_cap=2)
    assert len(ranked) == 2  # capped to 2 from the same feed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_orchestrate.py -k rank -q`
Expected: FAIL — `rank_candidates` not defined.

- [ ] **Step 3: Write minimal implementation** (append)

```python
def rank_candidates(
    cands: list[dict],
    feed_usage: dict,
    now: dt.datetime,
    lookback_hours: int,
    target: int = TARGET_DEFAULT,
    buffer: int = BUFFER,
    per_feed_cap: int = PER_FEED_CAP,
) -> list[dict]:
    """Deterministic, metadata-only ranking. Sort by score desc, then greedily take
    items while honoring a per-feed cap, up to target+buffer. `sorted` is stable, so
    equal-score ties keep input order."""
    scored = sorted(
        cands, key=lambda c: score_candidate(c, feed_usage, now, lookback_hours), reverse=True
    )
    out: list[dict] = []
    per_feed: dict[str, int] = {}
    limit = target + buffer
    for c in scored:
        f = c["feed_name"]
        if per_feed.get(f, 0) >= per_feed_cap:
            continue
        out.append(c)
        per_feed[f] = per_feed.get(f, 0) + 1
        if len(out) >= limit:
            break
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_orchestrate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/daily-podcast/orchestrate.py tests/test_orchestrate.py
git commit -m "feat: rank_candidates with per-feed cap and top-N"
```

---

### Task 5: `gather_candidates` (feedparser injected)

**Files:**
- Modify: `skills/daily-podcast/orchestrate.py`
- Test: `tests/test_orchestrate.py`

- [ ] **Step 1: Write the failing test**

```python
def test_gather_filters_lookback_dedup_and_clean(tmp_path):
    opml = tmp_path / "f.opml"
    opml.write_text(
        '<opml><body><outline type="rss" text="Feed A" xmlUrl="https://a/rss"/></body></opml>'
    )
    fresh = (2026, 6, 4, 9, 0, 0, 0, 0, 0)   # 3h before NOW
    stale = (2026, 6, 2, 9, 0, 0, 0, 0, 0)   # >24h before NOW

    def fake_parse(url):
        return {"entries": [
            {"title": "Fresh <b>x</b>", "link": "https://a/1",
             "summary": "<p>body</p>", "published_parsed": fresh},
            {"title": "Stale", "link": "https://a/2", "summary": "old",
             "published_parsed": stale},
            {"title": "Dup", "link": "https://a/covered", "summary": "s",
             "published_parsed": fresh},
        ]}

    out = orchestrate.gather_candidates(
        [str(opml)], 24, {"https://a/covered": {}}, NOW, parse=fake_parse
    )
    assert len(out) == 1
    assert out[0]["url"] == "https://a/1"
    assert out[0]["title"] == "Fresh x"        # tags stripped
    assert out[0]["summary"] == "body"
    assert out[0]["feed_name"] == "Feed A"


def test_gather_feed_exception_is_skipped(tmp_path):
    opml = tmp_path / "f.opml"
    opml.write_text('<opml><body><outline type="rss" text="A" xmlUrl="https://a/rss"/></body></opml>')

    def boom(url):
        raise OSError("timeout")

    assert orchestrate.gather_candidates([str(opml)], 24, {}, NOW, parse=boom) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_orchestrate.py -k gather -q`
Expected: FAIL — `gather_candidates` not defined.

- [ ] **Step 3: Write minimal implementation** (append)

```python
def _clean(s: str, limit: int = 600) -> str:
    s = _TAG_RE.sub(" ", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def _first_content(entry: dict) -> str:
    content = entry.get("content")
    if content and isinstance(content, list) and content[0].get("value"):
        return content[0]["value"]
    return ""


def _entry_published(entry: dict) -> dt.datetime | None:
    p = entry.get("published_parsed")
    if not p:
        return None
    try:
        return dt.datetime(*p[:6], tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def gather_candidates(
    opml_paths: list[str],
    lookback_hours: int,
    covered: dict,
    now: dt.datetime,
    parse: Callable | None = None,
) -> list[dict]:
    """Parse OPML, fetch each feed (no LLM), keep entries within the lookback window
    that aren't already covered. METADATA ONLY — never fetches article bodies. A feed
    that errors is logged and skipped."""
    if parse is None:
        import feedparser  # lazy: keeps unit tests import-light

        parse = feedparser.parse
    cutoff = now - dt.timedelta(hours=lookback_hours)
    feeds: list[dict] = []
    for p in opml_paths:
        feeds.extend(parse_opml(Path(p)))
    seen: set[str] = set()
    out: list[dict] = []
    for feed in feeds:
        try:
            d = parse(feed["xml_url"])
        except Exception as e:  # noqa: BLE001 - one bad feed must not kill the run
            log(f"feed error {feed['feed_name']}: {e}")
            continue
        entries = d.get("entries", []) if hasattr(d, "get") else getattr(d, "entries", [])
        for entry in entries:
            url = entry.get("link") or ""
            if not url or url in covered or url in seen:
                continue
            published = _entry_published(entry)
            if published is not None and published < cutoff:
                continue
            seen.add(url)
            out.append(
                {
                    "title": _clean(entry.get("title", "")),
                    "url": url,
                    "published": published,
                    "summary": _clean(entry.get("summary") or _first_content(entry)),
                    "feed_name": feed["feed_name"],
                    "category": feed.get("category", ""),
                }
            )
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_orchestrate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/daily-podcast/orchestrate.py tests/test_orchestrate.py
git commit -m "feat: gather_candidates (metadata-only feed gathering)"
```

---

### Task 6: `classify_output` (pure)

**Files:**
- Modify: `skills/daily-podcast/orchestrate.py`
- Test: `tests/test_orchestrate.py`

- [ ] **Step 1: Write the failing test**

```python
def test_classify_ok():
    r = orchestrate.classify_output('{"ok": true, "segment": "hello", "source_url": "u"}', "", 0)
    assert r["outcome"] == "OK" and r["segment"] == "hello"


def test_classify_refused():
    r = orchestrate.classify_output('{"ok": false, "reason": "not news"}', "", 0)
    assert r["outcome"] == "REFUSED" and "not news" in r["detail"]


def test_classify_blocked_on_policy_marker():
    r = orchestrate.classify_output(
        "", "API Error ... violative cyber content ... Usage Policy", 1
    )
    assert r["outcome"] == "BLOCKED"


def test_classify_error_when_garbage():
    r = orchestrate.classify_output("blah no json", "", 1)
    assert r["outcome"] == "ERROR"


def test_classify_ok_requires_nonempty_segment():
    r = orchestrate.classify_output('{"ok": true, "segment": "   "}', "", 0)
    assert r["outcome"] == "ERROR"  # empty segment is not a usable success
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_orchestrate.py -k classify -q`
Expected: FAIL — `classify_output` not defined.

- [ ] **Step 3: Write minimal implementation** (append)

```python
def classify_output(stdout: str, stderr: str, returncode: int) -> dict:
    """Map one claude -p result to an outcome. Pure: no I/O.
    OK (valid {"ok":true} with non-empty segment) | REFUSED ({"ok":false}) |
    BLOCKED (policy markers, no usable JSON) | ERROR (everything else)."""
    obj = extract_last_json(stdout)
    if isinstance(obj, dict) and obj.get("ok") is True and str(obj.get("segment", "")).strip():
        return {
            "outcome": "OK",
            "segment": obj["segment"].strip(),
            "source_url": obj.get("source_url", ""),
            "detail": "",
        }
    if isinstance(obj, dict) and obj.get("ok") is False:
        return {
            "outcome": "REFUSED",
            "segment": None,
            "source_url": None,
            "detail": str(obj.get("reason", ""))[:300],
        }
    if POLICY_RE.search(f"{stdout}\n{stderr}"):
        return {"outcome": "BLOCKED", "segment": None, "source_url": None,
                "detail": "usage-policy classifier"}
    return {
        "outcome": "ERROR",
        "segment": None,
        "source_url": None,
        "detail": (stderr or stdout or f"exit {returncode}").strip()[:300],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_orchestrate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/daily-podcast/orchestrate.py tests/test_orchestrate.py
git commit -m "feat: classify_output (OK/REFUSED/BLOCKED/ERROR)"
```

---

### Task 7: `summarize_item` (subprocess injected, timeout)

**Files:**
- Modify: `skills/daily-podcast/orchestrate.py`
- Test: `tests/test_orchestrate.py`

- [ ] **Step 1: Write the failing test**

```python
import subprocess
from types import SimpleNamespace

ITEM = {"title": "T", "url": "https://x/1", "feed_name": "Feed A"}
TPL = "title=<<TITLE>> url=<<URL>> feed=<<FEED>>"


def test_summarize_item_ok():
    captured = {}

    def runner(cmd, **kw):
        captured["cmd"] = cmd
        return SimpleNamespace(
            stdout='{"ok": true, "segment": "seg", "source_url": "ignored"}', stderr="",
            returncode=0)

    r = orchestrate.summarize_item(ITEM, TPL, runner=runner)
    assert r["outcome"] == "OK"
    assert r["source_url"] == "https://x/1"        # forced to the item url
    assert r["feed_name"] == "Feed A" and r["url"] == "https://x/1"
    assert "title=T url=https://x/1 feed=Feed A" in captured["cmd"][2]  # template filled


def test_summarize_item_timeout():
    def runner(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    r = orchestrate.summarize_item(ITEM, TPL, timeout=1, runner=runner)
    assert r["outcome"] == "TIMEOUT"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_orchestrate.py -k summarize_item -q`
Expected: FAIL — `summarize_item` not defined.

- [ ] **Step 3: Write minimal implementation** (append)

```python
def _drop_fields(item: dict) -> dict:
    return {"feed_name": item["feed_name"], "url": item["url"], "title": item["title"]}


def fill_prompt(template: str, item: dict) -> str:
    return (
        template.replace("<<TITLE>>", item["title"])
        .replace("<<URL>>", item["url"])
        .replace("<<FEED>>", item["feed_name"])
    )


def summarize_item(
    item: dict,
    prompt_template: str,
    timeout: int = SUMMARIZE_TIMEOUT_S,
    claude_bin: str = "claude",
    runner: Callable = subprocess.run,
) -> dict:
    """Summarize ONE item in its own isolated claude -p. Returns a result dict carrying
    the item's feed_name/url/title plus outcome/segment/source_url/detail."""
    prompt = fill_prompt(prompt_template, item)
    try:
        proc = runner(
            [claude_bin, "-p", prompt], capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return {
            **_drop_fields(item),
            "outcome": "TIMEOUT",
            "segment": None,
            "source_url": None,
            "detail": f"timeout after {timeout}s",
        }
    result = classify_output(proc.stdout or "", proc.stderr or "", proc.returncode)
    if result["outcome"] == "OK":
        result["source_url"] = item["url"]  # trust our url, not the model's echo
    return {**_drop_fields(item), **result}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_orchestrate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/daily-podcast/orchestrate.py tests/test_orchestrate.py
git commit -m "feat: summarize_item (isolated claude -p per item)"
```

---

### Task 8: `fan_out` (concurrency, survivors + dropped)

**Files:**
- Modify: `skills/daily-podcast/orchestrate.py`
- Test: `tests/test_orchestrate.py`

- [ ] **Step 1: Write the failing test**

```python
def test_fan_out_keeps_survivors_in_order_and_logs_drops():
    ranked = [
        {"title": "A", "url": "u/a", "feed_name": "F1"},
        {"title": "B", "url": "u/b", "feed_name": "F2"},  # will be blocked
        {"title": "C", "url": "u/c", "feed_name": "F3"},
    ]

    def fake_summarize(item, tpl, **kw):
        base = orchestrate._drop_fields(item)
        if item["title"] == "B":
            return {**base, "outcome": "BLOCKED", "segment": None, "source_url": None,
                    "detail": "usage-policy classifier"}
        return {**base, "outcome": "OK", "segment": f"seg-{item['title']}",
                "source_url": item["url"], "detail": ""}

    survivors, dropped = orchestrate.fan_out(
        ranked, "tpl", target=10, concurrency=2, summarize=fake_summarize)
    assert [s["title"] for s in survivors] == ["A", "C"]
    assert survivors[0]["feed_name"] == "F1"
    assert len(dropped) == 1 and dropped[0]["reason"] == "blocked" and dropped[0]["url"] == "u/b"


def test_fan_out_respects_target_cap():
    ranked = [{"title": f"T{i}", "url": f"u/{i}", "feed_name": "F"} for i in range(5)]

    def ok(item, tpl, **kw):
        return {**orchestrate._drop_fields(item), "outcome": "OK",
                "segment": "s", "source_url": item["url"], "detail": ""}

    survivors, dropped = orchestrate.fan_out(ranked, "tpl", target=3, summarize=ok)
    assert len(survivors) == 3 and dropped == []  # extras beyond target are unused, not dropped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_orchestrate.py -k fan_out -q`
Expected: FAIL — `fan_out` not defined.

- [ ] **Step 3: Write minimal implementation** (append)

```python
def fan_out(
    ranked: list[dict],
    prompt_template: str,
    target: int = TARGET_DEFAULT,
    concurrency: int = CONCURRENCY_DEFAULT,
    summarize: Callable = summarize_item,
) -> tuple[list[dict], list[dict]]:
    """Summarize ranked items concurrently (each isolated). Preserve ranked order via
    indexed results. Keep the first `target` OKs as survivors; log every non-OK as a
    drop. A crashed worker drops its item, never the run."""
    results: list[dict | None] = [None] * len(ranked)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futs = {ex.submit(summarize, item, prompt_template): i for i, item in enumerate(ranked)}
        for fut in concurrent.futures.as_completed(futs):
            i = futs[fut]
            try:
                results[i] = fut.result()
            except Exception as e:  # noqa: BLE001
                item = ranked[i]
                results[i] = {
                    **_drop_fields(item),
                    "outcome": "ERROR",
                    "segment": None,
                    "source_url": None,
                    "detail": str(e)[:300],
                }
    survivors: list[dict] = []
    dropped: list[dict] = []
    for r in results:
        if r is None:
            continue
        if r["outcome"] == "OK" and len(survivors) < target:
            survivors.append(
                {"title": r["title"], "segment": r["segment"],
                 "source_url": r["source_url"], "feed_name": r["feed_name"]}
            )
        elif r["outcome"] != "OK":
            dropped.append(
                {"feed_name": r["feed_name"], "url": r["url"],
                 "reason": r["outcome"].lower(), "detail": r["detail"]}
            )
    return survivors, dropped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_orchestrate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/daily-podcast/orchestrate.py tests/test_orchestrate.py
git commit -m "feat: fan_out with per-item drop-on-block"
```

---

### Task 9: `make_intro_outro` + deterministic fallback

**Files:**
- Modify: `skills/daily-podcast/orchestrate.py`
- Test: `tests/test_orchestrate.py`

- [ ] **Step 1: Write the failing test**

```python
def test_fallback_intro_outro_pluralization():
    one = orchestrate.fallback_intro_outro("June 4, 2026", 1)
    assert "1 story today" in one["intro"]
    many = orchestrate.fallback_intro_outro("June 4, 2026", 3)
    assert "3 stories today" in many["intro"]
    assert many["outro"] and many["summary"]


def test_make_intro_outro_uses_llm_json():
    def runner(cmd, **kw):
        return SimpleNamespace(
            stdout='{"intro": "I", "outro": "O", "summary": "S"}', stderr="", returncode=0)

    out = orchestrate.make_intro_outro(["A", "B"], "June 4, 2026", runner=runner)
    assert out == {"intro": "I", "outro": "O", "summary": "S"}


def test_make_intro_outro_falls_back_on_garbage():
    def runner(cmd, **kw):
        return SimpleNamespace(stdout="no json here", stderr="", returncode=0)

    out = orchestrate.make_intro_outro(["A", "B"], "June 4, 2026", runner=runner)
    assert "2 stories today" in out["intro"]  # deterministic fallback
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_orchestrate.py -k intro_outro -q`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Write minimal implementation** (append)

```python
def fallback_intro_outro(date_long: str, n: int) -> dict:
    noun = "story" if n == 1 else "stories"
    return {
        "intro": f"Today's digest for {date_long}. {n} {noun} today. Here's the rundown.",
        "outro": "That's the digest for today. Thanks for listening.",
        "summary": f"{n} {noun} for {date_long}.",
    }


def make_intro_outro(
    titles: list[str],
    date_long: str,
    claude_bin: str = "claude",
    runner: Callable = subprocess.run,
    timeout: int = SUMMARIZE_TIMEOUT_S,
) -> dict:
    """Write intro/outro/summary from the kept TITLES ONLY (benign — no bodies, so no
    block risk). Any failure → deterministic fallback so a run never dies here."""
    if not titles:
        return fallback_intro_outro(date_long, 0)
    headlines = "\n".join(f"- {t}" for t in titles)
    prompt = (
        "You are writing the intro and sign-off for a daily NEWS-DIGEST podcast. Below are "
        f"the headlines for today's episode (titles only). Write for {date_long}.\n\n"
        f"HEADLINES:\n{headlines}\n\n"
        f'INTRO (~350 chars): "Today\'s digest for {date_long}. <N> stories today, covering '
        "<2-4 word theme list>. Here's the rundown.\" Use the real count and 2-4 topic words "
        "drawn from the headlines.\n"
        "SIGN-OFF (~250 chars): brief, no new content, do not mention show notes or links.\n"
        "SUMMARY: one sentence hook for the show-notes preview.\n"
        'Output ONLY one JSON object as the final line: {"intro": "...", "outro": "...", '
        '"summary": "..."}'
    )
    try:
        proc = runner(
            [claude_bin, "-p", prompt], capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return fallback_intro_outro(date_long, len(titles))
    obj = extract_last_json(proc.stdout or "")
    if isinstance(obj, dict) and all(
        isinstance(obj.get(k), str) and obj[k].strip() for k in ("intro", "outro", "summary")
    ):
        return {k: obj[k].strip() for k in ("intro", "outro", "summary")}
    return fallback_intro_outro(date_long, len(titles))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_orchestrate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/daily-podcast/orchestrate.py tests/test_orchestrate.py
git commit -m "feat: make_intro_outro with deterministic fallback"
```

---

### Task 10: `assemble_manifest`

**Files:**
- Modify: `skills/daily-podcast/orchestrate.py`
- Test: `tests/test_orchestrate.py`

- [ ] **Step 1: Write the failing test**

```python
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
    assert m["segments"][0]["source_url"] is None       # intro
    assert m["segments"][1]["source_url"] == "u/a"      # 1:1 mapping
    assert m["segments"][-1]["title"] == "Sign-off"
```

This manifest must satisfy `render.py`'s `validate_manifest`: non-empty string `title`/`summary`, non-empty `segments` each with non-empty `text`, `source_url` http(s)-or-None, `voice` in {house,random,…}.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_orchestrate.py -k assemble -q`
Expected: FAIL — `assemble_manifest` not defined.

- [ ] **Step 3: Write minimal implementation** (append)

```python
def assemble_manifest(
    date_long: str, date_iso: str, survivors: list[dict], intro_outro: dict
) -> dict:
    """Build the render.py manifest: intro + one segment per survivor (strict 1:1
    source mapping) + sign-off. Shape matches render.validate_manifest."""
    segments: list[dict] = [
        {"title": "Intro", "text": intro_outro["intro"], "source_url": None}
    ]
    for s in survivors:
        segments.append(
            {"title": (s["title"][:120] or "Story"), "text": s["segment"],
             "source_url": s["source_url"]}
        )
    segments.append({"title": "Sign-off", "text": intro_outro["outro"], "source_url": None})
    return {
        "title": f"Daily Digest - {date_long}",
        "summary": intro_outro["summary"],
        "voice": "house",
        "date": date_iso,
        "segments": segments,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_orchestrate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/daily-podcast/orchestrate.py tests/test_orchestrate.py
git commit -m "feat: assemble_manifest (render.py-shaped)"
```

---

### Task 11: State I/O — config/covered/feed_usage/dropped

**Files:**
- Modify: `skills/daily-podcast/orchestrate.py`
- Test: `tests/test_orchestrate.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_orchestrate.py -k "covered or feed_usage or dropped" -q`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Write minimal implementation** (append)

```python
def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def load_covered() -> dict:
    try:
        data = json.loads(COVERED_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def load_feed_usage(path: Path | None = None) -> dict:
    path = path or FEED_USAGE_PATH
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def update_feed_usage(feed_names, date_iso: str, path: Path | None = None) -> None:
    path = path or FEED_USAGE_PATH
    usage = load_feed_usage(path)
    for f in feed_names:
        usage[f] = date_iso
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(usage, indent=2))
    except OSError as e:
        log(f"could not write feed usage: {e}")


def write_dropped_log(dropped: list[dict], run_date: str, path: Path | None = None) -> None:
    path = path or DROPPED_LOG_PATH
    if not dropped:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            for d in dropped:
                f.write(
                    json.dumps(
                        {
                            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                            "run_date": run_date,
                            "feed_name": d["feed_name"],
                            "url": d["url"],
                            "reason": d["reason"],
                            "detail": d.get("detail", ""),
                        }
                    )
                    + "\n"
                )
    except OSError as e:
        log(f"could not write dropped log: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_orchestrate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/daily-podcast/orchestrate.py tests/test_orchestrate.py
git commit -m "feat: state I/O (config, covered, feed_usage, dropped log)"
```

---

### Task 12: `run_render` + `build_report`

**Files:**
- Modify: `skills/daily-podcast/orchestrate.py`
- Test: `tests/test_orchestrate.py`

- [ ] **Step 1: Write the failing test**

```python
def test_run_render_parses_result(tmp_path):
    def runner(cmd, **kw):
        assert "--dry-run" not in cmd
        return SimpleNamespace(
            stdout='{\n  "status": "ready",\n  "episode_uri": "spotify:episode:1",\n'
            '  "title": "Daily Digest - June 4, 2026",\n  "chapter_count": 5,\n'
            '  "duration_s": 412.3,\n  "r2_status": "published"\n}',
            stderr="", returncode=0)

    res = orchestrate.run_render(tmp_path / "m.json", tmp_path, dry_run=False, runner=runner)
    assert res["episode_uri"] == "spotify:episode:1" and res["chapter_count"] == 5


def test_run_render_raises_on_failure(tmp_path):
    def runner(cmd, **kw):
        return SimpleNamespace(stdout="", stderr="boom: ffmpeg missing", returncode=1)

    import pytest
    with pytest.raises(orchestrate.RenderError, match="ffmpeg missing"):
        orchestrate.run_render(tmp_path / "m.json", tmp_path, dry_run=False, runner=runner)


def test_build_report_shipped_and_dryrun():
    ready = {"status": "ready", "episode_uri": "spotify:episode:1",
             "title": "T", "chapter_count": 5, "duration_s": 412.3, "r2_status": "published"}
    line = orchestrate.build_report(ready)
    assert line == "SHIPPED spotify:episode:1 - T - 5 chapters - 412.3s - r2=ok"
    dry = {"status": "dry-run", "title": "T", "chapter_count": 5, "duration_s": 412.3,
           "r2_status": None}
    assert orchestrate.build_report(dry).startswith("DRY-RUN ok - T - 5 chapters")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_orchestrate.py -k "run_render or build_report" -q`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Write minimal implementation** (append)

```python
def run_render(
    manifest_path: Path, workdir: Path, dry_run: bool, runner: Callable = subprocess.run
) -> dict:
    """Invoke render.py by path and parse its final result JSON (printed indent=2).
    Raise RenderError on non-zero exit or unparseable output."""
    cmd = [
        sys.executable,
        str(RENDER_PY),
        "--manifest",
        str(manifest_path),
        "--workdir",
        str(workdir),
    ]
    if dry_run:
        cmd.append("--dry-run")
    proc = runner(cmd, capture_output=True, text=True)
    result = extract_last_json(proc.stdout or "")
    if proc.returncode != 0 or not isinstance(result, dict):
        tail = (proc.stderr or proc.stdout or "render failed").strip().splitlines()
        raise RenderError(tail[-1] if tail else "render failed")
    return result


def build_report(result: dict) -> str:
    """The single-line stdout contract (mirrors prompts/daily.md step 9)."""
    r2 = {"published": "ok", "skipped": "skipped", "failed": "FAILED"}.get(
        result.get("r2_status"), "skipped"
    )
    cc = result.get("chapter_count", "?")
    dur = result.get("duration_s", "?")
    title = result.get("title", "")
    if result.get("status") == "dry-run":
        return f"DRY-RUN ok - {title} - {cc} chapters - {dur}s"
    return (
        f"SHIPPED {result.get('episode_uri', '')} - {title} - "
        f"{cc} chapters - {dur}s - r2={r2}"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_orchestrate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/daily-podcast/orchestrate.py tests/test_orchestrate.py
git commit -m "feat: run_render + build_report"
```

---

### Task 13: `main()` wiring + `prompts/summarize_item.md`

**Files:**
- Modify: `skills/daily-podcast/orchestrate.py`
- Create: `skills/daily-podcast/prompts/summarize_item.md`
- Test: `tests/test_orchestrate.py`

- [ ] **Step 1: Create the prompt file**

```markdown
You write ONE segment for a daily NEWS-DIGEST podcast covering technology and security news. You are given ONE news item. Use WebFetch to read the article at the URL, then write a single spoken podcast segment reporting it.

ITEM
- title: <<TITLE>>
- feed: <<FEED>>
- url: <<URL>>

RULES — this is JOURNALISM written at a reporting altitude:
- Report what happened, who is affected, why it matters, and the response or fix.
- This is news, NOT a how-to. Do NOT include exploit code, payloads, working commands, or step-by-step attack or intrusion procedures. If the article centers on such operational detail, report only the newsworthy facts (that a flaw, breach, or campaign exists, its impact, and the mitigation) and leave out the method.
- 600 to 900 characters, one paragraph, spoken style. No URLs in the text. Spell out abbreviations ("D R I", "CLAUDE dot md"). Numbers under ten as words. No em dashes; use hyphens. End on analysis, not a pointer to the source.

OUTPUT: print exactly ONE JSON object as your final output and nothing after it:
{"ok": true, "segment": "<the spoken segment>", "source_url": "<<URL>>"}
If you genuinely cannot summarize this item, print instead:
{"ok": false, "reason": "<short reason>"}
```

- [ ] **Step 2: Write the failing test** (mocks gather/fan_out/render so no network/LLM)

```python
def test_main_happy_path(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate, "CONFIG_PATH", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(
        '{"opml_files": ["/x.opml"], "lookback_hours": 24, "target_item_count": 2}')
    monkeypatch.setattr(orchestrate, "COVERED_PATH", tmp_path / "covered.json")
    monkeypatch.setattr(orchestrate, "FEED_USAGE_PATH", tmp_path / "feed_usage.json")
    monkeypatch.setattr(orchestrate, "DROPPED_LOG_PATH", tmp_path / "dropped.jsonl")
    monkeypatch.setattr(orchestrate, "SUMMARIZE_PROMPT_PATH", tmp_path / "p.md")
    (tmp_path / "p.md").write_text("PROMPT <<TITLE>>")

    monkeypatch.setattr(orchestrate, "gather_candidates",
                        lambda *a, **k: [{"title": "A", "url": "u/a", "feed_name": "F1",
                                          "summary": "", "published": None, "category": ""}])
    monkeypatch.setattr(orchestrate, "fan_out",
                        lambda *a, **k: ([{"title": "A", "segment": "seg a",
                                           "source_url": "u/a", "feed_name": "F1"}], []))
    monkeypatch.setattr(orchestrate, "make_intro_outro",
                        lambda *a, **k: {"intro": "I", "outro": "O", "summary": "S"})
    captured = {}

    def fake_render(manifest_path, workdir, dry_run, runner=None):
        captured["manifest"] = orchestrate.json.loads(Path(manifest_path).read_text())
        return {"status": "ready", "episode_uri": "spotify:episode:9", "title": "T",
                "chapter_count": 3, "duration_s": 100.0, "r2_status": "skipped"}

    monkeypatch.setattr(orchestrate, "run_render", fake_render)
    rc = orchestrate.main(["--workdir", str(tmp_path / "wd")])
    assert rc == 0
    # 1:1 mapping survived into the manifest (intro + 1 story + outro)
    assert len(captured["manifest"]["segments"]) == 3
    assert (tmp_path / "feed_usage.json").exists()  # updated on ready


def test_main_no_survivors_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate, "CONFIG_PATH", tmp_path / "config.json")
    (tmp_path / "config.json").write_text('{"opml_files": ["/x.opml"]}')
    monkeypatch.setattr(orchestrate, "COVERED_PATH", tmp_path / "c.json")
    monkeypatch.setattr(orchestrate, "SUMMARIZE_PROMPT_PATH", tmp_path / "p.md")
    (tmp_path / "p.md").write_text("P")
    monkeypatch.setattr(orchestrate, "gather_candidates",
                        lambda *a, **k: [{"title": "A", "url": "u/a", "feed_name": "F",
                                          "summary": "", "published": None, "category": ""}])
    monkeypatch.setattr(orchestrate, "fan_out",
                        lambda *a, **k: ([], [{"feed_name": "F", "url": "u/a",
                                               "reason": "blocked", "detail": "x"}]))
    monkeypatch.setattr(orchestrate, "DROPPED_LOG_PATH", tmp_path / "d.jsonl")
    rc = orchestrate.main(["--workdir", str(tmp_path / "wd")])
    assert rc == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/test_orchestrate.py -k main -q`
Expected: FAIL — `main` not defined.

- [ ] **Step 4: Write minimal implementation** (append)

```python
def _fail(msg: str) -> int:
    print(f"FAILED {msg}")
    return 1


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Unattended per-item daily-podcast orchestrator")
    ap.add_argument(
        "--dry-run", action="store_true",
        help="forward to render.py; skip upload + feed_usage write",
    )
    ap.add_argument("--workdir", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=0, help="cap items fanned out (testing)")
    ap.add_argument("--manifest-only", action="store_true", help="assemble manifest then stop")
    ap.add_argument("--concurrency", type=int, default=CONCURRENCY_DEFAULT)
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    now = dt.datetime.now(dt.timezone.utc)
    date_long = now.strftime("%B %-d, %Y")
    date_iso = now.date().isoformat()

    config = load_config()
    opml_files = config.get("opml_files") or []
    if not opml_files:
        return _fail("no opml_files in config")
    lookback = int(config.get("lookback_hours", 24))
    target = int(config.get("target_item_count", TARGET_DEFAULT))

    candidates = gather_candidates(opml_files, lookback, load_covered(), now)
    log(f"gathered {len(candidates)} candidates")
    if not candidates:
        return _fail("no candidates after gather/dedup")

    ranked = rank_candidates(candidates, load_feed_usage(), now, lookback, target=target)
    if args.limit:
        ranked = ranked[: args.limit]
    log(f"ranked top {len(ranked)} for summarization")

    prompt_template = SUMMARIZE_PROMPT_PATH.read_text()
    survivors, dropped = fan_out(
        ranked, prompt_template, target=target, concurrency=args.concurrency
    )
    write_dropped_log(dropped, date_iso)
    log(f"survivors={len(survivors)} dropped={len(dropped)}")
    if not survivors:
        return _fail("no viable items (all dropped/blocked)")

    intro_outro = make_intro_outro([s["title"] for s in survivors], date_long)
    manifest = assemble_manifest(date_long, date_iso, survivors, intro_outro)

    workdir = (
        Path(args.workdir)
        if args.workdir
        else Path(tempfile.gettempdir()) / f"daily-podcast-{date_iso}"
    )
    workdir.mkdir(parents=True, exist_ok=True)
    manifest_path = workdir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    if args.manifest_only:
        print(f"MANIFEST {manifest_path} ({len(survivors)} segments)")
        return 0

    try:
        result = run_render(manifest_path, workdir, args.dry_run)
    except RenderError as e:
        return _fail(str(e))

    if not args.dry_run and result.get("status") == "ready":
        update_feed_usage(sorted({s["feed_name"] for s in survivors}), date_iso)

    print(build_report(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the full suite + lint**

Run: `python3 -m pytest -q && ruff check skills/ tests/ && ruff format --check skills/ tests/`
Expected: all tests pass; ruff clean. (If `ruff format --check` flags `orchestrate.py`/`test_orchestrate.py`, run `ruff format skills/daily-podcast/orchestrate.py tests/test_orchestrate.py` and re-stage.)

- [ ] **Step 6: Commit**

```bash
git add skills/daily-podcast/orchestrate.py skills/daily-podcast/prompts/summarize_item.md tests/test_orchestrate.py
git commit -m "feat: orchestrate.py main() + summarize_item prompt"
```

---

### Task 14: Documentation updates

**Files:**
- Modify: `skills/daily-podcast/prompts/daily.md` (deprecation banner)
- Modify: `SKILL.md`, `README.md`, `CLAUDE.md`

No automated test; verification is ruff/pytest still green + the grep checks below.

- [ ] **Step 1: Add the deprecation banner to the top of `prompts/daily.md`** (immediately under the `# Daily Podcast Run` heading)

```markdown
> **DEPRECATED for unattended runs.** The cron path now uses
> `skills/daily-podcast/orchestrate.py`, which gathers + curates deterministically
> and summarizes each item in its own isolated `claude -p` so a cyber-content
> classifier block drops only that item instead of failing the whole run. This
> prompt is kept as a reference for the segment/voice rules and the manifest shape.
> See `docs/superpowers/specs/2026-06-04-per-item-orchestrator-design.md`.
```

- [ ] **Step 2: Update `SKILL.md`** — in "Running the pipeline → Headless" and "Scheduled runs (cron / launchd)", replace the `claude -p "$(cat .../prompts/daily.md)"` invocation with:

```bash
python3 skills/daily-podcast/orchestrate.py --prune-workdirs 7   # (orchestrate forwards unknown render flags? no — see note)
```

Note for the implementer: `orchestrate.py` does **not** accept `--prune-workdirs`; keep the cron recipe to `python3 skills/daily-podcast/orchestrate.py` and leave `--selftest`/`--prune-workdirs` as separate `render.py` calls if desired. Add a short "Orchestrator (unattended)" subsection documenting: deterministic gather/curation, per-item `claude -p`, drop-on-block, the new state files `feed_usage.json` and `dropped.jsonl`, and the CLI flags (`--dry-run`, `--workdir`, `--limit`, `--manifest-only`, `--concurrency`).

- [ ] **Step 3: Update `README.md`** — change the scheduled-run recipe to call `orchestrate.py`; mention `dropped.jsonl` under config files.

- [ ] **Step 4: Update project `CLAUDE.md`** — under "Architecture: the big picture", add `orchestrate.py` as the unattended entry point and state the **core invariant** ("no LLM request holds more than one article body; curation is deterministic metadata-only; per-item blocks are dropped + logged to `dropped.jsonl`; `feed_usage.json` drives the variety penalty"). Note `daily.md` is a deprecated reference. Keep render.py's invariants section unchanged.

- [ ] **Step 5: Verify**

Run:
```bash
python3 -m pytest -q && ruff check skills/ tests/
grep -rl "orchestrate.py" SKILL.md README.md CLAUDE.md docs/ | sort
grep -q "DEPRECATED for unattended runs" skills/daily-podcast/prompts/daily.md && echo "banner ok"
```
Expected: tests pass, ruff clean, all four docs reference `orchestrate.py`, banner present.

- [ ] **Step 6: Commit**

```bash
git add skills/daily-podcast/prompts/daily.md SKILL.md README.md CLAUDE.md
git commit -m "docs: document orchestrate.py; deprecate daily.md as reference"
```

---

## Cutover gate (manual, before flipping cron)

Not a code task — a release gate from the spec. Before changing the cron from `daily.md` to `orchestrate.py`, run one real-scale dry run and inspect drops:

```bash
python3 skills/daily-podcast/orchestrate.py --dry-run --workdir /tmp/orch-dryrun
cat ~/.config/daily-podcast/dropped.jsonl   # how many / which feeds blocked at real scale
```

Confirm a reasonable number of survivors and that the dropped set is sane. Only then update the scheduled job. The `dropped.jsonl` from this run is also concrete evidence for the Cyber Verification Program follow-up.

---

## Self-review

**1. Spec coverage:**
- Core invariant (no pooling; metadata-only curation) → Tasks 3–5 (ranking/gather read only metadata). ✓
- Per-item isolation + drop-on-block → Tasks 6–8. ✓
- Deterministic ranking (tier/recency/variety/concreteness, per-feed cap, target+buffer) → Tasks 3–4. ✓
- Degradation (survivors<target ships; 0 → FAILED) → Task 8 + Task 13 `main`. ✓
- Intro/outro isolated + fallback → Task 9. ✓
- Manifest shape unchanged for render.py → Task 10 (matches `validate_manifest`). ✓
- `render.py` unchanged, invoked by path; report line → Task 12. ✓
- Observability `dropped.jsonl`; `feed_usage.json` state → Tasks 11, 13. ✓
- `summarize_item.md`; `daily.md` deprecated reference; SKILL/README/CLAUDE docs → Tasks 13–14. ✓
- Cutover gate → documented above. ✓
- No-evasion boundary: enforced by design (one article per request; blocked items excluded, never reassembled) — reinforced in the prompt + docs. ✓

**2. Placeholder scan:** No "TBD"/"implement later". Every code step has complete code; every test has real assertions. The one prose-only task (14) gives the exact banner text and concrete doc edits. ✓

**3. Type consistency:** Result dicts carry a stable key set — `summarize_item`/`fan_out` workers return `{feed_name,url,title,outcome,segment,source_url,detail}`; survivors carry `{title,segment,source_url,feed_name}`; dropped carry `{feed_name,url,reason,detail}`. `classify_output` returns `{outcome,segment,source_url,detail}` merged with `_drop_fields`. Names match across Tasks 6→7→8→10→13. `extract_last_json` (Task 1) reused by `classify_output`, `make_intro_outro`, `run_render`. ✓
