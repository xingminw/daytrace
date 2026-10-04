#!/usr/bin/env python3
"""Local-only final audit and literal-evidence fallback for withheld narratives."""
import argparse
from datetime import date,timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ.setdefault('DAYTRACE_TIMEZONE','Asia/Shanghai')
os.umask(0o077)
from daytrace.db import connect,events_for_shifted_day
from daytrace.ai_privacy import CREDENTIAL,URL
from daytrace.ai_budget import Ledger


def evidence_fallback(events,withheld):
    titles=[]
    for e in events:
        t=e['title'].strip()
        if len(t)>=8 and t not in titles:titles.append(t)
    git=[e['title'] for e in events if e['source']=='git']
    codex=[e['title'] for e in events if e['source']=='codex']
    clauses=[]
    if codex:
        chosen=[t for t in titles if t in codex][:3] or codex[:1]
        clauses.append('用户文本中出现了'+ '、'.join('「'+t+'」' for t in chosen)+'等请求或讨论，执行结果未核实。')
    if git:
        chosen=list(dict.fromkeys(git))[:3]
        clauses.append('Git 可见提交标题包括'+ '、'.join('「'+t+'」' for t in chosen)+'；提交记录不等于全部任务完成。')
    clauses.append(f'依据 {len(events)} 条可安全处理记录；另有 {withheld} 条未提交。无记录不代表没有活动。')
    en=f'The available evidence contains {len(codex)} Codex request/discussion records and {len(git)} Git commit records. Requests do not establish execution, and commits do not establish completion of an entire task. {withheld} records were withheld. Missing records do not establish inactivity.'
    return {'zh':'本地事实说明（由已过滤记录生成，非 AI 原文）：'+''.join(clauses),'en':'Local evidence note (assembled from filtered records, not AI prose): '+en}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--db',type=Path,default=ROOT/'data/daytrace.sqlite');p.add_argument('--repair-withheld',action='store_true');args=p.parse_args();db=args.db.resolve()
    manifest=json.loads((db.parent/'ai-backfill-plan.json').read_text())
    con=connect(db);work=connect(db.parent/'ai-backfill-work.sqlite')
    repaired=[];missing=[];flags=[]
    for day in manifest['days']:
        d=day['date'];row=con.execute("SELECT value_json,error FROM day_channel WHERE date=? AND channel='ai_overview'",(d,)).fetchone()
        if not row or row['error'] or not row['value_json'] or row['value_json']=='null':missing.append(d);continue
        value=json.loads(row['value_json']);narr=value.get('overview',{}).get('narrative',{})
        if args.repair_withheld and isinstance(narr,dict) and str(narr.get('zh','')).startswith('记录涉及该项请求，执行结果未核实。'):
            value['overview']['narrative']=evidence_fallback(events_for_shifted_day(work,d,order='asc',limit=None),day['excluded_events'])
            value['local_evidence_fallback']=True
            for c in (con,work):c.execute("UPDATE day_channel SET value_json=? WHERE date=? AND channel='ai_overview'",(json.dumps(value,ensure_ascii=False),d));c.commit()
            repaired.append(d)
        txt=json.dumps(value,ensure_ascii=False)
        if CREDENTIAL.search(txt) or URL.search(txt):flags.append({'date':d,'reason':'credential_or_url_pattern'})
    ai_rows=con.execute("SELECT count(*) FROM day_channel WHERE channel='ai_overview' AND date BETWEEN ? AND ? AND value_json IS NOT NULL AND value_json!='null' AND error IS NULL",(manifest['first'],manifest['last'])).fetchone()[0]
    all_dates=[];d=date.fromisoformat(manifest['first']);end=date.fromisoformat(manifest['last'])
    while d<=end:all_dates.append(d.isoformat());d+=timedelta(days=1)
    ledger=Ledger(db.parent/'ai-backfill-ledger.sqlite')
    report={'candidate_days':len(manifest['days']),'overview_days':ai_rows,'missing_overviews':missing,'source_events':manifest['source_events'],'eligible_events':manifest['eligible_events'],'excluded_events':manifest['excluded_events'],'no_source_dates':[d for d in all_dates if d not in {x['date'] for x in manifest['days']}],'literal_evidence_fallback_dates':repaired,'outbound_pattern_flags':flags,'integrity':con.execute('PRAGMA integrity_check').fetchone()[0],'budget':ledger.snapshot(),'all_costs_include_withdrawn_sample':True}
    (db.parent/'ai-backfill-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False));con.close();work.close();ledger.con.close()

if __name__=='__main__':main()
