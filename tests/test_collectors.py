import json

from scripts.collect_hermes_sessions import (
    collect_hermes_events,
    extract_content_text,
    is_probable_peer_agent_message,
    message_sender_is_bot,
    project_from_chat_name,
)
from daytrace.schema import TraceEvent


def test_project_from_chat_name_uses_hermes_suffix():
    assert project_from_chat_name("Hermes - DayTrace") == "DayTrace"
    assert project_from_chat_name("Hermes - Daily Briefing") == "Daily Briefing"
    assert project_from_chat_name("Hermes - 日常") == "日常"


def test_content_extraction_ignores_feishu_image_base64_parts():
    content = extract_content_text(
        [
            {"type": "text", "text": "[Image]\n@Hermes 怎么开啊"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + "A" * 1000}},
        ]
    )

    assert content == "[Image]\n@Hermes 怎么开啊"
    assert "base64" not in content
    assert len(content) < 50


def test_peer_agent_feishu_messages_are_not_human_inputs():
    assert is_probable_peer_agent_message(
        '[Replying to: "@Hermes MTL 请直接贴 JSONL"]\n\n直接交付 omen 的 daytrace.event.v1 JSONL（5 行，device_id=omen）：'
    )
    assert is_probable_peer_agent_message(
        '[Replying to: "@Hermes Mac 请检查同步状态"]\n\n收到，我已完成检查，结果如下：'
    )
    assert is_probable_peer_agent_message(
        "[Replying to: '@Hermes Research 请交付报告']\n\n执行结果：已生成报告。"
    )
    assert is_probable_peer_agent_message("MTL 这边已上线，也已把 Feishu 配置改成 allow_bots=mentions")
    assert not is_probable_peer_agent_message("我把你拉到这个新群里了，这里还有 Hermes MTL")
    assert not is_probable_peer_agent_message(
        '[Replying to: "@Hermes 请看这里"]\n\n我觉得这里应该继续保留成人类输入。'
    )


def test_bot_sender_metadata_is_preferred_over_display_name_heuristics():
    assert message_sender_is_bot({"role": "user", "content": "Omen 完成", "is_bot": True})
    assert message_sender_is_bot({"role": "user", "source": {"is_bot": True}})
    assert message_sender_is_bot({"role": "user", "source": {"sender_type": "app"}})
    assert message_sender_is_bot({"role": "user", "sender_type": "bot"})
    assert message_sender_is_bot(
        {"role": "user", "raw_message": {"event": {"sender": {"sender_type": "bot"}}}}
    )
    assert not message_sender_is_bot({"role": "user", "content": "我提到了 Omen 这个 agent"})


def test_collect_hermes_events_skips_context_compaction_messages(tmp_path):
    session_id = "20260514_121000_compaction"
    (tmp_path / f"{session_id}.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "role": "user",
                        "content": "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted",
                        "timestamp": "2026-05-14T12:10:00-04:00",
                    }
                ),
                json.dumps(
                    {
                        "role": "user",
                        "content": "真正的用户输入",
                        "timestamp": "2026-05-14T12:11:00-04:00",
                    },
                    ensure_ascii=False,
                ),
            ]
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
                    "origin": {"chat_name": "Hermes - DayTrace", "chat_type": "group"},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    events = collect_hermes_events("2026-05-14", tmp_path, limit=10)

    assert [event.summary for event in events] == ["真正的用户输入"]


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
                json.dumps(
                    {
                        "role": "user",
                        "content": '[Replying to: "@Hermes MTL 请发 JSONL"]\n\n收到，我这边 branch 机器信息如下：DESKTOP-O9DG52H',
                        "timestamp": "2026-05-14T12:02:00-04:00",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "role": "user",
                        "content": '[Replying to: "@Hermes Mac 请检查同步状态"]\n\n收到，我已完成检查，结果如下：',
                        "timestamp": "2026-05-14T12:03:00-04:00",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "role": "user",
                        "content": "Omen worker 已完成同步检查。",
                        "timestamp": "2026-05-14T12:04:00-04:00",
                        "source": {"is_bot": True, "user_name": "Omen"},
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


