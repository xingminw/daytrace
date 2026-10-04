#!/usr/bin/env python3
"""Enable explicitly approved 04:30 host collection; no AI or outbound delivery."""
from datetime import datetime,timedelta
from pathlib import Path
import os
import plistlib
import shlex
import shutil
import subprocess
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
HOME=Path.home()

def main():
    localtime=Path('/etc/localtime').resolve()
    if 'Asia/Shanghai' not in str(localtime):raise SystemExit('Host timezone differs from Asia/Shanghai; review schedule before enabling')
    label='com.daytrace.local-daily';domain=f'gui/{os.getuid()}'
    logs=HOME/'Library/Logs/daytrace';logs.mkdir(parents=True,exist_ok=True)
    launcher=HOME/'.local/bin/daytrace-collect';launcher.parent.mkdir(parents=True,exist_ok=True)
    target=HOME/'Library/LaunchAgents'/(label+'.plist')
    saved=ROOT/'data/backups'/('daily-launchd-'+datetime.now().strftime('%Y%m%d-%H%M%S'));saved.mkdir(parents=True,exist_ok=True)
    for old in (target,launcher):
        if old.exists():shutil.copy2(old,saved/old.name)
    launcher.write_text('#!/bin/sh\nexport DAYTRACE_DISABLE_AI=1\nexport DAYTRACE_TIMEZONE=Asia/Shanghai\nexec '+shlex.join([str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/run_host.py')])+'\n');launcher.chmod(0o700)
    plist={'Label':label,'ProgramArguments':['/bin/sh',str(launcher)],'WorkingDirectory':str(HOME),'EnvironmentVariables':{'HOME':str(HOME),'PATH':str(ROOT/'.venv/bin')+':/opt/homebrew/bin:/usr/bin:/bin','DAYTRACE_DISABLE_AI':'1','DAYTRACE_TIMEZONE':'Asia/Shanghai','PYTHONUNBUFFERED':'1'},'StartCalendarInterval':{'Hour':4,'Minute':30},'RunAtLoad':False,'StandardOutPath':str(logs/'local-daily.log'),'StandardErrorPath':str(logs/'local-daily.log'),'Umask':0o077}
    subprocess.run(['launchctl','bootout',domain+'/'+label],capture_output=True)
    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(plistlib.dumps(plist));target.chmod(0o600)
    (ROOT/'deploy'/(label+'.plist')).write_bytes(plistlib.dumps(plist))
    subprocess.run(['launchctl','bootstrap',domain,str(target)],check=True)
    now=datetime.now(ZoneInfo('Asia/Shanghai'));next_run=now.replace(hour=4,minute=30,second=0,microsecond=0)
    if next_run<=now:next_run+=timedelta(days=1)
    print('Enabled '+label+'; expected next calendar time '+next_run.isoformat()+' (while logged in; wake-up may defer it)')

if __name__=='__main__':main()
