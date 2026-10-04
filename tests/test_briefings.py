import json
import pytest
from daytrace import briefings as b
from daytrace.db import connect,init_db,upsert_events
from daytrace.schema import TraceEvent


def evidence(id='e1',state='requested',scope='request_or_discussion',project='p'):
    return {'id':id,'project_id':project,'project_name':'论文项目','date':'2026-10-03','kind':'user_input' if state=='requested' else 'git_commit','supports':[state],'scope':scope,'text':'确认并处理 示意图 的图注更新','source_label':'测试来源','source_locator':'/Users/PRIVATE/repo','visibility':'filtered_export'}


def pack(*items):return {'kind':'weekly','period':'2026-W40','days':['2026-10-03'],'coverage':{},'evidence':list(items),'event_hash':'original-hash','evidence_hash':'evidence-hash'}


def payload(e,*,state=None,scope=None,text='确认并处理了图注更新'):
    return {'headline':'图注进入迭代','projects':[{'project_id':e['project_id'],'claims':[{'text':text,'state':state or e['supports'][0],'scope':scope or e['scope'],'evidence_ids':[e['id']],'support_quotes':{e['id']:e['text']}}]}]}


def test_supported_implemented_claim_is_not_keyword_suppressed():
    e=evidence(state='implemented',scope='repository_change')
    result=b.validate_brief(payload(e),pack(e))
    assert result['projects'][0]['claims'][0]['text']=='确认并处理了图注更新'


def test_reject_one_unsupported_claim_without_destroying_other_content():
    e=evidence();p=payload(e,state='implemented');p['projects'][0]['claims']+=payload(e,text='讨论了图注的修改要求')['projects'][0]['claims']
    result=b.validate_brief(p,pack(e))
    assert len(result['projects'][0]['claims'])==1
    assert result['rejected_claims'][0]['reason']=='unsupported_state_or_scope'


@pytest.mark.parametrize('state,scope',[('verified','browser_acceptance'),('decided','request_or_discussion'),('open','request_or_discussion')])
def test_request_does_not_prove_completion_decision_or_open_status(state,scope):
    e=evidence()
    with pytest.raises(ValueError,match='no supported claims'):b.validate_brief(payload(e,state=state,scope=scope),pack(e))


def test_git_does_not_prove_verification_and_evidence_cannot_cross_projects():
    e=evidence(state='implemented',scope='repository_change')
    with pytest.raises(ValueError):b.validate_brief(payload(e,state='verified'),pack(e))
    p=payload(e);p['projects'][0]['project_id']='other'
    with pytest.raises(ValueError):b.validate_brief(p,pack(e))


def test_unknown_ids_and_invented_quotes_fail_closed():
    e=evidence();p=payload(e);p['projects'][0]['claims'][0]['support_quotes'][e['id']]='invented proof'
    with pytest.raises(ValueError):b.validate_brief(p,pack(e))
    p=payload(e);p['projects'][0]['claims'][0]['evidence_ids']=['missing']
    with pytest.raises(ValueError):b.validate_brief(p,pack(e))


def test_export_omits_local_material_until_explicitly_included():
    a=evidence();local=evidence('local');local['visibility']='local_only';local['text']='样稿 V1 保留待核实事项'
    p=pack(a,local)
    default=b.prompt_for(p)
    assert '样稿 V1' not in default['user']
    approved=b.prompt_for(p,include_reviewed_facts=True)
    assert '样稿 V1' in approved['user'] and '/Users/' not in approved['user'] and 'source_locator' not in approved['user']


def test_new_generator_uses_evidence_gate_not_legacy_phrase_filter(monkeypatch):
    from daytrace import ai_privacy
    monkeypatch.setattr(ai_privacy,'suppress_unverified_claims',lambda *a:pytest.fail('legacy blanket filter must not run'))
    e=evidence(state='implemented',scope='repository_change')
    result=b.generate_draft(pack(e),lambda prompt:payload(e))
    assert result['projects'][0]['claims'][0]['state']=='implemented'


def test_generator_cannot_cite_non_exported_local_facts():
    e=evidence();e['visibility']='local_only'
    with pytest.raises(ValueError):b.generate_draft(pack(e),lambda prompt:payload(e))


def test_pack_keeps_privacy_boundary_and_merges_sessions(tmp_path):
    con=connect(tmp_path/'db');init_db(con)
    events=[]
    for id,kind,text,sid in [('header','thread_started','开始讨论','s1'),('one','user_input','调整 示意图 的图注','s1'),('two','user_input','继续比较结果叙事','s2'),('secret','user_input','password=FAKE_FIXTURE','s2'),('pasted','user_input','first\nassistant: PRIVATE','s2')]:
        events.append(TraceEvent(id=id,source='codex',kind=kind,start='2026-10-03T12:00:00',end=None,title=text,summary=text,project_guess='sample-paper',sensitivity='normal',evidence={'session_id':sid,'raw_text':text}))
    upsert_events(con,events);result=b.build_pack(con,'weekly','2026-W40')
    assert len(result['evidence'])==2 and result['coverage']['sessions_by_project']['sample-paper']==2
    body=b.prompt_for(result)['user']
    assert 'FAKE_FIXTURE' not in body and 'PRIVATE' not in body


def test_stable_ids_with_changed_content_mark_saved_brief_stale_and_archive_old(tmp_path):
    con=connect(tmp_path/'db');init_db(con);e=evidence();p=pack(e)
    raw=[{'id':'event','title':'old'}];p['event_hash']=b.event_fingerprint(raw)
    b.save_brief(con,'weekly','2026-W40',payload(e),p,origin='local_reviewed')
    assert not b.load_brief(con,'weekly','2026-W40',events=raw)['_stale']
    assert b.load_brief(con,'weekly','2026-W40',events=[{'id':'event','title':'new'}])['_stale']
    b.save_brief(con,'weekly','2026-W40',payload(e),p,origin='local_reviewed')
    assert con.execute('SELECT count(*) FROM report_brief_versions').fetchone()[0]==1


def test_weekly_reads_cached_ai_even_when_disabled_and_never_calls_provider(tmp_path,monkeypatch):
    from dashboard import server
    from daytrace import ai_client
    def deny(*a,**kw):pytest.fail('page read must not load credentials or call provider')
    for name in ('is_available','call_json','call_json_validated','_load_secrets_into_environ'):monkeypatch.setattr(ai_client,name,deny)
    path=tmp_path/'week.json';events=[{'id':'x','title':'old'}]
    path.write_text(json.dumps({'events_hash':server._events_hash(events),'value':{'headline':'已保存AI周报'}}))
    monkeypatch.setattr(server,'_week_ai_cache_path',lambda week:path)
    result=server._ai_weekly_summary(week='2026-W40',events=events,by_project=[],total_minutes=0,active_days=1)
    assert result['headline']=='已保存AI周报'
    events[0]['title']='edited';assert server._ai_weekly_summary(week='2026-W40',events=events,by_project=[],total_minutes=0,active_days=1)['_stale']
    path.unlink()
    assert server._ai_weekly_summary(week='2026-W40',events=events,by_project=[],total_minutes=0,active_days=1)['_not_generated']


def test_read_only_weekly_page_with_fake_key_and_no_cache(tmp_path,monkeypatch):
    from dashboard import server
    from daytrace import ai_client
    db=tmp_path/'db';con=connect(db);init_db(con)
    upsert_events(con,[TraceEvent(id='one',source='codex',kind='user_input',start='2026-10-03T12:00:00',end=None,title='讨论图注',summary='讨论图注',project_guess='paper',sensitivity='normal')]);con.close()
    monkeypatch.setenv('DEEPSEEK_API_KEY','FAKE_TEST_KEY_NOT_REAL')
    monkeypatch.setattr(server,'_week_ai_cache_path',lambda week:tmp_path/'absent.json')
    def deny(*a,**kw):pytest.fail('no page-triggered AI')
    for name in ('is_available','call_json','call_json_validated','_load_secrets_into_environ'):monkeypatch.setattr(ai_client,name,deny)
    html=server.weekly_page(db,'2026-W40')
    assert '浏览页面不会触发付费生成' in html and 'DEEPSEEK_API_KEY 未设置' not in html


def test_render_labels_local_and_ai_differently_and_escapes_text():
    e=evidence();v=b.validate_brief(payload(e,text='<script>alert(1)</script> 请求'),pack(e));v['origin']='local_reviewed'
    html=b.render_brief(v)
    assert '本地证据整理' in html and '<script>' not in html
    v['origin']='ai_reviewed';assert 'AI 生成' in b.render_brief(v)


def test_weekly_pack_ignores_degraded_daily_channels(tmp_path):
    con=connect(tmp_path/'db');init_db(con)
    e=TraceEvent(id='one',source='codex',kind='user_input',start='2026-10-03T12:00:00',end=None,title='比较方法的输入与验证边界',summary='比较方法的输入与验证边界',project_guess='sample-paper',sensitivity='normal')
    upsert_events(con,[e]);p=b.build_pack(con,'weekly','2026-W40')
    assert '比较方法' in b.prompt_for(p)['user']
    assert 'day_channel' not in b.prompt_for(p)['user']


def test_weekly_export_prefers_saved_local_evidence_brief(tmp_path,monkeypatch):
    from daytrace.report_export import archive_markdown_for_week
    from dashboard import server
    db=tmp_path/'db';con=connect(db);init_db(con);e=evidence()
    b.save_brief(con,'weekly','2026-W40',payload(e,text='讨论图注与验证边界'),pack(e),origin='local_reviewed');con.close()
    monkeypatch.setattr(server,'_week_ai_cache_path',lambda week:tmp_path/'none.json')
    text=archive_markdown_for_week(db,'2026-W40',lang='zh')
    assert '本地证据整理' in text and '讨论图注与验证边界' in text


def test_dirty_worktree_snapshot_is_context_not_project_progress(tmp_path):
    con=connect(tmp_path/'db');init_db(con)
    upsert_events(con,[TraceEvent(id='dirty',source='git',kind='working_tree_change',start='2026-10-03T12:00:00',end=None,title='paper: 3 uncommitted changes',summary='modified draft',project_guess='paper',sensitivity='normal')])
    p=b.build_pack(con,'weekly','2026-W40')
    assert p['evidence'][0]['supports']==[] and p['evidence'][0]['scope']=='working_tree_context'
