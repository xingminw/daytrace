"""Exercise the normal CLI, not the manually curated review sample path."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from zoneinfo import ZoneInfo
import pytest
from daytrace import briefings as b
from daytrace import brief_generation as g
from daytrace.brief_budget import BriefLedger, request_payload, estimate
from daytrace.ai_budget import BudgetStop, RequestStop
from daytrace.db import connect, init_db, upsert_events
from daytrace.schema import TraceEvent
from daytrace.projects import ensure_schema
from scripts import generate_brief as cli


@pytest.fixture
def source(tmp_path, monkeypatch):
    from daytrace import brief_sources, timezone
    monkeypatch.setattr(brief_sources, 'LOCAL_TZ', ZoneInfo('Asia/Shanghai'))
    monkeypatch.setattr(timezone, 'LOCAL_TZ', ZoneInfo('Asia/Shanghai'))
    monkeypatch.delenv('DAYTRACE_DISABLE_AI', raising=False)
    from daytrace import ai_client
    monkeypatch.setattr(ai_client, '_load_secrets_into_environ', lambda: pytest.fail('test must not load real credentials'))
    monkeypatch.setattr(ai_client, 'official_transport', lambda p: pytest.fail('real transport forbidden'))
    db = tmp_path/'db.sqlite'; con = connect(db); init_db(con); ensure_schema(con)
    return db, con


def event(con, id, day, text='比较排队模型与观测的差异', project='unseen-research'):
    upsert_events(con, [TraceEvent(id=id, source='codex', kind='user_input', start=day+'T12:00:00', end=None, title=text, summary=text, project_guess=project, sensitivity='normal', evidence={'raw_text':text})])


def git_repo(tmp_path, con):
    repo = tmp_path/'unknown-project'; repo.mkdir()
    def git(*args, env=None):
        return subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True,text=True,env=env).stdout
    git('init'); git('config','user.name','Test'); git('config','user.email','test@example.invalid')
    for day, subject in [('2026-09-25','Add queue model baseline'),('2026-10-03','Fix queue model input validation')]:
        env={**os.environ,'GIT_AUTHOR_DATE':day+'T12:00:00+08:00','GIT_COMMITTER_DATE':day+'T12:00:00+08:00'}
        git('commit','--allow-empty','-m',subject,env=env)
    con.execute('INSERT INTO repo_projects(project_id,canonical_key,name) VALUES(?,?,?)',('repo:unknown','local:test','unknown-project'))
    con.execute('INSERT INTO repo_checkouts(path,project_id) VALUES(?,?)',(str(repo),'repo:unknown'));con.commit()
    return repo


def response(payload, state_override=None):
    data=json.loads(payload['messages'][1]['content'])['evidence_pack']
    groups={}
    for e in data['evidence']:
        if not e['supports']:continue
        groups.setdefault(e['project_id'],[]).append({'text':e['text'], 'state':state_override or e['supports'][0], 'scope':e['scope'], 'evidence_ids':[e['id']], 'support_quotes':{e['id']:e['text']}})
    value={'headline':'项目事项与代码变化', 'projects':[{'project_id':pid,'claims':claims} for pid,claims in groups.items()]}
    return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value,ensure_ascii=False)}}], 'usage':{'prompt_tokens':100,'completion_tokens':100}}


def test_cli_dynamic_two_dates_two_weeks_unknown_project_and_publication(source,tmp_path,capsys):
    db,con=source;git_repo(tmp_path,con)
    event(con,'a','2026-09-25');event(con,'b','2026-10-03','讨论第二阶段的输入边界')
    original=con.execute('SELECT * FROM events').fetchall()
    plan_path=tmp_path/'plan.json'
    assert cli.main(['plan','--db',str(db),'--date','2026-09-25','--date','2026-10-03','--week','2026-W39','--week','2026-W40','--out',str(plan_path)])==0
    plan=json.loads(plan_path.read_text());assert len(plan['jobs'])==4
    assert plan['jobs'][0]['pack']['evidence'] != plan['jobs'][1]['pack']['evidence']
    assert 'Add queue model baseline' in plan['jobs'][2]['prompt']['user'] and 'Fix queue model' not in plan['jobs'][2]['prompt']['user']
    assert 'Fix queue model' in plan['jobs'][3]['prompt']['user']
    sent=[]
    args=['execute','--plan',str(plan_path),'--approve-plan',plan['approval_hash'],'--budget-rmb','1']
    assert cli.main(args,transport=lambda p:sent.append(p) or response(p))==0
    assert len(sent)==4
    assert cli.main(args,transport=lambda p:pytest.fail('resume must use persistent responses'))==0
    result=json.loads((tmp_path/'brief-generations'/plan['approval_hash']/'result.json').read_text())
    for item in result['jobs']:
        path=Path(item['draft']);draft=json.loads(path.read_text())
        assert cli.main(['publish','--db',str(db),'--draft',str(path),'--accept-draft',draft['draft_hash']])==0
    assert con.execute('SELECT count(*) FROM report_briefs').fetchone()[0]==4
    assert original==con.execute('SELECT * FROM events').fetchall()
    assert len(list((tmp_path/'backups').glob('before-brief-*.sqlite')))==4
    assert not (tmp_path/'ai-backfill-ledger.sqlite').exists()
    for job in plan['jobs']:
        assert '/unknown-project' not in job['prompt']['user']
        assert all('verified' not in e['supports'] for e in job['pack']['evidence'])


def test_requests_cannot_be_upgraded_and_invalid_generation_preserves_reports(source,tmp_path):
    db,con=source;event(con,'req','2026-10-03')
    plan=g.make_plan(db,[('daily','2026-10-03')]);job=plan['jobs'][0]
    pack=job['pack'];e=pack['evidence'][0]
    old={'headline':'原来的评审样本','projects':[{'project_id':e['project_id'],'claims':[{'text':'讨论排队模型','state':'requested','scope':e['scope'],'evidence_ids':[e['id']],'support_quotes':{e['id']:e['text']}}]}]}
    b.save_brief(con,'daily','2026-10-03',old,pack,origin='local_reviewed')
    before=con.execute('SELECT * FROM report_briefs').fetchall();sent=[]
    result=g.execute_plan(plan,plan['approval_hash'],'1',transport=lambda p:sent.append(p) or response(p,'verified'))
    assert result['status']=='stopped' and len(sent)==2
    assert before==con.execute('SELECT * FROM report_briefs').fetchall()
    result=g.execute_plan(plan,plan['approval_hash'],'1',transport=lambda p:pytest.fail('retry limit persists'))
    assert result['status']=='stopped' and result['budget']['attempts']==2


def test_empty_day_does_not_call_provider_and_publishes_clear_local_state(source):
    db,con=source;plan=g.make_plan(db,[('daily','2026-01-01'),('weekly','2026-W01')])
    assert all(j['mode']=='no_evidence' for j in plan['jobs']) and plan['two_attempt_upper_rmb']==0
    result=g.execute_plan(plan,plan['approval_hash'],'1',transport=lambda p:pytest.fail('no evidence, no paid call'))
    assert result['status']=='completed' and result['budget']['attempts']==0
    draft=json.loads(Path(result['jobs'][0]['draft']).read_text())
    assert draft['value']['headline']=='本期暂无可用活动记录'
    g.publish_draft(db,draft,draft['draft_hash'])
    assert b.load_brief(con,'daily','2026-01-01')['origin']=='local_rules'


def test_budget_blocks_before_send_and_cannot_be_raised_on_restart(source):
    db,con=source;event(con,'req','2026-10-03');plan=g.make_plan(db,[('daily','2026-10-03')])
    result=g.execute_plan(plan,plan['approval_hash'],'0.000001',transport=lambda p:pytest.fail('budget must refuse'))
    assert result['status']=='stopped' and result['budget']['attempts']==0
    with pytest.raises(BudgetStop):g.execute_plan(plan,plan['approval_hash'],'1',transport=lambda p:pytest.fail('no changed cap'))


def test_unknown_costs_are_reserved_after_restart(source):
    db,con=source;event(con,'req','2026-10-03');plan=g.make_plan(db,[('daily','2026-10-03')])
    def timeout(p):raise TimeoutError('PRIVATE PROVIDER BODY')
    result=g.execute_plan(plan,plan['approval_hash'],'1',transport=timeout)
    assert result['budget']['attempts']==2 and result['budget']['unknown_reserved_rmb']>0
    result2=g.execute_plan(plan,plan['approval_hash'],'1',transport=lambda p:pytest.fail('restart must not retry'))
    assert result2['budget']==result['budget']
    assert b'PRIVATE PROVIDER BODY' not in (db.parent/'brief-generation-ledger.sqlite').read_bytes()


def test_plan_and_publication_detect_source_changes_and_approval_mismatch(source):
    db,con=source;event(con,'req','2026-10-03');plan=g.make_plan(db,[('daily','2026-10-03')])
    with pytest.raises(RequestStop):g.execute_plan(plan,'wrong','1',transport=lambda p:pytest.fail('approval mismatch'))
    result=g.execute_plan(plan,plan['approval_hash'],'1',transport=response)
    draft=json.loads(Path(result['jobs'][0]['draft']).read_text())
    event(con,'new','2026-10-03','加入新的比较条件')
    with pytest.raises(RequestStop,match='source changed'):g.publish_draft(db,draft,draft['draft_hash'])


def test_disabled_schedule_cannot_execute_and_legacy_paths_fail_closed(source,monkeypatch):
    db,con=source;event(con,'req','2026-10-03');plan=g.make_plan(db,[('daily','2026-10-03')])
    monkeypatch.setenv('DAYTRACE_DISABLE_AI','1')
    with pytest.raises(RequestStop,match='disabled'):g.execute_plan(plan,plan['approval_hash'],'1',transport=response)
    monkeypatch.delenv('DAYTRACE_DISABLE_AI')
    from daytrace import ai_client
    with pytest.raises(ai_client.LLMError,match='retired'):ai_client.call_json(system='x',user='y')
    from scripts.backfill_ai_bounded import execute
    with pytest.raises(RequestStop,match='retired'):execute(db,None,None,'remaining')


def test_manual_sample_cannot_replace_generated_output(source):
    db,con=source;event(con,'req','2026-10-03');plan=g.make_plan(db,[('daily','2026-10-03')])
    pack=plan['jobs'][0]['pack']
    result=g.execute_plan(plan,plan['approval_hash'],'1',transport=response)
    draft=json.loads(Path(result['jobs'][0]['draft']).read_text());g.publish_draft(db,draft,draft['draft_hash'])
    with pytest.raises(ValueError,match='manual sample'):b.save_brief(con,'daily','2026-10-03',draft['value'],pack,origin='local_reviewed')


def test_git_commits_cannot_claim_acceptance(source,tmp_path):
    db,con=source;git_repo(tmp_path,con);plan=g.make_plan(db,[('weekly','2026-W40')])
    result=g.execute_plan(plan,plan['approval_hash'],'1',transport=lambda p:response(p,'verified'))
    assert result['status']=='stopped'


def test_source_failures_are_visible_and_remote_checkout_is_not_contacted(source,monkeypatch):
    db,con=source
    con.execute('INSERT INTO repo_projects(project_id,canonical_key,name) VALUES("r","local:r","remote-project")')
    con.execute('INSERT INTO repo_checkouts(path,project_id) VALUES("ssh://host/private","r")');con.commit()
    from daytrace import brief_sources
    monkeypatch.setattr(brief_sources,'git',lambda *a:pytest.fail('remote must not be read'))
    p=g.make_plan(db,[('daily','2026-10-03')])
    assert p['jobs'][0]['pack']['source_audit'][0]['status']=='nonlocal_checkout_not_contacted'


def test_provider_usage_contract_halts_new_jobs(source):
    db,con=source;event(con,'a','2026-10-03');event(con,'b','2026-10-04');plan=g.make_plan(db,[('daily','2026-10-03'),('daily','2026-10-04')]);sent=[]
    def bad(p):
        sent.append(p);out=response(p);out['usage']['completion_tokens']=999999;return out
    result=g.execute_plan(plan,plan['approval_hash'],'1',transport=bad)
    assert len(sent)==1 and result['status']=='stopped' and result['budget']['unknown_reserved_rmb']>0


def test_private_and_long_context_boundary_stays_local(source):
    db,con=source
    text='讨论模型的输入、对照与验证顺序。'*20
    event(con,'context','2026-10-03',text)
    event(con,'secret','2026-10-03','password=FAKE_TEST_SECRET')
    p=g.make_plan(db,[('daily','2026-10-03')]);out=p['jobs'][0]['prompt']['user']
    assert text in out and 'FAKE_TEST_SECRET' not in out
    assert p['jobs'][0]['pack']['coverage']['excluded']['credential_pattern']==1


def test_outbound_guard_preserves_json_escaping_without_rewriting_reviewed_fields():
    prompt={'system':'Return Chinese JSON', 'user':json.dumps({'text':'比较“输入”与输出，保留引号 " 和反斜线 \\ 的原意'},ensure_ascii=False), 'model':'deepseek-flash','thinking':{'type':'disabled'},'max_tokens':100}
    assert request_payload(prompt)['messages'][1]['content']==prompt['user']
    prompt['user']=json.dumps({'text':'password=FAKE_TEST_ONLY'})
    with pytest.raises(ValueError):request_payload(prompt)
