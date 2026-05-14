#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, date, time
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daytrace.io import write_events
from daytrace.schema import TraceEvent


LOCAL_TZ = ZoneInfo("America/Detroit")


def day_bounds(day: str) -> tuple[float, float]:
    d = date.fromisoformat(day)
    return datetime.combine(d, time.min, tzinfo=LOCAL_TZ).timestamp(), datetime.combine(
        d, time.max, tzinfo=LOCAL_TZ
    ).timestamp()


def parse_ts(value: str | None, fallback: float) -> tuple[float, str]:
    if not value:
        return fallback, datetime.fromtimestamp(fallback, tz=LOCAL_TZ).replace(
            tzinfo=None
        ).isoformat(timespec="seconds")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        local = dt.astimezone(LOCAL_TZ)
        return local.timestamp(), local.replace(tzinfo=None).isoformat(
            timespec="seconds"
        )
    except Exception:
        return fallback, datetime.fromtimestamp(fallback, tz=LOCAL_TZ).replace(
            tzinfo=None
        ).isoformat(timespec="seconds")


def guess_project(text: str) -> str | None:
    low = text.lower()
    aliases = {
        "daytrace": "daytrace",
        "daily-briefing": "daily-briefing",
        "daily briefing": "daily-briefing",
        "loft-sim": "LOFT-Sim",
        "baidu-signal-paper": "baidu-signal-paper",
        "paper-review": "paper-review",
        "overleaf": "overleaf",
    }
    for needle, project in aliases.items():
        if needle in low:
            return project
    if "简报" in text:
        return "daily-briefing"
    if "原始数据库" in text or "daily trace" in low:
        return "daytrace"
    return None


def collect_hermes_input_events(
    day: str, sessions_dir: Path | None = None, limit: int = 500
) -> list[TraceEvent]:
    root = sessions_dir or (Path.home() / ".hermes" / "sessions")
    start_ts, end_ts = day_bounds(day)
    if not root.exists():
        return []
    events: list[TraceEvent] = []
    for path in sorted(
        root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        if len(events) >= limit:
            break
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        session_id = path.stem
        for idx, line in enumerate(lines):
            if len(events) >= limit:
                break
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("role") != "user":
                continue
            content = str(obj.get("content") or "").strip()
            if (
                not content
                or content.startswith("[IMPORTANT: Background process")
                or content.startswith("[Your active task list")
            ):
                continue
            ts_float, ts_iso = parse_ts(obj.get("timestamp"), path.stat().st_mtime)
            if not (start_ts <= ts_float <= end_ts):
                continue
            project = guess_project(content)
            eid_seed = f"hermes-input:{session_id}:{idx}:{content}"
            events.append(
                TraceEvent(
                    id="hermes-input-"
                    + hashlib.sha1(eid_seed.encode()).hexdigest()[:16],
                    source="hermes",
                    kind="user_input",
                    start=ts_iso,
                    end=None,
                    title=content.splitlines()[0][:96],
                    summary=content[:500],
                    project_guess=project,
                    confidence=0.98,
                    sensitivity="private",
                    evidence={
                        "session_id": session_id,
                        "path": str(path),
                        "line_index": idx,
                        "raw_text": content,
                        "channel": "hermes_session",
                    },
                    raw_ref=str(path),
                )
            )
    return events


def collect_hermes_final_events(
    day: str, sessions_dir: Path | None = None, limit: int = 120
) -> list[TraceEvent]:
    root = sessions_dir or (Path.home() / ".hermes" / "sessions")
    start_ts, end_ts = day_bounds(day)
    events: list[TraceEvent] = []
    if not root.exists():
        return []
    for path in sorted(
        root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        if len(events) >= limit:
            break
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        session_id = path.stem
        for idx, line in enumerate(lines):
            if len(events) >= limit:
                break
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("role") != "assistant":
                continue
            content = str(obj.get("content") or "").strip()
            if len(content) < 80 or content.startswith("[CONTEXT COMPACTION"):
                continue
            ts_float, ts_iso = parse_ts(obj.get("timestamp"), path.stat().st_mtime)
            if not (start_ts <= ts_float <= end_ts):
                continue
            if not any(
                mark in content
                for mark in ["已", "完成", "测试", "passed", "当前", "结果", "改"]
            ):
                continue
            project = guess_project(content)
            eid_seed = f"hermes-outcome:{session_id}:{idx}:{content[:200]}"
            events.append(
                TraceEvent(
                    id="hermes-outcome-"
                    + hashlib.sha1(eid_seed.encode()).hexdigest()[:16],
                    source="hermes",
                    kind="assistant_result",
                    start=ts_iso,
                    end=None,
                    title=content.splitlines()[0].replace("#", "").strip()[:110]
                    or "Hermes result summary",
                    summary=content[:700],
                    project_guess=project,
                    confidence=0.72,
                    sensitivity="private",
                    evidence={
                        "session_id": session_id,
                        "path": str(path),
                        "line_index": idx,
                        "result_text": content[:1600],
                    },
                    raw_ref=str(path),
                )
            )
    return events


def collect_hermes_events(
    day: str, sessions_dir: Path | None = None, limit: int = 620
) -> list[TraceEvent]:
    inputs = collect_hermes_input_events(day, sessions_dir, limit=max(0, limit - 120))
    outcomes = collect_hermes_final_events(day, sessions_dir, limit=120)
    return (inputs + outcomes)[:limit]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--sessions-dir")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=620)
    args = parser.parse_args()
    events = collect_hermes_events(
        args.date,
        Path(args.sessions_dir).expanduser() if args.sessions_dir else None,
        args.limit,
    )
    write_events(args.out, events)
    print(f"wrote {len(events)} Hermes input/outcome events to {args.out}")


if __name__ == "__main__":
    main()
