#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daytrace.io import write_events
from daytrace.schema import TraceEvent


def run(cmd: list[str], timeout: int = 5) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def frontmost_app() -> str:
    code, out, _ = run(
        [
            "osascript",
            "-e",
            'tell application "System Events" to get name of first application process whose frontmost is true',
        ]
    )
    return out or "unknown"


def front_window_title() -> str:
    script = """
    tell application "System Events"
      set frontProc to first application process whose frontmost is true
      try
        return name of front window of frontProc
      on error
        return ""
      end try
    end tell
    """
    _, out, _ = run(["osascript", "-e", script])
    return out


def parse_idle_seconds(ioreg_output: str) -> float | None:
    m = re.search(r'HIDIdleTime" = (\d+)', ioreg_output)
    if not m:
        return None
    return int(m.group(1)) / 1_000_000_000


def idle_seconds() -> float | None:
    code, out, _ = run(["ioreg", "-c", "IOHIDSystem"])
    return parse_idle_seconds(out) if code == 0 else None


def screenshot(out_dir: Path | None) -> str | None:
    if out_dir is None:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ("screen-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".jpg")
    # -x: no sound. This may fail if Screen Recording permission is not granted.
    code, _, err = run(["screencapture", "-x", "-t", "jpg", str(path)], timeout=10)
    if code != 0 or not path.exists() or path.stat().st_size == 0:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        return None
    return str(path)


def guess_project(app: str, window_title: str) -> str | None:
    text = f"{app} {window_title}".lower()
    for name in [
        "daytrace",
        "daily-briefing",
        "loft-sim",
        "baidu-signal-paper",
        "paper-review",
        "overleaf",
    ]:
        if name in text:
            return name
    if "daily briefing" in text:
        return "daily-briefing"
    return None


def collect_samples(
    duration_seconds: int, interval_seconds: int, screenshot_dir: Path | None = None
) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    deadline = time.time() + max(1, duration_seconds)
    while time.time() < deadline or not events:
        now = datetime.now().isoformat(timespec="seconds")
        app = frontmost_app()
        title = front_window_title()
        idle = idle_seconds()
        active = idle is None or idle < 300
        shot = screenshot(screenshot_dir)
        project = guess_project(app, title)
        summary = f"Active app={app}; window={title or 'unknown'}; idle_seconds={idle}; screenshot={'yes' if shot else 'no'}"
        eid = (
            "activity-" + hashlib.sha1(f"{now}-{app}-{title}".encode()).hexdigest()[:16]
        )
        events.append(
            TraceEvent(
                id=eid,
                source="activity",
                kind="app_screen_sample",
                start=now,
                end=None,
                title=f"{app}: {title or 'frontmost window'}",
                summary=summary,
                project_guess=project,
                sensitivity="private" if shot else "normal",
                evidence={
                    "app": app,
                    "window_title": title,
                    "idle_seconds": idle,
                    "active": active,
                    "screenshot_path": shot,
                    "notes": "Activity context only: no keystroke contents are recorded.",
                },
            )
        )
        if time.time() + interval_seconds > deadline:
            break
        time.sleep(interval_seconds)
    return events


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-seconds", type=int, default=5)
    parser.add_argument("--interval-seconds", type=int, default=5)
    parser.add_argument("--screenshot-dir")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    events = collect_samples(
        args.duration_seconds,
        args.interval_seconds,
        Path(args.screenshot_dir).expanduser() if args.screenshot_dir else None,
    )
    write_events(args.out, events)
    print(f"wrote {len(events)} activity context samples to {args.out}")


if __name__ == "__main__":
    main()
