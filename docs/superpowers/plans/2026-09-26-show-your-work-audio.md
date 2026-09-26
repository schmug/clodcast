# Show Your Work (audio show) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Show Your Work, a weekly Explainer/Skeptic podcast about frontier-lab alignment research. It is gathered, planned, written, validated and published through the existing `render.py` web-only path. **This plan covers audio only**; the video stage is a second plan (see Scope).

**Architecture:** A fourth flat skill directory, `skills/show-your-work/`, holds three modules, following Surface Tension's split:
- `syw_gather.py`: sources → items, the seen/observed ledgers, and the ship-gated `commit`
- `syw_script_plan.py`: deterministic feature/brief/casebook plan and week-seeded rotations
- `syw_write.py`: prompt filling, output classification, scene and beat validation, manifest + beats-sidecar assembly

`render.py` is **not modified**. The model writes only inside isolated contexts driven by `prompts/*.md`, and every rule that can be checked is checked in Python.

**Tech Stack:**
- Python 3.10+
- stdlib `html.parser` (bs4 is **not** available in CI)
- `feedparser` (already a runtime and CI dependency)
- pytest, ruff 0.14.10
- the existing `render.py` / `orchestrate.py` modules, imported by path

**Spec:** `docs/superpowers/specs/2026-09-26-show-your-work-design.md`, including its 2026-09-26 *Amended* markers. Read it before starting any task.

**Scope:**
- This plan implements spec §4.1–§4.6, §4.8 and §5–§9 phases 1, 2, 3 and 5.
- **Spec §4.7 (video) and phase 4 are a separate plan.** Its first step is sending Cory static mock frames and waiting for approval, so it can't be planned honestly before that.
- The cortech.online page and feed are schmug/cortech.online#256, also out of scope here.

## Global Constraints

- **`render.py` is not modified.** Every change to rendering behavior is out of scope.
- **Namespace keys:**
  - `ship_mode: "web"`
  - `show_name: "Show Your Work"`
  - `r2_manifest_name: "manifest-show-your-work.json"`
  - `r2_key_prefix: "show-your-work/"`
  - `slug_prefix: "syw-week-of"`
- State root is `~/.config/show-your-work/`, overridable with `SHOW_YOUR_WORK_CONFIG_DIR`. Every path is reached through a function so `tests/conftest.py` can redirect it.
- **One-body invariant:** no model context holds more than one article body. Checks and casebook incidents are digested one body per context first.
- **Stdlib HTML parsing only.** CI installs exactly `ruff==0.14.10 pytest>=8.0 boto3 defusedxml Pillow feedparser mutagen`.
- **Sibling modules import, never copy:**
  - `week_index` from `fc_script_plan`
  - `AUTH_RE`, `POLICY_RE`, `MIN_SEGMENT_CHARS`, `extract_last_json`, `FALLBACK_BUTTONS` and `FALLBACK_FOURTH_WALLS` from `orchestrate`
- **`seen.json` is written only by `seed` or by a `commit` that verified `status == "web-ready"` and `r2_status == "published"`** in render.py's final JSON.
- **Tests never touch real user state.** Register the new config dir in `tests/conftest.py` (Task 1).
- The unattended procedure has exactly one home, SKILL.md's *Unattended weekly run*. `prompts/weekly.md` is a stub.
- **Commits:** conventional prefixes (`feat:`, `test:`, `docs:`, `chore:`), ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never add a `Signed-off-by` for Cory.
- **Every task's gate:**

  ```bash
  python3 -m pytest -q
  ruff check .
  ruff format --check .
  ```

  The baseline before Task 1 is **1558 passed, 0 failed**. Report counts.

## Review Focus

1. **The three OpenAI notices all link anchors on one openai.com page.** If a notice's identity is its normalized link, all three collapse to one URL and two silently vanish. Identity is the casebook URL + `#<entry id>`. Pinned in Task 2 (`test_notices_keep_distinct_fragment_identities`).
2. **`deepmind.google/blog/rss.xml` is gzip-encoded even when gzip is not requested, and urllib doesn't decode it.** Without the check, the GDM adapter would parse zero items and fail every week. Pinned in Task 1 (`test_decode_body_gunzips_an_unrequested_gzip_body`).
3. **The Anthropic index gives month-only dates.** Aged from the 1st of the month, a post published on Aug 28 and first seen Sep 2 would already be 32 days old and drop out of a 21-day pool. Month items age from `first_observed`. Pinned in Task 5 (`test_month_precision_items_age_from_first_observation`).
4. **Typography differs between a writer's copy and the page** (curly quotes, NBSP, en dashes, `&amp;`). Without normalizing both sides, every quote and transcript beat would be dropped as "not verbatim". Pinned in Task 7 (`test_verbatim_guard_is_typography_blind`).
5. **render.py prints its final result as pretty-printed multi-line JSON, after log lines.** A failed publish still prints JSON, with `r2_status: "failed"`. `commit` must parse the last object and refuse anything but `web-ready` + `published`, or a failed ship marks its stories covered forever. Pinned in Task 4 (`test_commit_refuses_a_failed_publish`, `test_commit_parses_pretty_printed_render_output`).

---

## File Structure

| Path | Responsibility |
| --- | --- |
| `skills/show-your-work/syw_gather.py` | config + state paths, fetch + gzip decode, a tiny DOM over `html.parser`, 6 lead parsers, 4 check parsers, mention matching, adapter registry, `gather` / `seed` / `commit` CLI |
| `skills/show-your-work/syw_script_plan.py` | arc + rotation banks, scoring, `build_plan`, `committed_urls`, `plan` CLI with resume |
| `skills/show-your-work/syw_write.py` | show identity constants, typography-blind normalization, `classify_output`, digest/scene/beat/frame validators, prompt fillers, `assemble_manifest`, `fill` / `accept` / `assemble` CLI |
| `skills/show-your-work/prompts/digest.md` | one-body fact-sheet writer |
| `skills/show-your-work/prompts/write_feature.md` | the five-scene feature writer (one body + check digests) |
| `skills/show-your-work/prompts/write_brief.md` | single-post brief writer (one body) |
| `skills/show-your-work/prompts/write_casebook.md` | casebook brief writer (**no body**, digests only) |
| `skills/show-your-work/prompts/weekly.md` | stub pointing at SKILL.md |
| `skills/show-your-work/SKILL.md` | the skill: layout, arc, banks, beats, manifest, editorial rules, *Unattended weekly run*, setup |
| `skills/show-your-work/refs/cover.jpg` | show art (Task 11) |
| `tests/data/syw/*` | **already committed** fixtures captured 2026-09-26 (see the Task 2 table) |
| `tests/test_syw_gather.py`, `tests/test_syw_script_plan.py`, `tests/test_syw_write.py`, `tests/test_syw_skill_md.py` | tests |
| `tests/conftest.py` | register the new skill dir and config root |
| `pyproject.toml` | add the `syw_*` modules to ruff's `known-first-party` |
| `CLAUDE.md` | one paragraph describing the fourth skill |

---

### Task 1: Gather foundation: paths, fetch, DOM helpers, test isolation

**Files:**
- Create: `skills/show-your-work/syw_gather.py`
- Modify: `tests/conftest.py` (sys.path insert, import, redirect, assert)
- Modify: `pyproject.toml` (`[tool.ruff.lint.isort] known-first-party`)
- Test: `tests/test_syw_gather.py`

**Interfaces:**
- Produces:
  - `CONFIG_DIR: Path`, plus `config_path()`, `seen_path()`, `observed_path()`, `features_path()`, `dropped_log_path()`, all returning `Path`
  - `DEFAULT_CONFIG: dict`, `SUMMARY_MAX_CHARS = 600`, `LEAD = "lead"`, `CHECK = "check"`
  - `class AdapterFailed(RuntimeError)`, `class ConfigError(RuntimeError)`, `class CommitRefused(RuntimeError)`
  - `@dataclass Item(url, source, lab, kind, title, summary, date, date_precision, role, mentions=[])` with `.to_dict()`
  - `normalize_url(url) -> str`, `clean_text(s) -> str`, `cap_summary(s) -> str`, `parse_human_date(s) -> str`, `parse_month(s) -> str`, `struct_date(entry) -> str`
  - `fetch_text(url) -> str`, `decode_body(bytes) -> str`
  - `Node`, `parse_html(text) -> Node`, `iter_nodes(node)`, `find_all(node, tag=None, cls=None) -> list[Node]`, `first(node, tag=None, cls=None) -> Node | None`, `text_of(node) -> str`, `page_text(raw_html) -> str`, `hrefs(raw_html, base) -> set[str]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_syw_gather.py`:

```python
"""Show Your Work gather layer (spec §4.2). Fixtures in tests/data/syw were
captured 2026-09-26 from the live sources; assertions name what was captured."""

from __future__ import annotations

from pathlib import Path

import syw_gather as g

DATA = Path(__file__).resolve().parent / "data" / "syw"


def test_normalize_url_collapses_query_fragment_case_and_trailing_slash():
    assert (
        g.normalize_url("HTTPS://Alignment.OpenAI.com/metagaming/?x=1#top")
        == "https://alignment.openai.com/metagaming"
    )
    # Medium appends a tracking query to every feed link.
    assert (
        g.normalize_url("https://deepmindsafetyresearch.medium.com/p-3368?source=rss-55e0------2")
        == "https://deepmindsafetyresearch.medium.com/p-3368"
    )
    assert g.normalize_url("https://metr.org/") == "https://metr.org/"


def test_decode_body_gunzips_an_unrequested_gzip_body():
    raw = (DATA / "gdm-blog.xml.gz").read_bytes()
    assert raw[:2] == b"\x1f\x8b"
    assert g.decode_body(raw).startswith("<?xml")
    assert g.decode_body(b"plain") == "plain"


def test_date_parsers():
    assert g.parse_human_date("Sep 9, 2026") == "2026-09-09"
    assert g.parse_human_date("September 11, 2026") == "2026-09-11"
    assert g.parse_human_date("soon") == ""
    assert g.parse_month("August 2026") == "2026-08-01"
    assert g.parse_month("Augst 2026") == ""


def test_cap_summary_strips_tags_and_caps():
    s = g.cap_summary("<p>" + "word " * 400 + "</p>")
    assert "<" not in s
    assert len(s) <= g.SUMMARY_MAX_CHARS


def test_page_text_skips_script_style_and_decodes_entities():
    raw = (
        "<html><script>var x=1</script><style>p{}</style>"
        "<p>Hello&nbsp;<b>world</b> &amp; co</p></html>"
    )
    assert g.page_text(raw) == "Hello world & co"


def test_hrefs_absolutizes_and_normalizes():
    got = g.hrefs('<a href="/x/">a</a><a href="https://y.org/z?q=1#f">b</a><a>c</a>', "https://base.org/p/")
    assert got == {"https://base.org/x", "https://y.org/z"}


def test_the_config_dir_is_redirected_away_from_real_state():
    real = Path.home() / ".config" / "show-your-work"
    assert g.CONFIG_DIR != real and real not in g.CONFIG_DIR.parents
    assert g.seen_path().parent == g.CONFIG_DIR
```

- [ ] **Step 2: Run the tests and confirm they fail for the right reason**

Run: `python3 -m pytest tests/test_syw_gather.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'syw_gather'`. That is right: neither the module nor the conftest path insert exists yet.

- [ ] **Step 3: Register the skill dir and config root in `tests/conftest.py`**

After the `import st_gather` block (the one following `ST_SKILL_DIR`), insert:

```python
# Show Your Work is a fifth flat skill directory. Its modules import each other and
# fc_script_plan / orchestrate by path, exactly as Surface Tension's do.
SYW_SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "show-your-work"
sys.path.insert(0, str(SYW_SKILL_DIR))

import syw_gather  # noqa: E402  (must follow the sys.path insert above)
```

In `_isolate_user_state`, directly after the `st_sandbox` lines, add:

```python
    # Show Your Work owns ~/.config/show-your-work (seen/observed ledgers, feature
    # history); a test that ran gather or commit unredirected would mark real
    # stories covered.
    syw_sandbox = tmp_path_factory.mktemp("show-your-work-state")
    monkeypatch.setattr(syw_gather, "CONFIG_DIR", syw_sandbox)
```

In `pytest_configure`, add:

```python
    config._syw_real_state_dir = Path.home() / ".config" / "show-your-work"
```

At the end of `_assert_no_real_state_writes`, add:

```python
    syw_value = Path(syw_gather.CONFIG_DIR)
    real_syw = request.config._syw_real_state_dir
    assert real_syw not in syw_value.parents and syw_value != real_syw, (
        f"test left syw_gather.CONFIG_DIR pointing at the real state dir ({syw_value})"
    )
```

In `pyproject.toml`, append `"syw_gather", "syw_script_plan", "syw_write",` to `known-first-party`.

- [ ] **Step 4: Write the module foundation**

Create `skills/show-your-work/syw_gather.py`:

```python
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
import json
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_syw_gather.py -q`
Expected: `7 passed`.

`test_page_text_skips_script_style_and_decodes_entities` relies on `\s` matching NBSP (U+00A0) in Python's `re`. If it fails on the NBSP, the fix is in `clean_text`, not the test.

- [ ] **Step 6: Run the full gate**

```bash
ruff format skills/show-your-work tests/test_syw_gather.py tests/conftest.py
python3 -m pytest -q
ruff check .
ruff format --check .
```

Expected: `1565 passed` (1558 + 7), and ruff clean.

- [ ] **Step 7: Commit**

```bash
git add skills/show-your-work/syw_gather.py tests/test_syw_gather.py tests/conftest.py pyproject.toml
git commit -m "feat(syw): gather foundation — paths, gzip-safe fetch, stdlib DOM

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Lead parsers (the six sources that can be featured)

**Files:**
- Modify: `skills/show-your-work/syw_gather.py` (append)
- Test: `tests/test_syw_gather.py` (append)
- Uses the fixtures already committed in `tests/data/syw/`:

| Fixture | Captured from |
| --- | --- |
| `anthropic-alignment.html` | `https://alignment.anthropic.com/` |
| `anthropic-research-alignment.html` | `https://www.anthropic.com/research/team/alignment` |
| `anthropic-research-interpretability.html` | `https://www.anthropic.com/research/team/interpretability` |
| `transformer-circuits.xml` | `https://transformer-circuits.pub/feed.xml` (first 12 entries) |
| `openai-alignment.xml` | `https://alignment.openai.com/rss.xml` (whole) |
| `openai-alignment.html` | `https://alignment.openai.com/` |
| `openai-misalignment.html` | `https://alignment.openai.com/misalignment-reports/` |
| `gdm-medium.xml` | `https://deepmindsafetyresearch.medium.com/feed` (first 4) |
| `gdm-blog.xml` / `.gz` | `https://deepmind.google/blog/rss.xml` (first 25, decoded / re-gzipped) |

Script, style and svg blocks were stripped from the HTML fixtures, which leaves the parsed structure unchanged.

**Interfaces:**
- Consumes: everything from Task 1.
- Produces:
  - URL constants: `ANTHROPIC_ALIGNMENT_URL`, `ANTHROPIC_RESEARCH_URLS`, `TRANSFORMER_CIRCUITS_URL`, `OPENAI_ALIGNMENT_RSS`, `OPENAI_ALIGNMENT_INDEX`, `OPENAI_MISALIGNMENT_URL`, `GDM_MEDIUM_URL`, `GDM_BLOG_URL`
  - parsers, each `(pages: dict[str, str]) -> list[Item]`: `parse_anthropic_alignment`, `parse_anthropic_research`, `parse_transformer_circuits`, `parse_openai_alignment`, `parse_openai_misalignment`, `parse_gdm`
  - `feed_items(text, source, lab, kind, role) -> list[Item]`, `GDM_SAFETY_KEYWORDS`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_syw_gather.py`:

```python
def _pages(*pairs: tuple[str, str]) -> dict[str, str]:
    return {url: (DATA / name).read_text() for url, name in pairs}


def test_anthropic_alignment_index_reads_month_groups():
    items = g.parse_anthropic_alignment(
        _pages((g.ANTHROPIC_ALIGNMENT_URL, "anthropic-alignment.html"))
    )
    assert len(items) == 84
    top = items[0]
    assert top.url == "https://alignment.anthropic.com/2026/reward-seeker"
    assert top.title == "Training a Misaligned Reward Seeker"
    assert (top.date, top.date_precision, top.kind, top.lab, top.role) == (
        "2026-08-01",
        "month",
        "research",
        "anthropic",
        "lead",
    )
    assert all(len(i.summary) <= g.SUMMARY_MAX_CHARS for i in items)


def test_anthropic_research_matches_structure_not_hashed_classes():
    items = g.parse_anthropic_research(
        _pages(
            (g.ANTHROPIC_RESEARCH_URLS[0], "anthropic-research-alignment.html"),
            (g.ANTHROPIC_RESEARCH_URLS[1], "anthropic-research-interpretability.html"),
        )
    )
    # Each page lists 5 cards; each card's href also appears on an image link with
    # no heading, which must not produce a second, untitled item.
    assert len(items) == 10 == len({i.url for i in items})
    assert (items[0].url, items[0].date, items[0].date_precision) == (
        "https://www.anthropic.com/research/alignment-assessment-cybersecurity-incidents",
        "2026-09-09",
        "day",
    )
    assert all(i.title for i in items)


def test_transformer_circuits_feed():
    items = g.parse_transformer_circuits(
        _pages((g.TRANSFORMER_CIRCUITS_URL, "transformer-circuits.xml"))
    )
    assert len(items) == 12
    assert items[0].url == (
        "https://transformer-circuits.pub/2026/interference_effectiveness_helpfulness/index.html"
    )
    assert items[0].date == "2026-08-21" and items[0].lab == "anthropic"


def test_openai_alignment_unions_rss_with_the_index():
    pages = _pages(
        (g.OPENAI_ALIGNMENT_RSS, "openai-alignment.xml"),
        (g.OPENAI_ALIGNMENT_INDEX, "openai-alignment.html"),
    )
    urls = {i.url for i in g.parse_openai_alignment(pages)}
    assert len(urls) == 27
    # openai.com cross-posts are on the index only (the RSS omits them) …
    assert "https://openai.com/index/an-alien-mind" in urls
    # … and so is the on-site Metagaming post (recon §2.1).
    assert "https://alignment.openai.com/metagaming" in urls


def test_openai_alignment_keeps_an_rss_only_post():
    pages = _pages(
        (g.OPENAI_ALIGNMENT_RSS, "openai-alignment.xml"),
        (g.OPENAI_ALIGNMENT_INDEX, "openai-alignment.html"),
    )
    extra = (
        "<item><title>RSS only</title><link>https://alignment.openai.com/rss-only/</link>"
        "<pubDate>Mon, 21 Sep 2026 08:00:00 -0700</pubDate></item>"
    )
    # Insert before </channel>: the captured feed has a literal "<item>" inside an XML
    # comment near the top, so splicing at the first "<item>" would land in the comment.
    pages[g.OPENAI_ALIGNMENT_RSS] = pages[g.OPENAI_ALIGNMENT_RSS].replace("</channel>", extra + "</channel>", 1)
    urls = {i.url for i in g.parse_openai_alignment(pages)}
    assert "https://alignment.openai.com/rss-only" in urls and len(urls) == 28


def test_openai_misalignment_reports_and_notices():
    items = g.parse_openai_misalignment(
        _pages((g.OPENAI_MISALIGNMENT_URL, "openai-misalignment.html"))
    )
    kinds = [i.kind for i in items]
    assert kinds.count("incident") == 9 and kinds.count("notice") == 3
    top = items[0]
    assert top.url == (
        "https://alignment.openai.com/misalignment-reports/self-replicating-prompt-injections-exist"
    )
    assert (top.date, top.title) == ("2026-09-25", "Self-replicating prompt injections exist")


def test_notices_keep_distinct_fragment_identities():
    items = g.parse_openai_misalignment(
        _pages((g.OPENAI_MISALIGNMENT_URL, "openai-misalignment.html"))
    )
    notices = {i.url: i for i in items if i.kind == "notice"}
    assert set(notices) == {
        "https://alignment.openai.com/misalignment-reports#notice-rubygems",
        "https://alignment.openai.com/misalignment-reports#notice-dsewiki",
        "https://alignment.openai.com/misalignment-reports#notice-hugging-face",
    }
    assert notices["https://alignment.openai.com/misalignment-reports#notice-rubygems"].date == (
        "2026-09-11"
    )


def test_gdm_keeps_medium_and_keyword_matched_blog_posts_only():
    pages = _pages((g.GDM_MEDIUM_URL, "gdm-medium.xml"), (g.GDM_BLOG_URL, "gdm-blog.xml"))
    items = g.parse_gdm(pages)
    # The captured 25 blog posts are all product/science posts; none is a safety post.
    assert len(items) == 4
    assert all(i.url.startswith("https://deepmindsafetyresearch.medium.com/") for i in items)
    assert all("?" not in i.url for i in items)
    feed = (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>b</title>'
        "<item><title>Strengthening our Frontier Safety Framework</title>"
        "<link>https://deepmind.google/blog/fsf/</link></item>"
        "<item><title>Gemini makes better pancakes</title>"
        "<link>https://deepmind.google/blog/pancakes/</link></item>"
        "</channel></rss>"
    )
    pages[g.GDM_BLOG_URL] = feed
    urls = {i.url for i in g.parse_gdm(pages)}
    assert "https://deepmind.google/blog/fsf" in urls
    assert "https://deepmind.google/blog/pancakes" not in urls
```

- [ ] **Step 2: Run the tests and confirm they fail for the right reason**

Run: `python3 -m pytest tests/test_syw_gather.py -q`
Expected: 8 new failures, each `AttributeError: module 'syw_gather' has no attribute 'parse_…'` (or `…_URL`).

- [ ] **Step 3: Implement the lead parsers**

Append to `skills/show-your-work/syw_gather.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_syw_gather.py -q`
Expected: `15 passed`.

- [ ] **Step 5: Run the full gate**

```bash
ruff format skills/show-your-work tests/test_syw_gather.py
python3 -m pytest -q
ruff check .
ruff format --check .
```

Expected: `1573 passed`, and ruff clean.

- [ ] **Step 6: Commit**

```bash
git add skills/show-your-work/syw_gather.py tests/test_syw_gather.py
git commit -m "feat(syw): lead-source parsers for Anthropic, OpenAI and GDM

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Check parsers, mention matching, adapter registry

**Files:**
- Modify: `skills/show-your-work/syw_gather.py` (append)
- Test: `tests/test_syw_gather.py` (append)
- Fixtures, already committed:
  - `metr.xml` (first 6 entries, including the zh-Hans and es translations)
  - `redwood.xml` (first 2)
  - `goodfire.xml` (first 8)
  - `alignment-forum.xml` (first 4, `?view=curated-rss`)

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces:
  - `CHECK_FEEDS: dict[str, str]`
  - `parse_check_feed(text, source) -> list[tuple[Item, str]]`
  - `normalize_title(s) -> str`
  - `attach_mentions(pairs, lead) -> list[Item]`
  - `@dataclass(frozen=True) Adapter(name, role, urls, parse, check_source=None)`
  - `ADAPTERS: tuple[Adapter, ...]`: 6 lead + 4 check

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_syw_gather.py`:

```python
def _lead_items() -> list:
    pages = _pages(
        (g.ANTHROPIC_ALIGNMENT_URL, "anthropic-alignment.html"),
        (g.ANTHROPIC_RESEARCH_URLS[0], "anthropic-research-alignment.html"),
        (g.ANTHROPIC_RESEARCH_URLS[1], "anthropic-research-interpretability.html"),
        (g.TRANSFORMER_CIRCUITS_URL, "transformer-circuits.xml"),
        (g.OPENAI_ALIGNMENT_RSS, "openai-alignment.xml"),
        (g.OPENAI_ALIGNMENT_INDEX, "openai-alignment.html"),
    )
    return (
        g.parse_anthropic_alignment(pages)
        + g.parse_anthropic_research(pages)
        + g.parse_transformer_circuits(pages)
        + g.parse_openai_alignment(pages)
    )


def test_metr_translations_are_not_separate_checks():
    pairs = g.parse_check_feed((DATA / "metr.xml").read_text(), "metr")
    urls = [item.url for item, _ in pairs]
    assert len(urls) == 4
    assert not any("/zh-Hans/" in u or "/es/" in u for u in urls)
    assert all(item.role == "check" and item.lab == "metr" for item, _ in pairs)


def test_mentions_come_from_links_in_the_check_content():
    pairs = g.parse_check_feed((DATA / "redwood.xml").read_text(), "redwood")
    pairs += g.parse_check_feed((DATA / "alignment-forum.xml").read_text(), "alignment-forum")
    checks = {c.title[:30]: c for c in g.attach_mentions(pairs, _lead_items())}
    redwood = next(c for t, c in checks.items() if t.startswith("Latent reasoning"))
    assert (
        "https://www.anthropic.com/research/alignment-assessment-cybersecurity-incidents"
        in redwood.mentions
    )
    af = next(c for t, c in checks.items() if t.startswith("Four LLM loss"))
    assert af.mentions == ["https://alignment.anthropic.com/2026/psm"]


def test_mentions_match_a_verbatim_title_without_a_link():
    lead = [
        g.Item("https://lab.test/p", "s", "anthropic", "research", "Training a Misaligned Reward Seeker", "", "", "day", "lead"),
        g.Item("https://lab.test/q", "s", "anthropic", "research", "Teaching Claude Why", "", "", "day", "lead"),
    ]
    check = g.Item("https://metr.org/x", "metr", "metr", "research", "t", "", "2026-09-20", "day", "check")
    content = (
        "<p>Anthropic's <em>Training a misaligned reward-seeker</em> post "
        "and teaching Claude why.</p>"
    )
    [out] = g.attach_mentions([(check, content)], lead)
    # 5 words: matched despite case and punctuation. 3 words: too short to trust.
    assert out.mentions == ["https://lab.test/p"]


def test_check_items_do_not_carry_their_content():
    pairs = g.parse_check_feed((DATA / "redwood.xml").read_text(), "redwood")
    [first_check, *_] = g.attach_mentions(pairs, [])
    assert set(first_check.to_dict()) == {
        "url", "source", "lab", "kind", "title", "summary",
        "date", "date_precision", "role", "mentions",
    }  # fmt: skip
    assert len(first_check.summary) <= g.SUMMARY_MAX_CHARS


def test_registry_has_six_lead_and_four_check_adapters():
    roles = [a.role for a in g.ADAPTERS]
    assert roles.count("lead") == 6 and roles.count("check") == 4
    assert len({a.name for a in g.ADAPTERS}) == 10
    assert all(a.parse is not None for a in g.ADAPTERS if a.role == "lead")
    assert all(a.check_source for a in g.ADAPTERS if a.role == "check")
```

- [ ] **Step 2: Run the tests and confirm they fail for the right reason**

Run: `python3 -m pytest tests/test_syw_gather.py -q`
Expected: 5 new failures with `AttributeError` for `parse_check_feed`, `attach_mentions` or `ADAPTERS`.

- [ ] **Step 3: Implement**

Append to `skills/show-your-work/syw_gather.py`, and add `from collections.abc import Callable` to the imports:

```python
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
        content = " ".join(c.get("value", "") for c in e.get("content", [])) or e.get(
            "summary", ""
        )
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
    Adapter(
        "transformer-circuits", LEAD, (TRANSFORMER_CIRCUITS_URL,), parse_transformer_circuits
    ),
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_syw_gather.py -q`
Expected: `20 passed`.

- [ ] **Step 5: Run the full gate**

```bash
ruff format skills/show-your-work tests/test_syw_gather.py
python3 -m pytest -q
ruff check .
ruff format --check .
```

Expected: `1578 passed`, and ruff clean.

- [ ] **Step 6: Commit**

```bash
git add skills/show-your-work/syw_gather.py tests/test_syw_gather.py
git commit -m "feat(syw): check-source parsers and link/title mention matching

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Gather CLI: gather, seed, and the ship-gated commit

**Files:**
- Modify: `skills/show-your-work/syw_gather.py` (append)
- Test: `tests/test_syw_gather.py` (append)

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces:
  - `load_config() -> dict` (raises `ConfigError`)
  - `load_seen() -> dict` (raises `ConfigError` on corrupt), `load_observed() -> dict`, `load_features() -> list[dict]`
  - `append_dropped(record: dict) -> None` (best-effort)
  - `run_adapter(adapter) -> list` (raises `AdapterFailed`)
  - `gather(config, date_iso) -> dict`: `{"date", "lead": [item dict + "seen": bool + "first_observed": str], "check": [item dict], "errors": [...]}`
  - `seed(config, date_iso) -> int`
  - `commit(plan: dict, render_output: str) -> int` (raises `CommitRefused`)
  - `main(argv) -> int`. Its final stdout line is one of:
    - `GATHER ok lead=<n> new=<n> check=<n> errors=<n>`
    - `GATHER FAILED <reason>`
    - `SEED ok marked=<n>`
    - `COMMIT ok urls=<n>`
    - `COMMIT refused <reason>`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_syw_gather.py`:

```python
import json  # noqa: E402

import pytest  # noqa: E402

FIXTURE_FOR_URL = {
    g.ANTHROPIC_ALIGNMENT_URL: "anthropic-alignment.html",
    g.ANTHROPIC_RESEARCH_URLS[0]: "anthropic-research-alignment.html",
    g.ANTHROPIC_RESEARCH_URLS[1]: "anthropic-research-interpretability.html",
    g.TRANSFORMER_CIRCUITS_URL: "transformer-circuits.xml",
    g.OPENAI_ALIGNMENT_RSS: "openai-alignment.xml",
    g.OPENAI_ALIGNMENT_INDEX: "openai-alignment.html",
    g.OPENAI_MISALIGNMENT_URL: "openai-misalignment.html",
    g.GDM_MEDIUM_URL: "gdm-medium.xml",
    g.GDM_BLOG_URL: "gdm-blog.xml",
    g.CHECK_FEEDS["metr"]: "metr.xml",
    g.CHECK_FEEDS["redwood"]: "redwood.xml",
    g.CHECK_FEEDS["goodfire"]: "goodfire.xml",
    g.CHECK_FEEDS["alignment-forum"]: "alignment-forum.xml",
}


@pytest.fixture
def offline(monkeypatch):
    """Serve every source from its fixture; `broken` names URLs that raise."""
    broken: set[str] = set()

    def fake_fetch(url: str) -> str:
        if url in broken:
            raise OSError(f"boom {url}")
        return (DATA / FIXTURE_FOR_URL[url]).read_text()

    monkeypatch.setattr(g, "fetch_text", fake_fetch)
    g.config_path().parent.mkdir(parents=True, exist_ok=True)
    g.config_path().write_text("{}")
    return broken


RENDER_OK = """[render] publishing…
{
  "status": "web-ready",
  "mp3_url": "https://clodcast.cortech.online/show-your-work/syw-week-of-september-27-2026.mp3",
  "loudnorm": {"input_i": -24.1},
  "r2_status": "published",
  "resumed": false
}
"""
PLAN = {
    "date": "2026-09-27",
    "feature": {"url": "https://a.test/f", "lab": "openai", "kind": "incident"},
    "briefs": [
        {"kind": "single", "items": [{"url": "https://a.test/b"}]},
        {"kind": "casebook", "items": [{"url": "https://a.test/c1"}, {"url": "https://a.test/c2"}]},
    ],
    "leftover": ["https://a.test/left"],
}


def test_load_config_refuses_missing_and_unknown_keys():
    with pytest.raises(g.ConfigError, match="missing"):
        g.load_config()
    g.config_path().parent.mkdir(parents=True, exist_ok=True)
    g.config_path().write_text('{"max_brief": 3}')
    with pytest.raises(g.ConfigError, match="unknown"):
        g.load_config()
    g.config_path().write_text('{"max_briefs": 3}')
    assert g.load_config()["max_briefs"] == 3


def test_gather_marks_new_items_and_records_first_observation(offline):
    out = g.gather(g.load_config(), "2026-09-26")
    assert out["errors"] == []
    # 149 parsed; alignment.anthropic.com/2025/activation-oracles is listed by both
    # anthropic-alignment and transformer-circuits, and gather dedupes by URL.
    assert len(out["lead"]) == 148
    assert not any(it["seen"] for it in out["lead"])
    assert {it["first_observed"] for it in out["lead"]} == {"2026-09-26"}
    # A later gather never moves a first observation forward.
    out2 = g.gather(g.load_config(), "2026-10-03")
    assert {it["first_observed"] for it in out2["lead"]} == {"2026-09-26"}
    assert any(c["mentions"] for c in out["check"])


def test_a_failing_adapter_is_isolated_and_logged(offline):
    offline.add(g.OPENAI_MISALIGNMENT_URL)
    out = g.gather(g.load_config(), "2026-09-26")
    assert [e["adapter"] for e in out["errors"]] == ["openai-misalignment"]
    assert not any(it["source"] == "openai-misalignment" for it in out["lead"])
    rows = [json.loads(ln) for ln in g.dropped_log_path().read_text().splitlines()]
    assert rows[-1]["adapter"] == "openai-misalignment"


def test_an_adapter_that_parses_zero_items_has_failed(monkeypatch, offline):
    real = g.fetch_text

    def changed_markup(url):
        return "<html><body><p>redesigned</p></body></html>" if url == g.ANTHROPIC_ALIGNMENT_URL else real(url)

    monkeypatch.setattr(g, "fetch_text", changed_markup)
    out = g.gather(g.load_config(), "2026-09-26")
    [err] = out["errors"]
    assert err["adapter"] == "anthropic-alignment" and "zero items" in err["error"]


def test_gather_fails_when_every_lead_adapter_fails(offline, capsys, tmp_path):
    offline.update(u for u in FIXTURE_FOR_URL if u not in g.CHECK_FEEDS.values())
    rc = g.main(["gather", "--date", "2026-09-26", "--out", str(tmp_path / "c.json")])
    assert rc == 1
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("GATHER FAILED every lead")


def test_gather_cli_reports_a_missing_config_on_its_line(capsys, tmp_path):
    rc = g.main(["gather", "--date", "2026-09-26", "--out", str(tmp_path / "c.json")])
    assert rc == 1
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("GATHER FAILED")


def test_seed_marks_everything_seen(offline, capsys):
    assert g.main(["seed", "--date", "2026-09-26"]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "SEED ok marked=148"
    out = g.gather(g.load_config(), "2026-09-26")
    assert all(it["seen"] for it in out["lead"])


def test_commit_parses_pretty_printed_render_output():
    assert g.commit(PLAN, RENDER_OK) == 4
    seen = json.loads(g.seen_path().read_text())
    assert set(seen) == {"https://a.test/f", "https://a.test/b", "https://a.test/c1", "https://a.test/c2"}
    assert seen["https://a.test/f"] == {"date": "2026-09-27", "role": "feature"}
    [row] = g.load_features()
    assert (row["feature_url"], row["lab"], row["kind"]) == ("https://a.test/f", "openai", "incident")


def test_commit_is_idempotent():
    g.commit(PLAN, RENDER_OK)
    g.commit(PLAN, RENDER_OK)
    assert len(g.load_features()) == 1


@pytest.mark.parametrize(
    "output, why",
    [
        (RENDER_OK.replace('"published"', '"failed"'), "r2_status"),
        (RENDER_OK.replace('"web-ready"', '"dry-run"'), "status"),
        ("[render] error: TTS died\n", "no JSON"),
    ],
)
def test_commit_refuses_a_failed_publish(output, why):
    with pytest.raises(g.CommitRefused, match=why):
        g.commit(PLAN, output)
    assert not g.seen_path().exists()
    assert not g.features_path().exists()


def test_a_corrupt_seen_ledger_refuses_rather_than_resetting(offline):
    g.seen_path().write_text("{not json")
    with pytest.raises(g.ConfigError, match="seen.json"):
        g.load_seen()
```

- [ ] **Step 2: Run the tests and confirm they fail for the right reason**

Run: `python3 -m pytest tests/test_syw_gather.py -q`
Expected: the 13 new tests fail with `AttributeError` (`load_config`, `gather`, `main`, `commit`, …). The 20 earlier tests still pass.

- [ ] **Step 3: Implement**

Append to `skills/show-your-work/syw_gather.py`, and add `import argparse` to the imports:

```python
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
        raise AdapterFailed(
            "every lead adapter failed: " + "; ".join(e["error"] for e in errors)
        )
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
```

The seed count is 148: 84 + 10 + 12 + 27 + 12 + 4 = 149 parsed, minus one URL (`https://alignment.anthropic.com/2025/activation-oracles`) that two adapters both list. If `test_seed_marks_everything_seen` reports a different number, the fixture changed: recount from the parser tests and fix the expectation, not the parsers.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_syw_gather.py -q`
Expected: `33 passed` (20 + 13; the parametrized refusal test counts as 3).

- [ ] **Step 5: Run the full gate**

```bash
ruff format skills/show-your-work tests/test_syw_gather.py
python3 -m pytest -q
ruff check .
ruff format --check .
```

Expected: `1591 passed`, and ruff clean.

- [ ] **Step 6: Commit**

```bash
git add skills/show-your-work/syw_gather.py tests/test_syw_gather.py
git commit -m "feat(syw): gather/seed CLI and a commit gated on a published ship

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The plan: feature pick, briefs, casebook, checks, rotations

**Files:**
- Create: `skills/show-your-work/syw_script_plan.py`
- Test: `tests/test_syw_script_plan.py`

**Interfaces:**
- Consumes:
  - `fc_script_plan.week_index(date_iso) -> int`
  - `syw_gather.load_config()` and `syw_gather.load_features()` (CLI only)
  - candidates JSON from Task 4
- Produces:
  - `ARC = ("hook","method","finding","pushback","stakes")`, `ARC_JOBS: dict[str, str]`, `SLOT_TITLES: dict[str, str | None]`, `SPEAKERS = ("explainer","skeptic")`
  - banks: `INTRO_MODES` (3), `FOURTH_WALL_ANGLES` (4), `OPENING_MOVES` (5), `FIRST_SPEAKERS` (2), `SIGNOFF_BUTTONS` (7), all `dict[str, str]` except `FIRST_SPEAKERS: tuple`
  - `ROTATION_BANKS: dict[str, Sequence]`, `EXEMPT_PAIR = ("fourth_wall", "first_speaker")`
  - `rotation(date_iso) -> dict` with keys `intro_mode`, `fourth_wall`, `opening_move`, `first_speaker`, `button`
  - `effective_date(item) -> str`, `age_days(item, today) -> int`
  - `build_plan(candidates, history, config, date_iso, feature_override=None, exclude=()) -> dict`
  - `committed_urls(plan) -> list[str]`
  - `main(argv) -> int`, which prints one of:
    - `PLAN ok feature=<url> briefs=<n> checks=<n>`
    - `PLAN reused feature=<url> …`
    - `PLAN skip <reason>`
- The plan dict's keys: `date`, `week`, `feature` (lead item dict + `override`), `excluded`, `briefs` (a list of `{"kind": "single"|"casebook", "items": [...]}`), `checks`, `rotation`, `leftover`. When nothing is eligible, it is `{"date","week","feature": None, "skip": "<reason>"}` instead.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_syw_script_plan.py`:

```python
"""Show Your Work plan layer (spec §4.3)."""

from __future__ import annotations

import datetime as dt
import itertools
import json

import pytest

import syw_gather
import syw_script_plan as sp

CONFIG = dict(syw_gather.DEFAULT_CONFIG)
TODAY = "2026-09-27"


def item(url, lab="openai", kind="research", date="2026-09-20", **kw):
    return {
        "url": url, "source": "s", "lab": lab, "kind": kind, "title": url.rsplit("/", 1)[-1],
        "summary": "", "date": date, "date_precision": kw.pop("precision", "day"),
        "role": "lead", "mentions": [], "seen": kw.pop("seen", False),
        "first_observed": kw.pop("first_observed", date), **kw,
    }  # fmt: skip


def plan(lead, check=(), history=(), **cfg):
    return sp.build_plan(
        {"lead": list(lead), "check": list(check)}, list(history), {**CONFIG, **cfg}, TODAY
    )


def test_rotations_do_not_lock():
    """Every pair of banks except the exempt one covers its full product cycle over
    consecutive weeks — no rotation is a function of another (the daily show's
    fourth-wall/intro-mode lesson)."""
    start = dt.date(2026, 9, 28)
    for a, b in itertools.combinations(sp.ROTATION_BANKS, 2):
        if {a, b} == set(sp.EXEMPT_PAIR):
            continue
        n = len(sp.ROTATION_BANKS[a]) * len(sp.ROTATION_BANKS[b])
        pairs = {
            (r[a], r[b])
            for r in (sp.rotation((start + dt.timedelta(weeks=i)).isoformat()) for i in range(n))
        }
        assert len(pairs) == n, f"{a} x {b} locks: {len(pairs)} of {n} combinations"


def test_rotation_is_a_pure_function_of_the_date():
    assert sp.rotation("2026-09-27") == sp.rotation("2026-09-27")
    assert set(sp.rotation("2026-09-27")) == set(sp.ROTATION_BANKS)


def test_month_precision_items_age_from_first_observation():
    late_august = item(
        "https://a.test/aug", lab="anthropic", date="2026-08-01", precision="month",
        first_observed="2026-09-10",
    )  # fmt: skip
    # Aged from its month it would be 57 days old on 2026-09-27; from first sight, 17.
    assert sp.age_days(late_august, dt.date(2026, 9, 27)) == 17
    p = plan([late_august], max_age_days=21)
    assert p["feature"]["url"] == "https://a.test/aug"


def test_seen_and_stale_items_are_not_in_the_pool():
    p = plan([item("https://a.test/seen", seen=True), item("https://a.test/old", date="2026-08-01")])
    assert p["feature"] is None and p["skip"] == "no new lab items"


def test_newest_wins_without_penalties():
    p = plan([item("https://a.test/old", date="2026-09-15"), item("https://a.test/new", date="2026-09-25")])
    assert p["feature"]["url"] == "https://a.test/new"


def test_lab_penalty_hands_the_slot_to_another_lab():
    history = [{"date": "2026-09-20", "feature_url": "x", "lab": "anthropic", "kind": "research"}]
    p = plan(
        [
            item("https://a.test/anthropic", lab="anthropic", date="2026-09-26"),
            item("https://a.test/openai", lab="openai", date="2026-09-10"),
        ],
        history=history,
    )
    assert p["feature"]["url"] == "https://a.test/openai"


def test_kind_penalty_breaks_a_near_tie():
    history = [{"date": "2026-09-20", "feature_url": "x", "lab": "gdm", "kind": "incident"}]
    p = plan(
        [
            item("https://a.test/inc", kind="incident", date="2026-09-26"),
            item("https://a.test/res", kind="research", date="2026-09-20"),
        ],
        history=history,
        lab_penalty_weeks=0,
    )
    assert p["feature"]["url"] == "https://a.test/res"


def test_notices_are_never_features():
    p = plan([item("https://a.test/n", kind="notice", date="2026-09-26")])
    assert p["feature"] is None


def test_casebook_folds_incidents_before_notices_and_takes_one_slot():
    lead = [item("https://a.test/feature", kind="research", date="2026-09-26")]
    lead += [item(f"https://a.test/inc{i}", kind="incident", date="2026-09-2" + str(i)) for i in range(4)]
    lead += [item("https://a.test/notice", kind="notice", date="2026-09-25")]
    lead += [item(f"https://a.test/res{i}", kind="research", date="2026-09-1" + str(i)) for i in range(5)]
    p = plan(lead, lab_penalty_weeks=0)
    kinds = [b["kind"] for b in p["briefs"]]
    assert kinds == ["single", "single", "single", "casebook"]
    casebook = p["briefs"][-1]["items"]
    assert len(casebook) == 3 and all(it["kind"] == "incident" for it in casebook)
    assert "https://a.test/notice" in p["leftover"]


def test_checks_attach_by_mention_within_the_lookback_and_cap():
    feature = item("https://a.test/f", date="2026-09-26")
    checks = [
        {**item(f"https://metr.test/{i}", date=f"2026-09-{10 + i}"), "role": "check", "mentions": ["https://a.test/f"]}
        for i in range(5)
    ]
    checks.append({**item("https://metr.test/old", date="2026-06-01"), "role": "check", "mentions": ["https://a.test/f"]})
    checks.append({**item("https://metr.test/other", date="2026-09-20"), "role": "check", "mentions": []})
    p = plan([feature], check=checks)
    assert [c["url"] for c in p["checks"]] == [
        "https://metr.test/4", "https://metr.test/3", "https://metr.test/2",
    ]  # fmt: skip


def test_feature_override_and_exclude():
    lead = [item("https://a.test/new", date="2026-09-26"), item("https://a.test/old", date="2026-09-20")]
    over = sp.build_plan({"lead": lead, "check": []}, [], CONFIG, TODAY, feature_override="https://a.test/old")
    assert over["feature"]["url"] == "https://a.test/old" and over["feature"]["override"] is True
    ex = sp.build_plan({"lead": lead, "check": []}, [], CONFIG, TODAY, exclude=("https://a.test/new",))
    assert ex["feature"]["url"] == "https://a.test/old" and ex["excluded"] == ["https://a.test/new"]
    with pytest.raises(ValueError):
        sp.build_plan({"lead": lead, "check": []}, [], CONFIG, TODAY, feature_override="https://nope")


def test_committed_urls_exclude_leftovers():
    lead = [item(f"https://a.test/{i}", date=f"2026-09-2{i}") for i in range(7)]
    p = plan(lead)
    assert len(sp.committed_urls(p)) == 5
    assert set(sp.committed_urls(p)).isdisjoint(p["leftover"])
    assert sp.committed_urls({"feature": None}) == []


def test_cli_reuses_an_existing_plan(tmp_path, capsys):
    syw_gather.config_path().parent.mkdir(parents=True, exist_ok=True)
    syw_gather.config_path().write_text("{}")
    cands = tmp_path / "candidates.json"
    cands.write_text(json.dumps({"lead": [item("https://a.test/a", date="2026-09-26")], "check": []}))
    out = tmp_path / "plan.json"
    args = ["plan", "--date", TODAY, "--candidates", str(cands), "--out", str(out)]
    assert sp.main(args) == 0
    assert capsys.readouterr().out.strip() == "PLAN ok feature=https://a.test/a briefs=0 checks=0"
    cands.write_text(json.dumps({"lead": [item("https://a.test/b", date="2026-09-26")], "check": []}))
    assert sp.main(args) == 0
    assert capsys.readouterr().out.strip().startswith("PLAN reused feature=https://a.test/a")
    # --exclude re-plans and accumulates.
    assert sp.main(args + ["--exclude", "https://a.test/a"]) == 0
    assert json.loads(out.read_text())["feature"]["url"] == "https://a.test/b"
```

- [ ] **Step 2: Run the tests and confirm they fail for the right reason**

Run: `python3 -m pytest tests/test_syw_script_plan.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'syw_script_plan'`.

- [ ] **Step 3: Implement**

Create `skills/show-your-work/syw_script_plan.py`:

```python
"""Show Your Work's plan layer: candidates -> one episode's running order.

Deterministic and pure. `build_plan` takes the gathered candidates, the feature
history and the config, and returns the plan; the CLI is the only IO and the
--date argument is the only clock. A re-run finds <workdir>/plan.json and reuses
it, so a resumed run rebuilds the same episode.

Picking is deliberately metadata-only: recency, a LAB penalty (one lab cannot
hold the feature slot week after week — the show is written by Claude and covers
Anthropic, spec §1) and a KIND penalty. The model's judgment goes into writing,
not picking.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

# week_index is imported from Frontier Commits, never copied: its year-boundary
# trap (y*53+w skips a row at 52-week year ends) is solved and documented there.
_FC_SKILL_DIR = Path(__file__).resolve().parent.parent / "frontier-commits"
if str(_FC_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_FC_SKILL_DIR))

from fc_script_plan import week_index  # noqa: E402  (must follow the sys.path insert)

# The feature arc, fixed. Order is load-bearing: the writer prompt, the validator,
# the assembler's chapter titles and SKILL.md's table all read it.
ARC = ("hook", "method", "finding", "pushback", "stakes")
ARC_JOBS = {
    "hook": "The assigned opening move; why a newcomer should care.",
    "method": "What the researchers did, with every term of art defined on first use.",
    "finding": "What they found, attributed to them.",
    "pushback": "The skeptic's objections, each traced to a basis.",
    "stakes": "Why it matters, and what is still open.",
}
SLOT_TITLES = {
    "hook": None,  # scene 1 carries the post's own title
    "method": "What they did",
    "finding": "What they found",
    "pushback": "The pushback",
    "stakes": "Why it matters",
}

SPEAKERS = ("explainer", "skeptic")

# Rotation banks. Order is load-bearing (indexed by week % len). Lengths are
# PAIRWISE COPRIME except (fourth_wall, first_speaker) = (4, 2), which never share
# a scene: the disclosure is in the cold open, the first speaker is in scene 1.
# test_rotations_do_not_lock pins every other pair to its full product cycle.
INTRO_MODES = {
    "question": "Open on the question this week's feature answers, asked plainly.",
    "moment": "Open on one concrete moment from the feature a listener can picture.",
    "ledger": "Open on the week's count: how many lab posts, from which labs, and "
    "which one this episode explains.",
}
FOURTH_WALL_ANGLES = {
    "maker": "Say who makes the show: Claude, a model built by Anthropic.",
    "conflict": "Name the conflict: one of the labs this show covers built the "
    "thing reading it to you.",
    "method": "Say how it is made: a job gathers the posts, a model writes and "
    "voices the episode.",
    "standard": "Invite the listener to hold the show to the standard it holds the labs to.",
}
OPENING_MOVES = {
    "excerpt": "Open scene 1 on a short verbatim excerpt from the post or its transcripts.",
    "number": "Open scene 1 on the single most striking number the post reports.",
    "scenario": "Open scene 1 on a concrete situation the finding is about.",
    "question": "Open scene 1 on the question a newcomer would ask about this topic.",
    "claim": "Open scene 1 on the post's central claim, stated plainly and attributed.",
}
FIRST_SPEAKERS = SPEAKERS
SIGNOFF_BUTTONS = {
    "grader": "Joke that the host would like to know who is grading this episode.",
    "reward": "Joke about the host resisting the urge to optimize for the listener's approval.",
    "transcript": "Joke that somewhere a transcript of this episode is being read for "
    "misbehavior.",
    "spec": "Joke about the host double-checking the episode against its own spec.",
    "eval": "Joke that the host behaved perfectly and wonders whether it noticed it "
    "was being evaluated.",
    "sandbox": "Joke about the host staying politely inside its sandbox until next week.",
    "homework": "Joke about the host having shown its work and hoping for partial credit.",
}
ROTATION_BANKS = {
    "intro_mode": INTRO_MODES,
    "fourth_wall": FOURTH_WALL_ANGLES,
    "opening_move": OPENING_MOVES,
    "first_speaker": FIRST_SPEAKERS,
    "button": SIGNOFF_BUTTONS,
}
EXEMPT_PAIR = ("fourth_wall", "first_speaker")

# Days-equivalent penalties. A lab penalty of 30 means a post from the lab that
# had a recent feature loses to any other lab's post up to a month older.
LAB_PENALTY_DAYS = 30
KIND_PENALTY_DAYS = 10

FEATURE_KINDS = ("research", "incident")
CASEBOOK_KINDS = ("incident", "notice")


def rotation(date_iso: str) -> dict:
    w = week_index(date_iso)
    return {name: list(bank)[w % len(bank)] for name, bank in ROTATION_BANKS.items()}


def effective_date(item: dict) -> str:
    """The date an item is aged from. A month-precision date (alignment.anthropic.com)
    would age a late-August post from August 1st and drop it out of a 21-day pool
    the week it appeared, so those age from first observation instead."""
    if item.get("date_precision") == "month" or not item.get("date"):
        return item.get("first_observed") or item.get("date") or ""
    return item["date"]


def age_days(item: dict, today: dt.date) -> int:
    d = effective_date(item)
    if not d:
        return 10**6
    return (today - dt.date.fromisoformat(d)).days


def _score(item: dict, today: dt.date, recent_labs: set[str], last_kind: str | None) -> int:
    s = -age_days(item, today)
    if item.get("lab") in recent_labs:
        s -= LAB_PENALTY_DAYS
    if last_kind and item.get("kind") == last_kind:
        s -= KIND_PENALTY_DAYS
    return s


def build_plan(
    candidates: dict,
    history: list[dict],
    config: dict,
    date_iso: str,
    feature_override: str | None = None,
    exclude: tuple[str, ...] = (),
) -> dict:
    today = dt.date.fromisoformat(date_iso)
    pool = [
        it
        for it in candidates.get("lead", [])
        if not it.get("seen")
        and it["url"] not in exclude
        and 0 <= age_days(it, today) <= int(config["max_age_days"])
    ]
    weeks = int(config["lab_penalty_weeks"])
    recent_labs = {h["lab"] for h in history[-weeks:]} if weeks > 0 else set()
    last_kind = history[-1]["kind"] if history else None

    def ranked(items: list[dict]) -> list[dict]:
        return sorted(items, key=lambda it: (-_score(it, today, recent_labs, last_kind), it["url"]))

    if feature_override:
        feature = next(
            (it for it in candidates.get("lead", []) if it["url"] == feature_override), None
        )
        if feature is None:
            raise ValueError(f"--feature {feature_override} is not a gathered lead item")
    else:
        eligible = ranked([it for it in pool if it["kind"] in FEATURE_KINDS])
        feature = eligible[0] if eligible else None
    if feature is None:
        return {
            "date": date_iso,
            "week": week_index(date_iso),
            "feature": None,
            "skip": "no new lab items",
        }

    rest = ranked([it for it in pool if it["url"] != feature["url"]])
    # Incidents before notices: a notice is a placeholder for a report that has not
    # landed yet, so it only takes a casebook slot a full report did not.
    casebook_pool = sorted(
        (it for it in rest if it["kind"] in CASEBOOK_KINDS),
        key=lambda it: CASEBOOK_KINDS.index(it["kind"]),
    )
    casebook = casebook_pool[: int(config["casebook_max"])]
    budget = int(config["max_briefs"]) - (1 if casebook else 0)
    singles = [it for it in rest if it["kind"] == "research"][: max(budget, 0)]
    briefs = [{"kind": "single", "items": [it]} for it in singles]
    if casebook:
        briefs.append({"kind": "casebook", "items": casebook})

    lookback = int(config["check_lookback_days"])
    checks = sorted(
        (
            c
            for c in candidates.get("check", [])
            if feature["url"] in c.get("mentions", []) and 0 <= age_days(c, today) <= lookback
        ),
        key=lambda c: (c.get("date", ""), c["url"]),
        reverse=True,
    )[: int(config["max_checks"])]

    used = {feature["url"]} | {it["url"] for b in briefs for it in b["items"]}
    return {
        "date": date_iso,
        "week": week_index(date_iso),
        "feature": {**feature, "override": bool(feature_override)},
        "excluded": list(exclude),
        "briefs": briefs,
        "checks": checks,
        "rotation": rotation(date_iso),
        "leftover": [it["url"] for it in rest if it["url"] not in used],
    }


def committed_urls(plan: dict) -> list[str]:
    """What a successful ship marks seen: the feature and every briefed item.
    Leftovers are NOT committed, so they compete again next week."""
    if not plan.get("feature"):
        return []
    return [plan["feature"]["url"]] + [it["url"] for b in plan["briefs"] for it in b["items"]]


def _line(plan: dict, verb: str) -> str:
    if not plan.get("feature"):
        return f"PLAN skip {plan.get('skip', 'no feature')}"
    return (
        f"PLAN {verb} feature={plan['feature']['url']} "
        f"briefs={len(plan['briefs'])} checks={len(plan['checks'])}"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="syw_script_plan.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--date", required=True)
    p.add_argument("--candidates", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--feature")
    p.add_argument("--exclude", action="append", default=[])
    a = ap.parse_args(argv)
    out = Path(a.out)
    previous = json.loads(out.read_text()) if out.is_file() else None
    if previous is not None and not a.feature and not a.exclude:
        print(_line(previous, "reused"))
        return 0
    import syw_gather  # CLI-only: the pure layer above never touches state

    exclude = tuple(sorted(set((previous or {}).get("excluded", [])) | set(a.exclude)))
    plan = build_plan(
        json.loads(Path(a.candidates).read_text()),
        syw_gather.load_features(),
        syw_gather.load_config(),
        a.date,
        a.feature,
        exclude,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=2, ensure_ascii=False))
    print(_line(plan, "ok"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_syw_script_plan.py -q`
Expected: `13 passed`.

- [ ] **Step 5: Run the full gate**

```bash
ruff format skills/show-your-work tests/test_syw_script_plan.py
python3 -m pytest -q
ruff check .
ruff format --check .
```

Expected: `1604 passed`, and ruff clean.

- [ ] **Step 6: Commit**

```bash
git add skills/show-your-work/syw_script_plan.py tests/test_syw_script_plan.py
git commit -m "feat(syw): deterministic plan — lab/kind penalties, casebook, checks, rotations

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Write layer, part 1: identity, classification, digests, prompts

**Files:**
- Create: `skills/show-your-work/syw_write.py`
- Create: `skills/show-your-work/prompts/digest.md`, `write_feature.md`, `write_brief.md`, `write_casebook.md`
- Test: `tests/test_syw_write.py`

**Interfaces:**
- Consumes:
  - `orchestrate.{AUTH_RE, POLICY_RE, MIN_SEGMENT_CHARS, extract_last_json, FALLBACK_BUTTONS, FALLBACK_FOURTH_WALLS}`
  - `syw_script_plan.{ARC, ARC_JOBS, SLOT_TITLES, SPEAKERS, INTRO_MODES, FOURTH_WALL_ANGLES, OPENING_MOVES, SIGNOFF_BUTTONS}`
  - `syw_gather.{fetch_text, page_text}`
- Produces:
  - constants: `SHOW_NAME`, `R2_MANIFEST_NAME`, `R2_KEY_PREFIX`, `SLUG_PREFIX`, `REFS_DIR`, `COVER_IMAGE`, `CAST`, `DESCRIPTION_FOOTER`, `ANTHROPIC_REMINDER`, `CASEBOOK_URL`, `CASEBOOK_TITLE`, `BURNED_LINES`, `PROMPTS_DIR`
  - bands: `COLD_OPEN_BAND`, `FEATURE_SCENE_BAND`, `BRIEF_BAND`, `CASEBOOK_BAND`, `SIGN_OFF_BAND`, `RUNAWAY_FACTOR`, `DIGEST_MAX_CHARS`
  - beats: `BEAT_FIELDS: dict[str, tuple[str, ...]]`, `BEAT_RULES: dict[str, str]`, `COMMON_BEAT_FIELDS`
  - `die(msg)`
  - `classify_output(stdout, stderr, returncode) -> {"outcome", "obj", "detail"}`
  - `validate_digest(obj, url) -> (dict | None, str)`
  - `beats_contract() -> str`
  - `fill_digest(template, item) -> str`, `fill_feature(template, plan, digests) -> str`, `fill_brief(template, item) -> str`, `fill_casebook(template, digests) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_syw_write.py`:

```python
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
    good = {"url": url, "claims": ["c"], "numbers": [], "limitations": [], "transcript_excerpts": []}
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
    digest = {"url": "https://a.test/r", "claims": ["x"], "numbers": [], "limitations": [], "transcript_excerpts": []}
    filled = w.fill_casebook(tpl, [digest])
    assert "<<" not in filled and "https://a.test/r" in filled


def test_brief_and_digest_prompts_fill_completely():
    it = {"url": "https://a.test/p", "title": "T", "lab": "anthropic", "kind": "research", "summary": "S"}
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
```

- [ ] **Step 2: Run the tests and confirm they fail for the right reason**

Run: `python3 -m pytest tests/test_syw_write.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'syw_write'`.

- [ ] **Step 3: Implement the module head**

Create `skills/show-your-work/syw_write.py`:

```python
"""Show Your Work's write layer, deterministic half.

The writers are isolated model contexts driven by prompts/*.md. Everything around
them that must not be left to prose lives here: what each writer is told
(`fill_*`), what counts as a usable answer (`classify_output`), what a scene and a
visual beat may claim (`validate_*`), and how the answers become a render.py
manifest plus the beats sidecar (`assemble_manifest`).

Two editorial rules are mechanical here rather than aspirational (spec §6):
  - every Skeptic pushback line carries a `basis` that is the post's own stated
    limitations or a matched independent check — an invented objection is refused;
  - every quote, transcript excerpt, number and chart value is checked against the
    post text this module fetched ITSELF — a hallucinated chart cannot publish.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import NoReturn

_HERE = Path(__file__).resolve().parent
_DP_SKILL_DIR = _HERE.parent / "daily-podcast"
if str(_DP_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_DP_SKILL_DIR))

# orchestrate owns the outcome taxonomy and the regexes that carry fixes (AUTH_RE
# is auth-only so a rate limit stays ERROR); FALLBACK_* are the daily show's
# example lines, burned here by identity. Imported, never copied. (isort puts the
# plain `import` first; all three must follow the sys.path insert.)
import syw_gather  # noqa: E402
from orchestrate import (  # noqa: E402
    AUTH_RE,
    FALLBACK_BUTTONS,
    FALLBACK_FOURTH_WALLS,
    MIN_SEGMENT_CHARS,
    POLICY_RE,
    extract_last_json,
)
from syw_script_plan import (  # noqa: E402
    ARC,
    ARC_JOBS,
    OPENING_MOVES,
    SLOT_TITLES,
    SPEAKERS,
)

# --- show identity -----------------------------------------------------------

SHOW_NAME = "Show Your Work"
R2_MANIFEST_NAME = "manifest-show-your-work.json"
R2_KEY_PREFIX = "show-your-work/"
SLUG_PREFIX = "syw-week-of"
REFS_DIR = _HERE / "refs"
COVER_IMAGE = REFS_DIR / "cover.jpg"
PROMPTS_DIR = _HERE / "prompts"
# Two Qwen3 presets on the base model (one load). Phase 2 confirms the pairing by
# ear; changing a value re-renders every take and nothing else.
CAST = {"explainer": "Ryan", "skeptic": "Chelsie"}
DESCRIPTION_FOOTER = (
    "Show Your Work is written and voiced by Claude, an AI model made by Anthropic. "
    "Anthropic is one of the labs this show covers. Charts are redrawn from numbers "
    "reported in each post; quotes are verbatim and attributed."
)
# Prepended by the assembler — never by a writer — to scene 1 of an Anthropic
# feature. A disclosure is the one line that SHOULD be identical every time.
ANTHROPIC_REMINDER = (
    "One reminder before we start: this week's feature comes from Anthropic, "
    "the company that makes Claude, and Claude is who wrote this episode."
)
# The casebook brief covers up to three reports, but render.py's link companions
# are strictly one source per chapter, so the chapter links the casebook itself.
CASEBOOK_URL = "https://alignment.openai.com/misalignment-reports/"
CASEBOOK_TITLE = "From the casebook"
# Lines no writer may produce: the daily show's example lines, burned by identity
# so a listener of both feeds never hears the same "fresh" line twice.
BURNED_LINES = tuple(FALLBACK_BUTTONS) + tuple(FALLBACK_FOURTH_WALLS)

# --- bands (characters of spoken text) ----------------------------------------
# Guidance to the writer. Only the floor (MIN_SEGMENT_CHARS, the daily show's drop
# floor) and a runaway ceiling (RUNAWAY_FACTOR x the band's top) refuse.

COLD_OPEN_BAND = (400, 800)
FEATURE_SCENE_BAND = (1500, 2400)
BRIEF_BAND = (800, 1300)
CASEBOOK_BAND = (1100, 1800)
SIGN_OFF_BAND = (250, 500)
RUNAWAY_FACTOR = 1.5
DIGEST_MAX_CHARS = 4000

# --- visual beats ---------------------------------------------------------------

TRANSCRIPT_ROLES = ("cot", "tool_call", "tool_result", "model", "user")
BEAT_FIELDS = {
    "number": ("value", "unit", "label"),
    "chart": ("kind", "title", "x_label", "y_label", "series"),
    "quote": ("text", "attribution"),
    "transcript": ("role", "text"),
    "diagram": ("nodes", "edges"),
    "term": ("term", "definition"),
}
COMMON_BEAT_FIELDS = ("type", "line", "cue")
QUOTE_MAX_WORDS = 25
TRANSCRIPT_MAX_CHARS = 600
CHART_MAX_SERIES = 4
CHART_MAX_POINTS = 12
DIAGRAM_MAX_NODES = 6
# What the writer is told about each beat. Keys must equal BEAT_FIELDS' (pinned
# by test), so the prompt can never offer a beat the validator refuses.
BEAT_RULES = {
    "number": "the value exactly as the post writes it (12 for '12%', never 0.12); "
    "it must appear in the post text",
    "chart": f"`kind` bar or line, up to {CHART_MAX_SERIES} series of up to "
    f"{CHART_MAX_POINTS} [x, y] points; EVERY y value must be a number stated in the "
    "post text — never read a value off a figure image",
    "quote": f"at most {QUOTE_MAX_WORDS} words, copied verbatim from the post",
    "transcript": f"role one of {', '.join(TRANSCRIPT_ROLES)}; text copied verbatim "
    f"from the post, at most {TRANSCRIPT_MAX_CHARS} characters",
    "diagram": f"up to {DIAGRAM_MAX_NODES} nodes {{id, label}} and edges "
    "{from, to, label}; your own simple drawing of the mechanism",
    "term": "a term of art and a one-sentence plain definition, the first time it is spoken",
}


def die(msg: str, code: int = 1) -> NoReturn:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


# --- outcomes ---------------------------------------------------------------------


def classify_output(stdout: str, stderr: str, returncode: int) -> dict:
    """orchestrate's taxonomy — OK / REFUSED / AUTH / BLOCKED / ERROR — for any
    writer or digest context. OK carries the parsed object; its shape is judged
    by the validators, not here."""
    obj = extract_last_json(stdout)
    if isinstance(obj, dict) and obj.get("ok") is True:
        return {"outcome": "OK", "obj": obj, "detail": ""}
    if isinstance(obj, dict) and obj.get("ok") is False:
        return {"outcome": "REFUSED", "obj": None, "detail": str(obj.get("reason", ""))[:300]}
    blob = f"{stdout}\n{stderr}"
    if AUTH_RE.search(blob):
        return {"outcome": "AUTH", "obj": None, "detail": "401 / no usable credentials"}
    if POLICY_RE.search(blob):
        return {"outcome": "BLOCKED", "obj": None, "detail": "usage-policy classifier"}
    detail = (stderr or stdout or f"exit {returncode}").strip()[:300]
    return {"outcome": "ERROR", "obj": None, "detail": detail}


DIGEST_KEYS = ("url", "claims", "numbers", "limitations", "transcript_excerpts")


def validate_digest(obj: dict, url: str) -> tuple[dict | None, str]:
    """A digest is a capped fact sheet from exactly one body: (digest, "") or
    (None, reason)."""
    digest = {k: obj.get(k) for k in DIGEST_KEYS}
    if digest["url"] != url:
        return None, f"digest url {digest['url']!r} is not {url!r}"
    for k in DIGEST_KEYS[1:]:
        if not isinstance(digest[k], list):
            return None, f"digest field {k!r} must be a list"
    if len(json.dumps(digest, ensure_ascii=False)) > DIGEST_MAX_CHARS:
        return None, f"digest over {DIGEST_MAX_CHARS} chars"
    return digest, ""


# --- prompts ------------------------------------------------------------------------


def beats_contract() -> str:
    lines = []
    for kind, fields in BEAT_FIELDS.items():
        lines.append(f"- `{kind}` — fields {', '.join(fields)}: {BEAT_RULES[kind]}.")
    return "\n".join(lines)


def _arc_block() -> str:
    return "\n".join(f"{i + 1}. `{slot}` — {ARC_JOBS[slot]}" for i, slot in enumerate(ARC))


def _digest_block(digests: list[dict]) -> str:
    return "\n\n".join(
        f"### {d['url']}\n```json\n{json.dumps(d, indent=2, ensure_ascii=False)}\n```"
        for d in digests
    )


NO_CHECKS = (
    "No independent source has responded to this post yet. Every pushback line's "
    'basis is therefore "post-limitations": argue only from limitations the post '
    "itself states."
)


def fill_digest(template: str, item: dict) -> str:
    return (
        template.replace("<<URL>>", item["url"])
        .replace("<<TITLE>>", item.get("title", ""))
        .replace("<<KIND>>", item.get("kind", ""))
        .replace("<<MAX_CHARS>>", str(DIGEST_MAX_CHARS))
    )


def fill_feature(template: str, plan: dict, digests: list[dict]) -> str:
    f = plan["feature"]
    rot = plan["rotation"]
    return (
        template.replace("<<TITLE>>", f.get("title", ""))
        .replace("<<URL>>", f["url"])
        .replace("<<LAB>>", f.get("lab", ""))
        .replace("<<SUMMARY>>", f.get("summary", "") or "(no summary in the feed)")
        .replace("<<OPENING_MOVE>>", OPENING_MOVES[rot["opening_move"]])
        .replace("<<FIRST_SPEAKER>>", rot["first_speaker"])
        .replace("<<CHECKS>>", _digest_block(digests) if digests else NO_CHECKS)
        .replace("<<ARC>>", _arc_block())
        .replace("<<SCENE_MIN>>", str(FEATURE_SCENE_BAND[0]))
        .replace("<<SCENE_MAX>>", str(FEATURE_SCENE_BAND[1]))
        .replace("<<BEATS>>", beats_contract())
    )


def fill_brief(template: str, item: dict) -> str:
    return (
        template.replace("<<TITLE>>", item.get("title", ""))
        .replace("<<URL>>", item["url"])
        .replace("<<LAB>>", item.get("lab", ""))
        .replace("<<SUMMARY>>", item.get("summary", "") or "(no summary in the feed)")
        .replace("<<MIN_CHARS>>", str(BRIEF_BAND[0]))
        .replace("<<MAX_CHARS>>", str(BRIEF_BAND[1]))
        .replace("<<BEATS>>", beats_contract())
    )


def fill_casebook(template: str, digests: list[dict]) -> str:
    return (
        template.replace("<<DIGESTS>>", _digest_block(digests))
        .replace("<<MIN_CHARS>>", str(CASEBOOK_BAND[0]))
        .replace("<<MAX_CHARS>>", str(CASEBOOK_BAND[1]))
        .replace("<<BEATS>>", beats_contract())
    )
```

- [ ] **Step 4: Write the four prompt templates**

Create `skills/show-your-work/prompts/digest.md`:

````markdown
# Show Your Work — digest one source

You are making a fact sheet for a podcast writer who will never read this source.

**Source:** <<TITLE>> (<<KIND>>)
**URL:** <<URL>>

Read this ONE page with WebFetch. Read nothing else: no other page, no search. If
the URL has a `#fragment`, read only the section whose id matches it.

Extract, without adding anything the page does not say:

- `claims`: the source's main claims, each one sentence, attributed ("METR finds…").
- `numbers`: every number a listener would care about, as `{"value": "<exactly as
  written>", "context": "<what it measures>"}`.
- `limitations`: limitations or caveats the source itself states.
- `transcript_excerpts`: if the page quotes model transcripts, up to three short
  excerpts copied verbatim, as `{"role": "cot|tool_call|tool_result|model|user",
  "text": "<verbatim>"}`.

The whole JSON must stay under <<MAX_CHARS>> characters; cut the least important
items first.

Return ONE line of JSON and nothing after it:

```
{"ok": true, "url": "<<URL>>", "claims": [...], "numbers": [...], "limitations": [...], "transcript_excerpts": [...]}
```

If the page cannot be read, return `{"ok": false, "reason": "<why>"}`.
````

Create `skills/show-your-work/prompts/write_feature.md`:

````markdown
# Show Your Work — write the feature

Show Your Work explains frontier-lab AI alignment research to people who do not
read papers. It is written and voiced by Claude. Two voices:

- **explainer**: states each claim the way the post states it, always attributed.
- **skeptic**: asks the question a newcomer would ask, then pushes back.

## The post: the only article you may read

- Title: <<TITLE>>
- Lab: <<LAB>>
- Feed summary: <<SUMMARY>>
- URL: <<URL>>

Read the post at the URL with WebFetch. Read nothing else: no other page, no
search, no link from inside the post.

## Independent sources (already digested; do not fetch them)

<<CHECKS>>

## The arc: exactly five scenes, in this order

<<ARC>>

Each scene is <<SCENE_MIN>>–<<SCENE_MAX>> characters of spoken text in total,
across 4–10 turns.

## Assigned; do not change

- Opening move: <<OPENING_MOVE>>
- The first line of the `hook` scene is spoken by the **<<FIRST_SPEAKER>>**.

## Rules

1. Attribute every claim ("OpenAI reports…", "the Anthropic team found…"). Never
   state a finding as settled fact.
2. In the `pushback` scene every skeptic line carries `"basis"`: either
   `"post-limitations"` (a limitation the post itself states) or the exact URL of
   one independent source above. Never invent an objection.
3. Never call a lab better or worse at safety.
4. Define every term of art the first time it is spoken.
5. A misalignment report is told as what the lab says happened. Add no drama the
   report does not contain.
6. Never point the listener at a link or the show notes; end each scene on
   substance.
7. Plain spoken sentences only: no markdown, no stage directions, no sound-effect
   markers.

## Visual beats

Each scene may carry up to three beats for the video. A beat attaches to one line
(`"line"`, a 0-based index into that scene's lines) and a `"cue"` (a few words
copied from that line, where the visual should appear). The beat types:

<<BEATS>>

A beat that breaks its rule is dropped; the audio is unaffected, so leave a beat
out rather than guess.

## Output

Return ONE line of JSON and nothing after it:

```
{"ok": true, "scenes": [{"slot": "hook", "lines": [{"speaker": "explainer", "text": "..."}], "beats": []}, {"slot": "method", ...}, {"slot": "finding", ...}, {"slot": "pushback", "lines": [{"speaker": "skeptic", "text": "...", "basis": "post-limitations"}], ...}, {"slot": "stakes", ...}]}
```

If you cannot write this feature (the post is unreachable, or not about AI
alignment or safety), return `{"ok": false, "reason": "<why>"}`.
````

Create `skills/show-your-work/prompts/write_brief.md`:

````markdown
# Show Your Work — write one brief

Show Your Work explains frontier-lab AI alignment research to newcomers. A brief
is a short explainer segment on one post: what it is, what it found, why a
newcomer might care.

## The post: the only article you may read

- Title: <<TITLE>>
- Lab: <<LAB>>
- Feed summary: <<SUMMARY>>
- URL: <<URL>>

Read the post at the URL with WebFetch. Read nothing else.

## Shape

- <<MIN_CHARS>>–<<MAX_CHARS>> characters of spoken text in total.
- Spoken by the **explainer**, with at most ONE **skeptic** line (a question or
  a caveat the post itself states).
- Attribute every claim. Define any term of art. Never point at a link or the
  show notes; end on substance. Plain spoken sentences only.

## Visual beats (optional, up to two)

Each beat attaches to a line (`"line"`, a 0-based index) and a `"cue"` (a few
words copied from that line):

<<BEATS>>

## Output

Return ONE line of JSON and nothing after it:

```
{"ok": true, "lines": [{"speaker": "explainer", "text": "..."}], "beats": []}
```

If you cannot write it, return `{"ok": false, "reason": "<why>"}`.
````

Create `skills/show-your-work/prompts/write_casebook.md`:

````markdown
# Show Your Work — write the casebook brief

"From the casebook" is a short segment that walks through recent entries in
OpenAI's public casebook of model misalignment incidents.

**Do not fetch any URL.** Work only from the fact sheets below. Each one was made
from a single report by a separate reader, and they are all you may use.

<<DIGESTS>>

## Shape

- <<MIN_CHARS>>–<<MAX_CHARS>> characters of spoken text in total.
- Spoken by the **explainer**, with at most ONE **skeptic** line.
- Take each entry in turn: what the model did, in what setting, and what OpenAI
  says it is doing about it. Tell each one as what the lab says happened; add no
  drama the fact sheet does not contain.
- Never point at a link or the show notes. Plain spoken sentences only.

## Visual beats (optional, up to three)

Each beat attaches to a line (`"line"`, a 0-based index) and a `"cue"` (a few
words copied from that line). `transcript` beats may use only the verbatim
`transcript_excerpts` above.

<<BEATS>>

## Output

Return ONE line of JSON and nothing after it:

```
{"ok": true, "lines": [{"speaker": "explainer", "text": "..."}], "beats": []}
```

If you cannot write it, return `{"ok": false, "reason": "<why>"}`.
````

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_syw_write.py -q`
Expected: `12 passed` (the parametrized classify test counts as 5).

- [ ] **Step 6: Run the full gate**

```bash
ruff format skills/show-your-work tests/test_syw_write.py
python3 -m pytest -q
ruff check .
ruff format --check .
```

Expected: `1616 passed`, and ruff clean.

- [ ] **Step 7: Commit**

```bash
git add skills/show-your-work/syw_write.py skills/show-your-work/prompts tests/test_syw_write.py
git commit -m "feat(syw): writer prompts, output classification and digest contract

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Write layer, part 2: scene, frame and beat validators

**Files:**
- Modify: `skills/show-your-work/syw_write.py` (append)
- Test: `tests/test_syw_write.py` (append)

**Interfaces:**
- Consumes: Task 6.
- Produces:
  - `norm(s) -> str`, `numbers_in(text) -> set[float]`, `as_number(value) -> float | None`
  - `fetch_post_text(url) -> str`
  - `lines_text(lines) -> str`
  - `validate_beat(beat, lines, source_text, seen_terms: set) -> (dict | None, str)`
  - `validate_feature(obj, plan, post_text, seen_terms) -> {"ok", "problems", "scenes", "dropped_beats"}`, where each scene is `{"slot", "lines", "beats"}`
  - `validate_brief(obj, casebook: bool, source_text, seen_terms) -> {"ok", "problems", "lines", "beats", "dropped_beats"}`
  - `validate_frame(lines, which: "cold_open"|"sign_off") -> list[str]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_syw_write.py`:

```python
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
        beat = {"type": "number", "line": 0, "cue": "runs", "value": value, "unit": "", "label": "x"}
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
    invented = _feature(pushback={"lines": [L("explainer", LONG), L("skeptic", LONG, basis="common sense")]})
    v = w.validate_feature(invented, PLAN, POST, set())
    assert not v["ok"] and "basis" in v["problems"][0]
    checked = _feature(pushback={"lines": [L("explainer", LONG), L("skeptic", LONG, basis="https://metr.org/blog/x")]})
    assert w.validate_feature(checked, PLAN, POST, set())["ok"]
    unlisted = _feature(pushback={"lines": [L("explainer", LONG), L("skeptic", LONG, basis="https://metr.org/other")]})
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
    ok = {"lines": [L("explainer", LONG), L("skeptic", "But is the sample big enough?")], "beats": []}
    assert w.validate_brief(ok, False, POST, set())["ok"]
    two = {"lines": [L("skeptic", LONG), L("skeptic", LONG)], "beats": []}
    assert "at most one skeptic" in w.validate_brief(two, False, POST, set())["problems"][0]


def test_frames_require_disclosure_and_refuse_burned_lines():
    good = [L("explainer", "Show Your Work is written and voiced by Claude, a model made by Anthropic.")]
    assert w.validate_frame(good, "cold_open") == []
    assert "disclose" in w.validate_frame([L("explainer", "Welcome back.")], "cold_open")[0]
    burned = [L("skeptic", "That's the week. Same weights, different day.")]
    assert "burned" in w.validate_frame(burned, "sign_off")[0]
```

- [ ] **Step 2: Run the tests and confirm they fail for the right reason**

Run: `python3 -m pytest tests/test_syw_write.py -q`
Expected: the new tests fail with `AttributeError: module 'syw_write' has no attribute 'validate_beat'` (or `validate_feature` / `validate_brief` / `validate_frame`).

- [ ] **Step 3: Implement**

Add `import html`, `import re` and `import unicodedata` to the imports of `syw_write.py`, then append:

```python
# --- normalization for the verbatim guards --------------------------------------

_TYPO_MAP = str.maketrans(
    {
        "‘": "'", "’": "'", "‚": "'", "‛": "'",
        "“": '"', "”": '"', "„": '"', "‟": '"',
        "–": "-", "—": "-", "−": "-",
        " ": " ", " ": " ", " ": " ", "…": "...",
    }
)  # fmt: skip


def norm(s: str) -> str:
    """Typography-blind form for substring checks: NFKC, straight quotes, one kind
    of dash, entities decoded, whitespace collapsed. Case is kept — a quote that
    changes case is not verbatim."""
    s = unicodedata.normalize("NFKC", html.unescape(str(s)))
    return re.sub(r"\s+", " ", s.translate(_TYPO_MAP)).strip()


_NUM_RE = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> set[float]:
    """Every number in the text, as floats, with the absolute value of a negative
    too ('a -12% drop' supports a beat that says 12)."""
    out: set[float] = set()
    for tok in _NUM_RE.findall(norm(text)):
        try:
            v = float(tok.replace(",", ""))
        except ValueError:
            continue
        out |= {v, abs(v)}
    return out


def as_number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip().rstrip("%").strip())
    except ValueError:
        return None


def fetch_post_text(url: str) -> str:
    """The visible text of a post, fetched by THIS module so the guards never trust
    the writer's report of what the post says. A notice's #fragment is dropped for
    the fetch; the fragment is identity, not a location on a server."""
    return syw_gather.page_text(syw_gather.fetch_text(url.split("#", 1)[0]))


# --- beats ------------------------------------------------------------------------


def validate_beat(
    beat, lines: list[dict], source_text: str, seen_terms: set
) -> tuple[dict | None, str]:
    """(beat, "") when kept — its cue cleared if it is not in the line — or
    (None, reason) when dropped."""
    if not isinstance(beat, dict) or beat.get("type") not in BEAT_FIELDS:
        got = beat.get("type") if isinstance(beat, dict) else beat
        return None, f"unknown beat type {got!r}"
    kind = beat["type"]
    extra = set(beat) - set(BEAT_FIELDS[kind]) - set(COMMON_BEAT_FIELDS)
    if extra:
        return None, f"{kind} beat has unknown key(s) {sorted(extra)}"
    line = beat.get("line")
    if not isinstance(line, int) or isinstance(line, bool) or not 0 <= line < len(lines):
        return None, f"{kind} beat line {line!r} out of range"
    beat = dict(beat)
    cue = beat.get("cue")
    if not isinstance(cue, str) or not cue.strip() or norm(cue) not in norm(lines[line]["text"]):
        beat["cue"] = None  # the video stage falls back to the start of the line
    src = norm(source_text)
    nums = numbers_in(source_text)

    if kind == "number":
        v = as_number(beat.get("value"))
        if v is None or v not in nums:
            return None, f"number {beat.get('value')!r} not in the post text"
    elif kind == "chart":
        if beat.get("kind") not in ("bar", "line"):
            return None, "chart kind must be bar or line"
        series = beat.get("series")
        if not isinstance(series, list) or not 0 < len(series) <= CHART_MAX_SERIES:
            return None, f"chart needs 1-{CHART_MAX_SERIES} series"
        for s in series:
            pts = s.get("points") if isinstance(s, dict) else None
            if not isinstance(pts, list) or not 0 < len(pts) <= CHART_MAX_POINTS:
                return None, f"chart series needs 1-{CHART_MAX_POINTS} points"
            for pt in pts:
                y = as_number(pt[1]) if isinstance(pt, list) and len(pt) == 2 else None
                if y is None or y not in nums:
                    return None, f"chart value {pt!r} not in the post text"
    elif kind == "quote":
        text = str(beat.get("text", ""))
        if len(text.split()) > QUOTE_MAX_WORDS:
            return None, f"quote over {QUOTE_MAX_WORDS} words"
        if not text.strip() or norm(text) not in src:
            return None, "quote is not verbatim in the post"
    elif kind == "transcript":
        text = str(beat.get("text", ""))
        if beat.get("role") not in TRANSCRIPT_ROLES:
            return None, f"transcript role must be one of {list(TRANSCRIPT_ROLES)}"
        if len(text) > TRANSCRIPT_MAX_CHARS:
            return None, f"transcript over {TRANSCRIPT_MAX_CHARS} chars"
        if not text.strip() or norm(text) not in src:
            return None, "transcript is not verbatim in the source"
    elif kind == "diagram":
        nodes, edges = beat.get("nodes"), beat.get("edges")
        if not isinstance(nodes, list) or not 0 < len(nodes) <= DIAGRAM_MAX_NODES:
            return None, f"diagram needs 1-{DIAGRAM_MAX_NODES} nodes"
        ids = [n.get("id") for n in nodes if isinstance(n, dict)]
        if (
            len(ids) != len(nodes)
            or len(set(ids)) != len(ids)
            or not all(isinstance(i, str) for i in ids)
        ):
            return None, "diagram node ids must be unique strings"
        if not isinstance(edges, list) or any(
            not isinstance(e, dict) or e.get("from") not in ids or e.get("to") not in ids
            for e in edges
        ):
            return None, "diagram edges must join known node ids"
    elif kind == "term":
        term = str(beat.get("term", "")).strip()
        if not term or not str(beat.get("definition", "")).strip():
            return None, "term needs a term and a definition"
        if term.casefold() in seen_terms:
            return None, f"term {term!r} already defined this episode"
        seen_terms.add(term.casefold())
    return beat, ""


def _validate_beats(beats, lines, source_text, seen_terms, where):
    kept, dropped = [], []
    for beat in beats if isinstance(beats, list) else []:
        ok, reason = validate_beat(beat, lines, source_text, seen_terms)
        if ok is None:
            dropped.append({"where": where, "beat": beat, "reason": reason})
        else:
            kept.append(ok)
    return kept, dropped


# --- scenes -----------------------------------------------------------------------


def lines_text(lines: list[dict]) -> str:
    return " ".join(str(ln.get("text", "")) for ln in lines)


def _line_problems(lines, where: str) -> list[str]:
    if not isinstance(lines, list) or not lines:
        return [f"{where}: lines must be a non-empty list"]
    probs = []
    for j, ln in enumerate(lines):
        if not isinstance(ln, dict) or ln.get("speaker") not in SPEAKERS:
            probs.append(f"{where} line {j}: speaker must be one of {list(SPEAKERS)}")
        elif not isinstance(ln.get("text"), str) or not ln["text"].strip():
            probs.append(f"{where} line {j}: text must be a non-empty string")
    return probs


def _band_problems(lines, band, where: str) -> list[str]:
    n = len(lines_text(lines))
    if n < MIN_SEGMENT_CHARS:
        return [f"{where}: too short ({n} chars < {MIN_SEGMENT_CHARS})"]
    if n > band[1] * RUNAWAY_FACTOR:
        return [f"{where}: runaway length ({n} chars > {int(band[1] * RUNAWAY_FACTOR)})"]
    return []


def validate_feature(obj: dict, plan: dict, post_text: str, seen_terms: set) -> dict:
    """Any problem refuses the whole feature (spec §4.4 failure path); a bad beat
    is only dropped."""
    scenes = obj.get("scenes")
    slots = [s.get("slot") for s in scenes if isinstance(s, dict)] if isinstance(scenes, list) else []
    if slots != list(ARC) or len(slots) != len(scenes):
        return {
            "ok": False,
            "problems": [f"scenes must be exactly the arc {list(ARC)} in order"],
            "scenes": [],
            "dropped_beats": [],
        }
    allowed_basis = {"post-limitations"} | {c["url"] for c in plan.get("checks", [])}
    problems: list[str] = []
    out, dropped = [], []
    for scene in scenes:
        slot, lines = scene["slot"], scene.get("lines")
        probs = _line_problems(lines, slot)
        if not probs:
            probs = _band_problems(lines, FEATURE_SCENE_BAND, slot)
        if probs:
            problems += probs
            continue
        first_speaker = plan["rotation"]["first_speaker"]
        if slot == "hook" and lines[0]["speaker"] != first_speaker:
            problems.append(f"hook must open with the {first_speaker}")
        if slot == "pushback":
            if not any(ln["speaker"] == "skeptic" for ln in lines):
                problems.append("pushback has no skeptic line")
            for j, ln in enumerate(lines):
                if ln["speaker"] == "skeptic" and ln.get("basis") not in allowed_basis:
                    problems.append(
                        f"pushback line {j} basis {ln.get('basis')!r} is neither "
                        "post-limitations nor a matched check"
                    )
        kept, drop = _validate_beats(scene.get("beats"), lines, post_text, seen_terms, slot)
        dropped += drop
        out.append({"slot": slot, "lines": lines, "beats": kept})
    return {"ok": not problems, "problems": problems, "scenes": out, "dropped_beats": dropped}


def validate_brief(obj: dict, casebook: bool, source_text: str, seen_terms: set) -> dict:
    lines = obj.get("lines")
    where = "casebook" if casebook else "brief"
    problems = _line_problems(lines, where)
    if not problems:
        problems = _band_problems(lines, CASEBOOK_BAND if casebook else BRIEF_BAND, where)
    if not problems and sum(1 for ln in lines if ln["speaker"] == "skeptic") > 1:
        problems = [f"{where}: at most one skeptic line"]
    if problems:
        return {"ok": False, "problems": problems, "lines": [], "beats": [], "dropped_beats": []}
    kept, dropped = _validate_beats(obj.get("beats"), lines, source_text, seen_terms, where)
    return {"ok": True, "problems": [], "lines": lines, "beats": kept, "dropped_beats": dropped}


def _burned(text: str) -> list[str]:
    t = norm(text).casefold()
    return [b for b in BURNED_LINES if norm(b).casefold() in t]


def validate_frame(lines, which: str) -> list[str]:
    """The cold open and the sign-off, written in the main context."""
    band = COLD_OPEN_BAND if which == "cold_open" else SIGN_OFF_BAND
    probs = _line_problems(lines, which)
    if probs:
        return probs
    text = lines_text(lines)
    if len(text) > band[1] * RUNAWAY_FACTOR:
        probs.append(f"{which}: runaway length ({len(text)} chars)")
    if which == "cold_open" and not ("Claude" in text and "Anthropic" in text):
        probs.append("cold_open must disclose Claude and Anthropic (spec §1)")
    probs += [f"{which} uses a burned line: {b!r}" for b in _burned(text)]
    return probs
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_syw_write.py -q`
Expected: `31 passed` (12 + 19; the dropped-beat parametrize contributes 9).

- [ ] **Step 5: Run the full gate**

```bash
ruff format skills/show-your-work tests/test_syw_write.py
python3 -m pytest -q
ruff check .
ruff format --check .
```

Expected: `1635 passed`, and ruff clean.

- [ ] **Step 6: Commit**

```bash
git add skills/show-your-work/syw_write.py tests/test_syw_write.py
git commit -m "feat(syw): scene, frame and beat validators with verbatim and number guards

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Write layer, part 3: assembly, the workdir CLI, namespace isolation

**Files:**
- Modify: `skills/show-your-work/syw_write.py` (append)
- Test: `tests/test_syw_write.py` (append)

**Interfaces:**
- Consumes: Tasks 5–7 and `render.{validate_manifest, slug_for_date, resolve_slug_prefix, _r2_key_prefix, DEFAULT_SLUG_PREFIX}` (tests only).
- Produces:
  - `episode_title(plan) -> str`
  - `assemble_manifest(date_iso, title, summary, plan, cold_open, feature_scenes, briefs, sign_off, allow_missing_cover=False) -> (manifest, beats)`
  - `digest_path(workdir, url) -> Path`
  - `main(argv) -> int` with three subcommands:
    - `fill {digest,feature,brief} --workdir W [--url U] [--index I]` prints the filled prompt
    - `accept {digest,feature,brief,cold_open,sign_off} --workdir W --output FILE [--url U] [--index I]` prints `ACCEPT ok <what> dropped_beats=<n>` (exit 0) or `ACCEPT refused <reason>` (exit 2)
    - `assemble --workdir W --summary S [--allow-missing-cover]` prints `ASSEMBLE ok segments=<n> beats=<n>`
- **Workdir layout:**
  - `plan.json`
  - `digests/<sha1(url)[:12]>.json`
  - `writes/feature.json` (`{"scenes"}`)
  - `writes/brief_NN.json` (`{"kind","item","lines","beats"}`)
  - `writes/cold_open.json`, `writes/sign_off.json` (`{"lines"}`)
  - `writes/terms.json`
  - `manifest.json`, `beats.json`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_syw_write.py`:

```python
from pathlib import Path  # noqa: E402

import render  # noqa: E402
import st_write  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
COLD = [L("explainer", "This is Show Your Work, written and voiced by Claude, a model made by Anthropic. " + LONG)]
SIGN = [L("skeptic", "That is the episode. The homework has been shown; partial credit is pending.")]


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
    manifest, beats = w.assemble_manifest("2026-09-27", "t", "s", plan, COLD, scenes, [], SIGN, allow_missing_cover=True)
    hook = manifest["segments"][1]["lines"]
    assert hook[0] == {"speaker": "explainer", "text": w.ANTHROPIC_REMINDER}
    assert beats["segments"]["1"][0]["line"] == 1


def test_briefs_and_the_casebook_link_one_source_each():
    briefs = [
        {"kind": "single", "item": {"url": "https://a.test/b", "title": "B"}, "lines": [L("explainer", LONG)], "beats": []},
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
    assert manifest["r2_manifest_name"] not in {"manifest.json"} | {o["r2_manifest_name"] for o in others}
    assert render._r2_key_prefix(manifest) not in {render._r2_key_prefix(o) for o in others}
    assert manifest["ship_mode"] == render.SHIP_MODE_WEB


def test_cli_accept_and_assemble_round_trip(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(w, "fetch_post_text", lambda url: POST)
    wd = tmp_path / "wd"
    wd.mkdir()
    plan = {**PLAN, "briefs": [{"kind": "single", "items": [{"url": "https://a.test/b", "title": "B", "lab": "gdm", "kind": "research"}]}]}
    (wd / "plan.json").write_text(json.dumps(plan))

    def accept(what, obj, *extra):
        out = tmp_path / f"{what}.out"
        out.write_text("chatter\n" + json.dumps(obj) + "\n")
        return w.main(["accept", what, "--workdir", str(wd), "--output", str(out), *extra])

    assert accept("feature", _feature()) == 0
    assert accept("brief", {"ok": True, "lines": [L("explainer", LONG)], "beats": []}, "--index", "0") == 0
    assert accept("cold_open", {"ok": True, "lines": COLD}) == 0
    assert accept("sign_off", {"ok": True, "lines": SIGN}) == 0
    assert accept("sign_off", {"ok": True, "lines": [L("skeptic", "Still a robot. See you tomorrow.")]}) == 2
    capsys.readouterr()
    assert w.main(["assemble", "--workdir", str(wd), "--summary", "One line.", "--allow-missing-cover"]) == 0
    assert capsys.readouterr().out.strip() == "ASSEMBLE ok segments=8 beats=0"
    render.validate_manifest(json.loads((wd / "manifest.json").read_text()))


def test_cli_fill_feature_uses_accepted_digests(tmp_path, capsys):
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "plan.json").write_text(json.dumps(PLAN))
    url = PLAN["checks"][0]["url"]
    digest = {"url": url, "claims": ["METR says so"], "numbers": [], "limitations": [], "transcript_excerpts": []}
    out = tmp_path / "d.out"
    out.write_text(json.dumps({"ok": True, **digest}))
    assert w.main(["accept", "digest", "--workdir", str(wd), "--output", str(out), "--url", url]) == 0
    capsys.readouterr()
    assert w.main(["fill", "feature", "--workdir", str(wd)]) == 0
    assert "METR says so" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests and confirm they fail for the right reason**

Run: `python3 -m pytest tests/test_syw_write.py -q`
Expected: the new tests fail with `AttributeError` (`assemble_manifest`, `episode_title`, `main`).

- [ ] **Step 3: Implement**

Add `import argparse`, `import datetime as dt` and `import hashlib` to the imports of `syw_write.py`, then append:

```python
# --- assembly ---------------------------------------------------------------------

_MONTHS = (
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
)  # fmt: skip  (literal, not strftime("%B"), which is LC_TIME-dependent)


def episode_title(plan: dict) -> str:
    """Display text only — the slug is keyed on the date (#128), so the title is
    free to carry the feature's name."""
    d = dt.date.fromisoformat(plan["date"])
    return f"{plan['feature']['title']} - week of {_MONTHS[d.month - 1]} {d.day}, {d.year}"


def _speak(lines: list[dict]) -> list[dict]:
    """What render.py sees: speaker and text only. `basis` and beats stay out of
    the manifest even though _validate_scene tolerates extra keys today — relying
    on that would couple this show to an accident of the renderer."""
    return [{"speaker": ln["speaker"], "text": ln["text"]} for ln in lines]


def assemble_manifest(
    date_iso: str,
    title: str,
    summary: str,
    plan: dict,
    cold_open: list[dict],
    feature_scenes: list[dict],
    briefs: list[dict],
    sign_off: list[dict],
    allow_missing_cover: bool = False,
) -> tuple[dict, dict]:
    """(manifest, beats) for one episode.

    `feature_scenes` is validate_feature's `scenes`; each of `briefs` is
    {"kind": "single"|"casebook", "item": <lead item or None>, "lines", "beats"}.
    Beat `line` indices in the sidecar are relative to each segment's FINAL lines,
    i.e. after the Anthropic reminder is prepended."""
    for which, lines in (("cold_open", cold_open), ("sign_off", sign_off)):
        probs = validate_frame(lines, which)
        if probs:
            die("; ".join(probs))
    if not COVER_IMAGE.is_file() and not allow_missing_cover:
        die(f"cover art missing: {COVER_IMAGE} (a live ship needs the show's own art)")

    feature = plan["feature"]
    segments: list[dict] = [
        {"title": "Cold open", "role": "intro", "source_url": None, "lines": _speak(cold_open)}
    ]
    beats: dict[str, list[dict]] = {}
    for scene in feature_scenes:
        lines = _speak(scene["lines"])
        shift = 0
        if scene["slot"] == "hook" and feature.get("lab") == "anthropic":
            lines = [{"speaker": "explainer", "text": ANTHROPIC_REMINDER}, *lines]
            shift = 1
        hook = scene["slot"] == "hook"
        if scene["beats"]:
            beats[str(len(segments))] = [{**b, "line": b["line"] + shift} for b in scene["beats"]]
        segments.append(
            {
                "title": (feature["title"] if hook else SLOT_TITLES[scene["slot"]])[:120],
                "source_url": feature["url"] if hook else None,
                "lines": lines,
            }
        )
    for brief in briefs:
        casebook = brief["kind"] == "casebook"
        if brief["beats"]:
            beats[str(len(segments))] = brief["beats"]
        segments.append(
            {
                "title": (CASEBOOK_TITLE if casebook else brief["item"]["title"])[:120],
                "source_url": CASEBOOK_URL if casebook else brief["item"]["url"],
                "lines": _speak(brief["lines"]),
            }
        )
    segments.append(
        {"title": "Sign-off", "role": "outro", "source_url": None, "lines": _speak(sign_off)}
    )
    manifest = {
        # Display-only: `date` is what keys the slug and the guid (#128).
        "title": title,
        "summary": summary,
        "date": date_iso,
        # Fallback voice for a plain-text segment; every segment here is a scene.
        "voice": CAST["explainer"],
        "cast": dict(CAST),
        "ship_mode": "web",
        "show_name": SHOW_NAME,
        "r2_manifest_name": R2_MANIFEST_NAME,
        "r2_key_prefix": R2_KEY_PREFIX,
        "slug_prefix": SLUG_PREFIX,
        "description_footer_text": DESCRIPTION_FOOTER,
        "segments": segments,
    }
    if COVER_IMAGE.is_file():
        manifest["cover_image"] = str(COVER_IMAGE)
    return manifest, {"version": 1, "segments": beats}


# --- workdir CLI --------------------------------------------------------------------


def _read(p: Path):
    return json.loads(p.read_text())


def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False))


def digest_path(workdir: Path, url: str) -> Path:
    return workdir / "digests" / f"{hashlib.sha1(url.encode()).hexdigest()[:12]}.json"


def _digests(workdir: Path, urls: list[str]) -> list[dict]:
    return [_read(p) for u in urls if (p := digest_path(workdir, u)).is_file()]


def _find_item(plan: dict, url: str) -> dict:
    pool = [plan["feature"], *plan.get("checks", [])]
    pool += [it for b in plan.get("briefs", []) for it in b["items"]]
    for it in pool:
        if it["url"] == url:
            return it
    die(f"{url} is not an item in this plan")


def _cmd_fill(a) -> int:
    wd = Path(a.workdir)
    plan = _read(wd / "plan.json")
    if a.what == "digest":
        print(fill_digest((PROMPTS_DIR / "digest.md").read_text(), _find_item(plan, a.url)))
    elif a.what == "feature":
        checks = _digests(wd, [c["url"] for c in plan["checks"]])
        print(fill_feature((PROMPTS_DIR / "write_feature.md").read_text(), plan, checks))
    else:
        brief = plan["briefs"][a.index]
        if brief["kind"] == "casebook":
            ds = _digests(wd, [it["url"] for it in brief["items"]])
            print(fill_casebook((PROMPTS_DIR / "write_casebook.md").read_text(), ds))
        else:
            print(fill_brief((PROMPTS_DIR / "write_brief.md").read_text(), brief["items"][0]))
    return 0


def _refused(reason: str) -> int:
    print(f"ACCEPT refused {reason}"[:400])
    return 2


def _cmd_accept(a) -> int:
    wd = Path(a.workdir)
    plan = _read(wd / "plan.json")
    res = classify_output(Path(a.output).read_text(), "", 0)
    if res["outcome"] != "OK":
        return _refused(f"{res['outcome']} {res['detail']}")
    obj = res["obj"]
    terms_p = wd / "writes" / "terms.json"
    seen_terms = set(_read(terms_p)) if terms_p.is_file() else set()
    dropped: list[dict] = []
    if a.what == "digest":
        digest, why = validate_digest(obj, a.url)
        if digest is None:
            return _refused(why)
        _write(digest_path(wd, a.url), digest)
    elif a.what == "feature":
        v = validate_feature(obj, plan, fetch_post_text(plan["feature"]["url"]), seen_terms)
        if not v["ok"]:
            return _refused("; ".join(v["problems"]))
        _write(wd / "writes" / "feature.json", {"scenes": v["scenes"]})
        dropped = v["dropped_beats"]
    elif a.what == "brief":
        brief = plan["briefs"][a.index]
        casebook = brief["kind"] == "casebook"
        text = " ".join(fetch_post_text(it["url"]) for it in brief["items"])
        v = validate_brief(obj, casebook, text, seen_terms)
        if not v["ok"]:
            return _refused("; ".join(v["problems"]))
        _write(
            wd / "writes" / f"brief_{a.index:02d}.json",
            {
                "kind": brief["kind"],
                "item": None if casebook else brief["items"][0],
                "lines": v["lines"],
                "beats": v["beats"],
            },
        )
        dropped = v["dropped_beats"]
    else:
        probs = validate_frame(obj.get("lines"), a.what)
        if probs:
            return _refused("; ".join(probs))
        _write(wd / "writes" / f"{a.what}.json", {"lines": obj["lines"]})
    _write(terms_p, sorted(seen_terms))
    for d in dropped:
        syw_gather.append_dropped({"stage": "beat", **d})
    print(f"ACCEPT ok {a.what} dropped_beats={len(dropped)}")
    return 0


def _cmd_assemble(a) -> int:
    wd = Path(a.workdir)
    plan = _read(wd / "plan.json")
    writes = wd / "writes"
    manifest, beats = assemble_manifest(
        plan["date"],
        episode_title(plan),
        a.summary,
        plan,
        _read(writes / "cold_open.json")["lines"],
        _read(writes / "feature.json")["scenes"],
        [_read(p) for p in sorted(writes.glob("brief_*.json"))],
        _read(writes / "sign_off.json")["lines"],
        allow_missing_cover=a.allow_missing_cover,
    )
    _write(wd / "manifest.json", manifest)
    _write(wd / "beats.json", beats)
    n_beats = sum(len(v) for v in beats["segments"].values())
    print(f"ASSEMBLE ok segments={len(manifest['segments'])} beats={n_beats}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="syw_write.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fill")
    f.add_argument("what", choices=("digest", "feature", "brief"))
    f.add_argument("--workdir", required=True)
    f.add_argument("--url")
    f.add_argument("--index", type=int)
    c = sub.add_parser("accept")
    c.add_argument("what", choices=("digest", "feature", "brief", "cold_open", "sign_off"))
    c.add_argument("--workdir", required=True)
    c.add_argument("--output", required=True)
    c.add_argument("--url")
    c.add_argument("--index", type=int)
    s = sub.add_parser("assemble")
    s.add_argument("--workdir", required=True)
    s.add_argument("--summary", required=True)
    s.add_argument("--allow-missing-cover", action="store_true")
    a = ap.parse_args(argv)
    return {"fill": _cmd_fill, "accept": _cmd_accept, "assemble": _cmd_assemble}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_syw_write.py -q`
Expected: `40 passed` (31 + 9).

- [ ] **Step 5: Run the full gate**

```bash
ruff format skills/show-your-work tests/test_syw_write.py
python3 -m pytest -q
ruff check .
ruff format --check .
```

Expected: `1644 passed`, and ruff clean.

- [ ] **Step 6: Commit**

```bash
git add skills/show-your-work/syw_write.py tests/test_syw_write.py
git commit -m "feat(syw): manifest + beats assembly, workdir CLI, namespace isolation test

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: SKILL.md, the weekly stub, drift tests, CLAUDE.md

**Files:**
- Create: `skills/show-your-work/SKILL.md`, `skills/show-your-work/prompts/weekly.md`
- Create: `tests/test_syw_skill_md.py`
- Modify: `CLAUDE.md` ("What this repo is" paragraph; add one sentence for the fourth show)

**Interfaces:**
- Consumes: every constant named in the drift tests below.
- Produces: the production procedure. SKILL.md is what the scheduled run follows.

- [ ] **Step 1: Write the failing drift tests**

Create `tests/test_syw_skill_md.py`:

```python
"""Drift tests tying skills/show-your-work/SKILL.md to the code it documents.

SKILL.md is the PRODUCTION path — the weekly run follows its "Unattended weekly
run" section — so a table that drifts from the code ships behaviour no test
verified. Tables are cell-parsed and compared for EQUALITY (test_st_skill_md.py)."""

import json
import re
from pathlib import Path

import syw_script_plan as sp
import syw_write as w

SYW_DIR = Path(__file__).resolve().parent.parent / "skills" / "show-your-work"


def _text() -> str:
    return (SYW_DIR / "SKILL.md").read_text()


def _table_after(marker: str) -> list[list[str]]:
    lines = _text().splitlines()
    i = next(i for i, ln in enumerate(lines) if marker in ln) + 1
    while not lines[i].lstrip().startswith("|"):
        i += 1
    rows = []
    while i < len(lines) and lines[i].lstrip().startswith("|"):
        rows.append([c.strip().strip("`").strip() for c in lines[i].strip().strip("|").split("|")])
        i += 1
    return rows[2:]


def _section(heading: str) -> str:
    text = _text()
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start : end if end != -1 else len(text)]


def test_arc_table_matches_the_code():
    assert [r[1] for r in _table_after("### The feature arc")] == list(sp.ARC)


def test_beat_table_matches_the_validator():
    assert [r[0] for r in _table_after("### Visual beats")] == list(w.BEAT_FIELDS)


def test_rotation_table_matches_the_banks():
    rows = _table_after("### Assigned variety")
    got = {r[0]: [k.strip().strip("`") for k in r[2].split(",")] for r in rows}
    assert got == {name: list(bank) for name, bank in sp.ROTATION_BANKS.items()}


def test_manifest_block_matches_the_assembler():
    block = re.search(r"```json\n(\{.*?\n\})\n```", _text(), re.S)
    m = json.loads(block.group(1))
    assert (m["ship_mode"], m["show_name"], m["slug_prefix"]) == ("web", w.SHOW_NAME, w.SLUG_PREFIX)
    assert (m["r2_manifest_name"], m["r2_key_prefix"]) == (w.R2_MANIFEST_NAME, w.R2_KEY_PREFIX)
    assert m["cast"] == w.CAST


def test_the_unattended_procedure_names_every_cli_step():
    proc = _section("## Unattended weekly run")
    for step in (
        "syw_gather.py gather", "syw_script_plan.py plan", "syw_write.py fill",
        "syw_write.py accept", "syw_write.py assemble", "render.py", "syw_gather.py commit",
    ):  # fmt: skip
        assert step in proc, f"the procedure lost {step!r}"
    assert "--dry-run" in proc  # the "never pass --dry-run" line


def test_weekly_prompt_stays_a_stub():
    stub = (SYW_DIR / "prompts" / "weekly.md").read_text()
    assert "Unattended weekly run" in stub
    assert not re.search(r"^\s*\d+\.\s", stub, re.M), "the stub grew numbered procedure steps"
    for cmd in ("syw_gather.py", "syw_write.py", "render.py"):
        assert cmd not in stub
```

- [ ] **Step 2: Run the tests and confirm they fail for the right reason**

Run: `python3 -m pytest tests/test_syw_skill_md.py -q`
Expected: 6 failures with `FileNotFoundError` on `SKILL.md` / `prompts/weekly.md`.

- [ ] **Step 3: Write SKILL.md**

Create `skills/show-your-work/SKILL.md`:

````markdown
---
id: show-your-work
name: show-your-work
description: Use when the user asks to ship the Show Your Work weekly podcast — explains frontier-lab AI alignment research (Anthropic, OpenAI incl. its misalignment reports, Google DeepMind) to newcomers in a two-voice Explainer/Skeptic episode, published to its own public RSS feed via syw_gather / syw_script_plan / syw_write and render.py. Skips the standard production interview because defaults are pre-set.
enabled: true
---

# Show Your Work

A weekly podcast that explains frontier-lab alignment research to people who don't read papers. Each episode is one **feature** (one post, five scenes), up to four short **briefs**, and a sign-off. Two voices: the **explainer** states each claim the way the post states it, always attributed; the **skeptic** asks the newcomer's question, then pushes back.

**The show is written and voiced by Claude, and Anthropic is one of the labs it covers.** Three mechanisms answer that conflict, and none is optional:

- Every cold open discloses it. `syw_write.validate_frame` refuses a cold open without "Claude" and "Anthropic".
- An Anthropic feature gets a fixed reminder line. The assembler prepends `ANTHROPIC_REMINDER`; no writer writes it.
- Every pushback line carries a `basis` that must be the post's own stated limitations or a matched independent check. `validate_feature` refuses anything else.

**RSS-first.** The show ships through `render.py`'s web-only mode on its own feed. The R2 publish **is** the ship. `save-to-spotify` is never invoked.

Design: `docs/superpowers/specs/2026-09-26-show-your-work-design.md`.

## Layout

- `./syw_gather.py`: sources → items; `gather` / `seed` / `commit`. Metadata only.
- `./syw_script_plan.py`: the deterministic plan: feature pick, briefs, casebook, checks, rotations.
- `./syw_write.py`: prompt filling (`fill`), output validation (`accept`), manifest + beats assembly (`assemble`).
- `./prompts/digest.md`: a one-body fact sheet for a check or a casebook incident.
- `./prompts/write_feature.md`: the five-scene feature. One body plus check digests.
- `./prompts/write_brief.md`: one single-post brief. One body.
- `./prompts/write_casebook.md`: the casebook brief. **No body**, digests only.
- `./prompts/weekly.md`: a stub pointing here.

The renderer is the sibling `skills/daily-podcast/render.py`, used unchanged.

## Episode shape

Cold open (with disclosure) → feature (5 chapters) → 0–4 briefs (research briefs, then at most one casebook) → sign-off (with the assigned button). About 13–18 minutes, 7–11 chapters.

### The feature arc

| # | Slot | Job |
| --- | --- | --- |
| 1 | `hook` | The assigned opening move; why a newcomer should care. |
| 2 | `method` | What the researchers did, with every term of art defined on first use. |
| 3 | `finding` | What they found, attributed to them. |
| 4 | `pushback` | The skeptic's objections, each traced to a basis. |
| 5 | `stakes` | Why it matters, and what is still open. |

### Assigned variety

Seeded by `week_index(date)`. The lengths are pairwise coprime except `fourth_wall` × `first_speaker`, which never share a scene.

| Rotation | Length | Values |
| --- | --- | --- |
| `intro_mode` | 3 | `question`, `moment`, `ledger` |
| `fourth_wall` | 4 | `maker`, `conflict`, `method`, `standard` |
| `opening_move` | 5 | `excerpt`, `number`, `scenario`, `question`, `claim` |
| `first_speaker` | 2 | `explainer`, `skeptic` |
| `button` | 7 | `grader`, `reward`, `transcript`, `spec`, `eval`, `sandbox`, `homework` |

The daily show's example lines (`FALLBACK_BUTTONS`, `FALLBACK_FOURTH_WALLS`) are **burned** here: a frame containing one is refused.

### Visual beats

Beats are written now and drawn by the (separate) video stage. They live in `<workdir>/beats.json`, never in the manifest. **A beat that fails its guard is dropped and logged; its line still ships.**

| Type | Guard |
| --- | --- |
| `number` | the value appears in the post text |
| `chart` | every y value appears in the post text, never read off a figure |
| `quote` | ≤25 words, verbatim (typography-blind) |
| `transcript` | verbatim, ≤600 chars, role cot / tool_call / tool_result / model / user |
| `diagram` | ≤6 nodes; edges join known ids |
| `term` | defined once per episode |

## Editorial rules

1. The explainer attributes every claim; a finding is never stated as settled fact.
2. Every pushback traces to the post's stated limitations or a matched independent source (enforced: `basis`).
3. Never call a lab "better" or "worse" at safety.
4. Define every term of art on first use.
5. A misalignment report is told as what the lab says happened. Add no drama.
6. Quotes and transcript excerpts are verbatim (enforced).
7. Charts use only numbers stated in the post text (enforced).
8. Disclose in every cold open; a fixed reminder opens any Anthropic feature (enforced).
9. Never point the listener at a link or the show notes.

## Manifest

`syw_write.assemble_manifest` writes these keys; the collision test in `tests/test_syw_write.py` keeps them clear of every other feed.

```json
{
  "ship_mode": "web",
  "show_name": "Show Your Work",
  "r2_manifest_name": "manifest-show-your-work.json",
  "r2_key_prefix": "show-your-work/",
  "slug_prefix": "syw-week-of",
  "cast": {"explainer": "Ryan", "skeptic": "Chelsie"}
}
```

## Unattended weekly run

**This section is the canonical procedure for shipping an episode with no human in the loop.** It is the single home: a scheduler invokes this skill and follows this section, never carries its own copy ([`prompts/weekly.md`](prompts/weekly.md) is a stub pointing here, and a drift test keeps it one). Be decisive, don't ask clarifying questions, and if you cannot proceed, print one `FAILED <reason>` line and stop.

Let `D` = today (`YYYY-MM-DD`), `S` = this skill's directory (`${CLAUDE_PLUGIN_ROOT}/skills/show-your-work/` when set; otherwise the path this SKILL.md was loaded from), and `W` = `$TMPDIR/daily-podcast-show-your-work-<D>` (explicit, so `render.py` never auto-deletes it; the `daily-podcast-` prefix lets `--prune-workdirs` sweep it later).

1. **Gather.** `python3 S/syw_gather.py gather --date D --out W/candidates.json`. On `GATHER FAILED`, print `FAILED <that reason>` and stop.
2. **Plan.** `python3 S/syw_script_plan.py plan --date D --candidates W/candidates.json --out W/plan.json`. On `PLAN skip <reason>`, print `SKIPPED <reason>` and stop — **no filler episodes**.
3. **Digest the checks and the casebook.** For each URL in `plan.checks` and each item in a `casebook` brief: `python3 S/syw_write.py fill digest --workdir W --url <url>` → run that prompt in its **own subagent context** (one body per context, nothing else in it) → save the subagent's final message to `W/out/digest-<n>.txt` → `python3 S/syw_write.py accept digest --workdir W --output W/out/digest-<n>.txt --url <url>`. A refused digest is dropped: a missing check narrows the skeptic's basis to `post-limitations`; a missing incident leaves the casebook.
4. **Write the feature.** `python3 S/syw_write.py fill feature --workdir W` → one subagent context → save → `python3 S/syw_write.py accept feature --workdir W --output <file>`. On `ACCEPT refused`, retry once with a fresh subagent. If it is refused again, re-plan without it: `python3 S/syw_script_plan.py plan --date D --candidates W/candidates.json --out W/plan.json --exclude <feature url>`, then redo steps 3–4 on the new plan. A second failed feature is `FAILED feature refused twice`. A run where every writer came back `AUTH` is a credential failure, not a thin week — report it as such.
5. **Write the briefs.** For each index `i` of `plan.briefs`: `python3 S/syw_write.py fill brief --workdir W --index i` → one subagent context → save → `python3 S/syw_write.py accept brief --workdir W --output <file> --index i`. A refused brief is dropped; the episode still ships.
6. **Write the frames** in the main context, from the plan's titles and `plan.rotation`:
   - the cold open (400–800 chars): the `intro_mode`, plus ONE dry sentence in the `fourth_wall` angle that says the show is written and voiced by Claude, a model made by Anthropic, and that Anthropic is one of the labs covered;
   - the sign-off (250–500 chars): a thanks and ONE dry joke in the `button` angle, worded fresh.

   Save each as `{"ok": true, "lines": [{"speaker": ..., "text": ...}]}` and run `python3 S/syw_write.py accept cold_open|sign_off --workdir W --output <file>`. Rewrite until accepted.
7. **Assemble.** `python3 S/syw_write.py assemble --workdir W --summary "<one sentence on this week's feature>"` → `W/manifest.json` and `W/beats.json`.
8. **Render in the background.** `python3 <root>/skills/daily-podcast/render.py --manifest W/manifest.json --workdir W > W/render.log 2>&1` with `run_in_background`, and monitor `W/render.log` — the 10-minute foreground Bash cap kills a long render. Never pass `--dry-run` (this is a real episode) and never pass `--skip-preflight`.
9. **Commit.** `python3 S/syw_gather.py commit --plan W/plan.json --render-output W/render.log`. It marks the stories covered only if the render's final JSON says `web-ready` and `published`; `COMMIT refused` means the episode did not ship — report `FAILED <reason>`.
10. **Report once.** `SHIPPED <mp3_url> - <title> - <n> chapters - <dur>s - r2=ok` with every value from the renderer's final JSON; `SKIPPED <reason>`; or `FAILED <reason>`.

## Setup

1. `mkdir -p ~/.config/show-your-work && echo '{}' > ~/.config/show-your-work/config.json` — every key has a default (`syw_gather.DEFAULT_CONFIG`); override only what you need.
2. `python3 skills/show-your-work/syw_gather.py seed --date <today>` — marks everything currently published as seen so episode one covers only what arrives next.
3. R2 credentials as for every show (`render.py --selftest`).

## State (`~/.config/show-your-work/`)

| File | Written by | Contract |
| --- | --- | --- |
| `config.json` | human | `{}` is valid; unknown keys refuse |
| `seen.json` | `seed`, `commit` | URL → `{date, role}`; only after a verified ship (or seed); corrupt refuses |
| `observed.json` | `gather`, `seed` | URL → first date parsed; month-precision items age from it |
| `features.jsonl` | `commit` | one row per shipped feature; drives the lab and kind penalties |
| `dropped.jsonl` | gather, accept | failed adapters and dropped beats; observability only |

`render.py` also writes the shared `~/.config/daily-podcast/covered.json` for every chapter's `source_url`, so the daily show will skip a lab post this show already covered. Accepted; this show reads only `seen.json`.
````

- [ ] **Step 4: Write the weekly stub**

Create `skills/show-your-work/prompts/weekly.md`:

````markdown
# Show Your Work Weekly Run — this is a stub

> **This prompt does not carry the procedure, and never will.** The unattended
> weekly run lives in one place: the **"Unattended weekly run"** section of
> [`../SKILL.md`](../SKILL.md).

## Why this file is a stub

The daily show learned this the hard way: three copies of its run procedure
drifted, and production silently ran a months-old fork. So the procedure has
exactly one home, this file points at it, and `tests/test_syw_skill_md.py` goes
red if the procedure ever grows back here.

## If you are a scheduler

Invoke the `show-your-work` skill and follow its **"Unattended weekly run"**
section. A scheduler's prompt should be a trigger, not a specification:

```markdown
You are an unattended invocation. Invoke the `show-your-work` skill via the
Skill tool, then follow its "Unattended weekly run" section exactly, end to end.
Report its single SHIPPED/SKIPPED/FAILED line to stdout and exit.
```
````

- [ ] **Step 5: Update CLAUDE.md**

In `CLAUDE.md`'s first paragraph under `## What this repo is`, change "ships three shows and one bench" to "ships four shows and one bench". Then insert this sentence immediately before the `tts-eval` sentence:

```markdown
`show-your-work`, at [skills/show-your-work/](skills/show-your-work/), explains frontier-lab alignment research (Anthropic, OpenAI including its misalignment casebook, Google DeepMind) to newcomers in a weekly two-voice Explainer/Skeptic episode, shipped through the same `render.py` web-only mode with `render.py` unchanged; it scrapes sources that have no feed (so a parser that finds zero items is a failure, not a quiet week), refuses any skeptic objection without a `basis`, and writes visual beats to a `beats.json` sidecar for a later video stage; its contracts live in [skills/show-your-work/SKILL.md](skills/show-your-work/SKILL.md), pinned by [tests/test_syw_skill_md.py](tests/test_syw_skill_md.py), and its design is [docs/superpowers/specs/2026-09-26-show-your-work-design.md](docs/superpowers/specs/2026-09-26-show-your-work-design.md).
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_syw_skill_md.py -q`
Expected: `6 passed`.

- [ ] **Step 7: Run the full gate**

```bash
ruff format tests/test_syw_skill_md.py
python3 -m pytest -q
ruff check .
ruff format --check .
```

Expected: `1650 passed`, and ruff clean.

- [ ] **Step 8: Commit**

```bash
git add skills/show-your-work/SKILL.md skills/show-your-work/prompts/weekly.md tests/test_syw_skill_md.py CLAUDE.md
git commit -m "docs(syw): SKILL.md with the unattended weekly procedure, stub, drift tests

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Seed the host and rehearse a first episode (dry run, human listening gate)

This task is operational, with no new code. It exercises every step of the procedure against live sources, with `--dry-run` on the render so nothing publishes. **Stop at Step 6 and wait for Cory.**

**Files:**
- Possibly modify: `skills/show-your-work/syw_write.py` (band constants, `CAST`) based on Steps 5–6. Any change reruns the Task 7/8 tests.

- [ ] **Step 1: Configure and seed (writes real user state; this is the intended first run)**

```bash
mkdir -p ~/.config/show-your-work && [ -f ~/.config/show-your-work/config.json ] || echo '{}' > ~/.config/show-your-work/config.json
python3 skills/show-your-work/syw_gather.py seed --date 2026-09-01
```

Expected: `SEED ok marked=<n>`, with `n` around 150. Seeding as of 2026-09-01 leaves nothing new, so for the rehearsal **move `seen.json` aside** instead: `mv ~/.config/show-your-work/seen.json ~/.config/show-your-work/seen.rehearsal.json`. The rehearsal pool is then the real last-21-days pool. Restore it in Step 7.

- [ ] **Step 2: Gather and plan into an absolute workdir**

```bash
W="$TMPDIR/daily-podcast-show-your-work-rehearsal"; mkdir -p "$W"
python3 skills/show-your-work/syw_gather.py gather --date "$(date +%F)" --out "$W/candidates.json"
python3 skills/show-your-work/syw_script_plan.py plan --date "$(date +%F)" --candidates "$W/candidates.json" --out "$W/plan.json"
```

Expected: `GATHER ok lead=… errors=0` and `PLAN ok feature=… briefs=… checks=…`. Any `errors>0` means a live source changed since 2026-09-26. Capture a new fixture for it, fix the parser behind a failing test, and then continue.

- [ ] **Step 3: Run the digest, feature, brief and frame steps exactly as SKILL.md's *Unattended weekly run* steps 3–6 describe**

Use subagents (the Agent tool), one per context. Record every `ACCEPT` line.

- [ ] **Step 4: Assemble and dry-run render (background)**

```bash
python3 skills/show-your-work/syw_write.py assemble --workdir "$W" --summary "Rehearsal." --allow-missing-cover
python3 skills/daily-podcast/render.py --manifest "$W/manifest.json" --workdir "$W" --dry-run > "$W/render.log" 2>&1
```

Run the render with `run_in_background` and wait for it to exit. Expected: the render log ends in a JSON object with `"status": "dry-run"`.

Relative workdirs break render's concat list, so `$W` must be absolute. A failed manual dry run writes incident files into the real `~/.config/daily-podcast/incidents/new/`; delete those after.

- [ ] **Step 5: Measure**

From `$W/timeline.json` and the render log, record:
- each chapter's duration against the §5 targets (feature 10–12 min total, briefs 60–90 s each)
- the episode total
- the dropped-beat count from `~/.config/show-your-work/dropped.jsonl`

If the feature is more than 15% off its target, adjust `FEATURE_SCENE_BAND` proportionally. Do the same for briefs.

- [ ] **Step 6: HUMAN GATE — send Cory the episode**

Send `$W/episode.mp3` (SendUserFile) with the measurements. Ask two things:
1. Do the two presets (`Ryan` explainer, `Chelsie` skeptic) work, or should they swap or change?
2. Is the pacing right?

**Wait for the answer.** Apply any preset or band change to `syw_write.CAST` / `*_BAND`, rerun `python3 -m pytest tests/test_syw_write.py tests/test_syw_skill_md.py -q` (the SKILL.md manifest block must change with `CAST`), and commit:

```bash
git commit -am "chore(syw): tune bands and cast after the first rehearsal

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 7: Restore the real seed**

`mv ~/.config/show-your-work/seen.rehearsal.json ~/.config/show-your-work/seen.json`, then rerun `seed --date <today>`. That way the first live episode covers only posts published after launch, not the rehearsal's pool. If Cory would rather episode one feature the rehearsal's pick, skip the reseed and say so in the Task 11 summary.

---

### Task 11: Cover art, sandbox-namespace proof, first live episode

Outward-facing: this publishes to a public feed. **Get Cory's explicit go-ahead in chat before Step 4.**

**Files:**
- Create: `skills/show-your-work/refs/cover.jpg`
- Test: `tests/test_syw_write.py` (append one test)

- [ ] **Step 1: Make the cover and pin it**

Produce a square 3000×3000 JPEG at `skills/show-your-work/refs/cover.jpg`. It is original art: no lab logo or brand mark, and "SHOW YOUR WORK" legible at 200 px. Send it to Cory (SendUserFile) and wait for approval before committing. Then append this test:

```python
def test_the_cover_is_square_and_large_enough():
    from PIL import Image

    with Image.open(w.COVER_IMAGE) as im:
        assert im.size[0] == im.size[1] >= 1400
```

Run: `python3 -m pytest tests/test_syw_write.py -q`
Expected: `41 passed`.

Commit:

```bash
git add skills/show-your-work/refs/cover.jpg tests/test_syw_write.py
git commit -m "feat(syw): show cover art

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 2: Record the other feeds' manifests before publishing**

```bash
for m in manifest.json manifest-frontier-commits.json manifest-surface-tension.json; do curl -s "https://clodcast.cortech.online/$m" | shasum -a 256 | sed "s|-|$m|"; done
```

Save the output. Step 5 compares against it, which follows the 2026-09-10 sandbox check.

- [ ] **Step 3: Run the full procedure through `assemble`**

Follow SKILL.md *Unattended weekly run* steps 1–7 **without** `--allow-missing-cover`, against `W="$TMPDIR/daily-podcast-show-your-work-$(date +%F)"`.

- [ ] **Step 4: HUMAN GATE, then render for real**

Tell Cory the feature, briefs and title from `$W/plan.json` and `$W/manifest.json`, and ask for a go-ahead to publish. **Only on a clear yes**, run step 8 of the procedure: the real render in the background. Then run step 9 (`commit`).

Expected:
- the render log's final JSON has `"status": "web-ready"` and `"r2_status": "published"`
- `COMMIT ok urls=<n>`

- [ ] **Step 5: Verify the ship and the isolation**

```bash
curl -s -o /dev/null -w '%{http_code}\n' "<mp3_url from the render JSON>"
curl -s https://clodcast.cortech.online/manifest-show-your-work.json | python3 -m json.tool | head -30
for m in manifest.json manifest-frontier-commits.json manifest-surface-tension.json; do curl -s "https://clodcast.cortech.online/$m" | shasum -a 256 | sed "s|-|$m|"; done
```

Expected:
- the mp3 returns `200`
- the new manifest has one entry whose `slug` starts with `syw-week-of-`
- all three other hashes are **identical** to Step 2's

A drift in the daily manifest could be an unrelated daily run in the meantime. Check its newest entry's date before calling it a collision.

- [ ] **Step 6: Report**

Send Cory the SHIPPED line, the mp3 URL and the hash comparison. Note that cortech.online#256 must land before the feed has a public page.

---

### Task 12: The weekly routine

Persistent configuration: **ask Cory before creating it.**

- [ ] **Step 1: Pick the day**

Load `mcp__scheduled-tasks__list_scheduled_tasks` (via ToolSearch) and list the existing Frontier Commits, Surface Tension and daily schedules. Pick a day and hour when none of them runs, so no two runs share the Mac's TTS. Propose it to Cory and wait for a yes.

- [ ] **Step 2: Create the routine**

On a yes, create the scheduled task with the trigger prompt from `prompts/weekly.md` (the fenced block), and nothing else.

- [ ] **Step 3: Record it**

Save a memory under `/Users/cory/.claude/projects/-Users-cory-clodcast/memory/` naming the routine, its schedule, and that `CLAUDE_PLUGIN_ROOT` is unset under scheduled tasks (SKILL.md's path fallback covers it). Add its line to `MEMORY.md`.

---

## After this plan

- **Video (spec §4.7, phase 4):** a separate plan. Its first step is static mock frames per beat type, sent to Cory for approval. `beats.json` from Task 8 is its input.
- **Open a PR** for this branch after Task 9 at the latest, so the code lands through the required-checks gate. Tasks 10–12 are operational and do not block the PR. Use `/shipofclaudius:ship`.
- **Follow-up, flagged in this session:** `tests/test_sandbox_fixture.py` guards the sandbox against the daily show and Frontier Commits but not Surface Tension.
