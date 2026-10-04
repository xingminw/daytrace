#!/usr/bin/env python3
"""Read-only stdlib Codex extraction, sent to an authorized host over SSH stdin.

No writes, installs, SQLite connection, credentials, assistant/tool content or API.
The host injects CONFIG; only event JSONL plus inventory metadata is returned.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
from datetime import datetime, timezone
from urllib.parse import urlsplit

CONFIG = {}
CONTEXT_PREFIXES = ('<environment_context>', '<external_codex_apps_open_page', '<in-app-browser-context', '<recommended_plugins>', '# AGENTS.md instructions', '<codex_delegation>')


def emit(value):
    print(json.dumps(value,ensure_ascii=False,separators=(',',':')),flush=True)


def user_text(obj):
    payload=obj.get('payload') or {}
    if not isinstance(payload,dict):return ''
    if obj.get('type')=='event_msg' and payload.get('type')=='user_message':
        text=str(payload.get('message') or '').strip()
        return '' if text.startswith(CONTEXT_PREFIXES) else text
    if obj.get('type')!='response_item' or payload.get('type')!='message' or payload.get('role')!='user':return ''
    parts=[]
    for part in payload.get('content') or []:
        if not isinstance(part,dict):continue
        text=str(part.get('text') or '').strip()
        if text and not text.startswith(CONTEXT_PREFIXES):parts.append(text)
    return '\n'.join(parts)


def canonical_remote(value):
    match=re.match(r'^(?:[^/@:]+@)?([^/:]+):(.+)$',value)
    if '://' not in value and match and not value.startswith(('/','.')):
        host,path=match.groups()
    else:
        parsed=urlsplit(value)
        if not parsed.hostname or parsed.scheme not in {'http','https','ssh','git'}:return None
        host,path=parsed.hostname,parsed.path
    host=host.lower();path=path.strip('/').removesuffix('.git')
    if host=='github.com':path=path.lower()
    return host+'/'+path if path else None


def git(path,*args):
    try:
        r=subprocess.run(['git','-C',str(path),*args],capture_output=True,text=True,timeout=5)
        return r.stdout.strip() if r.returncode==0 else ''
    except (OSError,subprocess.TimeoutExpired):return ''


REPOS={}

def repository(cwd):
    if not cwd:return None
    if cwd in REPOS:return REPOS[cwd]
    REPOS[cwd]=None
    if not Path(cwd).is_dir():return None
    top=git(cwd,'rev-parse','--show-toplevel')
    if not top:return None
    common=git(top,'rev-parse','--path-format=absolute','--git-common-dir') or str(Path(top)/'.git')
    remote=canonical_remote(git(top,'config','--get','remote.origin.url'))
    key='remote:'+remote if remote else 'local:'+CONFIG['device_id']+':'+common
    REPOS[cwd]={'canonical_key':key,'name':remote.rsplit('/',1)[-1] if remote else Path(top).name,'remote_url':'https://'+remote if remote else None,'checkout':top,'common_dir':common}
    return REPOS[cwd]


def epoch(timestamp):
    try:
        if isinstance(timestamp,(int,float)):return float(timestamp)
        value=datetime.fromisoformat(str(timestamp).replace('Z','+00:00'))
        if value.tzinfo is None:return None  # Do not guess timezone for naive remote records.
        return value.timestamp()
    except (ValueError,TypeError,OverflowError):return None


def main():
    if platform.node()!=CONFIG['expected_hostname']:
        emit({'type':'error','error':'unexpected_hostname'});return 2
    home=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))
    known=CONFIG.get('files',{})
    files=sorted([p for folder in ('sessions','archived_sessions') for p in (home/folder).rglob('*.jsonl')])
    sessions={};sent=unchanged=excluded=errors=0
    for path in files:
        st=path.stat();key=str(path);prior=known.get(key,{})
        stamp={'size':st.st_size,'mtime_ns':st.st_mtime_ns}
        if all(prior.get(k)==v for k,v in stamp.items()) and not prior.get('parse_errors'):
            unchanged+=1
            if prior.get('session_id'):
                sid=prior['session_id'];old=sessions.get(sid,{})
                sessions[sid]={**prior,'has_messages':prior.get('has_messages',False) or old.get('has_messages',False)}
            emit({'type':'file','path':key,**prior,'unchanged':True});continue
        meta={};sid='';allowed=True;seen=set();messages=0;bad=0
        with path.open(encoding='utf-8',errors='replace') as f:
            for idx,line in enumerate(f):
                try:obj=json.loads(line)
                except ValueError:bad+=1;continue
                if obj.get('type')=='session_meta':
                    meta=obj.get('payload') or {};sid=str(meta.get('id') or '')
                    source=json.dumps([meta.get('source'),meta.get('thread_source'),meta.get('agent_role')]).lower()
                    allowed=not any(tag in source for tag in ('subagent','guardian'))
                    continue
                if not allowed:continue
                text=user_text(obj)
                if not text or not sid:continue
                ts=epoch(obj.get('timestamp'))
                if ts is None:bad+=1;continue
                signature=(int(ts),hashlib.sha256(text.encode()).hexdigest())
                if signature in seen:continue
                seen.add(signature);messages+=1
                repo=repository(meta.get('cwd'))
                payload=obj.get('payload') or {}
                emit({'type':'event','session_id':sid,'timestamp':obj['timestamp'],'text':text,'cwd':meta.get('cwd'),'source_path':key,'source_line':idx,'source_message_id':payload.get('id'),'repository':repo})
                sent+=1
        info={**stamp,'session_id':sid,'has_messages':bool(messages),'allowed':allowed,'parse_errors':bad,'cwd':meta.get('cwd')}
        if sid:
            old=sessions.get(sid,{})
            info['has_messages']=info['has_messages'] or old.get('has_messages',False)
            sessions[sid]=info
        if not allowed:excluded+=1
        errors+=bad
        emit({'type':'file','path':key,**info,'unchanged':False})
    # CLI history is only a fallback for sessions without transcript user records.
    history=home/'history.jsonl'
    if history.exists():
        st=history.stat();stamp={'size':st.st_size,'mtime_ns':st.st_mtime_ns};prior=known.get(str(history),{})
        # Always reconsider history when transcript inventory changed.
        if unchanged==len(files) and not prior.get('parse_errors') and all(prior.get(k)==v for k,v in stamp.items()):
            emit({'type':'file','path':str(history),**stamp,'unchanged':True});unchanged+=1
        else:
            bad=0
            with history.open(encoding='utf-8',errors='replace') as f:
                for idx,line in enumerate(f):
                    try:obj=json.loads(line)
                    except ValueError:bad+=1;continue
                    sid=str(obj.get('session_id') or '');text=str(obj.get('text') or '').strip()
                    info=sessions.get(sid,{})
                    if not sid or not text or info.get('has_messages') or info.get('allowed') is False:continue
                    ts=epoch(obj.get('ts'))
                    if ts is None:bad+=1;continue
                    emit({'type':'event','session_id':sid,'timestamp':datetime.fromtimestamp(ts,timezone.utc).isoformat(),'text':text,'cwd':info.get('cwd'),'source_path':str(history),'source_line':idx,'source_message_id':None,'repository':repository(info.get('cwd'))})
                    sent+=1
            errors+=bad
            emit({'type':'file','path':str(history),**stamp,'parse_errors':bad,'unchanged':False})
    emit({'type':'summary','hostname':platform.node(),'device_id':CONFIG['device_id'],'rollout_files':len(files),'unchanged_files':unchanged,'excluded_agent_files':excluded,'parse_errors':errors,'events_emitted':sent})
    return 0


if __name__=='__main__':raise SystemExit(main())
