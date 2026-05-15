#!/usr/bin/env python3
from __future__ import annotations

import argparse
import calendar
import html
import json
from datetime import date as dt_date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daytrace.db import connect, query_events, query_filter_options, query_summary, query_today

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "daytrace.sqlite"

STYLE = """
:root { color-scheme: light; --bg:#f7f5ef; --card:#fffaf0; --ink:#202124; --muted:#6b645c; --line:#e7dfd0; --accent:#2f6fed; --purple:#7b61ff; --green:#16a34a; --orange:#f59e0b; --red:#ef4444; }
* { box-sizing: border-box; }
body { margin:0; font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:radial-gradient(circle at top left,#fff7df 0,#f8f5ee 35%,#f4efe5 100%); color:var(--ink); }
body.events-page { height:100vh; overflow:hidden; }
header { padding:8px 18px; border-bottom:1px solid var(--line); background:rgba(255,250,240,.94); position:sticky; top:0; backdrop-filter: blur(10px); z-index:5; display:grid; grid-template-columns:auto auto 1fr auto; gap:12px; align-items:center; min-height:50px; }
h1 { margin:0; font-size:20px; letter-spacing:-0.03em; white-space:nowrap; }.sub { color:var(--muted); font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
nav { display:flex; gap:6px; flex-wrap:nowrap; justify-content:flex-end; justify-self:end; margin-left:auto; } nav a { padding:5px 9px; border:1px solid var(--line); border-radius:999px; background:white; color:#3b352e; font-weight:650; font-size:13px; white-space:nowrap; } nav a.active { background:var(--ink); color:white; border-color:var(--ink); }
main { padding:12px 18px 28px; max-width:none; margin:0 auto; min-height:calc(100vh - 51px); }
body.events-page main { height:calc(100vh - 51px); min-height:0; overflow:hidden; padding-bottom:12px; }
body.events-page form { height:100%; }
.grid { display:grid; grid-template-columns: repeat(4, minmax(150px,1fr)); gap:10px; }.section-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }.three-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }.report-grid { display:grid; grid-template-columns:minmax(360px,1.15fr) minmax(320px,.85fr); gap:12px; align-items:start; }.analysis-grid { display:grid; grid-template-columns:repeat(2,minmax(260px,1fr)); gap:12px; }.wide-card { grid-column:1 / -1; }
.card { background:rgba(255,250,240,.94); border:1px solid var(--line); border-radius:14px; padding:12px; box-shadow:0 8px 18px rgba(65,45,10,.05); }
.metric { font-size:26px; font-weight:850; letter-spacing:-0.04em; }.metric-small { font-size:18px; font-weight:850; }.label { color:var(--muted); margin-top:3px; font-size:12px; } section { margin-top:12px; } h2 { font-size:16px; margin:0 0 8px; } h3 { margin:0 0 5px; font-size:14px; }
.bar { display:flex; align-items:center; gap:10px; margin:9px 0; }.bar-name { width:170px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:14px; }.bar-track { flex:1; height:10px; border-radius:999px; background:#ece3d2; overflow:hidden; }.bar-fill { height:100%; background:linear-gradient(90deg,#2f6fed,#7b61ff); border-radius:999px; }.bar-count { width:42px; text-align:right; color:var(--muted); font-variant-numeric:tabular-nums; }
.filters { display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin:0 0 8px; } input,select,button { border:1px solid var(--line); background:white; border-radius:8px; padding:5px 7px; font:inherit; font-size:12px; } button { background:var(--accent); color:white; border-color:var(--accent); cursor:pointer; }.checkbox { display:flex; gap:4px; align-items:center; }
.bucket-head { display:flex; gap:10px; align-items:center; justify-content:space-between; margin-bottom:8px; }.mini-event { padding:8px 0; border-top:1px dashed #eadfcd; }.mini-event:first-of-type { border-top:none; }.event-title { font-weight:700; }.muted { color:var(--muted); }.pills { display:flex; flex-wrap:wrap; gap:6px; }.tag { display:inline-flex; max-width:100%; border-radius:999px; padding:2px 8px; background:#ebe6ff; color:#4632a8; font-size:12px; font-weight:650; overflow:hidden; text-overflow:ellipsis; }.source { background:#e8f0ff; color:#174ea6; }.device { background:#dcfce7; color:#166534; }.location { background:#ffedd5; color:#9a3412; }.low { background:#fff3cd; color:#8a5a00; }
.daily-report { line-height:1.55; }.daily-report ul { margin:8px 0 0 18px; padding:0; }.daily-report li { margin:5px 0; }.report-lede { font-size:15px; color:#362f27; margin:0 0 8px; }.day-nav { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }.day-nav a { border:1px solid var(--line); background:white; border-radius:999px; padding:4px 9px; font-size:12px; font-weight:700; }
.donut-row { display:grid; grid-template-columns:120px 1fr; gap:12px; align-items:center; }.donut { width:112px; height:112px; border-radius:50%; display:grid; place-items:center; background:conic-gradient(var(--accent) 0 40%, var(--purple) 40% 70%, var(--orange) 70% 88%, #cbd5e1 88% 100%); }.donut:after { content:attr(data-label); width:70px; height:70px; border-radius:50%; background:var(--card); display:grid; place-items:center; font-size:13px; font-weight:800; color:var(--muted); text-align:center; }.legend-dot { width:9px; height:9px; display:inline-block; border-radius:50%; margin-right:6px; }.stack { display:flex; height:18px; overflow:hidden; border-radius:999px; background:#ece3d2; border:1px solid #e2d6c4; }.stack-seg { min-width:2px; height:100%; }.mini-table { width:100%; border-collapse:separate; border-spacing:0; }.mini-table th,.mini-table td { font-size:12px; padding:6px 4px; border-bottom:1px solid #eadfcd; }.mini-table th { position:static; background:transparent; box-shadow:none; color:var(--muted); }.spark { display:grid; grid-template-columns:repeat(24,1fr); gap:2px; align-items:end; height:86px; padding-top:6px; }.spark-bar { background:linear-gradient(180deg,#7b61ff,#2f6fed); border-radius:4px 4px 0 0; min-height:3px; }.spark-labels { display:grid; grid-template-columns:repeat(4,1fr); color:var(--muted); font-size:11px; margin-top:4px; }
.table-wrap { max-height:none; min-height:calc(100vh - 86px); overflow:auto; border:1px solid var(--line); border-radius:16px; background:var(--card); }
body.events-page .table-wrap { height:100%; min-height:0; max-height:100%; overflow:hidden; overscroll-behavior:contain; }
table { width:100%; min-width:0; border-collapse:separate; border-spacing:0; background:var(--card); table-layout:fixed; }
body.events-page table { height:100%; min-width:0; display:flex; flex-direction:column; }
body.events-page thead, body.events-page tbody { display:block; }
body.events-page tbody { flex:1; min-height:0; overflow-y:auto; overflow-x:hidden; overscroll-behavior:contain; }
body.events-page tr { display:table; width:100%; table-layout:fixed; }
th,td { padding:7px 9px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; font-size:12px; }
th { position:sticky; top:0; background:#fff7e8; z-index:3; color:#4d4438; box-shadow:0 1px 0 var(--line); } tr:last-child td { border-bottom:none; }
th .th-title { display:flex; align-items:center; justify-content:space-between; gap:6px; font-weight:750; margin-bottom:5px; } th .sort { color:var(--muted); font-size:11px; } th input, th select { width:100%; min-width:0; padding:4px 5px; font-size:11px; } th select + select { margin-top:4px; } .header-filter { margin:0; display:flex; align-items:center; gap:5px; white-space:nowrap; } .header-filter select { max-width:132px; }
.col-time { width:190px; }.col-source { width:78px; }.col-device { width:70px; }.col-location { width:76px; }.col-project { width:130px; }.col-title { width:auto; }
body.events-page th:nth-child(1), body.events-page td:nth-child(1) { width:190px; }
body.events-page th:nth-child(2), body.events-page td:nth-child(2) { width:78px; }
body.events-page th:nth-child(3), body.events-page td:nth-child(3) { width:70px; }
body.events-page th:nth-child(4), body.events-page td:nth-child(4) { width:76px; }
body.events-page th:nth-child(5), body.events-page td:nth-child(5) { width:130px; }
body.events-page th:nth-child(6), body.events-page td:nth-child(6) { width:auto; }
.time-range { display:grid; grid-template-columns:1fr; gap:4px; }
.time-range .header-filter { display:block; }
.time-range .date-picker summary { width:100%; justify-content:space-between; padding:4px 6px; font-size:11px; border-color:#d3c5ad; }
.time-range .calendar-pop { width:176px; padding:8px; }
.time-range .date-picker.end-date .calendar-pop { left:0; right:auto; }
.cal-day.no-data { color:#b4aa9c; background:#f8f5ee; border-color:#eee6d8; }
.cal-day.disabled { pointer-events:none; opacity:.34; background:#f3eee5; color:#a79b8b; border-color:#eadfcd; }
.cal-day.active.has-data, .cal-day.active.no-data, .cal-day.active.disabled { background:var(--ink); color:white; border-color:var(--ink); opacity:1; box-shadow:0 0 0 2px #f6d365 inset; }
body.events-page tbody tr.source-git td { background:#fff8e7; }
body.events-page tbody tr.source-github td { background:#f0f7ff; }
body.events-page tbody tr.source-docs td { background:#f3f8ff; }
body.events-page tbody tr.source-hermes td { background:#f7f2ff; }
body.events-page tbody tr.source-codex td { background:#eef6ff; }
body.events-page tbody tr.source-activity td { background:#f3fbf6; }
body.events-page tbody tr.source-macos-activity td { background:#f3fbf6; }
body.events-page tbody tr.source-outcome td { background:#fff7ed; }
body.events-page tbody tr.source-milestone td { background:#f0fdf4; }
body.events-page tbody tr:hover td { filter:brightness(.985); }
.time { white-space:nowrap; font-variant-numeric:tabular-nums; }.summary { color:#3b352e; overflow-wrap:anywhere; margin-top:2px; }.title-text { overflow-wrap:anywhere; }.more-note { color:var(--muted); font-weight:650; font-size:11px; white-space:nowrap; }.db-cell { color:#2f2a24; overflow-wrap:anywhere; }.sort-link { color:#2f2a24; font-weight:800; cursor:pointer; }
details.evidence { color:var(--muted); font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; } details.evidence summary { cursor:pointer; color:var(--accent); font-family:ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; } details.evidence pre { white-space:pre-wrap; overflow-wrap:anywhere; max-height:220px; max-width:100%; overflow:auto; margin:8px 0 0; padding:8px; border-radius:10px; background:#fff; border:1px solid var(--line); }
a { color:var(--accent); text-decoration:none; }
.date-picker { position:relative; display:inline-block; }
.date-picker summary { list-style:none; cursor:pointer; display:flex; gap:6px; align-items:center; border:1px solid var(--line); background:white; border-radius:10px; padding:5px 9px; font-size:12px; font-weight:650; }
.date-picker summary::-webkit-details-marker { display:none; }
.calendar-pop { position:absolute; top:34px; left:0; width:238px; background:white; border:1px solid var(--line); border-radius:14px; padding:10px; box-shadow:0 14px 32px rgba(40,30,10,.14); z-index:20; }
.cal-head { display:flex; justify-content:space-between; align-items:center; font-weight:800; margin-bottom:8px; }
.cal-grid { display:grid; grid-template-columns:repeat(7,1fr); gap:3px; }
.cal-dow,.cal-day { text-align:center; font-size:11px; padding:5px 0; border-radius:8px; color:var(--muted); }
.cal-day { color:#3b352e; text-decoration:none; border:1px solid transparent; }
.cal-day.has-data { background:#e8f0ff; color:#174ea6; font-weight:800; border-color:#c7d7ff; }
.cal-day.active { background:var(--ink); color:white; border-color:var(--ink); }
.cal-day.empty { pointer-events:none; opacity:.25; }
.cal-all { display:block; margin-top:8px; text-align:center; font-size:12px; font-weight:750; }
@media (max-width:1000px) { header { grid-template-columns:1fr; gap:8px; } nav { flex-wrap:wrap; } .grid,.section-grid,.three-grid,.report-grid,.analysis-grid { grid-template-columns:1fr; } main { padding:10px; } .donut-row { grid-template-columns:1fr; } }
"""

COLORS = ["#2f6fed", "#7b61ff", "#f59e0b", "#16a34a", "#ef4444", "#64748b"]
VISIBLE_EVENT_SOURCES = ["codex", "git", "hermes"]
SOURCE_LABELS = {"codex": "Codex", "git": "GitHub", "github": "GitHub", "hermes": "Hermes"}


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def source_row_class(source: str | None) -> str:
    normalized = "".join(ch if ch.isalnum() else "-" for ch in (source or "").lower()).strip("-")
    return f"source-{normalized or 'unknown'}"


def format_date_input(value: str | None) -> str:
    if not value:
        return ""
    return value[:10]


def normalize_date_bound(value: str | None, end_of_day: bool = False) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    date_part = value[:10]
    try:
        dt_date.fromisoformat(date_part)
    except ValueError:
        return None
    return date_part + ("T23:59:59" if end_of_day else "T00:00:00")


def resolve_date_range(start_value: str | None, end_value: str | None) -> tuple[str | None, str | None]:
    """Resolve date inputs into effective timestamp bounds.

    UX rule for the database page:
    - start only: filter that single local day
    - start + end: filter the inclusive day range
    - end only: filter up to that local day end
    Invalid date strings are ignored instead of widening the query silently.
    """
    start = normalize_date_bound(start_value)
    end = normalize_date_bound(end_value, end_of_day=True)
    if end:
        return start, end
    if start:
        return start, normalize_date_bound(start[:10], end_of_day=True)
    return None, None


def format_event_time(value: str | None) -> str:
    if not value:
        return ""
    value = value.strip()
    if "T" in value and len(value) >= 19:
        return value[:19].replace("T", " ")
    if "T" in value and len(value) >= 16:
        return value[:16].replace("T", " ")
    if len(value) >= 19:
        return value[:19]
    return value.replace("T", " ")


def truncate_display_text(text: str | None, max_chars: int = 260) -> tuple[str, int]:
    value = "" if text is None else str(text)
    if len(value) <= max_chars:
        return value, 0
    return value[:max_chars].rstrip(), len(value) - max_chars


def display_title_content(title: str | None, summary: str | None) -> str:
    title_text, title_remaining = truncate_display_text(title, 120)
    summary_text, summary_remaining = truncate_display_text(summary, 320)
    title_suffix = (
        f'<span class="more-note"> 后面还有 {title_remaining} 字符</span>'
        if title_remaining
        else ""
    )
    summary_suffix = (
        f'<span class="more-note"> 后面还有 {summary_remaining} 字符</span>'
        if summary_remaining
        else ""
    )
    return (
        f'<strong class="title-text" title="{esc(title)}">{esc(title_text)}{title_suffix}</strong>'
        f'<div class="summary" title="{esc(summary)}">{esc(summary_text)}{summary_suffix}</div>'
    )


def display_source(source: str | None) -> str:
    return SOURCE_LABELS.get(source or "", source or "")


EVENT_LIMIT_OPTIONS = ["100", "500", "1000", "all"]


def parse_event_limit(value: str | None) -> int | None:
    if value == "all":
        return None
    try:
        limit = int(value or "500")
    except ValueError:
        return 500
    return limit if limit in {100, 500, 1000} else 500


def event_limit_control(selected: str | None) -> str:
    value = selected if selected in EVENT_LIMIT_OPTIONS else "500"
    labels = {"100": "100", "500": "500", "1000": "1000", "all": "All"}
    opts = "".join(
        f'<option value="{esc(option)}"{" selected" if option == value else ""}>{esc(labels[option])}</option>'
        for option in EVENT_LIMIT_OPTIONS
    )
    return f'<select name="limit" onchange="this.form.submit()" title="Rows to show">{opts}</select>'


def json_response(handler: BaseHTTPRequestHandler, obj, status=200):
    body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def html_response(handler: BaseHTTPRequestHandler, body: str, status=200):
    data = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def layout(title: str, subtitle: str, active: str, content: str, date_control: str = "") -> str:
    nav = "".join(
        f'<a class="{ "active" if active == key else "" }" href="{href}">{label}</a>'
        for key, label, href in [
            ("today", "报告", "/today"),
            ("events", "数据库", "/events"),
        ]
    )
    body_class = f'{active}-page'
    script = """
<script>
document.addEventListener('click', (event) => {
  document.querySelectorAll('details.date-picker[open]').forEach((picker) => {
    if (!picker.contains(event.target)) picker.removeAttribute('open');
  });
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    document.querySelectorAll('details.date-picker[open]').forEach((picker) => picker.removeAttribute('open'));
  }
});
</script>"""
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{esc(title)}</title><style>{STYLE}</style></head><body class="{body_class}"><header><h1>{esc(title)}</h1>{date_control}<div class="sub">{subtitle}</div><nav>{nav}</nav></header><main>{content}</main>{script}</body></html>"""


def select_control(name: str, options: list[dict[str, str]], selected: str | bool | None, label: str = "") -> str:
    opts=[]
    selected = "" if selected is None or isinstance(selected, bool) else selected
    for item in options:
        value = item["value"]
        is_selected = " selected" if value == selected else ""
        opts.append(f'<option value="{esc(value)}"{is_selected}>{esc(item["label"])}</option>')
    label_html = f'<span>{esc(label)}</span>' if label else ""
    return f'{label_html}<select name="{esc(name)}" onchange="this.form.submit()">{"".join(opts)}</select>'


def available_dates(con) -> list[str]:
    return [row["date"] for row in con.execute("SELECT DISTINCT date FROM events ORDER BY date DESC").fetchall()]


def available_event_date_counts(con, source_in: list[str] | None = None) -> dict[str, int]:
    where = ""
    params: list[str] = []
    if source_in:
        placeholders = ",".join("?" for _ in source_in)
        where = f"WHERE source IN ({placeholders})"
        params.extend(source_in)
    rows = con.execute(
        f"""
        SELECT substr(start, 1, 10) AS event_date, COUNT(*) AS count
        FROM events
        {where}
        GROUP BY substr(start, 1, 10)
        ORDER BY event_date DESC
        """,
        params,
    ).fetchall()
    return {row["event_date"]: int(row["count"]) for row in rows}


def available_event_date_options(con, source_in: list[str] | None = None) -> list[dict[str, str]]:
    return [
        {"value": day, "label": f"{day} · {count} events"}
        for day, count in available_event_date_counts(con, source_in).items()
    ]


def end_date_options(start_date: str | None, date_options: list[dict[str, str]]) -> list[dict[str, str]]:
    start = format_date_input(start_date)
    if not start:
        return date_options
    return [item for item in date_options if item["value"] >= start]


def date_filter_calendar_control(
    action: str,
    name: str,
    selected: str | None,
    date_counts: dict[str, int],
    hidden: dict[str, str | None] | None = None,
    label_text: str = "Date",
    empty_label: str = "All dates",
    min_date: str | None = None,
    allow_empty: bool = True,
    picker_class: str = "",
) -> str:
    hidden = hidden or {}
    selected_date = format_date_input(selected)
    min_date = format_date_input(min_date)
    try:
        base = dt_date.fromisoformat(
            selected_date or (max(date_counts) if date_counts else dt_date.today().isoformat())
        )
    except ValueError:
        base = dt_date.today()
    cal = calendar.Calendar(firstweekday=0)
    days = []
    for day in cal.itermonthdates(base.year, base.month):
        if day.month != base.month:
            days.append('<span class="cal-day empty">·</span>')
            continue
        value = day.isoformat()
        has_data = value in date_counts
        disabled = bool(min_date and value < min_date)
        cls = "cal-day"
        cls += " has-data" if has_data else " no-data"
        if disabled:
            cls += " disabled"
        if value == selected_date:
            cls += " active"
        title_bits = [value, f"{date_counts.get(value, 0)} events"]
        if disabled:
            title_bits.append(f"before Start Date {min_date}")
        title = esc(" · ".join(title_bits))
        if disabled:
            days.append(f'<span class="{cls}" title="{title}">{day.day}</span>')
            continue
        params = {k: v for k, v in hidden.items() if v and k != name}
        params[name] = value
        days.append(f'<a class="{cls}" href="{esc(action)}?{esc(urlencode(params))}" title="{title}">{day.day}</a>')
    empty_link = ""
    if allow_empty:
        params = {k: v for k, v in hidden.items() if v and k != name}
        suffix = "?" + esc(urlencode(params)) if params else ""
        empty_link = f'<a class="cal-all" href="{esc(action)}{suffix}">{esc(empty_label)}</a>'
    label = selected_date or empty_label
    label_html = f'<span>{esc(label_text)}</span>' if label_text else ""
    dows = "".join(f'<div class="cal-dow">{d}</div>' for d in ['M', 'T', 'W', 'T', 'F', 'S', 'S'])
    klass = f'date-picker {picker_class}'.strip()
    return f"""<div class="header-filter">{label_html}<details class="{esc(klass)}"><summary>📅 {esc(label)} ▾</summary><div class="calendar-pop"><div class="cal-head"><span>{base.year}-{base.month:02d}</span></div><div class="cal-grid">{dows}{''.join(days)}</div>{empty_link}</div></details></div>"""


def calendar_control(action: str, selected: str | None, dates: list[str], hidden: dict[str, str | None] | None = None, allow_all: bool = False, label_text: str = "Date") -> str:
    hidden = hidden or {}
    date_set = set(dates)
    selected = selected or (dates[0] if dates else "")
    try:
        base = dt_date.fromisoformat(selected) if selected else dt_date.today()
    except ValueError:
        base = dt_date.today()
    cal = calendar.Calendar(firstweekday=0)
    hidden_html = "".join(f'<input type="hidden" name="{esc(k)}" value="{esc(v or "")}">' for k, v in hidden.items() if k != "date")
    days=[]
    for day in cal.itermonthdates(base.year, base.month):
        if day.month != base.month:
            days.append('<span class="cal-day empty">·</span>')
            continue
        value = day.isoformat()
        params = {k: v for k, v in hidden.items() if v and k != "date"}
        params["date"] = value
        cls = "cal-day"
        if value in date_set:
            cls += " has-data"
        if value == selected:
            cls += " active"
        days.append(f'<a class="{cls}" href="{esc(action)}?{esc(urlencode(params))}" title="{value}">{day.day}</a>')
    all_link = ""
    if allow_all:
        params = {k: v for k, v in hidden.items() if v and k != "date"}
        all_link = f'<a class="cal-all" href="{esc(action)}{("?" + esc(urlencode(params))) if params else ""}">All dates</a>'
    label = selected or "All"
    label_html = f'<span>{esc(label_text)}</span>' if label_text else ""
    return f"""<div class="header-filter">{label_html}<details class="date-picker"><summary>📅 {esc(label)} ▾</summary><div class="calendar-pop"><div class="cal-head"><span>{base.year}-{base.month:02d}</span></div><div class="cal-grid">{''.join(f'<div class="cal-dow">{d}</div>' for d in ['M','T','W','T','F','S','S'])}{''.join(days)}</div>{all_link}</div></details>{hidden_html}</div>"""


def bar_list(items, name_key):
    max_count = max([i["count"] for i in items], default=1)
    rows=[]
    for item in items[:12]:
        name=item[name_key]
        count=item["count"]
        pct=max(3, int(count / max_count * 100))
        rows.append(f'<div class="bar"><div class="bar-name" title="{esc(name)}">{esc(name)}</div><div class="bar-track"><div class="bar-fill" style="width:{pct}%"></div></div><div class="bar-count">{count}</div></div>')
    return "\n".join(rows) or "<div class='label'>暂无数据</div>"


def donut(items, name_key, label):
    total = sum(i["count"] for i in items) or 1
    start = 0.0
    segments=[]
    for idx, item in enumerate(items[:6]):
        pct = item["count"] / total * 100
        end = start + pct
        segments.append(f"{COLORS[idx % len(COLORS)]} {start:.2f}% {end:.2f}%")
        start = end
    if start < 100:
        segments.append(f"#e5e7eb {start:.2f}% 100%")
    bg = ", ".join(segments)
    return f'<div class="donut-row"><div class="donut" data-label="{esc(label)}" style="background:conic-gradient({bg})"></div><div>{bar_list(items, name_key)}</div></div>'


def pct_text(count: int, total: int) -> str:
    if total <= 0:
        return "0%"
    return f"{count / total * 100:.0f}%"


def first_item(items: list[dict[str, Any]], key: str, fallback: str = "暂无") -> str:
    if not items:
        return fallback
    return str(items[0].get(key) or fallback)


def report_stack(items: list[dict[str, Any]]) -> str:
    total = sum(int(i["count"]) for i in items)
    if total <= 0:
        return '<div class="label">暂无数据</div>'
    segs = []
    rows = []
    for idx, item in enumerate(items[:6]):
        count = int(item["count"])
        width = max(2, count / total * 100)
        color = COLORS[idx % len(COLORS)]
        raw_name = item.get("source") or item.get("project") or item.get("device_id") or item.get("location_id") or "unknown"
        name = display_source(raw_name) if item.get("source") else raw_name
        segs.append(f'<div class="stack-seg" title="{esc(name)} · {count}" style="width:{width:.2f}%;background:{color}"></div>')
        rows.append(f'<span><i class="legend-dot" style="background:{color}"></i>{esc(name)} {count}（{pct_text(count,total)}）</span>')
    return f'<div class="stack">{"".join(segs)}</div><div class="pills" style="margin-top:8px">{"".join(rows)}</div>'


def hourly_distribution(con, date: str) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT substr(start, 12, 2) AS hour, COUNT(*) AS count
        FROM events
        WHERE date = ?
        GROUP BY substr(start, 12, 2)
        """,
        (date,),
    ).fetchall()
    counts = {int(row["hour"]): int(row["count"]) for row in rows if str(row["hour"]).isdigit()}
    return [{"hour": f"{hour:02d}:00", "count": counts.get(hour, 0)} for hour in range(24)]


def sparkline(hours: list[dict[str, Any]]) -> str:
    max_count = max((int(h["count"]) for h in hours), default=0) or 1
    bars = []
    for h in hours:
        count = int(h["count"])
        height = max(3, count / max_count * 82) if count else 3
        opacity = "1" if count else ".18"
        bars.append(f'<div class="spark-bar" title="{esc(h["hour"])} · {count}" style="height:{height:.1f}px;opacity:{opacity}"></div>')
    return f'<div class="spark">{"".join(bars)}</div><div class="spark-labels"><span>00</span><span>06</span><span>12</span><span style="text-align:right">23</span></div>'


def mini_table(items: list[dict[str, Any]], name_key: str, total: int, label: str) -> str:
    rows = []
    for item in items[:8]:
        name = item.get(name_key) or "未归因"
        count = int(item["count"])
        rows.append(f'<tr><td>{esc(name)}</td><td style="text-align:right;font-variant-numeric:tabular-nums">{count}</td><td style="text-align:right;color:var(--muted)">{pct_text(count,total)}</td></tr>')
    body = "".join(rows) or '<tr><td colspan="3" class="label">暂无数据</td></tr>'
    return f'<table class="mini-table"><thead><tr><th>{esc(label)}</th><th style="text-align:right">Count</th><th style="text-align:right">Share</th></tr></thead><tbody>{body}</tbody></table>'


def daily_report_text(today: dict[str, Any], hours: list[dict[str, Any]]) -> str:
    summary = today["summary"]
    total = int(summary["total_events"])
    top_source = first_item(today["by_source"], "source")
    top_project = first_item(today["by_project"], "project")
    low = int(summary.get("low_confidence") or 0)
    active_hours = [h for h in hours if int(h["count"])]
    busiest = max(hours, key=lambda h: int(h["count"]), default={"hour": "--", "count": 0})
    source_count = len(summary.get("sources") or [])
    project_count = len(summary.get("projects") or [])
    confidence_note = "归因质量还需要检查" if low else "当天事件均已有项目归因"
    if total == 0:
        return "<p class='report-lede'>这一天没有可分析事件。</p><ul><li>可以切换到有数据的日期，或先运行 collector/import。</li></ul>"
    return f"""
<p class="report-lede">这一天记录了 <strong>{total}</strong> 条事件，覆盖 <strong>{source_count}</strong> 个来源、<strong>{project_count}</strong> 个项目。</p>
<ul>
  <li>主导来源是 <strong>{esc(display_source(top_source))}</strong>，主导项目是 <strong>{esc(top_project)}</strong>。</li>
  <li>活跃时间覆盖 <strong>{len(active_hours)}</strong> 个小时段，峰值在 <strong>{esc(busiest['hour'])}</strong>，该小时有 <strong>{int(busiest['count'])}</strong> 条事件。</li>
  <li>未归因/待检查事件 <strong>{low}</strong> 条，占当天 <strong>{pct_text(low,total)}</strong>；{confidence_note}。</li>
  <li>下面优先看来源、项目、设备/位置和小时分布，不再展示质量较差的时间轴。</li>
</ul>
"""


def date_filter(action: str, date: str | None, extra: str = "") -> str:
    return f"""<div class="filters card"><form method="get" action="{action}" style="display:flex;gap:10px;flex-wrap:wrap;align-items:center"><label>日期 <input name="date" value="{esc(date or '')}" placeholder="YYYY-MM-DD"></label>{extra}<button type="submit">查看</button><a href="{action}">全部日期</a></form></div>"""


def today_page(db_path: Path, date: str | None):
    con = connect(db_path)
    all_dates = available_dates(con)
    if not date:
        date = all_dates[0] if all_dates else ""
    empty_today = {
        "summary": query_summary(con, None),
        "timeline": [],
        "by_source": [],
        "by_project": [],
        "by_device": [],
        "by_location": [],
        "needs_review": [],
    }
    today = query_today(con, date) if date else empty_today
    s = today["summary"]
    total = int(s["total_events"])
    hours = hourly_distribution(con, date) if date else []
    review_rows = "".join(
        f"<div class='mini-event'><strong>{esc(e['title'])}</strong><div class='muted'>{esc(display_source(e['source']))} · {esc(e['project'])}</div></div>"
        for e in today["needs_review"][:8]
    ) or "<div class='label'>暂无需要人工检查的事件</div>"
    dates_desc = all_dates
    prev_link = next_link = ""
    if date in dates_desc:
        idx = dates_desc.index(date)
        if idx + 1 < len(dates_desc):
            prev_day = dates_desc[idx + 1]
            prev_link = f'<a href="/today?date={esc(prev_day)}">← 前一天 {esc(prev_day)}</a>'
        if idx - 1 >= 0:
            next_day = dates_desc[idx - 1]
            next_link = f'<a href="/today?date={esc(next_day)}">后一天 {esc(next_day)} →</a>'
    day_nav = f'<div class="day-nav">{prev_link}{next_link}<a href="/events?start_from={esc(date)}&start_to={esc(date)}">打开当天数据库</a></div>' if date else ""
    content = f"""
<div class="grid">
  <div class="card"><div class="metric">{total}</div><div class="label">当天事件</div></div>
  <div class="card"><div class="metric">{len(s['sources'])}</div><div class="label">来源数量</div></div>
  <div class="card"><div class="metric">{len(s['projects'])}</div><div class="label">项目数量</div></div>
  <div class="card"><div class="metric">{s['low_confidence']}</div><div class="label">未归因 / 待检查</div></div>
</div>
<section class="report-grid">
  <div class="card daily-report"><div class="bucket-head"><h2>每日 Report · {esc(date or '无日期')}</h2><span class="tag source">Daily</span></div>{daily_report_text(today, hours)}{day_nav}</div>
  <div class="card"><h2>来源结构</h2>{donut(today['by_source'], 'source', 'Source')}{report_stack(today['by_source'])}</div>
</section>
<section class="analysis-grid">
  <div class="card"><h2>项目占比</h2>{donut(today['by_project'], 'project', 'Project')}</div>
  <div class="card"><h2>项目排行</h2>{mini_table(today['by_project'], 'project', total, 'Project')}</div>
  <div class="card"><h2>小时分布</h2>{sparkline(hours)}<div class="label">用于看当天事件密度；这里不再渲染事件时间轴。</div></div>
  <div class="card"><h2>时空上下文</h2><h3>Device</h3>{bar_list(today['by_device'], 'device_id')}<h3 style="margin-top:16px">Location</h3>{bar_list(today['by_location'], 'location_id')}</div>
  <div class="card wide-card"><h2>待人工检查</h2>{review_rows}</div>
</section>
"""
    date_control = calendar_control('/today', date, all_dates)
    return layout("DayTrace · 报告", f"{total} events · daily report", "today", content, date_control=date_control)


def sources_page(db_path: Path, date: str | None):
    con = connect(db_path)
    summary = query_summary(con, date)
    source_cards=[]
    for src in summary["sources"]:
        source = src["source"]
        examples = query_events(con, date=date, source=source, limit=3)
        sample = "".join(f"<div class='mini-event'><strong>{esc(e['title'])}</strong><div class='muted'>{esc(e['project'])}</div></div>" for e in examples)
        source_cards.append(f"""<div class="card"><div class="bucket-head"><h2>{esc(source)}</h2><span class="tag source">active</span></div><div class="grid" style="grid-template-columns:repeat(2,1fr)"><div><div class="metric">{src['count']}</div><div class="label">Events</div></div><div><div class="metric">mac</div><div class="label">Collector device</div></div></div><div class="label" style="margin-top:10px">规则：单机 prototype 默认保留该 source 的事件；后续在这里展示 filtering rules / noise / errors。</div>{sample}</div>""")
    cards = ''.join(source_cards) or '<div class="card label">暂无 source</div>'
    content = f"<section class='section-grid' style='margin-top:0'>{cards}</section>"
    date_control = calendar_control('/sources', date, available_dates(con), allow_all=True)
    return layout("DayTrace · 来源是啥", f"{summary['total_events']} events", "sources", content, date_control=date_control)


def events_table(events, filters: dict[str, str | None], options: dict[str, Any]):
    rows=[]
    for e in events:
        row_class = source_row_class(e.get("source"))
        rows.append(f"""
<tr class="{row_class}">
  <td><span class="time" title="{esc(e['start'])}">{esc(format_event_time(e['start']))}</span></td>
  <td class="db-cell"><strong>{esc(display_source(e['source']))}</strong></td>
  <td class="db-cell">{esc(e['device_id'])}</td>
  <td class="db-cell">{esc(e['location_id'])}</td>
  <td class="db-cell">{esc(e['project'])}</td>
  <td>{display_title_content(e.get('title'), e.get('summary'))}</td>
</tr>""")
    hidden_order = f'<input type="hidden" name="order" value="{esc(filters.get("order") or "desc")}">'
    order = "asc" if filters.get("order") == "asc" else "desc"
    next_order = "desc" if order == "asc" else "asc"
    sort_arrow = "↑" if order == "asc" else "↓"
    sort_params = {
        k: v for k, v in {
            "source": filters.get("source"),
            "device_id": filters.get("device_id"),
            "location_id": filters.get("location_id"),
            "project": filters.get("project"),
            "start_from": filters.get("start_from"),
            "start_to": filters.get("start_to"),
            "search": filters.get("search"),
            "limit": filters.get("limit"),
            "order": next_order,
        }.items() if v
    }
    sort_href = "/events?" + urlencode(sort_params)
    date_counts = options.get("date_counts", {})
    date_hidden = {
        "source": filters.get("source"),
        "device_id": filters.get("device_id"),
        "location_id": filters.get("location_id"),
        "project": filters.get("project"),
        "search": filters.get("search"),
        "limit": filters.get("limit"),
        "order": filters.get("order"),
        "start_from": format_date_input(filters.get("start_from")),
        "start_to": format_date_input(filters.get("start_to")),
    }
    time_filter = f"""<div class=\"time-range\">{date_filter_calendar_control('/events', 'start_from', filters.get('start_from'), date_counts, date_hidden, 'Start', 'All dates')}{date_filter_calendar_control('/events', 'start_to', filters.get('start_to') or filters.get('start_from'), date_counts, date_hidden, 'End', 'Same day' if filters.get('start_from') else 'No end', min_date=filters.get('start_from'), picker_class='end-date')}</div>"""
    return f"""
<form method="get" action="/events">
  {hidden_order}
  <div class="table-wrap"><table><colgroup><col class="col-time"><col class="col-source"><col class="col-device"><col class="col-location"><col class="col-project"><col class="col-title"></colgroup>
  <thead><tr>
    <th><div class="th-title"><a class="sort-link" href="{esc(sort_href)}">Time {sort_arrow}</a></div>{time_filter}</th>
    <th><div class="th-title"><span>Source</span></div>{select_control('source', options['source'], filters.get('source'))}</th>
    <th><div class="th-title"><span>Device</span></div>{select_control('device_id', options['device_id'], filters.get('device_id'))}</th>
    <th><div class="th-title"><span>Location</span></div>{select_control('location_id', options['location_id'], filters.get('location_id'))}</th>
    <th><div class="th-title"><span>Project</span></div>{select_control('project', options['project'], filters.get('project'))}</th>
    <th><div class="th-title"><span>Title / Content</span><label class="header-filter"><span>Rows</span>{event_limit_control(filters.get('limit'))}</label></div><input name="search" value="{esc(filters.get('search') or '')}" placeholder="Search title/content"></th>
  </tr></thead><tbody>{''.join(rows) or '<tr><td colspan="6">暂无事件</td></tr>'}</tbody></table></div>
</form>"""

def events_page(db_path: Path, qs: dict[str, list[str]]):
    con = connect(db_path)
    source = qs.get("source", [None])[0] or None
    if source and source not in VISIBLE_EVENT_SOURCES:
        source = None
    project = qs.get("project", [None])[0] or None
    device_id = qs.get("device_id", [None])[0] or None
    location_id = qs.get("location_id", [None])[0] or None
    search = qs.get("search", [None])[0] or None
    raw_limit = qs.get("limit", ["500"])[0]
    event_limit = parse_event_limit(raw_limit)
    raw_start_from = qs.get("start_from", [None])[0] or None
    raw_start_to = qs.get("start_to", [None])[0] or None
    display_start_from = normalize_date_bound(raw_start_from)
    display_start_to = normalize_date_bound(raw_start_to, end_of_day=True)
    if display_start_from and display_start_to and display_start_to < display_start_from:
        raw_start_to = None
        display_start_to = None
    effective_start_from, effective_start_to = resolve_date_range(raw_start_from, raw_start_to)
    order = qs.get("order", ["desc"])[0]
    if order not in {"asc", "desc"}:
        order = "desc"
    filters = {
        "source": source,
        "project": project,
        "device_id": device_id,
        "location_id": location_id,
        "search": search,
        "start_from": display_start_from,
        "start_to": display_start_to,
        "source_in": VISIBLE_EVENT_SOURCES,
        "limit": raw_limit if raw_limit in EVENT_LIMIT_OPTIONS else "500",
        "order": order,
    }
    option_filters = {
        **filters,
        "start_from": effective_start_from,
        "start_to": effective_start_to,
    }
    events = query_events(
        con,
        date=None,
        source=source,
        project=project,
        device_id=device_id,
        location_id=location_id,
        search=search,
        source_in=None if source else VISIBLE_EVENT_SOURCES,
        start_from=effective_start_from,
        start_to=effective_start_to,
        order=order,
        limit=event_limit,
    )
    options: dict[str, Any] = query_filter_options(con, option_filters)
    options["date_counts"] = available_event_date_counts(con, VISIBLE_EVENT_SOURCES)
    options["source"] = [{"value": "", "label": "All"}] + [
        {"value": source_value, "label": SOURCE_LABELS[source_value]}
        for source_value in VISIBLE_EVENT_SOURCES
    ]
    content = events_table(events, filters, options)
    return layout("DayTrace · 数据库", f"{len(events)} events", "events", content)


class Handler(BaseHTTPRequestHandler):
    db_path: Path = DEFAULT_DB

    def log_message(self, format, *args):
        print("[dashboard] " + format % args)

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        date = qs.get("date", [None])[0] or None
        try:
            if parsed.path == "/":
                self.send_response(302)
                self.send_header("Location", "/today" + (("?" + urlencode({"date": date})) if date else ""))
                self.end_headers()
            elif parsed.path == "/today":
                html_response(self, today_page(self.db_path, date))
            elif parsed.path == "/sources":
                self.send_response(302)
                self.send_header("Location", "/events")
                self.end_headers()
            elif parsed.path == "/events":
                html_response(self, events_page(self.db_path, qs))
            elif parsed.path == "/api/summary":
                con = connect(self.db_path)
                json_response(self, query_summary(con, date))
            elif parsed.path == "/api/today":
                con = connect(self.db_path)
                if not date:
                    latest = con.execute("SELECT date FROM events ORDER BY date DESC LIMIT 1").fetchone()
                    date = latest["date"] if latest else ""
                json_response(self, query_today(con, date) if date else {})
            elif parsed.path == "/api/events":
                con = connect(self.db_path)
                raw_api_limit = qs.get("limit", ["500"])[0]
                limit = parse_event_limit(raw_api_limit)
                api_source = qs.get("source", [None])[0] or None
                if api_source and api_source not in VISIBLE_EVENT_SOURCES:
                    api_source = None
                api_start_from, api_start_to = resolve_date_range(
                    qs.get("start_from", [None])[0] or None,
                    qs.get("start_to", [None])[0] or None,
                )
                json_response(self, {"events": query_events(
                    con,
                    date=date,
                    source=api_source,
                    project=qs.get("project", [None])[0] or None,
                    kind=qs.get("kind", [None])[0] or None,
                    device_id=qs.get("device_id", [None])[0] or None,
                    location_id=qs.get("location_id", [None])[0] or None,
                    search=qs.get("search", [None])[0] or None,
                    source_in=None if api_source else VISIBLE_EVENT_SOURCES,
                    start_from=api_start_from,
                    start_to=api_start_to,
                    order=qs.get("order", ["desc"])[0],
                    limit=limit,
                )})
            elif parsed.path == "/api/sources":
                con = connect(self.db_path)
                summary = query_summary(con, date)
                json_response(self, {"date": date, "sources": summary["sources"], "devices": summary["devices"], "locations": summary["locations"]})
            else:
                html_response(self, "<h1>404</h1>", 404)
        except Exception as exc:
            json_response(self, {"error": str(exc)}, 500)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    Handler.db_path = Path(args.db).expanduser().resolve()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"DayTrace dashboard: http://{args.host}:{args.port}", flush=True)
    print(f"Database: {Handler.db_path}", flush=True)
    server.serve_forever()

if __name__ == "__main__":
    main()
