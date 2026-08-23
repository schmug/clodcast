"""Shared paths, config, and helpers for the frontier-commits skill.

Sibling modules (fc_snapshot, fc_stories, fc_script_plan) import this module and
reach every path through the helper *functions* below, never by importing the
constants — so tests redirect the whole skill by monkeypatching
fc_common.CONFIG_DIR and DAILY_PODCAST_CONFIG_DIR (tests/conftest.py patches
both for every test; the second is the secrets fallback tier below).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, NoReturn

CONFIG_DIR = Path.home() / ".config" / "frontier-commits"
# secrets.json has ONE durable home for this host: the daily-podcast dir (0600,
# already populated with R2 + deploy-hook keys). We fall back to it rather than
# duplicating credentials; a frontier-commits secrets.json wins if it ever exists.
DAILY_PODCAST_CONFIG_DIR = Path.home() / ".config" / "daily-podcast"

SNAPSHOT_RE = r"\d{4}-\d{2}-\d{2}\.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "show_name": "Frontier Commits",
    "host_name": "Cory",
    "orgs": [
        {"name": "anthropics", "filter": "none"},
        {"name": "openai", "filter": "none"},
        {"name": "xai-org", "filter": "none"},
        {"name": "google", "filter": "ai"},
        {"name": "google-deepmind", "filter": "none"},
    ],
    "ai_topics": [
        "ai",
        "artificial-intelligence",
        "machine-learning",
        "deep-learning",
        "llm",
        "language-model",
        "genai",
        "generative-ai",
        "agent",
        "agents",
        "agentic-ai",
        "mcp",
        "model-context-protocol",
        "gemini",
        "gemma",
        "neural-network",
        "reinforcement-learning",
        "nlp",
        "transformer",
    ],
    "ai_description_patterns": [
        r"\bAI\b",
        r"\bML\b",
        r"machine[ -]learning",
        r"deep[ -]learning",
        r"language model",
        r"\bLLM\b",
        r"\bagent",
        r"neural",
        r"transformer",
        r"\bGemini\b",
        r"\bGemma\b",
        r"diffusion",
        r"embedding",
        r"\bRAG\b",
    ],
    "allowlist": {"google": ["langextract", "adk-python", "sam", "artemis", "mantis"]},
    "denylist": {},
    "lookback_days": 7,
    "min_stories_per_episode": 2,
    "target_stories_per_episode": 6,
    "per_org_story_cap": 3,
    "release_min_stars": 500,
    "stale_min_stars": 1000,
    "stale_stages_days": [180, 365],
    "surge_min_delta_7d": 500,
    "surge_min_ratio": 0.20,
    "snapshot_retention_days": 400,
    "releases_repo_cap_per_org": 30,
    "r2_bucket": None,
    "r2_public_base_url": None,
    "labs_manifest_name": "labs.json",
}

VALID_ORG_FILTERS = ("none", "ai")


def config_path() -> Path:
    return CONFIG_DIR / "config.json"


def snapshot_dir() -> Path:
    return CONFIG_DIR / "snapshots"


def reported_path() -> Path:
    return CONFIG_DIR / "reported.json"


def labs_hash_path() -> Path:
    return CONFIG_DIR / "labs_json.sha256"


def secrets_path() -> Path:
    return CONFIG_DIR / "secrets.json"


def log(msg: str) -> None:
    print(msg, flush=True)


def die(msg: str, code: int = 1) -> NoReturn:
    log(f"error: {msg}")
    sys.exit(code)


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        die(f"missing {path} — see SKILL.md 'Setup' for the schema")
    try:
        file_cfg = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        die(f"unreadable {path}: {e}")
    if not isinstance(file_cfg, dict):
        die(f"{path} must contain a JSON object")
    cfg = {**DEFAULT_CONFIG, **file_cfg}
    # allowlist/denylist merge PER ORG, not wholesale: a file entry overrides only
    # that org's list, defaults for unmentioned orgs survive. A wholesale
    # replacement would silently drop the default google allowlist — the only
    # mechanism rescuing non-AI-named google repos past the org's "ai" filter.
    # A null file value degrades to {} rather than TypeError.
    for k in ("allowlist", "denylist"):
        cfg[k] = {**DEFAULT_CONFIG[k], **(file_cfg.get(k) or {})}
    if not isinstance(cfg["orgs"], list):
        die(f"{path}: \"orgs\" must be a list — see SKILL.md 'Setup' for the schema")
    for org in cfg["orgs"]:
        if not isinstance(org, dict) or "name" not in org:
            die(f'each org needs a "name": {org!r}')
        # Org names land verbatim in gh api paths (gh_paginate), where '/', '?',
        # '&', or '..' would rewrite the request path — refuse anything outside
        # GitHub's own org-name alphabet.
        if not isinstance(org["name"], str) or not re.fullmatch(r"[A-Za-z0-9-]+", org["name"]):
            die(f"org name {org['name']!r} must match [A-Za-z0-9-]+")
        if org.get("filter", "none") not in VALID_ORG_FILTERS:
            die(f"org {org['name']!r} filter must be one of {VALID_ORG_FILTERS}")
    return cfg


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(obj, indent=2, sort_keys=True))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class GhError(RuntimeError):
    pass


def _gh_env() -> dict[str, str]:
    """launchd doesn't inherit the shell env, and gh's keyring can be locked
    pre-login — a GH_TOKEN in secrets.json (either dir) is the deterministic
    fallback. Env var still wins if already set."""
    env = dict(os.environ)
    if "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env:
        for p in (secrets_path(), DAILY_PODCAST_CONFIG_DIR / "secrets.json"):
            try:
                token = json.loads(p.read_text()).get("GH_TOKEN")
            except (OSError, json.JSONDecodeError, AttributeError):
                token = None
            if token:
                env["GH_TOKEN"] = token
                break
    return env


def run_gh(args: list[str], runner=subprocess.run) -> Any:
    cmd = ["gh", *args]
    proc = runner(cmd, capture_output=True, text=True, env=_gh_env(), timeout=120)
    if proc.returncode != 0:
        raise GhError(
            f"gh {' '.join(args[:2])} exited {proc.returncode}: "
            f"{(proc.stderr or proc.stdout or '').strip()[:300]}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise GhError(f"gh {' '.join(args[:2])}: non-JSON output ({e})") from e


def gh_paginate(
    path: str, runner=subprocess.run, per_page: int = 100, max_pages: int = 40
) -> list[dict[str, Any]]:
    """Manual pagination: deterministic, injectable, no dependence on gh flags."""
    sep = "&" if "?" in path else "?"
    out: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        batch = run_gh(["api", f"{path}{sep}per_page={per_page}&page={page}"], runner=runner)
        if not isinstance(batch, list):
            raise GhError(f"gh api {path}: expected a list page, got {type(batch).__name__}")
        out.extend(batch)
        if len(batch) < per_page:
            break
    return out


def load_r2_secrets() -> dict[str, str]:
    """Env first, then frontier secrets.json, then the daily-podcast secrets.json
    (same key names render.py uses)."""
    keys = ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ACCOUNT_ID", "PAGES_DEPLOY_HOOK_URL")
    out = {k: os.environ[k] for k in keys if os.environ.get(k)}
    for p in (secrets_path(), DAILY_PODCAST_CONFIG_DIR / "secrets.json"):
        if len(out) == len(keys):
            break
        try:
            data = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            for k in keys:
                if k not in out and data.get(k):
                    out[k] = data[k]
    return out
