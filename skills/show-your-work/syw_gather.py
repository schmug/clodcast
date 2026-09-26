"""Show Your Work's gather layer: frontier-lab alignment sources -> one item schema.

Pure metadata, no LLM. This is the gather half of the repo's one-body-per-request
invariant: feed content is read here only to find which lab posts a check source
LINKS to (`mentions`), then discarded; a summary is capped at SUMMARY_MAX_CHARS
so a feed that inlines whole bodies cannot walk one into ranking.

Two recon findings (spec §2, 2026-09-26) shape every adapter:

  1. The two core sources have NO feed: alignment.anthropic.com 404s every feed
     path and the OpenAI misalignment casebook is static HTML. They are scraped,
     so an adapter that parses ZERO items is a FAILURE (the markup changed), never
     a quiet week. Zero NEW items is fine.
  2. OpenAI's alignment rss.xml is incomplete (it omits cross-posts and reports),
     so that adapter unions the RSS with the index page by normalized URL.

Stdlib html.parser only: CI installs feedparser/defusedxml/boto3/Pillow/mutagen
and nothing else, so bs4 is not available.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import html
import json
import os
import re
import sys
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

# Its OWN config dir (frontier-commits / surface-tension precedent). Every path is
# reached through a function so tests redirect the module by patching CONFIG_DIR.
CONFIG_DIR = Path(
    os.environ.get("SHOW_YOUR_WORK_CONFIG_DIR", Path.home() / ".config" / "show-your-work")
)


def config_path() -> Path:
    return CONFIG_DIR / "config.json"


def seen_path() -> Path:
    return CONFIG_DIR / "seen.json"


def observed_path() -> Path:
    return CONFIG_DIR / "observed.json"


def features_path() -> Path:
    return CONFIG_DIR / "features.jsonl"


def dropped_log_path() -> Path:
    return CONFIG_DIR / "dropped.jsonl"


DEFAULT_CONFIG: dict[str, Any] = {
    "max_age_days": 21,
    "check_lookback_days": 60,
    "max_briefs": 4,
    "casebook_max": 3,
    "lab_penalty_weeks": 2,
    "max_checks": 3,
    # None = every adapter in ADAPTERS; a list enables only the named ones.
    "adapters": None,
}

SUMMARY_MAX_CHARS = 600
FETCH_TIMEOUT_S = 30
# An honest UA. Verified 2026-09-26: every source answers it with 200 (Goodfire's
# .ai host 301s to .com, which is why CHECK_FEEDS uses the .com URL).
USER_AGENT = "clodcast-show-your-work/1.0 (+https://cortech.online)"

LEAD = "lead"
CHECK = "check"


class AdapterFailed(RuntimeError):
    """One source could not be fetched or parsed. Isolated: the others continue."""


class ConfigError(RuntimeError):
    """config.json is missing or invalid — reported on the GATHER line, never as a
    bare traceback (the fc_snapshot lesson: a die() before the try printed no
    scheduler line at all)."""


class CommitRefused(RuntimeError):
    """The render did not ship, so nothing may be marked covered."""


def log(msg: str) -> None:
    print(f"[syw_gather] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# Items
# --------------------------------------------------------------------------


@dataclass
class Item:
    url: str
    source: str
    lab: str
    kind: str  # "research" | "incident" | "notice"
    title: str
    summary: str
    date: str  # YYYY-MM-DD, or "" when the source gives none
    date_precision: str  # "day" | "month"
    role: str  # LEAD | CHECK
    mentions: list[str] = field(default_factory=list)  # check items: lead URLs linked

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_url(url: str) -> str:
    """Identity form of a URL: lowercase scheme and host, no query, no fragment, no
    trailing slash. Medium appends `?source=rss-…`; OpenAI's index links
    `/metagaming/` where METR links `/metagaming` — both must collapse.

    Fragments are dropped on purpose. The one place a fragment IS identity
    (misalignment notices, which all share one openai.com page) builds its URL
    without calling this — see parse_openai_misalignment."""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(s or "")).strip()


def cap_summary(s: str) -> str:
    s = clean_text(re.sub(r"<[^>]+>", " ", s or ""))
    return s if len(s) <= SUMMARY_MAX_CHARS else s[: SUMMARY_MAX_CHARS - 1].rstrip() + "…"


def parse_human_date(s: str) -> str:
    """'Sep 9, 2026' / 'September 11, 2026' -> '2026-09-09'. '' when unparsable."""
    s = clean_text(s)
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def parse_month(s: str) -> str:
    """'August 2026' -> '2026-08-01'. '' when unparsable."""
    try:
        return dt.datetime.strptime(clean_text(s), "%B %Y").date().isoformat()
    except ValueError:
        return ""


def struct_date(entry: Any) -> str:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    return dt.date(*t[:3]).isoformat() if t else ""


# --------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------


def fetch_text(url: str) -> str:
    """GET a page as text. The single network seam: tests monkeypatch it."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
        return decode_body(resp.read())


def decode_body(body: bytes) -> str:
    """Gunzip a gzip body even though we never ask for one: deepmind.google's
    blog feed sends `Content-Encoding: gzip` unconditionally (verified 2026-09-26)
    and urllib does not decode it."""
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    return body.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# A tiny DOM over html.parser
# --------------------------------------------------------------------------

_VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}  # fmt: skip
_SKIP_TEXT = {"script", "style", "noscript", "svg", "template"}


@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    children: list  # Node | str

    def classes(self) -> list[str]:
        return self.attrs.get("class", "").split()


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, [])
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, [])
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, {k: (v or "") for k, v in attrs}, []))

    def handle_endtag(self, tag):
        # Pop to the nearest matching open tag; a stray end tag is ignored.
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_html(text: str) -> Node:
    builder = _TreeBuilder()
    builder.feed(text)
    builder.close()
    return builder.root


def iter_nodes(node: Node) -> Iterator[Node]:
    yield node
    for child in node.children:
        if isinstance(child, Node):
            yield from iter_nodes(child)


def find_all(node: Node, tag: str | None = None, cls: str | None = None) -> list[Node]:
    return [
        n
        for n in iter_nodes(node)
        if (tag is None or n.tag == tag) and (cls is None or cls in n.classes())
    ]


def first(node: Node | None, tag: str | None = None, cls: str | None = None) -> Node | None:
    if node is None:
        return None
    hits = find_all(node, tag, cls)
    return hits[0] if hits else None


def text_of(node: Node | None) -> str:
    if node is None:
        return ""
    out: list[str] = []

    def walk(n: Node) -> None:
        if n.tag in _SKIP_TEXT:
            return
        for c in n.children:
            if isinstance(c, str):
                out.append(c)
            else:
                walk(c)

    walk(node)
    return clean_text(" ".join(out))


def page_text(raw_html: str) -> str:
    """The visible text of a whole page, for the write layer's verbatim guards."""
    return text_of(parse_html(raw_html))


def hrefs(raw_html: str, base: str) -> set[str]:
    """Every link target in an HTML fragment, absolutized and normalized."""
    root = parse_html(raw_html)
    return {
        normalize_url(urljoin(base, a.attrs["href"]))
        for a in find_all(root, "a")
        if a.attrs.get("href")
    }


# --------------------------------------------------------------------------
# Lead parsers — pure: {url: page text} -> [Item]
# --------------------------------------------------------------------------

ANTHROPIC_ALIGNMENT_URL = "https://alignment.anthropic.com/"
ANTHROPIC_RESEARCH_URLS = (
    "https://www.anthropic.com/research/team/alignment",
    "https://www.anthropic.com/research/team/interpretability",
)
TRANSFORMER_CIRCUITS_URL = "https://transformer-circuits.pub/feed.xml"
OPENAI_ALIGNMENT_RSS = "https://alignment.openai.com/rss.xml"
OPENAI_ALIGNMENT_INDEX = "https://alignment.openai.com/"
OPENAI_MISALIGNMENT_URL = "https://alignment.openai.com/misalignment-reports/"
GDM_MEDIUM_URL = "https://deepmindsafetyresearch.medium.com/feed"
GDM_BLOG_URL = "https://deepmind.google/blog/rss.xml"


def parse_anthropic_alignment(pages: dict[str, str]) -> list[Item]:
    """alignment.anthropic.com: `div.toc` holds `div.date` month headers, each
    followed by `a.paper` / `a.note` cards (h3 title, div.description). Dates are
    MONTH-level only, which is why the plan ages these items from first
    observation rather than from the date (spec §4.2)."""
    root = parse_html(pages[ANTHROPIC_ALIGNMENT_URL])
    toc = first(root, "div", "toc")
    items: list[Item] = []
    month = ""
    for child in toc.children if toc else []:
        if not isinstance(child, Node):
            continue
        if child.tag == "div" and "date" in child.classes():
            month = parse_month(text_of(child))
        elif child.tag == "a" and child.attrs.get("href"):
            title = text_of(first(child, "h3"))
            if not title:
                continue
            items.append(
                Item(
                    url=normalize_url(urljoin(ANTHROPIC_ALIGNMENT_URL, child.attrs["href"])),
                    source="anthropic-alignment",
                    lab="anthropic",
                    kind="research",
                    title=title,
                    summary=cap_summary(text_of(first(child, "div", "description"))),
                    date=month,
                    date_precision="month",
                    role=LEAD,
                )
            )
    return items


def parse_anthropic_research(pages: dict[str, str]) -> list[Item]:
    """anthropic.com/research/team/*: Next.js cards. CSS-module class names are
    hashed, so match on STRUCTURE — an <a href="/research/<slug>"> holding a
    heading, a <time> and a <p> — never on a class. The image link repeats each
    href without a heading; those are skipped, and hrefs dedupe across pages."""
    items: dict[str, Item] = {}
    for page_url in ANTHROPIC_RESEARCH_URLS:
        root = parse_html(pages[page_url])
        for a in find_all(root, "a"):
            href = a.attrs.get("href", "")
            if not href.startswith("/research/") or href.startswith("/research/team/"):
                continue
            heading = next((n for n in iter_nodes(a) if n.tag in ("h2", "h3", "h4")), None)
            title = text_of(heading)
            if not title:
                continue
            url = normalize_url(urljoin(page_url, href))
            items.setdefault(
                url,
                Item(
                    url=url,
                    source="anthropic-research",
                    lab="anthropic",
                    kind="research",
                    title=title,
                    summary=cap_summary(text_of(first(a, "p"))),
                    date=parse_human_date(text_of(first(a, "time"))),
                    date_precision="day",
                    role=LEAD,
                ),
            )
    return list(items.values())


def feed_items(text: str, source: str, lab: str, kind: str, role: str) -> list[Item]:
    import feedparser  # function-local, the render.py posture

    out = []
    for e in feedparser.parse(text).entries:
        if not e.get("link") or not e.get("title"):
            continue
        out.append(
            Item(
                url=normalize_url(e.link),
                source=source,
                lab=lab,
                kind=kind,
                title=clean_text(e.title),
                summary=cap_summary(e.get("summary", "")),
                date=struct_date(e),
                date_precision="day",
                role=role,
            )
        )
    return out


def parse_transformer_circuits(pages: dict[str, str]) -> list[Item]:
    return feed_items(
        pages[TRANSFORMER_CIRCUITS_URL], "transformer-circuits", "anthropic", "research", LEAD
    )


def parse_openai_alignment(pages: dict[str, str]) -> list[Item]:
    """rss.xml UNION the index page. The RSS omits openai.com cross-posts (the ↗
    rows) and the Metagaming post, so the index is authoritative for coverage; the
    RSS still contributes anything the index has scrolled past. Index rows win."""
    root = parse_html(pages[OPENAI_ALIGNMENT_INDEX])
    merged: dict[str, Item] = {}
    for art in find_all(root, "article", "ap-post"):
        a = first(first(art, "h2"), "a")
        if a is None or not a.attrs.get("href"):
            continue
        url = normalize_url(urljoin(OPENAI_ALIGNMENT_INDEX, a.attrs["href"]))
        time = first(art, "time")
        merged[url] = Item(
            url=url,
            source="openai-alignment",
            lab="openai",
            kind="research",
            title=text_of(a).rstrip("↗").strip(),
            summary=cap_summary(text_of(first(art, "p"))),
            date=(time.attrs.get("datetime", "") if time else "")[:10],
            date_precision="day",
            role=LEAD,
        )
    rss = feed_items(pages[OPENAI_ALIGNMENT_RSS], "openai-alignment", "openai", "research", LEAD)
    for it in rss:
        merged.setdefault(it.url, it)
    return list(merged.values())


def parse_openai_misalignment(pages: dict[str, str]) -> list[Item]:
    """The casebook: `details.cb-entry` rows. A REPORT carries data-date /
    data-title and an `a.cb-link` to its own page. A NOTICE (`cb-notice`) has no
    page of its own — all three link anchors on ONE openai.com update page — so its
    identity is the casebook URL plus the entry's id. Normalizing a notice's link
    would collapse every notice into one URL and dedupe all but the first away."""
    root = parse_html(pages[OPENAI_MISALIGNMENT_URL])
    items: list[Item] = []
    for d in find_all(root, "details", "cb-entry"):
        copy = cap_summary(text_of(first(d, "p", "cb-copy")))
        if "cb-notice" in d.classes():
            ident = d.attrs.get("id", "")
            if not ident:
                continue
            time = first(first(d, "p", "cb-meta"), "time")
            items.append(
                Item(
                    url=normalize_url(OPENAI_MISALIGNMENT_URL) + "#" + ident,
                    source="openai-misalignment",
                    lab="openai",
                    kind="notice",
                    title=text_of(first(d, "h3")),
                    summary=copy,
                    date=(time.attrs.get("datetime", "") if time else "")[:10],
                    date_precision="day",
                    role=LEAD,
                )
            )
            continue
        link = first(d, "a", "cb-link")
        if link is None or not link.attrs.get("href"):
            continue
        items.append(
            Item(
                url=normalize_url(urljoin(OPENAI_MISALIGNMENT_URL, link.attrs["href"])),
                source="openai-misalignment",
                lab="openai",
                kind="incident",
                title=d.attrs.get("data-title") or text_of(first(d, "h3")),
                summary=copy,
                date=d.attrs.get("data-date", "")[:10],
                date_precision="day",
                role=LEAD,
            )
        )
    return items


# The GDM blog feed has no categories and no summaries (recon §2.1), so safety
# posts are picked by a closed keyword list matched as whole words on the title
# and summary. The Medium safety blog is taken whole.
GDM_SAFETY_KEYWORDS = (
    "safety",
    "alignment",
    "aligned",
    "interpretability",
    "misuse",
    "scheming",
    "deceptive",
    "oversight",
    "responsibility",
)
_GDM_KW_RE = re.compile(r"\b(" + "|".join(map(re.escape, GDM_SAFETY_KEYWORDS)) + r")\b", re.I)


def parse_gdm(pages: dict[str, str]) -> list[Item]:
    items = feed_items(pages[GDM_MEDIUM_URL], "gdm-safety", "gdm", "research", LEAD)
    blog = feed_items(pages[GDM_BLOG_URL], "gdm-safety", "gdm", "research", LEAD)
    return items + [it for it in blog if _GDM_KW_RE.search(f"{it.title} {it.summary}")]


# --------------------------------------------------------------------------
# Check parsers — [(Item, content_html)]; the content is dropped in attach_mentions
# --------------------------------------------------------------------------

CHECK_FEEDS = {
    "metr": "https://metr.org/feed.xml",
    "redwood": "https://blog.redwoodresearch.org/feed",
    "goodfire": "https://www.goodfire.com/research/rss.xml",
    "alignment-forum": "https://www.alignmentforum.org/feed.xml?view=curated-rss",
}

# METR republishes posts in translation under /zh-Hans/ and /es/ with the same
# links, so a translation would attach as a second and third "independent" check.
_TRANSLATION_PATH_RE = re.compile(r"^/[a-z]{2}(?:-[A-Za-z]+)?/")


def parse_check_feed(text: str, source: str) -> list[tuple[Item, str]]:
    import feedparser

    out = []
    for e in feedparser.parse(text).entries:
        if not e.get("link") or not e.get("title"):
            continue
        if _TRANSLATION_PATH_RE.match(urlsplit(e.link).path):
            continue
        content = " ".join(c.get("value", "") for c in e.get("content", [])) or e.get("summary", "")
        item = Item(
            url=normalize_url(e.link),
            source=source,
            lab=source,
            kind="research",
            title=clean_text(e.title),
            summary=cap_summary(e.get("summary", "")),
            date=struct_date(e),
            date_precision="day",
            role=CHECK,
        )
        out.append((item, content))
    return out


def normalize_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", clean_text(s).casefold()).strip()


MIN_TITLE_MATCH_WORDS = 4


def attach_mentions(pairs: list[tuple[Item, str]], lead: list[Item]) -> list[Item]:
    """A check item MENTIONS a lead item when its content links the lead URL or
    contains the lead title (normalized, at least MIN_TITLE_MATCH_WORDS words, so a
    generic three-word title never matches by accident). The content is read here
    and nowhere else; the returned items do not carry it."""
    by_url = {it.url for it in lead}
    titled = [
        (t, it.url)
        for it in lead
        if len((t := normalize_title(it.title)).split()) >= MIN_TITLE_MATCH_WORDS
    ]
    out = []
    for item, content in pairs:
        links = hrefs(content, item.url) if content else set()
        text = f" {normalize_title(page_text(content))} " if content else ""
        hits = {u for u in links if u in by_url}
        hits |= {u for t, u in titled if f" {t} " in text}
        item.mentions = sorted(hits)
        out.append(item)
    return out


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Adapter:
    name: str
    role: str
    urls: tuple[str, ...]
    parse: Callable[[dict[str, str]], list[Item]] | None  # lead adapters
    check_source: str | None = None  # check adapters


ADAPTERS: tuple[Adapter, ...] = (
    Adapter("anthropic-alignment", LEAD, (ANTHROPIC_ALIGNMENT_URL,), parse_anthropic_alignment),
    Adapter("anthropic-research", LEAD, ANTHROPIC_RESEARCH_URLS, parse_anthropic_research),
    Adapter("transformer-circuits", LEAD, (TRANSFORMER_CIRCUITS_URL,), parse_transformer_circuits),
    Adapter(
        "openai-alignment",
        LEAD,
        (OPENAI_ALIGNMENT_RSS, OPENAI_ALIGNMENT_INDEX),
        parse_openai_alignment,
    ),
    Adapter("openai-misalignment", LEAD, (OPENAI_MISALIGNMENT_URL,), parse_openai_misalignment),
    Adapter("gdm-safety", LEAD, (GDM_MEDIUM_URL, GDM_BLOG_URL), parse_gdm),
    *(Adapter(name, CHECK, (url,), None, check_source=name) for name, url in CHECK_FEEDS.items()),
)


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

_DATE_ONLY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def load_config() -> dict:
    p = config_path()
    if not p.is_file():
        raise ConfigError(f"missing {p}; create it ({{}} is valid) — see SKILL.md Setup")
    try:
        user = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise ConfigError(f"{p} is not valid JSON: {e}") from e
    if not isinstance(user, dict):
        raise ConfigError(f"{p} must hold a JSON object")
    unknown = set(user) - set(DEFAULT_CONFIG)
    if unknown:
        raise ConfigError(f"{p} has unknown key(s) {sorted(unknown)}")
    return {**DEFAULT_CONFIG, **user}


def _atomic_write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False))
    os.replace(tmp, path)


def load_seen() -> dict:
    """The coverage ledger. A corrupt file REFUSES rather than reading as empty:
    empty would re-feature every story the show has ever covered."""
    p = seen_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise ConfigError(f"{p} (seen.json) is corrupt: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{p} (seen.json) must hold a JSON object")
    return data


def load_observed() -> dict:
    """First-observation dates. Corrupt reads as empty: the worst case is that a
    month-precision item ages from today, which withholds nothing."""
    try:
        data = json.loads(observed_path().read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_features() -> list[dict]:
    try:
        lines = features_path().read_text().splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def append_dropped(record: dict) -> None:
    """Observability only; never fails the caller."""
    try:
        dropped_log_path().parent.mkdir(parents=True, exist_ok=True)
        stamped = {"timestamp": dt.datetime.now(dt.timezone.utc).isoformat(), **record}
        with dropped_log_path().open("a") as f:
            f.write(json.dumps(stamped, ensure_ascii=False) + "\n")
    except OSError as e:
        log(f"warn: could not write dropped.jsonl: {e}")


# --------------------------------------------------------------------------
# Gather / seed / commit
# --------------------------------------------------------------------------


def run_adapter(adapter: Adapter) -> list:
    try:
        # fetch_text is looked up at call time, so a test's monkeypatch applies.
        pages = {u: fetch_text(u) for u in adapter.urls}
    except Exception as e:  # noqa: BLE001 - any fetch failure isolates this adapter
        raise AdapterFailed(f"{adapter.name}: fetch failed: {e}") from e
    try:
        if adapter.parse is not None:
            out = adapter.parse(pages)
        else:
            out = parse_check_feed(pages[adapter.urls[0]], adapter.check_source or adapter.name)
    except Exception as e:  # noqa: BLE001 - a parser crash is this adapter's failure
        raise AdapterFailed(f"{adapter.name}: parse failed: {e}") from e
    if not out:
        raise AdapterFailed(f"{adapter.name}: parsed zero items — the markup or feed changed")
    return out


def gather(config: dict, date_iso: str) -> dict:
    wanted = config.get("adapters")
    adapters = [a for a in ADAPTERS if wanted is None or a.name in wanted]
    lead: list[Item] = []
    pairs: list[tuple[Item, str]] = []
    errors: list[dict] = []
    for a in adapters:
        try:
            out = run_adapter(a)
        except AdapterFailed as e:
            errors.append({"adapter": a.name, "role": a.role, "error": str(e)})
            append_dropped({"stage": "gather", "adapter": a.name, "reason": str(e)})
            log(f"warn: {e}")
            continue
        (lead if a.role == LEAD else pairs).extend(out)
    lead_names = {a.name for a in adapters if a.role == LEAD}
    failed = {e["adapter"] for e in errors}
    if lead_names and lead_names <= failed:
        raise AdapterFailed("every lead adapter failed: " + "; ".join(e["error"] for e in errors))
    uniq: dict[str, Item] = {}
    for it in lead:
        uniq.setdefault(it.url, it)
    checks = attach_mentions(pairs, list(uniq.values()))
    seen = load_seen()
    observed = load_observed()
    for url in uniq:
        observed.setdefault(url, date_iso)
    _atomic_write(observed_path(), observed)
    return {
        "date": date_iso,
        "lead": [
            {**it.to_dict(), "seen": url in seen, "first_observed": observed[url]}
            for url, it in uniq.items()
        ],
        "check": [c.to_dict() for c in checks],
        "errors": errors,
    }


def seed(config: dict, date_iso: str) -> int:
    """Mark everything currently on every index as seen, so episode one is not a
    two-year back catalogue. Ships nothing."""
    out = gather(config, date_iso)
    seen = load_seen()
    for it in out["lead"]:
        seen.setdefault(it["url"], {"date": date_iso, "role": "seeded"})
    _atomic_write(seen_path(), seen)
    return len(out["lead"])


def commit(plan: dict, render_output: str) -> int:
    """Mark the plan's feature and briefed items seen and record the feature — ONLY
    when render.py's final JSON says the episode shipped. render.py prints that JSON
    pretty-printed after its log lines, and prints one on a failed publish too
    (r2_status "failed"), so the check is on the values, never on exit code alone."""
    _dp = Path(__file__).resolve().parent.parent / "daily-podcast"
    if str(_dp) not in sys.path:
        sys.path.insert(0, str(_dp))
    from orchestrate import extract_last_json

    result = extract_last_json(render_output)
    if not isinstance(result, dict):
        raise CommitRefused("no JSON result in the render output")
    if result.get("status") != "web-ready":
        raise CommitRefused(f"render status {result.get('status')!r} is not 'web-ready'")
    if result.get("r2_status") != "published":
        raise CommitRefused(f"r2_status {result.get('r2_status')!r} is not 'published'")
    feature = plan.get("feature")
    if not feature:
        raise CommitRefused("the plan has no feature")
    seen = load_seen()
    seen[feature["url"]] = {"date": plan["date"], "role": "feature"}
    urls = [feature["url"]]
    for brief in plan.get("briefs", []):
        for it in brief["items"]:
            seen.setdefault(it["url"], {"date": plan["date"], "role": "brief"})
            urls.append(it["url"])
    _atomic_write(seen_path(), seen)
    history = load_features()
    row = {
        "date": plan["date"],
        "feature_url": feature["url"],
        "lab": feature["lab"],
        "kind": feature["kind"],
        "mp3_url": result.get("mp3_url"),
    }
    last = history[-1] if history else {}
    if (last.get("date"), last.get("feature_url")) != (row["date"], row["feature_url"]):
        features_path().parent.mkdir(parents=True, exist_ok=True)
        with features_path().open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(urls)


def _date(s: str) -> str:
    if not _DATE_ONLY_RE.fullmatch(s):
        raise ConfigError(f"--date must be YYYY-MM-DD (got {s!r})")
    dt.date.fromisoformat(s)
    return s


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="syw_gather.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g_ = sub.add_parser("gather")
    g_.add_argument("--date", required=True)
    g_.add_argument("--out", required=True)
    s_ = sub.add_parser("seed")
    s_.add_argument("--date", required=True)
    c_ = sub.add_parser("commit")
    c_.add_argument("--plan", required=True)
    c_.add_argument("--render-output", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "gather":
            out = gather(load_config(), _date(a.date))
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(json.dumps(out, indent=2, ensure_ascii=False))
            new = sum(1 for it in out["lead"] if not it["seen"])
            print(
                f"GATHER ok lead={len(out['lead'])} new={new} "
                f"check={len(out['check'])} errors={len(out['errors'])}"
            )
        elif a.cmd == "seed":
            print(f"SEED ok marked={seed(load_config(), _date(a.date))}")
        else:
            plan = json.loads(Path(a.plan).read_text())
            n = commit(plan, Path(a.render_output).read_text())
            print(f"COMMIT ok urls={n}")
    except (AdapterFailed, ConfigError) as e:
        print(f"{'GATHER' if a.cmd != 'commit' else 'COMMIT'} FAILED {e}")
        return 1
    except CommitRefused as e:
        print(f"COMMIT refused {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
