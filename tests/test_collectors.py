from scripts.collect_docs import collect_doc_events
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
