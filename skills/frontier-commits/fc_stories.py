"""Deterministic story detector for Frontier Commits.

Pure function of (snapshots, reported.json, config): no network, no LLM.
Curation stays metadata-only — the same philosophy as the daily show's
one-body-per-request invariant. The "mention once, not repeatedly" rule is
mechanical: a story key that reaches reported.json never re-emits; a repo
resurfaces only when its STAGE changes (stale-180 → stale-365 → archived).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from pathlib import Path

import fc_common

FORK_ACTIVE_DAYS = 14

# Staleness ceiling for the newest snapshot. Gap-tolerant across a missed cron
# day or a weekend, but a detect run against a stale world is worse than no run
# at all: GOING_STALE measures days from run_date against an ancient pushed_at,
# so a six-month-old snapshot yields garbage stories. Beyond this, die and
# demand a fresh snapshot instead.
MAX_SNAPSHOT_AGE_DAYS = 3

TYPE_PRIORITY = {
    "NEW_REPO": 100,
    "ARCHIVED": 90,
    "NOTABLE_FORK": 80,
    "RELEASE": 60,
    "GOING_STALE": 50,
    "STAR_SURGE": 40,
}

# GitHub's own repo-name alphabet (orgs are stricter still — load_config enforces
# [A-Za-z0-9-]+). org and repo land verbatim in story keys and URLs, so names
# outside this alphabet — which only a damaged or hand-edited snapshot can carry —
# are dropped at the adapter rather than allowed to forge a colliding key.
_SAFE_NAME_RE = re.compile(r"[A-Za-z0-9_.-]+")
_DATE_ONLY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _safe_name(name: object) -> bool:
    return isinstance(name, str) and bool(_SAFE_NAME_RE.fullmatch(name))


def _valid_run_date(s: object) -> bool:
    """Strict YYYY-MM-DD. The regex gate matters: 3.11+ fromisoformat also
    accepts compact forms like 20260825, which would vary by Python version."""
    if not isinstance(s, str) or not _DATE_ONLY_RE.fullmatch(s):
        return False
    try:
        dt.date.fromisoformat(s)
    except ValueError:
        return False
    return True


def _parse_iso(ts: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _date_only(ts: str) -> str:
    d = _parse_iso(ts)
    return d.date().isoformat() if d else ""


def list_snapshot_dates() -> list[str]:
    d = fc_common.snapshot_dir()
    if not d.is_dir():
        return []
    # The filename regex alone admits calendar-invalid dates (2026-02-30.json),
    # which would later blow up date arithmetic (an uncaught ValueError in
    # detect_star_surge) — apply the same calendar check _valid_run_date embodies.
    return sorted(
        m.group(1)
        for p in d.iterdir()
        if (m := re.fullmatch(fc_common.SNAPSHOT_RE, p.name)) and _valid_run_date(m.group(1))
    )


def load_snapshot(date_iso: str) -> dict | None:
    # date_iso lands verbatim in a path; anything the shared snapshot-filename
    # predicate rejects ('../escaped', datetimes) can't name a snapshot anyway.
    if not isinstance(date_iso, str) or not re.fullmatch(fc_common.SNAPSHOT_RE, f"{date_iso}.json"):
        return None
    path = fc_common.snapshot_dir() / f"{date_iso}.json"
    try:
        snap = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return snap if isinstance(snap, dict) else None


def pick_baseline(dates: list[str], run_date: str, lookback_days: int) -> str | None:
    target = (dt.date.fromisoformat(run_date) - dt.timedelta(days=lookback_days)).isoformat()
    eligible = [d for d in dates if d <= target]
    if eligible:
        return eligible[-1]
    older = [d for d in dates if d < run_date]
    return older[-1] if older else None


def story(type_: str, org: str, repo: str, stage: str, facts: dict) -> dict:
    # The key is an OPAQUE mention-once token: exact-matched against
    # reported.json, never parsed, never a path. Stage is embedded VERBATIM — a
    # release tag may legally contain ':' ("v1:beta"), and sanitizing it could
    # collide two distinct releases into one key (a silently skipped mention).
    # Verbatim is injective because org and repo can never contain ':' or '/'
    # (org names are config-validated; repo names are gated by _safe_name at
    # the org_views / detect_releases boundaries).
    return {
        "key": f"{type_}:{org}/{repo}:{stage}",
        "type": type_,
        "org": org,
        "repo": repo,
        "url": f"https://github.com/{org}/{repo}",
        "title": f"{org}/{repo}",
        "score": 0.0,
        "facts": facts,
    }


def org_views(snap: dict) -> dict:
    """Unwrap a snapshot file into `{org: {repo_name: record}}`.

    This adapter is the malformed-data gate for every detector: snapshots come
    off disk and may be damaged, so a non-dict org view, a non-dict repos map,
    or a non-dict record is dropped here instead of crashing a detector
    mid-run. Names outside GitHub's alphabet are dropped too (see
    _SAFE_NAME_RE — they would forge colliding story keys/URLs). Stars are
    coerced here as well: upstream repo_record emits None for a
    present-but-null stargazers_count, which would TypeError inside the
    detectors' numeric comparisons BEFORE score_story's guard ever applies —
    killing the whole run and losing the healthy stories with it.
    """
    orgs = snap.get("orgs", {})
    if not isinstance(orgs, dict):
        return {}
    out: dict = {}
    for org, view in orgs.items():
        if not _safe_name(org) or not isinstance(view, dict):
            continue
        repos = view.get("repos")
        if not isinstance(repos, dict):
            repos = {}
        kept: dict = {}
        for name, rec in repos.items():
            if not _safe_name(name) or not isinstance(rec, dict):
                continue
            stars = rec.get("stars")
            if not (isinstance(stars, (int, float)) and not isinstance(stars, bool) and stars >= 0):
                stars = 0
            kept[name] = {**rec, "stars": stars}
        out[org] = kept
    return out


def _appeared(org: str, name: str, r: dict, base: dict | None, cutoff_date: str) -> bool:
    """When the baseline actually SAW the org, "appeared" is SET MEMBERSHIP —
    present in cur, absent from the baseline's repo set for that org — never
    date arithmetic. A created_at cutoff would permanently silence a repo
    created ON the baseline date but AFTER that day's snapshot was taken
    (absent from base, yet excluded by the date gate — and the cutoff only
    moves forward).

    But an org with no usable baseline view is UNKNOWN, not empty.
    build_snapshot OMITS a failed org from snap["orgs"] entirely (it lands in
    snap["errors"]), and an org newly added to config has no baseline entry
    at all — treating either as an empty set would announce the org's ENTIRE
    catalog as "new" (ancient repos at top NEW_REPO priority, filling
    per_org_story_cap and permanently burning their mention-once keys). A
    present-but-EMPTY org view is ambiguous the same way: the "ai" name
    filter can legitimately strip an org to zero repos while its real repos
    are years old. Both cases fall back to the date-cutoff arm, same as the
    no-baseline path. The asymmetry is deliberately conservative: the
    fallback can delay announcing a genuinely-new repo by at most the
    lookback window, but it can never flood ancient repos into an episode."""
    if base is not None and base.get(org):
        return name not in base[org]
    return _date_only(r.get("created_at", "")) > cutoff_date


def detect_new_repos(cur: dict, cutoff_date: str, base: dict | None = None) -> list[dict]:
    out = []
    for org, repos in cur.items():
        for name, r in repos.items():
            if r.get("fork") or not _appeared(org, name, r, base, cutoff_date):
                continue
            out.append(
                story(
                    "NEW_REPO",
                    org,
                    name,
                    "new",
                    {
                        "created_at": r.get("created_at", ""),
                        "stars": r.get("stars", 0),
                        "description": r.get("description", ""),
                        "language": r.get("language", ""),
                        "topics": r.get("topics", []),
                    },
                )
            )
    return sorted(out, key=lambda s: (s["org"], s["repo"]))


def detect_notable_forks(
    cur: dict, cutoff_date: str, run_date: str, base: dict | None = None
) -> list[dict]:
    # The actively-pushed arm only: the spec's upstream-stars arm needs a
    # per-fork parent lookup (network) and is deliberately deferred.
    active_cutoff = dt.date.fromisoformat(run_date) - dt.timedelta(days=FORK_ACTIVE_DAYS)
    out = []
    for org, repos in cur.items():
        for name, r in repos.items():
            if not r.get("fork") or not _appeared(org, name, r, base, cutoff_date):
                continue
            pushed = _parse_iso(r.get("pushed_at", ""))
            if not pushed or pushed.date() < active_cutoff:
                continue
            out.append(
                story(
                    "NOTABLE_FORK",
                    org,
                    name,
                    "new",
                    {
                        "created_at": r.get("created_at", ""),
                        "pushed_at": r.get("pushed_at", ""),
                        "stars": r.get("stars", 0),
                        "description": r.get("description", ""),
                    },
                )
            )
    return sorted(out, key=lambda s: (s["org"], s["repo"]))


def detect_archived(cur: dict, base: dict | None) -> list[dict]:
    if not base:
        return []
    out = []
    for org, repos in cur.items():
        for name, r in repos.items():
            prev = base.get(org, {}).get(name)
            if prev and not prev.get("archived") and r.get("archived"):
                out.append(
                    story(
                        "ARCHIVED",
                        org,
                        name,
                        "archived",
                        {
                            "stars": r.get("stars", 0),
                            "pushed_at": r.get("pushed_at", ""),
                            "description": r.get("description", ""),
                        },
                    )
                )
    return sorted(out, key=lambda s: (s["org"], s["repo"]))


def detect_going_stale(cur: dict, run_date: str, config: dict) -> list[dict]:
    today = dt.date.fromisoformat(run_date)
    out = []
    for org, repos in cur.items():
        for name, r in repos.items():
            if r.get("archived") or r.get("stars", 0) < config["stale_min_stars"]:
                continue
            pushed = _parse_iso(r.get("pushed_at", ""))
            if not pushed:
                continue
            days = (today - pushed.date()).days
            crossed = [b for b in sorted(config["stale_stages_days"]) if days >= b]
            if not crossed:
                continue
            out.append(
                story(
                    "GOING_STALE",
                    org,
                    name,
                    f"stale-{crossed[-1]}",
                    {
                        "stars": r.get("stars", 0),
                        "pushed_at": r.get("pushed_at", ""),
                        "days_since_push": days,
                        "description": r.get("description", ""),
                    },
                )
            )
    return sorted(out, key=lambda s: (s["org"], s["repo"]))


def iso_week(run_date: str) -> str:
    y, w, _ = dt.date.fromisoformat(run_date).isocalendar()
    return f"{y}-W{w:02d}"


def detect_star_surge(
    cur: dict, base: dict | None, baseline_date: str | None, run_date: str, config: dict
) -> list[dict]:
    if not base or not baseline_date:
        return []
    span = max(1, (dt.date.fromisoformat(run_date) - dt.date.fromisoformat(baseline_date)).days)
    out = []
    for org, repos in cur.items():
        for name, r in repos.items():
            prev = base.get(org, {}).get(name)
            if not prev:
                continue
            delta7 = (r.get("stars", 0) - prev.get("stars", 0)) * 7 / span
            bar = max(
                config["surge_min_delta_7d"], config["surge_min_ratio"] * prev.get("stars", 0)
            )
            if delta7 >= bar:
                # Stage = ISO week, so a same-week re-run rebuilds the same
                # episode; a surge sustained into the NEXT week may
                # legitimately re-emit (scoring already de-prioritizes it).
                out.append(
                    story(
                        "STAR_SURGE",
                        org,
                        name,
                        iso_week(run_date),
                        {
                            "stars": r.get("stars", 0),
                            "stars_before": prev.get("stars", 0),
                            "delta_per_7d": round(delta7),
                            "span_days": span,
                            "description": r.get("description", ""),
                        },
                    )
                )
    return sorted(out, key=lambda s: (s["org"], s["repo"]))


def detect_releases(snap: dict, cutoff_date: str, cur: dict, config: dict) -> list[dict]:
    orgs = snap.get("orgs", {})
    if not isinstance(orgs, dict):
        return []
    out = []
    best: dict = {}
    for org, view in orgs.items():
        if not _safe_name(org) or not isinstance(view, dict):
            continue
        allow = set(config.get("allowlist", {}).get(org, []))
        releases = view.get("releases")
        for rel in releases if isinstance(releases, list) else []:
            if not isinstance(rel, dict):
                continue
            name = rel.get("repo", "")
            tag = rel.get("tag", "")
            # An empty tag can't form a distinguishable stage: every tagless
            # release of a repo would share one key, so only the first would
            # ever be mentioned. Skip rather than collide.
            if not _safe_name(name) or not isinstance(tag, str) or not tag:
                continue
            # Strictly-before, not at-or-before: a release published late on
            # the baseline day (after that day's snapshot was taken) would
            # otherwise be PERMANENTLY missed at weekly cadence. A boundary-day
            # release may be nominated in two consecutive runs pre-mark;
            # reported.json (stage=tag) makes the mention exactly-once —
            # permanently missing a release is the failure mode, a duplicate
            # candidate is not.
            if _date_only(rel.get("published_at", "")) < cutoff_date:
                continue
            rec = cur.get(org, {}).get(name, {})
            if rec.get("stars", 0) < config["release_min_stars"] and name not in allow:
                continue
            # A release train (v2.1.237/238/239 inside one window) must not take
            # one story slot per tag — collapse to the newest release per repo.
            # Stage stays the tag, so a NEWER tag next week re-nominates once.
            key = (org, name)
            prev = best.get(key)
            marker = (rel.get("published_at", ""), tag)
            if prev is not None and marker <= (prev.get("published_at", ""), prev.get("tag", "")):
                continue
            best[key] = {
                "rel": rel,
                "published_at": rel.get("published_at", ""),
                "tag": tag,
                "org": org,
                "name": name,
                "rec": rec,
            }
    for entry in best.values():
        out.append(
            story(
                "RELEASE",
                entry["org"],
                entry["name"],
                entry["tag"],
                {
                    "tag": entry["tag"],
                    "published_at": entry["published_at"],
                    "stars": entry["rec"].get("stars", 0),
                    "description": entry["rec"].get("description", ""),
                },
            )
        )
    return sorted(out, key=lambda s: (s["org"], s["repo"], s["key"]))


def load_reported() -> dict:
    """Malformed/missing → {} — worst case a repeated mention one week
    (mirrors the covered.json posture)."""
    try:
        data = json.loads(fc_common.reported_path().read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def filter_new(candidates: list[dict], reported: dict) -> list[dict]:
    return [c for c in candidates if c["key"] not in reported]


def mark_reported(keys: list[str], date_iso: str, episode_uri: str) -> None:
    rep = load_reported()
    for k in keys:
        rep[k] = {"date": date_iso, "episode_uri": episode_uri}
    fc_common.atomic_write_json(fc_common.reported_path(), rep)


def score_story(s: dict) -> float:
    # The spec's "recency" term is deliberately omitted: every detector already
    # gates on a recency window, so a score term would double-count it.
    stars = s["facts"].get("stars", 0)
    if not isinstance(stars, (int, float)) or stars < 0:
        stars = 0  # a damaged snapshot must degrade a score, not crash log10
    return TYPE_PRIORITY[s["type"]] + 10 * math.log10(stars + 10)


def select_stories(cands: list[dict], target: int, per_org_cap: int) -> list[dict]:
    ordered = sorted(cands, key=lambda s: (-s["score"], s["key"]))
    picked: list[dict] = []
    org_counts: dict[str, int] = {}
    for s in ordered:
        if len(picked) >= target:
            break
        if org_counts.get(s["org"], 0) >= per_org_cap:
            continue
        picked.append(s)
        org_counts[s["org"]] = org_counts.get(s["org"], 0) + 1
    return picked


def detect_all(run_date: str, config: dict) -> dict:
    dates = list_snapshot_dates()
    usable = [d for d in dates if d <= run_date]
    if not usable:
        fc_common.die(f"no snapshot at or before {run_date} — run fc_snapshot.py first")
    cur_date = usable[-1]
    if cur_date != run_date:
        gap = (dt.date.fromisoformat(run_date) - dt.date.fromisoformat(cur_date)).days
        if gap > MAX_SNAPSHOT_AGE_DAYS:
            fc_common.die(
                f"newest snapshot {cur_date} is older than {MAX_SNAPSHOT_AGE_DAYS} days "
                f"before {run_date} — run fc_snapshot.py first"
            )
        fc_common.log(f"warn: no snapshot for {run_date}, using {cur_date}")
    snap = load_snapshot(cur_date)
    if snap is None:
        fc_common.die(f"snapshot {cur_date} is unreadable")
    cur = org_views(snap)
    # The baseline must NEVER be the current snapshot itself: pick_baseline's
    # fallback arm only excludes run_date, so on a missed cron day with a young
    # snapshot dir it would resolve to cur_date — cutoff becomes the snapshot's
    # own date (NEW_REPO/NOTABLE_FORK can never fire), ARCHIVED compares the
    # file with itself, and STAR_SURGE deltas are all 0: a silently empty
    # episode. With no distinct baseline, baseline_date=None already does the
    # right thing (date-cutoff fallback, no delta detectors).
    baseline_date = pick_baseline(
        [d for d in usable if d != cur_date], run_date, config["lookback_days"]
    )
    base_snap = load_snapshot(baseline_date) if baseline_date else None
    base = org_views(base_snap) if base_snap else None
    cutoff = (
        baseline_date
        or (
            dt.date.fromisoformat(run_date) - dt.timedelta(days=config["lookback_days"])
        ).isoformat()
    )

    cands = [
        *detect_new_repos(cur, cutoff, base),
        *detect_notable_forks(cur, cutoff, run_date, base),
        *detect_archived(cur, base),
        *detect_going_stale(cur, run_date, config),
        *detect_star_surge(cur, base, baseline_date, run_date, config),
        *detect_releases(snap, cutoff, cur, config),
    ]
    cands = filter_new(cands, load_reported())
    for s in cands:
        s["score"] = round(score_story(s), 2)
    stories = select_stories(
        cands, config["target_stories_per_episode"], config["per_org_story_cap"]
    )
    return {
        "run_date": run_date,
        "baseline_date": baseline_date,
        "thin": len(stories) < config["min_stories_per_episode"],
        "stories": stories,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Deterministic story detector for Frontier Commits")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ap_detect = sub.add_parser("detect", help="detect stories; prints one JSON object")
    ap_detect.add_argument("--date", help="run date YYYY-MM-DD (default: today)")
    ap_detect.add_argument("--lookback-days", type=int, help="override config lookback_days")
    ap_mark = sub.add_parser("mark", help="mark a detect output's stories as reported")
    ap_mark.add_argument("--stories", required=True, help="path to a detect-output JSON file")
    ap_mark.add_argument(
        "--episode-uri",
        required=True,
        # Free-form on purpose: this show ships web-only (#155), so the identity is
        # the published mp3 URL, not an episode URI on any platform.
        help="episode identity to record (the published mp3 URL for a web-only ship)",
    )
    args = ap.parse_args(argv)

    if args.cmd == "detect":
        # ONE resolved date: every derived field (baseline, cutoffs, ISO-week
        # stage) descends from this value — never a second wall-clock read.
        run_date = args.date or dt.date.today().isoformat()
        if not _valid_run_date(run_date):
            fc_common.die(f"--date must be YYYY-MM-DD, got {run_date!r}")
        config = fc_common.load_config()
        if args.lookback_days is not None:
            if args.lookback_days <= 0:
                fc_common.die("--lookback-days must be positive")
            config = {**config, "lookback_days": args.lookback_days}
        out = detect_all(run_date, config)
        print(json.dumps(out))  # the FINAL stdout line — schedulers parse it
        return 0

    # mark: never silently mark nothing — a malformed input dies loudly, and a
    # thin week's empty stories list is a legitimate no-op.
    try:
        data = json.loads(Path(args.stories).read_text())
    except (OSError, json.JSONDecodeError) as e:
        fc_common.die(f"unreadable stories file {args.stories}: {e}")
    stories = data.get("stories") if isinstance(data, dict) else None
    if not isinstance(stories, list):
        fc_common.die(f"{args.stories}: expected a detect output with a 'stories' list")
    keys = []
    for s in stories:
        if not isinstance(s, dict) or not isinstance(s.get("key"), str) or not s["key"]:
            fc_common.die(f"{args.stories}: every story needs a string 'key': {s!r}")
        keys.append(s["key"])
    run_date = data.get("run_date")
    if not _valid_run_date(run_date):
        fc_common.die(f"{args.stories}: 'run_date' must be YYYY-MM-DD, got {run_date!r}")
    mark_reported(keys, run_date, args.episode_uri)
    fc_common.log(f"marked {len(keys)} story keys reported for {run_date}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
