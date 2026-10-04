#!/usr/bin/env python3
"""Install loopback dashboard. Prepare daily plist without enabling its schedule."""
from pathlib import Path
from datetime import datetime
import os
import plistlib
import shlex
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]
HOME=Path.home()
DATA=ROOT/'data'
DOMAIN=f'gui/{os.getuid()}'

def main():
    agents=HOME/'Library/LaunchAgents';agents.mkdir(parents=True,exist_ok=True)
    logs=HOME/'Library/Logs/daytrace';logs.mkdir(parents=True,exist_ok=True)
    launcher=HOME/'.local/bin/daytrace-dashboard';launcher.parent.mkdir(parents=True,exist_ok=True)
    args=[str(ROOT/'.venv/bin/python'),str(ROOT/'dashboard/server.py'),'--db',str(DATA/'daytrace.sqlite'),'--host','127.0.0.1','--port','8766']
    launcher.write_text('#!/bin/sh\nexec '+shlex.join(args)+'\n');launcher.chmod(0o700)
    env={'HOME':str(HOME),'PATH':str(ROOT/'.venv/bin')+':/opt/homebrew/bin:/usr/bin:/bin','DAYTRACE_TIMEZONE':'Asia/Shanghai','DAYTRACE_DISABLE_AI':'1','PYTHONUNBUFFERED':'1'}
    backup=DATA/'backups'/('launchd-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    for suffix,cmd in {'dashboard':['/bin/sh',str(launcher)],'local-daily':[str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/run_local.py')]}.items():
        label='com.daytrace.'+suffix
        plist={'Label':label,'ProgramArguments':cmd,'WorkingDirectory':str(HOME),'EnvironmentVariables':env,'StandardOutPath':str(logs/(suffix+'.log')),'StandardErrorPath':str(logs/(suffix+'.log')),'Umask':0o077}
        if suffix=='dashboard':plist.update(RunAtLoad=True,KeepAlive=True,ThrottleInterval=10)
        else:plist.update(StartCalendarInterval={'Hour':4,'Minute':30},RunAtLoad=False)
        rendered=ROOT/'deploy'/(label+'.plist');rendered.write_bytes(plistlib.dumps(plist))
        if suffix!='dashboard':
            print('Prepared only (NOT enabled): '+str(rendered));continue
        target=agents/(label+'.plist')
        if target.exists():
            backup.mkdir(parents=True,exist_ok=True);shutil.copy2(target,backup/target.name)
        subprocess.run(['launchctl','bootout',DOMAIN+'/'+label],capture_output=True)
        target.write_bytes(plistlib.dumps(plist));target.chmod(0o600)
        subprocess.run(['launchctl','bootstrap',DOMAIN,str(target)],check=True)
        print('Installed: '+str(target))

if __name__=='__main__':main()
