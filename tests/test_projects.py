import json
import subprocess
from pathlib import Path
import pytest
import yaml
from daytrace.db import connect, init_db, upsert_events, query_events
from daytrace.schema import TraceEvent
from daytrace.projects import canonical_remote, checkout_info, sync_projects, save_aliases


def git(path,*args):
    return subprocess.run(['git','-C',str(path),*args],check=True,capture_output=True,text=True).stdout.strip()


def repo(path,remote=None):
    path.mkdir(parents=True)
    git(path,'init','-q')
    if remote:git(path,'remote','add','origin',remote)
    return path


def event(eid,path=None,guess=None):
    return TraceEvent(id=eid,source='codex',kind='user_input',start='2026-09-01T10:00:00',end=None,title='User action',summary='Original private summary',project_guess=guess,sensitivity='normal',evidence={'cwd':str(path)} if path else {})


def setup(tmp_path,events):
    con=connect(tmp_path/'trace.sqlite');init_db(con);upsert_events(con,events)
    cfg=tmp_path/'projects.yaml';cfg.write_text(yaml.safe_dump({'roots':[str(tmp_path/'repos')],'aliases':{},'paths':{}}))
    return con,cfg


def test_remote_identity_sanitizes_secrets_and_merges_protocols():
    expected='github.com/owner/repo'
    assert canonical_remote('git@github.com:Owner/Repo.git')==expected
    assert canonical_remote('https://user:SECRET@github.com/owner/repo.git?token=SECRET')==expected
    assert canonical_remote('ssh://git@github.com/Owner/Repo.git')==expected
    assert canonical_remote('/tmp/local.git') is None
    assert canonical_remote('file:///tmp/local.git') is None


def test_same_remote_checkouts_merge_and_migration_is_idempotent(tmp_path):
    a=repo(tmp_path/'repos/a','git@github.com:owner/shared.git')
    b=repo(tmp_path/'repos/b','https://github.com/owner/shared.git')
    con,cfg=setup(tmp_path,[event('a',a,'old-project'),event('b',b,'another-old-name')])
    con.execute("INSERT INTO work_items(record_id,title) VALUES('rec_old','Historical task')")
    con.execute("INSERT INTO event_work_item_links(event_id,record_id,match_type) VALUES('a','rec_old','manual')");con.commit()
    raw_before=[tuple(r) for r in con.execute('SELECT id,title,summary,evidence_json FROM events ORDER BY id')]
    legacy_before=[tuple(r) for r in con.execute('SELECT * FROM work_items')]
    links_before=[tuple(r) for r in con.execute('SELECT * FROM event_work_item_links')]
    first=sync_projects(con,cfg)
    attribution=[tuple(r) for r in con.execute('SELECT id,repo_project_id,project_guess,original_project_guess FROM events ORDER BY id')]
    assert first['projects']==1 and first['checkouts']==2 and first['linked_events']==2
    assert attribution[0][1]==attribution[1][1]
    assert attribution[0][2:] == ('shared','old-project')
    second=sync_projects(con,cfg)
    assert first==second
    assert attribution==[tuple(r) for r in con.execute('SELECT id,repo_project_id,project_guess,original_project_guess FROM events ORDER BY id')]
    assert raw_before==[tuple(r) for r in con.execute('SELECT id,title,summary,evidence_json FROM events ORDER BY id')]
    assert legacy_before==[tuple(r) for r in con.execute('SELECT * FROM work_items')]
    assert links_before==[tuple(r) for r in con.execute('SELECT * FROM event_work_item_links')]
    assert query_events(con)[0]['repo_project_id']
    # Re-import of the same source event cannot create a duplicate or lose its original attribution.
    upsert_events(con,[event('a',a,'old-project')]);sync_projects(con,cfg)
    assert con.execute('SELECT count(*) FROM events').fetchone()[0]==2
    assert con.execute("SELECT original_project_guess FROM events WHERE id='a'").fetchone()[0]=='old-project'


def test_remote_less_worktree_shares_identity_and_plain_directory_is_ignored(tmp_path):
    a=repo(tmp_path/'repos/a')
    git(a,'-c','user.name=Test','-c','user.email=test@example.invalid','commit','--allow-empty','-qm','Initial')
    b=tmp_path/'repos/b';git(a,'worktree','add','--detach',str(b))
    plain=tmp_path/'plain';plain.mkdir()
    assert checkout_info(plain) is None
    assert checkout_info(a)['project_id']==checkout_info(b)['project_id']
    con,cfg=setup(tmp_path,[event('a',a),event('b',b),event('plain',plain)])
    stats=sync_projects(con,cfg)
    assert stats['projects']==1 and stats['checkouts']==2
    assert stats['linked_events']==2 and stats['unlinked_events']==1


def test_same_basename_different_remote_not_merged_and_manual_old_mapping(tmp_path):
    a=repo(tmp_path/'repos/a','git@github.com:owner1/shared.git')
    b=repo(tmp_path/'repos/b','git@github.com:owner2/shared.git')
    con,cfg=setup(tmp_path,[event('a',a,'shared'),event('b',b,'shared'),event('old',None,'old-checkout'),event('ambiguous',None,'shared')])
    result=sync_projects(con,cfg)
    assert result['projects']==2 and result['unlinked_events']==2
    pa=con.execute("SELECT repo_project_id FROM events WHERE id='a'").fetchone()[0]
    pb=con.execute("SELECT repo_project_id FROM events WHERE id='b'").fetchone()[0]
    assert pa!=pb
    assert len({r[0] for r in con.execute('SELECT name FROM repo_projects')})==2
    save_aliases(con,[('old-checkout',pa)],cfg)
    assert con.execute("SELECT repo_project_id FROM events WHERE id='old'").fetchone()[0]==pa
    count=con.execute('SELECT count(*) FROM events').fetchone()[0]
    save_aliases(con,[('old-checkout',pa)],cfg)
    assert con.execute('SELECT count(*) FROM events').fetchone()[0]==count
    prior=cfg.read_bytes()
    with pytest.raises(ValueError,match='Unknown local repository'):
        save_aliases(con,[('old-checkout','rec_feishu_legacy')],cfg)
    assert cfg.read_bytes()==prior


def test_legacy_feishu_entrypoints_do_not_run_commands(monkeypatch,tmp_path):
    from daytrace import work_items,report_delivery
    from scripts import cleanup_feishu_reports,translate_work_items
    def blocked(*args,**kwargs):raise AssertionError('External process must not run')
    monkeypatch.setattr(subprocess,'run',blocked)
    cfg=tmp_path/'enabled.yaml';cfg.write_text('enabled: true\ntables: [{key: tasks}]\n')
    assert work_items.load_config(cfg) is None
    for call in [lambda:work_items._run_lark([]),lambda:work_items.sync_from_feishu(None,{'tables':[{}]}),lambda:report_delivery._lark([]),lambda:cleanup_feishu_reports._lark([])]:
        with pytest.raises(RuntimeError,match='disconnected'):call()
    assert cleanup_feishu_reports.main()==2
    assert translate_work_items.main()==2


def test_old_feishu_form_returns_gone_without_mutation(monkeypatch):
    from dashboard import server
    handler=object.__new__(server.Handler)
    handler.path='/api/work-items/alias'
    seen=[]
    monkeypatch.setattr(server,'json_response',lambda h,obj,status=200:seen.append(status))
    monkeypatch.setattr(server,'_apply_audit_aliases',lambda *a:pytest.fail('Legacy form must not mutate mappings'))
    handler.do_POST()
    assert seen==[410]


def test_explicit_historical_path_maps_without_local_checkout(tmp_path):
    a=repo(tmp_path/'repos/a','git@github.com:owner/project.git')
    con,cfg=setup(tmp_path,[event('old','/old-machine/Projects/renamed/subdir','legacy')])
    sync_projects(con,cfg)
    pid=checkout_info(a)['project_id']
    data=yaml.safe_load(cfg.read_text());data['paths']={'/old-machine/Projects/renamed':pid};cfg.write_text(yaml.safe_dump(data))
    assert sync_projects(con,cfg)['linked_events']==1
    row=con.execute("SELECT project_guess,original_project_guess FROM events WHERE id='old'").fetchone()
    assert tuple(row)==('project','legacy')


def test_git_metadata_timeout_is_nonfatal(tmp_path,monkeypatch):
    from daytrace import projects
    def timeout(*args,**kwargs):raise subprocess.TimeoutExpired(args[0],3)
    monkeypatch.setattr(projects.subprocess,'run',timeout)
    assert projects.checkout_info(tmp_path) is None


def test_background_roots_only_retains_cached_external_mapping(tmp_path,monkeypatch):
    from daytrace import projects
    inside=repo(tmp_path/'repos/inside','git@github.com:owner/inside.git')
    outside=repo(tmp_path/'protected/outside','git@github.com:owner/outside.git')
    con,cfg=setup(tmp_path,[event('inside',inside),event('outside',outside)])
    first=projects.sync_projects(con,cfg)
    assert first['projects']==2
    original=projects.checkout_info
    def guarded(path):
        assert not str(path).startswith(str(tmp_path/'protected'))
        return original(path)
    monkeypatch.setenv('DAYTRACE_PROJECT_METADATA_ROOTS_ONLY','1')
    monkeypatch.setattr(projects,'checkout_info',guarded)
    second=projects.sync_projects(con,cfg)
    assert second['projects']==2 and second['linked_events']==2
