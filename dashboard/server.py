#!/usr/bin/env python3
from __future__ import annotations

import argparse
import calendar
import html
import json
from datetime import date as dt_date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
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
.grid { display:grid; grid-template-columns: repeat(4, minmax(150px,1fr)); gap:10px; }.section-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }.three-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }
.card { background:rgba(255,250,240,.94); border:1px solid var(--line); border-radius:14px; padding:12px; box-shadow:0 8px 18px rgba(65,45,10,.05); }
.metric { font-size:26px; font-weight:850; letter-spacing:-0.04em; }.label { color:var(--muted); margin-top:3px; font-size:12px; } section { margin-top:12px; } h2 { font-size:16px; margin:0 0 8px; } h3 { margin:0 0 5px; font-size:14px; }
.bar { display:flex; align-items:center; gap:10px; margin:9px 0; }.bar-name { width:170px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:14px; }.bar-track { flex:1; height:10px; border-radius:999px; background:#ece3d2; overflow:hidden; }.bar-fill { height:100%; background:linear-gradient(90deg,#2f6fed,#7b61ff); border-radius:999px; }.bar-count { width:42px; text-align:right; color:var(--muted); font-variant-numeric:tabular-nums; }
.filters { display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin:0 0 8px; } input,select,button { border:1px solid var(--line); background:white; border-radius:8px; padding:5px 7px; font:inherit; font-size:12px; } button { background:var(--accent); color:white; border-color:var(--accent); cursor:pointer; }.checkbox { display:flex; gap:4px; align-items:center; }
.timeline { position:relative; padding-left:28px; }.timeline:before { content:""; position:absolute; left:9px; top:6px; bottom:6px; width:3px; border-radius:99px; background:linear-gradient(var(--accent),var(--purple),var(--orange)); }.bucket { position:relative; margin:0 0 16px; padding:14px 14px 14px 16px; border:1px solid var(--line); background:white; border-radius:16px; }.bucket:before { content:""; position:absolute; left:-25px; top:20px; width:14px; height:14px; border-radius:50%; background:var(--accent); box-shadow:0 0 0 4px #e8f0ff; }.bucket-head { display:flex; gap:10px; align-items:center; justify-content:space-between; margin-bottom:8px; }.hour { font-size:20px; font-weight:850; font-variant-numeric:tabular-nums; }.mini-event { padding:8px 0; border-top:1px dashed #eadfcd; }.mini-event:first-of-type { border-top:none; }.event-title { font-weight:700; }.muted { color:var(--muted); }.pills { display:flex; flex-wrap:wrap; gap:6px; }.tag { display:inline-flex; max-width:100%; border-radius:999px; padding:2px 8px; background:#ebe6ff; color:#4632a8; font-size:12px; font-weight:650; overflow:hidden; text-overflow:ellipsis; }.source { background:#e8f0ff; color:#174ea6; }.device { background:#dcfce7; color:#166534; }.location { background:#ffedd5; color:#9a3412; }.low { background:#fff3cd; color:#8a5a00; }
.donut-row { display:grid; grid-template-columns:120px 1fr; gap:12px; align-items:center; }.donut { width:112px; height:112px; border-radius:50%; display:grid; place-items:center; background:conic-gradient(var(--accent) 0 40%, var(--purple) 40% 70%, var(--orange) 70% 88%, #cbd5e1 88% 100%); }.donut:after { content:attr(data-label); width:70px; height:70px; border-radius:50%; background:var(--card); display:grid; place-items:center; font-size:13px; font-weight:800; color:var(--muted); text-align:center; }
.table-wrap { max-height:none; min-height:calc(100vh - 86px); overflow:auto; border:1px solid var(--line); border-radius:16px; background:var(--card); }
body.events-page .table-wrap { height:100%; min-height:0; max-height:100%; overflow:hidden; overscroll-behavior:contain; }
table { width:100%; min-width:1180px; border-collapse:separate; border-spacing:0; background:var(--card); table-layout:fixed; }
body.events-page table { height:100%; display:flex; flex-direction:column; }
body.events-page thead, body.events-page tbody { display:block; }
body.events-page tbody { flex:1; min-height:0; overflow:auto; overscroll-behavior:contain; }
body.events-page tr { display:table; width:100%; table-layout:fixed; }
th,td { padding:7px 9px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; font-size:12px; }
th { position:sticky; top:0; background:#fff7e8; z-index:3; color:#4d4438; box-shadow:0 1px 0 var(--line); } tr:last-child td { border-bottom:none; }
th .th-title { display:flex; align-items:center; justify-content:space-between; gap:6px; font-weight:750; margin-bottom:5px; } th .sort { color:var(--muted); font-size:11px; } th input, th select { width:100%; min-width:0; padding:4px 5px; font-size:11px; } th select + select { margin-top:4px; } .header-filter { margin:0; display:flex; align-items:center; gap:5px; white-space:nowrap; } .header-filter select { max-width:132px; }
.col-time { width:150px; }.col-source { width:140px; }.col-device { width:95px; }.col-location { width:85px; }.col-project { width:125px; }.col-title { width:520px; }.col-evidence { width:50px; }
body.events-page th:nth-child(1), body.events-page td:nth-child(1) { width:150px; }
body.events-page th:nth-child(2), body.events-page td:nth-child(2) { width:140px; }
body.events-page th:nth-child(3), body.events-page td:nth-child(3) { width:95px; }
body.events-page th:nth-child(4), body.events-page td:nth-child(4) { width:85px; }
body.events-page th:nth-child(5), body.events-page td:nth-child(5) { width:125px; }
body.events-page th:nth-child(6), body.events-page td:nth-child(6) { width:520px; }
body.events-page th:nth-child(7), body.events-page td:nth-child(7) { width:50px; }
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
.time { white-space:nowrap; font-variant-numeric:tabular-nums; }.summary { color:#3b352e; overflow-wrap:anywhere; margin-top:2px; }.title-text { overflow-wrap:anywhere; }.db-cell { color:#2f2a24; overflow-wrap:anywhere; }.sort-link { color:#2f2a24; font-weight:800; cursor:pointer; }
details.evidence { color:var(--muted); font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; } details.evidence summary { cursor:pointer; color:var(--accent); font-family:ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; } details.evidence pre { white-space:pre-wrap; overflow-wrap:anywhere; max-height:220px; max-width:100%; overflow:auto; margin:8px 0 0; padding:8px; border-radius:10px; background:#fff; border:1px solid var(--line); }
a { color:var(--accent); text-decoration:none; }
.date-picker { position:relative; display:inline-block; }
.date-picker summary { list-style:none; cursor:pointer; display:flex; gap:6px; align-items:center; border:1px solid var(--line); background:white; border-radius:10px; padding:5px 9px; font-size:12px; font-weight:650; }
.date-picker summary::-webkit-details-marker { display:none; }
.calendar-pop { position:absolute; top:34px; left:0; width:238px; background:white; border:1px solid var(--line); border-radius:14px; padding:10px; box-shadow:0 14px 32px rgba(40,30,10,.14); z-index:20; }
.cal-head { display:flex; justify-content:space-between; align-items:center; font-weight:800; margin-bottom:8px; }
.cal-grid { display:grid; grid-template-columns:repeat(7,1fr); gap:4px; }
.cal-dow,.cal-day { text-align:center; font-size:11px; padding:5px 0; border-radius:8px; color:var(--muted); }
.cal-day { color:#3b352e; text-decoration:none; border:1px solid transparent; }
.cal-day.has-data { background:#e8f0ff; color:#174ea6; font-weight:800; border-color:#c7d7ff; }
.cal-day.active { background:var(--ink); color:white; border-color:var(--ink); }
.cal-day.empty { pointer-events:none; opacity:.25; }
.cal-all { display:block; margin-top:8px; text-align:center; font-size:12px; font-weight:750; }
@media (max-width:1000px) { header { grid-template-columns:1fr; gap:8px; } nav { flex-wrap:wrap; } .grid,.section-grid,.three-grid { grid-template-columns:1fr; } main { padding:10px; } .donut-row { grid-template-columns:1fr; } }
"""

COLORS = ["#2f6fed", "#7b61ff", "#f59e0b", "#16a34a", "#ef4444", "#64748b"]


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def source_row_class(source: str | None) -> str:
    normalized = "".join(ch if ch.isalnum() else "-" for ch in (source or "").lower()).strip("-")
    return f"source-{normalized or 'unknown'}"


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


def date_filter(action: str, date: str | None, extra: str = "") -> str:
    return f"""<div class="filters card"><form method="get" action="{action}" style="display:flex;gap:10px;flex-wrap:wrap;align-items:center"><label>日期 <input name="date" value="{esc(date or '')}" placeholder="YYYY-MM-DD"></label>{extra}<button type="submit">查看</button><a href="{action}">全部日期</a></form></div>"""


def today_page(db_path: Path, date: str | None):
    con = connect(db_path)
    if not date:
        latest = con.execute("SELECT date FROM events ORDER BY date DESC LIMIT 1").fetchone()
        date = latest["date"] if latest else ""
    today = query_today(con, date) if date else {"summary": query_summary(con, None), "timeline": [], "by_source": [], "by_project": [], "by_device": [], "by_location": [], "needs_review": []}
    s = today["summary"]
    timeline_rows=[]
    for bucket in today["timeline"]:
        events_html=[]
        for e in bucket.get("events", []):
            events_html.append(f"""<div class="mini-event"><div class="event-title">{esc(e['title'])}</div><div class="muted">{esc(e['summary'])}</div><div class="pills"><span class="tag source">{esc(e['source'])}</span><span class="tag">{esc(e['project'])}</span><span class="tag device">{esc(e['device_id'])}</span><span class="tag location">{esc(e['location_id'])}</span></div></div>""")
        timeline_rows.append(f"""<div class="bucket"><div class="bucket-head"><div><div class="hour">{esc(bucket['hour'])}</div><div class="label">{bucket['count']} events · {bucket['source_count']} sources · {bucket['project_count']} projects</div></div><div class="pills">{''.join(f'<span class="tag source">{esc(src)}</span>' for src in bucket.get('sources', [])[:4])}</div></div>{''.join(events_html) or '<div class="label">暂无事件详情</div>'}</div>""")
    review_rows = "".join(f"<div class='mini-event'><strong>{esc(e['title'])}</strong><div class='muted'>{esc(e['source'])} · {esc(e['project'])}</div></div>" for e in today["needs_review"][:8]) or "<div class='label'>暂无需要人工检查的事件</div>"
    content = f"""
<div class="grid">
  <div class="card"><div class="metric">{s['total_events']}</div><div class="label">Events</div></div>
  <div class="card"><div class="metric">{len(s['sources'])}</div><div class="label">Sources</div></div>
  <div class="card"><div class="metric">{len(s['projects'])}</div><div class="label">Projects</div></div>
  <div class="card"><div class="metric">{s['low_confidence']}</div><div class="label">Unattributed</div></div>
</div>
<section class="section-grid">
  <div class="card"><h2>Timeline 时间轴</h2><div class="timeline">{''.join(timeline_rows) or '<div class="label">暂无 timeline 数据</div>'}</div></div>
  <div>
    <div class="card"><h2>时空上下文</h2><h3>Device</h3>{bar_list(today['by_device'], 'device_id')}<h3 style="margin-top:16px">Location</h3>{bar_list(today['by_location'], 'location_id')}</div>
    <section class="card"><h2>待人工检查</h2>{review_rows}</section>
  </div>
</section>
<section class="three-grid">
  <div class="card"><h2>Source 占比</h2>{donut(today['by_source'], 'source', 'Source')}</div>
  <div class="card"><h2>Project 占比</h2>{donut(today['by_project'], 'project', 'Project')}</div>
  <div class="card"><h2>Device 占比</h2>{donut(today['by_device'], 'device_id', 'Device')}</div>
</section>
"""
    date_control = calendar_control('/today', date, available_dates(con))
    return layout("DayTrace · 报告", f"{s['total_events']} events", "today", content, date_control=date_control)


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


def events_table(events, filters: dict[str, str | None], options: dict[str, list[dict[str, str]]]):
    rows=[]
    for e in events:
        evidence_preview = json.dumps(e.get("evidence", {}), ensure_ascii=False, indent=2)
        row_class = source_row_class(e.get("source"))
        rows.append(f"""
<tr class="{row_class}">
  <td><span class="time">{esc(e['start'])}</span></td>
  <td class="db-cell"><strong>{esc(e['source'])}</strong></td>
  <td class="db-cell">{esc(e['device_id'])}</td>
  <td class="db-cell">{esc(e['location_id'])}</td>
  <td class="db-cell">{esc(e['project'])}</td>
  <td><strong class="title-text">{esc(e['title'])}</strong><div class="summary">{esc(e['summary'])}</div></td>
  <td><details class="evidence"><summary>查看</summary><pre>{esc(evidence_preview)}</pre></details></td>
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
            "search": filters.get("search"),
            "order": next_order,
        }.items() if v
    }
    sort_href = "/events?" + urlencode(sort_params)
    return f"""
<form method="get" action="/events">
  {hidden_order}
  <div class="table-wrap"><table><colgroup><col class="col-time"><col class="col-source"><col class="col-device"><col class="col-location"><col class="col-project"><col class="col-title"><col class="col-evidence"></colgroup>
  <thead><tr>
    <th><div class="th-title"><a class="sort-link" href="{esc(sort_href)}">Time {sort_arrow}</a></div></th>
    <th><div class="th-title"><span>Source</span></div>{select_control('source', options['source'], filters.get('source'))}</th>
    <th><div class="th-title"><span>Device</span></div>{select_control('device_id', options['device_id'], filters.get('device_id'))}</th>
    <th><div class="th-title"><span>Location</span></div>{select_control('location_id', options['location_id'], filters.get('location_id'))}</th>
    <th><div class="th-title"><span>Project</span></div>{select_control('project', options['project'], filters.get('project'))}</th>
    <th><div class="th-title"><span>Title / Summary</span></div><input name="search" value="{esc(filters.get('search') or '')}" placeholder="Search title/summary/evidence"></th>
    <th><div class="th-title"><span>Evidence</span></div></th>
  </tr></thead><tbody>{''.join(rows) or '<tr><td colspan="7">暂无事件</td></tr>'}</tbody></table></div>
</form>"""

def events_page(db_path: Path, qs: dict[str, list[str]]):
    con = connect(db_path)
    source = qs.get("source", [None])[0] or None
    project = qs.get("project", [None])[0] or None
    device_id = qs.get("device_id", [None])[0] or None
    location_id = qs.get("location_id", [None])[0] or None
    search = qs.get("search", [None])[0] or None
    order = qs.get("order", ["desc"])[0]
    if order not in {"asc", "desc"}:
        order = "desc"
    events = query_events(con, date=None, source=source, project=project, device_id=device_id, location_id=location_id, search=search, order=order, limit=500)
    filters = {
        "source": source,
        "project": project,
        "device_id": device_id,
        "location_id": location_id,
        "search": search,
        "order": order,
    }
    options = query_filter_options(con)
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
                limit = int(qs.get("limit", [500])[0])
                json_response(self, {"events": query_events(
                    con,
                    date=date,
                    source=qs.get("source", [None])[0] or None,
                    project=qs.get("project", [None])[0] or None,
                    kind=qs.get("kind", [None])[0] or None,
                    device_id=qs.get("device_id", [None])[0] or None,
                    location_id=qs.get("location_id", [None])[0] or None,
                    search=qs.get("search", [None])[0] or None,
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
