"""Cross-device Codex identity and provenance; preserve source text and conflicts."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from .timezone import LOCAL_TZ

SCHEMA='''
CREATE TABLE IF NOT EXISTS codex_record_index (
 record_key TEXT PRIMARY KEY, family_key TEXT NOT NULL, event_id TEXT NOT NULL,
 content_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_codex_family ON codex_record_index(family_key);
CREATE TABLE IF NOT EXISTS event_observations (
 device_id TEXT NOT NULL, record_key TEXT NOT NULL, content_sha256 TEXT NOT NULL,
 event_id TEXT NOT NULL, source_path TEXT, source_line INTEGER,
 source_message_id TEXT, archive_path TEXT, observed_at TEXT DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(device_id,record_key,content_sha256)
);
CREATE TABLE IF NOT EXISTS codex_content_conflicts (
 family_key TEXT NOT NULL,event_a TEXT NOT NULL,event_b TEXT NOT NULL,
 reason TEXT NOT NULL,observed_at TEXT DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(family_key,event_a,event_b)
);
'''


def ensure_schema(con):con.executescript(SCHEMA)


def identity(session_id,timestamp,text):
    if isinstance(timestamp,(int,float)):ts=int(timestamp)
    else:
        d=datetime.fromisoformat(str(timestamp).replace('Z','+00:00'))
        if d.tzinfo is None:d=d.replace(tzinfo=LOCAL_TZ)
        ts=int(d.timestamp())
    content=hashlib.sha256(text.strip().encode()).hexdigest()
    family=f'codex-user:{session_id}:{ts}'
    return family+':'+content,family,content


def observe(con,device,key,content,eid,path=None,line=None,message_id=None,archive=None):
    con.execute('INSERT OR IGNORE INTO event_observations(device_id,record_key,content_sha256,event_id,source_path,source_line,source_message_id,archive_path) VALUES(?,?,?,?,?,?,?,?)',(device,key,content,eid,path,line,message_id,archive))


def index_existing(con):
    ensure_schema(con)
    # Prefer primary-Mac event IDs when the same logical record already exists.
    for r in con.execute("SELECT * FROM events WHERE source='codex' AND kind='user_input' ORDER BY CASE device_id WHEN 'Mac' THEN 0 ELSE 1 END,id").fetchall():
        try:e=json.loads(r['evidence_json'] or '{}')
        except (ValueError,TypeError):continue
        sid=e.get('session_id')
        if not sid:continue
        text=e.get('raw_text') or r['summary'] or ''
        key,family,content=identity(sid,r['start'],text)
        con.execute('INSERT OR IGNORE INTO codex_record_index VALUES(?,?,?,?)',(key,family,r['id'],content))
        eid=con.execute('SELECT event_id FROM codex_record_index WHERE record_key=?',(key,)).fetchone()[0]
        observe(con,r['device_id'],key,content,eid,e.get('rollout_path') or r['raw_ref'],e.get('rollout_line'),e.get('source_message_id'))
    con.commit()


def register_repository(con,device,repo):
    if not repo:return
    key=repo['canonical_key']
    pid='repo:'+hashlib.sha256(key.encode()).hexdigest()[:24]
    con.execute('INSERT INTO repo_projects(project_id,canonical_key,name,remote_url) VALUES(?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET last_seen_at=CURRENT_TIMESTAMP',(pid,key,repo['name'],repo.get('remote_url')))
    checkout='ssh://'+device+str(repo['checkout'])
    con.execute('INSERT INTO repo_checkouts(path,project_id,common_dir) VALUES(?,?,?) ON CONFLICT(path) DO UPDATE SET project_id=excluded.project_id,last_seen_at=CURRENT_TIMESTAMP',(checkout,pid,repo.get('common_dir')))


def import_records(con,records,device,archive_path):
    from .db import upsert_events
    from .schema import TraceEvent
    index_existing(con)
    stats={'received':0,'inserted':0,'duplicates':0,'cross_device_duplicates':0,'potential_conflicts':0}
    affected=set()
    for record in records:
        stats['received']+=1
        sid=str(record['session_id']);text=str(record['text']).strip()
        key,family,content=identity(sid,record['timestamp'],text)
        register_repository(con,device,record.get('repository'))
        prior=con.execute('SELECT event_id FROM codex_record_index WHERE record_key=?',(key,)).fetchone()
        if prior:
            eid=prior[0];stats['duplicates']+=1
            if con.execute('SELECT 1 FROM event_observations WHERE record_key=? AND device_id!=? LIMIT 1',(key,device)).fetchone():stats['cross_device_duplicates']+=1
        else:
            eid='codex-user-'+hashlib.sha256(key.encode()).hexdigest()[:24]
            d=datetime.fromisoformat(record['timestamp'].replace('Z','+00:00')).astimezone(LOCAL_TZ)
            when=d.replace(tzinfo=None).isoformat(timespec='seconds')
            repo=record.get('repository')
            evidence={'session_id':sid,'raw_text':text,'cwd':record.get('cwd'),'rollout_path':record.get('source_path'),'rollout_line':record.get('source_line'),'source_message_id':record.get('source_message_id'),'original_timestamp':record['timestamp'],'remote_device_id':device,'daytrace_remote_repository':repo}
            ev=TraceEvent(id=eid,source='codex',kind='user_input',start=when,end=None,title=text.splitlines()[0][:96],summary=text,project_guess=repo['name'] if repo else None,sensitivity='normal',evidence=evidence,raw_ref=record.get('source_path'),device_id=device,collector_id='ssh-readonly')
            upsert_events(con,[ev],commit=False)
            for other in con.execute('SELECT event_id FROM codex_record_index WHERE family_key=? AND content_sha256!=?',(family,content)).fetchall():
                con.execute('INSERT OR IGNORE INTO codex_content_conflicts(family_key,event_a,event_b,reason) VALUES(?,?,?,?)',(family,other[0],eid,'same session and second with different content; both preserved'))
                stats['potential_conflicts']+=1
            con.execute('INSERT INTO codex_record_index VALUES(?,?,?,?)',(key,family,eid,content))
            stats['inserted']+=1
            affected.add(d.date().isoformat())
            if d.hour<4:
                from datetime import timedelta
                affected.add((d.date()-timedelta(days=1)).isoformat())
        observe(con,device,key,content,eid,record.get('source_path'),record.get('source_line'),record.get('source_message_id'),str(archive_path))
    con.commit()
    return stats,affected
