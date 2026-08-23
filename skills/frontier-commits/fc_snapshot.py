"""Daily org snapshot collector for the frontier-commits skill.

Pure Python, no LLM, no Claude credential — designed to run under launchd with
only the gh CLI. Star history no longer exists upstream (the stargazers
timestamps endpoint 404s), so these daily snapshots ARE the show's and the
/labs/ page's only source of velocity. Losing a day degrades trends; it never
breaks a run — fc_stories diffs against the newest snapshot available.
"""

from __future__ import annotations

import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

import fc_common


class SweepFailed(RuntimeError):
    pass


def repo_record(raw: dict) -> dict:
    return {
        "stars": raw.get("stargazers_count", 0),
        "forks": raw.get("forks_count", 0),
        "open_issues": raw.get("open_issues_count", 0),
        "created_at": raw.get("created_at", ""),
        "pushed_at": raw.get("pushed_at", ""),
        "archived": bool(raw.get("archived", False)),
        "fork": bool(raw.get("fork", False)),
        "topics": raw.get("topics") or [],
        "description": raw.get("description") or "",
        "language": raw.get("language") or "",
    }


def matches_ai(record: dict, config: dict, org: str) -> bool:
    name = record.get("name", "")
    if name in config.get("denylist", {}).get(org, []):
        return False
    if name in config.get("allowlist", {}).get(org, []):
        return True
    topics = {t.lower() for t in record.get("topics", [])}
    if topics & {t.lower() for t in config["ai_topics"]}:
        return True
    desc = record.get("description", "")
    return any(re.search(p, desc, re.IGNORECASE) for p in config["ai_description_patterns"])


def filter_repos(records: list[dict], org_cfg: dict, config: dict) -> list[dict]:
    if org_cfg.get("filter", "none") == "none":
        # denylist still applies even without the AI filter
        deny = set(config.get("denylist", {}).get(org_cfg["name"], []))
        return [r for r in records if r.get("name") not in deny]
    return [r for r in records if matches_ai(r, config, org_cfg["name"])]


def _parse_iso(ts: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def repos_needing_release_check(
    repos: dict, now: dt.datetime, hours: int = 48, cap: int = 30
) -> list[str]:
    cutoff = now - dt.timedelta(hours=hours)
    names = sorted(
        n for n, r in repos.items() if (ts := _parse_iso(r.get("pushed_at", ""))) and ts >= cutoff
    )
    if len(names) > cap:
        fc_common.log(f"warn: release check capped at {cap} of {len(names)} recently-pushed repos")
        names = names[:cap]
    return names


def fetch_releases(
    org: str, repo_names: list[str], window_days: int, now: dt.datetime, runner=subprocess.run
) -> list[dict]:
    cutoff = now - dt.timedelta(days=window_days)
    out: list[dict] = []
    for name in repo_names:
        try:
            rels = fc_common.run_gh(
                ["api", f"repos/{org}/{name}/releases?per_page=5"], runner=runner
            )
        except fc_common.GhError as e:
            fc_common.log(f"warn: releases for {org}/{name} skipped: {e}")
            continue
        for rel in rels if isinstance(rels, list) else []:
            ts = _parse_iso(rel.get("published_at") or "")
            if ts and ts >= cutoff:
                out.append(
                    {
                        "repo": name,
                        "tag": rel.get("tag_name", ""),
                        "published_at": rel.get("published_at", ""),
                    }
                )
    return out


def sweep_org(
    org_cfg: dict, config: dict, runner=subprocess.run, now: dt.datetime | None = None
) -> dict:
    if now is None:
        now = dt.datetime.now(dt.timezone.utc)
    raw = fc_common.gh_paginate(f"orgs/{org_cfg['name']}/repos", runner=runner)
    records = []
    for item in raw:
        rec = repo_record(item)
        rec["name"] = item.get("name", "")
        records.append(rec)
    kept = filter_repos(records, org_cfg, config)
    repos = {r.pop("name"): r for r in kept}
    releases = fetch_releases(
        org_cfg["name"],
        repos_needing_release_check(repos, now, cap=config["releases_repo_cap_per_org"]),
        config["lookback_days"],
        now,
        runner=runner,
    )
    return {
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "repos": repos,
        "releases": releases,
    }


def build_snapshot(config: dict, date_iso: str, runner=subprocess.run) -> dict:
    orgs: dict = {}
    errors: dict = {}
    for org_cfg in config["orgs"]:
        try:
            orgs[org_cfg["name"]] = sweep_org(org_cfg, config, runner=runner)
        except fc_common.GhError as e:
            errors[org_cfg["name"]] = str(e)
            fc_common.log(f"warn: org {org_cfg['name']} failed, continuing: {e}")
    if not orgs:
        raise SweepFailed(f"every org failed: {errors}")
    return {"schema_version": 1, "date": date_iso, "orgs": orgs, "errors": errors}


def write_snapshot(snap: dict) -> Path:
    path = fc_common.snapshot_dir() / f"{snap['date']}.json"
    fc_common.atomic_write_json(path, snap)
    return path


def prune_snapshots(retention_days: int, today: dt.date) -> list[str]:
    """Only files named YYYY-MM-DD.json are candidates; anything else is kept
    (same no-data-loss posture as covered.json pruning)."""
    removed = []
    d = fc_common.snapshot_dir()
    if not d.is_dir():
        return removed
    cutoff = today - dt.timedelta(days=retention_days)
    for p in sorted(d.iterdir()):
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.json", p.name)
        if not m:
            continue
        try:
            when = dt.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if when < cutoff:
            try:
                p.unlink()
                removed.append(p.name)
            except OSError as e:
                fc_common.log(f"warn: could not prune {p.name}: {e}")
    return removed


if __name__ == "__main__":
    sys.exit("CLI arrives with labs.json publishing — import the functions instead")
