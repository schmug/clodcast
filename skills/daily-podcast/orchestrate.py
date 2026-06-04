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
