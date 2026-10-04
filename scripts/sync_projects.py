#!/usr/bin/env python3
"""Back up database, update local repository attribution and refresh stats."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
import os
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['DAYTRACE_DISABLE_AI']='1'
from daytrace.db import connect,init_db
from daytrace.projects import CONFIG,sync_projects
from daytrace.daily_report import regenerate_day_from_db

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db',type=Path,default=ROOT/'data/daytrace.sqlite')
    ap.add_argument('--config',type=Path,default=CONFIG)
    args=ap.parse_args()
    if args.db.exists():
        backup=args.db.parent/'backups'/('before-project-index-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite')
        backup.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(args.db) as src:
            with sqlite3.connect(backup) as dst:src.backup(dst)
    con=connect(args.db);init_db(con)
    try:
        result=sync_projects(con,args.config)
        for row in con.execute('SELECT date FROM day_report').fetchall():
            regenerate_day_from_db(con,row['date'],include_ai=False)
        result['integrity']=con.execute('pragma integrity_check').fetchone()[0]
        print(json.dumps(result,ensure_ascii=False))
    finally:con.close()

if __name__=='__main__':main()
