import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from scripts import collect_git
from daytrace import ai_client


def test_configured_timezone_converts_utc_to_shanghai():
    code = 'from scripts.collect_codex import iso_from_epoch; print(iso_from_epoch(1786803553))'
    result = subprocess.run([sys.executable, '-c', code], env={**os.environ, 'DAYTRACE_TIMEZONE':'Asia/Shanghai'}, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == '2026-08-15T22:19:13'


def test_ai_disabled_even_with_existing_key(monkeypatch):
    monkeypatch.setenv('DAYTRACE_DISABLE_AI', '1')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-not-real')
    assert not ai_client.is_available()
    import pytest
    with pytest.raises(ai_client.LLMError, match='AI disabled'):
        ai_client.call_json(system='test', user='private')


def test_git_history_converts_timezone_and_omits_current_dirty_state(tmp_path, monkeypatch):
    repo=tmp_path/'repo'
    repo.mkdir()
    (repo/'.git').mkdir()
    calls=[]
    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd,0,stdout='a'*40+'\t2026-08-15T23:30:00-04:00\tExample\n',stderr='')
    monkeypatch.setattr(collect_git.subprocess,'run',run)
    monkeypatch.setattr(collect_git,'LOCAL_TZ',ZoneInfo('Asia/Shanghai'))
    events=collect_git.collect_git_events('2026-08-16',[repo])
    assert len(events)==1
    assert events[0].start=='2026-08-16T11:30:00'
    assert not any('status' in c for c in calls)
    assert '--since=2026-08-16T00:00:00+08:00' in calls[0]


def test_current_desktop_messages_and_context_filter():
    from scripts.collect_codex import rollout_user_text
    obj = {"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"<environment_context>context</environment_context>"},{"type":"input_text","text":"Please restore my website"}]}}
    assert rollout_user_text(obj) == "Please restore my website"
    obj["payload"]["role"] = "assistant"
    assert rollout_user_text(obj) == ""
