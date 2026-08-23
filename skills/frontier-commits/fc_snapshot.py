"""Daily org snapshot collector for the frontier-commits skill.

Pure Python, no LLM, no Claude credential — designed to run under launchd with
only the gh CLI (plus boto3, lazily, when R2 publishing is configured). Star
history no longer exists upstream (the stargazers timestamps endpoint 404s), so
these daily snapshots ARE the show's and the /labs/ page's only source of
velocity. Losing a day degrades trends; it never breaks a run — fc_stories
diffs against the newest snapshot available.

The CLI (`python3 fc_snapshot.py [--date YYYY-MM-DD] [--dry-run]`) sweeps,
writes the snapshot, prunes retention, and hash-gate-publishes labs.json to R2.
Its final stdout line is the scheduler contract:
`SNAPSHOT ok date=… orgs=… repos=… releases=… labs=…` or `SNAPSHOT FAILED …`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

import fc_common
import fc_stories


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
    names = [
        n for n, r in repos.items() if (ts := _parse_iso(r.get("pushed_at", ""))) and ts >= cutoff
    ]
    # Most-recently-pushed first (name as deterministic tiebreak): the cap must
    # drop the least active repos, never the hottest one because its name sorts
    # late alphabetically.
    names.sort(key=lambda n: (_parse_iso(repos[n]["pushed_at"]), n), reverse=True)
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
        except (fc_common.GhError, subprocess.SubprocessError, OSError) as e:
            # Not just GhError: run_gh(timeout=120) raises TimeoutExpired, and a
            # missing gh binary raises FileNotFoundError — one repo's failure
            # must never sink the rest of the release sweep.
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
        # The single resolved clock — an injected `now` must be the timestamp
        # recorded, or replayed/backfilled sweeps lie about when they ran.
        "fetched_at": now.isoformat(timespec="seconds"),
        "repos": repos,
        "releases": releases,
    }


def build_snapshot(config: dict, date_iso: str, runner=subprocess.run) -> dict:
    orgs: dict = {}
    errors: dict = {}
    for org_cfg in config["orgs"]:
        try:
            orgs[org_cfg["name"]] = sweep_org(org_cfg, config, runner=runner)
        except (fc_common.GhError, subprocess.SubprocessError, OSError) as e:
            # Broad on purpose: run_gh(timeout=120) raises TimeoutExpired and a
            # missing gh binary (the realistic launchd failure) raises
            # FileNotFoundError — either escaping here would lose every healthy
            # org's data for the day.
            errors[org_cfg["name"]] = str(e)
            fc_common.log(f"warn: org {org_cfg['name']} failed, continuing: {e}")
    if not orgs:
        raise SweepFailed(f"every org failed: {errors}")
    return {"schema_version": 1, "date": date_iso, "orgs": orgs, "errors": errors}


def write_snapshot(snap: dict) -> Path:
    name = f"{snap.get('date') or ''}.json"
    # The date lands verbatim in a filename, and the shared SNAPSHOT_RE is the
    # same predicate prune_snapshots matches with — so a snapshot can neither
    # escape snapshot_dir() (date='../escaped') nor be written under a
    # non-canonical name ('2026-8-5', ISO datetimes) that retention never sees.
    if not re.fullmatch(fc_common.SNAPSHOT_RE, name):
        raise ValueError(f"snapshot date must be YYYY-MM-DD, got {snap.get('date')!r}")
    path = fc_common.snapshot_dir() / name
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
        m = re.fullmatch(fc_common.SNAPSHOT_RE, p.name)
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


# --- labs.json aggregation ---------------------------------------------------

DISPLAY_NAMES = {
    "anthropics": "Anthropic",
    "openai": "OpenAI",
    "xai-org": "xAI",
    "google": "Google",
    "google-deepmind": "Google DeepMind",
}

LABS_TOP_N = 10
NEW_REPO_WINDOW_DAYS = 30
# A movers baseline younger than this yields noise deltas; ">= 6 days older"
# is inclusive at the boundary so a weekly cadence (exactly 7, sometimes 6
# days apart around DST/cron drift) always finds its baseline.
MOVERS_MIN_BASELINE_AGE_DAYS = 6
ARCHIVED_WINDOW_DAYS = 30
STALE_WATCH_MIN_DAYS = 120
ACTIVE_WINDOW_DAYS = 30


def _days_since(run: dt.date, ts: str) -> int | None:
    d = _parse_iso(ts)
    return (run - d.date()).days if d else None


def _date_str(ts: str) -> str:
    d = _parse_iso(ts)
    return d.date().isoformat() if d else ""


def _views_for(date_iso: str | None) -> dict | None:
    if not date_iso:
        return None
    snap = fc_stories.load_snapshot(date_iso)
    return fc_stories.org_views(snap) if snap else None


def _lab_entry(
    org: str,
    view: dict,
    run: dt.date,
    config: dict,
    base_view: dict | None,
    base_span: int,
    arch_view: dict | None,
) -> dict:
    """One lab's aggregates. `base_view`/`arch_view` are None when that org is
    UNKNOWN in the comparison snapshot (absent or empty) — the comparisons then
    skip rather than treat the org as an empty set, mirroring fc_stories'
    _appeared: new_repos falls back to the created_at window (inclusive at
    exactly NEW_REPO_WINDOW_DAYS), and movers/archived_recent stay empty."""
    new_cutoff = (run - dt.timedelta(days=NEW_REPO_WINDOW_DAYS)).isoformat()
    new_repos: list[dict] = []
    movers: list[dict] = []
    stale: list[dict] = []
    archived: list[dict] = []
    totals = {"repos": len(view), "active_30d": 0, "stars": 0}
    for name, r in view.items():
        totals["stars"] += r["stars"]
        pushed_days = _days_since(run, r.get("pushed_at", ""))
        if pushed_days is not None and pushed_days <= ACTIVE_WINDOW_DAYS:
            totals["active_30d"] += 1

        prev = base_view.get(name) if base_view else None
        is_new = prev is None if base_view else _date_str(r.get("created_at", "")) >= new_cutoff
        if is_new:
            new_repos.append(
                {
                    "name": name,
                    "created_at": r.get("created_at", ""),
                    "stars": r["stars"],
                    "description": r.get("description", ""),
                    "language": r.get("language", ""),
                }
            )
        if prev:
            # both sides came through org_views, so stars are always numeric
            delta_7d = round((r["stars"] - prev["stars"]) * 7 / base_span)
            if delta_7d:
                movers.append({"name": name, "stars": r["stars"], "delta_7d": delta_7d})
        if (
            not r.get("archived")
            and r["stars"] >= config["stale_min_stars"]
            and pushed_days is not None
            and pushed_days >= STALE_WATCH_MIN_DAYS
        ):
            stale.append(
                {
                    "name": name,
                    "stars": r["stars"],
                    "days_since_push": pushed_days,
                    "pushed_at": r.get("pushed_at", ""),
                }
            )
        aprev = arch_view.get(name) if arch_view else None
        if aprev and not aprev.get("archived") and r.get("archived"):
            archived.append(
                {"name": name, "stars": r["stars"], "pushed_at": r.get("pushed_at", "")}
            )

    # created_at desc with name-asc tiebreak (two stable sorts — the strings
    # can't be negated for a single-key sort)
    new_repos.sort(key=lambda x: x["name"])
    new_repos.sort(key=lambda x: x["created_at"], reverse=True)
    movers.sort(key=lambda m: (-abs(m["delta_7d"]), m["name"]))
    stale.sort(key=lambda s: (-s["days_since_push"], s["name"]))
    archived.sort(key=lambda a: a["name"])
    return {
        "org": org,
        "display": DISPLAY_NAMES.get(org, org),
        "totals": totals,
        "new_repos": new_repos[:LABS_TOP_N],
        "movers": movers[:LABS_TOP_N],
        "stale_watch": stale[:LABS_TOP_N],
        "archived_recent": archived,
    }


def build_labs_json(config: dict, run_date: str) -> dict:
    """Aggregate the snapshot history into the labs.json the /labs/ page
    prerenders (schema_version 1). Movers diff against the newest snapshot at
    least MOVERS_MIN_BASELINE_AGE_DAYS older than run_date; archived flips diff
    against the OLDEST snapshot within ARCHIVED_WINDOW_DAYS (so a flip that
    happened mid-window is still caught). An org absent from the current
    snapshot gets no entry at all — build_snapshot omits failed orgs, and a lab
    of zeros would misreport an outage as a dead lab."""
    run = dt.date.fromisoformat(run_date)
    usable = [d for d in fc_stories.list_snapshot_dates() if d <= run_date]
    out: dict = {
        "schema_version": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "date": run_date,
        "snapshot_date": None,
        "labs": [],
    }
    snap = fc_stories.load_snapshot(usable[-1]) if usable else None
    if snap is None:
        return out
    cur_date = usable[-1]
    out["snapshot_date"] = cur_date
    cur = fc_stories.org_views(snap)

    older = [d for d in usable if d != cur_date]
    movers_dates = [
        d for d in older if (run - dt.date.fromisoformat(d)).days >= MOVERS_MIN_BASELINE_AGE_DAYS
    ]
    arch_dates = [d for d in older if (run - dt.date.fromisoformat(d)).days <= ARCHIVED_WINDOW_DAYS]
    movers_date = movers_dates[-1] if movers_dates else None
    arch_date = arch_dates[0] if arch_dates else None
    movers_views = _views_for(movers_date)
    arch_views = _views_for(arch_date)
    movers_span = (run - dt.date.fromisoformat(movers_date)).days if movers_date else 0

    for org_cfg in config["orgs"]:
        org = org_cfg["name"]
        view = cur.get(org)
        if view is None:
            continue  # UNKNOWN today, not empty — no lab entry
        out["labs"].append(
            _lab_entry(
                org,
                view,
                run,
                config,
                (movers_views or {}).get(org) or None,
                movers_span,
                (arch_views or {}).get(org) or None,
            )
        )
    return out


# --- hash-gated R2 publish ---------------------------------------------------


def labs_content_hash(labs: dict) -> str:
    """sha256 of the canonical dump EXCLUDING generated_at, so a rebuild with
    identical content but a fresh timestamp is 'unchanged' (no PUT, no hook)."""
    content = {k: v for k, v in labs.items() if k != "generated_at"}
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _r2_labs_client(creds: dict):
    """boto3 S3 client on R2's S3-compatible endpoint — the same lazy-import
    pattern as render.py's r2_client (deliberately not imported: the skills
    stay independent)."""
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=f"https://{creds['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=creds["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=creds["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def fire_hook(url: str) -> None:
    """POST the Pages deploy hook so the site rebuilds. Best-effort: a timeout
    or error is logged, never raised — mirror of render.py's fire_pages_hook."""
    try:
        req = urllib.request.Request(url, data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            fc_common.log(f"labs deploy hook fired: {resp.status}")
    except Exception as e:
        fc_common.log(f"warn: labs deploy hook failed (non-fatal): {e}")


def publish_labs(labs: dict, config: dict, client_factory=None) -> str:
    """Hash-gated publish of labs.json to R2: 'published' | 'unchanged' |
    'skipped' | 'failed'. Never raises — the web page is optional and must not
    sink a snapshot run.

    The bucket comes from config.json ONLY (no env override): the launchd run
    has no shell env anyway, and reading env here would let a developer shell's
    R2_BUCKET turn a rehearsal into a real PUT. Credentials resolve via
    fc_common.load_r2_secrets (env → secrets.json tiers). Missing bucket or
    creds → 'skipped', same posture as render.py's three-state R2 'absent'.
    The hash file is written only AFTER a successful PUT, so a failed publish
    leaves the gate open and the next run retries."""
    try:
        secrets = fc_common.load_r2_secrets()
        bucket = config.get("r2_bucket")
        creds_ok = all(
            secrets.get(k) for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ACCOUNT_ID")
        )
        if not bucket or not creds_ok:
            return "skipped"
        new_hash = labs_content_hash(labs)
        hash_path = fc_common.labs_hash_path()
        try:
            if hash_path.read_text().strip() == new_hash:
                return "unchanged"
        except OSError:
            pass  # no previous publish recorded — publish
        client = (client_factory or _r2_labs_client)(secrets)
        client.put_object(
            Bucket=bucket,
            Key=config.get("labs_manifest_name") or "labs.json",
            Body=json.dumps(labs, indent=2, sort_keys=True).encode(),
            ContentType="application/json",
            CacheControl="no-cache",
        )
        hash_path.parent.mkdir(parents=True, exist_ok=True)
        hash_path.write_text(new_hash)
        hook = secrets.get("PAGES_DEPLOY_HOOK_URL")
        if hook:
            fire_hook(hook)
        return "published"
    except Exception as e:
        fc_common.log(f"warn: labs.json publish failed (non-fatal): {e}")
        return "failed"


# --- CLI ---------------------------------------------------------------------

# Module-level seam for tests: main routes every gh call through this alias so
# a test swaps the runner without touching subprocess itself.
_default_runner = subprocess.run


def _valid_snapshot_date(s: str) -> bool:
    """Canonical calendar-valid YYYY-MM-DD only — the same filename predicate
    write_snapshot enforces, checked up front so the FAILED line names the bad
    date instead of a mid-run ValueError."""
    if not re.fullmatch(fc_common.SNAPSHOT_RE, f"{s}.json"):
        return False
    try:
        dt.date.fromisoformat(s)
    except ValueError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Daily GitHub org sweep for Frontier Commits: write a snapshot, "
        "prune retention, and hash-gate-publish labs.json to R2. The final stdout "
        "line is always 'SNAPSHOT ok …' or 'SNAPSHOT FAILED …' — schedulers parse it."
    )
    ap.add_argument("--date", help="run date YYYY-MM-DD (default: today)")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="sweep and write the snapshot, but skip the R2 publish and deploy hook",
    )
    args = ap.parse_args(argv)
    # ONE resolved date drives everything downstream — the snapshot name, the
    # labs aggregation windows, and the prune cutoff. Never a second clock read.
    date_iso = args.date or dt.date.today().isoformat()
    if not _valid_snapshot_date(date_iso):
        print(f"SNAPSHOT FAILED --date must be YYYY-MM-DD, got {date_iso!r}")
        return 1
    config = fc_common.load_config()
    try:
        snap = build_snapshot(config, date_iso, runner=_default_runner)
        write_snapshot(snap)
        removed = prune_snapshots(
            config["snapshot_retention_days"], dt.date.fromisoformat(date_iso)
        )
        if removed:
            fc_common.log(f"pruned {len(removed)} old snapshots")
        labs = build_labs_json(config, date_iso)
        if args.dry_run:
            fc_common.log(
                f"dry-run: would publish {config['labs_manifest_name']} "
                f"({len(labs['labs'])} labs, content sha256 {labs_content_hash(labs)[:12]})"
            )
            labs_status = "dry-run"
        else:
            labs_status = publish_labs(labs, config)
    except (SweepFailed, fc_common.GhError, subprocess.SubprocessError, OSError) as e:
        # One parseable line, never a traceback: a gh timeout (TimeoutExpired),
        # a missing gh binary (FileNotFoundError under launchd), and a full
        # disk mid-write must all land here. Flattened — stderr fragments in
        # the message may carry newlines, and the contract is a single line.
        print(f"SNAPSHOT FAILED {' '.join(str(e).split())}")
        return 1
    n_repos = sum(len(v["repos"]) for v in snap["orgs"].values())
    n_rel = sum(len(v["releases"]) for v in snap["orgs"].values())
    print(
        f"SNAPSHOT ok date={date_iso} orgs={len(snap['orgs'])}/{len(config['orgs'])} "
        f"repos={n_repos} releases={n_rel} labs={labs_status}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
