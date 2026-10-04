import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from daytrace.db import connect,init_db,upsert_events
from daytrace.schema import TraceEvent
from daytrace.codex_import import import_records,identity


def record(sid='session-one',text='Original user request',timestamp='2026-09-29T01:00:00Z'):
    return {'session_id':sid,'text':text,'timestamp':timestamp,'source_path':'/remote/session.jsonl','source_line':3,'repository':{'canonical_key':'remote:github.com/owner/repo','name':'repo','remote_url':'https://github.com/owner/repo','checkout':'/remote/repo','common_dir':'/remote/repo/.git'}}


def test_identity_normalizes_time_and_preserves_session():
    assert identity('s','2026-09-29T01:00:00Z','text')==identity('s','2026-09-29T09:00:00+08:00','text')
    assert identity('s','2026-09-29T01:00:00Z','text')!=identity('other','2026-09-29T01:00:00Z','text')


def test_import_overlap_replay_and_conflicts(tmp_path):
    con=connect(tmp_path/'db.sqlite');init_db(con)
    r=record()
    # Existing local timestamp is explicitly TZ-aware for a portable test.
    ev=TraceEvent(id='original-local',source='codex',kind='user_input',start=r['timestamp'],end=None,title='Original',summary=r['text'],project_guess=None,sensitivity='normal',evidence={'session_id':r['session_id'],'raw_text':r['text']})
    upsert_events(con,[ev])
    original=tuple(con.execute('SELECT title,summary,evidence_json FROM events').fetchone())
    stats,_=import_records(con,[r,record('unique'),record(text='Different content same second')],'ubuntu-server','local-archive')
    assert (stats['inserted'],stats['duplicates'],stats['cross_device_duplicates'],stats['potential_conflicts'])==(2,1,1,1)
    assert con.execute('SELECT count(*) FROM events').fetchone()[0]==3
    assert con.execute('SELECT count(*) FROM event_observations').fetchone()[0]==4
    assert con.execute('SELECT count(*) FROM repo_checkouts').fetchone()[0]==1
    assert tuple(con.execute("SELECT title,summary,evidence_json FROM events WHERE id='original-local'").fetchone())==original
    again,_=import_records(con,[r,record('unique'),record(text='Different content same second')],'ubuntu-server','local-archive')
    assert again['inserted']==0 and again['duplicates']==3
    assert con.execute('pragma integrity_check').fetchone()[0]=='ok'


def test_reader_filters_agents_and_supports_incremental_replay(tmp_path):
    folder=tmp_path/'sessions';folder.mkdir()
    ts='2026-09-29T01:00:00Z'
    def write(name,sid,source,messages):
        objects=[{'type':'session_meta','payload':{'id':sid,'source':source}}]+messages
        (folder/name).write_text('\n'.join(json.dumps(x) for x in objects)+'\n')
    legacy={'timestamp':ts,'type':'event_msg','payload':{'type':'user_message','message':'same user text'}}
    modern={'timestamp':ts,'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'same user text'}]}}
    assistant={'timestamp':ts,'type':'response_item','payload':{'type':'message','role':'assistant','content':[{'text':'not exported'}]}}
    write('user.jsonl','user','cli',[legacy,modern,assistant])
    write('agent.jsonl','agent',{'subagent':'other'},[legacy])
    (tmp_path/'history.jsonl').write_text('\n'.join(json.dumps(x) for x in [{'session_id':'user','ts':1790643600,'text':'history duplicate'},{'session_id':'agent','ts':1790643600,'text':'agent must not pass'},{'session_id':'fallback','ts':1790643600,'text':'CLI fallback'}]))
    code=(Path(__file__).resolve().parents[1]/'scripts/remote_codex_reader.py').read_text()
    def run(files):
        cfg={'device_id':'test','expected_hostname':platform.node(),'files':files}
        proc=subprocess.run([sys.executable,'-B','-'],input=code.replace('CONFIG = {}','CONFIG = '+repr(cfg),1),env={**os.environ,'CODEX_HOME':str(tmp_path)},capture_output=True,text=True,check=True)
        return [json.loads(x) for x in proc.stdout.splitlines()]
    output=run({});events=[x for x in output if x['type']=='event']
    assert [x['text'] for x in events]==['same user text','CLI fallback']
    assert output[-1]['excluded_agent_files']==1
    before={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    state={x['path']:{k:v for k,v in x.items() if k not in {'type','path','unchanged'}} for x in output if x['type']=='file'}
    replay=run(state)
    assert replay[-1]['events_emitted']==0
    assert before=={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


def test_timeout_skips_without_import(tmp_path,monkeypatch):
    from scripts import collect_remote_codex as collector
    def timeout(command,**kwargs):
        assert command[-4:]==['server','python3','-B','-']
        assert 'ForwardAgent=no' in command
        raise subprocess.TimeoutExpired(command,1)
    monkeypatch.setattr(collector.subprocess,'run',timeout)
    result=collector.collect_device(tmp_path/'db.sqlite',{'device_id':'ubuntu-server','ssh_alias':'server','expected_hostname':'ubuntu'})
    assert result['status']=='skipped'
    assert not (tmp_path/'db.sqlite').exists()
