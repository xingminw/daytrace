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


def find_repos(roots: list[Path]) -> list[Path]:
    repos = []
    for root in roots:
        if not root.exists():
            continue
        if (root / ".git").exists():
            repos.append(root)
            continue
        for child in root.iterdir():
            if child.is_dir() and (child / ".git").exists():
                repos.append(child)
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
    since = datetime.combine(d, time.min).isoformat()
    until = datetime.combine(d, time.max).isoformat()
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
                    "--pretty=format:%H%x09%ad%x09%s",
                    # iso-strict → "2026-05-13T17:22:25-04:00" with T
                    # separator. Plain "iso" uses a space which breaks our
                    # lexicographic start_from/start_to filtering.
                    "--date=iso-strict",
                    "--max-count=50",
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
                        start=when[:19],
                        end=None,
                        title=f"{project}: {subject}",
                        summary=f"Commit {sha[:7]} in {repo}",
                        project_guess=project,
                        sensitivity="normal",
                        evidence={"repo": str(repo), "sha": sha, "subject": subject},
                        raw_ref=str(repo),
                    )
                )
            status = subprocess.run(
                ["git", "-C", str(repo), "status", "--short"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            lines = [line for line in status.stdout.splitlines() if line.strip()]
            if lines and len(events) < limit:
                now = datetime.now().isoformat(timespec="seconds")
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
