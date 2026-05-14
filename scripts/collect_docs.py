#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, date, time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daytrace.io import write_events
from daytrace.schema import TraceEvent

DOC_EXTS = {".md", ".tex", ".txt"}
SKIP_PARTS = {".git", ".pytest_cache", "node_modules", "venv", ".venv", "__pycache__"}


def day_bounds(day: str) -> tuple[float, float]:
    d = date.fromisoformat(day)
    start = datetime.combine(d, time.min).timestamp()
    end = datetime.combine(d, time.max).timestamp()
    return start, end


def guess_project(path: Path) -> str | None:
    parts = path.parts
    for marker in ("Projects", "projects"):
        if marker in parts:
            i = parts.index(marker)
            if i + 1 < len(parts):
                return parts[i + 1]
    return path.parent.name if path.parent.name else None


def safe_excerpt(path: Path, max_chars: int = 300) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    text = " ".join(line.strip() for line in text.splitlines() if line.strip())
    return text[:max_chars]


def collect_doc_events(
    day: str, roots: list[Path], include_all_for_test: bool = False, limit: int = 200
) -> list[TraceEvent]:
    start_ts, end_ts = day_bounds(day)
    events: list[TraceEvent] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if len(events) >= limit:
                return events
            if any(part in SKIP_PARTS for part in path.parts):
                continue
            try:
                if not path.is_file() or path.suffix.lower() not in DOC_EXTS:
                    continue
                mtime = path.stat().st_mtime
                if not include_all_for_test and not (start_ts <= mtime <= end_ts):
                    continue
                iso = datetime.fromtimestamp(mtime).isoformat(timespec="seconds")
                rel_title = path.name
                project = guess_project(path)
                events.append(
                    TraceEvent(
                        id="docs-" + hashlib.sha1(str(path).encode()).hexdigest()[:16],
                        source="docs",
                        kind="document_modified",
                        start=iso,
                        end=None,
                        title=rel_title,
                        summary=f"Document modified: {path}",
                        project_guess=project,
                        confidence=0.75 if project else 0.4,
                        sensitivity="normal",
                        evidence={
                            "path": str(path),
                            "extension": path.suffix,
                            "excerpt": safe_excerpt(path),
                        },
                        raw_ref=str(path),
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
    events = collect_doc_events(args.date, roots, limit=args.limit)
    write_events(args.out, events)
    print(f"wrote {len(events)} doc events to {args.out}")


if __name__ == "__main__":
    main()
