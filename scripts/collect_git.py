#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import subprocess
from datetime import datetime, date, time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daytrace.io import write_events
from daytrace.schema import TraceEvent
from daytrace.timezone import LOCAL_TZ


def find_repos(roots: list[Path]) -> list[Path]:
    repos = []
    for root in roots:
        if not root.exists():
            continue
        if (root / ".git").exists():
            repos.append(root)
            continue
        def scan(parent, depth=0):
            if depth >= 4:
                return
            for child in parent.iterdir():
                if child.name.startswith('.') or child.name in {'node_modules','data','venv'} or child.is_symlink():
                    continue
                if child.is_dir():
                    if (child / '.git').exists():
                        repos.append(child)
                    else:
                        scan(child, depth+1)
        scan(root)
    # de-dupe case-insensitive macOS aliases
    seen = set()
    out = []
    for r in repos:
        key = str(r).lower()
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def collect_git_events(
    day: str, roots: list[Path], limit: int = 200
) -> list[TraceEvent]:
    events = []
    d = date.fromisoformat(day)
    since = datetime.combine(d, time.min, tzinfo=LOCAL_TZ).isoformat()
    until = datetime.combine(d, time.max, tzinfo=LOCAL_TZ).isoformat()
    for repo in find_repos(roots):
        project = repo.name
        try:
            log = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "log",
                    f"--since={since}",
                    f"--until={until}",
                    "--pretty=format:%H%x09%cI%x09%s",
                    # iso-strict → "2026-05-13T17:22:25-04:00" with T
                    # separator. Plain "iso" uses a space which breaks our
                    # lexicographic start_from/start_to filtering.
                    "--date=iso-strict",
                    f"--max-count={limit}",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for line in log.stdout.splitlines():
                if not line.strip() or len(events) >= limit:
                    break
                parts = line.split("\t", 2)
                if len(parts) != 3:
                    continue
                sha, when, subject = parts
                events.append(
                    TraceEvent(
                        id="git-commit-" + sha[:16],
                        source="git",
                        kind="commit",
                        start=datetime.fromisoformat(when).astimezone(LOCAL_TZ).replace(tzinfo=None).isoformat(timespec="seconds"),
                        end=None,
                        title=f"{project}: {subject}",
                        summary=f"Commit {sha[:7]} in {repo}",
                        project_guess=project,
                        sensitivity="normal",
                        evidence={"repo": str(repo), "sha": sha, "subject": subject},
                        raw_ref=str(repo),
                    )
                )
            # A current working tree cannot reconstruct historical changes.
            if d != datetime.now(LOCAL_TZ).date():
                continue
            status = subprocess.run(
                ["git", "-C", str(repo), "status", "--short"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            lines = [line for line in status.stdout.splitlines() if line.strip()]
            if lines and len(events) < limit:
                now = datetime.now(LOCAL_TZ).replace(tzinfo=None).isoformat(timespec="seconds")
                eid = "git-status-" + hashlib.sha1(str(repo).encode()).hexdigest()[:16]
                events.append(
                    TraceEvent(
                        id=eid,
                        source="git",
                        kind="working_tree_change",
                        start=now,
                        end=None,
                        title=f"{project}: {len(lines)} uncommitted changes",
                        summary="; ".join(lines[:12]),
                        project_guess=project,
                        sensitivity="normal",
                        evidence={"repo": str(repo), "status_lines": lines[:50]},
                        raw_ref=str(repo),
                    )
                )
        except Exception:
            continue
    return events


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--root", action="append", default=[])
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    roots = [Path(p).expanduser() for p in args.root] or [Path.home() / "Projects"]
    events = collect_git_events(args.date, roots, args.limit)
    write_events(args.out, events)
    print(f"wrote {len(events)} git events to {args.out}")


if __name__ == "__main__":
    main()
