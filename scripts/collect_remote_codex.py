#!/usr/bin/env python3
"""Best-effort authorized SSH collection. Remote Python is read-only and stdlib-only."""
from __future__ import annotations
import argparse
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['DAYTRACE_DISABLE_AI']='1'
os.umask(0o077)
from daytrace.db import connect,init_db
from daytrace.codex_import import import_records
from daytrace.projects import sync_projects
from daytrace.daily_report import regenerate_day_from_db


def backup_db(db,stamp):
    if not db.exists():return None
    dest=db.parent/'backups'/('before-remote-'+stamp+'.sqlite');dest.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(f'file:{db}?mode=ro',uri=True) as src:
        with sqlite3.connect(dest) as dst:src.backup(dst)
    return dest


def ingest_archive(db,archive,device):
    records=[];files={};summary=None
    with archive.open(encoding='utf-8') as stream:
        for line in stream:
            obj=json.loads(line)
            if obj['type']=='event':records.append(obj)
            elif obj['type']=='file':files[obj['path']]={k:v for k,v in obj.items() if k not in {'type','path','unchanged'}}
            elif obj['type']=='summary':summary=obj
            elif obj['type']=='error':raise ValueError('Remote reader reported '+obj.get('error','unknown error'))
    if summary is None or summary.get('device_id')!=device:
        raise ValueError('Missing/mismatched export summary; not importing partial stream')
    if summary['events_emitted']!=len(records):raise ValueError('Export record count mismatch')
    con=connect(db);init_db(con)
    try:
        stats,affected=import_records(con,records,device,archive)
        index=sync_projects(con)
        # Rebuild all existing dates because project identity changes can affect old attribution.
        dates=set(r[0] for r in con.execute('SELECT date FROM day_report'))|affected
        for d in sorted(dates):regenerate_day_from_db(con,d,include_ai=False)
        coverage=con.execute('SELECT count(*),min(start),max(start) FROM events WHERE device_id=?',(device,)).fetchone()
        observation_count=con.execute('SELECT count(*) FROM event_observations WHERE device_id=?',(device,)).fetchone()[0]
        stats.update({'project_index':index,'device_events':coverage[0],'first_event':coverage[1],'last_event':coverage[2],'source_observations':observation_count,'integrity':con.execute('pragma integrity_check').fetchone()[0]})
        return stats,files,summary
    finally:con.close()


def collect_device(db,device,full=False):
    name=device['device_id'];alias=device['ssh_alias']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',name) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',alias):raise ValueError('Unsafe device ID / SSH alias')
    directory=db.parent/'remote-codex'/name;directory.mkdir(parents=True,exist_ok=True)
    state_path=directory/'state.json'
    state=json.loads(state_path.read_text()) if state_path.exists() and not full else {'files':{}}
    cfg={'device_id':name,'expected_hostname':device['expected_hostname'],'files':state['files']}
    reader=(ROOT/'scripts/remote_codex_reader.py').read_text().replace('CONFIG = {}','CONFIG = '+repr(cfg),1)
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    archive=directory/(stamp+'.jsonl');partial=directory/(stamp+'.partial')
    command=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UpdateHostKeys=no','-o','ConnectTimeout=8','-o','ConnectionAttempts=1','-o','ServerAliveInterval=5','-o','ServerAliveCountMax=2','-o','ForwardAgent=no','-o','ForwardX11=no','-o','ClearAllForwardings=yes','-o','PermitLocalCommand=no',alias,'python3','-B','-']
    try:
        with partial.open('wb') as out:
            proc=subprocess.run(command,input=reader.encode(),stdout=out,stderr=subprocess.PIPE,timeout=min(int(device.get('timeout_seconds',60)),60))
        if proc.returncode:
            return {'device_id':name,'status':'skipped','reason':'SSH or remote reader failed','exit_code':proc.returncode,'stderr_tail':proc.stderr.decode(errors='replace')[-240:]}
        partial.replace(archive)
        if archive.stat().st_size>64*1024*1024:raise ValueError('Export exceeds local 64 MiB import limit')
        backup=backup_db(db,stamp)
        stats,files,summary=ingest_archive(db,archive,name)
        result={'device_id':name,'status':'partial' if summary['parse_errors'] else 'ok','backup':str(backup) if backup else None,'archive':str(archive),'export':summary,'import':stats}
        temp=state_path.with_suffix('.tmp');temp.write_text(json.dumps({'files':files,'last_success':stamp},ensure_ascii=False,indent=2));temp.replace(state_path)
        (directory/'last-run.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        return result
    except subprocess.TimeoutExpired:
        return {'device_id':name,'status':'skipped','reason':'SSH collection timed out; local service unaffected'}
    except (OSError,ValueError,sqlite3.Error) as exc:
        return {'device_id':name,'status':'skipped','reason':str(exc),'error_type':type(exc).__name__}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db',type=Path,default=ROOT/'data/daytrace.sqlite')
    ap.add_argument('--config',type=Path,default=ROOT/'config/remote_codex.json')
    ap.add_argument('--full',action='store_true',help='Re-read all source files to audit idempotence')
    ap.add_argument('--replay',type=Path,help='Replay an existing local export; makes no SSH call')
    ap.add_argument('--device',default='ubuntu-server')
    args=ap.parse_args();db=args.db.resolve();db.parent.mkdir(parents=True,exist_ok=True)
    with (db.parent/'local-run.lock').open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'status':'skipped','reason':'another collection is active'}));return 0
        if args.replay:
            backup=backup_db(db,datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
            stats,_,summary=ingest_archive(db,args.replay.resolve(),args.device)
            results=[{'device_id':args.device,'status':'replayed','backup':str(backup),'export':summary,'import':stats}]
        else:
            cfg=json.loads(args.config.read_text()) if args.config.exists() else {'devices':[]}
            results=[collect_device(db,d,args.full) for d in cfg['devices']]
        report={'mode':'local-import-no-ai','devices':results}
        (db.parent/'remote-codex-last-run.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False))
    # A remote failure is reported explicitly but never blocks the local host.
    return 0

if __name__=='__main__':raise SystemExit(main())
