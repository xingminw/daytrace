"""Offline report export — render daily / weekly reports as standalone
HTML (single self-contained file) + Markdown summary, decoupled from
the dashboard HTTP server.

Two output formats:
  • HTML  — single file with inlined CSS, no JS dependency on the
            server. Drop in email, Feishu drive, anywhere. Charts
            and 4-tile dashboard preserved.
  • MD    — pure-text summary suitable for email body or chat. Charts
            dropped; AI narrative + 3-col Insights kept.

Usage (programmatic):
    from daytrace.report_export import (
        archive_html_for_date, archive_html_for_week,
        archive_markdown_for_date, archive_markdown_for_week,
    )

CLI: see scripts/export_report.py
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ───── HTML post-processing ──────────────────────────────────────────────

# What we strip from the live-server HTML to make it self-contained and
# non-interactive:
#   • <header>…</header>          — page nav (toggle pills, day-switcher,
#                                    DB button) — useless offline
#   • <form …>…</form>             — filter forms that POST to the server
#   • <script>…</script>           — interactive JS (view-switchers,
#                                    scroll-restore, sticky-header). The
#                                    default CSS view stays visible.
#   • <a href="/…">                — server-relative links → neutralized
#
# We then prepend an "archive banner" inside <main>.

_HEADER_RE = re.compile(r"<header>.*?</header>", re.DOTALL)
_FORM_RE   = re.compile(r"<form\b[^>]*>.*?</form>", re.DOTALL)
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.DOTALL)
# Internal anchor href starts with /  (e.g. /today?date=..., /events?...)
_INTERNAL_HREF_RE = re.compile(r'href="(/[^"]*)"')


def _archive_banner_html(kind: str, key: str) -> str:
    label = "每日" if kind == "daily" else "每周"
    return (
        '<div class="archive-banner" style="'
        'margin:0 auto 16px; padding:10px 14px; '
        'background:#fff7e8; border:1px dashed #f0d68b; '
        'border-radius:10px; font-size:13px; color:#5a4a2e; '
        'display:flex; align-items:center; gap:10px;">'
        '<span style="font-size:16px;">📦</span>'
        f'<span><b>{label}归档</b> · {key} · 生成于 {_now_iso()}'
        ' · 此版本为数据快照,无交互;原始 dashboard 链接已禁用</span>'
        '</div>'
    )


def _strip_to_archive_html(live_html: str, kind: str, key: str) -> str:
    """Take a full-page HTML string from today_page/weekly_page and turn
    it into a self-contained archive: no nav, no forms, no JS, internal
    anchors neutralized, banner injected."""
    html = live_html
    html = _HEADER_RE.sub("", html)
    html = _FORM_RE.sub("", html)
    html = _SCRIPT_RE.sub("", html)
    # Neutralize internal anchors — keep the text but disable the link
    # (offline reader can't reach the server).
    html = _INTERNAL_HREF_RE.sub(
        lambda m: 'href="#" data-archived-href="' + m.group(1) + '" '
                  'title="离线归档版本,链接已禁用" '
                  'style="pointer-events:none; color:inherit; '
                  'text-decoration:none; opacity:0.7;"',
        html,
    )
    # Inject banner right after <main>
    html = html.replace("<main>", "<main>" + _archive_banner_html(kind, key), 1)
    return html


def archive_html_for_date(db_path: Path, date: str) -> str:
    """Render the daily report as a self-contained archive HTML string."""
    # Import lazily to avoid circular import (server imports many things).
    from dashboard.server import today_page
    live = today_page(db_path, date)
    return _strip_to_archive_html(live, "daily", date)


def archive_html_for_week(db_path: Path, week: str) -> str:
    """Render the weekly report as a self-contained archive HTML string."""
    from dashboard.server import weekly_page
    live = weekly_page(db_path, week)
    return _strip_to_archive_html(live, "weekly", week)


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


def _md_dashboard_line(header: dict, channels: dict[str, Any], ai_cost: float | None) -> str:
    switches = channels.get("context_switches") or {}
    longest = channels.get("longest_focus_block") or {}
    bits = [
        f"**事件总数** {header['total_events']} · 切换 {switches.get('count', 0)} 次",
        f"**活跃** {_format_duration_short(header['active_minutes'])}",
    ]
    if longest:
        bits.append(
            f"**最长专注** {_format_duration_short(longest.get('duration_min', 0))} "
            f"({longest.get('start','?')}–{longest.get('end','?')} · "
            f"{longest.get('dominant_project','?')})"
        )
    if ai_cost is not None:
        bits.append(f"**AI 花费** ${ai_cost:.3f}")
    return " · ".join(bits)


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
    return f"**变化趋势** {chip} {direction or 'steady'} — {comparison}".strip()


def _md_insights(ai_overview: dict | None) -> str:
    if not ai_overview:
        return ""
    out: list[str] = []
    h = ai_overview.get("highlights") or []
    w = ai_overview.get("work_pattern") or []
    s = ai_overview.get("suggestions") or ai_overview.get("recommendations") or []
    if h:
        out.append("### 🚀 关键任务进展\n" + "\n".join(f"- {x}" for x in h))
    if w:
        out.append("### ⏰ 时间安排回顾\n" + "\n".join(f"- {x}" for x in w))
    if s:
        out.append("### 🔔 任务跟进提醒\n" + "\n".join(f"- {x}" for x in s))
    return "\n\n".join(out)


def _insert_charts_block(md_lines: list[str], chart_names: list[str]) -> None:
    """In-place: append a '## 图表' section linking to the named PNG files.
    The MD is read by `lark-cli drive +import` which inlines local images;
    in email, the same names are also embedded as cid:NAME inline attachments."""
    if not chart_names:
        return
    md_lines.append("## 图表")
    md_lines.append("")
    for name in chart_names:
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

    parts: list[str] = [f"# 每日 Report · {date}", ""]
    parts.append(_md_dashboard_line(dict(row), channels, float(ai_cost)))
    parts.append("")

    if ai_overview:
        headline = ai_overview.get("headline") or ""
        ov = ai_overview.get("overview")
        narrative = (ov or {}).get("narrative") if isinstance(ov, dict) else ai_overview.get("narrative")
        if headline:
            parts.append(f"## 📰 {headline}")
            parts.append("")
        if narrative:
            parts.append(narrative.strip())
            parts.append("")
        trend_line = _md_trend_line(ai_overview)
        if trend_line:
            parts.append(trend_line)
            parts.append("")
        insights = _md_insights(ai_overview)
        if insights:
            parts.append("## Insights")
            parts.append("")
            parts.append(insights)
            parts.append("")
    else:
        parts.append("_(AI overview 未生成 — DEEPSEEK_API_KEY 未配置或 backfill 未跑)_")
        parts.append("")

    _insert_charts_block(parts, chart_names or [])
    parts.append("---")
    parts.append(f"_DayTrace 归档 · 生成于 {_now_iso()}_")
    return "\n".join(parts)


def archive_markdown_for_week(db_path: Path, week: str, *,
                              chart_names: list[str] | None = None) -> str:
    """Render a Markdown summary for an ISO week (YYYY-Www).

    Top-level stats come from the weekly cache file (the dashboard
    writes it on render). If absent, we degrade gracefully."""
    from daytrace.db import connect, init_db, iso_week_to_date_range
    from dashboard.server import _week_ai_cache_path
    con = connect(db_path)
    init_db(con)

    monday, sunday, _ = iso_week_to_date_range(week)
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

    parts: list[str] = [f"# 周报 · {week} ({monday} ~ {sunday})", ""]
    dashboard_bits = [
        f"**事件总数** {total_events}",
        f"**活跃总时长** {total_active_min/60:.1f}h",
        f"**活跃天数** {active_days}/7",
        f"**AI 花费** ${ai_cost:.3f}",
    ]
    parts.append(" · ".join(dashboard_bits))
    parts.append("")

    if summary:
        headline = summary.get("headline") or ""
        ov = summary.get("overview")
        narrative = (ov or {}).get("narrative") if isinstance(ov, dict) else summary.get("narrative")
        if headline:
            parts.append(f"## 📰 {headline}")
            parts.append("")
        if narrative:
            parts.append(narrative.strip())
            parts.append("")
        trend_line = _md_trend_line(summary)
        if trend_line:
            parts.append(trend_line)
            parts.append("")
        insights = _md_insights(summary)
        if insights:
            parts.append("## Insights")
            parts.append("")
            parts.append(insights)
            parts.append("")
    else:
        parts.append("_(本周 AI 速读未生成 — 请先访问 /weekly?week=" + week + " 触发)_")
        parts.append("")

    _insert_charts_block(parts, chart_names or [])
    parts.append("---")
    parts.append(f"_DayTrace 归档 · 生成于 {_now_iso()}_")
    return "\n".join(parts)
