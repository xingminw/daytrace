"""Offline report export — render daily / weekly reports as Markdown
summaries that are styled enough to read well on their own (in email,
in a Feishu Docs import, anywhere).

Bilingual: the AI output is now {zh, en} per field (v14). The export
language is independent of the dashboard's UI language — it's
controlled by the `DAYTRACE_REPORT_LANG` env var (default "en",
override to "zh" or pass `lang="zh"` per call).

Public API:
    archive_markdown_for_date(db_path, date, chart_names=[...], lang="en") -> str
    archive_markdown_for_week(db_path, week, chart_names=[...], lang="en") -> str

CLI: see scripts/export_report.py
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


# Default report language. Overridden by DAYTRACE_REPORT_LANG env var
# (loaded lazily from ~/.daytrace/secrets.env by ai_client._load_secrets_into_environ).
def _default_report_lang() -> str:
    # Make sure secrets.env is merged in for launchd-spawned exporters
    from daytrace.ai_client import _load_secrets_into_environ
    _load_secrets_into_environ()
    v = (os.environ.get("DAYTRACE_REPORT_LANG", "en") or "en").strip().lower()
    return v if v in ("zh", "en") else "en"


def _L(value, lang: str) -> str:
    """Pull a single language string out of a bilingual {zh, en} dict.
    Tolerates plain strings (legacy v13 cache) and missing translations
    (falls back to the other language)."""
    if isinstance(value, dict):
        v = (value.get(lang) or "").strip()
        if v:
            return v
        other = "en" if lang == "zh" else "zh"
        return (value.get(other) or "").strip()
    if isinstance(value, str):
        return value.strip()
    return ""


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


_LABELS = {
    "zh": {
        "col_metric":   "指标",      "col_value":  "数值",      "col_note": "备注",
        "events":       "事件总数",  "active":     "活跃总时长","longest":  "最长专注",
        "active_days":  "活跃天数",  "ai_cost":    "AI 花费",
        "switches_n":   "切换 {n} 次",
        "today_total":  "当天累计",  "week_total": "本周累计",
        "full_week":    "全周累计",  "estimated":  "估算",
        "full_attend":  "满勤",      "blank_n":    "空白 {n} 天",
        "trend":        "变化趋势",
        "h_progress":   "🚀 关键任务进展",
        "h_pattern":    "⏰ 时间安排回顾",
        "h_followup":   "🔔 任务跟进提醒",
        "h_viz":        "📊 数据可视化",
        "h_timeline":   "📅 每日时间轴",
        "chart_hist":   "任务时间分布(按时段)",
        "chart_donut":  "任务总览",
        "no_data":      "_(无数据)_",
        "daily_title":  "每日 Report",
        "weekly_title": "周报",
        "tagline_daily":  "工作日复盘 · 数据来源:本机 + 远程设备 ssh catchup",
        "tagline_weekly": "ISO Week {w} · {days}/7 天活跃 · {h:.1f}h 总投入",
        "no_overview":  "_(AI overview 未生成 — DEEPSEEK_API_KEY 未配置或 backfill 未跑)_",
        "no_weekly":    "_(本周 AI 速读未生成 — 请先访问 /weekly?week={w} 触发)_",
        "footer_daily": "🌿 _DayTrace 归档 · 生成于 {t}_",
        "footer_weekly":"🌸 _DayTrace 归档 · 生成于 {t}_",
        "insights":     "💡 Insights",
        "section_overview": "## ✨ {headline}",
    },
    "en": {
        "col_metric":   "Metric",    "col_value":  "Value",     "col_note": "Note",
        "events":       "Events",    "active":     "Active",    "longest":  "Longest focus",
        "active_days":  "Active days","ai_cost":   "AI cost",
        "switches_n":   "{n} switches",
        "today_total":  "today total","week_total":"this week",
        "full_week":    "week total","estimated":  "estimated",
        "full_attend":  "every day", "blank_n":    "{n} blank days",
        "trend":        "Trend",
        "h_progress":   "🚀 Task progress",
        "h_pattern":    "⏰ Time pattern",
        "h_followup":   "🔔 Follow-ups",
        "h_viz":        "📊 Visualizations",
        "h_timeline":   "📅 Daily timeline",
        "chart_hist":   "Task time by hour-of-day",
        "chart_donut":  "Task totals",
        "no_data":      "_(no data)_",
        "daily_title":  "Daily Report",
        "weekly_title": "Weekly",
        "tagline_daily":  "Workday recap · source: this Mac + remote machines via ssh catchup",
        "tagline_weekly": "ISO Week {w} · {days}/7 days active · {h:.1f}h total",
        "no_overview":  "_(AI overview not generated — DEEPSEEK_API_KEY not set or backfill not run)_",
        "no_weekly":    "_(weekly AI overview not generated — visit /weekly?week={w} first to trigger)_",
        "footer_daily": "🌿 _DayTrace archive · generated {t}_",
        "footer_weekly":"🌸 _DayTrace archive · generated {t}_",
        "insights":     "💡 Insights",
        "section_overview": "## ✨ {headline}",
    },
}


def _t(key: str, lang: str, **fmt) -> str:
    s = _LABELS.get(lang, _LABELS["en"]).get(key) or _LABELS["en"].get(key) or key
    return s.format(**fmt) if fmt else s


def _md_dashboard_table_daily(header: dict, channels: dict[str, Any],
                              ai_cost: float | None, lang: str) -> str:
    """4-row dashboard table — renders cleanly in both Feishu Docs and
    Gmail HTML. Bilingual via `lang`."""
    switches = channels.get("context_switches") or {}
    longest = channels.get("longest_focus_block") or {}
    rows = [
        ("📝", _t("events", lang), f"{header['total_events']}",
         _t("switches_n", lang, n=switches.get("count", 0))),
        ("⏱", _t("active", lang), _format_duration_short(header['active_minutes']), ""),
    ]
    if longest:
        rows.append((
            "🎯", _t("longest", lang),
            _format_duration_short(longest.get('duration_min', 0)),
            f"{longest.get('start','?')}–{longest.get('end','?')} · {longest.get('dominant_project','?')}",
        ))
    if ai_cost is not None:
        rows.append(("💸", _t("ai_cost", lang), f"${ai_cost:.3f}", _t("today_total", lang)))
    lines = [
        f"|  | {_t('col_metric', lang)} | {_t('col_value', lang)} | {_t('col_note', lang)} |",
        "|---|---|---|---|",
    ]
    for emoji, name, val, note in rows:
        lines.append(f"| {emoji} | {name} | **{val}** | {note} |")
    return "\n".join(lines)


def _md_dashboard_table_weekly(*, total_events: int, total_active_min: float,
                               active_days: int, ai_cost: float, lang: str) -> str:
    blank_note = (_t("full_attend", lang) if active_days == 7
                  else _t("blank_n", lang, n=7 - active_days))
    lines = [
        f"|  | {_t('col_metric', lang)} | {_t('col_value', lang)} | {_t('col_note', lang)} |",
        "|---|---|---|---|",
        f"| 📝 | {_t('events', lang)} | **{total_events}** | {_t('full_week', lang)} |",
        f"| ⏱ | {_t('active', lang)} | **{total_active_min/60:.1f}h** | {_t('estimated', lang)} |",
        f"| 📅 | {_t('active_days', lang)} | **{active_days}/7** | {blank_note} |",
        f"| 💸 | {_t('ai_cost', lang)} | **${ai_cost:.3f}** | {_t('week_total', lang)} |",
    ]
    return "\n".join(lines)


def _md_trend_line(ai_overview: dict | None, lang: str) -> str:
    if not ai_overview:
        return ""
    tr = ai_overview.get("trend")
    if not isinstance(tr, dict):
        return ""
    direction = tr.get("direction") or ""
    comparison = _L(tr.get("comparison"), lang)
    chip = {"rising": "↗", "steady": "→", "dropping": "↘",
            "new": "✦", "paused": "⏸", "blocked": "🚧"}.get(direction, "→")
    return f"`{_t('trend', lang)}` {chip} **{direction or 'steady'}** — {comparison}".strip()


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


def _md_insights(ai_overview: dict | None, lang: str) -> str:
    if not ai_overview:
        return ""
    out: list[str] = []
    h = ai_overview.get("highlights") or []
    w = ai_overview.get("work_pattern") or []
    s = ai_overview.get("suggestions") or ai_overview.get("recommendations") or []
    def _bullets(xs: list) -> str:
        return "\n".join(
            f"- {_strip_leading_section_emoji(_L(x, lang))}"
            for x in xs if _L(x, lang)
        )
    if h:
        out.append(f"### {_t('h_progress', lang)}\n" + _bullets(h))
    if w:
        out.append(f"### {_t('h_pattern', lang)}\n" + _bullets(w))
    if s:
        out.append(f"### {_t('h_followup', lang)}\n" + _bullets(s))
    return "\n\n".join(out)


def _insert_charts_block(md_lines: list[str], chart_names: list[str], lang: str) -> None:
    """Append a chart-section with each PNG under its own subheading.
    lark-cli +import inlines the local PNGs into the docx; email's
    add_related() embeds them as cid:NAME inline attachments."""
    if not chart_names:
        return
    titles = {"hist":  _t("chart_hist", lang), "donut": _t("chart_donut", lang)}
    md_lines.append(f"## {_t('h_viz', lang)}")
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
                              chart_names: list[str] | None = None,
                              lang: str | None = None) -> str:
    """Render a Markdown summary for a single date. Bilingual via `lang`
    (defaults to DAYTRACE_REPORT_LANG env, then 'en')."""
    if lang is None:
        lang = _default_report_lang()
    from daytrace.db import connect, init_db
    con = connect(db_path)
    init_db(con)
    row = con.execute(
        "SELECT * FROM day_report WHERE date = ?", (date,)
    ).fetchone()
    if row is None:
        return f"# {_t('daily_title', lang)} · {date}\n\n{_t('no_data', lang)}\n"
    channels = _load_day_channels(con, date)
    ai_overview = channels.get("ai_overview")
    ai_cost = con.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM day_channel "
        "WHERE date=? AND generator='ai'", (date,)
    ).fetchone()[0] or 0.0

    parts: list[str] = [
        f"# 📊 {_t('daily_title', lang)} · {date}",
        "",
        f"> **{date}** · {_t('tagline_daily', lang)}",
        "",
        _md_dashboard_table_daily(dict(row), channels, float(ai_cost), lang),
        "",
        "---",
        "",
    ]

    if ai_overview:
        headline = _L(ai_overview.get("headline"), lang)
        ov = ai_overview.get("overview")
        narrative = _L((ov or {}).get("narrative") if isinstance(ov, dict) else ai_overview.get("narrative"), lang)
        if headline:
            parts.append(_t("section_overview", lang, headline=headline))
            parts.append("")
        if narrative:
            for line in narrative.strip().split("\n"):
                parts.append(f"> {line}" if line.strip() else ">")
            parts.append("")
        trend_line = _md_trend_line(ai_overview, lang)
        if trend_line:
            parts.append(trend_line)
            parts.append("")
        if chart_names:
            parts.append("---")
            parts.append("")
            _insert_charts_block(parts, chart_names, lang)
            parts.append("---")
            parts.append("")
        insights = _md_insights(ai_overview, lang)
        if insights:
            parts.append(f"## {_t('insights', lang)}")
            parts.append("")
            parts.append(insights)
            parts.append("")
    else:
        parts.append(_t("no_overview", lang))
        parts.append("")
        if chart_names:
            _insert_charts_block(parts, chart_names, lang)

    parts.append("---")
    parts.append(_t("footer_daily", lang, t=_now_iso()))
    return "\n".join(parts)


def _md_daily_timeline(con, days: list[str], lang: str) -> str:
    """Build the daily-timeline section for the weekly MD by replaying
    each day's cached ai_overview headline + narrative. Skips days with no
    overview row (catchup didn't reach that date, or AI was unavailable)."""
    weekday_labels_zh = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday_labels_en = ["Mon",  "Tue",  "Wed",  "Thu",  "Fri",  "Sat",  "Sun"]
    wd_set = weekday_labels_en if lang == "en" else weekday_labels_zh
    blocks: list[str] = []
    for idx, d in enumerate(days):
        wd = wd_set[idx] if idx < 7 else "?"
        row = con.execute(
            "SELECT value_json FROM day_channel "
            "WHERE date=? AND channel='ai_overview'",
            (d,),
        ).fetchone()
        if not row or not row[0]:
            blocks.append(f"### {wd} · {d}\n\n{_t('no_data', lang)}\n")
            continue
        try:
            val = json.loads(row[0])
        except Exception:
            blocks.append(f"### {wd} · {d}\n\n{_t('no_data', lang)}\n")
            continue
        headline = _L(val.get("headline"), lang)
        ov = val.get("overview")
        if isinstance(ov, dict):
            narrative = _L(ov.get("narrative"), lang)
        else:
            narrative = _L(val.get("narrative"), lang)
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
                              chart_names: list[str] | None = None,
                              lang: str | None = None) -> str:
    """Render a Markdown summary for an ISO week (YYYY-Www). Bilingual
    via `lang` (defaults to DAYTRACE_REPORT_LANG env, then 'en')."""
    if lang is None:
        lang = _default_report_lang()
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

    iso_w = week.split("-W")[-1]
    parts: list[str] = [
        f"# 📊 {_t('weekly_title', lang)} · {week}",
        "",
        f"> **{monday} ~ {sunday}** · " + _t("tagline_weekly", lang, w=iso_w,
                                              days=active_days, h=total_active_min/60),
        "",
        _md_dashboard_table_weekly(
            total_events=total_events,
            total_active_min=total_active_min,
            active_days=active_days,
            ai_cost=float(ai_cost),
            lang=lang,
        ),
        "",
        "---",
        "",
    ]

    if summary:
        headline = _L(summary.get("headline"), lang)
        ov = summary.get("overview")
        narrative = _L((ov or {}).get("narrative") if isinstance(ov, dict) else summary.get("narrative"), lang)
        if headline:
            parts.append(_t("section_overview", lang, headline=headline))
            parts.append("")
        if narrative:
            for line in narrative.strip().split("\n"):
                parts.append(f"> {line}" if line.strip() else ">")
            parts.append("")
        trend_line = _md_trend_line(summary, lang)
        if trend_line:
            parts.append(trend_line)
            parts.append("")
        if chart_names:
            parts.append("---")
            parts.append("")
            _insert_charts_block(parts, chart_names, lang)
            parts.append("---")
            parts.append("")
        insights = _md_insights(summary, lang)
        if insights:
            parts.append(f"## {_t('insights', lang)}")
            parts.append("")
            parts.append(insights)
            parts.append("")
    else:
        parts.append(_t("no_weekly", lang, w=week))
        parts.append("")
        if chart_names:
            _insert_charts_block(parts, chart_names, lang)

    # Per-day timeline at the end — replays each day's cached ai_overview.
    timeline_md = _md_daily_timeline(con, days, lang)
    if timeline_md:
        parts.append("---")
        parts.append("")
        parts.append(f"## {_t('h_timeline', lang)}")
        parts.append("")
        parts.append(timeline_md)
        parts.append("")

    parts.append("---")
    parts.append(_t("footer_weekly", lang, t=_now_iso()))
    return "\n".join(parts)
