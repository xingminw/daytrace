#!/usr/bin/env python3
"""Back up, collect local sources, import and rebuild deterministic reports only.

No SSH, Feishu sync/delivery, email or LLM calls. Dates are calendar dates in
DAYTRACE_TIMEZONE; reports retain the existing 04:00 workday boundary.
"""
from __future__ import annotations
import argparse
from datetime import date, datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['DAYTRACE_DISABLE_AI'] = '1'
os.umask(0o077)

from daytrace.timezone import LOCAL_TZ
from daytrace.db import connect, init_db
from daytrace.daily_report import regenerate_day_from_db, record_pull_attempt
from scripts.collect_from_config import collect_configured
from scripts.import_inbox import import_inbox


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--start', type=date.fromisoformat)
    p.add_argument('--end', type=date.fromisoformat, default=datetime.now(LOCAL_TZ).date())
    p.add_argument('--config', default=str(ROOT/'config/devices/mac-local.yaml'))
    p.add_argument('--db', default=str(ROOT/'data/daytrace.sqlite'))
    args = p.parse_args()
    start = args.start or args.end - timedelta(days=2)
    # A machine that was off longer than three days catches up on next run.
    if args.start is None and Path(args.db).exists():
        with sqlite3.connect(f"file:{Path(args.db).resolve()}?mode=ro", uri=True) as previous:
            last = previous.execute("select max(date) from device_pull_log where last_success_at is not null").fetchone()[0]
        if last:
            start = min(start, date.fromisoformat(last) - timedelta(days=1))
    if start > args.end:
        p.error('--start must not be after --end')
    db = Path(args.db).resolve()
    data = db.parent
    data.mkdir(parents=True, exist_ok=True)
    with (data/'local-run.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another local run is active', flush=True)
            return 1
        stamp = datetime.now(LOCAL_TZ).strftime('%Y%m%d-%H%M%S')
        backup = None
        if db.exists():
            backups = data/'backups'
            backups.mkdir(exist_ok=True)
            backup = backups/f'daytrace-{stamp}.sqlite'
            with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as src:
                with sqlite3.connect(backup) as dst:
                    src.backup(dst)
        con = connect(db)
        init_db(con)
        before = con.execute('select count(*) from events').fetchone()[0]
        con.close()
        began = time.monotonic()
        days = []
        manifests = []
        d = start
        while d <= args.end:
            day = d.isoformat()
            manifest = collect_configured(Path(args.config), day, 1, data/'inbox')
            manifests.append(manifest)
            days.append(day)
            print(json.dumps({'collected':day,'events':manifest['total_events']}, ensure_ascii=False), flush=True)
            d += timedelta(days=1)
        imported = import_inbox(data/'inbox', db)
        if imported['files_failed']:
            print(json.dumps({'import':imported,'backup':str(backup)}), flush=True)
            return 1
        con = connect(db)
        from daytrace.projects import sync_projects
        project_index = sync_projects(con)
        for m in manifests:
            record_pull_attempt(con, device_id=m['device_id'], date=m['end_day'], success=True, event_count=m['total_events'])
        # First calendar day's early hours belong to the preceding workday.
        report_days = [(start-timedelta(days=1)).isoformat(), *days]
        for day in report_days:
            regenerate_day_from_db(con, day, include_ai=False)
        result = {
            'start':str(start),'end':str(args.end),'timezone':str(LOCAL_TZ),'project_index':project_index,
            'workday_boundary_hour':4,'ai_enabled':False,'backup':str(backup) if backup else None,
            'events_before':before,'events_after':con.execute('select count(*) from events').fetchone()[0],
            'import':imported,'report_days':len(report_days),
            'sources':[dict(r) for r in con.execute('select source,count(*) as events,min(date) as first_date,max(date) as last_date,count(distinct date) as dates_with_events from events group by source')],
            'empty_calendar_dates':[day for day in days if not con.execute('select 1 from events where date=? limit 1',(day,)).fetchone()],
            'integrity_check':con.execute('pragma integrity_check').fetchone()[0],
            'elapsed_seconds':round(time.monotonic()-began,1),
        }
        con.close()
        (data/f'recovery-{stamp}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False),flush=True)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
