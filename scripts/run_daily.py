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


def cmd_status(args: argparse.Namespace) -> int:
    """Dry-run: print which dates would be (re-)processed if catchup ran now.

    Designed for external schedulers (Hermes / cron) that want to ask
    "is there work to do?" without actually doing it. Output is JSON on
    stdout so the caller can parse it.
    """
    import json
    from daytrace.db import connect, init_db
    from daytrace.daily_report import pending_dates

    con = connect(args.db); init_db(con)
    plan = pending_dates(
        con,
        target_date=args.date,
        lookback_days=args.lookback_days,
        always_redo_recent=args.always_redo_recent,
    )
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0 if plan["to_run"] else 0  # status command never fails


def cmd_catchup(args: argparse.Namespace) -> int:
    """For each date that needs (re-)processing, run the hub pipeline.

    Idempotent: cache hits make re-running a settled date essentially
    free; new events get picked up via the events_hash check. Safe to
    invoke every night even if nothing changed.
    """
    from daytrace.db import connect, init_db
    from daytrace.daily_report import pending_dates

    con = connect(args.db); init_db(con)
    plan = pending_dates(
        con,
        target_date=args.date,
        lookback_days=args.lookback_days,
        always_redo_recent=args.always_redo_recent,
    )
    to_run = plan["to_run"]
    if not to_run:
        print(f"catchup: nothing to do (target={plan['target_date']})", flush=True)
        return 0

    print(
        f"catchup: target={plan['target_date']}  to_run={to_run}  "
        f"(missing={plan['missing']}, stale={plan['stale']})",
        flush=True,
    )

    failures = []
    for d in to_run:
        print(f"\n──────── {d} ────────", flush=True)
        sub = argparse.Namespace(
            date=d,
            config=args.config,
            inbox_token=args.inbox_token,
            as_identity=args.as_identity,
            pull_devices=args.pull_devices,
        )
        try:
            cmd_hub(sub)
        except Exception as e:
            print(f"!!! day {d} failed: {type(e).__name__}: {e}", flush=True)
            failures.append(d)

    print(
        f"\ncatchup done: {len(to_run) - len(failures)}/{len(to_run)} succeeded"
        + (f", failed: {failures}" if failures else ""),
        flush=True,
    )
    return 1 if failures else 0


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

    h = sub.add_parser("hub", help="branch work + pull + import + regen (single date)")
    h.add_argument("--config", required=True, help="this hub's own device config YAML")
    h.add_argument("--date", help="YYYY-MM-DD; defaults to shifted-day yesterday")
    h.add_argument("--inbox-token", required=True, help="Feishu Drive folder token")
    h.add_argument("--as", dest="as_identity", default="user", choices=["bot", "user"])
    h.add_argument(
        "--pull-devices", nargs="*", default=[],
        help="device IDs to pull (e.g. omen-wsl)",
    )
    h.set_defaults(func=cmd_hub)

    # `status` and `catchup` are designed for an external scheduler (Hermes,
    # cron, launchd) to ask "what's pending?" and "do all of it" without
    # threading per-day flags. Idempotent — safe to run on any cadence.
    s = sub.add_parser("status", help="dry-run: which dates would be (re-)processed?")
    s.add_argument("--db", default="data/daytrace.sqlite")
    s.add_argument("--date", help="YYYY-MM-DD target; defaults to shifted-day yesterday")
    s.add_argument("--lookback-days", type=int, default=7,
                   help="scan this many days back for stale/missing dates")
    s.add_argument("--always-redo-recent", type=int, default=2,
                   help="always re-run the N most recent days (cache makes this cheap)")
    s.set_defaults(func=cmd_status)

    c = sub.add_parser("catchup", help="run every pending date (use this from cron / Hermes)")
    c.add_argument("--config", required=True, help="this hub's own device config YAML")
    c.add_argument("--db", default="data/daytrace.sqlite")
    c.add_argument("--inbox-token", required=True, help="Feishu Drive folder token")
    c.add_argument("--as", dest="as_identity", default="user", choices=["bot", "user"])
    c.add_argument(
        "--pull-devices", nargs="*", default=[],
        help="device IDs to pull (e.g. omen-wsl)",
    )
    c.add_argument("--date", help="YYYY-MM-DD target; defaults to shifted-day yesterday")
    c.add_argument("--lookback-days", type=int, default=7,
                   help="scan this many days back for stale/missing dates")
    c.add_argument("--always-redo-recent", type=int, default=2,
                   help="always re-run the N most recent days (cache-cheap)")
    c.set_defaults(func=cmd_catchup)

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
