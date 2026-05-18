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

from daytrace.db import connect, init_db, query_events, query_filter_options, query_summary, query_today

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
.grid { display:grid; grid-template-columns: repeat(4, minmax(150px,1fr)); gap:10px; }.section-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }.three-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }.report-grid { display:grid; grid-template-columns:minmax(320px,1fr) minmax(320px,1fr); gap:12px; align-items:stretch; }
.right-column { display:flex; flex-direction:column; gap:12px; min-width:0; }
.highlights-card .dr-grid { display:grid; grid-template-columns:1fr 1fr; gap:18px; margin:0; }
@media (max-width:900px) { .highlights-card .dr-grid { grid-template-columns:1fr; } }
/* Global controls (day nav + 5-dim selector) — sticks under the page header
   so they're always reachable while scrolling through the daily report
   and project cards. */
.dim-bar { display:flex; justify-content:space-between; align-items:center; gap:12px; margin:0 -18px 12px; padding:8px 18px; flex-wrap:wrap; position:sticky; top:50px; z-index:4; background:rgba(247,245,239,.92); backdrop-filter:blur(10px); border-bottom:1px solid var(--line); }
.dim-bar .day-nav { margin-top:0; padding-top:0; }
.dim-bar-right { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
.dim-tabs, .unit-tabs { display:flex; gap:4px; background:rgba(255,250,240,.94); border:1px solid var(--line); border-radius:999px; padding:3px; box-shadow:0 4px 10px rgba(65,45,10,.04); }
.dim-tab, .unit-tab { font-size:12.5px; padding:4px 14px; border-radius:999px; border:none; background:transparent; color:#3b352e; font-weight:650; cursor:pointer; transition:background .12s, color .12s; }
.dim-tab:hover, .unit-tab:hover { background:rgba(0,0,0,.04); }
.dim-tab.active, .unit-tab.active { background:var(--ink); color:white; }.analysis-grid { display:grid; grid-template-columns:repeat(2,minmax(260px,1fr)); gap:12px; }.wide-card { grid-column:1 / -1; }
.card { background:rgba(255,250,240,.94); border:1px solid var(--line); border-radius:14px; padding:12px; box-shadow:0 8px 18px rgba(65,45,10,.05); }
.metric { font-size:26px; font-weight:850; letter-spacing:-0.04em; }.metric-small { font-size:18px; font-weight:850; }.label { color:var(--muted); margin-top:3px; font-size:12px; } section { margin-top:12px; } h2 { font-size:16px; margin:0 0 8px; } h3 { margin:0 0 5px; font-size:14px; }
.bar { display:flex; align-items:center; gap:10px; margin:9px 0; }.bar-name { width:170px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:14px; }.bar-track { flex:1; height:10px; border-radius:999px; background:#ece3d2; overflow:hidden; }.bar-fill { height:100%; background:linear-gradient(90deg,#2f6fed,#7b61ff); border-radius:999px; }.bar-count { width:42px; text-align:right; color:var(--muted); font-variant-numeric:tabular-nums; }
.filters { display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin:0 0 8px; } input,select,button { border:1px solid var(--line); background:white; border-radius:8px; padding:5px 7px; font:inherit; font-size:12px; } button { background:var(--accent); color:white; border-color:var(--accent); cursor:pointer; }.checkbox { display:flex; gap:4px; align-items:center; }
.bucket-head { display:flex; gap:10px; align-items:center; justify-content:space-between; margin-bottom:8px; }.mini-event { padding:8px 0; border-top:1px dashed #eadfcd; }.mini-event:first-of-type { border-top:none; }.event-title { font-weight:700; }.muted { color:var(--muted); }.pills { display:flex; flex-wrap:wrap; gap:6px; }.tag { display:inline-flex; max-width:100%; border-radius:999px; padding:2px 8px; background:#ebe6ff; color:#4632a8; font-size:12px; font-weight:650; overflow:hidden; text-overflow:ellipsis; }.source { background:#e8f0ff; color:#174ea6; }.device { background:#dcfce7; color:#166534; }.location { background:#ffedd5; color:#9a3412; }.low { background:#fff3cd; color:#8a5a00; }
.daily-report { line-height:1.55; display:flex; flex-direction:column; }.daily-report ul { margin:8px 0 0 18px; padding:0; }.daily-report li { margin:5px 0; }.report-lede { font-size:15px; color:#362f27; margin:0 0 8px; }.day-nav { display:flex; gap:8px; flex-wrap:wrap; margin-top:auto; padding-top:14px; }.day-nav a { border:1px solid var(--line); background:white; border-radius:999px; padding:4px 9px; font-size:12px; font-weight:700; }
.donut-row { display:grid; grid-template-columns:120px 1fr; gap:12px; align-items:center; }.donut { width:112px; height:112px; border-radius:50%; display:grid; place-items:center; background:conic-gradient(var(--accent) 0 40%, var(--purple) 40% 70%, var(--orange) 70% 88%, #cbd5e1 88% 100%); }.donut:after { content:attr(data-label); width:70px; height:70px; border-radius:50%; background:var(--card); display:grid; place-items:center; font-size:13px; font-weight:800; color:var(--muted); text-align:center; }.legend-dot { width:9px; height:9px; display:inline-block; border-radius:50%; margin-right:6px; }.stack { display:flex; height:18px; overflow:hidden; border-radius:999px; background:#ece3d2; border:1px solid #e2d6c4; }.stack-seg { min-width:2px; height:100%; }.mini-table { width:100%; border-collapse:separate; border-spacing:0; }.mini-table th,.mini-table td { font-size:12px; padding:6px 4px; border-bottom:1px solid #eadfcd; }.mini-table th { position:static; background:transparent; box-shadow:none; color:var(--muted); }.composition-card .cc-tabs { display:flex; gap:4px; flex-wrap:wrap; }
.composition-card .cc-tab { font-size:12px; padding:3px 10px; border-radius:999px; border:1px solid var(--line); background:white; color:#3b352e; font-weight:650; cursor:pointer; }
.composition-card .cc-tab.active { background:var(--ink); color:white; border-color:var(--ink); }
.composition-card { display:flex; flex-direction:column; }
.composition-card .cc-pane { display:none; flex:1; }
.composition-card .cc-pane.show { display:flex; }
.composition-card .cc-pane-body { display:grid; grid-template-columns:minmax(180px,.85fr) minmax(220px,1.15fr); gap:20px; align-items:center; width:100%; }
.composition-card .cc-pane-empty { place-items:center; }
.composition-card .cc-donut-wrap { display:flex; justify-content:center; align-items:center; padding:4px; }
.composition-card .cc-donut { width:210px; height:210px; border-radius:50%; display:grid; place-items:center; box-shadow:0 8px 18px rgba(40,30,10,.10); position:relative; }
.composition-card .cc-donut-hole { width:124px; height:124px; border-radius:50%; background:var(--card); display:grid; place-items:center; text-align:center; box-shadow:inset 0 1px 2px rgba(0,0,0,.05); }
.composition-card .cc-donut-total { font-size:28px; font-weight:800; color:var(--ink); font-variant-numeric:tabular-nums; line-height:1; }
.composition-card .cc-donut-label { font-size:11px; color:var(--muted); margin-top:3px; letter-spacing:.06em; text-transform:uppercase; }
.composition-card .cc-bars { display:flex; flex-direction:column; gap:7px; max-height:none; padding-right:2px; }
.composition-card .cc-bar { display:grid; grid-template-columns:11px minmax(70px,1fr) minmax(80px,2fr) 36px 36px; align-items:center; gap:9px; font-size:12.5px; }
.composition-card .cc-bar-sw { width:10px; height:10px; border-radius:3px; flex:none; }
.composition-card .cc-bar-name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#3b352e; font-weight:600; }
.composition-card .cc-bar-track { height:9px; border-radius:999px; background:#ece3d2; overflow:hidden; }
.composition-card .cc-bar-fill { display:block; height:100%; border-radius:999px; }
.composition-card .cc-bar-count { text-align:right; font-variant-numeric:tabular-nums; color:#3b352e; font-weight:600; }
.composition-card .cc-bar-pct { text-align:right; font-variant-numeric:tabular-nums; color:var(--muted); font-size:11px; }
@media (max-width:1100px) { .composition-card .cc-pane-body { grid-template-columns:1fr; } }
.timeline-section { margin:12px 0; }
/* ----- day_report cards (one card per day) ----- */
.dr-filter-strip { display:flex; gap:14px; align-items:center; flex-wrap:wrap; padding:8px 12px; margin:0 0 12px; background:rgba(255,250,240,.94); border:1px solid var(--line); border-radius:12px; font-size:12.5px; }
.dr-filter-inline { display:flex; gap:14px; align-items:center; flex-wrap:wrap; flex:1; }
.dr-filter-strip label { display:inline-flex; gap:4px; align-items:center; }
.dr-filter-strip select { border:1px solid var(--line); border-radius:8px; padding:3px 6px; background:white; font:inherit; font-size:12px; }
.dr-rowcount { margin-left:auto; }
.dr-reset { color:var(--accent); font-weight:650; }
.day-report-cards { display:flex; flex-direction:column; gap:14px; }
.day-report-card { background:rgba(255,250,240,.96); border:1px solid var(--line); border-radius:14px; padding:14px 18px; box-shadow:0 6px 14px rgba(65,45,10,.04); }
.dr-head { display:flex; align-items:center; gap:18px; flex-wrap:wrap; margin-bottom:10px; padding-bottom:10px; border-bottom:1px dashed #eadfcd; }
.dr-date { font-size:17px; font-weight:800; color:var(--ink); font-variant-numeric:tabular-nums; }
.dr-stats { display:flex; gap:14px; flex-wrap:wrap; flex:1; }
.dr-stat { display:flex; flex-direction:column; align-items:flex-start; padding:0 10px; border-left:1px solid #eadfcd; }
.dr-stat:first-child { border-left:none; padding-left:0; }
.dr-stat-num { font-size:15px; font-weight:700; color:var(--ink); font-variant-numeric:tabular-nums; }
.dr-stat-lbl { font-size:10.5px; color:var(--muted); letter-spacing:.06em; text-transform:uppercase; }
.dr-actions { display:flex; gap:10px; font-size:12px; }
.dr-actions a { color:var(--accent); white-space:nowrap; }
.dr-headline { font-size:18px; font-weight:800; color:var(--ink); margin:6px 0 6px; line-height:1.3; }
.dr-narrative { font-size:14px; line-height:1.65; color:#362f27; margin:0 0 12px; }
.dr-grid { display:grid; grid-template-columns:1fr 1fr; gap:18px; margin:0 0 12px; }
@media (max-width:900px) { .dr-grid { grid-template-columns:1fr; } }
.dr-section h4 { margin:0 0 6px; font-size:12.5px; font-weight:700; color:#4d4438; letter-spacing:.04em; }
.dr-bullets { margin:0; padding-left:18px; font-size:13px; line-height:1.55; }
.dr-bullets li { margin:3px 0; }
.dr-highlights li::marker { color:var(--green); }
.dr-concerns li::marker { color:var(--red); }
.dr-changes li::marker { color:var(--orange); }
.dr-continuity { background:#fff7e8; border:1px solid #f0d68b; border-radius:10px; padding:9px 12px; margin-bottom:12px; font-size:13px; line-height:1.5; }
.dr-cont-label { color:var(--muted); font-weight:650; margin-right:6px; }
.dr-cont-text { color:#362f27; }
.dr-facts { display:flex; flex-direction:column; gap:4px; padding:10px 12px; background:#faf6ec; border-radius:10px; font-size:12.5px; color:#4d4438; margin-bottom:10px; }
.dr-fact { font-variant-numeric:tabular-nums; }
.dr-raw-wrap > summary { cursor:pointer; color:var(--muted); font-size:11px; padding:4px 0; }
.dr-raw-row { margin:4px 0; padding-left:14px; }
.dr-raw-row > summary { cursor:pointer; color:var(--accent); font-size:11.5px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
.dr-raw-row pre { margin:4px 0; padding:8px; background:#fff; border:1px solid var(--line); border-radius:8px; font-size:11px; max-height:240px; overflow:auto; }

/* ----- momentum + status chips (used in cards and project rows) ----- */
.momentum-chip { display:inline-flex; align-items:center; gap:3px; font-size:11px; font-weight:700; padding:2px 9px; border-radius:999px; white-space:nowrap; }
.momentum-rising   { background:#dcfce7; color:#166534; }
.momentum-steady   { background:#e0e7ff; color:#3730a3; }
.momentum-dropping { background:#fee2e2; color:#991b1b; }
.momentum-new      { background:#fef3c7; color:#92400e; }
.momentum-paused   { background:#f1f5f9; color:#475569; }
.momentum-blocked  { background:#fee2e2; color:#7f1d1d; }
.status-chip { display:inline-flex; align-items:center; font-size:11px; font-weight:700; padding:2px 9px; border-radius:999px; white-space:nowrap; }
.status-in_progress { background:#dbeafe; color:#1e40af; }
.status-done        { background:#dcfce7; color:#166534; }
.status-blocked     { background:#fee2e2; color:#991b1b; }
.status-explored    { background:#fef3c7; color:#92400e; }
.status-unknown     { background:#f1f5f9; color:#475569; }

/* ----- day_project_report table ----- */
.dpr-table table { font-size:12.5px; }
.dpr-table th { background:#fff7e8; }
.dpr-table .col-date { width:90px; white-space:nowrap; }
.dpr-table .col-project { width:140px; }
.dpr-table .col-num { width:60px; text-align:right; font-variant-numeric:tabular-nums; }
.dpr-table .col-share { width:120px; }
.dpr-table .col-status { width:90px; }
.dpr-table .col-ai { min-width:280px; max-width:420px; }
.dpr-table .col-cont { width:170px; }
.dpr-table .col-titles { width:220px; }
.dpr-table .col-meta { width:160px; }
.project-chip { display:inline-flex; padding:2px 9px; border-radius:999px; background:#ebe6ff; color:#4632a8; font-size:12px; font-weight:650; text-decoration:none; max-width:130px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.project-chip:hover { background:#dcd3ff; }
.share-cell { display:flex; align-items:center; gap:6px; }
.share-bar { flex:1; height:6px; background:#ece3d2; border-radius:999px; overflow:hidden; }
.share-fill { height:100%; background:linear-gradient(90deg, var(--accent), var(--purple)); border-radius:999px; }
.share-pct { font-size:11px; font-variant-numeric:tabular-nums; color:#4d4438; min-width:30px; }
.ai-summary-text { font-weight:600; color:var(--ink); line-height:1.4; margin-bottom:4px; }
.ai-bullets { margin:3px 0; padding-left:16px; font-size:11.5px; line-height:1.45; }
.ai-bullets li { margin:1px 0; color:#4d4438; }
.ai-next-label { font-size:10px; color:var(--muted); margin-top:4px; letter-spacing:.05em; text-transform:uppercase; }
.ai-next li { color:#7b61ff; }
.cont-text { font-size:11.5px; color:#4d4438; margin-top:3px; line-height:1.4; }
.top-titles-list { margin:0; padding-left:16px; font-size:11.5px; line-height:1.45; color:#4d4438; }
.top-titles-list li { margin:1px 0; }
.tt-time { font-variant-numeric:tabular-nums; color:var(--muted); margin-right:4px; }

/* segmented pill group — same look as the global dim-bar */
.table-switcher { display:inline-flex; gap:4px; margin:0 0 12px; padding:3px; background:rgba(255,250,240,.94); border:1px solid var(--line); border-radius:999px; box-shadow:0 4px 10px rgba(65,45,10,.04); flex-wrap:wrap; }
.table-tab { font-size:12.5px; padding:5px 14px; border-radius:999px; border:none; background:transparent; color:#3b352e; font-weight:650; text-decoration:none; transition:background .12s, color .12s; }
.table-tab:hover { background:rgba(0,0,0,.04); }
.table-tab.active { background:var(--ink); color:white; }

/* compact stats strip inside the home page daily-report card —
   grid with equal columns so all 4 stats align on every day's render */
.dr-stats-compact { display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:14px; margin:0 0 12px; padding-bottom:10px; border-bottom:1px dashed #eadfcd; }
.dr-stats-compact .dr-stat { display:flex; flex-direction:column; gap:2px; padding:0; border:none; }
.dr-stats-compact .dr-stat-num { font-size:16px; font-weight:800; font-variant-numeric:tabular-nums; color:var(--ink); white-space:nowrap; }
.dr-stats-compact .dr-stat-lbl { font-size:10.5px; color:var(--muted); letter-spacing:.06em; text-transform:uppercase; }

/* project cards section on /today */
.project-cards-section { margin:14px 0; }
.section-title { font-size:14px; font-weight:700; color:#4d4438; margin:0 0 10px; letter-spacing:.02em; }
.project-cards { display:flex; flex-direction:column; gap:8px; }
.project-card { background:rgba(255,250,240,.96); border:1px solid var(--line); border-radius:12px; padding:0; box-shadow:0 4px 10px rgba(65,45,10,.04); overflow:hidden; transition:box-shadow .12s; }
.project-card[open] { box-shadow:0 8px 18px rgba(65,45,10,.07); }
/* Fixed-width columns so bars align across every row regardless of name /
   percent width. Mobile collapses below. */
.project-card > summary {
  list-style:none; cursor:pointer; padding:12px 14px;
  display:grid; align-items:center; gap:14px;
  grid-template-columns:
    minmax(0, 200px)   /* name */
    48px              /* share % (tabular) */
    minmax(120px, 1fr) /* bar fills */
    150px             /* events · time */
    24px;             /* chevron */
}
.project-card > summary::-webkit-details-marker { display:none; }
.project-card > summary::marker { content:""; }
.project-card > summary:hover { background:rgba(0,0,0,.02); }
.pc-project-name { font-weight:700; font-size:14px; color:var(--ink); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.pc-share-pct { font-size:12px; color:#4d4438; font-weight:700; font-variant-numeric:tabular-nums; text-align:right; }
.pc-share-bar { height:7px; background:#ece3d2; border-radius:999px; overflow:hidden; }
.pc-share-fill { height:100%; background:linear-gradient(90deg, var(--accent), var(--purple)); border-radius:999px; transition:width .2s; }
.pc-events { font-size:12px; color:var(--muted); white-space:nowrap; font-variant-numeric:tabular-nums; text-align:right; }
.pc-chevron { color:var(--muted); font-size:14px; line-height:1; transition:transform .15s; display:inline-flex; align-items:center; justify-content:center; width:24px; height:24px; border-radius:50%; background:rgba(0,0,0,.04); }
.project-card > summary:hover .pc-chevron { background:rgba(0,0,0,.08); color:var(--ink); }
/* Collapsed = ◂ (points left). Expanded rotates 90° clockwise → ▾ (down,
   pointing at the now-visible content). */
.project-card[open] .pc-chevron { transform:rotate(-90deg); }
.pc-body { padding:0 14px 14px; border-top:1px dashed #eadfcd; font-size:13px; line-height:1.55; color:#362f27; }
.pc-summary-text { font-size:14px; font-weight:600; margin:12px 0 8px; color:var(--ink); }
.pc-section-label { font-size:11px; font-weight:700; color:var(--muted); letter-spacing:.06em; text-transform:uppercase; margin:10px 0 4px; }
.pc-bullets { margin:0; padding-left:18px; font-size:13px; line-height:1.55; }
.pc-bullets li { margin:2px 0; }
.pc-next li { color:#5b3fc7; }
.pc-continuity { background:#fff7e8; border:1px solid #f0d68b; border-radius:10px; padding:8px 12px; margin:10px 0; font-size:12.5px; }
.pc-cont-label { color:var(--muted); font-weight:650; margin-right:6px; }
.pc-titles { margin:0; padding-left:18px; font-size:12.5px; color:#4d4438; }
.pc-titles li { margin:2px 0; }
.pc-titles .tt-time { font-variant-numeric:tabular-nums; color:var(--muted); margin-right:6px; }
.pc-meta { font-size:11.5px; margin-top:10px; }
.pc-actions { margin-top:10px; font-size:12px; }
.pc-actions a { color:var(--accent); }
@media (max-width:900px) {
  .project-card > summary { grid-template-columns:1fr 48px 110px 24px; gap:10px; }
  .pc-share-bar, .pc-events { display:none; }
}
.channel-cell summary { cursor:pointer; color:var(--accent); font-size:12px; max-width:160px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; display:block; }
.channel-cell pre { white-space:pre-wrap; overflow:auto; max-height:240px; max-width:340px; margin:6px 0 0; padding:6px 8px; background:#fff; border:1px solid var(--line); border-radius:8px; font-size:11px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
.small { font-size:11px; } .mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
.timeline-card .tl-tab-group { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
.timeline-card .tl-tabs, .timeline-card .tl-style-tabs { display:flex; gap:4px; flex-wrap:wrap; }
.timeline-card .tl-tab, .timeline-card .tl-style-tab { font-size:12px; padding:3px 10px; border-radius:999px; border:1px solid var(--line); background:white; color:#3b352e; font-weight:650; cursor:pointer; }
.timeline-card .tl-tab.active, .timeline-card .tl-style-tab.active { background:var(--ink); color:white; border-color:var(--ink); }
.timeline-card .tl-style-tab { background:#fff3cd; border-color:#f0d68b; color:#8a5a00; }
.timeline-card .tl-style-tab.active { background:#7c5c00; border-color:#7c5c00; color:white; }
.timeline-card .tl-axis-wrap { position:relative; margin:14px 0 6px; }
/* Shared 24h canvas */
.timeline-card .tl-axis, .timeline-card .tl-hist { position:relative; height:200px; background:linear-gradient(180deg,#faf6ec,#f3ead8); border:1px solid #e6dcc6; border-radius:12px; overflow:hidden; }
.timeline-card .tl-hist { display:none; }
.timeline-card[data-style="histogram"] .tl-axis { display:none; }
.timeline-card[data-style="histogram"] .tl-hist { display:block; }
.timeline-card[data-style="swimlane"] .tl-axis { display:none; }
.timeline-card[data-style="swimlane"] .tl-hist { display:none; }
/* Hour grid (shared markup) */
.timeline-card .tl-hour { position:absolute; top:0; bottom:0; border-left:1px dashed #e0d4bd; width:0; pointer-events:none; }
.timeline-card .tl-hour span { position:absolute; bottom:3px; left:4px; font-size:10px; color:var(--muted); font-variant-numeric:tabular-nums; }
/* Tick view */
.timeline-card .tl-tick { position:absolute; top:10px; bottom:18px; width:3px; border-radius:2px; background:#9b8e76; transform:translateX(-1px); opacity:.86; transition:opacity .12s, transform .12s; cursor:pointer; }
.timeline-card .tl-tick:hover { opacity:1; transform:translateX(-1px) scaleY(1.18); box-shadow:0 0 0 2px rgba(255,255,255,.6); z-index:2; }
/* Histogram view: 3 panes, only the one matching data-mode is visible.
   Pane is inset on the left to make room for the y-axis tick labels. */
.timeline-card .tl-hist-pane { display:none; position:absolute; left:38px; right:6px; top:8px; bottom:22px; }
.timeline-card[data-mode="source"]   .tl-hist-pane[data-for="source"]   { display:block; }
.timeline-card[data-mode="project"]  .tl-hist-pane[data-for="project"]  { display:block; }
.timeline-card[data-mode="device"]   .tl-hist-pane[data-for="device"]   { display:block; }
.timeline-card[data-mode="location"] .tl-hist-pane[data-for="location"] { display:block; }
.timeline-card[data-mode="activity"] .tl-hist-pane[data-for="activity"] { display:block; }
.timeline-card .tl-grid-line { position:absolute; left:0; right:0; border-top:1px dashed #d9ccaf; height:0; pointer-events:none; }
.timeline-card .tl-y-ticks { position:absolute; left:0; right:0; top:0; bottom:0; pointer-events:none; }
.timeline-card .tl-y-tick { position:absolute; left:-36px; transform:translateY(50%); width:32px; text-align:right; font-size:10px; color:var(--muted); font-variant-numeric:tabular-nums; background:transparent; }
.timeline-card .tl-bin { position:absolute; bottom:0; display:flex; flex-direction:column-reverse; border-radius:3px 3px 0 0; overflow:hidden; cursor:pointer; transition:filter .12s, transform .12s; }
.timeline-card .tl-bin:hover { filter:brightness(1.12) saturate(1.1); transform:scaleY(1.04); transform-origin:bottom; z-index:2; box-shadow:0 -2px 8px rgba(0,0,0,.18); }
.timeline-card .tl-seg { display:block; min-height:1px; }
/* Swimlane view */
.timeline-card .tl-swim { display:none; padding:6px 0; }
.timeline-card[data-style="swimlane"] .tl-swim { display:block; }
.timeline-card .tl-swim-pane { display:none; }
.timeline-card[data-mode="source"]   .tl-swim-pane[data-for="source"]   { display:block; }
.timeline-card[data-mode="project"]  .tl-swim-pane[data-for="project"]  { display:block; }
.timeline-card[data-mode="device"]   .tl-swim-pane[data-for="device"]   { display:block; }
.timeline-card[data-mode="location"] .tl-swim-pane[data-for="location"] { display:block; }
.timeline-card[data-mode="activity"] .tl-swim-pane[data-for="activity"] { display:block; }
.timeline-card .tl-swim-row { display:grid; grid-template-columns:160px 1fr; gap:10px; align-items:center; padding:4px 0; }
.timeline-card .tl-swim-label { display:flex; gap:6px; align-items:baseline; justify-content:space-between; padding:2px 10px; font-size:12.5px; font-weight:650; color:#3b352e; }
.timeline-card .tl-swim-name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.timeline-card .tl-swim-count { font-size:11px; font-variant-numeric:tabular-nums; flex:none; color:var(--muted); }
.timeline-card .tl-swim-track { position:relative; height:24px; background:linear-gradient(180deg,#faf6ec,#f3ead8); border:1px solid #e6dcc6; border-radius:6px; }
.timeline-card .tl-swim-tick { position:absolute; top:3px; bottom:3px; width:3px; border-radius:2px; transform:translateX(-1px); cursor:pointer; transition:transform .12s; }
.timeline-card .tl-swim-tick:hover { transform:translateX(-1px) scaleY(1.4) scaleX(1.6); z-index:2; box-shadow:0 0 0 2px rgba(255,255,255,.6); }
/* Overall row sits above per-dim panes, taller and a neutral ink color */
.timeline-card .tl-swim-overall { padding-bottom:8px; margin-bottom:4px; border-bottom:1px dashed #e6dcc6; }
.timeline-card .tl-swim-overall-label .tl-swim-name { font-weight:800; color:var(--ink); }
.timeline-card .tl-swim-overall-track { height:30px; background:linear-gradient(180deg,#f7f1e0,#ebe3cf); border-color:#d9ccaf; }
/* Fallback color for overall ticks whose value isn't in the active dim's
   top-10 palette — they show as a neutral grey instead of inheriting the
   ink color. Per-(dim,value) rules generated server-side override this. */
.timeline-card .tl-swim-tick-overall { background:#b8ad95; opacity:.85; }
/* Axis bottom labels */
.timeline-card .tl-axis-bottom { display:flex; justify-content:space-between; color:var(--muted); font-size:11px; padding:0 2px; font-variant-numeric:tabular-nums; }
.timeline-card .tl-meta { margin-top:8px; color:var(--muted); font-size:11px; }
.timeline-card .tl-empty { margin-top:8px; color:var(--muted); font-size:12px; text-align:center; padding:18px; }
/* Legends */
.timeline-card .tl-legend { display:none; flex-wrap:wrap; gap:6px 14px; margin-top:10px; font-size:12px; color:#4d4438; }
.timeline-card .tl-legend.show { display:flex; }
.timeline-card .tl-legend-item { display:inline-flex; align-items:center; gap:5px; max-width:240px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.timeline-card .tl-swatch { width:10px; height:10px; border-radius:3px; flex:none; }
/* Floating tooltip (one per card, repositioned on hover) */
.timeline-card .tl-tooltip { position:absolute; pointer-events:none; z-index:10; background:rgba(34,28,18,.95); color:#fff7e8; border-radius:10px; padding:8px 10px; box-shadow:0 10px 26px rgba(0,0,0,.28); font-size:11.5px; max-width:320px; line-height:1.45; }
.timeline-card .tl-tooltip[hidden] { display:none; }
.timeline-card .tl-tip-time { font-weight:700; font-variant-numeric:tabular-nums; color:#ffe7a8; margin-bottom:2px; }
.timeline-card .tl-tip-title { font-weight:600; margin-bottom:5px; overflow:hidden; text-overflow:ellipsis; display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; }
.timeline-card .tl-tip-chip { display:inline-flex; align-items:center; gap:4px; margin:2px 6px 0 0; padding:1px 7px; border-radius:999px; background:rgba(255,255,255,.1); font-size:11px; }
.timeline-card .tl-tip-chip b { font-weight:600; color:#ffd58a; }
.timeline-card .tl-tip-sw { width:8px; height:8px; border-radius:50%; flex:none; }
.spark { display:grid; grid-template-columns:repeat(24,1fr); gap:2px; align-items:end; height:86px; padding-top:6px; }.spark-bar { background:linear-gradient(180deg,#7b61ff,#2f6fed); border-radius:4px 4px 0 0; min-height:3px; }.spark-labels { display:grid; grid-template-columns:repeat(4,1fr); color:var(--muted); font-size:11px; margin-top:4px; }
.table-wrap { max-height:none; min-height:calc(100vh - 86px); overflow:auto; border:1px solid var(--line); border-radius:16px; background:var(--card); }
body.events-page .table-wrap { height:100%; min-height:0; max-height:100%; overflow:hidden; overscroll-behavior:contain; }
table { width:100%; min-width:0; border-collapse:separate; border-spacing:0; background:var(--card); table-layout:fixed; }
body.events-page table { height:100%; min-width:0; display:flex; flex-direction:column; }
body.events-page thead, body.events-page tbody { display:block; }
body.events-page tbody { flex:1; min-height:0; overflow-y:auto; overflow-x:hidden; overscroll-behavior:contain; }
body.events-page tr { display:table; width:100%; table-layout:fixed; }
th,td { padding:7px 9px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; font-size:12px; }
th { position:sticky; top:0; background:#fff7e8; z-index:3; color:#4d4438; box-shadow:0 1px 0 var(--line); } tr:last-child td { border-bottom:none; }
th .th-title { display:flex; align-items:center; justify-content:space-between; gap:6px; font-weight:750; margin-bottom:5px; } .clear-filters { font-size:11px; font-weight:600; text-decoration:none; padding:2px 8px; border-radius:999px; background:#fff3cd; color:#8a5a00; border:1px solid #f0d68b; white-space:nowrap; } .clear-filters:hover { background:#ffe69c; } .clear-filters.muted { background:transparent; color:var(--muted); border-color:transparent; cursor:default; } th .sort { color:var(--muted); font-size:11px; } th input, th select { width:100%; min-width:0; padding:4px 5px; font-size:11px; } th select + select { margin-top:4px; } .header-filter { margin:0; display:flex; align-items:center; gap:5px; white-space:nowrap; } .header-filter select { max-width:132px; }
.col-time { width:190px; }.col-source { width:78px; }.col-activity { width:96px; }.col-location { width:76px; }.col-project { width:130px; }.col-title { width:auto; }
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


def layout(title: str, subtitle: str, active: str, content: str, date_control: str = "", body_class: str | None = None) -> str:
    nav = "".join(
        f'<a class="{ "active" if active == key else "" }" href="{href}">{label}</a>'
        for key, label, href in [
            ("today", "日报", "/today"),
            ("weekly", "周报", "/weekly"),
            ("events", "数据库", "/events"),
        ]
    )
    if body_class is None:
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
    # Show "2026-05-15 周五" so the user knows what day of week without
    # squinting at the calendar. Chinese single-char abbreviations.
    _WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]
    if selected:
        try:
            _wd = _WEEKDAYS[dt_date.fromisoformat(selected).weekday()]
            label = f"{selected} 周{_wd}"
        except ValueError:
            label = selected
    else:
        label = "All"
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


_COMPOSITION_OTHER_COLOR = "#b8ad95"


def composition_pane(items: list[dict[str, Any]], name_keys: list[str] | str,
                      *, unit_label: str = "events") -> str:
    """One pane: a large donut (left, half the card) + a colored bar list
    (right, half the card). Donut segments and bar fills share the same
    TIMELINE_PALETTE so colors line up across donut → bars → timeline.

    `name_keys` is a list of dict keys to try in order when extracting the
    bucket name from an item — supports rows from compute_breakdown (which
    uses 'name') and rows from query_summary (which uses 'source'/'project'
    /etc) without forking the function."""
    if isinstance(name_keys, str):
        name_keys = [name_keys]

    def _name_of(item: dict) -> str:
        for k in name_keys:
            v = item.get(k)
            if v:
                return str(v)
        return "misc"

    items = list(items or [])
    total = sum(int(i["count"]) for i in items)
    if total <= 0:
        return '<div class="cc-pane-body cc-pane-empty"><div class="label">暂无数据</div></div>'

    # Stable per-item color: ranked by current order (which is count desc from
    # query_summary), top-N from the timeline palette, rest neutral grey so
    # the dominant categories never collide with each other.
    color_for: dict[str, str] = {}
    for idx, item in enumerate(items):
        name = _name_of(item)
        color_for[name] = (
            TIMELINE_PALETTE[idx] if idx < len(TIMELINE_PALETTE) else _COMPOSITION_OTHER_COLOR
        )

    # Conic gradient segments for the donut, in the same order as the bar list.
    segs: list[str] = []
    pos = 0.0
    for item in items:
        name = _name_of(item)
        pct = int(item["count"]) / total * 100
        end = pos + pct
        segs.append(f"{color_for[name]} {pos:.3f}% {end:.3f}%")
        pos = end
    if pos < 100:
        segs.append(f"#ece3d2 {pos:.3f}% 100%")
    donut_html = (
        f'<div class="cc-donut" style="background:conic-gradient({", ".join(segs)})">'
        f'<div class="cc-donut-hole">'
        f'<div class="cc-donut-total">{total}</div>'
        f'<div class="cc-donut-label">{esc(unit_label)}</div>'
        f"</div></div>"
    )

    # Bars: width ∝ count / max, share% to the right; matched swatch on the left.
    max_count = max((int(i["count"]) for i in items), default=1)
    bar_rows = []
    for item in items[:14]:
        name = _name_of(item)
        count = int(item["count"])
        width = max(3, count / max_count * 100)
        share = count / total * 100
        color = color_for[name]
        bar_rows.append(
            f'<div class="cc-bar">'
            f'<span class="cc-bar-sw" style="background:{color}"></span>'
            f'<span class="cc-bar-name" title="{esc(name)}">{esc(name)}</span>'
            f'<span class="cc-bar-track"><span class="cc-bar-fill" style="width:{width:.2f}%;background:{color}"></span></span>'
            f'<span class="cc-bar-count">{count}</span>'
            f'<span class="cc-bar-pct">{share:.0f}%</span>'
            f"</div>"
        )

    return (
        '<div class="cc-pane-body">'
        f'<div class="cc-donut-wrap">{donut_html}</div>'
        f'<div class="cc-bars">{"".join(bar_rows)}</div>'
        "</div>"
    )


def composition_card(today: dict[str, Any], *, mode: str = "source", unit: str = "count") -> str:
    """Multi-dimension composition card. One donut + matching colored bar
    list per dimension, only the pane matching the global `data-mode` is
    shown. The global dim-bar above the page drives `data-mode` for both
    this card and the timeline card."""
    # name_key was per-dim historically (matched the SQL column alias from
    # query_summary). With compute_breakdown all rows now use "name", but we
    # still fall back to the legacy keys so dims that haven't been recomputed
    # (composition card on pages other than /today) keep working.
    dims = [
        ("source", today.get("by_source") or [], ["name", "source"]),
        ("project", today.get("by_project") or [], ["name", "project"]),
        ("device", today.get("by_device") or [], ["name", "device_id"]),
        ("location", today.get("by_location") or [], ["name", "location_id"]),
        ("activity", today.get("by_activity") or [], ["name"]),
    ]
    unit_label = "chars" if unit == "chars" else "events"
    panes = []
    for dim_id, items, name_keys in dims:
        cls = "cc-pane show" if dim_id == mode else "cc-pane"
        panes.append(
            f'<div class="{cls}" data-for="{dim_id}">{composition_pane(items, name_keys, unit_label=unit_label)}</div>'
        )
    return (
        f'<div class="card composition-card" data-mode="{esc(mode)}">'
        f'<div class="bucket-head"><h2>分布构成</h2></div>'
        f'{"".join(panes)}'
        f"</div>"
    )


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


# Top-2 colors must be perceptually far apart, because the dominant source
# and the runner-up usually account for >70% of events and any blue/purple
# adjacency makes the timeline unreadable. Hues are spaced ~120° apart for
# the first three slots, then we fill in.
TIMELINE_PALETTE = [
    "#2f6fed",  # 1. blue
    "#f59e0b",  # 2. amber       (very far from blue)
    "#16a34a",  # 3. green
    "#ef4444",  # 4. red
    "#7b61ff",  # 5. purple
    "#14b8a6",  # 6. teal
    "#d946ef",  # 7. magenta
    "#0ea5e9",  # 8. sky
    "#84cc16",  # 9. lime
    "#f43f5e",  # 10. rose
]


HISTOGRAM_BIN_MINUTES = 20  # 24h / 20min = 72 bins
SWIMLANE_MAX_LANES = 8       # top-N categories get their own lane; rest -> "其他"


def event_timeline_card(
    events: list[dict[str, Any]], date: str,
    *, mode: str = "source", boundary_hour: int | None = None,
) -> str:
    """A 24h horizontal timeline with three switchable view styles and three
    switchable color-by dimensions. All views share one tooltip element so
    hovering any tick / histogram segment / swimlane chip shows the same rich
    info card. Clicking jumps to /events with the relevant time-window filter.

    View styles:
      - ticks      : every event is a hair-line at its exact minute (default)
      - histogram  : 20-min bins, stacked by current color-by dimension
                     (read total volume + per-category breakdown at a glance)
      - swimlane   : one row per top-N category, ticks placed in their own lane
                     (instantly see what you were doing when)

    Switching is CSS-driven via `data-style` + `data-mode` attributes on the
    card root, so we never re-render — one attribute flip toggles visibility
    and recolors. The histogram and swimlane DOM trees are rendered once per
    dimension (3 panes each); CSS attribute selectors show only the active
    combination."""

    from collections import defaultdict

    DIMS = ("source", "project", "device", "location", "activity")
    if mode not in DIMS:
        mode = "source"

    # Shifted-day boundary: events at e.g. 02:30 rendered as if at the
    # END of "yesterday's" timeline (i.e. position 22:30 if boundary=4).
    from daytrace.stats import DAY_BOUNDARY_HOUR as _DAY_B
    if boundary_hour is None:
        boundary_hour = _DAY_B
    boundary_min = (boundary_hour % 24) * 60

    def shifted(abs_min: int) -> int:
        """Map clock-minute (0..1439) to position-minute (0..1439) on the
        shifted day axis where boundary_hour is the left edge."""
        return (abs_min - boundary_min) % (24 * 60)

    # ---- 1. Place each event on the 0..1440-minute axis -----------------
    ticks: list[dict[str, Any]] = []
    counts: dict[str, dict[str, int]] = {d: {} for d in DIMS}
    for ev in events:
        start = ev.get("start") or ""
        if len(start) < 16:
            continue
        try:
            hour = int(start[11:13])
            minute = int(start[14:16])
        except ValueError:
            continue
        if not (0 <= hour < 24 and 0 <= minute < 60):
            continue
        abs_min = hour * 60 + minute
        pos_min = shifted(abs_min)
        source = (ev.get("source") or "other") or "other"
        project = (ev.get("project") or "misc") or "misc"
        device = (ev.get("device_id") or "unknown") or "unknown"
        location = (ev.get("location_id") or "unknown") or "unknown"
        activity = (ev.get("activity") or "未分类") or "未分类"
        counts["source"][source] = counts["source"].get(source, 0) + 1
        counts["project"][project] = counts["project"].get(project, 0) + 1
        counts["device"][device] = counts["device"].get(device, 0) + 1
        counts["location"][location] = counts["location"].get(location, 0) + 1
        counts["activity"][activity] = counts["activity"].get(activity, 0) + 1
        ticks.append(
            {
                "min": pos_min,                    # axis-relative for bin indexing
                "pos": pos_min / (24 * 60) * 100,  # axis-relative percentage
                "source": source,
                "project": project,
                "device": device,
                "location": location,
                "activity": activity,
                "title": ev.get("title") or "",
                "time": start[11:16],              # original wall-clock for tooltip
            }
        )

    # ---- 2. Palette per dimension (top-N, rest get a neutral grey) ------
    palettes: dict[str, dict[str, str]] = {}
    for cat, by_count in counts.items():
        top = sorted(by_count.items(), key=lambda kv: (-kv[1], kv[0]))[: len(TIMELINE_PALETTE)]
        palettes[cat] = {name: TIMELINE_PALETTE[i] for i, (name, _) in enumerate(top)}

    OTHER_COLOR = "#b8ad95"

    def color_for(cat: str, name: str) -> str:
        return palettes[cat].get(name, OTHER_COLOR)

    # CSS color rules for the **Overall** swim row — those ticks live above
    # the per-dim panes and recolor when `data-mode` changes. We emit one
    # rule per (dim, value) so a single attribute flip on the card recolors
    # all 248 overall ticks at once, with no DOM rewrite.
    color_rules: list[str] = []
    for cat in DIMS:
        for name, color in palettes[cat].items():
            safe = name.replace("\\", "\\\\").replace('"', '\\"')
            color_rules.append(
                f'.timeline-card[data-mode="{cat}"] '
                f'.tl-swim-tick-overall[data-{cat}="{safe}"]'
                f'{{background:{color};}}'
            )

    # ---- 4. Tick view ---------------------------------------------------
    # Hour grid labels are wall-clock hours, but positions follow the
    # shifted axis. For boundary=4 the labels read 04,06,08,...,02,04.
    hour_grid = "".join(
        f'<div class="tl-hour" style="left:{i / 12 * 100:.4f}%"><span>{(boundary_hour + i * 2) % 24:02d}</span></div>'
        for i in range(13)  # 13 ticks at positions 0%, 8.33%, ..., 100%
    )

    # (ticks view was removed — histogram + swimlane cover both
    # "see-every-event" and "see-density" use cases.)

    # ---- 5. Histogram view: 20-min stacked bins per dimension ----------
    # Per-dimension bins[bin_idx][name] = count
    bin_count = (24 * 60) // HISTOGRAM_BIN_MINUTES  # 72
    bins_by_cat: dict[str, list[dict[str, int]]] = {
        cat: [defaultdict(int) for _ in range(bin_count)] for cat in DIMS
    }
    for t in ticks:
        idx = t["min"] // HISTOGRAM_BIN_MINUTES
        for cat in DIMS:
            bins_by_cat[cat][idx][t[cat]] += 1

    bin_width_pct = 100.0 / bin_count
    # Global max bin total per dimension to scale heights independently.
    max_total_by_cat: dict[str, int] = {
        cat: max((sum(b.values()) for b in bins), default=0) or 1
        for cat, bins in bins_by_cat.items()
    }

    def render_hist_pane(cat: str) -> str:
        bins = bins_by_cat[cat]
        max_total = max_total_by_cat[cat]
        # Per-pane y-axis: 4 ticks (25/50/75/100% of max) with dashed grid lines.
        grid_parts: list[str] = []
        ytick_parts: list[str] = []
        for frac in (0.25, 0.5, 0.75, 1.0):
            value = max(1, round(max_total * frac))
            pct = frac * 100
            grid_parts.append(
                f'<div class="tl-grid-line" style="bottom:{pct:.0f}%"></div>'
            )
            ytick_parts.append(
                f'<span class="tl-y-tick" style="bottom:{pct:.0f}%">{value}</span>'
            )
        bin_html_parts = []
        for idx, bucket in enumerate(bins):
            total = sum(bucket.values())
            if total == 0:
                continue
            left = idx * bin_width_pct
            height = total / max_total * 100
            # Sort segments: known palette names first (palette order), then unknowns by count desc.
            ordered = sorted(
                bucket.items(),
                key=lambda kv: (
                    list(palettes[cat].keys()).index(kv[0])
                    if kv[0] in palettes[cat]
                    else len(palettes[cat]) + 1,
                    -kv[1],
                ),
            )
            # column-reverse stacks bottom-up; segments share the column height by flex weight
            seg_html = "".join(
                f'<span class="tl-seg" data-name="{esc(name)}" data-count="{c}" '
                f'style="background:{color_for(cat, name)};flex:{c}"></span>'
                for name, c in ordered
            )
            start_min = idx * HISTOGRAM_BIN_MINUTES
            end_min = start_min + HISTOGRAM_BIN_MINUTES
            window = f"{start_min // 60:02d}:{start_min % 60:02d}–{end_min // 60:02d}:{end_min % 60:02d}"
            # Encode breakdown as a compact data-attr for tooltip rendering
            breakdown_json = ";".join(
                f"{esc(n)}|{c}|{color_for(cat, n)}" for n, c in ordered
            )
            bin_html_parts.append(
                f'<div class="tl-bin" '
                f'data-bin="{idx}" data-window="{window}" data-total="{total}" '
                f'data-breakdown="{breakdown_json}" data-start="{start_min}" data-end="{end_min}" '
                f'style="left:{left:.4f}%;width:{bin_width_pct:.4f}%;height:{height:.2f}%">'
                f"{seg_html}"
                f"</div>"
            )
        # Hour grid is rendered inside the pane so its 0-100% spans the pane's
        # x-range (which is inset for the y-axis labels), not the full canvas.
        # Labels rotate to match the shifted-day boundary.
        pane_hour_grid = "".join(
            f'<div class="tl-hour" style="left:{i / 12 * 100:.4f}%"><span>{(boundary_hour + i * 2) % 24:02d}</span></div>'
            for i in range(13)
        )
        return (
            f'<div class="tl-hist-pane" data-for="{cat}">'
            f"{pane_hour_grid}"
            f'{"".join(grid_parts)}'
            f'<div class="tl-y-ticks">{"".join(ytick_parts)}</div>'
            f'{"".join(bin_html_parts)}'
            f"</div>"
        )

    histogram_html = (
        f'<div class="tl-hist">{"".join(render_hist_pane(c) for c in DIMS)}</div>'
    )

    # ---- 6. Swimlane view: top-N categories each get a row -------------
    def render_swim_pane(cat: str) -> str:
        names = list(palettes[cat].keys())[:SWIMLANE_MAX_LANES]
        has_other = any(t[cat] not in names for t in ticks)
        lanes = names + (["其他"] if has_other else [])
        lane_html_parts = []
        for lane_name in lanes:
            if lane_name == "其他":
                lane_ticks = [t for t in ticks if t[cat] not in names]
                color = OTHER_COLOR
                count = len(lane_ticks)
            else:
                lane_ticks = [t for t in ticks if t[cat] == lane_name]
                color = color_for(cat, lane_name)
                count = len(lane_ticks)
            tick_marks = "".join(
                f'<span class="tl-swim-tick" '
                f'data-time="{esc(t["time"])}" data-min="{t["min"]}" '
                f'data-source="{esc(t["source"])}" data-project="{esc(t["project"])}" '
                f'data-device="{esc(t["device"])}" data-location="{esc(t["location"])}" '
                f'data-activity="{esc(t["activity"])}" '
                f'data-title="{esc(t["title"])}" '
                f'style="left:{t["pos"]:.4f}%;background:{color}"></span>'
                for t in lane_ticks
            )
            lane_html_parts.append(
                f'<div class="tl-swim-row">'
                f'<div class="tl-swim-label" style="border-left:3px solid {color}">'
                f'<span class="tl-swim-name" title="{esc(lane_name)}">{esc(lane_name)}</span>'
                f'<span class="tl-swim-count muted">×{count}</span>'
                f"</div>"
                f'<div class="tl-swim-track">{tick_marks}</div>'
                f"</div>"
            )
        return f'<div class="tl-swim-pane" data-for="{cat}">{"".join(lane_html_parts)}</div>'

    # Overall aggregate lane: ALL events on one track, drawn above the
    # per-category panes so density is visible regardless of the active
    # dimension. Neutral color so it doesn't masquerade as one category.
    overall_ticks = "".join(
        f'<span class="tl-swim-tick tl-swim-tick-overall" '
        f'data-time="{esc(t["time"])}" data-min="{t["min"]}" '
        f'data-source="{esc(t["source"])}" data-project="{esc(t["project"])}" '
        f'data-device="{esc(t["device"])}" data-location="{esc(t["location"])}" '
        f'data-activity="{esc(t["activity"])}" '
        f'data-title="{esc(t["title"])}" '
        f'style="left:{t["pos"]:.4f}%"></span>'
        for t in ticks
    )
    overall_row = (
        '<div class="tl-swim-row tl-swim-overall">'
        '<div class="tl-swim-label tl-swim-overall-label">'
        '<span class="tl-swim-name">总览</span>'
        f'<span class="tl-swim-count muted">×{len(ticks)}</span>'
        '</div>'
        f'<div class="tl-swim-track tl-swim-overall-track">{overall_ticks}</div>'
        '</div>'
    )
    swimlane_html = (
        f'<div class="tl-swim">{overall_row}{"".join(render_swim_pane(c) for c in DIMS)}</div>'
    )

    # ---- 7. Legends, tabs, tooltip, JS ---------------------------------
    def legend_block(cat: str, palette: dict[str, str], active: bool) -> str:
        if not palette:
            items_html = '<span class="muted">无数据</span>'
        else:
            items_html = "".join(
                f'<span class="tl-legend-item"><span class="tl-swatch" style="background:{color}"></span>{esc(name)} <span class="muted">×{counts[cat][name]}</span></span>'
                for name, color in palette.items()
            )
        cls = "tl-legend show" if active else "tl-legend"
        return f'<div class="{cls}" data-for="{cat}">{items_html}</div>'

    legends_html = "".join(
        legend_block(c, palettes[c], active=(c == mode)) for c in DIMS
    )

    style_tabs_html = (
        '<div class="tl-style-tabs" role="tablist" aria-label="视图样式">'
        '<button type="button" class="tl-style-tab active" data-style="swimlane">泳道</button>'
        '<button type="button" class="tl-style-tab" data-style="histogram">直方图</button>'
        "</div>"
    )

    style_html = "<style>" + "".join(color_rules) + "</style>" if color_rules else ""

    tooltip_html = '<div class="tl-tooltip" hidden></div>'

    # The handler does three things: tab switching (style+mode), hover tooltip
    # rendering, and click → /events filter jump. All scoped to this card via
    # the wrapping closure on document.currentScript.
    js_html = (
        "<script>(function(){"
        "var s=document.currentScript;var card=s&&s.closest('.timeline-card');if(!card)return;"
        "var date=card.getAttribute('data-date');"
        "var tip=card.querySelector('.tl-tooltip');"
        # Style tab handler (mode tabs were removed — global dim-bar drives mode)
        "card.querySelectorAll('.tl-style-tab').forEach(function(btn){btn.addEventListener('click',function(){"
        "card.setAttribute('data-style',btn.dataset.style);"
        "card.querySelectorAll('.tl-style-tab').forEach(function(b){b.classList.toggle('active',b===btn);});"
        "});});"
        # tooltip helpers
        "function chip(label,val,color){if(!val)return '';var sw=color?('<span class=\"tl-tip-sw\" style=\"background:'+color+'\"></span>'):'';return '<span class=\"tl-tip-chip\">'+sw+'<b>'+label+'</b> '+val+'</span>';}"
        "function esc(s){return String(s==null?'':s).replace(/[&<>\"]/g,function(c){return({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'})[c];});}"
        "function showTip(html,ev){tip.innerHTML=html;tip.hidden=false;var r=card.getBoundingClientRect();var x=ev.clientX-r.left+12;var y=ev.clientY-r.top+12;var w=tip.offsetWidth;if(x+w>card.clientWidth-8)x=card.clientWidth-w-8;tip.style.left=x+'px';tip.style.top=y+'px';}"
        "function hideTip(){tip.hidden=true;}"
        # Per-tick / per-swim-tick tooltip
        "function bindEvent(el){el.addEventListener('mousemove',function(ev){var html='<div class=\"tl-tip-time\">'+esc(el.dataset.time)+'</div><div class=\"tl-tip-title\">'+esc(el.dataset.title||'(无标题)')+'</div>'+chip('活动',esc(el.dataset.activity),null)+chip('来源',esc(el.dataset.source),null)+chip('项目',esc(el.dataset.project),null)+chip('设备',esc(el.dataset.device),null)+chip('位置',esc(el.dataset.location),null);showTip(html,ev);});el.addEventListener('mouseleave',hideTip);el.addEventListener('click',function(){if(!date)return;var t=el.dataset.time||'00:00';location.href='/events?start_from='+encodeURIComponent(date)+'&start_to='+encodeURIComponent(date)+'&search='+encodeURIComponent(el.dataset.title||'');});}"
        "card.querySelectorAll('.tl-swim-tick').forEach(bindEvent);"
        # Per-bin tooltip + click filters to the 20-min window
        "card.querySelectorAll('.tl-bin').forEach(function(bin){bin.addEventListener('mousemove',function(ev){var bd=(bin.dataset.breakdown||'').split(';').filter(Boolean);var rows=bd.map(function(p){var x=p.split('|');return chip(esc(x[0]),'×'+x[1],x[2]);}).join('');var html='<div class=\"tl-tip-time\">'+esc(bin.dataset.window)+' · 共 '+bin.dataset.total+' 条</div>'+rows;showTip(html,ev);});bin.addEventListener('mouseleave',hideTip);bin.addEventListener('click',function(){if(!date)return;var sm=parseInt(bin.dataset.start,10),em=parseInt(bin.dataset.end,10);function fmt(m){return ('0'+(m/60|0)).slice(-2)+':'+('0'+(m%60)).slice(-2)+':00';}location.href='/events?start_from='+encodeURIComponent(date+'T'+fmt(sm))+'&start_to='+encodeURIComponent(date+'T'+fmt(em-1));});});"
        "})();</script>"
    )

    empty_hint = (
        '<div class="tl-empty">当天暂无事件</div>'
        if not ticks
        else f'<div class="tl-meta">共 {len(ticks)} 条事件 · 切换 <b>视图样式</b> 看不同呈现；hover 看详情，点击跳到对应时间窗的数据库</div>'
    )

    return (
        f'<div class="card wide-card timeline-card" data-mode="{esc(mode)}" data-style="swimlane" data-date="{esc(date or "")}">'
        f'<div class="bucket-head"><h2>一天时间轴 · {esc(date or "")}</h2>'
        f'<div class="tl-tab-group">{style_tabs_html}</div>'
        f"</div>"
        f"{style_html}"
        f'<div class="tl-axis-wrap">'
        f"  {histogram_html}"
        f"  {swimlane_html}"
        f"  {tooltip_html}"
        f"</div>"
        f'<div class="tl-axis-bottom">'
        f'<span>{boundary_hour:02d}:00</span>'
        f'<span>{(boundary_hour + 6) % 24:02d}:00</span>'
        f'<span>{(boundary_hour + 12) % 24:02d}:00</span>'
        f'<span>{(boundary_hour + 18) % 24:02d}:00</span>'
        f'<span>{boundary_hour:02d}:00</span>'
        f'</div>'
        f"{empty_hint}"
        f"{legends_html}"
        f"{js_html}"
        f"</div>"
    )


def mini_table(items: list[dict[str, Any]], name_key: str, total: int, label: str) -> str:
    rows = []
    for item in items[:8]:
        name = item.get(name_key) or "misc"
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
  <li>misc/待检查事件 <strong>{low}</strong> 条，占当天 <strong>{pct_text(low,total)}</strong>；{confidence_note}。</li>
  <li>下方时间轴可按 来源 / 项目 / 设备 切换上色，看一天的真实分布。</li>
</ul>
"""


def date_filter(action: str, date: str | None, extra: str = "") -> str:
    return f"""<div class="filters card"><form method="get" action="{action}" style="display:flex;gap:10px;flex-wrap:wrap;align-items:center"><label>日期 <input name="date" value="{esc(date or '')}" placeholder="YYYY-MM-DD"></label>{extra}<button type="submit">查看</button><a href="{action}">全部日期</a></form></div>"""


DIMENSIONS = [
    ("source", "来源"),
    ("project", "项目"),
    ("device", "设备"),
    ("activity", "活动"),
]

# Global unit toggle: "条目" weights each event as 1, "字数" weights each
# event by len(title)+len(summary). Affects donut shares and project-card
# share bars. Persisted via URL ?unit=chars.
UNITS = [
    ("count", "条目"),
    ("chars", "字数"),
]


def _event_weight(ev: dict, unit: str) -> int:
    if unit == "chars":
        return int(ev.get("char_count") or 0)
    return 1


def compute_breakdown(events: list[dict], field: str, unit: str = "count") -> list[dict]:
    """Group events by `field`, return [{name, count, share}] desc by count.

    `count` here is the unit-weighted aggregate (events or chars). `share`
    is normalized over the unit total."""
    from collections import Counter
    bag: Counter = Counter()
    for ev in events:
        name = ev.get(field)
        if not name:
            # field-specific fallback
            if field == "project":
                name = ev.get("project_guess") or "misc"
            elif field == "device_id":
                name = "unknown"
            elif field == "location_id":
                name = "unknown"
            elif field == "activity":
                name = "未分类"
            else:
                name = "other"
        bag[str(name)] += _event_weight(ev, unit)
    total = sum(bag.values()) or 1
    return [{"name": n, "count": c, "share": round(c / total, 4)} for n, c in bag.most_common()]


def _mode_link(path: str, params: dict[str, str | None]) -> str:
    """Build a URL preserving only the truthy params (drops empty values)."""
    qs = urlencode({k: v for k, v in params.items() if v})
    return f"{path}?{qs}" if qs else path


def today_page(db_path: Path, date: str | None, mode: str | None = None, unit: str | None = None):
    valid_modes = {dim_id for dim_id, _ in DIMENSIONS}
    if mode not in valid_modes:
        mode = "source"
    valid_units = {u for u, _ in UNITS}
    if unit not in valid_units:
        unit = "count"
    con = connect(db_path)
    all_dates = available_dates(con)
    if not date:
        # Default to shifted-yesterday rather than "most recent with any data" —
        # today's shifted-day is usually still in flight at the time the user
        # opens the dashboard, so yesterday is the page they actually want.
        from datetime import datetime as _dt, date as _dt_date, timedelta as _td
        from daytrace import stats as _stats
        now = _dt.now()
        ref = now.date() if now.hour >= _stats.DAY_BOUNDARY_HOUR else (now.date() - _td(days=1))
        date = (ref - _td(days=1)).isoformat()
        # Fall back to "most recent with data" if that exact day has nothing
        # (e.g. backfill gap, machine off all day).
        if date not in all_dates and all_dates:
            date = all_dates[0]
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
    hours = hourly_distribution(con, date) if date else []
    # Shifted-day window: 04:00–04:00 by default. Affects timeline + project
    # cards + composition shares. Cached day_report rows use the same window.
    from daytrace.db import events_for_shifted_day
    day_events = events_for_shifted_day(con, date, limit=2000) if date else []
    total = len(day_events)  # subtitle uses shifted-day total, not calendar-day
    # Enrich events with activity labels from the side table (filled by AI).
    if date:
        from daytrace.db import load_activity_labels_for_date
        labels = load_activity_labels_for_date(con, date)
        for ev in day_events:
            ev["activity"] = labels.get(ev["id"], "未分类") if labels else "未分类"
        # Recompute every dim breakdown with the active unit (count or chars)
        # so the composition donut + per-project shares reflect what the user
        # picked in the dim-bar.
        today["by_source"]   = compute_breakdown(day_events, "source",      unit)
        today["by_project"]  = compute_breakdown(day_events, "project",     unit)
        today["by_device"]   = compute_breakdown(day_events, "device_id",   unit)
        today["by_location"] = compute_breakdown(day_events, "location_id", unit)
        today["by_activity"] = compute_breakdown(day_events, "activity",    unit)
    timeline_html = event_timeline_card(day_events, date or "", mode=mode)
    composition_html = composition_card(today, mode=mode, unit=unit)

    # Lazy-regenerate stats channels if the day_report row is missing or
    # stale. AI channels stay untouched (those are expensive — user triggers
    # them via the backfill script / API explicitly).
    day_report_row = None
    day_channels: dict[str, str | None] = {}
    if date:
        from daytrace.daily_report import regenerate_day_from_db
        if not con.execute(
            "SELECT 1 FROM day_report WHERE date = ?", (date,)
        ).fetchone():
            try:
                regenerate_day_from_db(con, date, include_ai=False)
            except Exception:  # never let report-render fail because of regen
                pass
        day_report_row = con.execute(
            "SELECT total_events, active_minutes, events_hash FROM day_report WHERE date = ?",
            (date,),
        ).fetchone()
        if day_report_row:
            for r in con.execute(
                "SELECT channel, value_json FROM day_channel WHERE date = ?",
                (date,),
            ).fetchall():
                day_channels[r["channel"]] = r["value_json"]

    # Build the rich daily-report body (AI overview + continuity + facts).
    ai_overview = _safe_load_json(day_channels.get("ai_overview"))
    ai_continuity = _safe_load_json(day_channels.get("ai_continuity_day"))
    if day_report_row is None:
        rich_daily_body = (
            f"{daily_report_text(today, hours)}"
            "<div class='muted small' style='margin-top:8px'>(还没有 day_report 缓存；"
            "运行 backfill 后可看到 AI 速读)</div>"
        )
    else:
        rich_daily_body = (
            _render_stats_strip_compact(dict(day_report_row), day_channels)
            + _render_ai_overview_block(ai_overview)
            + _render_continuity_block(ai_continuity)
            + _render_facts_block(date or "", day_channels)
        )

    dates_desc = all_dates
    prev_link = next_link = ""
    if date in dates_desc:
        idx = dates_desc.index(date)
        if idx + 1 < len(dates_desc):
            prev_day = dates_desc[idx + 1]
            href = _mode_link("/today", {"date": prev_day, "mode": mode if mode != "source" else None})
            prev_link = f'<a href="{esc(href)}">← 前一天 {esc(prev_day)}</a>'
        if idx - 1 >= 0:
            next_day = dates_desc[idx - 1]
            href = _mode_link("/today", {"date": next_day, "mode": mode if mode != "source" else None})
            next_link = f'<a href="{esc(href)}">后一天 {esc(next_day)} →</a>'
    day_nav_inner = (
        f"{prev_link}{next_link}"
        f'<a href="/events?start_from={esc(date)}&start_to={esc(date)}">打开当天数据库</a>'
        if date else ""
    )

    # Global control bar — day-nav on the left, unit toggle (条目/字数) and
    # dimension selector on the right. Both selectors are cross-card concerns
    # that affect every visualization on the page.
    dim_tabs = "".join(
        f'<button type="button" class="dim-tab{" active" if dim_id == mode else ""}" data-mode="{dim_id}">{label}</button>'
        for dim_id, label in DIMENSIONS
    )
    unit_tabs = "".join(
        f'<button type="button" class="unit-tab{" active" if u_id == unit else ""}" data-unit="{u_id}">{label}</button>'
        for u_id, label in UNITS
    )
    dim_bar = (
        f'<section class="dim-bar">'
        f'<div class="day-nav">{day_nav_inner}</div>'
        f'<div class="dim-bar-right">'
        f'<div class="unit-tabs" title="按条目数或字数统计">{unit_tabs}</div>'
        f'<div class="dim-tabs">{dim_tabs}</div>'
        f'</div>'
        f"</section>"
    )
    dim_sync_js = (
        "<script>(function(){"
        "function apply(mode){"
        "document.querySelectorAll('.dim-tab').forEach(function(b){b.classList.toggle('active',b.dataset.mode===mode);});"
        "document.querySelectorAll('.composition-card,.timeline-card').forEach(function(c){c.setAttribute('data-mode',mode);});"
        "document.querySelectorAll('.composition-card .cc-pane').forEach(function(p){p.classList.toggle('show',p.dataset.for===mode);});"
        "document.querySelectorAll('.timeline-card .tl-legend').forEach(function(l){l.classList.toggle('show',l.dataset.for===mode);});"
        "var url=new URL(location.href);if(mode==='source'){url.searchParams.delete('mode');}else{url.searchParams.set('mode',mode);}"
        "history.replaceState({},'',url);"
        # Rewrite same-page links (day-nav etc.) so any in-page navigation
        # keeps the mode without us threading it everywhere server-side.
        "document.querySelectorAll('a[href^=\"/today\"]').forEach(function(a){try{var u=new URL(a.href,location.origin);if(mode==='source'){u.searchParams.delete('mode');}else{u.searchParams.set('mode',mode);}a.href=u.pathname+(u.search||'');}catch(e){}});"
        "}"
        "document.querySelectorAll('.dim-tab').forEach(function(btn){btn.addEventListener('click',function(){apply(btn.dataset.mode);});});"
        # Unit toggle reloads — the breakdowns are recomputed server-side.
        # Save scrollY to sessionStorage before reload, restore on next load.
        # This keeps users in the project card they were inspecting.
        "var SCROLL_KEY='daytrace.today.scrollY';"
        "var saved=sessionStorage.getItem(SCROLL_KEY);"
        "if(saved!==null){window.scrollTo(0,parseInt(saved,10)||0);sessionStorage.removeItem(SCROLL_KEY);}"
        "document.querySelectorAll('.unit-tab').forEach(function(btn){btn.addEventListener('click',function(){"
        "if(btn.classList.contains('active'))return;"
        "sessionStorage.setItem(SCROLL_KEY,String(window.scrollY));"
        "var u=btn.dataset.unit;var url=new URL(location.href);"
        "if(u==='count'){url.searchParams.delete('unit');}else{url.searchParams.set('unit',u);}"
        "location.href=url.toString();"
        "});});"
        "})();</script>"
    )

    project_cards_html = (
        project_cards_section(con, date, day_events=day_events, unit=unit, top_n_open=3)
        if date else ""
    )
    highlights_concerns_html = _render_highlights_concerns_card(ai_overview)

    content = f"""
{dim_bar}
<section class="report-grid">
  <div class="card daily-report"><div class="bucket-head"><h2>每日 Report · {esc(date or '无日期')}</h2><span class="tag source">Daily</span></div>{rich_daily_body}</div>
  <div class="right-column">
    {composition_html}
    {highlights_concerns_html}
  </div>
</section>
<section class="timeline-section">
  {timeline_html}
</section>
{project_cards_html}
{dim_sync_js}
"""
    # Calendar control: thread `mode` so picking a date on the header keeps it.
    cal_hidden = {"mode": mode} if mode != "source" else {}
    date_control = calendar_control('/today', date, all_dates, hidden=cal_hidden)
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
  <td class="db-cell">{esc(e.get('activity') or '未分类')}</td>
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
            "activity": filters.get("activity"),
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
        "activity": filters.get("activity"),
        "location_id": filters.get("location_id"),
        "project": filters.get("project"),
        "search": filters.get("search"),
        "limit": filters.get("limit"),
        "order": filters.get("order"),
        "start_from": format_date_input(filters.get("start_from")),
        "start_to": format_date_input(filters.get("start_to")),
    }
    time_filter = f"""<div class=\"time-range\">{date_filter_calendar_control('/events', 'start_from', filters.get('start_from'), date_counts, date_hidden, 'Start', 'All dates')}{date_filter_calendar_control('/events', 'start_to', filters.get('start_to') or filters.get('start_from'), date_counts, date_hidden, 'End', 'Same day' if filters.get('start_from') else 'No end', min_date=filters.get('start_from'), picker_class='end-date')}</div>"""
    has_active_filter = any(
        filters.get(k)
        for k in ("source", "activity", "location_id", "project", "search", "start_from", "start_to")
    )
    clear_link = (
        '<a class="clear-filters" href="/events" title="清空所有筛选条件">✕ 清空筛选</a>'
        if has_active_filter
        else '<span class="clear-filters muted" title="当前未设置任何筛选">✕ 清空筛选</span>'
    )
    return f"""
<form method="get" action="/events">
  {hidden_order}
  <div class="table-wrap"><table><colgroup><col class="col-time"><col class="col-source"><col class="col-activity"><col class="col-location"><col class="col-project"><col class="col-title"></colgroup>
  <thead><tr>
    <th><div class="th-title"><a class="sort-link" href="{esc(sort_href)}">Time {sort_arrow}</a><span class="clear-filters-wrap">{clear_link}</span></div>{time_filter}</th>
    <th><div class="th-title"><span>Source</span></div>{select_control('source', options['source'], filters.get('source'))}</th>
    <th><div class="th-title"><span>Activity</span></div>{select_control('activity', options['activity'], filters.get('activity'))}</th>
    <th><div class="th-title"><span>Location</span></div>{select_control('location_id', options['location_id'], filters.get('location_id'))}</th>
    <th><div class="th-title"><span>Project</span></div>{select_control('project', options['project'], filters.get('project'))}</th>
    <th><div class="th-title"><span>Title / Content</span><label class="header-filter"><span>Rows</span>{event_limit_control(filters.get('limit'))}</label></div><input name="search" value="{esc(filters.get('search') or '')}" placeholder="Search title/content"></th>
  </tr></thead><tbody>{''.join(rows) or '<tr><td colspan="6">暂无事件</td></tr>'}</tbody></table></div>
</form>"""

TABLE_TABS = [
    ("events",      "原始事件"),
    ("day",         "日报告 (day_report)"),
    ("day_project", "项目日报告 (day_project_report)"),
]


def table_switcher_html(active: str, qs: dict[str, list[str]]) -> str:
    """Pill row at the top of the /events page for switching which table to view.

    Preserves the `date` query param so a user filtering by date keeps that
    filter when they switch tables. Other event-specific filters (source,
    project, search, etc.) are intentionally dropped — they don't translate."""
    date = (qs.get("date", [None])[0] or "")
    pills = []
    for table_id, label in TABLE_TABS:
        params = {"table": table_id}
        if date:
            params["date"] = date
        href = "/events" + ("?" + urlencode(params) if params else "")
        cls = "table-tab" + (" active" if table_id == active else "")
        pills.append(f'<a class="{cls}" href="{esc(href)}">{esc(label)}</a>')
    return f'<section class="table-switcher">{"".join(pills)}</section>'


def _format_channels_cell(json_str: str | None) -> str:
    """Render a channel cell: a <details> with raw JSON inside, summary
    showing key short fact (e.g., headline for AI) when available."""
    if json_str is None or json_str == "":
        return '<span class="muted">∅</span>'
    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return f'<span class="muted">非 JSON</span><pre>{esc(json_str[:120])}</pre>'
    if data is None:
        return '<span class="muted">null</span>'
    if isinstance(data, dict):
        # Pick a one-line teaser
        teaser = (
            data.get("headline")
            or data.get("summary")
            or (f"total={data.get('total')}" if "total" in data else "")
            or (f"count={data.get('count')}" if "count" in data else "")
            or ""
        )
    elif isinstance(data, list):
        teaser = f"{len(data)} 项"
    else:
        teaser = str(data)[:80]
    pretty = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    return (
        f'<details class="channel-cell"><summary>{esc(teaser)}</summary>'
        f'<pre>{esc(pretty)}</pre></details>'
    )


def _safe_load_json(json_str: str | None):
    if not json_str:
        return None
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return None


def _format_duration_short(minutes: int) -> str:
    if minutes is None:
        return "—"
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m}m" if m else f"{h}h"


def _momentum_chip(momentum: str | None) -> str:
    icons = {
        "rising": "↗", "steady": "→", "dropping": "↘",
        "new": "✨", "paused": "⏸", "blocked": "⛔",
    }
    if not momentum:
        return ""
    icon = icons.get(momentum, "•")
    return f'<span class="momentum-chip momentum-{esc(momentum)}">{icon} {esc(momentum)}</span>'


def _status_chip(status: str | None) -> str:
    if not status:
        return ""
    return f'<span class="status-chip status-{esc(status)}">{esc(status)}</span>'


# ----- day_report: card-per-day human view -----------------------------

def _render_ai_overview_block(overview: dict | None) -> str:
    """Headline + narrative only. Highlights / Concerns are rendered
    separately so they can live in the right column next to the donut."""
    if not overview:
        return '<div class="dr-headline muted">📰 (今天的 AI 速读还没有生成)</div>'
    headline = overview.get("headline") or "(尚未生成 AI 速读)"
    narrative = overview.get("narrative") or ""
    return (
        f'<div class="dr-headline">📰 {esc(headline)}</div>'
        f'<p class="dr-narrative">{esc(narrative)}</p>'
    )


def _render_highlights_concerns_card(overview: dict | None) -> str:
    """Two-column card (✨ Highlights / ⚠️ Concerns) for the right column.
    Returns empty string if there's nothing to show — caller can omit it."""
    if not overview:
        return ""
    highlights = overview.get("highlights") or []
    concerns = overview.get("concerns") or []
    if not highlights and not concerns:
        return ""
    hl = "".join(f"<li>{esc(h)}</li>" for h in highlights)
    cn = "".join(f"<li>{esc(c)}</li>" for c in concerns)
    sections = []
    if hl:
        sections.append(f'<div class="dr-section"><h4>✨ Highlights</h4><ul class="dr-bullets dr-highlights">{hl}</ul></div>')
    if cn:
        sections.append(f'<div class="dr-section"><h4>⚠️ Concerns</h4><ul class="dr-bullets dr-concerns">{cn}</ul></div>')
    return (
        '<div class="card highlights-card">'
        f'<div class="dr-grid">{"".join(sections)}</div>'
        '</div>'
    )


def _render_continuity_block(continuity: dict | None) -> str:
    if not continuity:
        return ""
    relation = continuity.get("relation_to_yesterday") or ""
    momentum = continuity.get("momentum") or ""
    changes = continuity.get("notable_changes") or []
    changes_html = ""
    if changes:
        changes_html = (
            "<ul class='dr-bullets dr-changes'>"
            + "".join(f"<li>{esc(c)}</li>" for c in changes)
            + "</ul>"
        )
    return (
        '<div class="dr-continuity">'
        f'<span class="dr-cont-label">vs 昨天</span> '
        f'{_momentum_chip(momentum)} '
        f'<span class="dr-cont-text">{esc(relation)}</span>'
        f"{changes_html}"
        "</div>"
    )


def _render_facts_block(date_val: str, channels: dict[str, str | None]) -> str:
    longest = _safe_load_json(channels.get("longest_focus_block"))
    peaks = _safe_load_json(channels.get("peak_windows")) or []
    quality = _safe_load_json(channels.get("quality")) or {}
    facts: list[str] = []
    if longest:
        facts.append(
            f"⏱ 最长专注 <b>{esc(longest.get('start','?'))}–{esc(longest.get('end','?'))}</b> "
            f"({_format_duration_short(longest.get('duration_min', 0))}, "
            f"{esc(longest.get('dominant_source','?'))} / "
            f"<a href='/today?date={esc(date_val)}&mode=project'>{esc(longest.get('dominant_project','?'))}</a>)"
        )
    if peaks:
        peak_str = " · ".join(f"{esc(p['label'])}={p['count']}" for p in peaks[:3])
        facts.append(f"📈 峰值 {peak_str}")
    if quality:
        bits = []
        if quality.get("sensitive"):
            bits.append(f"sensitive {quality['sensitive']}")
        if quality.get("missing_project"):
            bits.append(f"missing project {quality['missing_project']}")
        if bits:
            facts.append("🔍 " + " · ".join(bits))
    if not facts:
        return ""
    return '<div class="dr-facts">' + "".join(f"<div class='dr-fact'>{f}</div>" for f in facts) + "</div>"


def _render_stats_strip_compact(header: dict, channels: dict[str, str | None]) -> str:
    """Compact horizontal stats strip for the home page daily-report card."""
    time_span = _safe_load_json(channels.get("time_span")) or {}
    switches = _safe_load_json(channels.get("context_switches")) or {}
    first, last = time_span.get("first") or "?", time_span.get("last") or "?"
    chips = [
        (str(header["total_events"]), "events"),
        (_format_duration_short(header["active_minutes"]), "active"),
        (f"{first}–{last}", "span"),
        (str(switches.get("count", 0)), "switches"),
    ]
    return (
        '<div class="dr-stats-compact">'
        + "".join(
            f'<div class="dr-stat"><span class="dr-stat-num">{esc(v)}</span><span class="dr-stat-lbl">{esc(lbl)}</span></div>'
            for v, lbl in chips
        )
        + '</div>'
    )


def _render_stats_strip(header: dict, channels: dict[str, str | None]) -> str:
    time_span = _safe_load_json(channels.get("time_span")) or {}
    switches = _safe_load_json(channels.get("context_switches")) or {}
    first, last = time_span.get("first") or "?", time_span.get("last") or "?"
    chips = [
        ("dr-stat-num", str(header["total_events"]), "events"),
        ("dr-stat-num", _format_duration_short(header["active_minutes"]), "active"),
        ("dr-stat-num", f"{first}–{last}", "span"),
        ("dr-stat-num", str(switches.get("count", 0)), "switches"),
    ]
    return "".join(
        f'<div class="dr-stat"><span class="{c}">{esc(v)}</span><span class="dr-stat-lbl">{esc(lbl)}</span></div>'
        for c, v, lbl in chips
    )


def _render_raw_channels_block(channels: dict[str, str | None]) -> str:
    rows = []
    for ch, val in sorted(channels.items()):
        if val is None:
            continue
        try:
            pretty = json.dumps(json.loads(val), ensure_ascii=False, indent=2, sort_keys=True)
        except (json.JSONDecodeError, TypeError):
            pretty = str(val)
        rows.append(
            f"<details class='dr-raw-row'><summary>{esc(ch)}</summary>"
            f"<pre>{esc(pretty)}</pre></details>"
        )
    return (
        "<details class='dr-raw-wrap'><summary>原始 channel JSON</summary>"
        + "".join(rows)
        + "</details>"
    )


def _format_chars_short(n: int) -> str:
    if n < 1000:
        return f"{n} 字"
    if n < 10000:
        return f"{n / 1000:.1f}K 字"
    return f"{n // 1000}K 字"


def _render_project_card(date_val: str, row: dict, channels: dict[str, str | None],
                          *, unit: str = "count", open_by_default: bool) -> str:
    """One project card for the home page. Collapsed-by-default shows a single
    line (name + share + status + summary); expanded shows what_was_done,
    next steps, continuity, top titles, mix."""
    summary = _safe_load_json(channels.get("ai_summary")) or {}
    continuity = _safe_load_json(channels.get("ai_continuity")) or {}
    top_titles = _safe_load_json(channels.get("top_titles")) or []
    source_mix = _safe_load_json(channels.get("source_mix")) or {}
    time_span = _safe_load_json(channels.get("time_span")) or {}

    project = row["project"]
    event_count = row["event_count"]
    active_min = row["active_minutes"]
    share_pct = row["share"] * 100
    chars = int(row.get("chars") or 0)
    summary_text = summary.get("summary") if isinstance(summary, dict) else ""

    # Fact line shows both quantities; the active unit just controls the bar.
    fact_line = (
        f"{event_count} events · {_format_duration_short(active_min)}"
        + (f" · {_format_chars_short(chars)}" if chars else "")
    )

    head_html = (
        '<summary class="pc-summary-row">'
        f'<span class="pc-project-name" title="{esc(project)}">{esc(project)}</span>'
        f'<span class="pc-share-pct">{share_pct:.0f}%</span>'
        f'<div class="pc-share-bar"><div class="pc-share-fill" style="width:{share_pct:.1f}%"></div></div>'
        f'<span class="pc-events">{fact_line}</span>'
        '<span class="pc-chevron">◂</span>'
        '</summary>'
    )

    # Expanded body
    body_parts = []
    if summary_text:
        body_parts.append(f'<div class="pc-summary-text">{esc(summary_text)}</div>')
    if isinstance(summary, dict):
        what_was_done = summary.get("what_was_done") or []
        next_steps = summary.get("next_steps") or []
        if what_was_done:
            body_parts.append(
                "<div class='pc-section-label'>✅ 做了什么</div>"
                "<ul class='pc-bullets'>"
                + "".join(f"<li>{esc(w)}</li>" for w in what_was_done)
                + "</ul>"
            )
        if next_steps:
            body_parts.append(
                "<div class='pc-section-label'>➡ 后续</div>"
                "<ul class='pc-bullets pc-next'>"
                + "".join(f"<li>{esc(n)}</li>" for n in next_steps)
                + "</ul>"
            )
    if isinstance(continuity, dict) and continuity:
        body_parts.append(
            '<div class="pc-continuity">'
            f'<span class="pc-cont-label">vs 上次活跃</span> '
            f'{_momentum_chip(continuity.get("momentum"))} '
            f'<span class="pc-cont-text">{esc(continuity.get("relation_to_previous") or "")}</span>'
            '</div>'
        )
    if top_titles:
        body_parts.append(
            "<div class='pc-section-label'>🔖 代表事件</div>"
            "<ul class='pc-titles'>"
            + "".join(
                f"<li><span class='tt-time'>{esc(t.get('time','--:--'))}</span> {esc(t.get('title',''))}</li>"
                for t in top_titles[:5]
            )
            + "</ul>"
        )
    meta_bits = []
    if time_span:
        meta_bits.append(f"⏱ {esc(time_span.get('first','?'))}–{esc(time_span.get('last','?'))}")
    if source_mix:
        mix_str = " · ".join(f"{esc(k)}({v})" for k, v in sorted(source_mix.items(), key=lambda kv: -kv[1])[:5])
        meta_bits.append(f"🎛 {mix_str}")
    if meta_bits:
        body_parts.append(f'<div class="pc-meta muted">{" &nbsp; ".join(meta_bits)}</div>')
    # Action links
    body_parts.append(
        '<div class="pc-actions">'
        f'<a href="/events?project={esc(project)}&start_from={esc(date_val)}&start_to={esc(date_val)}">看该项目的原始事件 →</a>'
        '</div>'
    )

    body_html = f'<div class="pc-body">{"".join(body_parts)}</div>'
    open_attr = " open" if open_by_default else ""
    return f'<details class="project-card"{open_attr}>{head_html}{body_html}</details>'


def project_cards_section(
    con, date: str, *, day_events: list[dict] | None = None,
    unit: str = "count", top_n_open: int = 3,
) -> str:
    """Render the per-project cards for one date.

    When `unit='chars'`, the share bars and ordering reweight by total char
    count per project (sum of title+summary lengths) instead of event count.
    Each card still shows raw event_count + active_minutes as facts; the
    share % is the unit-weighted one."""
    rows = con.execute(
        "SELECT project, event_count, active_minutes, share, updated_at"
        " FROM day_project_report WHERE date = ?",
        (date,),
    ).fetchall()
    if not rows:
        return ""
    rows = [dict(r) for r in rows]

    # If chars unit, recompute char_count + share per project from day_events.
    if unit == "chars" and day_events:
        from collections import Counter
        char_per_project: Counter = Counter()
        for ev in day_events:
            p = (ev.get("project") or ev.get("project_guess") or "misc")
            char_per_project[p] += int(ev.get("char_count") or 0)
        total_chars = sum(char_per_project.values()) or 1
        for r in rows:
            r["chars"] = char_per_project.get(r["project"], 0)
            r["share"] = r["chars"] / total_chars
    rows.sort(key=lambda r: -(r.get("chars", r.get("event_count", 0)) if unit == "chars" else r.get("event_count", 0)))

    cards = []
    for i, prow in enumerate(rows):
        channel_rows = con.execute(
            "SELECT channel, value_json FROM day_project_channel"
            " WHERE date = ? AND project = ?",
            (date, prow["project"]),
        ).fetchall()
        channels = {r["channel"]: r["value_json"] for r in channel_rows}
        cards.append(_render_project_card(date, prow, channels, unit=unit, open_by_default=(i < top_n_open)))
    sub = "按字数排序" if unit == "chars" else "按条目排序"
    return (
        '<section class="project-cards-section">'
        f'<h2 class="section-title">📁 项目分项 · {len(rows)} 个 <span class="muted small">· {sub}</span></h2>'
        f'<div class="project-cards">{"".join(cards)}</div>'
        '</section>'
    )


def _render_day_card(date_val: str, header: dict, channels: dict[str, str | None]) -> str:
    """Full day card for the /events?table=day database view."""
    overview = _safe_load_json(channels.get("ai_overview"))
    continuity = _safe_load_json(channels.get("ai_continuity_day"))
    return f"""
<div class="day-report-card">
  <div class="dr-head">
    <div class="dr-date">{esc(date_val)}</div>
    <div class="dr-stats">{_render_stats_strip(header, channels)}</div>
    <div class="dr-actions">
      <a href="/today?date={esc(date_val)}">在报告页打开 →</a>
      <a href="/events?start_from={esc(date_val)}&start_to={esc(date_val)}">看原始事件 →</a>
    </div>
  </div>
  {_render_ai_overview_block(overview)}
  {_render_continuity_block(continuity)}
  {_render_facts_block(date_val, channels)}
  {_render_raw_channels_block(channels)}
</div>
"""


def day_report_table_page(db_path: Path, qs: dict[str, list[str]]) -> str:
    """Human-friendly view: one card per day with AI summary + key stats up
    top, raw channel JSON folded away at the bottom."""
    con = connect(db_path)
    init_db(con)
    selected_date = qs.get("date", [None])[0] or None
    where_parts, params = [], []
    if selected_date:
        where_parts.append("date = ?")
        params.append(selected_date)
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    rows = con.execute(
        f"""
        SELECT date, events_hash, total_events, active_minutes, updated_at
        FROM day_report {where}
        ORDER BY date DESC LIMIT 60
        """,
        params,
    ).fetchall()

    # Pre-load all channels per date
    cards = []
    total_cost = 0.0
    total_tokens = 0
    for row in rows:
        date_val = row["date"]
        channel_rows = con.execute(
            "SELECT channel, value_json, cost_usd, tokens_in, tokens_out"
            " FROM day_channel WHERE date = ?",
            (date_val,),
        ).fetchall()
        channels = {r["channel"]: r["value_json"] for r in channel_rows}
        day_cost = sum((r["cost_usd"] or 0) for r in channel_rows)
        day_tokens = sum((r["tokens_in"] or 0) + (r["tokens_out"] or 0) for r in channel_rows)
        total_cost += day_cost
        total_tokens += day_tokens
        cards.append(_render_day_card(date_val, dict(row), channels))

    cards_html = "\n".join(cards) or '<div class="card label">暂无 day_report 行</div>'
    # Calendar-style date picker (same widget as the home page header).
    all_dates = [
        r["date"] for r in
        con.execute("SELECT date FROM day_report ORDER BY date DESC").fetchall()
    ]
    cal_widget = calendar_control(
        "/events", selected_date, all_dates,
        hidden={"table": "day"}, allow_all=True, label_text="日期",
    )
    filter_strip = (
        '<div class="dr-filter-strip">'
        f'{cal_widget}'
        f'<span class="muted small">共 {len(rows)} 天 · AI 总花费 ${total_cost:.4f} · {total_tokens} tokens</span>'
        '</div>'
    )

    content = (
        table_switcher_html("day", qs)
        + filter_strip
        + f'<section class="day-report-cards">{cards_html}</section>'
    )
    return layout(
        "DayTrace · 日报告",
        f"{len(rows)} 天 · ${total_cost:.4f}",
        "events", content,
        body_class="day-report-page",
    )


# ----- day_project_report: filterable+sortable table ------------------

PROJECT_TABLE_ORDERS = {
    "date_desc":   ("date DESC, event_count DESC", "Date ↓"),
    "date_asc":    ("date ASC, event_count DESC",  "Date ↑"),
    "events_desc": ("event_count DESC, date DESC", "Events ↓"),
    "events_asc":  ("event_count ASC, date DESC",  "Events ↑"),
    "active_desc": ("active_minutes DESC, date DESC", "Active ↓"),
    "active_asc":  ("active_minutes ASC, date DESC",  "Active ↑"),
}


def _render_project_row(row: dict, channels: dict[str, str | None]) -> str:
    summary = _safe_load_json(channels.get("ai_summary")) or {}
    continuity = _safe_load_json(channels.get("ai_continuity")) or {}
    source_mix = _safe_load_json(channels.get("source_mix")) or {}
    time_span = _safe_load_json(channels.get("time_span")) or {}
    top_titles = _safe_load_json(channels.get("top_titles")) or []

    project_link = (
        f'<a class="project-chip" href="/events?project={esc(row["project"])}'
        f'&start_from={esc(row["date"])}&start_to={esc(row["date"])}" '
        f'title="点击看该项目的事件">{esc(row["project"])}</a>'
    )

    share_pct = row["share"] * 100
    share_cell = (
        f'<div class="share-cell">'
        f'<div class="share-bar"><div class="share-fill" style="width:{share_pct:.1f}%"></div></div>'
        f'<span class="share-pct">{share_pct:.0f}%</span></div>'
    )

    src_mix_str = ", ".join(f"{esc(k)}({v})" for k, v in sorted(source_mix.items(), key=lambda kv: -kv[1])[:3])
    span_str = (
        f"{esc(time_span.get('first','?'))}–{esc(time_span.get('last','?'))}"
        if time_span else "—"
    )

    summary_text = summary.get("summary") if isinstance(summary, dict) else ""
    what_was_done = summary.get("what_was_done") if isinstance(summary, dict) else None
    status = summary.get("status") if isinstance(summary, dict) else None
    next_steps = summary.get("next_steps") if isinstance(summary, dict) else None

    ai_cell_parts = [f'<div class="ai-summary-text">{esc(summary_text or "—")}</div>']
    if what_was_done:
        ai_cell_parts.append(
            "<ul class='ai-bullets'>"
            + "".join(f"<li>{esc(w)}</li>" for w in what_was_done[:4])
            + "</ul>"
        )
    if next_steps:
        ai_cell_parts.append(
            "<div class='ai-next-label'>next:</div>"
            "<ul class='ai-bullets ai-next'>"
            + "".join(f"<li>{esc(n)}</li>" for n in next_steps[:3])
            + "</ul>"
        )
    ai_cell = "".join(ai_cell_parts)

    cont_html = ""
    if isinstance(continuity, dict) and continuity:
        cont_html = (
            f'{_momentum_chip(continuity.get("momentum"))}'
            f'<div class="cont-text">{esc(continuity.get("relation_to_previous") or "")}</div>'
        )

    titles_cell = ""
    if top_titles:
        titles_cell = (
            "<ul class='top-titles-list'>"
            + "".join(
                f"<li><span class='tt-time'>{esc(t.get('time','--:--'))}</span> {esc(t.get('title',''))}</li>"
                for t in top_titles[:4]
            )
            + "</ul>"
        )

    return (
        f'<tr>'
        f'<td class="col-date"><a href="/events?table=day&date={esc(row["date"])}">{esc(row["date"])}</a></td>'
        f'<td class="col-project">{project_link}</td>'
        f'<td class="col-num">{row["event_count"]}</td>'
        f'<td class="col-num">{_format_duration_short(row["active_minutes"])}</td>'
        f'<td class="col-share">{share_cell}</td>'
        f'<td class="col-status">{_status_chip(status)}</td>'
        f'<td class="col-ai">{ai_cell}</td>'
        f'<td class="col-cont">{cont_html}</td>'
        f'<td class="col-titles">{titles_cell}</td>'
        f'<td class="col-meta muted small">{esc(span_str)}<br>{esc(src_mix_str)}</td>'
        f"</tr>"
    )


def day_project_report_table_page(db_path: Path, qs: dict[str, list[str]]) -> str:
    """Filterable, sortable per-(date, project) view. Each row shows the AI
    summary inline so a glance across rows reads as a project journal."""
    con = connect(db_path)
    init_db(con)
    selected_date = qs.get("date", [None])[0] or None
    selected_project = qs.get("project", [None])[0] or None
    selected_status = qs.get("status", [None])[0] or None
    order_key = qs.get("order", ["date_desc"])[0]
    if order_key not in PROJECT_TABLE_ORDERS:
        order_key = "date_desc"

    where_parts, params = [], []
    if selected_date:
        where_parts.append("date = ?")
        params.append(selected_date)
    if selected_project:
        where_parts.append("project = ?")
        params.append(selected_project)
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    order_clause, _ = PROJECT_TABLE_ORDERS[order_key]
    rows = con.execute(
        f"""
        SELECT date, project, event_count, active_minutes, share, updated_at
        FROM day_project_report {where}
        ORDER BY {order_clause}
        LIMIT 300
        """,
        params,
    ).fetchall()

    rendered: list[str] = []
    status_set: set[str] = set()
    for row in rows:
        channel_rows = con.execute(
            "SELECT channel, value_json FROM day_project_channel"
            " WHERE date = ? AND project = ?",
            (row["date"], row["project"]),
        ).fetchall()
        channels = {r["channel"]: r["value_json"] for r in channel_rows}
        summary = _safe_load_json(channels.get("ai_summary")) or {}
        if isinstance(summary, dict) and summary.get("status"):
            status_set.add(summary["status"])
        if selected_status and summary.get("status") != selected_status:
            continue
        rendered.append(_render_project_row(dict(row), channels))

    # Filter controls
    all_dates = [
        r["date"] for r in
        con.execute("SELECT DISTINCT date FROM day_project_report ORDER BY date DESC").fetchall()
    ]
    all_projects = [
        r["project"] for r in
        con.execute(
            "SELECT DISTINCT project FROM day_project_report ORDER BY project"
        ).fetchall()
    ]
    statuses = sorted(status_set) or ["in_progress", "done", "blocked", "explored"]

    def opts(values, selected):
        return "".join(
            f'<option value="{esc(v)}"{" selected" if v == selected else ""}>{esc(v)}</option>'
            for v in values
        )

    sort_opts = "".join(
        f'<option value="{k}"{" selected" if k == order_key else ""}>{esc(label)}</option>'
        for k, (_, label) in PROJECT_TABLE_ORDERS.items()
    )

    # Calendar-style date picker; the rest stay as compact dropdowns since
    # they enumerate small fixed sets (project, status, sort).
    cal_widget = calendar_control(
        "/events", selected_date, all_dates,
        hidden={
            "table": "day_project",
            "project": selected_project,
            "status": selected_status,
            "order": order_key if order_key != "date_desc" else "",
        },
        allow_all=True, label_text="日期",
    )
    filter_strip = (
        '<div class="dr-filter-strip">'
        f'{cal_widget}'
        '<form method="get" action="/events" class="dr-filter-inline">'
        '<input type="hidden" name="table" value="day_project">'
        f'<input type="hidden" name="date" value="{esc(selected_date or "")}">'
        f'<label>📁 项目 <select name="project" onchange="this.form.submit()">'
        f'<option value="">全部</option>{opts(all_projects, selected_project)}</select></label> '
        f'<label>🏷 状态 <select name="status" onchange="this.form.submit()">'
        f'<option value="">全部</option>{opts(statuses, selected_status)}</select></label> '
        f'<label>↕ 排序 <select name="order" onchange="this.form.submit()">{sort_opts}</select></label> '
        f'<a href="/events?table=day_project" class="dr-reset">清空筛选</a>'
        f'<span class="muted small dr-rowcount">{len(rendered)} rows</span>'
        '</form>'
        '</div>'
    )

    head = (
        "<thead><tr>"
        "<th class='col-date'>Date</th>"
        "<th class='col-project'>Project</th>"
        "<th class='col-num'>Events</th>"
        "<th class='col-num'>Active</th>"
        "<th class='col-share'>Share</th>"
        "<th class='col-status'>Status</th>"
        "<th class='col-ai'>AI summary · what was done · next</th>"
        "<th class='col-cont'>vs prev</th>"
        "<th class='col-titles'>Top titles</th>"
        "<th class='col-meta'>Span · sources</th>"
        "</tr></thead>"
    )
    body = "".join(rendered) or '<tr><td colspan="10" class="label">没有匹配的行</td></tr>'
    table_html = f'<div class="table-wrap dpr-table"><table>{head}<tbody>{body}</tbody></table></div>'

    content = table_switcher_html("day_project", qs) + filter_strip + table_html
    return layout(
        "DayTrace · 项目日报告",
        f"{len(rendered)} rows",
        "events", content,
        body_class="day-project-report-page",
    )


def events_page(db_path: Path, qs: dict[str, list[str]]):
    con = connect(db_path)
    source = qs.get("source", [None])[0] or None
    if source and source not in VISIBLE_EVENT_SOURCES:
        source = None
    project = qs.get("project", [None])[0] or None
    location_id = qs.get("location_id", [None])[0] or None
    activity_filter = qs.get("activity", [None])[0] or None
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
        "location_id": location_id,
        "activity": activity_filter,
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
        location_id=location_id,
        search=search,
        source_in=None if source else VISIBLE_EVENT_SOURCES,
        start_from=effective_start_from,
        start_to=effective_start_to,
        order=order,
        # Filter by activity in Python after enrichment — query_events
        # doesn't know about the side table.
        limit=None if activity_filter else event_limit,
    )
    # Enrich with AI activity labels.
    from daytrace.db import load_activity_labels_for_event_ids
    labels = load_activity_labels_for_event_ids(con, [e["id"] for e in events])
    for ev in events:
        ev["activity"] = labels.get(ev["id"], "未分类")
    if activity_filter:
        events = [e for e in events if e.get("activity") == activity_filter]
        if event_limit:
            events = events[:event_limit]
    options: dict[str, Any] = query_filter_options(con, option_filters)
    options["date_counts"] = available_event_date_counts(con, VISIBLE_EVENT_SOURCES)
    options["source"] = [{"value": "", "label": "All"}] + [
        {"value": source_value, "label": SOURCE_LABELS[source_value]}
        for source_value in VISIBLE_EVENT_SOURCES
    ]
    # Build the activity dropdown options from currently visible label set.
    activity_values = sorted({e["activity"] for e in events if e.get("activity")})
    options["activity"] = [{"value": "", "label": "All"}] + [
        {"value": a, "label": a} for a in activity_values
    ]
    content = table_switcher_html("events", qs) + events_table(events, filters, options)
    return layout("DayTrace · 数据库", f"{len(events)} events", "events", content)


# ───────────────────────────── weekly report ──────────────────────────────
# v1: pure aggregation (no AI). Sections: stats strip, by-project, by-source,
# per-day mini bars, vs-last-week delta, top events. AI sections (summary,
# next-week recommendations, work-items integration) will land here later.

def _project_of(ev: dict) -> str:
    return str(ev.get("project") or ev.get("project_guess") or "misc")


def _weekly_breakdown(events: list[dict], field: str, top: int = 12) -> list[dict]:
    """Group by field (project/source/device_id), sort desc by count."""
    from collections import Counter
    bag: Counter = Counter()
    for ev in events:
        if field == "project":
            name = _project_of(ev)
        else:
            name = str(ev.get(field) or "unknown")
        bag[name] += 1
    total = sum(bag.values()) or 1
    rows = [{"name": n, "count": c, "share": c / total} for n, c in bag.most_common(top)]
    return rows


def _per_day_counts(events: list[dict], days: list[str], boundary_hour: int) -> dict[str, int]:
    """Bucket events into their owning shifted-day."""
    from datetime import datetime, timedelta
    out = {d: 0 for d in days}
    for ev in events:
        start = ev.get("start") or ""
        try:
            dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        except ValueError:
            continue
        # naive shift: subtract boundary_hour, then take date()
        shifted = (dt - timedelta(hours=boundary_hour)).date().isoformat()
        if shifted in out:
            out[shifted] += 1
    return out


def _diff_breakdowns(this_week: list[dict], last_week: list[dict]) -> list[dict]:
    """Project-level Δ table for the vs-last-week section."""
    a = {r["name"]: r["count"] for r in this_week}
    b = {r["name"]: r["count"] for r in last_week}
    names = sorted(set(a) | set(b), key=lambda n: -(a.get(n, 0) + b.get(n, 0)))
    out = []
    for n in names:
        cur = a.get(n, 0)
        prev = b.get(n, 0)
        delta = cur - prev
        if cur == 0 and prev == 0:
            continue
        out.append({"name": n, "this": cur, "last": prev, "delta": delta})
    return out


def _week_picker_control(current: str, prev_label: str, next_label: str) -> str:
    """Header chip showing current week + prev/next links (mirrors date pill)."""
    return (
        '<div class="date-control">'
        f'<a class="date-nav-btn" href="/weekly?week={esc(prev_label)}">←</a>'
        f'<span class="date-label" style="padding:6px 10px;border-radius:8px;background:#fff;border:1px solid var(--line);">{esc(current)}</span>'
        f'<a class="date-nav-btn" href="/weekly?week={esc(next_label)}">→</a>'
        '</div>'
    )


def _bar_row(label: str, count: int, total: int, *, max_count: int) -> str:
    pct_total = (count / total * 100) if total else 0
    pct_bar = (count / max_count * 100) if max_count else 0
    return (
        '<tr>'
        f'<td>{esc(label)}</td>'
        f'<td style="text-align:right; font-variant-numeric: tabular-nums;">{count}</td>'
        f'<td style="text-align:right; color:var(--muted); font-size:12px;">{pct_total:.1f}%</td>'
        f'<td style="width:120px;"><div style="height:8px;background:#eadfcd;border-radius:4px;overflow:hidden;"><div style="height:100%;width:{pct_bar:.1f}%;background:linear-gradient(90deg,#7b61ff,#2f6fed);"></div></div></td>'
        '</tr>'
    )


def _breakdown_card(title: str, rows: list[dict], total: int) -> str:
    if not rows:
        return f'<section class="card"><h3>{esc(title)}</h3><div class="muted">无数据</div></section>'
    max_count = max(r["count"] for r in rows)
    body = "".join(
        _bar_row(r["name"], r["count"], total, max_count=max_count) for r in rows
    )
    return (
        f'<section class="card"><h3>{esc(title)}</h3>'
        '<table class="mini-table" style="width:100%;"><tbody>'
        f'{body}'
        '</tbody></table></section>'
    )


_WEEK_ZH = ["一", "二", "三", "四", "五", "六", "日"]

# Same palette the daily report timeline uses; reusing it keeps a "DayTrace"
# touching the same name colored the same across daily + weekly views.
_WEEKLY_PALETTE = TIMELINE_PALETTE
_WEEKLY_OTHER_COLOR = "#cbd5e1"


def _shifted_day_of(ev: dict, boundary_hour: int) -> str | None:
    """Which shifted-day name does this event belong to?"""
    from datetime import datetime, timedelta
    start = ev.get("start") or ""
    try:
        dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt - timedelta(hours=boundary_hour)).date().isoformat()


def _event_weight_for_unit(ev: dict, unit: str) -> int:
    if unit == "chars":
        return int(ev.get("char_count") or 0)
    return 1  # "count" and "hours" both start from event count; hours is rescaled later


def _stack_value_of(ev: dict, stack_by: str) -> str:
    if stack_by == "project":
        return _project_of(ev)
    if stack_by == "activity":
        return str(ev.get("activity") or "未分类")
    return str(ev.get(stack_by) or "unknown")  # source / device_id


def _per_day_stack(
    events: list[dict], days: list[str], boundary_hour: int,
    *, stack_by: str, unit: str,
) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """For each day, return {dim_value: weight}, plus per-day totals.

    For unit='hours' we return raw event/char weight here; the caller rescales
    each day's bag against day_report.active_minutes (proportional split)."""
    from collections import defaultdict
    per_day: dict[str, dict[str, float]] = {d: defaultdict(float) for d in days}
    totals: dict[str, float] = {d: 0.0 for d in days}
    for ev in events:
        d = _shifted_day_of(ev, boundary_hour)
        if d not in per_day:
            continue
        v = _stack_value_of(ev, stack_by)
        w = _event_weight_for_unit(ev, unit)
        per_day[d][v] += w
        totals[d] += w
    return {d: dict(b) for d, b in per_day.items()}, totals


_SLOT_MIN = 5  # must match daytrace.stats.ACTIVE_SLOT_MIN


def _per_slot_hours_per_dim(
    events: list[dict], days: list[str], boundary_hour: int,
    *, stack_by: str,
) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """Per-(day, dim) HOURS via per-5min-slot proportional split.

    Algorithm:
      1) For every event, find its (shifted_day, 5min_slot, dim_value).
      2) For each (day, slot), distribute the slot's 5 minutes among
         the dim values present in that slot proportional to event count.
      3) Sum per (day, dim) to get minutes, divide by 60 for hours.

    Properties:
      - Σ_dim per day ≡ that day's active_minutes / 60 (no double counting)
      - Bursty 8 events of source X in one slot only earn ≤5 min total
        (capped by the slot), not 8 × the day's hours / total_events.
      - When two dims share a slot, the slot's 5 min is split by their
        event ratio within just that slot — local fairness.
    """
    from collections import defaultdict
    from daytrace.stats import _safe_minute  # type: ignore

    per_slot_dim: dict[tuple[str, int], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    days_set = set(days)
    for ev in events:
        d = _shifted_day_of(ev, boundary_hour)
        if d not in days_set:
            continue
        m = _safe_minute(ev.get("start"))
        if m is None:
            continue
        slot = m // _SLOT_MIN
        v = _stack_value_of(ev, stack_by)
        per_slot_dim[(d, slot)][v] += 1

    per_day_dim_min: dict[str, dict[str, float]] = {d: defaultdict(float) for d in days}
    for (d, _slot), dim_counts in per_slot_dim.items():
        total = sum(dim_counts.values())
        if total <= 0:
            continue
        for v, c in dim_counts.items():
            per_day_dim_min[d][v] += _SLOT_MIN * (c / total)

    per_day_hours = {d: {v: m / 60.0 for v, m in bag.items()} for d, bag in per_day_dim_min.items()}
    totals = {d: sum(bag.values()) for d, bag in per_day_hours.items()}
    return per_day_hours, totals


def _palette_for(top_names: list[str]) -> dict[str, str]:
    """Assign stable colors to the top-N names, grey for the rest."""
    palette: dict[str, str] = {}
    for i, n in enumerate(top_names):
        palette[n] = _WEEKLY_PALETTE[i] if i < len(_WEEKLY_PALETTE) else _WEEKLY_OTHER_COLOR
    return palette


_WEEKLY_UNIT_OPTS = [
    ("hours", "小时"),
    ("count", "事件数"),
    ("chars", "字数"),
]
_WEEKLY_DIM_OPTS = [
    ("project",   "项目"),
    ("source",    "数据源"),
    ("activity",  "活动"),
    ("device_id", "设备"),
]
_WEEKLY_VIEW_OPTS = [
    ("chart", "直方图"),
    ("swim",  "泳道"),
    ("heat",  "热力图"),
]


def _weekly_url(
    *, week: str, mode: str, unit: str, view: str, anchor: str = "",
    override: dict[str, str] | None = None,
) -> str:
    """Build a /weekly URL with all four state params, optionally overriding
    one and appending an #anchor so the browser scrolls to it on load."""
    params = {"week": week, "mode": mode, "unit": unit, "view": view}
    if override:
        params.update(override)
    qs = "&".join(f"{k}={esc(v)}" for k, v in params.items())
    return f"/weekly?{qs}" + (f"#{anchor}" if anchor else "")


def _pill_bar(
    *, css_class: str, options: list[tuple[str, str]], current: str,
    href_for: callable,
) -> str:
    """Render a `.dim-tabs` / `.unit-tabs` pill group with active state."""
    chips = []
    for value, label in options:
        cls = f"{css_class} active" if value == current else css_class
        chips.append(f'<a class="{cls}" href="{esc(href_for(value))}">{esc(label)}</a>')
    container = "dim-tabs" if "dim-tab" in css_class else "unit-tabs"
    return f'<div class="{container}">{"".join(chips)}</div>'


def _weekly_dim_bar(
    *, week: str, prev_week: str, next_week: str,
    mode: str, unit: str, view: str, monday: str, sunday: str,
) -> str:
    """Sticky global controls bar — same role as /today's dim-bar. Holds:
      - week-nav (prev / next / open this week's events)
      - unit pills (小时/事件数/字数)
      - dim pills (项目/数据源/活动/设备)

    All four params (week, mode, unit, view) are preserved across every
    link so users navigate without losing state."""
    week_nav_inner = (
        f'<a href="{_weekly_url(week=prev_week, mode=mode, unit=unit, view=view)}">← 上一周 {esc(prev_week)}</a>'
        f'<a href="{_weekly_url(week=next_week, mode=mode, unit=unit, view=view)}">下一周 {esc(next_week)} →</a>'
        f'<a href="/events?start_from={esc(monday)}&start_to={esc(sunday)}">打开本周数据库</a>'
    )
    unit_bar = _pill_bar(
        css_class="unit-tab", options=_WEEKLY_UNIT_OPTS, current=unit,
        href_for=lambda v: _weekly_url(
            week=week, mode=mode, unit=v, view=view, anchor="chart",
        ),
    )
    dim_bar = _pill_bar(
        css_class="dim-tab", options=_WEEKLY_DIM_OPTS, current=mode,
        href_for=lambda v: _weekly_url(
            week=week, mode=v, unit=unit, view=view, anchor="chart",
        ),
    )
    return (
        '<section class="dim-bar">'
        f'<div class="day-nav">{week_nav_inner}</div>'
        '<div class="dim-bar-right">'
        f'<div title="按小时/条目数/字数统计">{unit_bar}</div>'
        f'<div title="按哪个维度堆叠/上色">{dim_bar}</div>'
        '</div>'
        '</section>'
    )


def _view_switcher_pills(*, week: str, mode: str, unit: str, view: str) -> str:
    """The 直方图 / 泳道 / 热力图 pill row inside the main viz card."""
    return _pill_bar(
        css_class="dim-tab", options=_WEEKLY_VIEW_OPTS, current=view,
        href_for=lambda v: _weekly_url(
            week=week, mode=mode, unit=unit, view=v, anchor="chart",
        ),
    )


def _format_value(v: float, unit: str) -> str:
    if unit == "hours":
        return f"{v:.1f}h"
    if unit == "chars":
        if v >= 1000:
            return f"{v/1000:.1f}k"
        return f"{int(v)}"
    return f"{int(v)}"


def _weekly_swimlane_card(
    *, events: list[dict], days: list[str], boundary_hour: int,
    stack_by: str, top_names: list[str], palette: dict[str, str],
) -> str:
    """7 horizontal swim-lanes (one per shifted-day), each showing 24h ticks
    colored by the current stack_by dim. Mirrors the daily timeline's swim
    style — same hour grid, same hairline ticks, same hover semantics —
    but stacked across the week so you see "what project on which day at
    which hour" in one glance.

    Time axis runs from boundary_hour (e.g. 04:00) on the left to
    boundary_hour next day on the right, matching the daily timeline so
    the visual mental model carries over."""
    from datetime import datetime, date as _date, timedelta
    if not events:
        return ""

    boundary_min = (boundary_hour % 24) * 60
    days_set = set(days)
    OTHER = _WEEKLY_OTHER_COLOR

    def shifted_pos_min(dt: datetime) -> int:
        """Clock-minute → position-minute on the 0..1440 shifted axis."""
        m = dt.hour * 60 + dt.minute
        return (m - boundary_min) % (24 * 60)

    # Bucket ticks per day
    per_day_ticks: dict[str, list[dict]] = {d: [] for d in days}
    for ev in events:
        s = ev.get("start") or ""
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            continue
        d = (dt - timedelta(hours=boundary_hour)).date().isoformat()
        if d not in days_set:
            continue
        v = _stack_value_of(ev, stack_by)
        color = palette.get(v, OTHER)
        pos_min = shifted_pos_min(dt)
        per_day_ticks[d].append({
            "pos": pos_min / (24 * 60) * 100,
            "color": color,
            "title": ev.get("title") or "",
            "time": s[11:16],
            "value": v,
        })

    # Hour grid labels (every 2h), aligned with the daily timeline conventions
    hour_grid = "".join(
        f'<div style="position:absolute; left:{(i/12)*100:.4f}%; top:0; bottom:0; width:1px; background:rgba(0,0,0,0.04);">'
        f'<span style="position:absolute; top:-15px; left:-9px; font-size:9px; color:var(--muted); font-variant-numeric:tabular-nums;">{(boundary_hour + i*2) % 24:02d}</span></div>'
        for i in range(13)
    )

    rows_html = []
    for d in days:
        wd = _WEEK_ZH[_date.fromisoformat(d).weekday()]
        ticks = per_day_ticks[d]
        ticks_html = "".join(
            f'<div title="{esc(t["time"] + " · " + t["value"] + (" · " + t["title"][:40] if t["title"] else ""))}" '
            f'style="position:absolute; left:{t["pos"]:.3f}%; top:3px; bottom:3px; width:3px; '
            f'border-radius:2px; background:{t["color"]}; transform:translateX(-1px); cursor:default;"></div>'
            for t in ticks
        )
        empty_note = '' if ticks else (
            '<div style="position:absolute; inset:0; display:flex; align-items:center; justify-content:center; '
            'font-size:11px; color:#cbb;">无活动</div>'
        )
        rows_html.append(
            '<div style="display:grid; grid-template-columns:60px 1fr; gap:10px; align-items:center; padding:3px 0;">'
            f'<div style="font-size:12px; color:var(--muted); display:flex; gap:6px; align-items:baseline;">'
            f'<span style="font-weight:700; color:var(--ink);">周{wd}</span>'
            f'<span style="font-size:10px; color:#bbb; font-variant-numeric:tabular-nums;">{esc(d[5:])}</span>'
            f'<span style="margin-left:auto; font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums;">{len(ticks)}</span></div>'
            f'<div style="position:relative; height:22px; background:linear-gradient(180deg,#faf6ec,#f3ead8); '
            f'border:1px solid #e6dcc6; border-radius:5px;">{hour_grid}{ticks_html}{empty_note}</div>'
            '</div>'
        )

    return (
        '<div style="position:relative; padding-top:18px;">'
        + "".join(rows_html) +
        '</div>'
        '<div class="muted small" style="margin-top:8px;">'
        f'横轴是 24 小时 (shifted 边界 {boundary_hour:02d}:00 起)，每根竖线一个事件，颜色跟直方图一致。'
        '</div>'
    )


def _compute_palette_for_week(
    per_day: dict[str, dict[str, float]],
) -> tuple[list[str], dict[str, str]]:
    """Top-N dim values across the week → distinct palette colors, rest grey.
    Returned so other cards (swim-lane, legend) can reuse the same mapping
    and stay color-consistent with the main chart."""
    from collections import Counter
    overall: Counter = Counter()
    for bag in per_day.values():
        for k, v in bag.items():
            overall[k] += v
    top = [n for n, _ in overall.most_common(10)]
    palette = _palette_for(top)
    palette["其它"] = _WEEKLY_OTHER_COLOR
    return top, palette


def _main_chart_card(
    *, days: list[str], per_day: dict[str, dict[str, float]],
    per_day_totals: dict[str, float], unit: str, stack_by: str,
    top_names: list[str], palette: dict[str, str],
) -> str:
    """7-day stacked bar chart body. Each bar = one day; segments = stack_by dim.
    Returns inner HTML; the caller wraps it in the view-switcher card."""
    from datetime import date as _date
    from collections import Counter

    overall: Counter = Counter()
    for bag in per_day.values():
        for k, v in bag.items():
            overall[k] += v
    top = top_names

    def fold(bag: dict[str, float]) -> list[tuple[str, float]]:
        kept = []
        other = 0.0
        for k, v in bag.items():
            if k in palette:
                kept.append((k, v))
            else:
                other += v
        kept.sort(key=lambda kv: -kv[1])
        if other > 0:
            kept.append(("其它", other))
        return kept

    max_total = max(per_day_totals.values()) if per_day_totals else 0
    if max_total <= 0:
        return '<div class="muted">本周该维度无可用数据</div>'

    BAR_HEIGHT_PX = 220
    bars_html = []
    for d in days:
        bag = fold(per_day.get(d, {}))
        total = per_day_totals.get(d, 0.0)
        wd = _WEEK_ZH[_date.fromisoformat(d).weekday()]
        # Each segment height in px is proportional to its share of max_total.
        segments = []
        # Tooltip content: per-segment breakdown
        tooltip = f"{d} 周{wd} · {_format_value(total, unit)}\n" + "\n".join(
            f"  {k}: {_format_value(v, unit)}" for k, v in bag if v > 0
        )
        for k, v in bag:
            if v <= 0:
                continue
            h_px = (v / max_total) * BAR_HEIGHT_PX
            color = palette.get(k, _WEEKLY_OTHER_COLOR)
            segments.append(
                f'<div title="{esc(k)}: {_format_value(v, unit)}" '
                f'style="height:{h_px:.1f}px; background:{color}; '
                f'border-bottom:1px solid rgba(255,255,255,0.55);"></div>'
            )
        # Stack from top → tallest first; flex-end aligns to baseline.
        bars_html.append(
            f'<div title="{esc(tooltip)}" '
            f'style="flex:1; min-width:0; display:flex; flex-direction:column; align-items:center; gap:5px;">'
            f'<div style="height:{BAR_HEIGHT_PX}px; width:78%; display:flex; flex-direction:column-reverse; '
            f'border-radius:5px 5px 0 0; overflow:hidden; background:#f1ece2;">'
            + "".join(segments) +
            '</div>'
            f'<div style="font-size:12px; font-weight:600; color:var(--ink); font-variant-numeric:tabular-nums;">{_format_value(total, unit)}</div>'
            f'<div style="font-size:11px; color:var(--muted);">周{wd}</div>'
            f'<div style="font-size:10px; color:#bbb; font-variant-numeric:tabular-nums;">{esc(d[5:])}</div>'
            f'</div>'
        )

    # Legend
    legend = []
    for k in [n for n in top if overall[n] > 0]:
        color = palette[k]
        total = overall[k]
        legend.append(
            f'<span style="display:inline-flex; align-items:center; gap:6px; margin-right:14px;">'
            f'<span style="width:10px; height:10px; border-radius:3px; background:{color}; display:inline-block;"></span>'
            f'<span style="font-size:12px;">{esc(k)}</span>'
            f'<span style="font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums;">{_format_value(total, unit)}</span>'
            f'</span>'
        )
    if "其它" in [k for k in fold({n: overall[n] for n in overall}) [0:1]]:
        pass  # legend already covers top-N; "其它" segment is self-explanatory

    return (
        '<div style="display:flex; gap:8px; align-items:flex-end; padding:6px 4px 12px;">'
        + "".join(bars_html) +
        '</div>'
        '<div style="display:flex; flex-wrap:wrap; gap:4px; padding-top:6px; border-top:1px dashed #eadfcd;">'
        + "".join(legend) +
        '</div>'
    )


def _hour_heatmap_card(events: list[dict], days: list[str], boundary_hour: int) -> str:
    """24×7 heatmap of event density. Y = clock hour 0-23, X = day of week.
    Background opacity scales with event count. Great for spotting work
    rhythm patterns ("I always work 22-01" / "I never touch Saturdays")."""
    from datetime import datetime, timedelta, date as _date
    # buckets[(date, hour_of_day_in_clock_local)] → count
    from collections import Counter
    buckets: Counter = Counter()
    for ev in events:
        s = ev.get("start") or ""
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            continue
        # find shifted-day for the event so it sits in the right column
        shifted = (dt - timedelta(hours=boundary_hour)).date().isoformat()
        if shifted not in days:
            continue
        buckets[(shifted, dt.hour)] += 1

    if not buckets:
        return ""
    max_c = max(buckets.values()) or 1

    cells_html = []
    # header row
    header = ['<div></div>']  # corner
    for d in days:
        wd = _WEEK_ZH[_date.fromisoformat(d).weekday()]
        header.append(f'<div style="text-align:center; font-size:11px; color:var(--muted);">周{wd}<br><span style="font-size:10px; color:#bbb;">{esc(d[5:])}</span></div>')
    cells_html.append("".join(header))

    # 24 hour rows
    for h in range(24):
        row = [f'<div style="font-size:10px; color:var(--muted); text-align:right; padding-right:4px; font-variant-numeric:tabular-nums;">{h:02d}</div>']
        for d in days:
            c = buckets.get((d, h), 0)
            if c == 0:
                bg = "transparent"
                fg = "transparent"
            else:
                alpha = 0.15 + 0.85 * (c / max_c)
                bg = f"rgba(47, 111, 237, {alpha:.2f})"
                fg = "white" if alpha > 0.55 else "var(--ink)"
            label = c if c > 0 else ""
            row.append(
                f'<div title="{d} · {h:02d}:00 · {c} events" '
                f'style="background:{bg}; color:{fg}; height:18px; border-radius:3px; '
                f'display:flex; align-items:center; justify-content:center; font-size:10px; font-weight:600;">'
                f'{label}</div>'
            )
        cells_html.append("".join(row))

    grid = (
        '<div style="display:grid; grid-template-columns:30px repeat(7, 1fr); gap:2px;">'
        + "".join(cells_html) +
        '</div>'
    )

    total = sum(buckets.values())
    busiest_hour = max(range(24), key=lambda h: sum(buckets.get((d, h), 0) for d in days))
    busiest_count = sum(buckets.get((d, busiest_hour), 0) for d in days)
    return (
        '<div class="muted small" style="margin-bottom:8px;">'
        f'总 {total} 个事件 · 最忙时段 {busiest_hour:02d}:00（{busiest_count} 个）'
        '</div>'
        + grid
    )


# ───── AI weekly summary (cached on disk) ─────

def _week_ai_cache_path(week: str) -> Path:
    """Persistent cache for AI weekly summaries. Keyed by week + events_hash
    (events_hash baked into file content), survives dashboard restarts."""
    return DEFAULT_DB.parent / "week_ai_cache" / f"{week}.json"


def _events_hash(events: list[dict]) -> str:
    """SHA1 over sorted event IDs. Cache invalidates when new events land."""
    import hashlib
    h = hashlib.sha1()
    for eid in sorted(ev["id"] for ev in events if ev.get("id")):
        h.update(eid.encode("utf-8"))
    return h.hexdigest()[:16]


def _ai_weekly_summary(
    *, week: str, events: list[dict], by_project: list[dict],
    total_minutes: float, active_days: int,
) -> dict | None:
    """Return {headline, narrative, highlights, suggestions} or None if AI
    unavailable / hash unchanged / call failed. Cached on disk by events_hash."""
    import json as _json
    from daytrace import ai_client
    if not ai_client.is_available():
        return {"_unavailable": True}

    cache_path = _week_ai_cache_path(week)
    ev_hash = _events_hash(events)
    if cache_path.exists():
        try:
            cached = _json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("events_hash") == ev_hash:
                return cached.get("value")
        except Exception:
            pass

    if not events:
        return None

    # Compact summary the model can chew on cheaply
    top_projects = "\n".join(
        f"- {r['name']}: {r['count']} events ({r['share']*100:.0f}%)"
        for r in by_project[:8]
    )
    user = (
        f"这是 2026 年 ISO 周 {week} 的活动汇总。\n\n"
        f"总事件数: {len(events)}\n"
        f"活跃天数: {active_days}/7\n"
        f"活跃总时长（估计）: {total_minutes/60:.1f}h\n\n"
        f"项目分布 (top 8):\n{top_projects}\n\n"
        "请输出严格 JSON:\n"
        "{\n"
        '  "headline": "1 句话本周关键词 (≤30 字)",\n'
        '  "narrative": "2-3 句叙事, 主线 + 状态 + 重点产出",\n'
        '  "highlights": ["2-5 条具体进展, 每条 ≤40 字"],\n'
        '  "suggestions": ["1-3 条下周建议或观察, 每条 ≤50 字"]\n'
        "}"
    )
    system = (
        "你是 DayTrace 周报助手。只看聚合数字, 不杜撰未提供的细节。"
        "严格只输出 JSON, 不要 Markdown。"
    )

    def _validator(payload):
        from daytrace.ai_client import ShapeError
        if not isinstance(payload, dict):
            raise ShapeError("expected object")
        for k in ("headline", "narrative"):
            if not isinstance(payload.get(k), str):
                raise ShapeError(f"{k} must be string")
        for k in ("highlights", "suggestions"):
            if not isinstance(payload.get(k, []), list):
                raise ShapeError(f"{k} must be list")
        return payload

    try:
        resp = ai_client.call_json_validated(
            system=system, user=user, validator=_validator, max_tokens=900,
        )
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}

    value = resp.json
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        _json.dumps({"events_hash": ev_hash, "value": value,
                     "cost_usd": resp.cost_usd, "tokens_in": resp.tokens_in,
                     "tokens_out": resp.tokens_out}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return value


def _weekly_stats_strip(
    *, total_events: int, last_total: int,
    total_minutes: float, last_active_minutes: float,
    active_days: int, ai_cost: float,
) -> str:
    """Compact 4-tile stats strip; sits at the top of the weekly-report card
    (parallel to the daily report's own stats strip)."""
    delta = total_events - last_total
    delta_pct = ((delta / last_total) * 100) if last_total else 0
    hours_delta = (total_minutes - last_active_minutes) / 60.0
    delta_color = "#16a34a" if delta >= 0 else "#dc2626"
    hours_color = "#16a34a" if hours_delta >= 0 else "#dc2626"
    return (
        '<div class="dr-stats-compact">'
        f'<div class="dr-stat">'
        f'<span class="dr-stat-num">{total_events}</span>'
        f'<span class="dr-stat-lbl">事件总数</span>'
        f'<span class="muted" style="font-size:10.5px; margin-top:2px;">上周 {last_total} '
        f'<span style="color:{delta_color};">({delta:+d}, {delta_pct:+.0f}%)</span></span></div>'
        f'<div class="dr-stat">'
        f'<span class="dr-stat-num">{total_minutes/60:.1f}h</span>'
        f'<span class="dr-stat-lbl">活跃总时长</span>'
        f'<span class="muted" style="font-size:10.5px; margin-top:2px;">上周 {last_active_minutes/60:.1f}h '
        f'<span style="color:{hours_color};">({hours_delta:+.1f}h)</span></span></div>'
        f'<div class="dr-stat">'
        f'<span class="dr-stat-num">{active_days}/7</span>'
        f'<span class="dr-stat-lbl">活跃天数</span>'
        f'<span class="muted" style="font-size:10.5px; margin-top:2px;">空白 {7 - active_days}</span></div>'
        f'<div class="dr-stat">'
        f'<span class="dr-stat-num">${ai_cost:.3f}</span>'
        f'<span class="dr-stat-lbl">AI 报告花费</span>'
        f'<span class="muted" style="font-size:10.5px; margin-top:2px;">DeepSeek · 含周报</span></div>'
        '</div>'
    )


def _ai_summary_body(summary: dict | None) -> str:
    """Headline + narrative for the left card. Mirrors daily's
    _render_ai_overview_block. Highlights / suggestions go to the right card."""
    if summary is None:
        return '<div class="dr-headline muted">📰 (本周 AI 速读还没生成)</div>'
    if summary.get("_unavailable"):
        return '<div class="dr-headline muted">📰 (DEEPSEEK_API_KEY 未设置, 跳过)</div>'
    if summary.get("_error"):
        return f'<div class="dr-headline muted">📰 AI 调用失败: {esc(summary["_error"])}</div>'
    headline = summary.get("headline") or "(尚未生成)"
    narrative = summary.get("narrative") or ""
    return (
        f'<div class="dr-headline">📰 {esc(headline)}</div>'
        f'<p class="dr-narrative">{esc(narrative)}</p>'
    )


def _ai_highlights_card(summary: dict | None) -> str:
    """Right column: ✨ Highlights / 💡 Suggestions — same structure as daily's
    `_render_highlights_concerns_card`, just labeled for the weekly view."""
    if summary is None or summary.get("_unavailable") or summary.get("_error"):
        return ""
    highlights = summary.get("highlights") or []
    suggestions = summary.get("suggestions") or []
    if not highlights and not suggestions:
        return ""
    hl = "".join(f"<li>{esc(h)}</li>" for h in highlights)
    sg = "".join(f"<li>{esc(s)}</li>" for s in suggestions)
    sections = []
    if hl:
        sections.append(f'<div class="dr-section"><h4>✨ 关键进展</h4><ul class="dr-bullets dr-highlights">{hl}</ul></div>')
    if sg:
        sections.append(f'<div class="dr-section"><h4>💡 下周观察</h4><ul class="dr-bullets dr-concerns">{sg}</ul></div>')
    return (
        '<div class="card highlights-card">'
        f'<div class="dr-grid">{"".join(sections)}</div>'
        '</div>'
    )


def _vs_last_week_card(diffs: list[dict]) -> str:
    if not diffs:
        return ""
    rows_html = []
    for r in diffs[:15]:
        delta = r["delta"]
        if delta > 0:
            arrow, color = f"+{delta}", "#16a34a"
        elif delta < 0:
            arrow, color = f"{delta}", "#dc2626"
        else:
            arrow, color = "—", "var(--muted)"
        rows_html.append(
            '<tr>'
            f'<td>{esc(r["name"])}</td>'
            f'<td style="text-align:right; font-variant-numeric:tabular-nums;">{r["this"]}</td>'
            f'<td style="text-align:right; color:var(--muted); font-variant-numeric:tabular-nums;">{r["last"]}</td>'
            f'<td style="text-align:right; color:{color}; font-weight:600; font-variant-numeric:tabular-nums;">{arrow}</td>'
            '</tr>'
        )
    return (
        '<section class="card"><h3>跟上周比（按项目）</h3>'
        '<table class="mini-table" style="width:100%;">'
        '<thead><tr><th style="text-align:left;">项目</th>'
        '<th style="text-align:right;">本周</th>'
        '<th style="text-align:right;">上周</th>'
        '<th style="text-align:right;">Δ</th></tr></thead>'
        f'<tbody>{"".join(rows_html)}</tbody></table></section>'
    )


def weekly_page(
    db_path: Path, week: str | None,
    *, unit: str | None = None, mode: str | None = None,
    view: str | None = None,
) -> str:
    """ISO-week page mirroring the daily report's layout:

      • sticky dim-bar at top (week-nav + 单位 + 维度)
      • report-grid: weekly-report card (stats + AI overview) | highlights
      • main viz card with view-switcher: 直方图 / 泳道 / 热力图
      • breakdown tables + vs-last-week + per-day links

    URL params:
      week=YYYY-Www  (default: last week)
      unit=hours|count|chars   (default hours)
      mode=project|source|activity|device_id   (default project)
      view=chart|swim|heat   (default chart)
    """
    from daytrace.db import (
        connect, init_db, events_for_shifted_week,
        iso_week_to_date_range, date_to_iso_week, iso_week_neighbors,
        load_activity_labels_for_event_ids,
    )
    from daytrace import stats as _stats

    valid_units = {u for u, _ in _WEEKLY_UNIT_OPTS}
    valid_modes = {m for m, _ in _WEEKLY_DIM_OPTS}
    valid_views = {v for v, _ in _WEEKLY_VIEW_OPTS}
    if unit not in valid_units:
        unit = "hours"
    if mode not in valid_modes:
        mode = "project"
    if view not in valid_views:
        view = "chart"

    con = connect(db_path); init_db(con)
    if not week:
        from datetime import datetime, timedelta
        now = datetime.now()
        ref = now.date() if now.hour >= _stats.DAY_BOUNDARY_HOUR else (now.date() - timedelta(days=1))
        week = date_to_iso_week((ref - timedelta(days=7)).isoformat())

    try:
        monday, sunday, days = iso_week_to_date_range(week)
    except ValueError as e:
        return layout("DayTrace · 周报", "格式错误", "weekly",
                      f'<section class="card"><div class="muted">{esc(str(e))}</div></section>')
    prev_week, next_week = iso_week_neighbors(week)
    bh = _stats.DAY_BOUNDARY_HOUR

    events = events_for_shifted_week(con, week)
    last_events = events_for_shifted_week(con, prev_week)

    # Activity labels for stack_by=activity
    if events and mode == "activity":
        labels = load_activity_labels_for_event_ids(con, [e["id"] for e in events])
        for ev in events:
            ev["activity"] = labels.get(ev["id"], "未分类")

    # Per-day active minutes
    per_day_minutes: dict[str, float] = {d: 0.0 for d in days}
    for r in con.execute(
        "SELECT date, active_minutes FROM day_report WHERE date BETWEEN ? AND ?",
        (monday, sunday),
    ).fetchall():
        per_day_minutes[r["date"]] = float(r["active_minutes"] or 0)
    total_minutes = sum(per_day_minutes.values())

    total_events = len(events)
    per_day_counts = _per_day_counts(events, days, bh)
    active_days = sum(1 for v in per_day_counts.values() if v > 0)
    last_total = len(last_events)
    last_active_minutes = con.execute(
        "SELECT COALESCE(SUM(active_minutes),0) FROM day_report WHERE date BETWEEN ? AND ?",
        iso_week_to_date_range(prev_week)[:2],
    ).fetchone()[0] or 0
    ai_cost = con.execute(
        "SELECT COALESCE(SUM(cost_usd),0) FROM day_channel "
        "WHERE date BETWEEN ? AND ? AND generator='ai'",
        (monday, sunday),
    ).fetchone()[0] or 0

    by_project = _weekly_breakdown(events, "project", top=12)
    by_source = _weekly_breakdown(events, "source", top=8)
    diffs = _diff_breakdowns(by_project, _weekly_breakdown(last_events, "project", top=50))

    # Per-(day, dim) stacks for the histogram view (and to give the swim-lane
    # the right palette).
    if unit == "hours":
        per_day_stack, per_day_totals = _per_slot_hours_per_dim(
            events, days, bh, stack_by=mode,
        )
    else:
        per_day_stack, per_day_totals = _per_day_stack(
            events, days, bh, stack_by=mode, unit=unit,
        )
    top_names, palette = _compute_palette_for_week(per_day_stack)

    # AI summary
    ai_summary = _ai_weekly_summary(
        week=week, events=events, by_project=by_project,
        total_minutes=total_minutes, active_days=active_days,
    )

    # ── Build cards ────────────────────────────────────────────────────────
    dim_bar = _weekly_dim_bar(
        week=week, prev_week=prev_week, next_week=next_week,
        mode=mode, unit=unit, view=view, monday=monday, sunday=sunday,
    )

    stats_strip = _weekly_stats_strip(
        total_events=total_events, last_total=last_total,
        total_minutes=total_minutes, last_active_minutes=last_active_minutes,
        active_days=active_days, ai_cost=ai_cost,
    )

    # LEFT card (stats + AI overview) — mirrors daily's .card.daily-report
    weekly_report_card = (
        '<div class="card daily-report">'
        f'<div class="bucket-head"><h2>周报 · {esc(week)}</h2><span class="tag source">Weekly</span></div>'
        f'{stats_strip}'
        f'{_ai_summary_body(ai_summary)}'
        '</div>'
    )

    # RIGHT card (highlights + suggestions)
    highlights_card = _ai_highlights_card(ai_summary)
    right_column_html = highlights_card or (
        '<div class="card"><div class="muted small">'
        '✨ Highlights / 💡 Suggestions 还没生成 '
        '（DEEPSEEK_API_KEY 未设置或本周尚无事件）'
        '</div></div>'
    )

    # Main viz card — view switcher + selected view body
    view_switcher = _view_switcher_pills(week=week, mode=mode, unit=unit, view=view)
    view_title = {"chart": "直方图（每日堆叠）", "swim": "本周时间线（泳道）",
                  "heat": "活跃时段热力图（24h × 7d）"}[view]
    if view == "chart":
        view_body = _main_chart_card(
            days=days, per_day=per_day_stack, per_day_totals=per_day_totals,
            unit=unit, stack_by=mode, top_names=top_names, palette=palette,
        )
    elif view == "swim":
        view_body = _weekly_swimlane_card(
            events=events, days=days, boundary_hour=bh,
            stack_by=mode, top_names=top_names, palette=palette,
        ) or '<div class="muted">本周无事件</div>'
    else:  # heat
        view_body = _hour_heatmap_card(events, days, bh) or '<div class="muted">本周无事件</div>'

    main_viz_card = (
        '<section class="card" id="chart">'
        f'<div style="display:flex; align-items:center; gap:12px; margin-bottom:10px; flex-wrap:wrap;">'
        f'<h3 style="margin:0;">{esc(view_title)}</h3>'
        f'<div style="margin-left:auto;">{view_switcher}</div>'
        '</div>'
        f'{view_body}'
        '</section>'
    )

    # Per-day links
    from datetime import date as _date
    day_links = " · ".join(
        f'<a href="/today?date={d}">{d[5:]}（周{_WEEK_ZH[_date.fromisoformat(d).weekday()]}）</a>'
        for d in days
    )
    day_links_html = f'<section class="card"><h3>跳到每日报告</h3><div>{day_links}</div></section>'

    body = (
        dim_bar
        + '<section class="report-grid">'
        + weekly_report_card
        + '<div class="right-column">' + right_column_html + '</div>'
        + '</section>'
        + main_viz_card
        + '<div class="section-grid">'
        + _breakdown_card("项目分布（本周）", by_project, total_events)
        + _breakdown_card("数据源分布（本周）", by_source, total_events)
        + '</div>'
        + _vs_last_week_card(diffs)
        + day_links_html
    )

    if total_events == 0:
        body = (
            dim_bar +
            '<section class="card"><div class="muted">'
            f'本周（{monday} ~ {sunday}）暂无事件数据。'
            '可能是 catchup 还没跑到，或者这周确实没记录。'
            '</div></section>' + day_links_html
        )

    subtitle = (
        f"{monday} ~ {sunday} · {total_events} events · "
        f"{total_minutes/60:.1f}h active · {active_days}/7 days"
    )
    return layout(f"DayTrace · {week}", subtitle, "weekly", body)


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
                redirect_params = {}
                if date:
                    redirect_params["date"] = date
                mode = qs.get("mode", [None])[0] or None
                if mode and mode != "source":
                    redirect_params["mode"] = mode
                self.send_response(302)
                self.send_header("Location", "/today" + (("?" + urlencode(redirect_params)) if redirect_params else ""))
                self.end_headers()
            elif parsed.path == "/today":
                mode = qs.get("mode", [None])[0] or None
                unit = qs.get("unit", [None])[0] or None
                html_response(self, today_page(self.db_path, date, mode=mode, unit=unit))
            elif parsed.path == "/weekly":
                week = qs.get("week", [None])[0] or None
                w_unit = qs.get("unit", [None])[0] or None
                # Accept both `mode` (new, matches /today) and `stack_by` (legacy)
                w_mode = qs.get("mode", [None])[0] or qs.get("stack_by", [None])[0] or None
                w_view = qs.get("view", [None])[0] or None
                html_response(self, weekly_page(
                    self.db_path, week, unit=w_unit, mode=w_mode, view=w_view,
                ))
            elif parsed.path == "/sources":
                self.send_response(302)
                self.send_header("Location", "/events")
                self.end_headers()
            elif parsed.path == "/events":
                table_choice = qs.get("table", ["events"])[0] or "events"
                if table_choice == "day":
                    html_response(self, day_report_table_page(self.db_path, qs))
                elif table_choice == "day_project":
                    html_response(self, day_project_report_table_page(self.db_path, qs))
                else:
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
