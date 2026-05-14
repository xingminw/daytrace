#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
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


def iso_from_epoch(value: str | int | float | None) -> str:
    try:
        ts = float(value or 0)
    except Exception:
        ts = 0
    if ts > 10_000_000_000:  # milliseconds
        ts = ts / 1000
    return (
        datetime.fromtimestamp(ts, tz=LOCAL_TZ)
        .replace(tzinfo=None)
        .isoformat(timespec="seconds")
    )


def iso_to_epoch(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def guess_project(cwd: str | None, text: str = "") -> str | None:
    if cwd:
        parts = Path(cwd).parts
        if "Projects" in parts:
            i = parts.index("Projects")
            if i + 1 < len(parts):
                return parts[i + 1]
    low = text.lower()
    for name in [
        "daytrace",
        "daily-briefing",
        "loft-sim",
        "baidu-signal-paper",
        "paper-review",
        "overleaf",
    ]:
        if name in low:
            return name
    if "daily briefing" in low:
        return "daily-briefing"
    return None


def load_threads(state_db: Path) -> dict[str, dict]:
    if not state_db.exists():
        return {}
    con = sqlite3.connect(state_db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT id, rollout_path, created_at, updated_at, source, cwd, title,
               first_user_message, thread_source, model
        FROM threads
        """
    ).fetchall()
    return {row["id"]: dict(row) for row in rows}


def collect_history(day: str, codex_home: Path, limit: int) -> list[TraceEvent]:
    history_path = codex_home / "history.jsonl"
    state_db = codex_home / "state_5.sqlite"
    threads = load_threads(state_db)
    start_ts, end_ts = day_bounds(day)
    events: list[TraceEvent] = []
    if not history_path.exists():
        return events
    for line in history_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if len(events) >= limit:
            break
        try:
            obj = json.loads(line)
        except Exception:
            continue
        session_id = str(obj.get("session_id") or "")
        raw_text = str(obj.get("text") or "").strip()
        ts_raw = obj.get("ts")
        try:
            ts = float(ts_raw)
        except Exception:
            continue
        if not (start_ts <= ts <= end_ts) or not raw_text:
            continue
        thread = threads.get(session_id, {})
        cwd = thread.get("cwd")
        project = guess_project(cwd, raw_text)
        channel = thread.get("source") or "codex"
        when = iso_from_epoch(ts)
        eid_seed = f"codex-input:{session_id}:{ts_raw}:{raw_text}"
        events.append(
            TraceEvent(
                id="codex-input-" + hashlib.sha1(eid_seed.encode()).hexdigest()[:16],
                source="codex",
                kind="user_input",
                start=when,
                end=None,
                title=(raw_text.splitlines()[0][:96] or "Codex user input"),
                summary=raw_text[:500],
                project_guess=project,
                confidence=0.98,
                sensitivity="private",
                evidence={
                    "session_id": session_id,
                    "channel": channel,
                    "cwd": cwd,
                    "raw_text": raw_text,
                    "thread_title": thread.get("title"),
                    "rollout_path": thread.get("rollout_path"),
                },
                raw_ref=str(history_path),
            )
        )
    return events


def collect_thread_summaries(
    day: str, codex_home: Path, existing: int, limit: int
) -> list[TraceEvent]:
    threads = load_threads(codex_home / "state_5.sqlite")
    start_ts, end_ts = day_bounds(day)
    events: list[TraceEvent] = []
    for tid, thread in sorted(
        threads.items(), key=lambda kv: kv[1].get("updated_at") or 0, reverse=True
    ):
        if existing + len(events) >= limit:
            break
        ts = float(thread.get("created_at") or 0)
        if not (start_ts <= ts <= end_ts):
            continue
        first = str(
            thread.get("first_user_message") or thread.get("title") or ""
        ).strip()
        thread_source = str(thread.get("thread_source") or "")
        source_repr = str(thread.get("source") or "")
        if not first:
            continue
        if (
            thread_source == "subagent"
            or "subagent" in source_repr
            or first.startswith("The following is the Codex agent history")
        ):
            continue
        cwd = thread.get("cwd")
        project = guess_project(cwd, first)
        events.append(
            TraceEvent(
                id="codex-thread-" + hashlib.sha1(tid.encode()).hexdigest()[:16],
                source="codex",
                kind="thread_started",
                start=iso_from_epoch(thread.get("created_at")),
                end=iso_from_epoch(thread.get("updated_at"))
                if thread.get("updated_at")
                else None,
                title=str(thread.get("title") or first).splitlines()[0][:120],
                summary=first[:500],
                project_guess=project,
                confidence=0.9,
                sensitivity="private",
                evidence={
                    "thread_id": tid,
                    "source": thread.get("source"),
                    "thread_source": thread.get("thread_source"),
                    "cwd": cwd,
                    "first_user_message": first,
                    "rollout_path": thread.get("rollout_path"),
                },
                raw_ref=thread.get("rollout_path"),
            )
        )
    return events


def collect_rollout_user_messages(
    day: str, codex_home: Path, existing: int, limit: int
) -> list[TraceEvent]:
    """Collect Codex App / VS Code user prompts from rollout JSONL transcripts.

    history.jsonl is sparse for Codex Desktop/App sessions. Rollout files contain
    explicit event_msg:user_message records with timestamps; those are the stable
    user-initiated Codex App facts we want for DayTrace. Subagent/guardian threads
    are skipped because they are internal review noise, not user actions.
    """
    threads = load_threads(codex_home / "state_5.sqlite")
    start_ts, end_ts = day_bounds(day)
    events: list[TraceEvent] = []
    for tid, thread in sorted(
        threads.items(), key=lambda kv: kv[1].get("updated_at") or 0, reverse=True
    ):
        if existing + len(events) >= limit:
            break
        created = float(thread.get("created_at") or 0)
        updated = float(thread.get("updated_at") or created)
        if updated < start_ts or created > end_ts:
            continue
        thread_source = str(thread.get("thread_source") or "")
        source_repr = str(thread.get("source") or "")
        if thread_source == "subagent" or "subagent" in source_repr:
            continue
        rollout_path = thread.get("rollout_path")
        if not rollout_path:
            continue
        path = Path(str(rollout_path))
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        cwd = thread.get("cwd")
        for idx, line in enumerate(lines):
            if existing + len(events) >= limit:
                break
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("type") != "event_msg":
                continue
            payload = obj.get("payload") or {}
            if payload.get("type") != "user_message":
                continue
            raw_text = str(payload.get("message") or "").strip()
            if not raw_text or raw_text.startswith("<environment_context>"):
                continue
            ts = iso_to_epoch(obj.get("timestamp"))
            if ts is None or not (start_ts <= ts <= end_ts):
                continue
            when = iso_from_epoch(ts)
            eid_seed = f"codex-app-input:{tid}:{idx}:{when}:{raw_text}"
            events.append(
                TraceEvent(
                    id="codex-app-input-"
                    + hashlib.sha1(eid_seed.encode()).hexdigest()[:16],
                    source="codex",
                    kind="user_input",
                    start=when,
                    end=None,
                    title=(raw_text.splitlines()[0][:96] or "Codex App user input"),
                    summary=raw_text[:500],
                    project_guess=guess_project(cwd, raw_text),
                    confidence=0.98,
                    sensitivity="private",
                    evidence={
                        "session_id": tid,
                        "channel": thread.get("source") or "codex_app",
                        "originator": "Codex App / VS Code rollout",
                        "cwd": cwd,
                        "raw_text": raw_text,
                        "thread_title": thread.get("title"),
                        "rollout_path": str(path),
                        "rollout_line": idx,
                    },
                    raw_ref=str(path),
                )
            )
    return events


def collect_codex_events(
    day: str, codex_home: Path | None = None, limit: int = 500
) -> list[TraceEvent]:
    root = codex_home or (Path.home() / ".codex")
    events = collect_history(day, root, limit)
    events.extend(collect_rollout_user_messages(day, root, len(events), limit))
    events.extend(collect_thread_summaries(day, root, len(events), limit))
    # De-dupe by id while preserving order.
    seen = set()
    out = []
    for e in events:
        if e.id not in seen:
            seen.add(e.id)
            out.append(e)
    return out[:limit]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--codex-home")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()
    events = collect_codex_events(
        args.date,
        Path(args.codex_home).expanduser() if args.codex_home else None,
        args.limit,
    )
    write_events(args.out, events)
    print(f"wrote {len(events)} Codex input/thread events to {args.out}")


if __name__ == "__main__":
    main()
