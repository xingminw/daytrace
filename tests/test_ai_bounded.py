import json
import sqlite3
from pathlib import Path
import pytest
from daytrace.ai_privacy import prepare_event,clean_text
from daytrace.ai_budget import Ledger,BoundedClient,BudgetStop,RequestStop,TOTAL_LIMIT


def event(text='Fix navigation in project'):
    return {'id':'event-1','source':'codex','kind':'user_input','start':'2026-09-29T12:00:00','end':None,'title':text[:96],'summary':text,'project_guess':'repo','sensitivity':'normal','evidence':{'raw_text':text},'device_id':'Mac','location_id':'unknown','collector_id':'test'}


def envelope(value=None,usage=True):
    result={'model':'deepseek-flash','choices':[{'finish_reason':'stop','message':{'content':json.dumps(value or {'ok':True})}}]}
    if usage:result['usage']={'prompt_tokens':100,'completion_tokens':50,'completion_tokens_details':{'reasoning_tokens':0}}
    return result


@pytest.mark.parametrize('text,reason',[
 ('API_KEY=FAKE_FIXTURE_VALUE','credential_pattern'),
 ('Do this\nAssistant: private tool output','embedded_transcript_or_code'),
 ('工具输出：PRIVATE','embedded_transcript_or_code'),
 ('Start\n```json\n{"role":"assistant"}','embedded_transcript_or_code'),
 ('First line\nUnmarked copied secret response','multiline_ambiguous'),
 ('x'*1001,'long_ambiguous'),
 ('FAKEabc123FAKEabc123FAKEabc123FAKEabc123','opaque_token'),
])
def test_ambiguous_content_is_excluded_whole(text,reason):
    safe,why=prepare_event(event(text));assert safe is None and why==reason


def test_sensitive_flag_and_full_evidence_scanned_before_truncation():
    e=event();e['sensitivity']='private';assert prepare_event(e)[0] is None
    e=event();e['evidence']['raw_text']='safe prefix '*40+' password=FAKE_FIXTURE'
    assert prepare_event(e)[1]=='credential_pattern'


@pytest.mark.parametrize('text,private',[('Use /Volumes/Test Disk/Projects/repo now','Test Disk'),('查看/Users/person/private-file','person'),('Read C:\\Users\\person\\secret','person'),('Read \\\\server\\private','server'),('Use https://example.invalid/path?token=FAKE','example.invalid'),('Email person@example.invalid','person@')])
def test_paths_urls_identifiers_are_removed(text,private):
    assert private not in clean_text(text)


def test_budget_settles_usage_and_cache_survives_restart(tmp_path):
    path=tmp_path/'ledger.sqlite';ledger=Ledger(path);sent=[]
    client=BoundedClient(ledger,lambda payload:sent.append(payload) or envelope())
    client.call_json(system='Return JSON',user='safe request',max_tokens=100)
    assert sent[0]['thinking']=={'type':'disabled'} and sent[0]['model']=='deepseek-flash'
    assert ledger.snapshot()['committed_upper_rmb']==0.0006
    ledger.con.close();second=Ledger(path)
    BoundedClient(second,lambda p:pytest.fail('cached request must not resend')).call_json(system='Return JSON',user='safe request',max_tokens=100)
    assert second.snapshot()['attempts']==1


def test_timeout_retains_two_reservations_and_restart_cannot_retry(tmp_path):
    path=tmp_path/'ledger.sqlite';ledger=Ledger(path);calls=[]
    def timeout(payload):calls.append(1);raise TimeoutError('FAKE_PRIVATE_TEXT_MUST_NOT_BE_LOGGED')
    with pytest.raises(RequestStop):BoundedClient(ledger,timeout).call_json(system='JSON',user='safe',max_tokens=100)
    snapshot=ledger.snapshot();assert snapshot['attempts']==2 and snapshot['unknown_reserved_rmb']>0
    ledger.con.close();second=Ledger(path)
    with pytest.raises(RequestStop):BoundedClient(second,timeout).call_json(system='JSON',user='safe',max_tokens=100)
    assert len(calls)==2 and second.snapshot()==snapshot
    assert b'FAKE_PRIVATE_TEXT' not in path.read_bytes()


def test_crash_after_reservation_is_not_refunded(tmp_path):
    ledger=Ledger(tmp_path/'db');ledger.reserve('lost-response',200,100);before=ledger.snapshot();ledger.con.close()
    assert Ledger(tmp_path/'db').snapshot()==before


def test_sample_and_total_budget_stop_before_network(tmp_path):
    ledger=Ledger(tmp_path/'db');ledger.con.execute('INSERT INTO attempts(request_key,phase,charged,status) VALUES("prior","sample",999999,"reserved")');ledger.con.commit()
    client=BoundedClient(ledger,lambda p:pytest.fail('must stop before send'))
    with pytest.raises(BudgetStop):client.call_json(system='JSON',user='safe',max_tokens=100)
    ledger.phase='remaining';ledger.con.execute('UPDATE attempts SET charged=?',(TOTAL_LIMIT-1,));ledger.con.commit()
    with pytest.raises(BudgetStop):BoundedClient(ledger,lambda p:pytest.fail('must stop')).call_json(system='JSON',user='different',max_tokens=100)
    assert ledger.snapshot()['attempts']==1


def test_no_usage_keeps_full_reserve_and_output_reasoning_mismatch_halts(tmp_path):
    ledger=Ledger(tmp_path/'db');BoundedClient(ledger,lambda p:envelope(usage=False)).call_json(system='JSON',user='safe',max_tokens=100)
    assert ledger.snapshot()['unknown_reserved_rmb']>0
    bad=envelope();bad['usage']['completion_tokens_details']['reasoning_tokens']=1
    with pytest.raises(RequestStop):BoundedClient(ledger,lambda p:bad).call_json(system='JSON',user='other',max_tokens=100)
    with pytest.raises(RequestStop):ledger.reserve('new',1,1)


def test_validator_failure_and_transient_share_two_attempt_total(tmp_path):
    ledger=Ledger(tmp_path/'db');calls=[]
    def validate(value):raise ValueError('wrong shape')
    with pytest.raises(RequestStop):BoundedClient(ledger,lambda p:calls.append(1) or envelope()).call_json_validated(system='JSON',user='safe',max_tokens=100,validator=validate,transient_retries=20,shape_retries=20)
    assert len(calls)==2


def test_guard_filters_path_and_refuses_keys_at_send_boundary(tmp_path):
    ledger=Ledger(tmp_path/'db');sent=[];client=BoundedClient(ledger,lambda p:sent.append(p) or envelope())
    client.call_json(system='Return JSON',user='Work at /home/private/repo',max_tokens=100)
    assert '/home/' not in json.dumps(sent)
    with pytest.raises(ValueError):client.call_json(system='Return JSON',user='password=FAKE_FIXTURE',max_tokens=100)
    assert len(sent)==1


def test_sanitized_work_database_and_copy_preserve_source(tmp_path,monkeypatch):
    from scripts.backfill_ai_bounded import plan_source,work_database,execute
    from daytrace.db import connect,init_db,upsert_events
    from daytrace.schema import TraceEvent
    from daytrace.daily_report import regenerate_day_from_db
    db=tmp_path/'source.sqlite';con=connect(db);init_db(con)
    safe=event('Fix navigation');secret=event('password=FAKE_FIXTURE');secret['id']='secret'
    upsert_events(con,[TraceEvent(**safe),TraceEvent(**secret)])
    regenerate_day_from_db(con,'2026-09-29',include_ai=False)
    before=[tuple(r) for r in con.execute('SELECT * FROM events ORDER BY id')]
    manifest,events,audit=plan_source(db)
    assert len(events)==1 and len(audit)==1
    work=work_database(tmp_path/'work.sqlite',manifest,events)
    assert 'FAKE_FIXTURE' not in work.execute('SELECT group_concat(summary) FROM events').fetchone()[0]
    assert all(r[0]=='{}' for r in work.execute('SELECT evidence_json FROM events'))
    assert before==[tuple(r) for r in con.execute('SELECT * FROM events ORDER BY id')]


def test_send_guard_preserves_source_project_title():
    from daytrace.ai_privacy import final_guard
    _,user=final_guard('Return JSON','e1 | 12:00 | codex/repo | Fix navigation')
    assert user.endswith('codex/repo | Fix navigation')


def test_legacy_executor_is_retired_without_mutating_data(tmp_path):
    from scripts import backfill_ai_bounded as runner
    with pytest.raises(RequestStop,match='retired'):
        runner.execute(tmp_path/'nonexistent.sqlite',None,None,'sample')
    assert not list(tmp_path.iterdir())


def test_claim_guard_rejects_request_to_completion_hallucination():
    from daytrace.ai_privacy import validate_request_claims
    for claim in ('完成预算审计','Sent the requested greeting email','晚上拉取了最新文章','账号问题已经解决'):
        with pytest.raises(ValueError):validate_request_claims({'overview':{'narrative':{'zh':claim}}})
    assert validate_request_claims({'overview':{'narrative':{'zh':'用户请求审计预算，并询问报销分类。','en':'The user requested a budget audit.'}}})


def test_unverified_claim_suppression_is_explicit_and_counted():
    from daytrace.ai_privacy import suppress_unverified_claims
    value,count=suppress_unverified_claims({'summary':{'zh':'完成了预算审计','en':'Sent the requested email'},'status':'done','what_was_done':[{'zh':'用户询问内存规格','en':'Asked about RAM specifications'}]})
    assert count==3 and '未核实' in value['summary']['zh'] and 'unverified' in value['summary']['en']
    assert value['status']=='in_progress' and value['what_was_done'][0]['zh']=='用户询问内存规格'


def test_withheld_narrative_fallback_quotes_evidence_not_inferred_success():
    from scripts.verify_ai_backfill import evidence_fallback
    result=evidence_fallback([{'title':'请检查服务器内存扩容','source':'codex'},{'title':'Add repository index','source':'git'}],3)
    assert '「请检查服务器内存扩容」' in result['zh'] and '执行结果未核实' in result['zh']
    assert 'Git 可见提交标题' in result['zh'] and '3 条未提交' in result['zh']
    assert '本地事实说明' in result['zh'] and 'not AI prose' in result['en']
