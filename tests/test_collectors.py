import json

from scripts.collect_docs import collect_doc_events
from scripts.collect_hermes_sessions import collect_hermes_events, project_from_chat_name
from scripts.collect_macos_activity import parse_idle_seconds
from scripts.collect_outcomes_milestones import make_outcomes
from daytrace.schema import TraceEvent


def test_collect_doc_events_detects_modified_markdown(tmp_path):
    p = tmp_path / "Project" / "note.md"
    p.parent.mkdir()
    p.write_text("hello daytrace", encoding="utf-8")
    events = collect_doc_events("2026-05-13", [tmp_path], include_all_for_test=True)
    assert len(events) == 1
    assert events[0].source == "docs"
    assert events[0].kind == "document_modified"
    assert events[0].evidence["path"].endswith("note.md")


def test_parse_idle_seconds_from_ioreg_output():
    assert parse_idle_seconds('    "HIDIdleTime" = 2500000000') == 2.5


def test_project_from_chat_name_uses_hermes_suffix():
    assert project_from_chat_name("Hermes - DayTrace") == "DayTrace"
    assert project_from_chat_name("Hermes - Daily Briefing") == "Daily Briefing"
    assert project_from_chat_name("Hermes - 日常") == "日常"


def test_collect_hermes_events_keeps_only_user_inputs_and_uses_chat_project(tmp_path):
    session_id = "20260514_120000_test"
    unmapped_session_id = "20260514_130000_unmapped"
    (tmp_path / f"{session_id}.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "role": "user",
                        "content": "请检查一下这个明显 bug",
                        "timestamp": "2026-05-14T12:00:00-04:00",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "已修复并测试通过。",
                        "timestamp": "2026-05-14T12:01:00-04:00",
                    },
                    ensure_ascii=False,
                ),
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / f"{unmapped_session_id}.jsonl").write_text(
        json.dumps(
            {
                "role": "user",
                "content": "这个没有群聊来源，不应该进入 Hermes 主表",
                "timestamp": "2026-05-14T13:00:00-04:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "sessions.json").write_text(
        json.dumps(
            {
                "agent:main:feishu:group:test": {
                    "session_id": session_id,
                    "display_name": "Hermes - DayTrace",
                    "chat_type": "group",
                    "origin": {
                        "chat_name": "Hermes - DayTrace",
                        "chat_id": "oc_test",
                        "chat_type": "group",
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    events = collect_hermes_events("2026-05-14", tmp_path, limit=10)

    assert len(events) == 1
    assert events[0].kind == "user_input"
    assert events[0].project_guess == "DayTrace"
    assert events[0].evidence["chat_name"] == "Hermes - DayTrace"


def test_make_outcomes_promotes_hermes_assistant_results():
    event = TraceEvent(
        id="assistant-1",
        source="hermes",
        kind="assistant_result",
        start="2026-05-14T10:00:00",
        end=None,
        title="Dashboard updated",
        summary="已改好了，测试通过。",
        project_guess="daytrace",
        confidence=0.8,
        sensitivity="normal",
        evidence={"session": "test"},
    )
    outcomes = make_outcomes("2026-05-14", [event])
    assert len(outcomes) == 1
    assert outcomes[0].source == "outcome"
    assert outcomes[0].kind == "agent_result_reported"
    assert outcomes[0].evidence["from_event"] == "assistant-1"
