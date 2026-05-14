from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable

from .schema import TraceEvent


@dataclass
class DailyTrace:
    date: str
    events: list[TraceEvent]
    source_counts: dict[str, int]
    kind_counts: dict[str, int]
    project_counts: dict[str, int]
    low_confidence_count: int


def aggregate_events(date: str, events: Iterable[TraceEvent]) -> DailyTrace:
    event_list = sorted(
        list(events), key=lambda e: (e.start, e.source, e.kind, e.title)
    )
    return DailyTrace(
        date=date,
        events=event_list,
        source_counts=dict(Counter(e.source for e in event_list)),
        kind_counts=dict(Counter(e.kind for e in event_list)),
        project_counts=dict(Counter((e.project_guess or "未归因") for e in event_list)),
        low_confidence_count=sum(
            1 for e in event_list if e.confidence < 0.5 or not e.project_guess
        ),
    )


def _top(counter: dict[str, int], limit: int = 8) -> list[tuple[str, int]]:
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]


def render_markdown_report(daily: DailyTrace) -> str:
    lines: list[str] = []
    lines.append(f"# DayTrace · {daily.date}")
    lines.append("")
    lines.append("## 今日概览")
    lines.append("")
    if daily.events:
        lines.append(
            f"今天从 {len(daily.source_counts)} 个数据源收集到 {len(daily.events)} 条活动痕迹。"
        )
        project_text = "、".join(
            f"{name}（{count}）" for name, count in _top(daily.project_counts, 5)
        )
        lines.append(f"主要项目/归因：{project_text}。")
    else:
        lines.append("今天还没有收集到活动痕迹。")
    lines.append("")
    lines.append("## 数据源覆盖")
    lines.append("")
    for source, count in _top(daily.source_counts):
        lines.append(f"- {source}: {count} 条")
    if not daily.source_counts:
        lines.append("- 暂无")
    lines.append("")
    lines.append("## 项目进展")
    lines.append("")
    by_project: dict[str, list[TraceEvent]] = defaultdict(list)
    for event in daily.events:
        by_project[event.project_guess or "未归因"].append(event)
    for project, events in _top({k: len(v) for k, v in by_project.items()}, 10):
        lines.append(f"### {project}")
        for event in by_project[project][:8]:
            lines.append(
                f"- **{event.source}/{event.kind}** {event.title} — {event.summary}"
            )
        lines.append("")
    lines.append("## 代码与文档")
    lines.append("")
    code_doc_events = [e for e in daily.events if e.source in {"git", "docs", "github"}]
    for event in code_doc_events[:30]:
        lines.append(f"- [{event.source}] {event.title}")
    if len(code_doc_events) > 30:
        lines.append(f"- ……另有 {len(code_doc_events) - 30} 条代码/文档事件已折叠。")
    lines.append("")
    lines.append("## AI 协作与系统活动")
    lines.append("")
    ai_system_events = [
        e for e in daily.events if e.source in {"hermes", "macos_activity"}
    ]
    for event in ai_system_events[:20]:
        lines.append(f"- [{event.source}] {event.title}")
    if len(ai_system_events) > 20:
        lines.append(
            f"- ……另有 {len(ai_system_events) - 20} 条 AI/系统活动事件已折叠。"
        )
    lines.append("")
    lines.append("## 证据与低置信度项")
    lines.append("")
    lines.append(f"低置信度/未归因事件：{daily.low_confidence_count} 条。")
    for event in daily.events:
        if event.confidence < 0.5 or not event.project_guess:
            lines.append(
                f"- {event.start} · {event.source}/{event.kind}: {event.title}（confidence={event.confidence:.2f}）"
            )
    lines.append("")
    lines.append("## 可修正项")
    lines.append("")
    lines.append("你可以直接在 Feishu 里修正，例如：")
    lines.append("- “这段 Feishu 活动算 DayTrace。”")
    lines.append("- “这个 repo 归到 Daily Briefing。”")
    lines.append("- “今天主要地点是办公室。”")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_feishu_summary(daily: DailyTrace, report_path: str | None = None) -> str:
    top_projects = (
        "、".join(name for name, _ in _top(daily.project_counts, 3)) or "暂无"
    )
    top_sources = (
        "、".join(f"{s}:{c}" for s, c in _top(daily.source_counts, 4)) or "暂无"
    )
    lines = [
        f"# DayTrace · {daily.date}",
        f"已生成第一版 Prototype 日报：{len(daily.events)} 条事件，来源：{top_sources}。",
        f"主要项目/归因：{top_projects}。",
        f"低置信度/待修正：{daily.low_confidence_count} 条。",
    ]
    if report_path:
        lines.append(f"完整报告：`{report_path}`")
    return "\n".join(lines)
