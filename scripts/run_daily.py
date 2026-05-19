#!/usr/bin/env python3
"""Single-entrypoint daily runner — wires together collect → ssh+rsync pull
→ import → regenerate so cron / launchd doesn't have to call five scripts.

Subcommands:

  status   : dry-run; print which dates would be (re-)processed and, with
             --devices, the per-device pull matrix. JSON on stdout so a
             scheduler can parse it.

  catchup  : the daily entrypoint. For every pending (device, date) pair
             (per device_pull_log), either run collect_from_config locally
             or ssh into the remote, run it there, and rsync the inbox slice
             back. Then import everything and regenerate any day whose
             events_hash changed.

By default `--date` is "yesterday under the shifted-day boundary" (so a
run scheduled for 04:30 picks up the work day that just ended at 04:00).

Designed to be cron-safe:
  - idempotent: re-running for the same day is a no-op for cached stages
  - per-device, per-day state in device_pull_log: a remote offline today
    is retried tomorrow without losing visibility
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

# Per-machine inbox lives under data/ now (used to be repo-root inbox/).
# Same on every remote after `run_daily.py deploy` syncs this file.
INBOX_ROOT = REPO_ROOT / "data" / "inbox"


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


def cmd_status(args: argparse.Namespace) -> int:
    """Dry-run: print which dates would be (re-)processed if catchup ran now.

    Designed for external schedulers (Hermes / cron) that want to ask
    "is there work to do?" without actually doing it. Output is JSON on
    stdout so the caller can parse it.
    """
    import json
    from daytrace.db import connect, init_db
    from daytrace.daily_report import pending_dates, pull_status_matrix

    con = connect(args.db); init_db(con)
    plan = pending_dates(
        con,
        target_date=args.date,
        lookback_days=args.lookback_days,
        always_redo_recent=args.always_redo_recent,
    )
    out = {"pending_dates": plan}

    # Per-device pull matrix, if any devices were specified.
    if args.devices:
        out["device_pulls"] = pull_status_matrix(
            con,
            device_ids=args.devices,
            target_date=args.date,
            lookback_days=args.lookback_days,
        )

    print(json.dumps(out, ensure_ascii=False, indent=2))

    # Pretty per-device table to stderr so JSON on stdout stays clean.
    if args.devices and out.get("device_pulls"):
        print("\ndevice               date         pulled?  events  last_attempt", file=sys.stderr)
        for r in out["device_pulls"]:
            ok = "✓" if r["last_success_at"] else "✗"
            ev = r["last_event_count"] if r["last_event_count"] is not None else "-"
            why = r["last_success_at"] or r["last_error"] or "(never)"
            print(f"{r['device_id']:<20} {r['date']}   {ok:<7}  {ev!s:<6}  {why}",
                  file=sys.stderr)
    return 0


def _parse_ssh_remote(spec: str) -> dict:
    """Parse "device_id=ssh_alias:remote_repo[:remote_config_yaml]".

    Examples:
      omen-wsl=mtl-tail:/mnt/d/research-programs/daytrace
      omen-wsl=mtl-tail:/mnt/d/research-programs/daytrace:config/devices/omen-wsl.yaml

    The remote config path defaults to `config/devices/<device_id>.yaml`,
    which is the project's convention.
    """
    if "=" not in spec:
        raise ValueError(f"--remote expected 'device_id=ssh:path[:config]', got {spec!r}")
    device_id, rest = spec.split("=", 1)
    parts = rest.split(":", 2)
    if len(parts) < 2:
        raise ValueError(f"--remote rest needs ssh:path, got {rest!r}")
    ssh_alias = parts[0]
    remote_repo = parts[1]
    remote_config = parts[2] if len(parts) == 3 else f"config/devices/{device_id}.yaml"
    return {
        "device_id": device_id,
        "ssh_alias": ssh_alias,
        "remote_repo": remote_repo,
        "remote_config": remote_config,
    }


def remote_collect_and_pull(remote: dict, date: str) -> None:
    """For one remote device on one date:
       1) ssh into it, ask it to run collect_from_config locally
       2) rsync its data/inbox/<device>/<date>/ down to our local data/inbox/.

    Idempotent. Remote machine doesn't need its own cron — hub drives it.
    """
    dev = remote["device_id"]
    ssh_alias = remote["ssh_alias"]
    remote_repo = remote["remote_repo"]
    remote_config = remote["remote_config"]

    # 1) remote collect — writes <remote_repo>/data/inbox/<dev>/<date>/*.jsonl
    remote_cmd = (
        f"export PATH=$HOME/.npm-global/bin:$PATH && "
        f"cd {shlex.quote(remote_repo)} && "
        f"python3 scripts/collect_from_config.py "
        f"--config {shlex.quote(remote_config)} "
        f"--date {shlex.quote(date)}"
    )
    run_cmd(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
             ssh_alias, remote_cmd])

    # 2) rsync remote inbox slice down. Trailing slashes matter: src/ → dst/
    local_target = INBOX_ROOT / dev / date
    local_target.mkdir(parents=True, exist_ok=True)
    src = f"{ssh_alias}:{remote_repo}/data/inbox/{dev}/{date}/"
    run_cmd(["rsync", "-av", "--delete", src, f"{local_target}/"])


def _device_id_from_config(config_path: str) -> str:
    """Read just `device.id` from a collector YAML."""
    from daytrace.collector_config import load_collector_config
    return load_collector_config(config_path)["device"]["id"]


def _read_inbox_manifest_count(device_id: str, date: str) -> int | None:
    """After a collect (local or rsync'd back), read data/inbox/<dev>/<date>/manifest.json
    and return total_events. Returns None if the manifest isn't there yet."""
    import json
    p = INBOX_ROOT / device_id / date / "manifest.json"
    if not p.exists():
        return None
    try:
        return int(json.loads(p.read_text(encoding="utf-8")).get("total_events", 0))
    except Exception:
        return None


def cmd_catchup(args: argparse.Namespace) -> int:
    """SSH-direct catchup with per-(device, date) state tracking.

    Two phases:
      1) PULL — for every (device, date) the plan says we still need,
         either collect locally (hub) or ssh-collect+rsync from a remote.
         Every attempt (success or failure) is recorded in device_pull_log,
         so an unreachable remote shows up explicitly and gets retried on
         the next run instead of silently being "fresh forever".
      2) REGEN — import everything into events, then run pending_dates and
         regenerate_day_from_db on any date whose events_hash changed.

    Pre-reqs: ssh aliases configured (~/.ssh/config); each remote has a
    checked-out daytrace repo at the path given in --remote.
    """
    from daytrace.db import connect, init_db
    from daytrace.daily_report import (
        pending_dates, plan_device_pulls, record_pull_attempt,
        regenerate_day_from_db,
    )

    con = connect(args.db); init_db(con)

    # --remote on CLI overrides the registry; otherwise pull every machine in
    # config/remotes.yaml. Empty list = single-machine setup (no peers).
    if args.remote:
        remote_specs = args.remote
    else:
        from daytrace.remotes import load_remotes, remotes_as_cli_specs
        remote_specs = remotes_as_cli_specs(load_remotes(args.remotes_file))

    remotes = [_parse_ssh_remote(s) for s in remote_specs]
    remote_by_id = {r["device_id"]: r for r in remotes}
    hub_device_id = _device_id_from_config(args.config)
    all_device_ids = [hub_device_id] + list(remote_by_id.keys())

    # ── Phase 1: pull per (device, date) ─────────────────────────────────
    plan = plan_device_pulls(
        con,
        device_ids=all_device_ids,
        target_date=args.date,
        lookback_days=args.lookback_days,
        hard_cutoff_days=args.hard_cutoff_days,
        always_redo_recent=args.always_redo_recent,
    )
    pulls = plan["pulls"]
    print(
        f"catchup phase-1: target={plan['target_date']} window={plan['window']} "
        f"devices={all_device_ids} pulls_planned={len(pulls)}",
        flush=True,
    )

    pull_failures: list[tuple[str, str]] = []
    for p in pulls:
        dev = p["device_id"]; d = p["date"]; why = p["reason"]
        print(f"\n── pull {dev} {d} ({why}) ──", flush=True)
        try:
            if dev == hub_device_id:
                with Step(f"collect-local/{d}", crash=True):
                    run_cmd(["python3", "scripts/collect_from_config.py",
                             "--config", args.config, "--date", d])
            else:
                r = remote_by_id[dev]
                with Step(f"pull-remote/{dev}/{d}", crash=True):
                    remote_collect_and_pull(r, d)
            count = _read_inbox_manifest_count(dev, d)
            record_pull_attempt(
                con, device_id=dev, date=d, success=True, event_count=count,
            )
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            print(f"  !! pull failed: {err}", flush=True)
            record_pull_attempt(
                con, device_id=dev, date=d, success=False, error=err,
            )
            pull_failures.append((dev, d))

    # ── Phase 2: import + regen ──────────────────────────────────────────
    print(f"\n── phase-2: import + regen ──", flush=True)
    try:
        with Step("import-inbox", crash=True):
            run_cmd(["python3", "scripts/import_inbox.py"])
    except Exception as e:
        print(f"!!! import-inbox failed: {type(e).__name__}: {e}", flush=True)
        return 1

    # Sync work items + rebuild links. Best-effort — failure here doesn't
    # block regen; the dashboard just sees stale or empty work_items.
    try:
        with Step("work-items-sync", crash=False):
            from daytrace import work_items as wi
            cfg = wi.load_config()
            if cfg is None:
                print("    (work_items disabled / no config; skipping)", flush=True)
            else:
                sync_stats = wi.sync_from_feishu(con, cfg)
                total_fetched = sum(t.get("fetched", 0) for t in sync_stats.get("tables", []))
                links = wi.rebuild_links(con, lookback_days=30)
                print(
                    f"    work_items: fetched={total_fetched} across "
                    f"{len(sync_stats.get('tables', []))} table(s)  "
                    f"links={links['links_inserted']}",
                    flush=True,
                )
    except Exception as e:
        print(f"    !! work-items-sync skipped: {type(e).__name__}: {e}",
              flush=True)

    rep_plan = pending_dates(
        con, target_date=args.date,
        lookback_days=args.lookback_days,
        always_redo_recent=args.always_redo_recent,
    )
    to_run = rep_plan["to_run"]
    regen_failures: list[str] = []
    if not to_run:
        print(f"  nothing to regenerate (target={rep_plan['target_date']})", flush=True)
    else:
        print(f"  regen days: {to_run}", flush=True)
        for d in to_run:
            try:
                with Step(f"regen/{d}", crash=True):
                    rep = regenerate_day_from_db(con, d, include_ai=True)
                    cost = con.execute(
                        "SELECT COALESCE(SUM(cost_usd),0) FROM day_channel "
                        "WHERE date=? AND generator='ai'", (d,)
                    ).fetchone()[0]
                    print(f"    events={rep.total_events}  ai_cost=${cost:.4f}", flush=True)
            except Exception as e:
                print(f"!!! regen {d} failed: {type(e).__name__}: {e}", flush=True)
                regen_failures.append(d)

    print(
        f"\ncatchup done: "
        f"pulls={len(pulls)-len(pull_failures)}/{len(pulls)} OK, "
        f"regens={len(to_run)-len(regen_failures)}/{len(to_run)} OK"
        + (f"\n  pull_failures={pull_failures}" if pull_failures else "")
        + (f"\n  regen_failures={regen_failures}" if regen_failures else ""),
        flush=True,
    )
    return 1 if (pull_failures or regen_failures) else 0


def cmd_work_items_sync(args: argparse.Namespace) -> int:
    """Pull the Feishu 任务 Bitable into local work_items + rebuild
    event_work_item_links via URL / alias matching. Read-only on the
    Feishu side; safe to run any time. Skipped silently if work_items
    config is missing or disabled."""
    from daytrace.db import connect, init_db
    from daytrace import work_items as wi

    cfg = wi.load_config(args.config) if args.config else wi.load_config()
    if cfg is None:
        print("work-items-sync: feature disabled (no enabled config); skipping.",
              flush=True)
        return 0

    con = connect(args.db); init_db(con)
    tables = [t["name"] for t in cfg.get("tables", [])]
    print(f"work-items-sync: pulling {len(tables)} table(s): {tables}", flush=True)
    sync_stats = wi.sync_from_feishu(con, cfg)
    for entry in sync_stats.get("tables", []):
        if entry.get("error"):
            print(f"  ✗ {entry['name']}: {entry['error']}", flush=True)
        else:
            print(
                f"  ✓ {entry['name']}: fetched={entry['fetched']} "
                f"upserted={entry['upserted']}",
                flush=True,
            )

    link_stats = wi.rebuild_links(con, lookback_days=args.lookback_days)
    print(
        f"  links: scanned={link_stats['events_scanned']} "
        f"inserted={link_stats['links_inserted']} by={link_stats['by_type']}",
        flush=True,
    )
    return 0


def cmd_deploy(args: argparse.Namespace) -> int:
    """Push this hub's code (scripts/, daytrace/, config/) to every remote
    listed in config/remotes.yaml. Idempotent; rsync is incremental.

    Use this whenever you change collectors / configs on the hub — saves
    you from per-remote manual rsync. Catchup will then run the new code
    on each remote on its next invocation.
    """
    from daytrace.remotes import load_remotes

    remotes = load_remotes(args.remotes_file)
    if not remotes:
        print(f"deploy: no remotes configured in {args.remotes_file}; nothing to do",
              flush=True)
        return 0

    code_dirs = ["scripts", "daytrace", "config"]
    excludes = ["__pycache__/", "*.pyc", ".pytest_cache/", ".mypy_cache/"]
    print(f"deploy: pushing {code_dirs} to {len(remotes)} remote(s): "
          f"{[r.device_id for r in remotes]}", flush=True)

    failures: list[tuple[str, str]] = []
    for r in remotes:
        print(f"\n── deploy → {r.device_id} ({r.ssh}:{r.repo_path}) ──", flush=True)
        for d in code_dirs:
            local = REPO_ROOT / d
            if not local.exists():
                print(f"  skip {d}/ (not present locally)", flush=True)
                continue
            dest = f"{r.ssh}:{r.repo_path}/{d}/"
            cmd = ["rsync", "-av", "--delete"]
            for ex in excludes:
                cmd += ["--exclude", ex]
            cmd += [f"{local}/", dest]
            try:
                with Step(f"rsync/{r.device_id}/{d}", crash=True):
                    run_cmd(cmd)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                print(f"  !! failed: {err}", flush=True)
                failures.append((r.device_id, d))
                break  # don't try the other dirs on this remote if ssh is down

    if failures:
        print(f"\ndeploy done with failures: {failures}", flush=True)
        return 1
    print(f"\ndeploy done: {len(remotes)} remote(s) up to date", flush=True)
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

    # `status` and `catchup` are designed for an external scheduler
    # (cron / launchd) to ask "what's pending?" and "do all of it" without
    # threading per-day flags. Idempotent — safe to run on any cadence.
    s = sub.add_parser("status", help="dry-run: which dates would be (re-)processed?")
    s.add_argument("--db", default="data/daytrace.sqlite")
    s.add_argument("--date", help="YYYY-MM-DD target; defaults to shifted-day yesterday")
    s.add_argument("--lookback-days", type=int, default=7,
                   help="scan this many days back for stale/missing dates")
    s.add_argument("--always-redo-recent", type=int, default=2,
                   help="always re-run the N most recent days (cache makes this cheap)")
    s.add_argument("--devices", nargs="*", default=[],
                   help="also print per-device pull matrix for these device IDs")
    s.set_defaults(func=cmd_status)

    # `catchup` is the daily entrypoint: collect local + ssh-pull each remote
    # for every pending (device, date) pair, then import + regen.
    cs = sub.add_parser(
        "catchup",
        help="pull all pending (device, date) pairs via ssh+rsync, then import + regen",
    )
    cs.add_argument("--config", required=True, help="this hub's own device config YAML")
    cs.add_argument("--db", default="data/daytrace.sqlite")
    cs.add_argument(
        "--remote", action="append", default=[],
        help="repeatable: device_id=ssh_alias:remote_repo[:remote_config_yaml]",
    )
    cs.add_argument("--date", help="YYYY-MM-DD target; defaults to shifted-day yesterday")
    cs.add_argument("--lookback-days", type=int, default=7,
                    help="scan this many days back for stale/missing dates")
    cs.add_argument("--always-redo-recent", type=int, default=2,
                    help="always re-run the N most recent days (cache-cheap)")
    cs.add_argument("--hard-cutoff-days", type=int, default=30,
                    help="don't bother retrying pulls older than N days (keeps log bounded)")
    cs.add_argument("--remotes-file", default="config/remotes.yaml",
                    help="registry of remotes to pull from when --remote is empty")
    cs.set_defaults(func=cmd_catchup)

    # `deploy` keeps every remote in config/remotes.yaml in sync with the
    # hub's code. Run it after touching collectors / device configs / shared
    # daytrace modules so catchup's remote step runs the latest logic.
    ws = sub.add_parser(
        "work-items-sync",
        help="pull Feishu 任务 Bitable + rebuild event ↔ work_item links",
    )
    ws.add_argument("--db", default="data/daytrace.sqlite")
    ws.add_argument("--config", default=None,
                    help="path to work_items.yaml (default config/work_items.yaml)")
    ws.add_argument("--lookback-days", type=int, default=30,
                    help="scan this many days of events when rebuilding links")
    ws.set_defaults(func=cmd_work_items_sync)

    dp = sub.add_parser(
        "deploy",
        help="rsync scripts/ daytrace/ config/ to every remote in remotes.yaml",
    )
    dp.add_argument("--remotes-file", default="config/remotes.yaml")
    dp.set_defaults(func=cmd_deploy)

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
