#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daytrace.io import write_events
from daytrace.schema import TraceEvent


LOCAL_TZ = ZoneInfo("America/Detroit")


def run_json(args: list[str], timeout: int = 60) -> Any:
    proc = subprocess.run(
        ["gh", *args], capture_output=True, text=True, timeout=timeout
    )
    if proc.returncode != 0:
        raise RuntimeError(
            proc.stderr.strip() or proc.stdout.strip() or f"gh {' '.join(args)} failed"
        )
    text = proc.stdout.strip()
    if not text:
        return None
    return json.loads(text)


def gh_api(
    endpoint: str,
    fields: dict[str, str] | None = None,
    paginate: bool = False,
    slurp: bool = False,
) -> Any:
    args = ["api", endpoint]
    if paginate:
        args.append("--paginate")
    if slurp:
        args.append("--slurp")
    if fields:
        args.extend(["--method", "GET"])
    for k, v in (fields or {}).items():
        args.extend(["-f", f"{k}={v}"])
    return run_json(args)


def flatten_pages(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list) and value and all(isinstance(x, list) for x in value):
        out: list[Any] = []
        for page in value:
            out.extend(page)
        return out
    if isinstance(value, list):
        return value
    return [value]


def dt_bounds(day: str) -> tuple[datetime, datetime, str, str]:
    d = date.fromisoformat(day)
    start = datetime.combine(d, time.min, tzinfo=LOCAL_TZ)
    end = datetime.combine(d, time.max, tzinfo=LOCAL_TZ)
    since = start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    until = end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return start, end, since, until


def parse_github_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def in_range(ts: str | None, start: datetime, end: datetime) -> bool:
    dt = parse_github_ts(ts)
    return bool(dt and start <= dt.astimezone(LOCAL_TZ) <= end)


def local_iso(ts: str | None, fallback: str) -> str:
    dt = parse_github_ts(ts)
    if not dt:
        return fallback
    return dt.astimezone(LOCAL_TZ).replace(tzinfo=None).isoformat(timespec="seconds")


def event_id(prefix: str, *parts: object) -> str:
    return (
        prefix
        + "-"
        + hashlib.sha1(":".join(str(p) for p in parts).encode()).hexdigest()[:16]
    )


def project_from_repo(full_name: str | None) -> str | None:
    if not full_name:
        return None
    name = full_name.split("/", 1)[-1]
    aliases = {
        "daytrace": "daytrace",
        "daily-briefing": "daily-briefing",
        "loft-sim": "LOFT-Sim",
        "baidu-signal-paper": "baidu-signal-paper",
    }
    return aliases.get(name.lower(), name)


def collect_github_events(
    day: str, limit: int = 800, repo_limit: int = 80
) -> list[TraceEvent]:
    start, end, since, until = dt_bounds(day)
    user = gh_api("user")
    login = user["login"]
    now = datetime.now(LOCAL_TZ).replace(tzinfo=None).isoformat(timespec="seconds")
    events: list[TraceEvent] = [
        TraceEvent(
            id=event_id("github-account", login, day),
            source="github",
            kind="account_snapshot",
            start=now,
            end=None,
            title=f"GitHub account: {login}",
            summary=f"{user.get('name') or login}; public_repos={user.get('public_repos')}; location={user.get('location') or 'unknown'}",
            project_guess=None,
            sensitivity="private",
            evidence={
                "login": login,
                "name": user.get("name"),
                "company": user.get("company"),
                "location": user.get("location"),
                "public_repos": user.get("public_repos"),
                "created_at": user.get("created_at"),
                "updated_at": user.get("updated_at"),
            },
            raw_ref=f"https://github.com/{login}",
        )
    ]

    repos = flatten_pages(
        gh_api(
            "/user/repos?per_page=100&affiliation=owner,collaborator,organization_member&sort=updated&direction=desc",
            paginate=True,
            slurp=True,
        )
    )
    recent_repos = []
    for repo in repos:
        updated_at = repo.get("updated_at")
        if in_range(updated_at, start, end):
            recent_repos.append(repo)
            if len(recent_repos) >= repo_limit:
                break
        else:
            parsed_updated = parse_github_ts(updated_at)
            if parsed_updated and parsed_updated < start and len(recent_repos) >= 5:
                # Repos are sorted by update time; after a few hits this avoids walking old repos.
                break

    for repo in recent_repos:
        full = repo.get("full_name")
        project = project_from_repo(full)
        events.append(
            TraceEvent(
                id=event_id("github-repo", full, repo.get("updated_at")),
                source="github",
                kind="repository_updated",
                start=local_iso(repo.get("updated_at"), now),
                end=None,
                title=f"Repo updated: {full}",
                summary=f"{full} updated on GitHub; language={repo.get('language')}; private={repo.get('private')}; default_branch={repo.get('default_branch')}",
                project_guess=project,
                sensitivity="private" if repo.get("private") else "normal",
                evidence={
                    "full_name": full,
                    "html_url": repo.get("html_url"),
                    "private": repo.get("private"),
                    "fork": repo.get("fork"),
                    "language": repo.get("language"),
                    "default_branch": repo.get("default_branch"),
                    "pushed_at": repo.get("pushed_at"),
                    "updated_at": repo.get("updated_at"),
                    "owner": (repo.get("owner") or {}).get("login"),
                },
                raw_ref=repo.get("html_url"),
            )
        )

    for repo in recent_repos:
        full = repo.get("full_name")
        if not full:
            continue
        try:
            commits = flatten_pages(
                gh_api(
                    f"/repos/{full}/commits",
                    {
                        "since": since,
                        "until": until,
                        "author": login,
                        "per_page": "100",
                    },
                    paginate=True,
                    slurp=True,
                )
            )
        except Exception:
            commits = []
        for c in commits[:100]:
            commit = c.get("commit") or {}
            author = commit.get("author") or {}
            ts = author.get("date") or commit.get("committer", {}).get("date") or now
            msg = (commit.get("message") or "").splitlines()[0]
            sha = c.get("sha")
            events.append(
                TraceEvent(
                    id=event_id("github-commit", full, sha),
                    source="github",
                    kind="remote_commit",
                    start=local_iso(ts, now),
                    end=None,
                    title=f"Remote commit: {full}: {msg[:90]}",
                    summary=commit.get("message") or msg,
                    project_guess=project_from_repo(full),
                    sensitivity="private" if repo.get("private") else "normal",
                    evidence={
                        "repo": full,
                        "sha": sha,
                        "html_url": c.get("html_url"),
                        "author_login": (c.get("author") or {}).get("login"),
                        "author_name": author.get("name"),
                        "author_date": author.get("date"),
                    },
                    raw_ref=c.get("html_url"),
                )
            )

    # Issues search returns both issues and PRs; authenticated search includes accessible private repos.
    for kind, query_type in [
        ("pull_request_activity", "pr"),
        ("issue_activity", "issue"),
    ]:
        q = f"involves:{login} type:{query_type} updated:>={day}"
        try:
            result = gh_api(
                "search/issues",
                {"q": q, "sort": "updated", "order": "desc", "per_page": "100"},
            )
            items = result.get("items", []) if isinstance(result, dict) else []
        except Exception:
            items = []
        for item in items[:100]:
            updated_at = item.get("updated_at")
            if not in_range(updated_at, start, end):
                continue
            repo_url = item.get("repository_url") or ""
            full = repo_url.rsplit("/repos/", 1)[-1] if "/repos/" in repo_url else None
            events.append(
                TraceEvent(
                    id=event_id("github-search", kind, item.get("id"), updated_at),
                    source="github",
                    kind=kind,
                    start=local_iso(updated_at, now),
                    end=None,
                    title=f"GitHub {query_type}: {item.get('title', '')[:100]}",
                    summary=item.get("body") or item.get("title") or "",
                    project_guess=project_from_repo(full),
                    sensitivity="private",
                    evidence={
                        "repo": full,
                        "number": item.get("number"),
                        "state": item.get("state"),
                        "html_url": item.get("html_url"),
                        "user": (item.get("user") or {}).get("login"),
                        "updated_at": updated_at,
                        "query": q,
                    },
                    raw_ref=item.get("html_url"),
                )
            )

    seen: set[str] = set()
    out: list[TraceEvent] = []
    for e in sorted(events, key=lambda x: x.start, reverse=True):
        if e.id not in seen:
            seen.add(e.id)
            out.append(e)
    return out[:limit]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect read-only GitHub account/repo remote activity into DayTrace events."
    )
    parser.add_argument("--date", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=800)
    parser.add_argument("--repo-limit", type=int, default=80)
    args = parser.parse_args()
    events = collect_github_events(args.date, args.limit, args.repo_limit)
    write_events(args.out, events)
    print(f"wrote {len(events)} GitHub remote events to {args.out}")


if __name__ == "__main__":
    main()
