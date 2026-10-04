#!/usr/bin/env python3
"""Prepare offline, then explicitly execute an approved bounded AI backfill.

No network/secrets on the default plan path. Execution uses one fixed ledger,
fixed official endpoint/model, and a sanitized local working database.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime,timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import urllib.request
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault('DAYTRACE_TIMEZONE','Asia/Shanghai')
os.umask(0o077)
from daytrace import ai_client,ai_report
from daytrace.ai_budget import Ledger,BoundedClient,BudgetStop,RequestStop,MODEL
from daytrace.ai_privacy import POLICY_VERSION,prepare_event,fingerprint
from daytrace.db import connect,init_db,events_for_shifted_day,upsert_events
from daytrace.schema import TraceEvent
from daytrace.daily_report import regenerate_day_from_db
from daytrace.channels import compute_events_hash
from daytrace.timezone import LOCAL_TZ

START='2026-05-13';END='2026-10-03';SAMPLE='2026-09-29'


def plan_source(db):
    con=sqlite3.connect(f'file:{db.resolve()}?mode=ro',uri=True);con.row_factory=sqlite3.Row
    rows=[];prepared=[];audit=[];days=[]
    d=datetime.fromisoformat(START).date();end=datetime.fromisoformat(END).date()
    while d<=end:
        date=d.isoformat();events=events_for_shifted_day(con,date,order='asc',limit=None)
        safe=[];reasons=Counter()
        for event in events:
            # Keep a hash of full source fields to detect changes between plan/resume.
            rows.append({'id':event['id'],'start':event['start'],'title':event['title'],'summary':event['summary'],'project_guess':event.get('project_guess'),'sensitivity':event.get('sensitivity'),'evidence':event.get('evidence')})
            result,reason=prepare_event(event)
            if result:safe.append(result)
            else:reasons[reason]+=1;audit.append({'date':date,'event_id':event['id'],'reason':reason})
        if events:days.append({'date':date,'original_events':len(events),'eligible_events':len(safe),'excluded_events':len(events)-len(safe),'reasons':dict(reasons)})
        prepared.extend(safe);d+=timedelta(days=1)
    con.close()
    manifest={'policy':POLICY_VERSION,'model':MODEL,'first':START,'last':END,'sample_date':SAMPLE,'source_hash':fingerprint(rows),'safe_hash':fingerprint(prepared),'days':days,'source_events':sum(x['original_events'] for x in days),'eligible_events':len(prepared),'excluded_events':len(audit),'eligible_days':sum(bool(x['eligible_events']) for x in days),'limits':{'total_rmb':20,'sample_rmb':1,'attempts_per_request':2,'max_output_tokens':8000,'thinking':'disabled'},'network_calls':0}
    return manifest,prepared,audit


def backup(db):
    dest=db.parent/'backups'/('before-ai-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.sqlite');dest.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(f'file:{db}?mode=ro',uri=True) as source:
        with sqlite3.connect(dest) as target:source.backup(target)
    return dest


def work_database(path,manifest,events):
    exists=path.exists();con=connect(path);init_db(con)
    expected=manifest['safe_hash']+POLICY_VERSION
    stored=con.execute("SELECT value FROM meta WHERE key='bounded_ai_snapshot'").fetchone()
    if exists and (not stored or stored[0]!=expected):raise RequestStop('working snapshot changed; review required, ledger retained')
    if not stored:
        upsert_events(con,[TraceEvent(**e) for e in events])
        con.execute("INSERT INTO meta(key,value) VALUES('bounded_ai_snapshot',?)",(expected,));con.commit()
        for day in manifest['days']:
            if day['eligible_events']:regenerate_day_from_db(con,day['date'],include_ai=False)
    return con


def transport(payload):
    raise RequestStop("Legacy backfill transport retired; use the evidence briefing CLI")


def copy_ai_day(work,target,date):
    # Copy only generated AI values. Original events, stats and local raw evidence
    # remain untouched; summaries explicitly represent the filtered subset.
    original_events=events_for_shifted_day(target,date,order='asc',limit=None)
    original_hash=compute_events_hash(original_events)
    for table in ('day_channel','day_project_channel'):
        for row in work.execute(f"SELECT * FROM {table} WHERE date=? AND generator='ai'",(date,)).fetchall():
            if row['error']:continue
            obj=dict(row);obj['generator_version']='bounded-'+POLICY_VERSION;obj['source_hash']=original_hash
            if table=='day_project_channel' and not target.execute('SELECT 1 FROM day_project_report WHERE date=? AND project=?',(date,obj['project'])).fetchone():continue
            cols=list(obj);target.execute(f'INSERT OR REPLACE INTO {table} ({",".join(cols)}) VALUES({",".join("?" for _ in cols)})',[obj[k] for k in cols])
    ids=[e['id'] for e in events_for_shifted_day(work,date,order='asc',limit=None)]
    if ids:
        for row in work.execute('SELECT * FROM event_activity_labels WHERE event_id IN ('+','.join('?' for _ in ids)+')',ids).fetchall():
            obj=dict(row);cols=list(obj)
            target.execute(f'INSERT INTO event_activity_labels ({",".join(cols)}) VALUES({",".join("?" for _ in cols)}) ON CONFLICT(event_id) DO UPDATE SET label=excluded.label,label_json=excluded.label_json,source=excluded.source,model=excluded.model WHERE event_activity_labels.source != "manual"',[obj[k] for k in cols])
    target.commit()


def execute(db,manifest,events,phase):
    raise RequestStop("Legacy backfill execution retired; use scripts/generate_brief.py plan/execute. Historical ledger and results remain readable; a new approval is required.")


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--db',type=Path,default=ROOT/'data/daytrace.sqlite');p.add_argument('--execute',choices=['sample','remaining'])
    args=p.parse_args();db=args.db.resolve();plan_path=db.parent/'ai-backfill-plan.json'
    if args.execute:execute(db,None,None,args.execute)
    with (db.parent/'local-run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        manifest,events,audit=plan_source(db)
        if not args.execute:
            if (db.parent/'ai-backfill-ledger.sqlite').exists():
                old=json.loads(plan_path.read_text())
                if old['source_hash']!=manifest['source_hash']:raise RequestStop('cannot overwrite an active approval snapshot')
            plan_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
            (db.parent/'ai-exclusions.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
            print(json.dumps(manifest,ensure_ascii=False));return 0
        if not plan_path.exists():raise RequestStop('prepare and review an offline plan first')
        expected=json.loads(plan_path.read_text())
        if expected!=manifest:raise RequestStop('source or policy changed since reviewed plan')
        if args.execute=='remaining':
            sample_path=db.parent/'ai-backfill-sample-result.json'
            if not sample_path.exists() or json.loads(sample_path.read_text())['status']!='completed':raise RequestStop('sample must complete and be reviewed first')
        execute(db,manifest,events,args.execute)
    return 0

if __name__=='__main__':raise SystemExit(main())
