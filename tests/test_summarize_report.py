from daytrace.schema import TraceEvent
from daytrace.summarize import (
    aggregate_events,
    render_markdown_report,
    render_feishu_summary,
)


def event(id, source, kind, title, project="DayTrace", confidence=0.8):
    return TraceEvent(
        id=id,
        source=source,
        kind=kind,
        start="2026-05-13T10:00:00",
        end=None,
        title=title,
        summary=title + " summary",
        project_guess=project,
        confidence=confidence,
        sensitivity="normal",
        evidence={"path": "x"},
    )


def test_aggregate_groups_by_source_and_project():
    daily = aggregate_events(
        "2026-05-13",
        [
            event("1", "git", "commit", "commit A"),
            event("2", "docs", "document_modified", "doc B"),
            event(
                "3",
                "macos_activity",
                "app_focus_sample",
                "Feishu",
                project=None,
                confidence=0.2,
            ),
        ],
    )
    assert daily.date == "2026-05-13"
    assert daily.source_counts["git"] == 1
    assert daily.project_counts["DayTrace"] == 2
    assert daily.low_confidence_count == 1


def test_markdown_report_contains_key_sections():
    daily = aggregate_events("2026-05-13", [event("1", "git", "commit", "commit A")])
    md = render_markdown_report(daily)
    assert "# DayTrace · 2026-05-13" in md
    assert "## 项目进展" in md
    assert "## 证据与低置信度项" in md
    assert "commit A" in md


def test_feishu_summary_is_short():
    daily = aggregate_events("2026-05-13", [event("1", "git", "commit", "commit A")])
    summary = render_feishu_summary(daily)
    assert "DayTrace · 2026-05-13" in summary
    assert len(summary.splitlines()) <= 10
