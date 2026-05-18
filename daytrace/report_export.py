"""Offline report export — render daily / weekly reports as Markdown
summaries that are styled enough to read well on their own (in email,
in a Feishu Docs import, anywhere).

Public API:
    archive_markdown_for_date(db_path, date, chart_names=[...]) -> str
    archive_markdown_for_week(db_path, week, chart_names=[...]) -> str

Charts (PNGs) are written separately by the caller (see
`daytrace.report_charts`); we just inject `![](./<name>)` references so
both lark-cli's docx import and the email renderer's cid: rewrite pick
them up.

CLI: see scripts/export_report.py
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ───── Markdown export ───────────────────────────────────────────────────

def _safe_json(s: str | None) -> Any:
    if s is None:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None


def _format_duration_short(minutes: int | float | None) -> str:
    if not minutes:
        return "0m"
    m = int(minutes)
    h, r = divmod(m, 60)
    if h == 0:
        return f"{r}m"
    return f"{h}h {r}m" if r else f"{h}h"


def _load_day_channels(con: sqlite3.Connection, date: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for row in con.execute(
        "SELECT channel, value_json FROM day_channel WHERE date=?", (date,)
    ).fetchall():
        out[row["channel"]] = _safe_json(row["value_json"])
    return out


def _md_dashboard_table_daily(header: dict, channels: dict[str, Any], ai_cost: float | None) -> str:
    """4-row dashboard table for daily reports — renders cleanly in both
    Feishu Docs (table block) and Gmail HTML (styled <table>)."""
    switches = channels.get("context_switches") or {}
    longest = channels.get("longest_focus_block") or {}
    rows = [
        ("📝", "事件总数", f"{header['total_events']}", f"切换 {switches.get('count', 0)} 次"),
        ("⏱", "活跃总时长", _format_duration_short(header['active_minutes']), ""),
    ]
    if longest:
        rows.append((
            "🎯", "最长专注",
            _format_duration_short(longest.get('duration_min', 0)),
            f"{longest.get('start','?')}–{longest.get('end','?')} · {longest.get('dominant_project','?')}",
        ))
    if ai_cost is not None:
        rows.append(("💸", "AI 花费", f"${ai_cost:.3f}", "当天累计"))
    lines = ["|  | 指标 | 数值 | 备注 |", "|---|---|---|---|"]
    for emoji, name, val, note in rows:
        lines.append(f"| {emoji} | {name} | **{val}** | {note} |")
    return "\n".join(lines)


def _md_dashboard_table_weekly(*, total_events: int, total_active_min: float,
                               active_days: int, ai_cost: float) -> str:
    """4-row dashboard table for weekly reports."""
    lines = [
        "|  | 指标 | 数值 | 备注 |", "|---|---|---|---|",
        f"| 📝 | 事件总数 | **{total_events}** | 全周累计 |",
        f"| ⏱ | 活跃总时长 | **{total_active_min/60:.1f}h** | 估算 |",
        f"| 📅 | 活跃天数 | **{active_days}/7** | {'满勤' if active_days == 7 else f'空白 {7-active_days} 天'} |",
        f"| 💸 | AI 花费 | **${ai_cost:.3f}** | 本周累计 |",
    ]
    return "\n".join(lines)


def _md_trend_line(ai_overview: dict | None) -> str:
    if not ai_overview:
        return ""
    tr = ai_overview.get("trend")
    if not isinstance(tr, dict):
        return ""
    direction = tr.get("direction") or ""
    comparison = (tr.get("comparison") or "").strip()
    chip = {"rising": "↗", "steady": "→", "dropping": "↘",
            "new": "✦", "paused": "⏸", "blocked": "🚧"}.get(direction, "→")
    # Plain paragraph with inline-code label — renders nicely in both
    # Feishu (inline code styling) and Gmail (CSS-styled <code>).
    return f"`变化趋势` {chip} **{direction or 'steady'}** — {comparison}".strip()


_LEADING_EMOJI_RE = None  # populated lazily below


def _strip_leading_section_emoji(text: str) -> str:
    """The AI sometimes prepends the section emoji (🚀 / ⏰ / 🔔) to each
    bullet — redundant when the column header already carries that
    emoji. Strip it at render time so we don't have to invalidate the
    AI cache."""
    global _LEADING_EMOJI_RE
    if _LEADING_EMOJI_RE is None:
        import re as _re
        _LEADING_EMOJI_RE = _re.compile(r"^[🚀⏰🔔📌✨📰💡📊🎯]\s*")
    return _LEADING_EMOJI_RE.sub("", text.lstrip()).strip()


def _md_insights(ai_overview: dict | None) -> str:
    if not ai_overview:
        return ""
    out: list[str] = []
    h = ai_overview.get("highlights") or []
    w = ai_overview.get("work_pattern") or []
    s = ai_overview.get("suggestions") or ai_overview.get("recommendations") or []
    def _bullets(xs: list[str]) -> str:
        return "\n".join(f"- {_strip_leading_section_emoji(x)}" for x in xs)
    if h:
        out.append("### 🚀 关键任务进展\n" + _bullets(h))
    if w:
        out.append("### ⏰ 时间安排回顾\n" + _bullets(w))
    if s:
        out.append("### 🔔 任务跟进提醒\n" + _bullets(s))
    return "\n\n".join(out)


def _insert_charts_block(md_lines: list[str], chart_names: list[str]) -> None:
    """Append a '## 📊 数据可视化' section with each chart under its own
    subheading. lark-cli +import inlines the local PNGs into the docx;
    email's add_related() embeds them as cid:NAME inline attachments."""
    if not chart_names:
        return
    titles = {
        "hist":  "任务时间分布(按时段)",
        "donut": "任务总览",
    }
    md_lines.append("## 📊 数据可视化")
    md_lines.append("")
    for name in chart_names:
        # name = "<key>-<chart_key>.png" → extract chart_key for a friendly heading
        chart_key = name.rsplit("-", 1)[-1].split(".")[0] if "-" in name else "chart"
        title = titles.get(chart_key, chart_key)
        md_lines.append(f"### {title}")
        md_lines.append("")
        md_lines.append(f"![{name}](./{name})")
        md_lines.append("")


def archive_markdown_for_date(db_path: Path, date: str, *,
                              chart_names: list[str] | None = None) -> str:
    """Render a Markdown summary for a single date. Header line + AI
    overview + insights. Safe to use as email body or chat message."""
    from daytrace.db import connect, init_db
    con = connect(db_path)
    init_db(con)
    row = con.execute(
        "SELECT * FROM day_report WHERE date = ?", (date,)
    ).fetchone()
    if row is None:
        return f"# 每日 Report · {date}\n\n(还没有 day_report 数据,需要先跑 backfill)\n"
    channels = _load_day_channels(con, date)
    ai_overview = channels.get("ai_overview")
    ai_cost = con.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM day_channel "
        "WHERE date=? AND generator='ai'", (date,)
    ).fetchone()[0] or 0.0

    parts: list[str] = [
        f"# 📊 每日 Report · {date}",
        "",
        f"> **{date}** · 工作日复盘 · 数据来源:本机 + 远程设备 ssh catchup",
        "",
        _md_dashboard_table_daily(dict(row), channels, float(ai_cost)),
        "",
        "---",
        "",
    ]

    if ai_overview:
        headline = ai_overview.get("headline") or ""
        ov = ai_overview.get("overview")
        narrative = (ov or {}).get("narrative") if isinstance(ov, dict) else ai_overview.get("narrative")
        if headline:
            parts.append(f"## ✨ {headline}")
            parts.append("")
        if narrative:
            # Blockquote highlights the narrative — visually anchors the page.
            for line in narrative.strip().split("\n"):
                parts.append(f"> {line}" if line.strip() else ">")
            parts.append("")
        trend_line = _md_trend_line(ai_overview)
        if trend_line:
            parts.append(trend_line)
            parts.append("")
        # Charts go between narrative and insights — visual breather.
        if chart_names:
            parts.append("---")
            parts.append("")
            _insert_charts_block(parts, chart_names)
            parts.append("---")
            parts.append("")
        insights = _md_insights(ai_overview)
        if insights:
            parts.append("## 💡 Insights")
            parts.append("")
            parts.append(insights)
            parts.append("")
    else:
        parts.append("_(AI overview 未生成 — DEEPSEEK_API_KEY 未配置或 backfill 未跑)_")
        parts.append("")
        if chart_names:
            _insert_charts_block(parts, chart_names)

    parts.append("---")
    parts.append(f"🌿 _DayTrace 归档 · 生成于 {_now_iso()}_")
    return "\n".join(parts)


def _md_daily_timeline(con, days: list[str]) -> str:
    """Build the '每日时间轴' section for the weekly MD by replaying each
    day's cached ai_overview headline + narrative. Skips days with no
    overview row (catchup didn't reach that date, or AI was unavailable)."""
    weekday_labels = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    blocks: list[str] = []
    for idx, d in enumerate(days):
        wd = weekday_labels[idx] if idx < 7 else "?"
        row = con.execute(
            "SELECT value_json FROM day_channel "
            "WHERE date=? AND channel='ai_overview'",
            (d,),
        ).fetchone()
        if not row or not row[0]:
            blocks.append(f"### {wd} · {d}\n\n_(无数据)_\n")
            continue
        try:
            val = json.loads(row[0])
        except Exception:
            blocks.append(f"### {wd} · {d}\n\n_(数据损坏)_\n")
            continue
        headline = (val.get("headline") or "").strip()
        ov = val.get("overview")
        if isinstance(ov, dict):
            narrative = (ov.get("narrative") or "").strip()
        else:
            # v6 cache compat — narrative was a top-level string
            narrative = (val.get("narrative") or "").strip()
        block_lines = [f"### {wd} · {d}"]
        if headline:
            block_lines.append("")
            block_lines.append(f"**✨ {headline}**")
        if narrative:
            block_lines.append("")
            for line in narrative.strip().split("\n"):
                block_lines.append(f"> {line}" if line.strip() else ">")
        blocks.append("\n".join(block_lines))
    if not blocks:
        return ""
    return "\n\n".join(blocks)


def archive_markdown_for_week(db_path: Path, week: str, *,
                              chart_names: list[str] | None = None) -> str:
    """Render a Markdown summary for an ISO week (YYYY-Www).

    Top-level stats come from the weekly cache file (the dashboard
    writes it on render). If absent, we degrade gracefully."""
    from daytrace.db import connect, init_db, iso_week_to_date_range
    from dashboard.server import _week_ai_cache_path
    con = connect(db_path)
    init_db(con)

    monday, sunday, days = iso_week_to_date_range(week)
    total_events = con.execute(
        "SELECT COUNT(*) FROM events WHERE date BETWEEN ? AND ?", (monday, sunday)
    ).fetchone()[0]
    total_active_min = con.execute(
        "SELECT COALESCE(SUM(active_minutes),0) FROM day_report WHERE date BETWEEN ? AND ?",
        (monday, sunday),
    ).fetchone()[0] or 0
    active_days = con.execute(
        "SELECT COUNT(*) FROM day_report WHERE date BETWEEN ? AND ? AND total_events > 0",
        (monday, sunday),
    ).fetchone()[0]
    ai_cost = con.execute(
        "SELECT COALESCE(SUM(cost_usd),0) FROM day_channel "
        "WHERE date BETWEEN ? AND ? AND generator='ai'", (monday, sunday)
    ).fetchone()[0] or 0.0

    cache_path = _week_ai_cache_path(week)
    summary: dict | None = None
    if cache_path.exists():
        try:
            summary = json.loads(cache_path.read_text(encoding="utf-8")).get("value")
        except Exception:
            summary = None

    parts: list[str] = [
        f"# 📊 周报 · {week}",
        "",
        f"> **{monday} ~ {sunday}** · ISO Week {week.split('-W')[-1]} · "
        f"{active_days}/7 天活跃 · {total_active_min/60:.1f}h 总投入",
        "",
        _md_dashboard_table_weekly(
            total_events=total_events,
            total_active_min=total_active_min,
            active_days=active_days,
            ai_cost=float(ai_cost),
        ),
        "",
        "---",
        "",
    ]

    if summary:
        headline = summary.get("headline") or ""
        ov = summary.get("overview")
        narrative = (ov or {}).get("narrative") if isinstance(ov, dict) else summary.get("narrative")
        if headline:
            parts.append(f"## ✨ {headline}")
            parts.append("")
        if narrative:
            for line in narrative.strip().split("\n"):
                parts.append(f"> {line}" if line.strip() else ">")
            parts.append("")
        trend_line = _md_trend_line(summary)
        if trend_line:
            parts.append(trend_line)
            parts.append("")
        if chart_names:
            parts.append("---")
            parts.append("")
            _insert_charts_block(parts, chart_names)
            parts.append("---")
            parts.append("")
        insights = _md_insights(summary)
        if insights:
            parts.append("## 💡 Insights")
            parts.append("")
            parts.append(insights)
            parts.append("")
    else:
        parts.append("_(本周 AI 速读未生成 — 请先访问 /weekly?week=" + week + " 触发)_")
        parts.append("")
        if chart_names:
            _insert_charts_block(parts, chart_names)

    # Per-day timeline at the end: reuses each day's cached ai_overview
    # (headline + narrative). Reads as a vertical 周一→周日 recap.
    timeline_md = _md_daily_timeline(con, days)
    if timeline_md:
        parts.append("---")
        parts.append("")
        parts.append("## 📅 每日时间轴")
        parts.append("")
        parts.append(timeline_md)
        parts.append("")

    parts.append("---")
    parts.append(f"🌸 _DayTrace 归档 · 生成于 {_now_iso()}_")
    return "\n".join(parts)
