#!/usr/bin/env python3
"""Manual Mac-mini-first collection; unavailable remote devices are skipped."""
from pathlib import Path
import argparse
import os
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
os.environ['DAYTRACE_DISABLE_AI']='1'
os.environ['DAYTRACE_PROJECT_METADATA_ROOTS_ONLY']='1'
os.environ.setdefault('DAYTRACE_TIMEZONE','Asia/Shanghai')

def main():
    result=subprocess.run([sys.executable,str(ROOT/'scripts/run_local.py'),*sys.argv[1:]],cwd=ROOT)
    if result.returncode:return result.returncode
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--db',default=str(ROOT/'data/daytrace.sqlite'))
    args,_=parser.parse_known_args(sys.argv[1:])
    # Remote registry includes only explicitly authorized existing SSH aliases.
    return subprocess.run([sys.executable,str(ROOT/'scripts/collect_remote_codex.py'),'--db',args.db],cwd=ROOT).returncode

if __name__=='__main__':raise SystemExit(main())
