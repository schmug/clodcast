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

import datetime as dt
import gzip
import html
import os
import re
import sys
import urllib.request
from collections.abc import Iterator
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
