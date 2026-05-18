#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daytrace.collector_config import (  # noqa: E402
    CollectorConfigError,
    enabled_source,
    ensure_safe_id,
    expand_path,
    load_collector_config,
    stamp_events,
)
from daytrace.io import write_events  # noqa: E402
from daytrace.schema import TraceEvent  # noqa: E402
from scripts.collect_codex import collect_codex_events  # noqa: E402
from scripts.collect_git import collect_git_events  # noqa: E402
from scripts.collect_hermes_sessions import collect_hermes_events  # noqa: E402
from scripts.collect_claude_code import collect_claude_code_events  # noqa: E402

LOCAL_TZ = ZoneInfo("America/Detroit")


def iter_days(end_day: str, lookback_days: int) -> list[str]:
    end = date.fromisoformat(end_day)
    return [
        (end - timedelta(days=offset)).isoformat()
        for offset in range(max(lookback_days, 1) - 1, -1, -1)
    ]


def source_limit(source_config: dict, default: int) -> int:
    try:
        return int(source_config.get("limit", default))
    except Exception:
        return default


def collect_source_for_day(
    source_name: str, source_config: dict, day: str
) -> list[TraceEvent]:
    if source_name == "codex":
        codex_home = source_config.get("home") or source_config.get("codex_home")
        return collect_codex_events(
            day,
            expand_path(codex_home) if codex_home else None,
            source_limit(source_config, 500),
        )
    if source_name == "hermes":
        sessions_dir = source_config.get("sessions_dir")
        return collect_hermes_events(
            day,
            expand_path(sessions_dir) if sessions_dir else None,
            source_limit(source_config, 620),
        )
    if source_name == "git":
        roots = [expand_path(p) for p in source_config.get("roots", [])]
        repos = [expand_path(p) for p in source_config.get("repos", [])]
        if not roots and not repos:
            raise CollectorConfigError("git source requires explicit roots or repos")
        return collect_git_events(day, [*roots, *repos], source_limit(source_config, 200))
    if source_name == "claude_code":
        claude_home = source_config.get("home") or source_config.get("projects_dir")
        return collect_claude_code_events(
            day,
            expand_path(claude_home) if claude_home else None,
            source_limit(source_config, 800),
        )
    raise ValueError(f"unsupported source: {source_name}")


def collect_configured(
    config_path: Path,
    end_day: str,
    lookback_days: int,
    out_dir: Path,
) -> dict:
    config = load_collector_config(config_path)
    device_id = ensure_safe_id(config["device"]["id"], "device.id")
    batch_id = f"{device_id}-{end_day}-lookback-{lookback_days}d"
    source_names = ["codex", "hermes", "git", "claude_code"]
    manifest = {
        "schema_version": "daytrace.collector_manifest.v1",
        "batch_id": batch_id,
        "config_path": str(config_path),
        "device_id": device_id,
        "collector_id": str(config["device"]["collector_id"]),
        "location_id": str(config["device"].get("location_id") or "unknown"),
        "created_at": datetime.now(LOCAL_TZ).isoformat(timespec="seconds"),
        "lookback_days": lookback_days,
        "end_day": end_day,
        "files": [],
        "total_events": 0,
    }
    for day in iter_days(end_day, lookback_days):
        for source_name in source_names:
            source_config = enabled_source(config, source_name)
            if source_config is None:
                continue
            events = collect_source_for_day(source_name, source_config, day)
            stamped = stamp_events(events, config)
            rel = Path(device_id) / day / f"{source_name}.jsonl"
            out_path = out_dir / rel
            write_events(out_path, stamped)
            manifest["files"].append(
                {
                    "source": source_name,
                    "date": day,
                    "path": str(rel),
                    "event_count": len(stamped),
                }
            )
            manifest["total_events"] += len(stamped)
    manifest_path = out_dir / device_id / end_day / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect DayTrace events from local configured sources for any device."
    )
    parser.add_argument("--config", required=True, help="YAML/JSON collector config")
    parser.add_argument(
        "--date", default=datetime.now(LOCAL_TZ).date().isoformat(), help="end date"
    )
    parser.add_argument("--lookback-days", type=int, default=1)
    parser.add_argument("--out-dir", default="inbox")
    args = parser.parse_args()
    manifest = collect_configured(
        Path(args.config).expanduser(),
        args.date,
        args.lookback_days,
        Path(args.out_dir).expanduser(),
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
