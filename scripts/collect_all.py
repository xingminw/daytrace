#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
LOCAL_TZ = ZoneInfo("America/Detroit")


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect DayTrace stable sources: Activity, GitHub, Hermes, and Codex."
    )
    parser.add_argument("--date", default=datetime.now(LOCAL_TZ).date().isoformat())
    parser.add_argument("--db", default="data/daytrace.sqlite")
    parser.add_argument(
        "--clear-db",
        action="store_true",
        help="Delete the existing SQLite DB before import.",
    )
    parser.add_argument("--projects-root", default=str(Path.home() / "Projects"))
    parser.add_argument("--activity-seconds", type=int, default=3)
    parser.add_argument("--activity-interval", type=int, default=3)
    parser.add_argument(
        "--with-screenshot",
        action="store_true",
        help="Attempt screencapture into outputs/activity-screens/<date>.",
    )
    args = parser.parse_args()

    day = args.date
    events_dir = ROOT / "events"
    events_dir.mkdir(exist_ok=True)
    outputs_dir = ROOT / "outputs" / "activity-screens" / day
    screenshot_args = (
        ["--screenshot-dir", str(outputs_dir)] if args.with_screenshot else []
    )

    files = {
        "codex": f"events/codex-{day}.jsonl",
        "hermes": f"events/hermes-{day}.jsonl",
        "github": f"events/github-{day}.jsonl",
        "activity": f"events/activity-{day}.jsonl",
    }

    py = sys.executable
    run(
        [
            py,
            "scripts/collect_codex.py",
            "--date",
            day,
            "--out",
            files["codex"],
            "--limit",
            "600",
        ]
    )
    run(
        [
            py,
            "scripts/collect_hermes_sessions.py",
            "--date",
            day,
            "--out",
            files["hermes"],
            "--limit",
            "700",
        ]
    )
    run(
        [
            py,
            "scripts/collect_github.py",
            "--date",
            day,
            "--out",
            files["github"],
            "--limit",
            "800",
        ]
    )
    run(
        [
            py,
            "scripts/collect_macos_activity.py",
            "--duration-seconds",
            str(args.activity_seconds),
            "--interval-seconds",
            str(args.activity_interval),
            *screenshot_args,
            "--out",
            files["activity"],
        ]
    )
    db = ROOT / args.db
    if args.clear_db:
        for suffix in ["", "-wal", "-shm"]:
            path = Path(str(db) + suffix)
            if path.exists():
                path.unlink()
                print(f"deleted {path}")

    run(
        [
            py,
            "scripts/build_database.py",
            "--date",
            day,
            "--db",
            args.db,
            "--events",
            files["codex"],
            files["hermes"],
            files["github"],
            files["activity"],
        ]
    )


if __name__ == "__main__":
    main()
