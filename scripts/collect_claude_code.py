#!/usr/bin/env python3
"""Collect Claude Code user-input events from ~/.claude/projects/*/*.jsonl.

Each Claude Code conversation is stored as one JSONL file per sessionId
under a folder named after the working directory (with slashes turned to
dashes). Lines are turn records; we keep `type=user` lines as one event
each. The `message.content` may be a plain string or a list of content
blocks (multimodal) — we extract the concatenated text parts.

Mirrors the Codex / Hermes collector shape so downstream code (collector
config, day_report pipeline, AI activity labels) treats Claude Code as
just another `source`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daytrace.io import write_events
from daytrace.schema import TraceEvent


LOCAL_TZ = ZoneInfo("America/Detroit")


def day_bounds_epoch(day: str) -> tuple[float, float]:
    d = date.fromisoformat(day)
    return (
        datetime.combine(d, time.min, tzinfo=LOCAL_TZ).timestamp(),
        datetime.combine(d, time.max, tzinfo=LOCAL_TZ).timestamp(),
    )


def iso_to_epoch(value: str | None) -> float | None:
    """Claude Code timestamps look like '2026-05-16T02:48:59.350Z'."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def epoch_to_local_iso(epoch: float) -> str:
    return (
        datetime.fromtimestamp(epoch, tz=LOCAL_TZ)
        .replace(tzinfo=None)
        .isoformat(timespec="seconds")
    )


def cwd_from_project_dir(project_dir: Path) -> str:
    """The folder name encodes the cwd with slashes replaced by dashes:
        -Users-xingminwang-Projects-daytrace  →  /Users/xingminwang/Projects/daytrace
    The leading dash means an absolute path."""
    name = project_dir.name
    if name.startswith("-"):
        return name.replace("-", "/")
    return name


def guess_project(cwd: str | None, text: str = "") -> str | None:
    if cwd:
        parts = Path(cwd).parts
        if "Projects" in parts:
            i = parts.index("Projects")
            if i + 1 < len(parts):
                return parts[i + 1]
        # Documents/research-paper-xxx etc.
        for stem in ("research-paper", "research-programs"):
            if stem in parts:
                i = parts.index(stem)
                if i + 1 < len(parts):
                    return parts[i + 1]
    low = (text or "").lower()
    for name in (
        "daytrace", "daily-briefing", "loft-sim",
        "baidu-signal-paper", "paper-review",
    ):
        if name in low:
            return name
    return None


def extract_text(content) -> str:
    """`message.content` is either a str or a list of {type, text|...}."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                elif block.get("type") == "tool_result":
                    # Tool result echoes; skip — these aren't user thought.
                    continue
                else:
                    # Unknown block type; ignore quietly.
                    continue
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p for p in parts if p)
    return ""


def collect_claude_code_events(
    day: str,
    claude_home: Path | None = None,
    limit: int = 800,
) -> list[TraceEvent]:
    """Return one TraceEvent per Claude Code user turn within `day`."""
    root = claude_home or (Path.home() / ".claude" / "projects")
    if not root.exists():
        return []
    start_ts, end_ts = day_bounds_epoch(day)

    events: list[TraceEvent] = []
    seen_ids: set[str] = set()

    for project_dir in sorted(root.iterdir()):
        if not project_dir.is_dir():
            continue
        for session_path in sorted(project_dir.glob("*.jsonl")):
            session_id = session_path.stem
            if len(events) >= limit:
                break
            try:
                lines = session_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue
            for idx, line in enumerate(lines):
                if len(events) >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") != "user":
                    continue
                # Use cwd from the line itself — the folder name encoding
                # mangles dashes (daily-briefing → daily/briefing).
                cwd = obj.get("cwd") or cwd_from_project_dir(project_dir)
                # Claude Code occasionally injects synthetic "user" turns
                # (tool result echoes back to itself) via userType=external
                # vs internal sidechain. Skip sidechain noise.
                if obj.get("isSidechain"):
                    continue
                if obj.get("userType") and obj["userType"] != "external":
                    continue
                ts_epoch = iso_to_epoch(obj.get("timestamp"))
                if ts_epoch is None or not (start_ts <= ts_epoch <= end_ts):
                    continue
                msg = obj.get("message") or {}
                text = extract_text(msg.get("content")).strip()
                if not text:
                    continue
                # Tool-result wrappers Claude Code adds when a user message
                # is just an automated continuation; not real user input.
                if text.startswith("<command-name>") or text.startswith("[Request interrupted"):
                    continue

                when = epoch_to_local_iso(ts_epoch)
                eid_seed = f"claude-code:{session_id}:{idx}:{obj.get('uuid','')}"
                eid = "claude-code-" + hashlib.sha1(eid_seed.encode()).hexdigest()[:16]
                if eid in seen_ids:
                    continue
                seen_ids.add(eid)
                title = text.splitlines()[0][:96] or "Claude Code user input"
                events.append(
                    TraceEvent(
                        id=eid,
                        source="claude_code",
                        kind="user_input",
                        start=when,
                        end=None,
                        title=title,
                        summary=text,
                        project_guess=guess_project(cwd, text),
                        sensitivity="normal",
                        evidence={
                            "session_id": session_id,
                            "cwd": cwd,
                            "git_branch": obj.get("gitBranch"),
                            "rollout_path": str(session_path),
                            "uuid": obj.get("uuid"),
                            "line": idx,
                        },
                        raw_ref=str(session_path),
                    )
                )

    return events


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD (local time)")
    parser.add_argument("--claude-home",
                        help="override path to ~/.claude/projects")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=800)
    args = parser.parse_args()
    events = collect_claude_code_events(
        args.date,
        Path(args.claude_home).expanduser() if args.claude_home else None,
        args.limit,
    )
    write_events(args.out, events)
    print(f"wrote {len(events)} Claude Code user-input events to {args.out}")


if __name__ == "__main__":
    main()
