#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
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


def project_from_chat_name(chat_name: str | None) -> str | None:
    if not chat_name:
        return None
    name = chat_name.strip()
    if not name:
        return None
    lower = name.lower()
    if lower.startswith("hermes -"):
        return name.split("-", 1)[1].strip() or None
    if lower.startswith("hermes-"):
        return name.split("-", 1)[1].strip() or None
    if lower.startswith("hermes —"):
        return name.split("—", 1)[1].strip() or None
    return name


def session_sidecar_path(root: Path, session_id: str) -> Path:
    return root / f"session_{session_id}.json"


def load_channel_names() -> dict[str, str]:
    directory = Path.home() / ".hermes" / "channel_directory.json"
    if not directory.exists():
        return {}
    try:
        payload = json.loads(directory.read_text(encoding="utf-8"))
    except Exception:
        return {}
    names: dict[str, str] = {}
    platforms = payload.get("platforms") if isinstance(payload, dict) else None
    feishu_channels = platforms.get("feishu") if isinstance(platforms, dict) else None
    if not isinstance(feishu_channels, list):
        return names
    for channel in feishu_channels:
        if not isinstance(channel, dict) or channel.get("type") != "group":
            continue
        channel_id = str(channel.get("id") or "")
        channel_name = str(channel.get("name") or "")
        if channel_id and channel_name:
            names[channel_id] = channel_name
    return names


def infer_origin_from_session_text(root: Path, session_id: str) -> dict[str, str | None]:
    text_parts = []
    for path in [session_sidecar_path(root, session_id), root / f"{session_id}.jsonl"]:
        if path.exists():
            try:
                text_parts.append(path.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass
    text = "\n".join(text_parts)
    if not text:
        return {"chat_name": None, "chat_id": None, "chat_type": None}

    # Best case: Hermes stored or reasoned over the current session context.
    patterns = [
        r"Source:\s*Feishu\s*\(group:\s*([^\)]+)\)",
        r"Source\s+Feishu\s*\(group:\s*([^\)]+)\)",
        r"Current Session Context[^\n]{0,200}?group:\s*([^\)\n]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            name = match.group(1).strip().strip("`* ")
            if name.lower().startswith("hermes"):
                return {"chat_name": name, "chat_id": None, "chat_type": "group"}

    channel_names = load_channel_names()
    id_counts = {
        channel_id: text.count(channel_id)
        for channel_id, name in channel_names.items()
        if name.lower().startswith("hermes")
    }
    id_counts = {channel_id: count for channel_id, count in id_counts.items() if count > 0}
    if id_counts:
        best_id, best_count = max(id_counts.items(), key=lambda item: item[1])
        tied = [channel_id for channel_id, count in id_counts.items() if count == best_count]
        if len(tied) == 1:
            return {
                "chat_name": channel_names[best_id],
                "chat_id": best_id,
                "chat_type": "group",
            }

    name_counts = {
        name: text.count(name)
        for name in channel_names.values()
        if name.lower().startswith("hermes")
    }
    name_counts = {name: count for name, count in name_counts.items() if count > 0}
    if name_counts:
        best_name, best_count = max(name_counts.items(), key=lambda item: item[1])
        tied = [name for name, count in name_counts.items() if count == best_count]
        if len(tied) == 1:
            return {"chat_name": best_name, "chat_id": None, "chat_type": "group"}

    return {"chat_name": None, "chat_id": None, "chat_type": None}


def load_session_origin(root: Path, session_id: str) -> dict[str, str | None]:
    origin: dict[str, str | None] = {"chat_name": None, "chat_id": None, "chat_type": None}
    sessions_index = root / "sessions.json"
    if sessions_index.exists():
        try:
            sessions = json.loads(sessions_index.read_text(encoding="utf-8"))
            for item in sessions.values():
                if not isinstance(item, dict) or item.get("session_id") != session_id:
                    continue
                raw_origin = item.get("origin")
                item_origin = raw_origin if isinstance(raw_origin, dict) else {}
                origin["chat_name"] = str(
                    item.get("display_name") or item_origin.get("chat_name") or ""
                ) or None
                origin["chat_id"] = str(item_origin.get("chat_id") or "") or None
                origin["chat_type"] = str(
                    item_origin.get("chat_type") or item.get("chat_type") or ""
                ) or None
                return origin
        except Exception:
            pass

    sidecar = session_sidecar_path(root, session_id)
    if sidecar.exists():
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
            origin["chat_name"] = str(meta.get("display_name") or meta.get("chat_name") or "") or None
            origin["chat_id"] = str(meta.get("chat_id") or "") or None
            origin["chat_type"] = str(meta.get("chat_type") or "") or None
        except Exception:
            pass
    if origin.get("chat_name"):
        return origin
    inferred = infer_origin_from_session_text(root, session_id)
    for key, value in inferred.items():
        if value and not origin.get(key):
            origin[key] = value
    return origin


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
        origin = load_session_origin(root, session_id)
        chat_name = origin.get("chat_name")
        chat_project = project_from_chat_name(chat_name)
        if not chat_name or not chat_name.lower().startswith("hermes") or not chat_project:
            continue
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
            project = chat_project or guess_project(content)
            confidence = 0.99 if chat_project else 0.72
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
                    summary=content,
                    project_guess=project,
                    confidence=confidence,
                    sensitivity="private",
                    evidence={
                        "session_id": session_id,
                        "path": str(path),
                        "line_index": idx,
                        "raw_text": content,
                        "channel": "hermes_session",
                        "chat_name": chat_name,
                        "chat_id": origin.get("chat_id"),
                        "chat_type": origin.get("chat_type"),
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
    return collect_hermes_input_events(day, sessions_dir, limit=limit)


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
    print(f"wrote {len(events)} Hermes user-input events to {args.out}")


if __name__ == "__main__":
    main()
