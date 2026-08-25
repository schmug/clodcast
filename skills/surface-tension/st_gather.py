#!/usr/bin/env python3
"""
st_gather.py — Surface Tension's two-source candidate gather (#175).

Pulls candidates from the bubbles.town feeds AND the OPML "blogs" category into
ONE schema, then ranks them deterministically on the vote count that already
rides the feed. No LLM, no article fetch: this is the gather half of the repo's
one-body-per-request invariant.

Why votes rather than a heuristic: both existing shows approximate quality with
an invented proxy — `concreteness_score` in the daily show, `TYPE_PRIORITY +
10*log10(stars)` in Frontier Commits. A vote count is a human quality signal that
is ALREADY metadata, so this show satisfies the curation invariant more honestly
than either predecessor rather than less.

Feed shapes are the measured ones from the 2026-08-25 recon (#173), pinned by
tests/test_bubbles_fixtures.py — not a described format. The three findings that
shaped this module:

  1. Votes are INLINE at media:community/media:statistics/@favorites, and
     feedparser FLATTENS that nesting: read `entry.media_statistics["favorites"]`,
     never `entry.media_community` (the empty string). Values are attribute
     strings and need int() coercion. `/api/vote-count` agreed 10/10 and has no
     batch form, so a per-candidate round trip would buy nothing.
  2. `/feed/hot` is ranked by DISCUSSION, not recency — its oldest entry was ~16
     weeks old and only 12 of 17 fell inside a 168 h window. A uniform lookback
     would discard exactly the posts that feed exists to surface, so the lookback
     filter is PER FEED (see `feed_specs`).
  3. Atom `content` is the ENTIRE post body and feedparser mirrors it into
     `summary`. Only `SUMMARY_MAX_CHARS` keeps the ranking metadata-only.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlsplit

# Its OWN config dir, following the frontier-commits precedent. Not cosmetic:
# covered.json is the dedup source of truth, and a dir shared with the daily
# digest means a blog post covered here is silently withheld there, and vice
# versa. Env override so tests never touch real user state (conftest patches it).
CONFIG_DIR = Path(
    os.environ.get("SURFACE_TENSION_CONFIG_DIR", Path.home() / ".config" / "surface-tension")
)

# Every path is reached through a FUNCTION, never by importing the constant, so a
# test redirects the whole module by monkeypatching CONFIG_DIR alone (fc_common
# precedent; tests/conftest.py does exactly that for every test).


def config_path() -> Path:
    return CONFIG_DIR / "config.json"


def covered_path() -> Path:
    return CONFIG_DIR / "covered.json"


def dropped_log_path() -> Path:
    return CONFIG_DIR / "dropped.jsonl"


_TAG_RE = re.compile(r"<[^>]+>")

# The whole post body arrives in `summary` (recon finding 3). Cap it HERE rather
# than trusting the field to be short: an uncapped summary walks article bodies
# straight into the ranking path, which is the invariant this module exists under.
SUMMARY_MAX_CHARS = 600


def log(msg: str) -> None:
    print(f"[st_gather] {msg}", flush=True)


def die(msg: str, code: int = 1) -> NoReturn:
    log(f"error: {msg}")
    sys.exit(code)


# --------------------------------------------------------------------------
# OPML
# --------------------------------------------------------------------------


def parse_opml(path: Path) -> list[dict]:
    """Extract rss leaves from an OPML file as {feed_name, xml_url, category,
    category_inherited}.

    Uses defusedxml: OPML arrives from shared feed-reader exports, so it is
    untrusted input and stdlib xml.etree is XXE / billion-laughs vulnerable. A
    parse / IO / forbidden-entity error logs and yields [] — one bad OPML must
    not kill the run.

    The category resolution is the part that does NOT exist in
    `orchestrate.parse_opml`, which walks `root.iter("outline")` flat and reads
    `category=` off the leaf. A NESTED export stamps nothing on its leaves and
    encodes the category as the enclosing folder outline's text instead; read
    flat it yields "" for every feed, so a filter matches nothing and the run
    reports a thin week rather than a broken config. Walking the tree and
    falling back to the folder path recovers it, and `category_inherited` marks
    which feeds needed the fallback so the recovery is reported, not silent.
    """
    from xml.etree.ElementTree import ParseError

    from defusedxml.common import DefusedXmlException
    from defusedxml.ElementTree import parse as _xml_parse

    out: list[dict] = []
    try:
        root = _xml_parse(path).getroot()
    except (ParseError, DefusedXmlException, OSError) as e:
        log(f"OPML parse error {path}: {e}")
        return out

    def walk(node, folders: list[str]) -> None:
        for child in node.findall("outline"):
            if child.get("type") == "rss" and child.get("xmlUrl"):
                explicit = child.get("category") or ""
                out.append(
                    {
                        "feed_name": child.get("text") or child.get("title") or child.get("xmlUrl"),
                        "xml_url": child.get("xmlUrl"),
                        "category": explicit or "/".join(folders),
                        "category_inherited": not explicit and bool(folders),
                    }
                )
                walk(child, folders)
                continue
            label = (child.get("text") or child.get("title") or "").strip()
            walk(child, [*folders, label] if label else folders)

    body = root.find("body")
    walk(body if body is not None else root, [])
    return out


def category_values(raw: str) -> list[str]:
    """OPML 2.0 spells `category` as a COMMA-separated list of slash-delimited
    paths, so a feed can legitimately sit in several categories at once."""
    return [v.strip() for v in (raw or "").split(",") if v.strip()]


def category_matches(value: str, wanted: str) -> bool:
    """Substring-tolerant on path SEGMENTS, case-insensitive.

    Feedly writes `/user/<id>/category/Blogs`, not `Blogs` — an equality match
    against a real export finds nothing. Matching on segments accepts the bare
    name, the Feedly path, and the folder path this module synthesises for a
    nested export, without the false positives a naive `in` would admit
    ("Blogs" must not match "Bloggers").
    """
    want = (wanted or "").strip().lower()
    if not want:
        return False
    for value_path in category_values(value):
        low = value_path.lower()
        if low == want or want in [seg for seg in low.split("/") if seg]:
            return True
    return False


def select_feeds(feeds: list[dict], categories: list[str]) -> list[dict]:
    """Filter OPML feeds to `categories`. An empty result DIES.

    A category that matches zero feeds is a config error, not a thin day — and
    it is the guard that makes both category hazards loud. The message names the
    categories the file actually carries so the fix needs no second look.
    """
    if not categories:
        return feeds
    matched = [f for f in feeds if any(category_matches(f["category"], c) for c in categories)]
    if matched:
        return matched
    present = sorted({v for f in feeds for v in category_values(f["category"])})
    if not present:
        die(
            f"opml_categories {categories} matched none of the {len(feeds)} OPML feeds, and "
            "no category is recorded for ANY of them: the export carries no category= "
            'attribute on its <outline type="rss"> leaves and nests them under no named '
            "folder outline. Stamp category= on each feed, nest them under a folder, or "
            "clear opml_categories to take the whole file."
        )
    die(
        f"opml_categories {categories} matched none of the {len(feeds)} OPML feeds; "
        f"categories present in the file: {present}"
    )


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, Any] = {
    "show_name": "Surface Tension",
    "bubbles_feeds": [
        "https://bubbles.town/feed",
        # /feed/hot is ranked by DISCUSSION, not recency: in the 2026-08-25 capture
        # its oldest entry was ~16 weeks old and only 12 of 17 fell inside a 168 h
        # window. A uniform lookback would discard exactly the posts this feed
        # exists to surface, so it is explicitly unbounded. Votes carry it instead.
        {"url": "https://bubbles.town/feed/hot", "lookback_hours": None},
    ],
    # 100 entries spanning SIX HOURS. A weekly show cannot read a week of new posts
    # from one fetch, so this pool is honestly "the last few hours of unvoted posts"
    # (median 0 votes, max 3) — which is why it is gathered unranked.
    "rapid_fire_feed": "https://bubbles.town/feed/new",
    "opml_files": [],
    "opml_categories": ["Blogs"],
    "lookback_hours": 168,  # weekly
    "target_post_count": 4,
    "rapid_fire_count": 6,
    # Fan out target+BUFFER so the per-post writer subprocesses can drop a few and
    # still leave `target_post_count` survivors (orchestrate.BUFFER precedent).
    "buffer": 4,
    "per_domain_cap": 2,
    "opml_share": 0.25,
    "variety_days": 21,
}


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        die(
            f"missing {path} — see the schema in "
            "docs/superpowers/specs/2026-08-24-surface-tension-design.md section 4.1"
        )
    try:
        file_cfg = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        die(f"unreadable {path}: {e}")
    if not isinstance(file_cfg, dict):
        die(f"{path} must contain a JSON object")
    cfg = {**DEFAULT_CONFIG, **file_cfg}
    for key in ("bubbles_feeds", "opml_files", "opml_categories"):
        if not isinstance(cfg[key], list):
            die(f'{path}: "{key}" must be a list')
    if not all(isinstance(c, str) and c.strip() for c in cfg["opml_categories"]):
        die(f'{path}: "opml_categories" must be a list of non-empty strings')
    for key in ("target_post_count", "per_domain_cap", "rapid_fire_count"):
        if not isinstance(cfg[key], int) or cfg[key] <= 0:
            die(f'{path}: "{key}" must be a positive integer')
    if not isinstance(cfg["lookback_hours"], int) or cfg["lookback_hours"] <= 0:
        die(f'{path}: "lookback_hours" must be a positive integer')
    if not isinstance(cfg["opml_share"], (int, float)) or not 0 <= cfg["opml_share"] <= 1:
        die(f'{path}: "opml_share" must be between 0 and 1')
    return cfg


def load_covered() -> dict:
    """URL -> {date, mp3_url}. Written by render.py only after a successful ship.
    Malformed JSON degrades to {} rather than failing the run (render.py posture):
    a corrupt dedup log must not stop an episode, it only risks a repeat."""
    try:
        data = json.loads(covered_path().read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------
# Feed specs — one shape for both sources
# --------------------------------------------------------------------------


def _feed_label(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.netloc}{parts.path}".rstrip("/") or url


def feed_specs(config: dict) -> list[dict]:
    """Normalize `bubbles_feeds` into {url, name, source, category, lookback_hours}.

    An entry may be a plain URL string (the show-level lookback applies) or an
    object with an explicit `lookback_hours` — `null` meaning UNBOUNDED. The
    override exists because the lookback must NOT be uniform across these feeds
    (see DEFAULT_CONFIG's note on /feed/hot). Note the override governs the
    FILTER only: the recency TERM in the score is always measured against the
    show-level window, so an ancient hot post scores 0 for recency and lives or
    dies on its votes, which is the correct reading of that feed.
    """
    out: list[dict] = []
    for raw in config.get("bubbles_feeds", []):
        if isinstance(raw, str):
            raw = {"url": raw}
        if not isinstance(raw, dict) or not isinstance(raw.get("url"), str):
            die(f'each "bubbles_feeds" entry must be a URL string or an object with "url": {raw!r}')
        url = raw["url"]
        out.append(
            {
                "url": url,
                "name": raw.get("name") or _feed_label(url),
                "source": "bubbles",
                "category": "",
                "lookback_hours": raw.get("lookback_hours", config["lookback_hours"]),
            }
        )
    return out


def opml_specs(config: dict) -> list[dict]:
    """Parse every OPML file, filter to `opml_categories`, and normalize.

    Feeds are deduplicated by xmlUrl across files: the same blog listed twice is
    one fetch, not two.
    """
    paths = config.get("opml_files", [])
    if not paths:
        # A bubbles-only show is legitimate — and it is the shipped default, which
        # still carries an opml_categories value for whenever a file is added. The
        # category guard exists to catch a filter that matched nothing, not to
        # refuse a source nobody enabled.
        return []
    feeds: list[dict] = []
    for path in paths:
        feeds.extend(parse_opml(Path(path)))
    seen: set[str] = set()
    unique = []
    for f in feeds:
        if f["xml_url"] in seen:
            continue
        seen.add(f["xml_url"])
        unique.append(f)
    if not unique:
        # Configured and empty is the same silent-thin-week hazard the category
        # guard exists for, arriving one step earlier: an unreadable path, a
        # rejected entity, or an export with no rss leaves at all.
        die(f"opml_files yielded no rss feeds: {list(paths)}")
    inherited = sum(1 for f in unique if f["category_inherited"])
    if inherited:
        # Reported, not silent: a nested export carries its category on the folder
        # rather than the leaf, and the reader should know the filter is matching
        # a synthesised value.
        log(f"category inherited from an enclosing folder for {inherited}/{len(unique)} OPML feeds")
    selected = select_feeds(unique, config.get("opml_categories", []))
    return [
        {
            "url": f["xml_url"],
            "name": f["feed_name"],
            "source": "opml",
            "category": f["category"],
            "lookback_hours": config["lookback_hours"],
        }
        for f in selected
    ]


# --------------------------------------------------------------------------
# Candidates
# --------------------------------------------------------------------------

CANDIDATE_FIELDS = (
    "title",
    "url",
    "published",
    "summary",
    "votes",
    "comment_count",
    "source",
    "feed_name",
    "domain",
    "category",
)


def clean_text(s: str, limit: int = SUMMARY_MAX_CHARS) -> str:
    s = _TAG_RE.sub(" ", s or "")
    return re.sub(r"\s+", " ", s).strip()[:limit]


def _as_int(raw: Any) -> int:
    try:
        return max(0, int(str(raw).strip()))
    except (TypeError, ValueError):
        return 0


def entry_votes(entry: dict) -> int:
    """feedparser FLATTENS media:community > media:statistics, so the count lands
    on `media_statistics` and `media_community` is the empty string. An OPML blog
    feed carries neither — that is a 0, not an error."""
    stats = entry.get("media_statistics")
    return _as_int(stats.get("favorites")) if isinstance(stats, dict) else 0


def entry_comments(entry: dict) -> int:
    return _as_int(entry.get("slash_comments"))


def domain_for(url: str) -> str:
    """The blog's identity. Both the per-domain cap and the variety penalty key on
    this rather than on `feed_name`, because a bubbles candidate's feed name is
    "/feed" or "/feed/hot" — capping on that would stop nothing."""
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def entry_published(entry: dict) -> dt.datetime | None:
    p = entry.get("published_parsed")
    if not p:
        return None
    try:
        return dt.datetime(*p[:6], tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def _drop(drops: list | None, spec: dict, url: str, reason: str, detail: str, now) -> None:
    """dropped.jsonl is observability only — it never affects a pipeline decision."""
    if drops is None:
        return
    drops.append(
        {
            "timestamp": now.isoformat(),
            "run_date": now.date().isoformat(),
            "source": spec.get("source", ""),
            "feed_name": spec.get("name", ""),
            "url": url,
            "reason": reason,
            "detail": detail[:300],
        }
    )


def gather_candidates(
    specs: list[dict],
    covered: dict,
    now: dt.datetime,
    default_lookback_hours: int,
    parse: Callable | None = None,
    seen: set | None = None,
    drops: list | None = None,
) -> list[dict]:
    """Fetch each spec and yield candidates. METADATA ONLY — no article fetch, no
    LLM, and the summary is capped because on this source it IS the article.

    A feed that raises is logged, recorded as a drop, and skipped: one bad feed
    must not kill the run.
    """
    if parse is None:
        import feedparser  # lazy: keeps the unit tests import-light

        parse = feedparser.parse
    seen = seen if seen is not None else set()
    out: list[dict] = []
    for spec in specs:
        try:
            d = parse(spec["url"])
        except Exception as e:  # noqa: BLE001 - one bad feed must not kill the run
            log(f"feed error {spec['name']}: {e}")
            _drop(drops, spec, spec["url"], "feed_error", f"{type(e).__name__}: {e}", now)
            continue
        entries = d.get("entries", []) if hasattr(d, "get") else getattr(d, "entries", [])
        lookback = spec.get("lookback_hours", default_lookback_hours)
        cutoff = now - dt.timedelta(hours=lookback) if lookback else None
        for e in entries:
            url = e.get("link") or ""
            if not url.startswith(("http://", "https://")):
                # Would reach render.validate_manifest as a source_url and fail the
                # whole run; drop it here so one bad link can't poison the batch.
                _drop(drops, spec, url, "bad_link", "not an http(s) link", now)
                continue
            if url in covered or url in seen:
                continue
            published = entry_published(e)
            if cutoff is not None and published is not None and published < cutoff:
                continue
            seen.add(url)
            out.append(
                {
                    "title": clean_text(e.get("title", ""), 300),
                    "url": url,
                    "published": published,
                    "summary": clean_text(e.get("summary") or _first_content(e)),
                    "votes": entry_votes(e),
                    "comment_count": entry_comments(e),
                    "source": spec["source"],
                    "feed_name": spec["name"],
                    "domain": domain_for(url),
                    "category": spec.get("category", ""),
                }
            )
    return out


def _first_content(entry: dict) -> str:
    content = entry.get("content")
    if content and isinstance(content, list) and content[0].get("value"):
        return content[0]["value"]
    return ""


# --------------------------------------------------------------------------
# Ranking — deterministic, metadata-only
# --------------------------------------------------------------------------

WEIGHT_VOTES = 1.0
WEIGHT_RECENCY = 0.6
VARIETY_PENALTY = 0.5
# Ceiling for the vote term. The captures topped out at 39 (/feed) and 46
# (/feed/hot), so saturating here keeps one runaway post from drowning the field
# while leaving the whole realistic range discriminating.
VOTE_SATURATION = 40


def vote_score(votes: int) -> float:
    """Diminishing returns: the gap between 1 and 10 votes says more about a post
    than the gap between 30 and 40."""
    return min(1.0, math.log10(1 + max(votes, 0)) / math.log10(1 + VOTE_SATURATION))


def recency_score(published: dt.datetime | None, now: dt.datetime, lookback_hours: int) -> float:
    if published is None:
        return 0.3
    window = max(lookback_hours * 3600, 1)
    return max(0.0, min(1.0, 1.0 - (now - published).total_seconds() / window))


def recent_domains(covered: dict, now: dt.datetime, variety_days: int) -> set[str]:
    """Domains this show shipped within `variety_days`, read straight off covered.json.

    This is the variety penalty's ONLY input, and that is the whole point. #95
    found the daily show's `feed_usage.json` penalty inert since 2026-06-05
    because its only writer had stopped being the path that ships episodes. Here
    there is no second file to go stale: `render.py` rewrites covered.json after
    every successful ship, and this module already loads it for dedup — so the
    penalty cannot silently lose its data source without dedup losing it too.

    Keying on the DOMAIN rather than a feed name is #95's own observation: several
    feeds resolve to one blog, and the blog is what needs limiting. An entry with
    a missing or non-ISO date is skipped rather than guessed at (the same
    no-data-loss posture covered.json's 180-day prune takes).
    """
    out: set[str] = set()
    for url, meta in covered.items():
        raw = meta.get("date") if isinstance(meta, dict) else None
        try:
            shipped = dt.date.fromisoformat(raw)
        except (ValueError, TypeError):
            continue
        if (now.date() - shipped).days < variety_days:
            domain = domain_for(url)
            if domain:
                out.add(domain)
    return out


def score_candidate(c: dict, recent: set[str], now: dt.datetime, lookback_hours: int) -> float:
    """Votes + recency - variety. No invented quality proxy: the vote count is a
    human judgement that is already metadata, which is why this show can satisfy
    the curation invariant without a `concreteness_score`-style stand-in."""
    return (
        WEIGHT_VOTES * vote_score(c["votes"])
        + WEIGHT_RECENCY * recency_score(c["published"], now, lookback_hours)
        - (VARIETY_PENALTY if c["domain"] in recent else 0.0)
    )


def _take_next(pool: list[dict], per_domain: dict[str, int], cap: int) -> dict | None:
    """Pop the best remaining candidate whose domain is still under the cap.

    Discarding a capped candidate is safe because `per_domain` only ever grows —
    a domain at the cap can never become eligible again.
    """
    while pool:
        c = pool.pop(0)
        if per_domain.get(c["domain"], 0) < cap:
            return c
    return None


def rank_candidates(
    cands: list[dict],
    covered: dict,
    now: dt.datetime,
    *,
    lookback_hours: int = DEFAULT_CONFIG["lookback_hours"],
    target: int = DEFAULT_CONFIG["target_post_count"],
    buffer: int = DEFAULT_CONFIG["buffer"],
    per_domain_cap: int = DEFAULT_CONFIG["per_domain_cap"],
    opml_share: float = DEFAULT_CONFIG["opml_share"],
    variety_days: int = DEFAULT_CONFIG["variety_days"],
) -> list[dict]:
    """Rank each SOURCE on its own, then interleave on a fixed share.

    Why not one global sort: an OPML blog post has no vote count, so scoring it as
    zero votes puts every OPML candidate below every bubbles candidate and the
    second source — a decision locked in the design spec — becomes dead weight.
    Ranking within a pool keeps the vote signal honest exactly where it exists and
    never asks what a blog post's vote count "would have been".

    `covered` is a required argument rather than an optional variety map: the
    penalty and the dedup read the same object, so one cannot be wired up while
    the other quietly is not (#95).
    """
    recent = recent_domains(covered, now, variety_days)

    def ordered(source: str) -> list[dict]:
        pool = [c for c in cands if c["source"] == source]
        # `sorted` is stable, so equal scores keep gather order — a re-run of the
        # same inputs rebuilds the same episode.
        return sorted(
            pool, key=lambda c: score_candidate(c, recent, now, lookback_hours), reverse=True
        )

    bubbles, opml = ordered("bubbles"), ordered("opml")
    limit = target + buffer
    out: list[dict] = []
    per_domain: dict[str, int] = {}
    while len(out) < limit and (bubbles or opml):
        # Slot i belongs to the OPML pool when it crosses the next whole share —
        # for 0.25 that is every fourth slot, and the voted pool leads.
        i = len(out)
        wants_opml = int((i + 1) * opml_share) > int(i * opml_share)
        first, second = (opml, bubbles) if wants_opml else (bubbles, opml)
        chosen = _take_next(first, per_domain, per_domain_cap) or _take_next(
            second, per_domain, per_domain_cap
        )
        if chosen is None:
            break
        out.append(chosen)
        per_domain[chosen["domain"]] = per_domain.get(chosen["domain"], 0) + 1
    return out


# --------------------------------------------------------------------------
# The gather itself
# --------------------------------------------------------------------------


def json_ready(c: dict) -> dict:
    """`published` is a datetime internally (the ranking needs arithmetic) and an
    ISO string on the wire."""
    return {**c, "published": c["published"].isoformat() if c["published"] else None}


def write_dropped(records: list[dict]) -> None:
    """Append one JSON line per dropped item. Observability ONLY — nothing in a run
    reads it back, and a write failure must never change a run's outcome (the
    `write_run_log` contract). Never routed through an atomic replace: that would
    clobber the history to a single line."""
    if not records:
        return
    try:
        path = dropped_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
    except (OSError, TypeError, ValueError) as e:
        log(f"could not write {dropped_log_path()}: {e}")


def gather(config: dict, now: dt.datetime, covered: dict, parse: Callable | None = None) -> dict:
    """Both sources, both pools, one payload. No LLM and no article fetch.

    The main pool is ranked; the rapid-fire pool is not. `/feed/new` spans about
    six hours and its posts are almost all unvoted (median 0, max 3 in the
    capture), so there is nothing there for a vote ranking to sort — it is taken
    in feed order and capped. Its selection rules beyond that belong to the write
    layer.
    """
    drops: list[dict] = []
    specs = feed_specs(config) + opml_specs(config)
    seen: set[str] = set()
    cands = gather_candidates(
        specs,
        covered,
        now,
        config["lookback_hours"],
        parse=parse,
        seen=seen,
        drops=drops,
    )
    ranked = rank_candidates(
        cands,
        covered,
        now,
        lookback_hours=config["lookback_hours"],
        target=config["target_post_count"],
        buffer=config["buffer"],
        per_domain_cap=config["per_domain_cap"],
        opml_share=config["opml_share"],
        variety_days=config["variety_days"],
    )

    rapid: list[dict] = []
    if config.get("rapid_fire_feed"):
        rapid = gather_candidates(
            [
                {
                    "url": config["rapid_fire_feed"],
                    "name": _feed_label(config["rapid_fire_feed"]),
                    "source": "bubbles",
                    "category": "",
                    "lookback_hours": config["lookback_hours"],
                }
            ],
            covered,
            now,
            config["lookback_hours"],
            parse=parse,
            # Only the CHOSEN posts are excluded: a post that reached the pool and
            # lost is still fair game for the rapid-fire bit, but no post may
            # appear twice in one episode.
            seen={c["url"] for c in ranked},
            drops=drops,
        )[: config["rapid_fire_count"]]

    log(
        f"{len(ranked)} ranked from {len(cands)} candidates "
        f"({sum(1 for c in ranked if c['source'] == 'opml')} opml), "
        f"{len(rapid)} rapid-fire, {len(drops)} dropped"
    )
    return {
        "run_date": now.date().isoformat(),
        "posts": [json_ready(c) for c in ranked],
        "rapid_fire": [json_ready(c) for c in rapid],
        "dropped": drops,
    }


_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _valid_run_date(s: str) -> bool:
    """Strict YYYY-MM-DD. The regex gate matters: 3.11+ `fromisoformat` also
    accepts compact forms like 20260825, which would vary by Python version."""
    if not isinstance(s, str) or not _DATE_RE.fullmatch(s):
        return False
    try:
        dt.date.fromisoformat(s)
    except ValueError:
        return False
    return True


def main(argv: list[str] | None = None, parse: Callable | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Two-source candidate gather for Surface Tension")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ap_gather = sub.add_parser("gather", help="gather + rank candidates; prints one JSON object")
    ap_gather.add_argument("--date", help="run date YYYY-MM-DD (default: today)")
    ap_gather.add_argument("--target", type=int, help="override config target_post_count")
    args = ap.parse_args(argv)

    config = load_config()
    if args.target is not None:
        if args.target <= 0:
            die("--target must be positive")
        config = {**config, "target_post_count": args.target}

    if args.date:
        if not _valid_run_date(args.date):
            die(f"--date must be YYYY-MM-DD, got {args.date!r}")
        # End of the named day, not its midnight: a date-only run must not score a
        # post published that morning as arriving from the future.
        now = dt.datetime.combine(
            dt.date.fromisoformat(args.date), dt.time(23, 59, 59), tzinfo=dt.timezone.utc
        )
    else:
        now = dt.datetime.now(dt.timezone.utc)

    out = gather(config, now, load_covered(), parse=parse)
    write_dropped(out["dropped"])
    print(json.dumps(out))  # the FINAL stdout line — the caller parses it
    return 0


if __name__ == "__main__":
    sys.exit(main())
