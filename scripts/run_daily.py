#!/usr/bin/env python3
"""Single-entrypoint daily runner — wires together collect → upload → import
→ regenerate so cron / launchd doesn't have to call five separate scripts.

Two modes, picked via `--role`:

  branch  : collect events from a device config, upload to Feishu Drive.
            Used on satellite machines (omen-wsl, ipad-via-shortcut, etc.)
            that produce data but don't keep the DB.

  hub     : collect own events too (this Mac is also a producer), upload,
            pull every other device's day, import the inbox, regen all
            stats + AI for the day. End-to-end nightly.

By default `--date` is "yesterday under the shifted-day boundary" (so a
run scheduled for 04:30 picks up the work day that just ended at 04:00).

Designed to be cron-safe:
  - idempotent: re-running for the same day is a no-op for cached stages
  - safe-to-skip: each stage logs + continues on failure
  - one-line invocation so launchd / cron stays simple
  - log to stdout (cron captures), plus optional --log-file
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time
from datetime import date as _date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _now_local_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Step:
    """Tiny wrapper to time + report each stage with a uniform prefix.

    The `crash` knob controls whether a failure aborts the whole run or just
    logs and continues — branch mode is more tolerant (collector glitches
    shouldn't block uploads), hub mode is stricter (DB corruption should
    fail loud)."""

    def __init__(self, name: str, *, crash: bool = False):
        self.name = name
        self.crash = crash

    def __enter__(self):
        self.t0 = time.time()
        print(f"[{_now_local_iso()}] ▶ {self.name}", flush=True)
        return self

    def __exit__(self, exc_type, exc, tb):
        dur = time.time() - self.t0
        if exc is None:
            print(f"[{_now_local_iso()}] ✓ {self.name}  ({dur:.1f}s)", flush=True)
            return False
        print(f"[{_now_local_iso()}] ✗ {self.name}  ({dur:.1f}s) — {exc_type.__name__}: {exc}", flush=True)
        return not self.crash   # swallow if crash=False


def run_cmd(cmd: list[str], *, env: dict | None = None) -> int:
    print(f"  $ {shlex.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, env={**os.environ, **(env or {})}, cwd=REPO_ROOT)
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd)
    return proc.returncode


def yesterday_shifted() -> str:
    """The date a 04:00-boundary day belongs to at run time.

    Run at 04:30 → returns yesterday's calendar date (since the day that
    just ended at 04:00 of *today* is named after yesterday). Run at
    21:00 → returns today's calendar date (today's day is in progress)."""
    from daytrace import stats
    now = datetime.now()
    boundary = stats.DAY_BOUNDARY_HOUR
    if now.hour < boundary:
        return (now.date() - timedelta(days=1)).isoformat()
    return now.date().isoformat()


def cmd_branch(args: argparse.Namespace) -> int:
    """Collect + upload. Doesn't touch the local DB."""
    date = args.date or yesterday_shifted()
    config = args.config
    inbox_token = args.inbox_token
    identity = args.as_identity
    print(f"branch run: date={date} config={config} identity={identity}", flush=True)

    with Step("collect", crash=False):
        run_cmd(["python3", "scripts/collect_from_config.py",
                 "--config", config, "--date", date])

    with Step("upload-to-feishu-drive", crash=False):
        run_cmd([
            "python3", "scripts/feishu_drive_sync.py",
            "--inbox-token", inbox_token,
            "--as", identity,
            "upload-date",
            "--config", config,
            "--date", date,
            "--if-exists", "overwrite",
        ])
    return 0


def cmd_hub(args: argparse.Namespace) -> int:
    """Branch work + pull every other device + import + regen."""
    date = args.date or yesterday_shifted()
    config = args.config
    inbox_token = args.inbox_token
    identity = args.as_identity
    other_devices = args.pull_devices
    print(f"hub run: date={date} config={config} pull={other_devices}", flush=True)

    # 1) Be a branch for our own machine
    with Step("collect-own", crash=False):
        run_cmd(["python3", "scripts/collect_from_config.py",
                 "--config", config, "--date", date])

    with Step("upload-own", crash=False):
        run_cmd([
            "python3", "scripts/feishu_drive_sync.py",
            "--inbox-token", inbox_token,
            "--as", identity,
            "upload-date",
            "--config", config,
            "--date", date,
            "--if-exists", "overwrite",
        ])

    # 2) Pull every other device's contribution to the same date
    for dev in other_devices:
        with Step(f"pull/{dev}", crash=False):
            run_cmd([
                "python3", "scripts/feishu_drive_sync.py",
                "--inbox-token", inbox_token,
                "--as", identity,
                "pull",
                "--device", dev,
                "--date", date,
                "--force",   # we want today's data even if ledger says pulled
            ])

    # 3) Import every jsonl that landed in inbox into the SQLite DB
    with Step("import-inbox", crash=True):  # this one we want loud failures
        run_cmd(["python3", "scripts/import_inbox.py"])

    # 4) Regenerate stats + AI for the day (also makes report card up-to-date)
    with Step("regenerate-day-report", crash=True):
        from daytrace.db import connect, init_db
        from daytrace.daily_report import regenerate_day_from_db
        con = connect("data/daytrace.sqlite"); init_db(con)
        rep = regenerate_day_from_db(con, date, include_ai=True)
        cost = con.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM day_channel "
            "WHERE date = ? AND generator = 'ai'", (date,)
        ).fetchone()[0]
        print(f"    events={rep.total_events}  ai_cost=${cost:.4f}", flush=True)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="DayTrace daily runner — single entrypoint for cron / launchd."
    )
    parser.add_argument(
        "--log-file",
        help="if set, ALSO mirror all output to this file (cron usually captures stdout already)",
    )
    sub = parser.add_subparsers(dest="role", required=True)

    b = sub.add_parser("branch", help="collect + upload (satellite machine)")
    b.add_argument("--config", required=True, help="device config YAML")
    b.add_argument("--date", help="YYYY-MM-DD; defaults to shifted-day yesterday")
    b.add_argument("--inbox-token", required=True, help="Feishu Drive folder token")
    b.add_argument("--as", dest="as_identity", default="user", choices=["bot", "user"])
    b.set_defaults(func=cmd_branch)

    h = sub.add_parser("hub", help="branch work + pull + import + regen")
    h.add_argument("--config", required=True, help="this hub's own device config YAML")
    h.add_argument("--date", help="YYYY-MM-DD; defaults to shifted-day yesterday")
    h.add_argument("--inbox-token", required=True, help="Feishu Drive folder token")
    h.add_argument("--as", dest="as_identity", default="user", choices=["bot", "user"])
    h.add_argument(
        "--pull-devices", nargs="*", default=[],
        help="device IDs to pull (e.g. omen-wsl)",
    )
    h.set_defaults(func=cmd_hub)

    args = parser.parse_args()

    # Optional log-file tee
    if args.log_file:
        log_path = Path(args.log_file).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = _Tee(sys.stdout, open(log_path, "a", buffering=1))
        sys.stderr = sys.stdout

    print(f"=== DayTrace daily run @ {_now_local_iso()} (role={args.role}) ===", flush=True)
    try:
        return args.func(args) or 0
    except subprocess.CalledProcessError as exc:
        print(f"!!! aborted: {exc}", flush=True)
        return exc.returncode or 1
    except Exception as exc:
        print(f"!!! aborted: {type(exc).__name__}: {exc}", flush=True)
        return 1


class _Tee:
    def __init__(self, *streams):
        self._streams = streams
    def write(self, s):
        for st in self._streams:
            try: st.write(s); st.flush()
            except Exception: pass
    def flush(self):
        for st in self._streams:
            try: st.flush()
            except Exception: pass
    def __getattr__(self, name):
        return getattr(self._streams[0], name)


if __name__ == "__main__":
    sys.exit(main())
