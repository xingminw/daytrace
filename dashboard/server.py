#!/usr/bin/env python3
from __future__ import annotations

import argparse
import calendar
import html
import json
import re
from datetime import date as dt_date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daytrace.db import connect, init_db, query_events, query_filter_options, query_summary, query_today

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "daytrace.sqlite"


# ───── i18n ─────────────────────────────────────────────────────────────
# Cookie-driven 2-language UI. Default is zh (the author's primary
# language); `?lang=en` or the header switcher sets the cookie. Anything
# not in this map renders as zh — that's intentional, since
# fully-translating the dashboard is incremental work.
#
# Convention: keys are stable English snake_case; values are
# {"zh": "...", "en": "..."}. The translation function is `T`.

_STRINGS: dict[str, dict[str, str]] = {
    # Language switcher pill labels — one entry per language declared
    # in config/i18n.yaml. Adding a third language? Add lang_label_<code> here.
    "lang_label_zh":  {"zh": "中",     "en": "中"},
    "lang_label_en":  {"zh": "EN",     "en": "EN"},

    # Page titles / nav
    "nav_daily":      {"zh": "日报",   "en": "Daily"},
    "nav_weekly":     {"zh": "周报",   "en": "Weekly"},
    "nav_open_db":    {"zh": "数据库", "en": "Database"},
    "nav_open_db_t":  {"zh": "在新标签页打开数据库", "en": "Open database in a new tab"},
    "daily_title":    {"zh": "每日 Report", "en": "Daily Report"},
    "weekly_title":   {"zh": "周报",   "en": "Weekly"},
    "no_date":        {"zh": "无日期", "en": "no date"},
    "lang_switch_t":  {"zh": "切到英文", "en": "Switch to Chinese"},

    # Weekday short labels (used in 7-col timeline)
    "wd_1": {"zh": "周一", "en": "Mon"},
    "wd_2": {"zh": "周二", "en": "Tue"},
    "wd_3": {"zh": "周三", "en": "Wed"},
    "wd_4": {"zh": "周四", "en": "Thu"},
    "wd_5": {"zh": "周五", "en": "Fri"},
    "wd_6": {"zh": "周六", "en": "Sat"},
    "wd_7": {"zh": "周日", "en": "Sun"},

    # Stats tiles (4-tile dashboard)
    "stat_events":         {"zh": "事件总数",   "en": "Events"},
    "stat_active":         {"zh": "活跃总时长", "en": "Active hours"},
    "stat_longest_focus":  {"zh": "最长专注",   "en": "Longest focus"},
    "stat_ai_cost":        {"zh": "AI 花费",     "en": "AI cost"},
    "stat_active_days":    {"zh": "活跃天数",   "en": "Active days"},
    "stat_span":           {"zh": "时间跨度",   "en": "Time span"},
    "stat_today_total":    {"zh": "当天累计",   "en": "today total"},
    "stat_week_total":     {"zh": "本周累计",   "en": "this week"},
    "stat_full_week":      {"zh": "全周累计",   "en": "weekly total"},
    "stat_estimated":      {"zh": "估算",       "en": "estimated"},
    "stat_full_attend":    {"zh": "满勤",       "en": "every day"},
    "stat_blank_days":     {"zh": "空白",       "en": "blank"},
    "stat_no_ai":          {"zh": "(未运行 AI)", "en": "(AI not run)"},
    "stat_switches":       {"zh": "切换 {n} 次", "en": "{n} switches"},

    # Insights card
    "insights_title":     {"zh": "Insights",        "en": "Insights"},
    "insights_hint":      {"zh": "悬停标题看每列在说什么", "en": "Hover a column header to see what it covers"},
    "insights_progress":  {"zh": "关键任务进展",    "en": "Task progress"},
    "insights_pattern":   {"zh": "时间安排回顾",    "en": "Time pattern"},
    "insights_followup":  {"zh": "任务跟进提醒",    "en": "Follow-ups"},
    "insights_none":      {"zh": "(无)",             "en": "(none)"},
    "tip_progress":  {"zh": "今天/本周在飞书任务上有哪些具体推进 —— 完成的、改的、提的代码。只看带任务标签的事件,零散杂活不放这里。",
                      "en": "Concrete progress on Feishu tasks — what was finished, changed, or shipped. Only events linked to a task; ad-hoc work is excluded."},
    "tip_pattern":   {"zh": "你今天的作息和最近 7 天平均比起来怎么样 —— 几点开工、几点收工、有没有大块专注、是不是切换太频繁。基于具体数字,不会写空话。",
                      "en": "How today's schedule compares to your 7-day baseline — start/end times, long focus blocks, context switching. Grounded in numbers, no fluff."},
    "tip_followup":  {"zh": "明天/下周该盯哪些任务 —— deadline 临近的、好几天没动的、还有未提交改动的。",
                      "en": "Tasks to watch tomorrow/next week — closing deadlines, idle for days, or with uncommitted changes."},

    # Trend closer (Report card)
    "trend_label":   {"zh": "变化趋势", "en": "Trend"},
    "trend_tip":     {"zh": "和昨天/上周比,你的工作重心和节奏整体往哪儿走。",
                      "en": "Where your focus and pace are heading compared to yesterday / last week."},

    # Daily timeline card
    "timeline_title":    {"zh": "每日时间轴",  "en": "Daily timeline"},
    "timeline_tag":      {"zh": "Daily",        "en": "Daily"},
    "timeline_hint":     {"zh": "本周 7 天叙事并列展示;点击星期头可以跳到当日完整 dashboard",
                          "en": "All 7 days side by side; click a weekday header to open that day's full dashboard"},
    "timeline_no_data":  {"zh": "(无数据)",    "en": "(no data)"},

    # Tasks panel
    "tasks_all":       {"zh": "全部",   "en": "All"},
    "tasks_table_t":   {"zh": "任务",   "en": "Tasks"},
    "tasks_table_r":   {"zh": "审稿",   "en": "Reviews"},
    "tasks_col_p":     {"zh": "P",      "en": "P"},
    "tasks_col_status":{"zh": "状态",   "en": "Status"},
    "tasks_col_title": {"zh": "任务",   "en": "Task"},
    "tasks_col_topic": {"zh": "题目",   "en": "Title"},
    "tasks_col_hours": {"zh": "时长",   "en": "Hours"},
    "tasks_col_events":{"zh": "事件",   "en": "Events"},
    "tasks_col_last":  {"zh": "最近活动","en": "Last activity"},
    "tasks_col_due":   {"zh": "截止",   "en": "Due"},
    "tasks_show_done": {"zh": "显示已完成", "en": "Show completed"},
    "status_doing":    {"zh": "进行中", "en": "In progress"},
    "status_todo":     {"zh": "待办",   "en": "To do"},
    "status_done":     {"zh": "完成",   "en": "Done"},

    # Audit panel
    "audit_title":    {"zh": "未匹配项目审计",      "en": "Unlinked project audit"},
    "audit_tag":      {"zh": "Audit",                "en": "Audit"},
    "audit_window":   {"zh": "窗口",                "en": "window"},
    "audit_summary":  {"zh": "{n} 个项目 / 共 {ev} 条事件未对应任务",
                        "en": "{n} projects / {ev} events with no task link"},
    "audit_pg":       {"zh": "project_guess",       "en": "project_guess"},
    "audit_events":   {"zh": "事件",                "en": "Events"},
    "audit_days":     {"zh": "活跃天数",            "en": "Active days"},
    "audit_last":     {"zh": "最近活动",            "en": "Last activity"},
    "audit_match":    {"zh": "对应任务",            "en": "Map to task"},
    "audit_skip":     {"zh": "— 跳过 / 无对应 —",  "en": "— Skip / no match —"},
    "audit_save_hint":{"zh": "保存后会写入 config/work_item_aliases.yaml 并立刻重建链接(历史报告已缓存的不变;下次 catchup / 刷新时按新规则统计)",
                       "en": "Saved picks go to config/work_item_aliases.yaml and links rebuild immediately (cached historical reports unchanged; next catchup / refresh applies new rules)"},
    "audit_save_btn": {"zh": "保存并重建链接",       "en": "Save & rebuild links"},
    "other_bucket":   {"zh": "其它",                  "en": "Other"},
    "no_dim_data":    {"zh": "本周该维度无可用数据",  "en": "No data for this dimension this week"},
    "expand_all":     {"zh": "全部展开",              "en": "Expand all"},
    "collapse_all":   {"zh": "全部收起",              "en": "Collapse all"},
    # Projects card (right-bottom on daily/weekly)
    "projects_title":  {"zh": "项目 · 任务",          "en": "Projects · Tasks"},
    "projects_tag":    {"zh": "Per-item",            "en": "Per-item"},
    "projects_hint":   {"zh": "今日各项目的简要进展; 详细见数据库逐项日报告", "en": "Per-project recap; full detail in the per-item DB view"},
    "projects_hint_w": {"zh": "本周各项目累计进展; 点项目名跳转过滤后的事件库", "en": "Per-project recap across the week"},
    "projects_none":   {"zh": "今天没有项目活动。",   "en": "No project activity today."},
    "projects_next":   {"zh": "下一步",               "en": "Next"},
    "audit_applied":  {"zh": "已保存:新增 {a} · 移除 {r} · 重建 {l} 条事件链接",
                       "en": "Saved: +{a} · −{r} · {l} event links rebuilt"},

    # Reports / sections
    "report_tag":     {"zh": "Report",  "en": "Report"},
    "no_data_lede":   {"zh": "这一天没有可分析事件。", "en": "No events to analyze for this day."},
    "no_data_hint":   {"zh": "可以切换到有数据的日期,或先运行 collector/import。",
                       "en": "Switch to a day with data, or run a collector/import first."},
    "no_ai_overview": {"zh": "(AI 速读未生成)",    "en": "(AI overview not generated)"},
    "ai_unavailable": {"zh": "(DEEPSEEK_API_KEY 未设置, 跳过 AI 速读)",
                       "en": "(DEEPSEEK_API_KEY not set; AI overview skipped)"},
    "ai_failed":      {"zh": "AI 调用失败",       "en": "AI call failed"},

    # Misc
    "time_ago_now":    {"zh": "刚刚",        "en": "just now"},
    "time_ago_min":    {"zh": "{n} 分钟前", "en": "{n} min ago"},
    "time_ago_hr":     {"zh": "{n} 小时前", "en": "{n}h ago"},
    "time_ago_day":    {"zh": "{n} 天前",   "en": "{n}d ago"},
    "untouched":       {"zh": "未触碰",       "en": "untouched"},

    # Dimension pills (DIMENSIONS list — what to stack the chart by)
    "dim_source":   {"zh": "来源",   "en": "Source"},
    "dim_project":  {"zh": "项目",   "en": "Project"},
    "dim_task":     {"zh": "任务",   "en": "Task"},
    "dim_device":   {"zh": "设备",   "en": "Device"},
    "dim_activity": {"zh": "活动",   "en": "Activity"},
    "dim_tooltip":  {"zh": "按哪个维度堆叠/上色", "en": "Which dimension to stack / color by"},
    "dim_label":    {"zh": "维度",   "en": "Dim"},

    # Unit pills (UNITS list)
    "unit_count":  {"zh": "条目", "en": "Events"},
    "unit_chars":  {"zh": "字数", "en": "Chars"},
    "unit_hours":  {"zh": "小时", "en": "Hours"},
    "unit_label":  {"zh": "单位", "en": "Unit"},

    # Chart panel — view-switcher pills
    "chart_view_hist": {"zh": "直方图", "en": "Histogram"},
    "chart_view_dist": {"zh": "分布",   "en": "Distribution"},
    "chart_panel_tag": {"zh": "Chart",  "en": "Chart"},
    "chart_per_hour":  {"zh": "每小时", "en": "Per hour"},
    "chart_per_day":   {"zh": "每天",   "en": "Per day"},

    # Tasks panel
    "tasks_panel_t":   {"zh": "工作项 · 任务",       "en": "Work items · Tasks"},
    "tasks_panel_r":   {"zh": "工作项 · 审稿",       "en": "Work items · Reviews"},
    "tasks_n_rows":    {"zh": "{n} 行",                "en": "{n} rows"},
    "tasks_p_zero":    {"zh": "{n} 个 P0/P1 本期零活动", "en": "{n} P0/P1 with zero activity"},
    "tasks_p_stale_tip":{"zh": "高优先级任务但本期零活动", "en": "High-priority task with no activity this period"},
    "tasks_filter":    {"zh": "筛选", "en": "Filter"},
    "tasks_select_all":{"zh": "全部", "en": "All"},

    # Weekly view-switcher pills
    "view_swim":    {"zh": "泳道",     "en": "Swimlane"},
    "view_heat":    {"zh": "热力图",   "en": "Heatmap"},

    # Due-date chip
    "due_overdue":  {"zh": "已过期 {n}d", "en": "{n}d overdue"},
    "due_urgent":   {"zh": "急 {n}d",      "en": "due in {n}d"},
    "due_tight":    {"zh": "紧 {n}d",      "en": "due in {n}d"},

    # Section headers
    "jump_to_day":  {"zh": "跳到每日报告", "en": "Jump to a day"},
    "n_days":       {"zh": "{n} 天",        "en": "{n} days"},

    # Misc UI bits
    "no_title":        {"zh": "(无标题)", "en": "(no title)"},
    "filter_all":      {"zh": "全部",      "en": "All"},
    "task_prefix":     {"zh": "任务",      "en": "Task"},
    "review_prefix":   {"zh": "审稿",      "en": "Review"},
    "open_full_day":   {"zh": "→ 当日完整 dashboard", "en": "→ Full day dashboard"},
    "events_table_t":  {"zh": "原始事件",  "en": "Events"},
    # ── /events database page ─────────────────────────────────────────────
    "db_tab_events":     {"zh": "原始事件",       "en": "Events"},
    "db_tab_day":        {"zh": "日报告",         "en": "Day report"},
    "db_tab_day_project":{"zh": "逐项日报告",     "en": "Per-item day report"},
    "db_col_time":       {"zh": "时间",           "en": "Time"},
    "db_col_source":     {"zh": "来源",           "en": "Source"},
    "db_col_activity":   {"zh": "活动",           "en": "Activity"},
    "db_col_location":   {"zh": "位置",           "en": "Location"},
    "db_col_project":    {"zh": "项目",           "en": "Project"},
    "db_col_title":      {"zh": "标题 / 内容",    "en": "Title / Content"},
    "db_col_rows":       {"zh": "行数",           "en": "Rows"},
    "db_search_ph":      {"zh": "搜标题或内容",   "en": "Search title/content"},
    "db_no_events":      {"zh": "暂无事件",       "en": "No events"},
    "db_clear":          {"zh": "✕ 清空筛选",     "en": "✕ Clear filters"},
    "db_clear_t":        {"zh": "清空所有筛选条件", "en": "Clear all filter conditions"},
    "db_clear_idle_t":   {"zh": "当前未设置任何筛选","en": "No filters set"},
    "db_filter_all":     {"zh": "全部",           "en": "All"},
    "db_date_start":     {"zh": "开始",           "en": "Start"},
    "db_date_end":       {"zh": "结束",           "en": "End"},
    "db_date_all":       {"zh": "全部日期",       "en": "All dates"},
    "db_date_same":      {"zh": "同一天",         "en": "Same day"},
    "db_date_no_end":    {"zh": "无终止",         "en": "No end"},
    "db_view_raw":       {"zh": "看原始事件 →",   "en": "View raw events →"},
    "db_empty_dp":       {"zh": "暂无 day_project_report 行", "en": "No per-item rows yet"},
    "db_empty_day":      {"zh": "暂无 day_report 行","en": "No day-report rows yet"},
    "db_reset_filters":  {"zh": "清空筛选",       "en": "Reset filters"},
    # /events?table=day_project page (rebranded "逐项日报告")
    "dp_label_date":     {"zh": "日期",     "en": "Date"},
    "dp_label_project":  {"zh": "项目",     "en": "Project"},
    "dp_label_status":   {"zh": "状态",     "en": "Status"},
    "dp_label_order":    {"zh": "排序",     "en": "Sort"},
    "dp_th_date":        {"zh": "日期",     "en": "Date"},
    "dp_th_project":     {"zh": "项目",     "en": "Project"},
    "dp_th_events":      {"zh": "事件",     "en": "Events"},
    "dp_th_active":      {"zh": "活跃时长", "en": "Active"},
    "dp_th_status":      {"zh": "状态",     "en": "Status"},
    "dp_th_summary":     {"zh": "概要 · 做了什么", "en": "Summary · what was done"},
    "dp_th_next":        {"zh": "下一步",   "en": "Next"},
    "dp_th_cont":        {"zh": "对比上次", "en": "vs prev"},
    "dp_th_project_task":{"zh": "项目 · 任务", "en": "Project · Tasks"},
    "dp_rowcount":       {"zh": "{n} 行",   "en": "{n} rows"},
    # additional table tabs (generic browser)
    "db_tab_work_items": {"zh": "飞书任务",      "en": "Feishu tasks"},
    "db_tab_links":      {"zh": "事件→任务",     "en": "Event→Task links"},
    "db_tab_labels":     {"zh": "活动标签",      "en": "Activity labels"},
    "db_tab_day_ch":     {"zh": "AI 每日缓存",   "en": "AI day cache"},
    "db_tab_day_proj_ch":{"zh": "AI 逐项缓存",   "en": "AI per-item cache"},
    "db_tab_devices":    {"zh": "设备",          "en": "Devices"},
    "db_tab_locations":  {"zh": "位置",          "en": "Locations"},
    "db_tab_sources":    {"zh": "来源",          "en": "Sources"},
    "db_group_core":     {"zh": "核心数据",      "en": "Core"},
    "db_group_advanced": {"zh": "高级 / 调试",   "en": "Advanced / Debug"},
    "db_more":           {"zh": "更多",          "en": "More"},
    "db_less":           {"zh": "收起",          "en": "Less"},
    "open_db_t":       {"zh": "在新标签页打开本日事件", "en": "Open today's events in a new tab"},
    "open_db_short":   {"zh": "打开数据库 ↗", "en": "Open database ↗"},
    "donut_sorted_by": {"zh": "按 {dim} 排序 · 共 {n} 项", "en": "Sorted by {dim} · {n} items"},
    "swim_help":       {"zh": "横轴 24h (shifted 边界 {hh}:00 起),hover 任意竖线看事件详情。上方筛选 pill 同时控制下面的热力图。",
                         "en": "24h x-axis (shifted boundary at {hh}:00). Hover any tick for event detail; the top filter pills also control the heatmap below."},
    "weekly_dim_intro":{"zh": "维度 · {label}", "en": "Dim · {label}"},
    "weekly_filter":   {"zh": "筛选",      "en": "Filter"},
    "tag_timeline":    {"zh": "Timeline",  "en": "Timeline"},
    "page_timeline":   {"zh": "时间线",    "en": "Timeline"},
    "page_chart":      {"zh": "Chart",     "en": "Chart"},
    "tasks_show_total":{"zh": "{n} 条", "en": "{n}"},
    "tasks_col_source":  {"zh": "源",          "en": "Source"},
    "tasks_col_progress":{"zh": "本期进展",    "en": "Latest progress"},
    "tasks_col_next":    {"zh": "下一步",      "en": "Next step"},
    "tasks_ai_details":  {"zh": "展开原文",    "en": "Details"},
    "tasks_ai_summary":  {"zh": "概要",        "en": "Summary"},
    "tasks_ai_done":     {"zh": "做了什么",    "en": "What was done"},
    "tasks_ai_status":   {"zh": "状态",        "en": "Status"},
    "tasks_ai_next":     {"zh": "下一步",      "en": "Next steps"},
}


import contextvars as _contextvars
_CURRENT_LANG: _contextvars.ContextVar[str] = _contextvars.ContextVar(
    "daytrace_lang", default="zh"
)


def _load_strings_from_yaml() -> None:
    """Merge `config/strings.yaml` over the in-code `_STRINGS` defaults.

    Lets users override any UI label without forking the code, and lets
    them add languages beyond zh/en by extending the YAML. Silently
    no-ops when the file is missing or PyYAML isn't installed."""
    try:
        import yaml as _y
        from pathlib import Path as _P
        path = _P(__file__).resolve().parents[1] / "config" / "strings.yaml"
        if not path.exists():
            return
        data = _y.safe_load(path.read_text(encoding="utf-8")) or {}
        for k, langs in data.items():
            if not isinstance(langs, dict):
                continue
            existing = _STRINGS.setdefault(k, {})
            for lang_code, value in langs.items():
                if isinstance(value, str):
                    existing[lang_code] = value
    except Exception as e:
        print(f"[i18n] failed to load config/strings.yaml: {e}")


# ───── System-wide language configuration (config/i18n.yaml) ────────────
# Drives the language switcher, AI prompts, and fallback behavior. Edit
# the YAML to change supported languages; everything reads from here.

_I18N_CONFIG: dict = {
    "languages": ["zh", "en"],
    "fallback":  "en",
    "default":   "zh",
}


def _load_i18n_config() -> None:
    """Merge `config/i18n.yaml` over the in-code defaults."""
    try:
        import yaml as _y
        from pathlib import Path as _P
        path = _P(__file__).resolve().parents[1] / "config" / "i18n.yaml"
        if not path.exists():
            return
        data = _y.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(data.get("languages"), list):
            _I18N_CONFIG["languages"] = [str(x) for x in data["languages"]]
        if isinstance(data.get("fallback"), str):
            _I18N_CONFIG["fallback"] = data["fallback"]
        if isinstance(data.get("default"), str):
            _I18N_CONFIG["default"] = data["default"]
    except Exception as e:
        print(f"[i18n] failed to load config/i18n.yaml: {e}")


_load_i18n_config()


def supported_languages() -> list[str]:
    """Public accessor for the language list — used by the switcher,
    secrets export, AI prompt builders, etc."""
    return list(_I18N_CONFIG.get("languages", ["zh", "en"]))


def default_language() -> str:
    return _I18N_CONFIG.get("default", "zh")


def fallback_language() -> str:
    return _I18N_CONFIG.get("fallback", "en")


# Merge config/strings.yaml over the hard-coded defaults at import time.
# Users can extend or override any label without touching the source file
# (and add new languages by adding extra keys per entry).
_load_strings_from_yaml()


def T(key: str, lang: str | None = None, **fmt) -> str:
    """Translate a key for the given language.

    Resolution order:
      1. exact match for `lang`
      2. configured fallback language (config/i18n.yaml `fallback:`)
      3. any non-empty value the key has (so a partially-translated
         entry never crashes the render)
      4. the key itself (rather than the empty string — easier to spot
         a missing key in the rendered HTML).

    Reads the per-request ContextVar when `lang` is None."""
    if lang is None:
        lang = _CURRENT_LANG.get()
    entry = _STRINGS.get(key)
    if entry is None:
        return key
    val = entry.get(lang) or entry.get(fallback_language())
    if not val:
        for v in entry.values():
            if v:
                val = v
                break
    val = val or key
    return val.format(**fmt) if fmt else val


def L(value, lang: str | None = None) -> str:
    """Pick the best available language from an AI bilingual value.

    The AI payloads are now {"zh": "...", "en": "..."} dicts in every
    text field (v14). Legacy v6-v13 cache rows pass through the
    `_bilingual_str` normalizer first, which wraps plain strings as
    {"zh": str, "en": ""}. This helper pulls the right side out for
    the current request language, falling back to whatever non-empty
    value is available."""
    if lang is None:
        lang = _CURRENT_LANG.get()
    if isinstance(value, dict):
        v = (value.get(lang) or "").strip()
        if v:
            return v
        # Fallback to the other language
        other = "en" if lang == "zh" else "zh"
        return (value.get(other) or "").strip()
    if isinstance(value, str):
        return value
    return ""


def _lang_from_request(handler) -> str:
    """Parse the daytrace_lang cookie. Accepts any language declared in
    config/i18n.yaml; falls back to the configured default. The cookie
    value is forced to lower-case so case-insensitive matches work."""
    supported = supported_languages()
    raw = (handler.headers.get("Cookie") or "")
    for piece in raw.split(";"):
        p = piece.strip()
        if p.startswith("daytrace_lang="):
            v = p.split("=", 1)[1].strip().lower()
            if v in supported:
                return v
    return default_language()

STYLE = """
:root { color-scheme: light; --bg:#f7f5ef; --card:#fffaf0; --ink:#202124; --muted:#6b645c; --line:#e7dfd0; --accent:#2f6fed; --purple:#7b61ff; --green:#16a34a; --orange:#f59e0b; --red:#ef4444; }
* { box-sizing: border-box; }
body { margin:0; font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:radial-gradient(circle at top left,#fff7df 0,#f8f5ee 35%,#f4efe5 100%); color:var(--ink); }
body.events-page { height:100vh; overflow:hidden; }
header { padding:8px 18px; border-bottom:1px solid var(--line); background:rgba(255,250,240,.94); position:sticky; top:0; backdrop-filter: blur(10px); z-index:5; display:grid; grid-template-columns:auto auto 1fr auto; gap:12px; align-items:center; min-height:50px; }
.header-spacer { /* 1fr eater so right-rail right-aligns */ }
/* Right-rail nav: page toggle + db btn + lang toggle, kept on one row. */
.page-nav { display:inline-flex; align-items:center; gap:8px; flex-wrap:nowrap; }
/* Unified segmented control — same chrome as .dim-tabs / .unit-tabs
   so every toggle on the page (来源/项目/任务, 小时/条目/字数, 日报/周报,
   中/EN, Expand/Collapse) reads as the SAME widget. */
.page-toggle { display:inline-flex; align-items:center; gap:4px; background:rgba(255,250,240,.94); border:1px solid var(--line); border-radius:999px; padding:3px; box-shadow:0 4px 10px rgba(65,45,10,.04); }
.page-toggle-pill { display:inline-flex; align-items:center; font-size:12.5px; padding:4px 14px; border-radius:999px; border:none; background:transparent; color:#3b352e; font-weight:650; cursor:pointer; transition:background .12s, color .12s; text-decoration:none; }
.page-toggle-pill:hover { background:rgba(0,0,0,.04); }
.page-toggle-pill.active { background:var(--ink); color:white; }
/* Database button — matches the toggle containers so the right rail
   reads as one consistent cluster, not a white pill jammed between
   two cream segmented controls. */
.page-db-btn { display:inline-flex; align-items:center; height:30px; padding:0 14px; border:1px solid var(--line); background:rgba(255,250,240,.94); border-radius:999px; font-size:13px; font-weight:650; color:var(--ink); text-decoration:none; box-shadow:0 4px 10px rgba(65,45,10,.04); }
.page-db-btn:hover { background:#fdf6e3; }
/* Language toggle — segmented control with two pills. Mirrors the look
   of the dim-tabs / unit-tabs elsewhere in the dashboard so it reads as
   a deliberate switch rather than a stray button. */
.page-lang-toggle { display:inline-flex; align-items:center; gap:4px; background:rgba(255,250,240,.94); border:1px solid var(--line); border-radius:999px; padding:3px; box-shadow:0 4px 10px rgba(65,45,10,.04); }
.page-lang-toggle .lang-opt { display:inline-flex; align-items:center; justify-content:center; min-width:30px; padding:4px 12px; border-radius:999px; border:none; background:transparent; font-size:12.5px; font-weight:650; color:#3b352e; text-decoration:none; cursor:pointer; transition:background .12s, color .12s; }
.page-lang-toggle .lang-opt:hover { background:rgba(0,0,0,.04); }
.page-lang-toggle .lang-opt.active { background:var(--ink); color:white; }
h1 { margin:0; font-size:20px; letter-spacing:-0.03em; white-space:nowrap; }.sub { color:var(--muted); font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
nav { display:flex; gap:6px; flex-wrap:nowrap; justify-content:flex-end; justify-self:end; margin-left:auto; } nav a { padding:5px 9px; border:1px solid var(--line); border-radius:999px; background:rgba(255,250,240,.94); color:#3b352e; font-weight:650; font-size:13px; white-space:nowrap; } nav a:hover { background:#fff7e8; } nav a.active { background:var(--ink); color:white; border-color:var(--ink); }
/* Cap reading width on big screens so narrative + cards don't span
   ultrawide / 27" displays (line length stays under ~90ch). Below
   the cap we keep fluid 18px side padding so 13" laptops still
   breathe. The events database page is a wide table that genuinely
   needs the full viewport — opt it out via body.events-page. */
main { padding:12px 18px 28px; max-width:1480px; margin:0 auto; min-height:calc(100vh - 51px); width:100%; }
/* Header stays full-width (so its sticky cream backdrop spans the
   viewport) but its grid items are squeezed by side padding equal to
   `(100vw - 1480px) / 2` on big screens — capped at 200px so 1900+px
   screens align nicely with main without leaving a ridiculously wide
   gap. clamp() = (min ceiling, fluid, max ceiling). */
header { padding-left:max(18px, calc((100vw - 1480px) / 2));
         padding-right:max(18px, calc((100vw - 1480px) / 2)); }
body.events-page main { max-width:none; }
body.events-page header { padding-left:18px; padding-right:18px; }
body.events-page main { height:calc(100vh - 51px); min-height:0; overflow:hidden; padding-bottom:12px; }
body.events-page form { height:100%; }
.grid { display:grid; grid-template-columns: repeat(4, minmax(150px,1fr)); gap:10px; }.section-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }.three-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }.report-grid { display:grid; grid-template-columns:minmax(320px,1fr) minmax(320px,1fr); gap:12px; align-items:stretch; }
.left-column { display:flex; flex-direction:column; gap:12px; min-width:0; }
.right-column { display:flex; flex-direction:column; gap:12px; min-width:0; }
.highlights-card .dr-grid { display:grid; grid-template-columns:1fr 1fr; gap:18px; margin:0; }
@media (max-width:900px) { .highlights-card .dr-grid { grid-template-columns:1fr; } }
/* Global controls (day nav + 5-dim selector) — sticks under the page header
   so they're always reachable while scrolling through the daily report
   and project cards. */
.dim-bar { display:flex; justify-content:space-between; align-items:center; gap:12px; margin:0 -18px 12px; padding:8px 18px; flex-wrap:wrap; position:sticky; top:50px; z-index:4; background:rgba(247,245,239,.92); backdrop-filter:blur(10px); border-bottom:1px solid var(--line); }
/* Inline controls in the sticky header (used by /today + /weekly so the
   prev/next nav + date picker + dim pills all sit on one row). */
.header-controls { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.header-controls .dim-tabs { background:rgba(255,250,240,.94); }
/* Arrow nav buttons (← / →) and the open-db link share the same pill look
   as the date picker so the header row reads cleanly. */
.hdr-nav-btn { display:inline-flex; align-items:center; justify-content:center; min-width:30px; height:30px; padding:0 8px; border:1px solid var(--line); background:rgba(255,250,240,.94); border-radius:8px; font-size:14px; font-weight:700; color:var(--ink); cursor:pointer; }
.hdr-nav-btn:hover { background:#fff7e8; }
.hdr-open-db { display:inline-flex; align-items:center; height:30px; padding:0 10px; border:1px solid var(--line); background:rgba(255,250,240,.94); border-radius:8px; font-size:12px; font-weight:650; color:var(--ink); }
.hdr-open-db:hover { background:#fff7e8; }
.dim-bar .day-nav { margin-top:0; padding-top:0; }
.dim-bar-right { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
.dim-tabs, .unit-tabs { display:flex; gap:4px; background:rgba(255,250,240,.94); border:1px solid var(--line); border-radius:999px; padding:3px; box-shadow:0 4px 10px rgba(65,45,10,.04); }
.dim-tab, .unit-tab { font-size:12.5px; padding:4px 14px; border-radius:999px; border:none; background:transparent; color:#3b352e; font-weight:650; cursor:pointer; transition:background .12s, color .12s; }
.dim-tab:hover, .unit-tab:hover { background:rgba(0,0,0,.04); }
.dim-tab.active, .unit-tab.active { background:var(--ink); color:white; }.analysis-grid { display:grid; grid-template-columns:repeat(2,minmax(260px,1fr)); gap:12px; }.wide-card { grid-column:1 / -1; }
/* Tasks panels: 任务 + 审稿 side-by-side (2 cols), collapses to 1 col
   when narrow or when the toggle picks a single table. */
.day-jumps { display:flex; flex-wrap:wrap; gap:6px; }
.day-jump { display:inline-flex; align-items:center; padding:4px 10px; border:1px solid var(--line); background:rgba(255,250,240,.94); border-radius:999px; font-size:12px; font-weight:600; color:var(--ink); text-decoration:none; white-space:nowrap; box-shadow:inset 0 -1px 0 rgba(0,0,0,.03); }
.day-jump:hover { background:#fff7e8; }
.day-jump:hover { background:#fdf6e3; }
.tasks-grid { display:grid; grid-template-columns: 1fr 1fr; gap:12px; align-items:start; }
@media (max-width:1100px) { .tasks-grid { grid-template-columns: 1fr; } }
/* Two display modes:
   - compact (default, two cards side-by-side): hide 事件 + 最近活动 to free
     room for title / 时长 / 截止. Toggled by the [全部] pill.
   - full (one card alone, single column): show all columns.
   JS sets data-display-mode="full" on .tasks-grid when toggle picks single. */
.tasks-grid:not([data-display-mode="full"]) .tasks-card .col-events,
.tasks-grid:not([data-display-mode="full"]) .tasks-card .col-last { display: none; }
.tasks-card table.mini-table th,
.tasks-card table.mini-table td { vertical-align: top; padding:6px 6px; }
.tasks-card table.mini-table td:not(.tasks-title-cell),
.tasks-card table.mini-table th:not([data-sort="title"]) {
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.tasks-card .tasks-title-cell { word-break: break-word; white-space: normal; }
/* Full mode (single-table view via the 全部/任务/审稿 toggle): the table
   spans the page. With table-layout:fixed + width:100% the title col
   gets a percentage so it stays sensible no matter how wide the page
   is; data columns get pixel widths sized for their longest typical
   value. */
.tasks-grid[data-display-mode="full"] .tasks-card table.mini-table { table-layout: fixed; }
.tasks-grid[data-display-mode="full"] .tasks-card col[data-col="priority"] { width: 60px; }
.tasks-grid[data-display-mode="full"] .tasks-card col[data-col="status"]   { width: 88px; }
.tasks-grid[data-display-mode="full"] .tasks-card col[data-col="title"]    { width: 42%; }
.tasks-grid[data-display-mode="full"] .tasks-card col[data-col="hours"]    { width: 90px; }
.tasks-grid[data-display-mode="full"] .tasks-card col[data-col="events"]   { width: 84px; }
.tasks-grid[data-display-mode="full"] .tasks-card col[data-col="last"]     { width: 140px; }
.tasks-grid[data-display-mode="full"] .tasks-card col[data-col="due"]      { width: 150px; }
/* Audit table — consistent alignment + restrained styling.
   project_guess monospace (it's a raw identifier), numeric cols
   tabular + right-aligned, time/select left-aligned. Padding picks
   up from .mini-table; here we just standardize alignment + look. */
.audit-table th { padding:8px 10px; font-size:12.5px; font-weight:700; color:#6b6052; letter-spacing:0.04em; text-transform:uppercase; }
.audit-table td { padding:8px 10px; vertical-align:middle; font-size:13px; }
.audit-table th.audit-pg,   .audit-table td.audit-pg   { text-align:left; }
.audit-table th.audit-num,  .audit-table td.audit-num  { text-align:right; font-variant-numeric:tabular-nums; }
.audit-table th.audit-time, .audit-table td.audit-time { text-align:left; }
.audit-table td.audit-pg   { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size:12.5px; }
.audit-table td.audit-num  { font-weight:700; }
.audit-table td.muted      { color:var(--muted); font-weight:500; }
/* Keep the audit <select> on white so the interactive picker reads
   distinctly from the cream surface around it. */
.audit-table .audit-select { width:100%; max-width:100%; padding:5px 10px; border:1px solid var(--line); border-radius:8px; background-color:#fff; font-size:13px; color:var(--ink); }
.audit-table .audit-select:hover { background-color:#fafafa; }
.audit-table .audit-select:focus { outline:2px solid rgba(47,111,237,.35); outline-offset:1px; }
/* Weekly view-switcher card: only the active view's pane is visible.
   Toggling .weekly-viz[data-view] flips visibility with no reload (no scroll jump). */
.weekly-viz .wv-pane { display:none; }
.weekly-viz[data-view="swim"] .wv-pane[data-pane="swim"] { display:block; }
.weekly-viz[data-view="heat"] .wv-pane[data-pane="heat"] { display:block; }
/* Weekly top-chart card: histogram vs promoted-legend distribution view. */
.top-chart-card .tc-pane { display:none; }
.top-chart-card[data-tc-view="chart"] .tc-pane[data-pane="chart"] { display:block; }
.top-chart-card[data-tc-view="dist"]  .tc-pane[data-pane="dist"]  { display:block; }
.card { background:rgba(255,250,240,.94); border:1px solid var(--line); border-radius:14px; padding:14px 16px; box-shadow:0 8px 18px rgba(65,45,10,.05); }
/* Unified card header — single source of truth for every panel head:
   .card-title + optional .card-tag chip + .card-hint + .card-right
   slot for controls. See _card_head() helper. */
.card-head { display:flex; align-items:baseline; gap:10px; margin-bottom:12px; flex-wrap:wrap; }
.card-title { margin:0; font-size:17px; font-weight:700; color:var(--ink); line-height:1.3; }
.card-tag { display:inline-flex; font-size:11.5px; font-weight:650; padding:2px 10px; border-radius:999px; white-space:nowrap; }
.card-tag-narrative { background:rgba(123,97,255,0.14); color:#7b61ff; }
.card-tag-data      { background:rgba(47,111,237,0.12); color:#2f6fed; }
.card-tag-config    { background:rgba(245,158,11,0.16); color:#a06800; }
.card-tag-item      { background:rgba(22,163,74,0.14);  color:#16a34a; }
.card-hint { color:var(--muted); font-size:12.5px; }
.card-right { margin-left:auto; display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
.metric { font-size:26px; font-weight:850; letter-spacing:-0.04em; }.metric-small { font-size:18px; font-weight:850; }.label { color:var(--muted); margin-top:3px; font-size:12px; } section { margin-top:12px; } h2 { font-size:16px; margin:0 0 8px; } h3 { margin:0 0 5px; font-size:14px; }
.bar { display:flex; align-items:center; gap:10px; margin:9px 0; }.bar-name { width:170px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:14px; }.bar-track { flex:1; height:10px; border-radius:999px; background:#ece3d2; overflow:hidden; }.bar-fill { height:100%; background:linear-gradient(90deg,#2f6fed,#7b61ff); border-radius:999px; }.bar-count { width:42px; text-align:right; color:var(--muted); font-variant-numeric:tabular-nums; }
.filters { display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin:0 0 8px; } input,select,button { border:1px solid var(--line); background:rgba(255,250,240,.94); border-radius:8px; padding:5px 7px; font:inherit; font-size:12px; color:var(--ink); } button { background:var(--accent); color:white; border-color:var(--accent); cursor:pointer; }.checkbox { display:flex; gap:4px; align-items:center; }
.bucket-head { display:flex; gap:10px; align-items:center; justify-content:space-between; margin-bottom:8px; }.mini-event { padding:8px 0; border-top:1px dashed #eadfcd; }.mini-event:first-of-type { border-top:none; }.event-title { font-weight:700; }.muted { color:var(--muted); }.pills { display:flex; flex-wrap:wrap; gap:6px; }.tag { display:inline-flex; max-width:100%; border-radius:999px; padding:2px 8px; background:#ebe6ff; color:#4632a8; font-size:12px; font-weight:650; overflow:hidden; text-overflow:ellipsis; }.source { background:#e8f0ff; color:#174ea6; }.device { background:#dcfce7; color:#166534; }.location { background:#ffedd5; color:#9a3412; }.low { background:#fff3cd; color:#8a5a00; }
.daily-report { line-height:1.55; display:flex; flex-direction:column; }.daily-report ul { margin:8px 0 0 18px; padding:0; }.daily-report li { margin:5px 0; }.report-lede { font-size:15px; color:#362f27; margin:0 0 8px; }.day-nav { display:flex; gap:8px; flex-wrap:wrap; margin-top:auto; padding-top:14px; }.day-nav a { border:1px solid var(--line); background:rgba(255,250,240,.94); border-radius:999px; padding:4px 9px; font-size:12px; font-weight:700; }
.donut-row { display:grid; grid-template-columns:120px 1fr; gap:12px; align-items:center; }.donut { width:112px; height:112px; border-radius:50%; display:grid; place-items:center; background:conic-gradient(var(--accent) 0 40%, var(--purple) 40% 70%, var(--orange) 70% 88%, #cbd5e1 88% 100%); }.donut:after { content:attr(data-label); width:70px; height:70px; border-radius:50%; background:var(--card); display:grid; place-items:center; font-size:13px; font-weight:800; color:var(--muted); text-align:center; }.legend-dot { width:9px; height:9px; display:inline-block; border-radius:50%; margin-right:6px; }.stack { display:flex; height:18px; overflow:hidden; border-radius:999px; background:#ece3d2; border:1px solid #e2d6c4; }.stack-seg { min-width:2px; height:100%; }.mini-table { width:100%; border-collapse:separate; border-spacing:0; }.mini-table th,.mini-table td { font-size:12px; padding:6px 4px; border-bottom:1px solid #eadfcd; }.mini-table th { position:static; background:transparent; box-shadow:none; color:var(--muted); }.composition-card .cc-tabs { display:flex; gap:4px; flex-wrap:wrap; }
.composition-card .cc-tab { font-size:12px; padding:3px 10px; border-radius:999px; border:1px solid var(--line); background:rgba(255,250,240,.94); color:#3b352e; font-weight:650; cursor:pointer; }
.composition-card .cc-tab:hover { background:#fff7e8; }
.composition-card .cc-tab.active { background:var(--ink); color:white; border-color:var(--ink); }
.composition-card { display:flex; flex-direction:column; }
.composition-card .cc-pane { display:none; flex:1; }
.composition-card .cc-pane.show { display:flex; }
.composition-card .cc-pane-body { display:grid; grid-template-columns:minmax(180px,.85fr) minmax(220px,1.15fr); gap:20px; align-items:center; width:100%; }
.composition-card .cc-pane-empty { place-items:center; }
.composition-card .cc-donut-wrap, .top-chart-card .cc-donut-wrap { display:flex; justify-content:center; align-items:center; padding:4px; }
.composition-card .cc-donut, .top-chart-card .cc-donut { width:210px; height:210px; border-radius:50%; display:grid; place-items:center; box-shadow:0 8px 18px rgba(40,30,10,.10); position:relative; }
.composition-card .cc-donut-hole, .top-chart-card .cc-donut-hole { width:124px; height:124px; border-radius:50%; background:var(--card); display:grid; place-items:center; text-align:center; box-shadow:inset 0 1px 2px rgba(0,0,0,.05); }
.composition-card .cc-donut-total, .top-chart-card .cc-donut-total { font-size:28px; font-weight:800; color:var(--ink); font-variant-numeric:tabular-nums; line-height:1; }
.composition-card .cc-donut-label, .top-chart-card .cc-donut-label { font-size:11px; color:var(--muted); margin-top:3px; letter-spacing:.06em; text-transform:uppercase; }
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
.dr-filter-strip select { border:1px solid var(--line); border-radius:8px; padding:3px 6px; background:rgba(255,250,240,.94); font:inherit; font-size:12px; color:var(--ink); }
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
/* Section divider used inside the Report panel — visually subtle so the
   factual + AI sections (Dashboard / 总览 / 趋势 / 推荐) read as one card. */
/* Minimal section separator inside the Report panel: a thin dashed line.
   No labels, no header chrome — sections speak for themselves. */
.dr-sep { border:0; border-top:1px dashed #eadfcd; margin:14px 0 12px; height:0; }
.dr-trend { display:flex; align-items:center; gap:10px; font-size:13px; line-height:1.55; padding:6px 12px; background:#fff7e8; border:1px solid #f0d68b; border-radius:10px; margin-bottom:8px; }
.dr-trend-text { color:#362f27; }
/* Slim trend closer sitting under the narrative in the Report card —
   inline label + chip + 1 sentence, single row, no box; thin top border
   hands off visually from the narrative paragraph above. */
.dr-trend-closer { display:flex; align-items:center; gap:10px; flex-wrap:wrap; font-size:13px; line-height:1.55; padding-top:10px; margin-top:6px; border-top:1px dashed #eadfcd; cursor:help; }
.dr-trend-closer .trend-label { font-size:11px; font-weight:700; color:var(--muted); letter-spacing:.06em; padding:2px 8px; border-radius:999px; background:#f3ecd9; }
.dr-grid { display:grid; grid-template-columns:1fr 1fr; gap:18px; margin:0 0 12px; }
@media (max-width:900px) { .dr-grid { grid-template-columns:1fr; } }
.dr-section h4 { margin:0 0 6px; font-size:12.5px; font-weight:700; color:#4d4438; letter-spacing:.04em; }
/* Insights card — full-width row below Report+Chart with 3 strictly
   parallel columns (变化趋势 / 关键进展 / 建议). The three columns share
   identical structure (title + bullet list) so they read as peers; thin
   vertical dashed rules separate them. Collapses to 1 col on narrow. */
.insights-card { margin-top:12px; }
/* Insights card: full-width 3-column layout (rolled back to pre-v17).
   Per-project AI now lives inside the Tasks table (Latest/Next cols),
   so Insights again has space for all 3 themes. */
.insights-grid { display:grid; grid-template-columns:1fr 1fr 1fr; gap:0; align-items:start; }
.insights-col { padding:0 18px; border-left:1px dashed #eadfcd; min-width:0; }
.insights-col:first-child { padding-left:0; border-left:none; }
.insights-col:last-child  { padding-right:0; }
.insights-col h4 { margin:0 0 10px; font-size:14px; font-weight:700; color:#4d4438; letter-spacing:.02em; cursor:help; }
.insights-col ul { margin:0; padding-left:20px; }
.insights-col ul li { font-size:14.5px; line-height:1.65; color:#362f27; margin-bottom:8px; }
.insights-col ul li:last-child { margin-bottom:0; }
.insights-col .muted { font-size:13.5px; }
/* Tasks table — AI summary cells. Compact by default; click to unfold
   the original AI payload fields inside the cell. */
.col-ai-progress, .col-ai-next { font-size:12px; line-height:1.4; color:#3b352e; vertical-align:top; padding-top:8px; }
.ai-cell-text { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.task-row.expandable { cursor:pointer; }
.task-row.expandable .tasks-title-cell::after {
  content:"▾"; color:var(--muted); font-size:10px; margin-left:8px;
}
.task-row.expandable.row-expanded .tasks-title-cell::after { content:"▴"; }
.task-ai-row[hidden] { display:none; }
.task-ai-row td {
  padding:0 0 12px 0; border-top:none;
}
.task-ai-card {
  margin:0 0 0 312px; padding:10px 12px;
  border:1px dashed var(--line); border-radius:8px;
  background:rgba(255,255,255,.38);
  display:grid; grid-template-columns:1fr 1fr; gap:14px;
}
.ai-cell-expanded {
  color:#4d463d;
}
.ai-cell-expanded .ai-section { margin-top:6px; }
.ai-cell-expanded .ai-section:first-child { margin-top:0; }
.ai-cell-expanded .ai-label {
  display:block; color:var(--muted); font-size:10.5px; font-weight:700;
  margin-bottom:2px;
}
.ai-cell-expanded ul { margin:3px 0 0 16px; padding:0; }
.ai-cell-expanded li { margin:2px 0; }
@media (max-width: 980px) {
  .task-ai-card { margin-left:0; grid-template-columns:1fr; }
}
/* Projects card CSS removed — per-project AI now lives as Latest /
   Next columns inside the unified Tasks table (see .col-ai-progress). */
/* Daily timeline card — 7 cols side by side, each a <details> that
   shows weekday + headline by default and unfolds the narrative on
   click. Visual style:
     • transparent background (column blends into the parent card)
     • top accent line (per-weekday color) — only structure cue
     • narrative blockquote stays cream like the rest of the page */
.daily-timeline-card { margin-top:12px; }
/* dt-bulk uses the unified .dim-tabs chrome — cream outer pill,
   transparent inner buttons. Active pill = the current state (default
   all-expanded since columns render <details open>); flips when the
   user clicks the other button. */
.daily-timeline-card .dt-bulk { display:inline-flex; align-items:center; gap:4px; background:rgba(255,250,240,.94); border:1px solid var(--line); border-radius:999px; padding:3px; box-shadow:0 4px 10px rgba(65,45,10,.04); }
.daily-timeline-card .dt-bulk-btn { font-size:12.5px; padding:4px 12px; border-radius:999px; border:none; background:transparent; color:#3b352e; font-weight:650; cursor:pointer; font-family:inherit; transition:background .12s, color .12s; }
.daily-timeline-card .dt-bulk-btn:hover { background:rgba(0,0,0,.04); }
.daily-timeline-card .dt-bulk-btn.active { background:var(--ink); color:white; }
.dt-grid { display:grid; grid-template-columns:repeat(7, 1fr); gap:6px; align-items:start; }
.dt-col { background:transparent; padding:10px 10px 12px; border-top:2px solid #f0d68b; min-width:0; }
.dt-col[open] { background:rgba(255,247,232,0.5); border-radius:0 0 8px 8px; }
.dt-col.dt-empty { opacity:0.55; }
.dt-col:nth-child(3n+1) { border-top-color:#f59e0b; }
.dt-col:nth-child(3n+2) { border-top-color:#2f6fed; }
.dt-col:nth-child(3n)   { border-top-color:#16a34a; }
/* Per-column <details> is driven exclusively by the global
   Expand-all / Collapse-all buttons in the timeline header — clicking
   on a column header should not toggle its open state. */
.dt-col summary { list-style:none; cursor:default; margin:0; padding:0; pointer-events:none; }
.dt-col summary::-webkit-details-marker { display:none; }
.dt-col-head { display:flex; align-items:baseline; gap:6px; margin-bottom:8px; }
.dt-day-name { font-weight:700; font-size:14px; color:#1a1814; }
.dt-date { font-size:11.5px; color:var(--muted); font-variant-numeric:tabular-nums; }
.dt-headline { font-size:13px; font-weight:700; color:#1a1814; line-height:1.45; cursor:pointer; }
/* hover-colorize disabled — clicks no longer toggle. */
.dt-headline.muted { color:var(--muted); font-weight:500; font-style:italic; cursor:default; }
.dt-body { margin-top:10px; padding-top:10px; border-top:1px dashed var(--line); }
.dt-narrative { font-size:12.5px; line-height:1.65; color:#4d4438; margin:0 0 8px; word-break:break-word; }
.dt-day-link { display:inline-block; font-size:11.5px; color:#2f6fed; text-decoration:none; font-weight:600; }
.dt-day-link:hover { text-decoration:underline; }
@media (max-width:1100px) { .dt-grid { grid-template-columns:repeat(4, 1fr); } }
@media (max-width:720px)  { .dt-grid { grid-template-columns:repeat(2, 1fr); } }
@media (max-width:480px)  { .dt-grid { grid-template-columns:1fr; } }
@media (max-width:900px) {
  .insights-grid { grid-template-columns:1fr; gap:14px; }
  .insights-col { padding:0; border-left:none; border-top:1px dashed #eadfcd; padding-top:12px; }
  .insights-col:first-child { border-top:none; padding-top:0; }
}
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
.dpr-table .col-project-task { width:240px; }
.dpr-table .col-num { width:60px; text-align:right; font-variant-numeric:tabular-nums; }
.dpr-table .col-share { width:120px; }
.dpr-table .col-status { width:90px; }
.dpr-table .col-ai { min-width:240px; max-width:380px; }
.dpr-table .col-cont { width:170px; }
.dpr-table .col-titles { width:220px; }
.dpr-table .col-meta { width:160px; }
.project-task-cell { display:flex; flex-direction:column; gap:6px; }
.task-chip-row { display:flex; flex-wrap:wrap; gap:4px; }
.task-chip { display:inline-flex; max-width:220px; padding:1px 8px; border-radius:999px; background:#e8f0ff; color:#174ea6; font-size:11px; font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.task-chip.muted { background:#f0ebe0; color:var(--muted); font-weight:500; }
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
.table-switcher-wrap { display:flex; flex-direction:column; gap:6px; margin:0 0 12px; }
.table-switcher { display:inline-flex; gap:4px; padding:3px; background:rgba(255,250,240,.94); border:1px solid var(--line); border-radius:999px; box-shadow:0 4px 10px rgba(65,45,10,.04); flex-wrap:wrap; align-self:flex-start; }
.table-tab { font-size:12.5px; padding:5px 14px; border-radius:999px; border:none; background:transparent; color:#3b352e; font-weight:650; text-decoration:none; transition:background .12s, color .12s; }
.table-tab:hover { background:rgba(0,0,0,.04); }
.table-tab.active { background:var(--ink); color:white; }
/* Vertical divider between primary tabs and the "更多" toggle in the
   single-row table switcher. Stays in the flex flow so it wraps with
   the pills on narrow viewports. */
.table-switcher-divider { display:inline-block; width:1px; align-self:stretch; margin:2px 4px; background:var(--line); }
/* "更多 ▾" toggle: same pill chrome as .table-tab but with a small caret. */
.table-more-toggle { cursor:pointer; font-family:inherit; display:inline-flex; align-items:center; gap:4px; }
.table-more-toggle .tmt-caret { font-size:9px; opacity:.6; }
.table-more-toggle[aria-expanded="true"] { background:rgba(0,0,0,.05); }
/* Inline expansion bucket: hidden by default, becomes inline-flex when
   the user reveals it. `hidden` attribute alone gives display:none which
   would break the flex flow, so be explicit. */
.table-switcher-extras { display:inline-flex; gap:4px; flex-wrap:wrap; }
.table-switcher-extras[hidden] { display:none; }

/* generic table view (any DB table) */
.generic-table { table-layout:fixed; width:100%; }
.generic-table th, .generic-table td { padding:6px 9px; font-size:12.5px; border-bottom:1px solid #eadfcd; vertical-align:top; }
.generic-table th { background:#fff7e8; color:#4d4438; font-weight:700; font-size:11.5px; letter-spacing:.03em; text-transform:uppercase; position:sticky; top:0; box-shadow:0 1px 0 var(--line); }
.generic-table td.mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:11.5px; color:#4d4438; }
.generic-table td.muted { color:var(--muted); }
.generic-table td.num { text-align:right; font-variant-numeric:tabular-nums; }
.generic-table td.truncate { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.generic-table td.json-cell { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:11px; color:#4d4438; white-space:pre-wrap; word-break:break-all; max-height:80px; overflow:hidden; }
.generic-table-meta { display:flex; align-items:center; gap:12px; margin-bottom:8px; font-size:12px; color:var(--muted); }
.generic-table-meta .gt-name { font-family:ui-monospace,Menlo,monospace; color:var(--ink); font-weight:700; }
.generic-table-meta .gt-count { font-weight:650; color:var(--ink); }

/* compact stats strip inside the home page daily-report card —
   grid with equal columns so all 4 stats align on every day's render */
/* Stats strip — no bottom border; the following section (`_SECTION_SEP`)
   already draws the divider, and stacking two dashed lines looks broken. */
.dr-stats-compact { display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:14px; margin:0; }
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
.timeline-card .tl-tabs { display:flex; gap:4px; flex-wrap:wrap; }
.timeline-card .tl-tab { font-size:12px; padding:3px 10px; border-radius:999px; border:1px solid var(--line); background:rgba(255,250,240,.94); color:#3b352e; font-weight:650; cursor:pointer; }
.timeline-card .tl-tab:hover { background:#fff7e8; }
.timeline-card .tl-tab.active { background:var(--ink); color:white; border-color:var(--ink); }
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
/* Legend swatches are global — both the daily/weekly histogram legend
   AND the swimlane filter strip use them. Previously scoped to
   .timeline-card only, which left the histogram legend with 0×0 dots. */
.tl-legend { display:none; flex-wrap:wrap; gap:6px 14px; margin-top:10px; font-size:12px; color:#4d4438; }
.tl-legend.show { display:flex; }
.tl-legend-item { display:inline-flex; align-items:center; gap:5px; max-width:240px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.tl-swatch { width:10px; height:10px; border-radius:3px; flex:none; }
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
.date-picker summary { list-style:none; cursor:pointer; display:flex; gap:6px; align-items:center; border:1px solid var(--line); background:rgba(255,250,240,.94); border-radius:10px; padding:5px 9px; font-size:12px; font-weight:650; }
.date-picker summary:hover { background:#fff7e8; }
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

VISIBLE_EVENT_SOURCES = ["codex", "git", "hermes"]
SOURCE_LABELS = {"codex": "Codex", "git": "GitHub", "github": "GitHub", "hermes": "Hermes"}


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


_BOLD_MD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def esc_md(x) -> str:
    """Like esc(), but also renders `**bold**` markdown as <strong>.

    The AI overview prompt asks for bold emphasis in narrative/highlight
    text; without this the literal asterisks leak through to the page."""
    return _BOLD_MD_RE.sub(r"<strong>\1</strong>", esc(x))


def _card_head(
    title: str,
    *,
    tag: str = "",
    tag_color: str = "narrative",
    hint: str = "",
    right_html: str = "",
) -> str:
    """Standardized card header — every dashboard panel uses this so
    title size, chip color semantics, hint placement, and the right-rail
    controls slot are identical across cards.

    Args:
      title:     plain text (caller decides whether to T()-translate)
      tag:       short chip label (e.g. "Report"); empty hides the chip
      tag_color: "narrative" (紫) / "data" (蓝) / "config" (黄) / "item" (绿)
      hint:      muted explanatory text shown right after title/tag
      right_html: pre-rendered HTML for the right-aligned controls slot
                  (segment pills, toggle buttons, action links — anything)

    Returns:  <div class="card-head"> … </div>
    """
    parts = [f'<h3 class="card-title">{esc(title)}</h3>']
    if tag:
        parts.append(
            f'<span class="card-tag card-tag-{tag_color}">{esc(tag)}</span>'
        )
    if hint:
        parts.append(f'<span class="card-hint">{esc(hint)}</span>')
    if right_html:
        parts.append(f'<div class="card-right">{right_html}</div>')
    return f'<div class="card-head">{"".join(parts)}</div>'


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


def _maybe_gzip(handler: BaseHTTPRequestHandler, data: bytes) -> tuple[bytes, bool]:
    """gzip the body when the client accepts it AND the payload is large
    enough to benefit. ~5–10x reduction for our HTML — matters a lot over
    Tailscale / slow wifi. Below 1 KB the gzip envelope is net-negative."""
    if len(data) < 1024:
        return data, False
    accept = handler.headers.get("Accept-Encoding", "")
    if "gzip" not in accept.lower():
        return data, False
    import gzip
    return gzip.compress(data, compresslevel=5), True


def json_response(handler: BaseHTTPRequestHandler, obj, status=200):
    body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
    body, gzipped = _maybe_gzip(handler, body)
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    if gzipped:
        handler.send_header("Content-Encoding", "gzip")
        handler.send_header("Vary", "Accept-Encoding")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def html_response(handler: BaseHTTPRequestHandler, body: str, status=200):
    raw = body.encode("utf-8")
    # ETag of the uncompressed payload — gzip is just transport.
    import hashlib
    etag = '"' + hashlib.sha1(raw).hexdigest()[:16] + '"'
    # 304 short-circuit: if client sent matching If-None-Match, send no body.
    # Over slow Tailscale DERP this saves a 48 KB-ish payload + serialization.
    inm = handler.headers.get("If-None-Match", "")
    if inm and etag in inm:
        handler.send_response(304)
        handler.send_header("ETag", etag)
        # 5-minute fresh window: a dashboard reload within 5min skips the
        # network entirely (browser uses its cached body).
        handler.send_header("Cache-Control", "private, max-age=300")
        handler.end_headers()
        return
    data, gzipped = _maybe_gzip(handler, raw)
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("ETag", etag)
    handler.send_header("Cache-Control", "private, max-age=300")
    if gzipped:
        handler.send_header("Content-Encoding", "gzip")
        handler.send_header("Vary", "Accept-Encoding")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


# Inline SVG sparkline logo. Lives inside the <h1> so the brand mark sits
# right before "DayTrace · …" without depending on any /static route.
LOGO_INLINE_SVG = (
    '<svg width="22" height="22" viewBox="0 0 32 32" fill="#7aa2f7"'
    ' aria-hidden="true" focusable="false"'
    ' style="vertical-align:-3px;margin-right:10px;flex-shrink:0">'
    '<rect x="3" y="20" width="3" height="9"/>'
    '<rect x="7" y="11" width="3" height="18"/>'
    '<rect x="11" y="16" width="3" height="13"/>'
    '<rect x="15" y="6" width="3" height="23"/>'
    '<rect x="19" y="13" width="3" height="16"/>'
    '<rect x="23" y="22" width="3" height="7"/>'
    '<rect x="27" y="17" width="3" height="12"/>'
    '</svg>'
)
# Same shape, inlined as a data: URI so we can use it for the <link rel="icon">
# favicon without serving a separate static file. Dark-rounded background keeps
# the icon legible on white *and* dark browser tabs.
_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="6" fill="#0b1220"/>'
    '<g fill="#7aa2f7">'
    '<rect x="3" y="20" width="3" height="9"/>'
    '<rect x="7" y="11" width="3" height="18"/>'
    '<rect x="11" y="16" width="3" height="13"/>'
    '<rect x="15" y="6" width="3" height="23"/>'
    '<rect x="19" y="13" width="3" height="16"/>'
    '<rect x="23" y="22" width="3" height="7"/>'
    '<rect x="27" y="17" width="3" height="12"/>'
    '</g></svg>'
)
from urllib.parse import quote as _url_quote
FAVICON_DATA_URI = "data:image/svg+xml," + _url_quote(_FAVICON_SVG)


def layout(title: str, subtitle: str, active: str, content: str, date_control: str = "", body_class: str | None = None, lang: str | None = None) -> str:
    # lang defaults to the per-request ContextVar (set in do_GET from the
    # daytrace_lang cookie). Pass lang= explicitly only when rendering
    # outside an HTTP request (e.g. tests).
    if lang is None:
        lang = _CURRENT_LANG.get()
    # Right-rail: daily/weekly toggle pill + database (new tab) button + language switcher.
    toggle = "".join(
        f'<a class="page-toggle-pill{" active" if active == key else ""}" href="{href}">{esc(T(label_key, lang))}</a>'
        for key, label_key, href in [
            ("today",  "nav_daily",  "/today"),
            ("weekly", "nav_weekly", "/weekly"),
        ]
    )
    db_btn = (
        '<a class="page-db-btn" target="_blank" rel="noopener" '
        f'href="/events" title="{esc(T("nav_open_db_t", lang))}">{esc(T("nav_open_db", lang))} ↗</a>'
    )
    # Language switcher — one pill per language declared in
    # config/i18n.yaml. Active language highlighted; clicking another
    # sets the cookie and reloads.
    _lang_pills: list[str] = []
    for code in supported_languages():
        # Display label looks up `lang_label_<code>` in _STRINGS; defaults
        # to the uppercased code so adding a language is zero-config.
        display = T(f"lang_label_{code}") or code.upper()
        if display == f"lang_label_{code}":
            display = code.upper()
        _lang_pills.append(
            f'<a class="lang-opt{" active" if lang == code else ""}" '
            f'href="#" data-lang="{esc(code)}">{esc(display)}</a>'
        )
    lang_switcher = '<div class="page-lang-toggle">' + "".join(_lang_pills) + '</div>'
    # Wrap the right-rail children in a single nav container so the header
    # grid (5 columns) doesn't push any of them onto a second row.
    nav = (
        '<nav class="page-nav">'
        f'<div class="page-toggle">{toggle}</div>'
        f'{db_btn}'
        f'{lang_switcher}'
        '</nav>'
    )
    if body_class is None:
        body_class = f'{active}-page'
    html_lang_attr = "zh-CN" if lang == "zh" else "en"
    script = """
<script>
// Language toggle: set cookie + reload when an inactive option is clicked.
// If the link points somewhere real (static demo rewrites href to the
// other-language .html file), let the browser navigate normally.
document.addEventListener('click', (event) => {
  const btn = event.target.closest('.page-lang-toggle .lang-opt');
  if (btn) {
    const href = btn.getAttribute('href');
    if (href && href !== '#') return;  // static demo: let browser follow link
    event.preventDefault();
    if (btn.classList.contains('active')) return;  // already in this language
    const target = btn.getAttribute('data-lang') || 'zh';
    document.cookie = 'daytrace_lang=' + target + '; path=/; max-age=' + (60*60*24*365);
    window.location.reload();
    return;
  }
});
// Close date picker on outside click / Esc
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
    return f"""<!doctype html><html lang="{html_lang_attr}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{esc(title)}</title><link rel="icon" type="image/svg+xml" href="{FAVICON_DATA_URI}"><style>{STYLE}</style></head><body class="{body_class}"><header><h1>{LOGO_INLINE_SVG}{esc(title)}</h1>{date_control}<div class="header-spacer"></div>{nav}</header><main>{content}</main>{script}</body></html>"""


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
    # Show "2026-05-15 Fri / 周五" so the user knows the day of week
    # without squinting at the calendar.
    if selected:
        try:
            label = f"{selected} {_short_weekday(selected)}"
        except ValueError:
            label = selected
    else:
        label = "All"
    label_html = f'<span>{esc(label_text)}</span>' if label_text else ""
    return f"""<div class="header-filter">{label_html}<details class="date-picker"><summary>📅 {esc(label)} ▾</summary><div class="calendar-pop"><div class="cal-head"><span>{base.year}-{base.month:02d}</span></div><div class="cal-grid">{''.join(f'<div class="cal-dow">{d}</div>' for d in ['M','T','W','T','F','S','S'])}{''.join(days)}</div>{all_link}</div></details>{hidden_html}</div>"""


def pct_text(count: int, total: int) -> str:
    if total <= 0:
        return "0%"
    return f"{count / total * 100:.0f}%"


def first_item(items: list[dict[str, Any]], key: str, fallback: str = "暂无") -> str:
    if not items:
        return fallback
    return str(items[0].get(key) or fallback)


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


def _dimensions():
    """DIMENSIONS list — labels resolved against the current request language.
    Called as a function (not a constant) so changing the cookie flips the
    labels immediately. Returns [(dim_id, label), ...]."""
    return [
        ("source",   T("dim_source")),
        ("project",  T("dim_project")),
        ("task",     T("dim_task")),
        ("device",   T("dim_device")),
        ("activity", T("dim_activity")),
    ]


def _units():
    """UNITS list — same lazy-translation pattern as _dimensions()."""
    return [
        ("count", T("unit_count")),
        ("chars", T("unit_chars")),
        ("hours", T("unit_hours")),
    ]


# Static dim/unit IDs — used for validation only (the labels live in
# _dimensions() / _units() which read the current language).
DIMENSIONS = [("source", "Source"), ("project", "Project"),
              ("task", "Task"),     ("device", "Device"),
              ("activity", "Activity")]
UNITS = [("count", "Events"), ("chars", "Chars"), ("hours", "Hours")]


def _event_weight(ev: dict, unit: str) -> int:
    """Per-event weight for the count/chars units. Hours unit uses per-slot
    proportional split (compute_breakdown_hours) so this helper isn't called
    for that path."""
    if unit == "chars":
        return int(ev.get("char_count") or 0)
    return 1


def _breakdown_fallback_name(field: str) -> str:
    return {
        "project": "misc",
        "device_id": "unknown",
        "location_id": "unknown",
        "activity": "未分类",
        "task": "未对应任务",
    }.get(field, "other")


def compute_breakdown_hours(
    events: list[dict], field: str, *, slot_min: int = 5,
) -> list[dict]:
    """Per-5min-slot proportional split: for each slot containing events, the
    slot's `slot_min` minutes are distributed across `field` values present
    in that slot proportional to event count. Returns [{name, count, share}]
    where `count` is MINUTES (float) and shares sum to ~1.0.

    Same algorithm as the weekly main chart's hours mode, so daily and
    weekly hour totals agree exactly per (day, dim)."""
    from collections import defaultdict
    from daytrace.stats import _safe_minute

    per_slot: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    fb = _breakdown_fallback_name(field)
    for ev in events:
        m = _safe_minute(ev.get("start"))
        if m is None:
            continue
        name = ev.get(field) or (ev.get("project_guess") if field == "project" else None) or fb
        per_slot[m // slot_min][str(name)] += 1

    dim_min: dict[str, float] = defaultdict(float)
    for _slot, counts in per_slot.items():
        total = sum(counts.values())
        if total <= 0:
            continue
        for name, c in counts.items():
            dim_min[name] += slot_min * (c / total)

    total_min = sum(dim_min.values()) or 1
    items = sorted(dim_min.items(), key=lambda kv: -kv[1])
    return [
        {"name": n, "count": m, "share": round(m / total_min, 4)}
        for n, m in items
    ]


def compute_breakdown(events: list[dict], field: str, unit: str = "count") -> list[dict]:
    """Group events by `field`, return [{name, count, share}] desc by count.

    For unit='count' / 'chars': `count` is event count or summed char count.
    For unit='hours': `count` is minutes (float); see compute_breakdown_hours.
    `share` is normalized over the unit total."""
    if unit == "hours":
        return compute_breakdown_hours(events, field)
    from collections import Counter
    bag: Counter = Counter()
    fb = _breakdown_fallback_name(field)
    for ev in events:
        name = ev.get(field)
        if not name:
            name = ev.get("project_guess") if field == "project" else None
            if not name:
                name = fb
        bag[str(name)] += _event_weight(ev, unit)
    total = sum(bag.values()) or 1
    return [{"name": n, "count": c, "share": round(c / total, 4)} for n, c in bag.most_common()]


def _format_breakdown_value(v: float, unit: str) -> str:
    """Render a breakdown row's `count` field as user-visible text. Hours mode
    treats `count` as minutes."""
    if unit == "hours":
        m = int(round(v))
        if m < 60:
            return f"{m}m"
        h, mm = divmod(m, 60)
        return f"{h}h{mm:02d}m" if mm else f"{h}h"
    if unit == "chars":
        if v >= 10000:
            return f"{v/1000:.0f}k"
        if v >= 1000:
            return f"{v/1000:.1f}k"
        return f"{int(v)}"
    return f"{int(v)}"


def _mode_link(path: str, params: dict[str, str | None]) -> str:
    """Build a URL preserving only the truthy params (drops empty values)."""
    qs = urlencode({k: v for k, v in params.items() if v})
    return f"{path}?{qs}" if qs else path


def today_page(db_path: Path, date: str | None, mode: str | None = None, unit: str | None = None, style: str | None = None):
    valid_modes = {dim_id for dim_id, _ in DIMENSIONS}
    if mode not in valid_modes:
        mode = "project"
    valid_units = {u for u, _ in UNITS}
    if unit not in valid_units:
        unit = "count"
    con = connect(db_path)
    all_dates = available_dates(con)
    if not date:
        # Default to shifted-yesterday rather than "most recent with any data" —
        # today's shifted-day is usually still in flight at the time the user
        # opens the dashboard, so yesterday is the page they actually want.
        from datetime import datetime as _dt, timedelta as _td
        from daytrace import stats as _stats  # noqa: F401 — referenced via module attribute below
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
    # Stamp ev["task"] = linked work_item.title (or None) — drives the
    # 任务 dim option across histogram / distribution / swim.
    _enrich_events_with_tasks(con, day_events)
    total = len(day_events)  # subtitle uses shifted-day total, not calendar-day
    # Enrich events with activity labels (bilingual since v15).
    if date:
        from daytrace.db import load_activity_labels_for_date
        _cur_lang = _CURRENT_LANG.get()
        labels = load_activity_labels_for_date(con, date, lang=_cur_lang)
        _unclassified = "Unclassified" if _cur_lang == "en" else "未分类"
        for ev in day_events:
            ev["activity"] = labels.get(ev["id"], _unclassified) if labels else _unclassified
        # Recompute every dim breakdown with the active unit (count or chars)
        # so the composition donut + per-project shares reflect what the user
        # picked in the dim-bar.
        today["by_source"]   = compute_breakdown(day_events, "source",      unit)
        today["by_project"]  = compute_breakdown(day_events, "project",     unit)
        today["by_device"]   = compute_breakdown(day_events, "device_id",   unit)
        today["by_location"] = compute_breakdown(day_events, "location_id", unit)
        today["by_activity"] = compute_breakdown(day_events, "activity",    unit)
    # Map daily's existing mode names → weekly's _stack_value_of conventions.
    # daily uses "device" (-> device_id) and "location" (-> location_id);
    # _stack_value_of now accepts those aliases. The composition_card still
    # gets the original mode string, so its per-pane data attrs stay
    # backwards compatible.

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
        # v4 layout: Report card = Dashboard (4 tiles) + 总览 (headline +
        # narrative) + 趋势 (chip + 1 sentence as a closer). Insights card
        # below the top row hosts the 3-column 进展/节奏/跟进 grid.
        daily_ai_cost = con.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM day_channel "
            "WHERE date=? AND generator='ai'", (date,)
        ).fetchone()[0] or 0.0
        rich_daily_body = (
            _render_dashboard_section(
                dict(day_report_row), day_channels, date or "",
                ai_cost=float(daily_ai_cost),
            )
            + _render_overview_section(ai_overview)
            + _render_trend_closer(ai_overview, ai_continuity)
        )

    dates_desc = all_dates
    prev_link = next_link = ""
    if date in dates_desc:
        idx = dates_desc.index(date)
        if idx + 1 < len(dates_desc):
            prev_day = dates_desc[idx + 1]
            href = _mode_link("/today", {"date": prev_day, "mode": mode if mode != "project" else None, "unit": unit if unit != "count" else None})
            prev_link = f'<a class="hdr-nav-btn" title="前一天 {esc(prev_day)}" href="{esc(href)}">←</a>'
        if idx - 1 >= 0:
            next_day = dates_desc[idx - 1]
            href = _mode_link("/today", {"date": next_day, "mode": mode if mode != "project" else None, "unit": unit if unit != "count" else None})
            next_link = f'<a class="hdr-nav-btn" title="后一天 {esc(next_day)}" href="{esc(href)}">→</a>'
    # Database button removed from the centered header — the right-rail
    # already has a Database pill (.page-db-btn). Keeping both gave the
    # daily/weekly page two distinct "open DB" affordances that did the
    # same thing.
    open_db_link = ""

    # Global controls are now ALL inlined into the sticky page header (via
    # the layout()'s date_control slot) — single row holding: day-nav +
    # date picker + dim pills. Unit pills sit inside the Chart panel.
    valid_top_views = {"chart", "dist"}
    top_view = (style if style in valid_top_views else "chart")

    dim_pill_links = "".join(
        f'<a class="dim-tab{" active" if dim_id == mode else ""}" '
        f'data-param="mode" data-value="{dim_id}" '
        f'href="{esc(_mode_link("/today", {"date": date, "mode": dim_id if dim_id != "project" else None, "unit": unit if unit != "count" else None}))}">'
        f'{label}</a>'
        for dim_id, label in _dimensions()
    )
    dim_pills_html = (
        f'<div class="dim-tabs" title="{esc(T("dim_tooltip"))}">{dim_pill_links}</div>'
    )

    # Build daily top-chart-card (直方图 + 分布) — mirrors weekly's structure
    boundary_h = stats.DAY_BOUNDARY_HOUR if False else 4  # default; keep simple
    from daytrace import stats as _daily_stats
    boundary_h = _daily_stats.DAY_BOUNDARY_HOUR

    # Per-hour stack (24 bins) for histogram
    per_hour, per_hour_totals = _daily_per_hour_stack(
        day_events, boundary_h, mode=mode, unit=unit,
    )
    # Overall counter across hours — drives palette + distribution view.
    # Reuse the same palette helper as weekly so colors match the swim-lane.
    top_names, palette, overall_counter = _compute_palette_for_week(per_hour)

    histogram_body = _daily_histogram_body(
        per_hour=per_hour, per_hour_totals=per_hour_totals,
        unit=unit, mode=mode, top_names=top_names, palette=palette,
        boundary_hour=boundary_h, chart_height_px=220,
    )
    distribution_body = _distribution_view_body(
        overall=overall_counter, palette=palette, unit=unit, mode=mode,
    )

    # Unit pills go INSIDE this card (they only matter for chart + dist)
    unit_pill_links = "".join(
        f'<a class="unit-tab{" active" if u_id == unit else ""}" '
        f'data-param="unit" data-value="{u_id}" '
        f'href="{esc(_mode_link("/today", {"date": date, "mode": mode if mode != "project" else None, "unit": u_id if u_id != "count" else None}))}">'
        f'{label}</a>'
        for u_id, label in _units()
    )
    top_chart_switcher = (
        '<div class="dim-tabs" data-role="tc-switcher">'
        f'<button type="button" class="dim-tab{" active" if top_view == "chart" else ""}" data-view="chart">{esc(T("chart_view_hist"))}</button>'
        f'<button type="button" class="dim-tab{" active" if top_view == "dist" else ""}" data-view="dist">{esc(T("chart_view_dist"))}</button>'
        '</div>'
    )
    unit_label_daily = dict(_units()).get(unit, unit)
    dim_label_daily = dict(_dimensions()).get(mode, mode)
    # ┃ Chart panel ┃ — histogram (24 hourly bars) + distribution (donut+bars)
    chart_right = (
        f'<span class="muted small" style="font-weight:600;">{esc(T("unit_label"))}</span>'
        f'<div class="unit-tabs">{unit_pill_links}</div>'
        f'{top_chart_switcher}'
    )
    daily_top_chart_card = (
        f'<div class="card top-chart-card" id="top-chart" data-tc-view="{esc(top_view)}">'
        + _card_head(
            f"{T('chart_per_hour')} {unit_label_daily}",
            tag=T("chart_panel_tag"), tag_color="data",
            hint=f"· {T('dim_label')}: {dim_label_daily}",
            right_html=chart_right,
        )
        + f'<div class="tc-pane" data-pane="chart">{histogram_body}</div>'
        + f'<div class="tc-pane" data-pane="dist">{distribution_body}</div>'
        + '</div>'
    )

    # Right column under chart used to host a highlights/concerns mini-card;
    # those moved into the full-width Insights card (rendered between the
    # top row and the timeline). Right column is now just the chart card.
    right_column_body = daily_top_chart_card
    insights_card_html = _render_insights_card(ai_overview, continuity=ai_continuity)

    # Bottom swim-lane card — reuse the weekly module with days=[date] so
    # the rendering is literally identical (single row covering 24h, ticks
    # colored by the current dim mode). Filter pills sit above as a shared
    # control.
    sf_param = qs_swim_filter if False else None  # placeholder
    sf = "all"
    swim_body = (
        _weekly_swimlane_card(
            events=day_events, days=[date or ""], boundary_hour=boundary_h,
            stack_by=mode, top_names=top_names, palette=palette,
            swim_filter=sf,
        ) if date else ""
    )
    # Filter pills — All + each top-N value, same pattern as weekly
    filter_pills = [
        '<button type="button" class="dim-tab'
        + (' active' if sf == 'all' else '')
        + f'" data-filter="all">{esc(T("filter_all"))}</button>'
    ]
    for n in top_names:
        if overall_counter.get(n, 0) <= 0:
            continue
        color = palette.get(n, _WEEKLY_OTHER_COLOR)
        cls = "dim-tab active" if sf == n else "dim-tab"
        swatch = (
            f'<span style="display:inline-block; width:8px; height:8px; '
            f'border-radius:50%; background:{color}; margin-right:6px; '
            f'vertical-align:middle;"></span>'
        )
        filter_pills.append(
            f'<button type="button" class="{cls}" data-filter="{esc(n)}">'
            f'{swatch}{esc(n)}'
            f'<span class="muted" style="margin-left:6px; font-weight:500; font-size:11px;">'
            f'×{int(overall_counter[n]) if isinstance(overall_counter[n], (int, float)) else overall_counter[n]}</span>'
            f'</button>'
        )
    daily_filter_bar = (
        '<div data-role="swim-filter" '
        'style="display:flex; flex-wrap:wrap; gap:6px; align-items:center; '
        'margin:8px 0 12px;">'
        f'<span class="muted small" style="margin-right:4px; font-weight:600;">{esc(T("weekly_filter"))}</span>'
        + "".join(filter_pills) +
        '</div>'
    )
    # ┃ Timeline panel ┃ — 24h swim-lane + filter pills
    daily_swim_card = (
        f'<section class="card weekly-viz" id="chart" data-view="swim" data-filter="{esc(sf)}">'
        + _card_head(T("page_timeline"), tag=T("tag_timeline"), tag_color="narrative")
        + daily_filter_bar
        + '<div class="wv-pane" data-pane="swim" style="display:block;">' + swim_body + '</div>'
        + '</section>'
    )

    # Page JS — handles dim/unit pill live-URL nav (scroll preservation),
    # top-chart-card view switching, swim filter (no heatmap on daily so
    # the heat-specific branch from weekly is skipped).
    daily_sync_js = (
        '<script>(function(){'
        'var KEY="daytrace.today.scrollY";'
        'var saved=sessionStorage.getItem(KEY);'
        'if(saved!==null){window.scrollTo(0,parseInt(saved,10)||0);sessionStorage.removeItem(KEY);}'
        # Live-URL nav for all data-param pills (dim + unit pills page-wide)
        'document.querySelectorAll("a[data-param]").forEach(function(a){'
        'a.addEventListener("click",function(e){'
        'if(a.classList.contains("active")){e.preventDefault();return;}'
        'e.preventDefault();'
        'sessionStorage.setItem(KEY,String(window.scrollY));'
        'try{var u=new URL(location.href);'
        'u.searchParams.set(a.dataset.param,a.dataset.value);'
        'location.href=u.toString();}catch(err){location.href=a.href;}'
        '});});'
        # Top chart switcher
        'var tc=document.querySelector(".top-chart-card");'
        'if(tc){'
        'tc.querySelectorAll("[data-role=\\"tc-switcher\\"] .dim-tab").forEach(function(btn){'
        'btn.addEventListener("click",function(){'
        'var v=btn.dataset.view;'
        'tc.setAttribute("data-tc-view",v);'
        'tc.querySelectorAll("[data-role=\\"tc-switcher\\"] .dim-tab").forEach(function(b){'
        'b.classList.toggle("active",b.dataset.view===v);});'
        'try{var u=new URL(location.href);'
        'if(v==="chart"){u.searchParams.delete("style");}else{u.searchParams.set("style",v);}'
        'history.replaceState({},"",u);}catch(e){}'
        '});});}'
        # Donut hover (top-chart-card distribution view)
        'var donut=document.querySelector(".top-chart-card .cc-donut[data-segments]");'
        'if(donut){'
        'var dSegs=[];try{dSegs=JSON.parse(donut.dataset.segments||"[]");}catch(e){}'
        'var dWrap=donut.parentElement;dWrap.style.position="relative";'
        'var dTip=document.createElement("div");'
        'dTip.style.cssText="position:absolute;pointer-events:none;z-index:10;background:rgba(34,28,18,.95);color:#fff7e8;border-radius:10px;padding:7px 11px;box-shadow:0 10px 26px rgba(0,0,0,.28);font-size:12px;max-width:260px;line-height:1.45;display:none;white-space:nowrap;";'
        'dWrap.appendChild(dTip);'
        'function dEsc(s){return String(s==null?"":s).replace(/[&<>\\"]/g,function(c){return({"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;"})[c];});}'
        'donut.addEventListener("mousemove",function(ev){'
        'var r=donut.getBoundingClientRect();'
        'var cx=r.left+r.width/2,cy=r.top+r.height/2;'
        'var dx=ev.clientX-cx,dy=ev.clientY-cy;'
        'var dist=Math.sqrt(dx*dx+dy*dy);'
        'var outerR=r.width/2;var innerR=outerR*(124/210);'
        'if(dist<innerR||dist>outerR){dTip.style.display="none";return;}'
        'var a=Math.atan2(dx,-dy);if(a<0)a+=2*Math.PI;'
        'var pct=a/(2*Math.PI)*100;'
        'var seg=null;'
        'for(var i=0;i<dSegs.length;i++){if(pct>=dSegs[i].start&&pct<dSegs[i].end){seg=dSegs[i];break;}}'
        'if(!seg)seg=dSegs[dSegs.length-1];'
        'if(!seg){dTip.style.display="none";return;}'
        'dTip.innerHTML="<div style=\\"font-weight:700;margin-bottom:3px;\\"><span style=\\"display:inline-block;width:10px;height:10px;border-radius:2px;background:"+seg.color+";margin-right:6px;vertical-align:middle;\\"></span>"+dEsc(seg.name)+"</div>"+'
        '"<div>"+dEsc(seg.label)+" · "+(seg.share*100).toFixed(1)+"%</div>";'
        'dTip.style.display="block";'
        'var pr=dWrap.getBoundingClientRect();'
        'var x=ev.clientX-pr.left+14;var y=ev.clientY-pr.top+14;'
        'if(x+dTip.offsetWidth>pr.width-4)x=pr.width-dTip.offsetWidth-4;'
        'dTip.style.left=x+"px";dTip.style.top=y+"px";'
        '});'
        'donut.addEventListener("mouseleave",function(){dTip.style.display="none";});'
        '}'
        # Swim-lane filter (JS-only, no heatmap on daily)
        'var card=document.querySelector(".weekly-viz");'
        'if(card){'
        'function applyFilter(v){'
        'card.setAttribute("data-filter",v);'
        'card.querySelectorAll(".tl-swim-tick").forEach(function(t){'
        't.style.display=(v==="all"||t.dataset.value===v)?"":"none";});'
        'card.querySelectorAll(".tl-swim-row").forEach(function(row){'
        'var c=row.querySelectorAll(\'.tl-swim-tick:not([style*="display: none"])\').length;'
        'var b=row.querySelector("[data-row-count]");if(b)b.textContent="×"+c;});'
        'card.querySelectorAll("[data-role=\\"swim-filter\\"] .dim-tab").forEach(function(b){'
        'b.classList.toggle("active",b.dataset.filter===v);});'
        'try{var u=new URL(location.href);'
        'if(v==="all"){u.searchParams.delete("swim_filter");}else{u.searchParams.set("swim_filter",v);}'
        'history.replaceState({},"",u);}catch(e){}}'
        'card.querySelectorAll("[data-role=\\"swim-filter\\"] .dim-tab").forEach(function(btn){'
        'btn.addEventListener("click",function(){applyFilter(btn.dataset.filter);});});'
        'var init=card.getAttribute("data-filter")||"all";'
        'if(init!=="all"){applyFilter(init);}'
        '}'
        '})();</script>'
    )

    # Page layout — named panels for sharing vocabulary with the user:
    #   ┌──────────────────────────┐
    #   │ dim-bar                  │  ← global controls (day-nav + mode)
    #   ├─────────────┬────────────┤
    #   │ Report      │ Chart      │  ← top row (2 cols)
    #   │ panel       ├────────────┤
    #   │ (daily-     │ Highlights │
    #   │  report)    │ panel      │
    #   ├─────────────┴────────────┤
    #   │ Timeline panel           │  ← swim-lane + filter pills
    #   │ (weekly-viz)             │
    #   └──────────────────────────┘
    # Per-project AI summaries live INSIDE the Tasks table now (Latest/
    # Next columns), so the dedicated Projects card was removed. Layout:
    #   row 1: Report | Chart
    #   row 2: Insights (full-width, 3-col rollback)
    #   row 3: Swim · Tasks (with AI cols) · Audit
    tasks_panel_html = _tasks_panel(con, [date or ""], boundary_h) if date else ""
    audit_html = _alignment_audit_card(con, [date or ""]) if date else ""

    content = f"""
<section class="report-grid">
  <div class="card daily-report">{_card_head(f"{T('daily_title')} · {date or T('no_date')}", tag=T('report_tag'), tag_color='narrative')}{rich_daily_body}</div>
  <div class="right-column">{right_column_body}</div>
</section>
{insights_card_html}
{daily_swim_card}
{tasks_panel_html}
{audit_html}
{daily_sync_js}
"""
    # Header pattern: [←] [📅 picker] [→] [open db ↗] [dim pills]
    # — identical layout as weekly. "Open db" opens in a new tab.
    cal_hidden = {"mode": mode if mode != "project" else None, "unit": unit if unit != "count" else None}
    cal_hidden = {k: v for k, v in cal_hidden.items() if v}
    date_picker_html = calendar_control('/today', date, all_dates, hidden=cal_hidden, label_text="")
    header_controls = (
        '<div class="header-controls">'
        + prev_link + date_picker_html + next_link
        + open_db_link
        + dim_pills_html
        + '</div>'
    )
    return layout(f"DayTrace · {T('daily_title')}", f"{total} events · daily report", "today", content, date_control=header_controls)


def events_table(events, filters: dict[str, str | None], options: dict[str, Any]):
    rows=[]
    for e in events:
        row_class = source_row_class(e.get("source"))
        rows.append(f"""
<tr class="{row_class}">
  <td><span class="time" title="{esc(e['start'])}">{esc(format_event_time(e['start']))}</span></td>
  <td class="db-cell"><strong>{esc(display_source(e['source']))}</strong></td>
  <td class="db-cell">{esc(e.get('activity') or ('Unclassified' if _CURRENT_LANG.get()=='en' else '未分类'))}</td>
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
    time_filter = (
        f'<div class="time-range">'
        f'{date_filter_calendar_control("/events", "start_from", filters.get("start_from"), date_counts, date_hidden, T("db_date_start"), T("db_date_all"))}'
        f'{date_filter_calendar_control("/events", "start_to", filters.get("start_to") or filters.get("start_from"), date_counts, date_hidden, T("db_date_end"), T("db_date_same") if filters.get("start_from") else T("db_date_no_end"), min_date=filters.get("start_from"), picker_class="end-date")}'
        f'</div>'
    )
    has_active_filter = any(
        filters.get(k)
        for k in ("source", "activity", "location_id", "project", "search", "start_from", "start_to")
    )
    clear_link = (
        f'<a class="clear-filters" href="/events" title="{esc(T("db_clear_t"))}">{esc(T("db_clear"))}</a>'
        if has_active_filter
        else f'<span class="clear-filters muted" title="{esc(T("db_clear_idle_t"))}">{esc(T("db_clear"))}</span>'
    )
    return f"""
<form method="get" action="/events">
  {hidden_order}
  <div class="table-wrap"><table><colgroup><col class="col-time"><col class="col-source"><col class="col-activity"><col class="col-location"><col class="col-project"><col class="col-title"></colgroup>
  <thead><tr>
    <th><div class="th-title"><a class="sort-link" href="{esc(sort_href)}">{esc(T('db_col_time'))} {sort_arrow}</a><span class="clear-filters-wrap">{clear_link}</span></div>{time_filter}</th>
    <th><div class="th-title"><span>{esc(T('db_col_source'))}</span></div>{select_control('source', options['source'], filters.get('source'))}</th>
    <th><div class="th-title"><span>{esc(T('db_col_activity'))}</span></div>{select_control('activity', options['activity'], filters.get('activity'))}</th>
    <th><div class="th-title"><span>{esc(T('db_col_location'))}</span></div>{select_control('location_id', options['location_id'], filters.get('location_id'))}</th>
    <th><div class="th-title"><span>{esc(T('db_col_project'))}</span></div>{select_control('project', options['project'], filters.get('project'))}</th>
    <th><div class="th-title"><span>{esc(T('db_col_title'))}</span><label class="header-filter"><span>{esc(T('db_col_rows'))}</span>{event_limit_control(filters.get('limit'))}</label></div><input name="search" value="{esc(filters.get('search') or '')}" placeholder="{esc(T('db_search_ph'))}"></th>
  </tr></thead><tbody>{''.join(rows) or f'<tr><td colspan="6">{esc(T("db_no_events"))}</td></tr>'}</tbody></table></div>
</form>"""

# Every browsable DB table is declared here. Three view modes:
#   • "custom"  — handled by an existing dedicated page function (events,
#                 day_report, day_project_report). Just for tab labeling.
#   • "generic" — rendered by generic_table_page below. Set `cols` to a
#                 list of column specs (name, label_zh, label_en, width,
#                 transform). Missing columns are hidden; unlisted DB
#                 columns aren't shown (deliberate — keeps the user-facing
#                 view curated even as the schema evolves).
#   • "raw"     — same as generic but `cols=None` falls back to all columns
#                 of the table, JSON-pretty-printed if value looks like JSON.
#
# `group` puts the tab into "core" (always visible) or "advanced" (folded
# under a disclosure so the toolbar doesn't blow up).
TABLE_VIEWS: dict[str, dict] = {
    # ── Core (custom-rendered pages) ────────────────────────────────────
    "events": {
        "kind": "custom", "label_key": "db_tab_events", "group": "core",
    },
    "day": {
        "kind": "custom", "label_key": "db_tab_day", "group": "core",
    },
    "day_project": {
        "kind": "custom", "label_key": "db_tab_day_project", "group": "core",
    },
    # ── Core (generic, but user-relevant) ───────────────────────────────
    "work_items": {
        "kind": "generic", "label_key": "db_tab_work_items", "group": "core",
        "sql_table": "work_items",
        "default_order": "title",
        "cols": [
            {"name": "record_id",       "zh": "Record ID",   "en": "Record ID",  "width": 130, "mono": True},
            {"name": "table_key",       "zh": "表",         "en": "Table",       "width": 80},
            {"name": "title",           "zh": "标题",       "en": "Title",       "width": 240},
            {"name": "title_en",        "zh": "EN 标题",    "en": "EN title",    "width": 220},
            {"name": "status",          "zh": "状态",       "en": "Status",      "width": 80},
            {"name": "priority",        "zh": "P",          "en": "P",           "width": 40},
            {"name": "tags",            "zh": "标签",       "en": "Tags",        "width": 140},
            {"name": "due_date",        "zh": "截止",       "en": "Due",         "width": 110},
            {"name": "subtitle",        "zh": "副标题",     "en": "Subtitle",    "width": 160},
            {"name": "last_synced_at",  "zh": "同步时间",   "en": "Synced",      "width": 150, "muted": True},
        ],
    },
    # ── Advanced (generic, debug-y) ─────────────────────────────────────
    "event_work_item_links": {
        "kind": "generic", "label_key": "db_tab_links", "group": "advanced",
        "sql_table": "event_work_item_links",
        "default_order": "matched_at DESC",
        "cols": [
            {"name": "event_id",   "zh": "事件 ID",   "en": "Event ID",   "width": 200, "mono": True, "truncate": 32},
            {"name": "record_id",  "zh": "任务 ID",   "en": "Task ID",    "width": 130, "mono": True},
            {"name": "match_type", "zh": "匹配方式",  "en": "Match",      "width": 100},
            {"name": "confidence", "zh": "置信度",    "en": "Confidence", "width": 80,  "fmt": "pct"},
            {"name": "matched_at", "zh": "匹配时间",  "en": "Matched at", "width": 150, "muted": True},
        ],
    },
    "event_activity_labels": {
        "kind": "generic", "label_key": "db_tab_labels", "group": "advanced",
        "sql_table": "event_activity_labels",
        "default_order": "assigned_at DESC",
        "cols": [
            {"name": "event_id",   "zh": "事件 ID",   "en": "Event ID",   "width": 200, "mono": True, "truncate": 32},
            {"name": "label",      "zh": "标签 (zh)", "en": "Label (zh)", "width": 120},
            {"name": "label_json", "zh": "双语",      "en": "Bilingual",  "width": 200, "muted": True, "truncate": 60},
            {"name": "source",     "zh": "来源",      "en": "Source",     "width": 80},
            {"name": "confidence", "zh": "置信度",    "en": "Confidence", "width": 80,  "fmt": "pct"},
            {"name": "model",      "zh": "模型",      "en": "Model",      "width": 140, "muted": True},
            {"name": "assigned_at","zh": "时间",      "en": "Assigned at","width": 150, "muted": True},
        ],
    },
    "day_channel": {
        "kind": "generic", "label_key": "db_tab_day_ch", "group": "advanced",
        "sql_table": "day_channel",
        "default_order": "date DESC, channel",
        "cols": [
            {"name": "date",              "zh": "日期",     "en": "Date",        "width": 100},
            {"name": "channel",           "zh": "Channel",  "en": "Channel",     "width": 160},
            {"name": "generator_version", "zh": "版本",     "en": "Version",     "width": 80},
            {"name": "value_json",        "zh": "Value",    "en": "Value",       "width": 360, "truncate": 120, "mono": True},
            {"name": "tokens_in",         "zh": "in",       "en": "in",          "width": 70, "muted": True},
            {"name": "tokens_out",        "zh": "out",      "en": "out",         "width": 70, "muted": True},
            {"name": "cost_usd",          "zh": "成本$",    "en": "Cost $",      "width": 70, "fmt": "money"},
            {"name": "generated_at",      "zh": "生成时间", "en": "Generated at","width": 150, "muted": True},
            {"name": "error",             "zh": "错误",     "en": "Error",       "width": 200, "muted": True},
        ],
    },
    "day_project_channel": {
        "kind": "generic", "label_key": "db_tab_day_proj_ch", "group": "advanced",
        "sql_table": "day_project_channel",
        "default_order": "date DESC, project, channel",
        "cols": [
            {"name": "date",              "zh": "日期",     "en": "Date",        "width": 100},
            {"name": "project",           "zh": "项目",     "en": "Project",     "width": 140},
            {"name": "channel",           "zh": "Channel",  "en": "Channel",     "width": 140},
            {"name": "generator_version", "zh": "版本",     "en": "Version",     "width": 80},
            {"name": "value_json",        "zh": "Value",    "en": "Value",       "width": 360, "truncate": 120, "mono": True},
            {"name": "tokens_in",         "zh": "in",       "en": "in",          "width": 70, "muted": True},
            {"name": "tokens_out",        "zh": "out",      "en": "out",         "width": 70, "muted": True},
            {"name": "cost_usd",          "zh": "成本$",    "en": "Cost $",      "width": 70, "fmt": "money"},
            {"name": "generated_at",      "zh": "生成时间", "en": "Generated at","width": 150, "muted": True},
        ],
    },
    "devices": {
        "kind": "generic", "label_key": "db_tab_devices", "group": "advanced",
        "sql_table": "devices",
        "default_order": "device_id",
        "cols": None,  # show all columns
    },
    "locations": {
        "kind": "generic", "label_key": "db_tab_locations", "group": "advanced",
        "sql_table": "locations",
        "default_order": "location_id",
        "cols": None,
    },
    "sources": {
        "kind": "generic", "label_key": "db_tab_sources", "group": "advanced",
        "sql_table": "sources",
        "default_order": "source_id",
        "cols": None,
    },
}

# Back-compat for any caller still using TABLE_TABS
TABLE_TABS: list[tuple[str, str]] = [
    (k, v["label_key"]) for k, v in TABLE_VIEWS.items() if v.get("group") == "core"
]


def table_switcher_html(active: str, qs: dict[str, list[str]]) -> str:
    """Tab row at the top of the /events page for switching which table to view.

    Core tables are shown as inline pills (matching .table-switcher). Advanced
    tables sit behind a <details> disclosure so the bar doesn't blow up with
    debug tables that 99% of visits never need."""
    date = (qs.get("date", [None])[0] or "")

    def _pill(table_id: str, label_key: str) -> str:
        params = {"table": table_id}
        if date:
            params["date"] = date
        href = "/events" + ("?" + urlencode(params) if params else "")
        cls = "table-tab" + (" active" if table_id == active else "")
        return f'<a class="{cls}" href="{esc(href)}">{esc(T(label_key))}</a>'

    # The first 3 entries (events / day_report / day_project_report) are
    # the everyday tabs and always show. Everything else — Feishu tasks +
    # all advanced/debug tables — sits behind a single "更多 ▾" toggle so
    # the toolbar stays compact, but expands inline (same row) when the
    # user wants the debug surfaces.
    PRIMARY_KEYS = ("events", "day", "day_project")
    primary_pills = "".join(
        _pill(tid, TABLE_VIEWS[tid]["label_key"])
        for tid in PRIMARY_KEYS if tid in TABLE_VIEWS
    )
    extra_pills = "".join(
        _pill(tid, cfg["label_key"]) for tid, cfg in TABLE_VIEWS.items()
        if tid not in PRIMARY_KEYS
    )
    extras_open = active not in PRIMARY_KEYS  # auto-expand when current tab is hidden
    more_label = T("db_less") if extras_open else T("db_more")
    extras_html = (
        '<span class="table-switcher-divider" aria-hidden="true"></span>'
        '<button type="button" class="table-tab table-more-toggle"'
        + (' data-extras-toggle aria-expanded="true"' if extras_open else ' data-extras-toggle')
        + '>'
        f'<span class="tmt-label">{esc(more_label)}</span>'
        + ' <span class="tmt-caret">' + ('▴' if extras_open else '▾') + '</span>'
        + '</button>'
        + '<div class="table-switcher-extras"' + ('' if extras_open else ' hidden') + '>'
        + extra_pills
        + '</div>'
    ) if extra_pills else ""
    return (
        '<section class="table-switcher-wrap">'
        f'<div class="table-switcher">{primary_pills}{extras_html}</div>'
        '<script>(function(){'
        'var btn=document.querySelector(".table-more-toggle");if(!btn)return;'
        'var box=document.querySelector(".table-switcher-extras");if(!box)return;'
        f'var more={json.dumps(T("db_more"), ensure_ascii=False)};'
        f'var less={json.dumps(T("db_less"), ensure_ascii=False)};'
        'btn.addEventListener("click",function(){'
        'var open=box.hasAttribute("hidden");'
        'if(open){box.removeAttribute("hidden");'
        'btn.querySelector(".tmt-label").textContent=less;'
        'btn.querySelector(".tmt-caret").textContent="▴";'
        'btn.setAttribute("aria-expanded","true");}'
        'else{box.setAttribute("hidden","");'
        'btn.querySelector(".tmt-label").textContent=more;'
        'btn.querySelector(".tmt-caret").textContent="▾";'
        'btn.removeAttribute("aria-expanded");}'
        '});})();</script>'
        '</section>'
    )


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

_SECTION_SEP = '<hr class="dr-sep">'


def _section_header(label: str) -> str:
    """Back-compat shim — sections no longer have labeled headers; callers
    use `_SECTION_SEP` directly for the thin dashed divider."""
    return _SECTION_SEP


def _render_dashboard_section(
    header: dict, channels: dict[str, str | None], date_val: str,
    *, ai_cost: float | None = None,
) -> str:
    """Dashboard section — 4-tile stats strip only. Folds 最长专注 (was
    its own line) into a tile and adds an AI 花费 tile parallel to weekly."""
    return _render_stats_strip_compact(header, channels, ai_cost=ai_cost)


def _render_overview_section(overview_payload: dict | None) -> str:
    """Overview section — AI headline + narrative paragraph only.

    Bullet-form key moves used to live here too, but they duplicated the
    Insights `🚀 关键进展` column verbatim. Bullets now belong to Insights;
    the Report-card overview is a single story-like paragraph."""
    if not overview_payload:
        return (
            _SECTION_SEP
            + f'<div class="dr-narrative muted">{esc(T("no_ai_overview"))}</div>'
        )
    headline = L(overview_payload.get("headline"))
    ov = overview_payload.get("overview")
    if isinstance(ov, dict):
        narrative = L(ov.get("narrative"))
    else:
        narrative = L(overview_payload.get("narrative"))
    parts = [_SECTION_SEP]
    if headline:
        parts.append(f'<div class="dr-headline">📰 {esc(headline)}</div>')
    if narrative:
        parts.append(f'<p class="dr-narrative">{esc_md(narrative)}</p>')
    return "".join(parts)


def _render_trend_section(overview_payload: dict | None, continuity: dict | None) -> str:
    """Trend section — direction chip + 1-sentence comparison.

    Source priority: v7 `overview.trend` dict ↦ legacy `ai_continuity_day`.
    Hidden entirely (no separator) when nothing to show."""
    direction = ""
    comparison = ""
    if overview_payload:
        tr = overview_payload.get("trend")
        if isinstance(tr, dict):
            direction = tr.get("direction") or ""
            comparison = L(tr.get("comparison"))
    if not direction and continuity:
        direction = continuity.get("momentum") or ""
        comparison = L(continuity.get("relation_to_yesterday"))
    if not direction and not comparison:
        return ""
    return (
        _SECTION_SEP
        + '<div class="dr-trend">'
        + (_momentum_chip(direction) if direction else "")
        + (f'<span class="dr-trend-text">{esc_md(comparison)}</span>' if comparison else "")
        + '</div>'
    )


def _render_recommendations_section(overview_payload: dict | None) -> str:
    """Recommendations section — 1-3 actionable bullets. Hidden when empty."""
    if not overview_payload:
        return ""
    recs = overview_payload.get("recommendations") or []
    if not recs:
        return ""
    items = "".join(f"<li>{esc_md(r)}</li>" for r in recs)
    return (
        _SECTION_SEP
        + f'<ul class="dr-bullets dr-recommendations">{items}</ul>'
    )


# Plain-language hints shown on hover over each Insights column title.
# Speak to the reader ('你') about what the column contains — not about
# how the AI is prompted to produce it. Native browser `title=` tooltip
# (zero JS); we let the prompt's internal jargon stay private.
_INSIGHTS_TOOLTIPS = {
    "highlights":
        "今天/本周在飞书任务上有哪些具体推进 —— 完成的、改的、提的代码。"
        "只看带任务标签的事件,零散杂活不放这里。",
    "work_pattern":
        "你今天的作息和最近 7 天平均比起来怎么样 —— 几点开工、几点收工、"
        "有没有大块专注、是不是切换太频繁。基于具体数字,不会写空话。",
    "suggestions":
        "明天/下周该盯哪些任务 —— deadline 临近的、好几天没动的、"
        "还有未提交改动的。只看任务清单,不回顾今天该做啥。",
}


def _render_trend_closer(overview_payload: dict | None, continuity: dict | None) -> str:
    """Trend chip + 1-sentence comparison rendered as a slim closer below
    the Report-card narrative. Took over the standalone Insights column;
    here we render it as a single horizontal strip so it visually wraps
    up the overview without competing for vertical space."""
    direction = ""
    comparison = ""
    if overview_payload:
        tr = overview_payload.get("trend")
        if isinstance(tr, dict):
            direction = tr.get("direction") or ""
            comparison = L(tr.get("comparison"))
    if not direction and continuity:
        direction = continuity.get("momentum") or ""
        comparison = L(continuity.get("relation_to_yesterday"))
    if not direction and not comparison:
        return ""
    return (
        '<div class="dr-trend-closer" '
        f'title="{esc(T("trend_tip"))}">'
        f'<span class="trend-label">{esc(T("trend_label"))}</span>'
        + (_momentum_chip(direction) if direction else "")
        + (f'<span class="dr-trend-text">{esc_md(comparison)}</span>' if comparison else "")
        + '</div>'
    )


def _render_weekly_daily_timeline_card(con, days: list[str]) -> str:
    """Full-width 7-column horizontal timeline card. Each column is a
    <details> block — summary shows weekday + date + headline; click to
    unfold the day's narrative + a 'jump to /today' link.

    Reads cached ai_overview per day; tolerates v6 (top-level narrative)
    and v10+ (overview.narrative) shapes."""
    if not days:
        return ""
    weekday_labels = [T(f"wd_{i+1}") for i in range(7)]

    cols: list[str] = []
    any_data = False
    for idx, d in enumerate(days):
        wd = weekday_labels[idx] if idx < 7 else "?"
        row = con.execute(
            "SELECT value_json FROM day_channel WHERE date=? AND channel='ai_overview'",
            (d,),
        ).fetchone()
        val = None
        if row and row[0]:
            try:
                val = json.loads(row[0])
            except Exception:
                val = None
        if not val:
            cols.append(
                '<div class="dt-col dt-empty">'
                '<div class="dt-col-head">'
                f'<span class="dt-day-name">{esc(wd)}</span>'
                f'<span class="dt-date">{esc(d[5:])}</span>'
                '</div>'
                f'<div class="dt-headline muted">{esc(T("timeline_no_data"))}</div>'
                '</div>'
            )
            continue
        any_data = True
        headline = L(val.get("headline"))
        ov = val.get("overview")
        if isinstance(ov, dict):
            narrative = L(ov.get("narrative"))
        else:
            narrative = L(val.get("narrative"))

        body_html = ""
        if narrative or headline:
            inner: list[str] = []
            if narrative:
                inner.append(f'<p class="dt-narrative">{esc_md(narrative)}</p>')
            inner.append(
                f'<a class="dt-day-link" href="/today?date={esc(d)}">{esc(T("open_full_day"))}</a>'
            )
            body_html = '<div class="dt-body">' + "".join(inner) + '</div>'

        cols.append(
            '<details class="dt-col" open><summary>'
            '<div class="dt-col-head">'
            f'<span class="dt-day-name">{esc(wd)}</span>'
            f'<span class="dt-date">{esc(d[5:])}</span>'
            '</div>'
            + (f'<div class="dt-headline">{esc(headline or "(无标题)")}</div>')
            + '</summary>'
            + body_html
            + '</details>'
        )

    if not any_data:
        return ""
    toggle_html = (
        '<div class="dt-bulk">'
        # Default to expanded — every <details class="dt-col" open>.
        f'<button type="button" class="dt-bulk-btn active" data-dt-bulk="expand">{esc(T("expand_all"))}</button>'
        f'<button type="button" class="dt-bulk-btn" data-dt-bulk="collapse">{esc(T("collapse_all"))}</button>'
        '</div>'
    )
    bulk_script = (
        '<script>(function(){'
        'document.querySelectorAll(".daily-timeline-card .dt-bulk-btn").forEach(function(b){'
        'b.addEventListener("click",function(){'
        'var open=b.getAttribute("data-dt-bulk")==="expand";'
        'var card=b.closest(".daily-timeline-card");'
        'card.querySelectorAll("details.dt-col").forEach(function(d){d.open=open;});'
        # Sync active state — clicked button becomes active, sibling unsets.
        'card.querySelectorAll(".dt-bulk-btn").forEach(function(x){x.classList.remove("active");});'
        'b.classList.add("active");'
        '});});})();</script>'
    )
    return (
        '<section class="card daily-timeline-card">'
        + _card_head(
            T("timeline_title"),
            tag=T("timeline_tag"), tag_color="narrative",
            hint=T("timeline_hint"),
            right_html=toggle_html,
        )
        + '<div class="dt-grid">'
        + "".join(cols)
        + '</div>'
        + bulk_script +
        '</section>'
    )


def _render_insights_card(overview_payload: dict | None, *, continuity: dict | None = None) -> str:
    """Full-width 3-column Insights card (rolled back from v17's 2-section
    version). Per-project AI summaries now appear *inside* the Tasks table
    as Latest/Next columns, so Insights can stay focused on whole-day
    signals while still surfacing the 3 high-level themes.

      🚀 关键任务进展   highlights  — one task per bullet, today's advance
      ⏰ 时间安排回顾   work_pattern — today vs 7-day baseline, grounded in numbers
      🔔 任务跟进提醒   suggestions  — forward-looking milestones / deadlines
    """
    if not overview_payload:
        return ""

    highlights = overview_payload.get("highlights") or []
    work_pattern = overview_payload.get("work_pattern") or []
    suggestions = overview_payload.get("suggestions")
    if not suggestions:
        suggestions = overview_payload.get("recommendations") or []

    if not (highlights or work_pattern or suggestions):
        return ""

    import re as _re_local
    _strip_re = _re_local.compile(r"^[🚀⏰🔔📌✨📰💡📊🎯]\s*")

    _COL_KEYS = {
        "highlights":   ("insights_progress", "tip_progress"),
        "work_pattern": ("insights_pattern",  "tip_pattern"),
        "suggestions":  ("insights_followup", "tip_followup"),
    }

    def _col(emoji: str, key: str, items: list) -> str:
        label_key, tip_key = _COL_KEYS[key]
        texts = [L(x) for x in items]
        cleaned = [_strip_re.sub("", t.lstrip()).strip() for t in texts if t]
        body_html = (
            f'<ul>{"".join(f"<li>{esc_md(x)}</li>" for x in cleaned)}</ul>' if cleaned
            else f'<div class="muted">{esc(T("insights_none"))}</div>'
        )
        return (
            '<div class="insights-col">'
            f'<h4 title="{esc(T(tip_key))}">{emoji} {esc(T(label_key))}</h4>'
            f'{body_html}'
            '</div>'
        )

    return (
        '<section class="card insights-card">'
        + _card_head(T("insights_title"), tag="AI", tag_color="narrative", hint=T("insights_hint"))
        + '<div class="insights-grid">'
        + _col("🚀", "highlights",   highlights)
        + _col("⏰", "work_pattern", work_pattern)
        + _col("🔔", "suggestions",  suggestions)
        + '</div>'
        '</section>'
    )



# Back-compat shim — older callers (day_report_table) reference
# _render_highlights_panel; map it onto the new Insights card.
def _render_highlights_panel(overview_payload: dict | None) -> str:
    return _render_insights_card(overview_payload)


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


def _render_stats_strip_compact(header: dict, channels: dict[str, str | None], *, ai_cost: float | None = None) -> str:
    """4-tile compact stats strip for the daily report card. Mirrors the
    weekly card's 4-tile layout. Tiles:
      事件总数 (+ 切换 N 次) · 活跃总时长 · 最长专注 (+ 时段·项目) · AI 花费 (当天累计)
    时间跨度 dropped — value was inaccurate and just visual noise.
    峰值 dropped — irrelevant in a daily context."""
    switches = _safe_load_json(channels.get("context_switches")) or {}
    longest  = _safe_load_json(channels.get("longest_focus_block")) or {}

    sw_count = switches.get("count", 0)

    if longest:
        focus_num = _format_duration_short(longest.get("duration_min", 0))
        focus_sub = (
            f'{esc(longest.get("start","?"))}–{esc(longest.get("end","?"))} · '
            f'{esc(longest.get("dominant_project","?"))}'
        )
    else:
        focus_num = "—"
        focus_sub = "(无)"

    cost_num = f"${ai_cost:.3f}" if ai_cost is not None else "—"
    cost_sub = T("stat_today_total") if ai_cost is not None else T("stat_no_ai")

    def _tile(num: str, lbl: str, sub: str | None) -> str:
        sub_html = (
            f'<span class="muted" style="font-size:10.5px; margin-top:2px;">{sub}</span>'
            if sub else ""
        )
        return (
            '<div class="dr-stat">'
            f'<span class="dr-stat-num">{esc(num)}</span>'
            f'<span class="dr-stat-lbl">{esc(lbl)}</span>'
            f'{sub_html}'
            '</div>'
        )

    return (
        '<div class="dr-stats-compact">'
        + _tile(str(header["total_events"]), T("stat_events"), T("stat_switches", n=sw_count))
        + _tile(_format_duration_short(header["active_minutes"]), T("stat_active"), None)
        + _tile(focus_num, T("stat_longest_focus"), focus_sub)
        + _tile(cost_num, T("stat_ai_cost"), cost_sub)
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
      <a href="/events?start_from={esc(date_val)}&start_to={esc(date_val)}">{esc(T("db_view_raw"))}</a>
    </div>
  </div>
  {_render_overview_section(overview)}
  {_render_insights_card(overview, continuity=continuity)}
  {_render_facts_block(date_val, channels)}
  {_render_raw_channels_block(channels)}
</div>
"""


def _format_generic_value(val, col_spec: dict) -> tuple[str, str]:
    """Return (html_inner, css_class) for one cell. col_spec drives format
    (`fmt`: pct/money), truncation, mono-vs-text styling."""
    classes: list[str] = []
    if col_spec.get("mono"):
        classes.append("mono")
    if col_spec.get("muted"):
        classes.append("muted")
    if val is None:
        return '<span class="muted">∅</span>', " ".join(classes)
    fmt = col_spec.get("fmt")
    if fmt == "pct":
        try:
            return f"{float(val) * 100:.0f}%", " ".join(classes + ["num"])
        except (TypeError, ValueError):
            pass
    if fmt == "money":
        try:
            return f"${float(val):.4f}", " ".join(classes + ["num"])
        except (TypeError, ValueError):
            pass
    s = str(val)
    # JSON detection: prettify and render in a max-height pre
    stripped = s.strip()
    if (stripped.startswith("{") and stripped.endswith("}")) or \
       (stripped.startswith("[") and stripped.endswith("]")):
        try:
            parsed = json.loads(stripped)
            pretty = json.dumps(parsed, ensure_ascii=False, indent=2)
            return f'<pre class="gt-json">{esc(pretty)}</pre>', " ".join(classes + ["json-cell"])
        except json.JSONDecodeError:
            pass
    truncate = col_spec.get("truncate")
    if truncate and len(s) > truncate:
        return (
            f'<span title="{esc(s)}">{esc(s[:truncate])}…</span>',
            " ".join(classes + ["truncate"]),
        )
    # Numeric-ish: right-align
    try:
        float(s)
        if classes and "mono" not in classes:
            pass
        else:
            classes.append("num")
    except (TypeError, ValueError):
        pass
    return esc(s), " ".join(classes)


def generic_table_page(db_path: Path, table_key: str, qs: dict[str, list[str]]) -> str:
    """Render any DB table whose `TABLE_VIEWS[key]` entry is `kind='generic'`.
    Columns come from the entry's `cols` (curated, with width + label_zh/en),
    or from PRAGMA table_info if `cols=None`.

    No filtering UI here on purpose — this is a transparency surface, not a
    secondary report builder. Limit + order are read from QS so the user can
    paginate / sort manually."""
    con = connect(db_path)
    init_db(con)
    cfg = TABLE_VIEWS.get(table_key)
    if not cfg or cfg.get("kind") != "generic":
        return layout("DayTrace · ?", "unknown table", "events", "<div class='card'>unknown table</div>")

    sql_table = cfg.get("sql_table", table_key)
    cols = cfg.get("cols")
    # Discover the real schema so config typos don't crash the page —
    # we filter declared cols against actual table columns and skip any
    # that don't exist. Also drives the cols=None auto-mode.
    info_rows = con.execute(f"PRAGMA table_info({sql_table})").fetchall()
    real_cols = {r["name"] for r in info_rows}
    if cols is None:
        cols = [
            {"name": r["name"], "zh": r["name"], "en": r["name"], "width": 140}
            for r in info_rows
        ]
    else:
        cols = [c for c in cols if c["name"] in real_cols]

    # Build SELECT clause from declared columns. Avoid `SELECT *` so removing
    # a config column is enough to hide it without DB-side changes.
    col_names = [c["name"] for c in cols]
    select_clause = ", ".join(f'"{n}"' for n in col_names)
    order_clause = cfg.get("default_order") or col_names[0]
    try:
        limit = max(10, min(2000, int(qs.get("limit", ["500"])[0])))
    except ValueError:
        limit = 500
    rows = con.execute(
        f'SELECT {select_clause} FROM {sql_table} ORDER BY {order_clause} LIMIT ?',
        (limit,),
    ).fetchall()
    total = con.execute(f'SELECT COUNT(*) FROM {sql_table}').fetchone()[0]

    _lang = _CURRENT_LANG.get()
    # <colgroup> with fixed widths to address the long-standing "wide columns
    # eat the screen" complaint.
    colgroup = "".join(
        f'<col style="width:{c.get("width", 140)}px">' for c in cols
    )
    head_cells = "".join(
        f'<th>{esc(c.get(_lang) or c.get("en") or c["name"])}</th>'
        for c in cols
    )
    body_rows: list[str] = []
    for r in rows:
        cells: list[str] = []
        for c in cols:
            html_inner, klass = _format_generic_value(r[c["name"]], c)
            cells.append(f'<td class="{klass}">{html_inner}</td>')
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    body = "".join(body_rows) or f'<tr><td colspan="{len(cols)}" class="muted">{esc(T("db_no_events"))}</td></tr>'

    table_label = T(cfg["label_key"])
    meta = (
        '<div class="generic-table-meta">'
        f'<span><span class="gt-name">{esc(sql_table)}</span></span>'
        f'<span><span class="gt-count">{len(rows)}</span> / {total} rows</span>'
        '</div>'
    )
    content = (
        table_switcher_html(table_key, qs)
        + meta
        + '<div class="table-wrap">'
        + f'<table class="generic-table"><colgroup>{colgroup}</colgroup>'
        + f'<thead><tr>{head_cells}</tr></thead>'
        + f'<tbody>{body}</tbody></table>'
        + '</div>'
    )
    return layout(
        f"DayTrace · {table_label}", f"{len(rows)} / {total} rows",
        "events", content,
    )


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

    cards_html = "\n".join(cards) or f'<div class="card label">{esc(T("db_empty_day"))}</div>'
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
        f"DayTrace · {T('db_tab_day')}",
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

    # Project + tasks merged in one cell. tasks column is JSON list of
    # {title, title_en, n}; we localize via _CURRENT_LANG and show the
    # top few inline. Long tail collapsed into a "+N more" muted chip.
    project_link = (
        f'<a class="project-chip" href="/events?project={esc(row["project"])}'
        f'&start_from={esc(row["date"])}&start_to={esc(row["date"])}">'
        f'{esc(row["project"])}</a>'
    )
    tasks_raw = _safe_load_json(row.get("tasks")) or []
    _lang = _CURRENT_LANG.get()
    task_chips_html = ""
    if isinstance(tasks_raw, list) and tasks_raw:
        chips: list[str] = []
        TOP = 3
        for t in tasks_raw[:TOP]:
            if not isinstance(t, dict):
                continue
            ttl_en = (t.get("title_en") or "").strip()
            ttl_zh = (t.get("title") or "").strip()
            ttl = ttl_en if (_lang == "en" and ttl_en) else ttl_zh
            n = t.get("n") or 0
            chips.append(
                f'<span class="task-chip" title="{esc(ttl_zh)} · {n} events">'
                f'{esc(ttl)}'
                + (f' <span class="muted">·{n}</span>' if n else '')
                + '</span>'
            )
        if len(tasks_raw) > TOP:
            chips.append(
                f'<span class="task-chip muted">+{len(tasks_raw) - TOP}</span>'
            )
        task_chips_html = '<div class="task-chip-row">' + "".join(chips) + '</div>'

    project_task_cell = (
        f'<div class="project-task-cell">{project_link}{task_chips_html}</div>'
    )

    # AI 摘要 → 3 columns:
    #   1) 概要 + 做了什么  (summary + what_was_done bullets)
    #   2) 下一步             (next_steps bullets)
    #   3) 对比上次           (continuity momentum + relation_to_previous)
    summary_text = L(summary.get("summary")) if isinstance(summary, dict) else ""
    what_was_done = _ai_list(summary.get("what_was_done")) if isinstance(summary, dict) else None
    status = summary.get("status") if isinstance(summary, dict) else None
    next_steps = _ai_list(summary.get("next_steps")) if isinstance(summary, dict) else None

    summary_cell_parts: list[str] = []
    if status:
        summary_cell_parts.append(_status_chip(status))
    summary_cell_parts.append(
        f'<div class="ai-summary-text">{esc(summary_text or "—")}</div>'
    )
    if what_was_done:
        summary_cell_parts.append(
            "<ul class='ai-bullets'>"
            + "".join(f"<li>{esc(w)}</li>" for w in what_was_done[:4])
            + "</ul>"
        )
    summary_cell = "".join(summary_cell_parts)

    next_cell = ""
    if next_steps:
        next_cell = (
            "<ul class='ai-bullets ai-next'>"
            + "".join(f"<li>{esc(n)}</li>" for n in next_steps[:3])
            + "</ul>"
        )

    cont_cell = ""
    if isinstance(continuity, dict) and continuity:
        cont_cell = (
            f'{_momentum_chip(continuity.get("momentum"))}'
            f'<div class="cont-text">{esc(L(continuity.get("relation_to_previous")))}</div>'
        )

    return (
        f'<tr>'
        f'<td class="col-date"><a href="/events?table=day&date={esc(row["date"])}">{esc(row["date"])}</a></td>'
        f'<td class="col-project-task">{project_task_cell}</td>'
        f'<td class="col-num">{row["event_count"]}</td>'
        f'<td class="col-num">{_format_duration_short(row["active_minutes"])}</td>'
        f'<td class="col-ai">{summary_cell or "—"}</td>'
        f'<td class="col-ai">{next_cell or "—"}</td>'
        f'<td class="col-cont">{cont_cell or "—"}</td>'
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
        SELECT date, project, event_count, active_minutes, share, tasks, updated_at
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
        allow_all=True, label_text=T("dp_label_date"),
    )
    filter_strip = (
        '<div class="dr-filter-strip">'
        f'{cal_widget}'
        '<form method="get" action="/events" class="dr-filter-inline">'
        '<input type="hidden" name="table" value="day_project">'
        f'<input type="hidden" name="date" value="{esc(selected_date or "")}">'
        f'<label>📁 {esc(T("dp_label_project"))} <select name="project" onchange="this.form.submit()">'
        f'<option value="">{esc(T("db_filter_all"))}</option>{opts(all_projects, selected_project)}</select></label> '
        f'<label>🏷 {esc(T("dp_label_status"))} <select name="status" onchange="this.form.submit()">'
        f'<option value="">{esc(T("db_filter_all"))}</option>{opts(statuses, selected_status)}</select></label> '
        f'<label>↕ {esc(T("dp_label_order"))} <select name="order" onchange="this.form.submit()">{sort_opts}</select></label> '
        f'<a href="/events?table=day_project" class="dr-reset">{esc(T("db_reset_filters"))}</a>'
        f'<span class="muted small dr-rowcount">{esc(T("dp_rowcount", n=len(rendered)))}</span>'
        '</form>'
        '</div>'
    )

    head = (
        "<thead><tr>"
        f"<th class='col-date'>{esc(T('dp_th_date'))}</th>"
        f"<th class='col-project-task'>{esc(T('dp_th_project_task'))}</th>"
        f"<th class='col-num'>{esc(T('dp_th_events'))}</th>"
        f"<th class='col-num'>{esc(T('dp_th_active'))}</th>"
        f"<th class='col-ai'>{esc(T('dp_th_summary'))}</th>"
        f"<th class='col-ai'>{esc(T('dp_th_next'))}</th>"
        f"<th class='col-cont'>{esc(T('dp_th_cont'))}</th>"
        "</tr></thead>"
    )
    body = "".join(rendered) or f'<tr><td colspan="7" class="label">{esc(T("db_empty_dp"))}</td></tr>'
    table_html = f'<div class="table-wrap dpr-table"><table>{head}<tbody>{body}</tbody></table></div>'

    content = table_switcher_html("day_project", qs) + filter_strip + table_html
    return layout(
        f"DayTrace · {T('db_tab_day_project')}",
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
    # Enrich with AI activity labels (bilingual since v15).
    from daytrace.db import load_activity_labels_for_event_ids
    _cur_lang = _CURRENT_LANG.get()
    labels = load_activity_labels_for_event_ids(con, [e["id"] for e in events], lang=_cur_lang)
    _unclassified = "Unclassified" if _cur_lang == "en" else "未分类"
    for ev in events:
        ev["activity"] = labels.get(ev["id"], _unclassified)
    if activity_filter:
        events = [e for e in events if e.get("activity") == activity_filter]
        if event_limit:
            events = events[:event_limit]
    options: dict[str, Any] = query_filter_options(con, option_filters)
    options["date_counts"] = available_event_date_counts(con, VISIBLE_EVENT_SOURCES)
    options["source"] = [{"value": "", "label": T("db_filter_all")}] + [
        {"value": source_value, "label": SOURCE_LABELS[source_value]}
        for source_value in VISIBLE_EVENT_SOURCES
    ]
    # Build the activity dropdown options from currently visible label set.
    activity_values = sorted({e["activity"] for e in events if e.get("activity")})
    options["activity"] = [{"value": "", "label": T("db_filter_all")}] + [
        {"value": a, "label": a} for a in activity_values
    ]
    content = table_switcher_html("events", qs) + events_table(events, filters, options)
    return layout(f"DayTrace · {T('events_table_t')}", f"{len(events)} events", "events", content)


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


def _short_weekday(date_str: str) -> str:
    """Localized short weekday label for a YYYY-MM-DD date.
    Returns '周一' / 'Mon' / etc. based on the current request language."""
    try:
        from datetime import date as _d_local
        wd_idx = _d_local.fromisoformat(date_str).weekday()  # 0 = Monday
        return T(f"wd_{wd_idx + 1}")
    except Exception:
        return ""

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
    _lang = _CURRENT_LANG.get()
    if stack_by == "project":
        return _project_of(ev)
    if stack_by == "task":
        _no_task = "Unlinked" if _lang == "en" else "未对应任务"
        return str(ev.get("task") or _no_task)
    if stack_by == "activity":
        _unc = "Unclassified" if _lang == "en" else "未分类"
        return str(ev.get("activity") or _unc)
    if stack_by == "device":
        return str(ev.get("device_id") or "unknown")
    if stack_by == "location":
        return str(ev.get("location_id") or "unknown")
    return str(ev.get(stack_by) or "unknown")  # source / device_id / etc


def _enrich_events_with_tasks(con, events: list[dict]) -> list[dict]:
    """Stamp `ev["task"]` with the linked work_item title — OR a collapsed
    label when the work_item's table is flagged `collapse_in_dim` in
    config/work_items.yaml.

    Why collapse: the 审稿 table has 33+ individual manuscript rows; in
    the Chart panel's 任务 dim those would each show up as separate
    buckets and crowd out the real tasks. Collapsing folds them all to
    "审稿" so the dim view stays readable. The Tasks panel still lists
    each review row individually."""
    if not events:
        return events
    has_wi = con.execute("SELECT 1 FROM work_items LIMIT 1").fetchone()
    if not has_wi:
        for ev in events:
            ev.setdefault("task", None)
        return events

    # Build collapse map: table_key → collapsed_label
    # In EN mode, prefer `collapsed_label_en` from work_items.yaml when set.
    _lang = _CURRENT_LANG.get()
    collapse_map: dict[str, str] = {}
    try:
        from daytrace.work_items import load_config
        cfg = load_config()
        for t in (cfg or {}).get("tables", []):
            if t.get("collapse_in_dim"):
                label = None
                if _lang == "en":
                    label = t.get("collapsed_label_en") or t.get("name_en")
                label = label or t.get("collapsed_label") or t.get("name") or t["key"]
                collapse_map[t["key"]] = label
    except Exception:
        pass

    event_ids = [e["id"] for e in events if e.get("id")]
    if not event_ids:
        return events
    title_map: dict[str, str] = {}
    chunk = 900
    for i in range(0, len(event_ids), chunk):
        sub = event_ids[i:i+chunk]
        ph = ",".join("?" * len(sub))
        for r in con.execute(
            f"""
            SELECT l.event_id, w.title, w.title_en, w.table_key
              FROM event_work_item_links l
              JOIN work_items w ON w.record_id = l.record_id
             WHERE l.event_id IN ({ph})
            """, sub
        ).fetchall():
            tk = r["table_key"] or "tasks"
            if tk in collapse_map:
                title_map[r["event_id"]] = collapse_map[tk]
            else:
                if _lang == "en":
                    en = (r["title_en"] or "").strip() if "title_en" in r.keys() else ""
                    title_map[r["event_id"]] = en or r["title"]
                else:
                    title_map[r["event_id"]] = r["title"]
    for ev in events:
        ev["task"] = title_map.get(ev.get("id"))
    return events


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


# Weekly pills — the *labels* on these tuples are placeholders. The
# template renders them via T() (see _weekly_unit_opts / _weekly_dim_opts
# below), so the cookie language flips them on the fly.
_WEEKLY_UNIT_OPTS = [("hours", "Hours"), ("count", "Events"), ("chars", "Chars")]
_WEEKLY_DIM_OPTS = [("source",   "Source"), ("project",  "Project"),
                    ("task",     "Task"),   ("device",   "Device"),
                    ("activity", "Activity")]
_WEEKLY_VIEW_OPTS = [("swim", "Swimlane"), ("heat", "Heatmap")]


def _weekly_unit_opts():
    return [
        ("hours", T("unit_hours")),
        ("count", T("unit_count")),
        ("chars", T("unit_chars")),
    ]


def _weekly_dim_opts():
    return [
        ("source",   T("dim_source")),
        ("project",  T("dim_project")),
        ("task",     T("dim_task")),
        ("device",   T("dim_device")),
        ("activity", T("dim_activity")),
    ]


def _weekly_view_opts():
    return [
        ("swim", T("view_swim", lang=None) if "view_swim" in _STRINGS else "Swimlane"),
        ("heat", T("view_heat", lang=None) if "view_heat" in _STRINGS else "Heatmap"),
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
    href_for: callable, param_name: str | None = None,
) -> str:
    """Render a `.dim-tabs` / `.unit-tabs` pill group with active state.

    `param_name` (e.g. "mode" / "unit") is stamped onto each anchor as
    `data-param` + `data-value`. The page-level JS reads these and rebuilds
    the target URL off `location.href` at click time — so any state that
    other handlers set via replaceState (view, top_view, swim_filter…)
    survives navigation instead of being baked in at server-render time."""
    chips = []
    for value, label in options:
        cls = f"{css_class} active" if value == current else css_class
        data_attrs = ""
        if param_name:
            data_attrs = f' data-param="{esc(param_name)}" data-value="{esc(value)}"'
        chips.append(
            f'<a class="{cls}"{data_attrs} href="{esc(href_for(value))}">{esc(label)}</a>'
        )
    container = "dim-tabs" if "dim-tab" in css_class else "unit-tabs"
    return f'<div class="{container}">{"".join(chips)}</div>'


def _weekly_header_controls(
    *, db_path: Path, week: str, prev_week: str, next_week: str,
    mode: str, unit: str, view: str, monday: str, sunday: str,
) -> str:
    """Header layout matches daily exactly:
       [← prev] [📅 calendar picker] [next →]  [open db ↗]  [dim pills]

    Calendar picker uses the same calendar_control as daily; clicking any
    day navigates to /weekly?date=YYYY-MM-DD which the route converts
    server-side into the matching ISO-week."""
    prev_html = (
        f'<a class="hdr-nav-btn" title="上一周 {esc(prev_week)}" '
        f'href="{_weekly_url(week=prev_week, mode=mode, unit=unit, view=view)}">←</a>'
    )
    next_html = (
        f'<a class="hdr-nav-btn" title="下一周 {esc(next_week)}" '
        f'href="{_weekly_url(week=next_week, mode=mode, unit=unit, view=view)}">→</a>'
    )
    # Database button removed from the centered header — right-rail's
    # .page-db-btn covers the same affordance. (See daily-side comment.)
    open_db_html = ""
    # Week picker: feed daily-style calendar with Monday as the selected date.
    # When user clicks any day, route /weekly?date= maps it to that day's week.
    # available_dates list comes from the events DB so days-with-data are highlighted.
    cal_hidden = {
        "mode": mode if mode != "project" else None,
        "unit": unit if unit != "hours" else None,
        "view": view if view != "swim" else None,
    }
    cal_hidden = {k: v for k, v in cal_hidden.items() if v}
    available = available_dates(connect(db_path))
    # Override the label so it shows the WEEK (e.g. "2026-W20") instead of
    # the picked day's date. We can't easily pass a custom label into
    # calendar_control without changing its signature, so we post-process.
    raw_picker = calendar_control(
        '/weekly', monday, available, hidden=cal_hidden, label_text="",
    )
    import re as _re
    week_label = f"{esc(week)} ({esc(monday[5:])}~{esc(sunday[5:])})"
    picker_html = _re.sub(
        r'(<summary>📅 )[^▾]+( ▾</summary>)',
        lambda m: m.group(1) + week_label + m.group(2),
        raw_picker, count=1,
    )

    dim_bar_html = _pill_bar(
        css_class="dim-tab", options=_weekly_dim_opts(), current=mode,
        href_for=lambda v: _weekly_url(week=week, mode=v, unit=unit, view=view),
        param_name="mode",
    )
    return (
        '<div class="header-controls">'
        + prev_html + picker_html + next_html
        + open_db_html
        + dim_bar_html
        + '</div>'
    )


def _view_switcher_pills(*, week: str, mode: str, unit: str, view: str) -> str:
    """The 直方图 / 泳道 / 热力图 pill row inside the main viz card."""
    return _pill_bar(
        css_class="dim-tab", options=_weekly_view_opts(), current=view,
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
    swim_filter: str = "all",
) -> str:
    """7-day swim-lane reusing the daily timeline-card's CSS classes
    (.tl-swim-row, .tl-swim-track, .tl-swim-tick, .tl-tooltip, .tl-tip-*)
    so hover, scaling, and tooltip styling are identical to /today.

    Layout:
      - one shared X-axis at the top (04, 06, 08, ..., 04) using .tl-hour markers
      - 7 .tl-swim-row's, one per shifted-day, with day label + .tl-swim-track
      - .tl-legend strip with palette swatches + counts
      - .tl-tooltip element for the JS to populate on hover
    """
    from datetime import datetime, date as _date, timedelta
    from collections import Counter
    if not events:
        return ""

    boundary_min = (boundary_hour % 24) * 60
    days_set = set(days)
    OTHER = _WEEKLY_OTHER_COLOR

    def shifted_pos_min(dt: datetime) -> int:
        m = dt.hour * 60 + dt.minute
        return (m - boundary_min) % (24 * 60)

    # Bucket ticks per day, capture full event metadata for tooltip
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
            "date": d,
            "value": v,
            "source": ev.get("source") or "other",
            "project": ev.get("project") or "misc",
            "device": ev.get("device_id") or "unknown",
            "activity": ev.get("activity") or "未分类",
        })

    # Top X-axis: 13 hour markers (every 2h), starting from boundary
    LABEL_W = 70
    hour_labels = "".join(
        f'<div style="position:absolute; left:{(i/12)*100:.4f}%; transform:translateX(-50%); '
        f'font-size:10px; color:var(--muted); font-variant-numeric:tabular-nums;">'
        f'{(boundary_hour + i*2) % 24:02d}</div>'
        for i in range(13)
    )

    # Inline vertical grid lines drawn inside each track (so they line up
    # with the top axis labels regardless of the 70px label-column offset)
    grid_lines = "".join(
        f'<div style="position:absolute; left:{(i/12)*100:.4f}%; top:0; bottom:0; '
        f'width:1px; background:rgba(0,0,0,0.045); pointer-events:none;"></div>'
        for i in range(1, 12)  # skip 0 and 12 (left/right edges)
    )

    rows_html = []
    overall_counts: Counter = Counter()
    for d in days:
        wd = _short_weekday(d)  # "周一" / "Mon" / etc.
        ticks = per_day_ticks[d]
        ticks_html = "".join(
            f'<span class="tl-swim-tick" '
            f'data-time="{esc(t["time"])}" data-date="{esc(t["date"])}" '
            f'data-source="{esc(t["source"])}" data-project="{esc(t["project"])}" '
            f'data-device="{esc(t["device"])}" data-activity="{esc(t["activity"])}" '
            f'data-title="{esc(t["title"])}" data-value="{esc(t["value"])}" '
            f'style="left:{t["pos"]:.4f}%; background:{t["color"]};"></span>'
            for t in ticks
        )
        for t in ticks:
            overall_counts[t["value"]] += 1
        rows_html.append(
            '<div class="tl-swim-row">'
            f'<div class="tl-swim-label" style="border-left:3px solid #e6dcc6;">'
            f'<span class="tl-swim-name">{wd} <span class="muted" style="font-size:11px; font-weight:500;">{esc(d[5:])}</span></span>'
            f'<span class="tl-swim-count muted" data-row-count="1">×{len(ticks)}</span>'
            '</div>'
            f'<div class="tl-swim-track">{grid_lines}{ticks_html}</div>'
            '</div>'
        )

    # Filter pill bar has moved up to the bottom-card root so swim AND heat
    # share it. This card just emits the swim DOM + tooltip + JS for tick hover.

    # Top axis row, aligned to where the .tl-swim-track starts (offset matches
    # .tl-swim-row's first column = 160px from the daily CSS).
    AXIS_OFFSET = 160 + 10  # 160px label col + 10px gap
    top_axis = (
        f'<div class="tl-swim-row" style="padding:0 0 6px;">'
        f'<div class="tl-swim-label"></div>'
        f'<div style="position:relative; height:14px;">{hour_labels}</div>'
        '</div>'
    )

    # Legend dropped — the filter pill bar above already serves as the
    # color key (each pill shows swatch + name + count), so repeating it
    # below was just visual noise.
    legend_html = ""

    # Tooltip element + JS that wires up .tl-swim-tick hover (scoped to
    # .weekly-swim so it doesn't clash with the daily timeline-card's JS).
    tooltip_html = '<div class="tl-tooltip" hidden></div>'

    js_html = (
        '<script>(function(){'
        'var card=document.currentScript&&document.currentScript.closest(".weekly-swim");'
        'if(!card)return;'
        'var tip=card.querySelector(".tl-tooltip");'
        'function esc(s){return String(s==null?"":s).replace(/[&<>\\"]/g,function(c){return ({"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;"})[c];});}'
        'function chip(label,val){if(!val)return ""; return "<span class=\\"tl-tip-chip\\"><b>"+label+"</b> "+esc(val)+"</span>";}'
        'function show(html,ev){if(!tip)return;tip.innerHTML=html;tip.hidden=false;'
        'var r=card.getBoundingClientRect();'
        'var x=ev.clientX-r.left+12;var y=ev.clientY-r.top+12;'
        'var w=tip.offsetWidth;if(x+w>card.clientWidth-8)x=card.clientWidth-w-8;'
        'tip.style.left=x+"px";tip.style.top=y+"px";}'
        'function hide(){if(tip)tip.hidden=true;}'
        # Tick hover tooltip (filter logic now lives in page-level JS so both
        # swim and heat respond to the same shared pill bar)
        'card.querySelectorAll(".tl-swim-tick").forEach(function(el){'
        'el.addEventListener("mousemove",function(ev){'
        'var html="<div class=\\"tl-tip-time\\">"+esc(el.dataset.date+" "+el.dataset.time)+"</div>"+'
        '"<div class=\\"tl-tip-title\\">"+esc(el.dataset.title||"(无标题)")+"</div>"+'
        'chip("项目",el.dataset.project)+chip("来源",el.dataset.source)+'
        'chip("活动",el.dataset.activity)+chip("设备",el.dataset.device);'
        'show(html,ev);});'
        'el.addEventListener("mouseleave",hide);});'
        '})();</script>'
    )

    return (
        # The .timeline-card class pulls in tooltip / tick / track styling
        # from the daily timeline CSS. .weekly-swim is our own scope tag.
        '<div class="timeline-card weekly-swim" style="position:relative; padding:0;">'
        + top_axis
        + "".join(rows_html) +
        legend_html +
        tooltip_html +
        js_html +
        '<div class="muted small" style="margin-top:6px;">'
        f'{esc(T("swim_help", hh=f"{boundary_hour:02d}"))}'
        '</div>'
        '</div>'
    )


def _compute_palette_for_week(
    per_day: dict[str, dict[str, float]],
) -> tuple[list[str], dict[str, str], "Counter"]:
    """Top-N dim values across the week → distinct palette colors, rest grey.
    Returns (top_names, palette, overall_counter). The Counter is the summed
    per-dim totals across the week — distribution view + breakdown tables
    can reuse it instead of re-aggregating."""
    from collections import Counter
    overall: Counter = Counter()
    for bag in per_day.values():
        for k, v in bag.items():
            overall[k] += v
    top = [n for n, _ in overall.most_common(10)]
    palette = _palette_for(top)
    palette[T("other_bucket")] = _WEEKLY_OTHER_COLOR
    palette["其它"] = _WEEKLY_OTHER_COLOR  # back-compat: zh-default callers
    return top, palette, overall


def _nice_axis_max(raw_max: float, unit: str) -> tuple[float, list[float]]:
    """Round raw_max up to a nice number and pick 4-5 evenly-spaced ticks.
    Mirrors the convention the daily report's tl-y-tick uses."""
    if raw_max <= 0:
        return 1.0, [0]
    # candidate step sizes per unit — pick the smallest step that puts
    # at most 5 ticks between 0 and raw_max
    import math
    if unit == "hours":
        candidates = [0.5, 1, 2, 3, 4, 6, 8, 12]
    elif unit == "chars":
        candidates = [500, 1000, 2000, 5000, 10000, 20000, 50000, 100000]
    else:  # count
        candidates = [5, 10, 20, 50, 100, 200, 500, 1000, 2000]
    for step in candidates:
        n_ticks = math.ceil(raw_max / step)
        if n_ticks <= 5:
            nice_max = step * n_ticks
            ticks = [step * i for i in range(n_ticks + 1)]
            return nice_max, ticks
    step = candidates[-1]
    n_ticks = math.ceil(raw_max / step)
    return step * n_ticks, [step * i for i in range(n_ticks + 1)]


def _main_chart_card(
    *, days: list[str], per_day: dict[str, dict[str, float]],
    per_day_totals: dict[str, float], unit: str, stack_by: str,
    top_names: list[str], palette: dict[str, str],
    chart_height_px: int = 200,
) -> str:
    """7-day stacked bar chart with Y-axis ticks, grid lines, and a legend.

    Layout:
      ┌──────────────────────────────────────┐
      │  ┃ Y-axis labels    [stacked bars]  │
      │  ┃ (e.g. 8h, 4h, 0)                  │
      │  └──── 日期标签 ───────────────────  │
      │  legend swatches                      │
      └──────────────────────────────────────┘"""
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
            kept.append((T("other_bucket"), other))
        return kept

    raw_max = max(per_day_totals.values()) if per_day_totals else 0
    if raw_max <= 0:
        return f'<div class="muted">{esc(T("no_dim_data"))}</div>'

    axis_max, axis_ticks = _nice_axis_max(raw_max, unit)

    # Y-axis tick labels (left of chart) and horizontal grid lines (across chart)
    Y_AXIS_W = 40
    y_ticks_html = []
    grid_lines_html = []
    for t in axis_ticks:
        pct_from_bottom = (t / axis_max) * 100
        y_ticks_html.append(
            f'<div style="position:absolute; right:6px; bottom:calc({pct_from_bottom:.2f}% - 7px); '
            f'font-size:10px; color:var(--muted); font-variant-numeric:tabular-nums; line-height:1;">'
            f'{_format_value(t, unit)}</div>'
        )
        grid_lines_html.append(
            f'<div style="position:absolute; left:0; right:0; bottom:{pct_from_bottom:.2f}%; '
            f'height:0; border-top:1px dashed #d9ccaf; pointer-events:none;"></div>'
        )

    # Each day = one stacked bar (anchored to bottom of chart area)
    bars_html = []
    x_labels_html = []
    for d in days:
        bag = fold(per_day.get(d, {}))
        total = per_day_totals.get(d, 0.0)
        wd = _short_weekday(d)  # "周一" / "Mon" / etc.
        bar_pct = (total / axis_max) * 100
        segments = []
        tooltip = f"{d} {wd} · {_format_value(total, unit)}\n" + "\n".join(
            f"  {k}: {_format_value(v, unit)}" for k, v in bag if v > 0
        )
        for k, v in bag:
            if v <= 0:
                continue
            seg_pct = (v / total) * 100 if total > 0 else 0
            color = palette.get(k, _WEEKLY_OTHER_COLOR)
            segments.append(
                f'<div title="{esc(k)}: {_format_value(v, unit)}" '
                f'style="height:{seg_pct:.2f}%; background:{color}; '
                f'border-bottom:1px solid rgba(255,255,255,0.55);"></div>'
            )
        bars_html.append(
            f'<div title="{esc(tooltip)}" '
            f'style="flex:1; min-width:0; display:flex; justify-content:center; align-items:flex-end; '
            f'height:100%; position:relative; z-index:1;">'
            f'<div style="width:62%; height:{bar_pct:.2f}%; display:flex; flex-direction:column-reverse; '
            f'border-radius:4px 4px 0 0; overflow:hidden; background:#f1ece2; min-height:2px;">'
            + "".join(segments) +
            '</div>'
            # value above the bar
            f'<div style="position:absolute; left:0; right:0; bottom:calc({bar_pct:.2f}% + 4px); '
            f'text-align:center; font-size:10.5px; font-weight:700; color:var(--ink); '
            f'font-variant-numeric:tabular-nums; pointer-events:none;">{_format_value(total, unit)}</div>'
            '</div>'
        )
        x_labels_html.append(
            f'<div style="flex:1; min-width:0; text-align:center; padding-top:6px;">'
            f'<div style="font-size:11px; font-weight:600; color:#3b352e;">{wd}</div>'
            f'<div style="font-size:10px; color:#bbb; font-variant-numeric:tabular-nums;">{esc(d[5:])}</div>'
            '</div>'
        )

    # Legend strip (top-N values with palette swatches + totals)
    legend_items = "".join(
        f'<span class="tl-legend-item">'
        f'<span class="tl-swatch" style="background:{palette.get(k, _WEEKLY_OTHER_COLOR)};"></span>'
        f'{esc(k)} <span class="muted">{esc(_format_value(overall[k], unit))}</span>'
        '</span>'
        for k in top if overall.get(k, 0) > 0
    )
    legend_html = (
        '<div style="display:flex; flex-wrap:wrap; gap:6px 14px; padding:8px 8px 0;'
        f' margin-left:{Y_AXIS_W}px; border-top:1px dashed #eadfcd; font-size:12px;">'
        + legend_items +
        '</div>'
    ) if legend_items else ""

    return (
        '<div style="padding:8px 4px 4px;">'
        f'<div style="display:flex; align-items:stretch; height:{chart_height_px}px;">'
        f'<div style="position:relative; width:{Y_AXIS_W}px; flex:none;">'
        + "".join(y_ticks_html) +
        '</div>'
        '<div style="position:relative; flex:1;">'
        + "".join(grid_lines_html) +
        '<div style="position:absolute; inset:0; display:flex; gap:6px; align-items:flex-end;">'
        + "".join(bars_html) +
        '</div></div>'
        '</div>'
        f'<div style="display:flex; gap:6px; padding-left:{Y_AXIS_W}px;">'
        + "".join(x_labels_html) +
        '</div>'
        '</div>'
        + legend_html
    )


def _distribution_view_body(
    *, overall: "Counter", palette: dict[str, str], unit: str, mode: str,
) -> str:
    """Promoted-legend view: donut on the left + horizontal bars per dim
    value on the right (top 12 + 其它 rollup), sorted desc. Donut covers
    the whole pie; bars give a sortable easy-to-read table next to it."""
    items_all = list(overall.most_common())
    if not items_all:
        return f'<div class="muted">{esc(T("no_dim_data"))}</div>'

    grand_total = sum(v for _, v in items_all) or 1
    items_top = overall.most_common(12)
    rest_total = sum(v for _, v in items_all[12:])

    dim_label = {
        "project":  T("dim_project"), "source":   T("dim_source"),
        "activity": T("dim_activity"), "device":   T("dim_device"),
        "device_id":T("dim_device"),
    }.get(mode, mode)

    # ── Donut (conic-gradient): every visible top item gets its own slice,
    # everything beyond top-12 rolls into a single 其它 grey slice. We also
    # encode each segment's [start, end, name, value, color] as JSON so the
    # hover JS can do angle-based lookup (conic-gradient is one DOM node).
    import json as _json
    segs_css: list[str] = []
    segs_data: list[dict] = []
    pos = 0.0
    for name, value in items_top:
        color = palette.get(name, _WEEKLY_OTHER_COLOR)
        pct = value / grand_total * 100
        end = pos + pct
        segs_css.append(f"{color} {pos:.3f}% {end:.3f}%")
        segs_data.append({
            "name": name, "color": color,
            "start": round(pos, 3), "end": round(end, 3),
            "label": _format_value(value, unit),
            "share": round(value / grand_total, 4),
        })
        pos = end
    if rest_total > 0:
        segs_css.append(f"{_WEEKLY_OTHER_COLOR} {pos:.3f}% 100%")
        segs_data.append({
            "name": T("other_bucket"), "color": _WEEKLY_OTHER_COLOR,
            "start": round(pos, 3), "end": 100.0,
            "label": _format_value(rest_total, unit),
            "share": round(rest_total / grand_total, 4),
        })
    segments_attr = esc(_json.dumps(segs_data, ensure_ascii=False))
    donut_html = (
        f'<div class="cc-donut" data-segments="{segments_attr}" '
        f'style="background:conic-gradient({", ".join(segs_css)})">'
        '<div class="cc-donut-hole">'
        f'<div class="cc-donut-total">{esc(_format_value(grand_total, unit))}</div>'
        f'<div class="cc-donut-label">{esc(dim_label)}</div>'
        '</div></div>'
    )

    # ── Bars (right column)
    bar_items = list(items_top)
    if rest_total > 0:
        bar_items.append((T("other_bucket"), rest_total))
    max_val = max(v for _, v in bar_items) or 1
    rows_html = []
    for name, value in bar_items:
        color = palette.get(name, _WEEKLY_OTHER_COLOR)
        share_pct = value / grand_total * 100
        bar_pct = value / max_val * 100
        rows_html.append(
            '<div style="display:grid; grid-template-columns:14px minmax(120px,1.2fr) minmax(140px,2.5fr) 60px 40px; '
            'align-items:center; gap:10px; padding:4px 0;">'
            f'<span style="width:11px; height:11px; border-radius:3px; background:{color}; display:inline-block;"></span>'
            f'<span class="cc-bar-name" title="{esc(name)}" style="font-weight:600; color:#3b352e; font-size:12.5px;">{esc(name)}</span>'
            f'<span style="height:9px; background:#ece3d2; border-radius:999px; overflow:hidden;">'
            f'<span style="display:block; height:100%; width:{bar_pct:.2f}%; background:{color}; border-radius:999px;"></span>'
            f'</span>'
            f'<span style="text-align:right; font-size:12.5px; font-weight:700; font-variant-numeric:tabular-nums;">{esc(_format_value(value, unit))}</span>'
            f'<span style="text-align:right; font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums;">{share_pct:.0f}%</span>'
            '</div>'
        )

    return (
        # 2-col layout: donut on left, bars on right. The cc-* classes pull
        # in styling from the daily composition card so the donut size /
        # hole / label match.
        '<div style="display:grid; grid-template-columns:auto 1fr; gap:24px; '
        'align-items:center; padding:8px 8px 4px;">'
        f'<div class="cc-donut-wrap" style="flex:none;">{donut_html}</div>'
        '<div>'
        f'<div class="muted small" style="margin-bottom:4px;">'
        f'{esc(T("donut_sorted_by", dim=dim_label, n=len(bar_items)))}'
        '</div>'
        + "".join(rows_html) +
        '</div>'
        '</div>'
    )


def _hour_heatmap_card(
    events: list[dict], days: list[str], boundary_hour: int,
    *, stack_by: str, palette: dict[str, str], top_names: list[str],
) -> str:
    """24×7 heatmap, cells colored by the dominant `stack_by` value in that
    (day × hour) bucket and opacity scaled to event count.

    Each cell carries data-bins (JSON {dim_value: count}) so the shared
    swim/heat filter pills can recolor it client-side without a reload:
    filter=all → dominant value's color, alpha = total/max_total;
    filter=X    → X's color, alpha = X_count/max_X_count."""
    import json as _json
    from datetime import datetime, timedelta, date as _date
    from collections import defaultdict
    if not events:
        return ""

    # bins[(day, hour)] = {dim_value: count}
    bins: dict[tuple[str, int], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    days_set = set(days)
    for ev in events:
        s = ev.get("start") or ""
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            continue
        shifted = (dt - timedelta(hours=boundary_hour)).date().isoformat()
        if shifted not in days_set:
            continue
        v = _stack_value_of(ev, stack_by)
        bins[(shifted, dt.hour)][v] += 1

    if not bins:
        return ""

    # max_total across all cells (used for filter=all alpha scaling on the
    # server-side default render; JS recomputes per filter on user input).
    cell_totals = {k: sum(b.values()) for k, b in bins.items()}
    max_total = max(cell_totals.values()) or 1

    def hex_to_rgba(hexcol: str, alpha: float) -> str:
        h = hexcol.lstrip("#")
        if len(h) == 3:
            h = "".join(c + c for c in h)
        try:
            r = int(h[0:2], 16); g = int(h[2:4], 16); b = int(h[4:6], 16)
        except ValueError:
            r, g, b = 47, 111, 237  # fallback blue
        return f"rgba({r},{g},{b},{alpha:.2f})"

    cells_html = []
    # Header row
    header = ['<div></div>']  # corner spacer
    for d in days:
        wd = _short_weekday(d)  # "周一" / "Mon" / etc.
        header.append(
            f'<div style="text-align:center; font-size:11px; color:var(--muted);">'
            f'{wd}<br><span style="font-size:10px; color:#bbb;">{esc(d[5:])}</span></div>'
        )
    cells_html.append("".join(header))

    # Hour rows ordered by shifted-day axis (04, 05, …, 23, 00, 01, 02, 03)
    hour_order = [(boundary_hour + i) % 24 for i in range(24)]
    for h in hour_order:
        row = [
            f'<div style="font-size:10px; color:var(--muted); text-align:right; '
            f'padding-right:4px; font-variant-numeric:tabular-nums;">{h:02d}</div>'
        ]
        for d in days:
            cell_bins = dict(bins.get((d, h), {}))
            total = sum(cell_bins.values())
            # Each cell is a flex row of horizontal segments — one per dim
            # value present, width-weighted by its count. Same alpha (=
            # total/max_total) on every segment in the cell so the whole
            # cell still reads "denser = more events", but colors stay
            # separated rather than blended.
            if total > 0:
                alpha = 0.15 + 0.85 * (total / max_total)
                sorted_bins = sorted(cell_bins.items(), key=lambda kv: (-kv[1], kv[0]))
                segments = []
                for name, c in sorted_bins:
                    color_hex = palette.get(name, _WEEKLY_OTHER_COLOR)
                    segments.append(
                        f'<span class="hm-seg" data-value="{esc(name)}" '
                        f'style="background:{hex_to_rgba(color_hex, alpha)}; '
                        f'flex:{c}; height:100%;"></span>'
                    )
                seg_html = "".join(segments)
                label_html = (
                    f'<span class="hm-cell-label" style="position:absolute; '
                    f'inset:0; display:flex; align-items:center; justify-content:center; '
                    f'font-size:10px; font-weight:700; '
                    f'color:{"white" if alpha > 0.55 else "var(--ink)"}; '
                    f'text-shadow:0 1px 2px rgba(0,0,0,0.28); pointer-events:none;">'
                    f'{total}</span>'
                )
            else:
                seg_html = ""
                label_html = ""
            bins_attr = esc(_json.dumps(cell_bins, ensure_ascii=False))
            row.append(
                f'<div class="hm-cell" data-bins="{bins_attr}" '
                f'data-total="{total}" data-day="{esc(d)}" data-hour="{h:02d}" '
                f'title="{d} {h:02d}:00 · {total} events" '
                f'style="position:relative; height:18px; border-radius:3px; '
                f'overflow:hidden; display:flex; align-items:stretch;">'
                f'{seg_html}{label_html}</div>'
            )
        cells_html.append("".join(row))

    grid = (
        '<div class="hm-grid" style="display:grid; grid-template-columns:30px repeat(7, 1fr); gap:2px;">'
        + "".join(cells_html) +
        '</div>'
    )

    grand_total = sum(cell_totals.values())
    busiest_hour = max(hour_order, key=lambda h: sum(cell_totals.get((d, h), 0) for d in days))
    busiest_count = sum(cell_totals.get((d, busiest_hour), 0) for d in days)
    # Palette JSON for JS to color cells by filter
    palette_attr = esc(_json.dumps({k: v for k, v in palette.items()}, ensure_ascii=False))
    return (
        f'<div class="weekly-heat" data-palette="{palette_attr}" '
        f'data-max-total="{max_total}">'
        '<div class="muted small" style="margin-bottom:8px;">'
        f'总 {grand_total} 个事件 · 最忙时段 {busiest_hour:02d}:00（{busiest_count} 个）· '
        '颜色按当前维度上色 · 透明度按条数'
        '</div>'
        + grid +
        '</div>'
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


def _load_week_daily_overviews(con, days: list[str]) -> str:
    """Pull each day's ai_overview from day_channel for the given days and
    format them into a compact per-day block the weekly prompt can use to
    name real Feishu tasks (instead of inventing project names from
    by_project aggregates). Empty string when no AI overviews exist."""
    import json as _json
    lines: list[str] = []
    for d in days:
        row = con.execute(
            "SELECT value_json FROM day_channel WHERE date=? AND channel='ai_overview'",
            (d,),
        ).fetchone()
        if not row or not row[0]:
            continue
        try:
            v = _json.loads(row[0])
        except Exception:
            continue
        # AI overviews are v14 bilingual dicts. We feed only the Chinese
        # side into the weekly prompt context — the prompt itself produces
        # both languages independently. L() handles both v14 dicts and
        # pre-v14 plain strings.
        headline = L(v.get("headline"), lang="zh")
        ov = v.get("overview") or {}
        narrative = L(ov.get("narrative") if isinstance(ov, dict) else None, lang="zh")
        if not narrative:
            narrative = L(v.get("narrative"), lang="zh")
        highlights = v.get("highlights") or []
        block = [f"## {d} — {headline}"]
        if narrative:
            block.append(narrative.strip())
        if highlights:
            block.append("关键任务进展:")
            for h in highlights[:4]:
                block.append(f"  • {L(h, lang='zh')}")
        lines.append("\n".join(block))
    return "\n\n".join(lines)


def _ai_weekly_summary(
    *, week: str, events: list[dict], by_project: list[dict],
    total_minutes: float, active_days: int,
    days: list[str] | None = None, con=None,
    last_week_total: int | None = None,
    last_week_active_minutes: float | None = None,
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
    # Per-day AI overviews — gives the weekly model real Feishu task names
    # and concrete day-of-week landmarks instead of just aggregate stats.
    daily_overviews_text = ""
    if con is not None and days:
        daily_overviews_text = _load_week_daily_overviews(con, days)
    daily_overviews_block = (
        f"【本周每日 AI 速读 — 真任务名 + 当天叙事, 用这个串成周叙事】\n"
        f"{daily_overviews_text}\n\n"
        if daily_overviews_text else ""
    )
    # Light week-over-week numeric reference for work_pattern
    wow_lines: list[str] = []
    if last_week_total is not None:
        wow_lines.append(f"上周事件数 {last_week_total} → 本周 {len(events)}")
    if last_week_active_minutes is not None:
        wow_lines.append(f"上周活跃 {last_week_active_minutes/60:.1f}h → 本周 {total_minutes/60:.1f}h")
    wow_block = ("【上周对比】\n" + "\n".join(wow_lines) + "\n\n") if wow_lines else ""

    user = (
        f"【本周】{week} · {len(events)} events · {active_days}/7 天活跃 · "
        f"{total_minutes/60:.1f}h\n\n"
        f"{wow_block}"
        f"【项目分布 (top 8, 仅供参考)】\n{top_projects}\n\n"
        f"{daily_overviews_block}"
        "请输出严格 JSON, 双语 schema (每个文本字段是 {\"zh\":..., \"en\":...}):\n"
        "{\n"
        '  "headline":   {"zh": "≤30 字, 一句话抓住本周主线",\n'
        '                 "en": "≤40 chars, 1-sentence weekly headline"},\n'
        '  "overview": {\n'
        '    "narrative": {\n'
        '      "zh": "**3-5 句, 150-250 字** 的**主题式**总结(不是日记式流水账)。识别本周的 2-4 条主线 (任务/方向), 每条说清楚: 推进到什么程度、卡点在哪、有什么阶段性产出。",\n'
        '      "en": "**3-5 sentences, 220-360 chars**, theme-based (NOT a chronological recap). Identify 2-4 main threads of the week (tasks/directions); for each, say how far it moved, where it stalled, what shipped."\n'
        '    }\n'
        '  },\n'
        '  "trend": {\n'
        '    "direction": "rising | steady | dropping | new | paused | blocked",\n'
        '    "comparison": {"zh": "1 句 (≤60 字) 工作重心/节奏 vs 上周怎么变",\n'
        '                   "en": "1 sentence (≤90 chars) on how focus/pace shifted vs last week"}\n'
        '  },\n'
        '  "highlights":   [{"zh": "**3-5 条** 本周真正推进的飞书任务+动作 (用任务全名), 每条 ≤50 字",\n'
        '                    "en": "3-5 task advances this week (full Feishu titles + concrete action), ≤80 chars each"}],\n'
        '  "work_pattern": [{"zh": "**2-4 条** 本周节奏观察 (活跃时长 vs 上周、活跃天数、有没有大块专注/碎片化、收工节奏), 每条带数字",\n'
        '                    "en": "2-4 pattern observations comparing this week to last (active hours, day coverage, deep blocks vs fragmentation, end-of-day timing), grounded in numbers"}],\n'
        '  "suggestions":  [{"zh": "**2-4 条** 下周该盯的任务 (deadline 临近 / 已停 N 天 / 未提交), 用任务全名 + 具体行动",\n'
        '                    "en": "2-4 tasks to watch next week (closing deadlines / N days idle / uncommitted), full task names + concrete next-step"}]\n'
        "}"
    )
    system = (
        "你是一位软件工程师的私人**周复盘助手** / weekly recap assistant. "
        "读者是这位工程师本人 / the reader is the engineer themself.\n\n"
        "**双语输出**: 每个文本字段都是 {\"zh\":..., \"en\":...},两种语言独立生成同一份内容(各自符合语言习惯)。\n\n"
        "**任务视角硬规则**: narrative / highlights / suggestions 必须用"
        "**完整任务标题** (例: ‘DayTrace 应用开发’, 不是 ‘daytrace’)。"
        "上下文里的【每日 AI 速读】是真任务名的来源, **不要**从【项目分布】"
        "里拿 daily-manager / misc / daytrace 这种 project_guess 当主体。\n\n"
        "**narrative 是『主题式总结』, 不是『日记』**:\n"
        "  • 把本周内容**按主线归类** (2-4 条), 然后讲每条主线的**推进程度**, "
        "而不是按日期顺序背流水账。\n"
        "  • 用户看 narrative 想知道 ‘这周 4 条主线各推到哪了, 哪些收尾了, "
        "哪些还在挣扎, 哪些刚起头’ — 用对比和节奏感, 不要 ‘周一... 周二... 周三...’。\n"
        "  • 注意: 逐日叙事会在专门的『每日时间轴』里展示, 不要在 narrative 里"
        "重复, 你这里只做**主题/层次/对比**的总览。\n\n"
        "**写作风格**:\n"
        "  • bullet 写法多样化, 不要每条都是 ‘任务名: 动作’ 死板模板。\n\n"
        "**禁止**:\n"
        "  ❌ 用项目名代替任务名\n"
        "  ❌ 编造任务名 (只能用每日速读里出现过的真任务)\n"
        "  ❌ narrative 写成 ‘周一... 周二... 周三...’ 的日记体\n"
        "  ❌ narrative 写成 ‘本周完成了 X, Y, Z’ 的通报体\n"
        "  ❌ 对数据/系统/工具提建议\n"
        "  ❌ 泛化效率说教、数字复述\n\n"
        "严格只输出 JSON, 不要 Markdown。"
    )

    def _validator(payload):
        """Weekly validator now mirrors the daily v14 bilingual normalizer.
        Accepts either plain-string fields (legacy v13 cache) or bilingual
        {"zh": ..., "en": ...} dicts (v14); shape comes out canonicalized
        as bilingual dicts so the renderers can L() everything."""
        from daytrace.ai_client import ShapeError
        # Reuse daytrace's daily validator — its _bilingual_str / _bilingual_list
        # already handle both shapes.
        from daytrace.ai_report import validate_overview as _validate
        return _validate(payload)

    try:
        resp = ai_client.call_json_validated(
            system=system, user=user, validator=_validator, max_tokens=4500,
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
    _lw = T("nav_weekly")  # "Weekly" / "周报"
    _prev_label = "Prev " if _CURRENT_LANG.get() == "en" else "上周 "
    _blank_label = T("stat_blank_days")
    return (
        '<div class="dr-stats-compact">'
        f'<div class="dr-stat">'
        f'<span class="dr-stat-num">{total_events}</span>'
        f'<span class="dr-stat-lbl">{esc(T("stat_events"))}</span>'
        f'<span class="muted" style="font-size:10.5px; margin-top:2px;">{esc(_prev_label)}{last_total} '
        f'<span style="color:{delta_color};">({delta:+d}, {delta_pct:+.0f}%)</span></span></div>'
        f'<div class="dr-stat">'
        f'<span class="dr-stat-num">{total_minutes/60:.1f}h</span>'
        f'<span class="dr-stat-lbl">{esc(T("stat_active"))}</span>'
        f'<span class="muted" style="font-size:10.5px; margin-top:2px;">{esc(_prev_label)}{last_active_minutes/60:.1f}h '
        f'<span style="color:{hours_color};">({hours_delta:+.1f}h)</span></span></div>'
        f'<div class="dr-stat">'
        f'<span class="dr-stat-num">{active_days}/7</span>'
        f'<span class="dr-stat-lbl">{esc(T("stat_active_days"))}</span>'
        f'<span class="muted" style="font-size:10.5px; margin-top:2px;">{esc(_blank_label)} {7 - active_days}</span></div>'
        f'<div class="dr-stat">'
        f'<span class="dr-stat-num">${ai_cost:.3f}</span>'
        f'<span class="dr-stat-lbl">{esc(T("stat_ai_cost"))}</span>'
        f'<span class="muted" style="font-size:10.5px; margin-top:2px;">{esc(T("stat_week_total"))}</span></div>'
        '</div>'
    )


def _ai_summary_body(summary: dict | None) -> str:
    """Weekly Report-card body: Overview narrative + trend closer, mirroring
    daily. The 3-column Insights row sits separately below."""
    if summary is None:
        return '<div class="dr-narrative muted">(本周 AI 速读还没生成)</div>'
    if summary.get("_unavailable"):
        return '<div class="dr-narrative muted">(DEEPSEEK_API_KEY 未设置, 跳过 AI 速读)</div>'
    if summary.get("_error"):
        return f'<div class="dr-narrative muted">AI 调用失败: {esc(summary["_error"])}</div>'
    return (
        _render_overview_section(summary)
        + _render_trend_closer(summary, None)
    )


def _ai_insights_card_weekly(summary: dict | None) -> str:
    """Full-width Insights card for the weekly page. Hidden on error states."""
    if summary is None or summary.get("_unavailable") or summary.get("_error"):
        return ""
    return _render_insights_card(summary)


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


def _daily_per_hour_stack(
    events: list[dict], boundary_hour: int,
    *, mode: str, unit: str,
) -> tuple[dict[int, dict[str, float]], dict[int, float]]:
    """For each shifted-hour 0..23 (0 = boundary_hour, 23 = boundary_hour+23
    mod 24), return {dim_value: weight} + per-hour totals.

    unit='hours': per-5min-slot proportional split (same algorithm as the
    weekly main chart, just bucketed into hour bins instead of day bins).
    Per-hour total = active_minutes in that hour, in hours.

    unit='count' / 'chars': simple per-event weight summed per (hour, dim).
    """
    from collections import defaultdict
    from daytrace.stats import _safe_minute

    per_hour: dict[int, dict[str, float]] = {h: defaultdict(float) for h in range(24)}

    if unit == "hours":
        # Aggregate per 5-min slot, then assign to hour bin.
        per_slot: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for ev in events:
            m = _safe_minute(ev.get("start"))
            if m is None:
                continue
            slot = m // 5
            v = _stack_value_of(ev, mode)
            per_slot[slot][v] += 1
        for slot, counts in per_slot.items():
            total = sum(counts.values())
            if total <= 0:
                continue
            clock_hour = (slot * 5) // 60
            shifted_h = (clock_hour - boundary_hour) % 24
            for v, c in counts.items():
                # 5 min * proportion / 60 = hours
                per_hour[shifted_h][v] += (5 * (c / total)) / 60.0
    else:
        for ev in events:
            m = _safe_minute(ev.get("start"))
            if m is None:
                continue
            clock_hour = m // 60
            shifted_h = (clock_hour - boundary_hour) % 24
            v = _stack_value_of(ev, mode)
            w = int(ev.get("char_count") or 0) if unit == "chars" else 1
            per_hour[shifted_h][v] += w

    per_hour_out = {h: dict(b) for h, b in per_hour.items()}
    totals = {h: sum(b.values()) for h, b in per_hour_out.items()}
    return per_hour_out, totals


def _daily_histogram_body(
    *, per_hour: dict[int, dict[str, float]],
    per_hour_totals: dict[int, float], unit: str, mode: str,
    top_names: list[str], palette: dict[str, str],
    boundary_hour: int, chart_height_px: int = 220,
) -> str:
    """24-bar stacked histogram mirroring _main_chart_card's look but for
    hours within one day. X-axis labels = shifted clock hours
    (boundary_hour, +1, …, +23). Same Y-axis tick + grid + bar styling."""
    from collections import Counter

    overall: Counter = Counter()
    for bag in per_hour.values():
        for k, v in bag.items():
            overall[k] += v

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
            kept.append((T("other_bucket"), other))
        return kept

    raw_max = max(per_hour_totals.values()) if per_hour_totals else 0
    if raw_max <= 0:
        return '<div class="muted">该维度今日无可用数据</div>'

    axis_max, axis_ticks = _nice_axis_max(raw_max, unit)
    Y_AXIS_W = 40

    y_ticks_html = []
    grid_lines_html = []
    for t in axis_ticks:
        pct = (t / axis_max) * 100
        y_ticks_html.append(
            f'<div style="position:absolute; right:6px; bottom:calc({pct:.2f}% - 7px); '
            f'font-size:10px; color:var(--muted); font-variant-numeric:tabular-nums; line-height:1;">'
            f'{_format_value(t, unit)}</div>'
        )
        grid_lines_html.append(
            f'<div style="position:absolute; left:0; right:0; bottom:{pct:.2f}%; '
            f'height:0; border-top:1px dashed #d9ccaf; pointer-events:none;"></div>'
        )

    # 24 bars from shifted_h=0 to 23. Show every-other hour label so the
    # X axis doesn't crowd.
    bars_html = []
    x_labels_html = []
    for shifted_h in range(24):
        clock_hour = (boundary_hour + shifted_h) % 24
        bag = fold(per_hour.get(shifted_h, {}))
        total = per_hour_totals.get(shifted_h, 0.0)
        bar_pct = (total / axis_max) * 100 if axis_max else 0
        tooltip_lines = [f"{clock_hour:02d}:00 · {_format_value(total, unit)}"]
        tooltip_lines.extend(f"  {k}: {_format_value(v, unit)}" for k, v in bag if v > 0)
        tooltip = "\n".join(tooltip_lines)
        segs = []
        for k, v in bag:
            if v <= 0:
                continue
            seg_pct = (v / total) * 100 if total > 0 else 0
            color = palette.get(k, _WEEKLY_OTHER_COLOR)
            segs.append(
                f'<div title="{esc(k)}: {_format_value(v, unit)}" '
                f'style="height:{seg_pct:.2f}%; background:{color}; '
                f'border-bottom:1px solid rgba(255,255,255,0.55);"></div>'
            )
        bars_html.append(
            f'<div title="{esc(tooltip)}" '
            f'style="flex:1; min-width:0; display:flex; justify-content:center; align-items:flex-end; '
            f'height:100%; position:relative; z-index:1;">'
            f'<div style="width:74%; height:{bar_pct:.2f}%; display:flex; flex-direction:column-reverse; '
            f'border-radius:3px 3px 0 0; overflow:hidden; background:#f1ece2; min-height:1px;">'
            + "".join(segs) +
            '</div>'
            '</div>'
        )
        # Show label every 2 hours to avoid crowding
        if shifted_h % 2 == 0:
            x_labels_html.append(
                f'<div style="flex:1; min-width:0; text-align:center; padding-top:5px;">'
                f'<div style="font-size:10.5px; color:var(--muted); font-variant-numeric:tabular-nums;">{clock_hour:02d}</div>'
                '</div>'
            )
        else:
            x_labels_html.append('<div style="flex:1; min-width:0;"></div>')

    # Legend strip — top-N value swatches + totals (mirrors weekly histogram)
    legend_items = "".join(
        f'<span class="tl-legend-item">'
        f'<span class="tl-swatch" style="background:{palette.get(k, _WEEKLY_OTHER_COLOR)};"></span>'
        f'{esc(k)} <span class="muted">{esc(_format_value(overall[k], unit))}</span>'
        '</span>'
        for k in top_names if overall.get(k, 0) > 0
    )
    legend_html = (
        '<div style="display:flex; flex-wrap:wrap; gap:6px 14px; padding:8px 8px 0;'
        f' margin-left:{Y_AXIS_W}px; border-top:1px dashed #eadfcd; font-size:12px;">'
        + legend_items +
        '</div>'
    ) if legend_items else ""

    return (
        '<div style="padding:8px 4px 4px;">'
        f'<div style="display:flex; align-items:stretch; height:{chart_height_px}px;">'
        f'<div style="position:relative; width:{Y_AXIS_W}px; flex:none;">'
        + "".join(y_ticks_html) +
        '</div>'
        '<div style="position:relative; flex:1;">'
        + "".join(grid_lines_html) +
        '<div style="position:absolute; inset:0; display:flex; gap:2px; align-items:flex-end;">'
        + "".join(bars_html) +
        '</div></div>'
        '</div>'
        f'<div style="display:flex; gap:2px; padding-left:{Y_AXIS_W}px;">'
        + "".join(x_labels_html) +
        '</div>'
        '</div>'
        + legend_html
    )


def _compute_task_stats(
    con, days: list[str], boundary_hour: int,
) -> dict[str, dict]:
    """Per-task stats over the given shifted-day range.
    Returns {record_id: {event_count, minutes, last_activity_iso}}.

    - event_count + minutes are scoped to `days` (passing days=[date] gives
      a single-day view; passing the whole week gives weekly totals).
    - last_activity_iso is all-time (so "未触碰" rows still tell you when
      you last did anything on this task, even if it's outside the window).
    """
    if not days:
        return {}
    from collections import defaultdict
    from daytrace.stats import _safe_minute

    # Ranged: count + slot-union per record over the days
    rows = con.execute(
        """
        SELECT e.id, e.date, e.start, l.record_id
          FROM events e
          JOIN event_work_item_links l ON l.event_id = e.id
         WHERE e.date BETWEEN ? AND ?
        """,
        (min(days), max(days)),
    ).fetchall()
    event_count: dict[str, int] = defaultdict(int)
    slots: dict[str, set] = defaultdict(set)
    for r in rows:
        rid = r["record_id"]
        event_count[rid] += 1
        m = _safe_minute(r["start"])
        if m is not None:
            slots[rid].add((r["date"], m // 5))

    # All-time last activity per task
    last_rows = con.execute(
        """
        SELECT l.record_id, MAX(e.start) AS last_start
          FROM events e
          JOIN event_work_item_links l ON l.event_id = e.id
         GROUP BY l.record_id
        """
    ).fetchall()
    last_map = {r["record_id"]: r["last_start"] for r in last_rows}

    out: dict[str, dict] = {}
    all_rids = set(event_count) | set(last_map)
    for rid in all_rids:
        out[rid] = {
            "event_count": event_count.get(rid, 0),
            "minutes": len(slots.get(rid, set())) * 5,
            "last_activity": last_map.get(rid),
        }
    return out


def _format_time_ago(iso: str | None) -> str:
    if not iso:
        return T("untouched")
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return iso[:10]
    delta = datetime.now() - dt
    s = int(delta.total_seconds())
    if s < 90:
        return T("time_ago_now")
    if s < 3600:
        return T("time_ago_min", n=s // 60)
    if s < 86400:
        return T("time_ago_hr",  n=s // 3600)
    if s < 86400 * 7:
        return T("time_ago_day", n=s // 86400)
    return iso[:10]


def _due_chip_html(due_date: str | None) -> str:
    if not due_date:
        return '<span class="muted">—</span>'
    from datetime import date as _date
    try:
        d = _date.fromisoformat(due_date)
    except ValueError:
        return f'<span class="muted">{esc(due_date)}</span>'
    delta = (d - _date.today()).days
    if delta < 0:
        bg, color, label = "#fce8e8", "#b32a2a", T("due_overdue", n=-delta)
    elif delta <= 3:
        bg, color, label = "#fce8e8", "#b32a2a", T("due_urgent",  n=delta)
    elif delta <= 7:
        bg, color, label = "#fff3cd", "#7a5a00", T("due_tight",   n=delta)
    else:
        bg, color, label = "#eef5ff", "#2f6fed", f"{delta}d"
    return (
        f'<span style="padding:2px 8px; border-radius:999px; '
        f'background:{bg}; color:{color}; font-size:11px; font-weight:700; '
        f'font-variant-numeric:tabular-nums;">{esc(label)} · {esc(due_date[5:])}</span>'
    )


_STATUS_COLOR = {
    "进行中": ("#dcf3e3", "#1f7a3e"),
    "待办":   ("#fff3cd", "#7a5a00"),
    "完成":   ("#eee", "#888"),
}


def _localized_task_title(wi: dict | None) -> str:
    """Pick title_en when current lang is en AND the row has it; otherwise
    fall back to the original Chinese title. Run scripts/translate_work_items.py
    to bulk-populate title_en via DeepSeek."""
    if not wi:
        return ""
    if _CURRENT_LANG.get() == "en":
        en = (wi.get("title_en") or "").strip()
        if en:
            return en
    return wi.get("title") or ""


def _localized_status(status: str | None) -> str:
    """Translate Feishu task status word for display.
    The raw values stay in zh in the SQLite mirror; only display text flips."""
    if not status:
        return ""
    if status == "进行中": return T("status_doing")
    if status == "待办":   return T("status_todo")
    if status == "完成":   return T("status_done")
    return status
_PRIORITY_COLOR = {
    "P0": ("#fce8e8", "#b32a2a"),
    "P1": ("#fde9d3", "#a05300"),
    "P2": ("#eef5ff", "#2f6fed"),
    "P3": ("#eee",    "#666"),
}


def _chip(text: str, palette: tuple[str, str] | None) -> str:
    if not text:
        return ""
    bg, color = palette or ("#eee", "#444")
    return (
        f'<span style="padding:2px 8px; border-radius:999px; '
        f'background:{bg}; color:{color}; font-size:11px; font-weight:700;">'
        f'{esc(text)}</span>'
    )


_TABLE_KEY_COLOR = {
    "tasks":   ("#dcf3e3", "#1f7a3e"),
    "reviews": ("#e9e4ff", "#5a3fb8"),
}


def _tasks_panel_one(
    con, days: list[str], boundary_hour: int, *, table_key: str, label: str, stats: dict,
) -> str:
    """Render ONE table's Tasks card (one entry per work_item row).
    Returns "" if no rows for this table."""
    from daytrace.work_items import list_work_items
    items = list_work_items(con, table_key=table_key)
    if not items:
        return ""

    table_labels = {"tasks": T("tasks_table_t"), "reviews": T("tasks_table_r")}

    rows_html = []
    for wi in items:
        rid = wi["record_id"]
        st = stats.get(rid, {})
        minutes = st.get("minutes", 0)
        ev_count = st.get("event_count", 0)
        last_iso = st.get("last_activity")
        status = wi.get("status") or ""
        priority = wi.get("priority") or ""
        table_key = wi.get("table_key") or "tasks"
        # External link button
        ext = wi.get("external_links") or []
        link_html = ""
        if isinstance(ext, list) and ext:
            link_html = (
                f'<a href="{esc(ext[0])}" target="_blank" rel="noopener" '
                f'title="{esc(ext[0])}" style="color:#2f6fed; font-size:10px; '
                f'margin-left:6px;">↗</a>'
            )
        # ⚠ when high-priority + stale in window
        is_stale = (
            status in ("进行中", "待办")
            and priority in ("P0", "P1")
            and ev_count == 0
        )
        subtitle = wi.get("subtitle") or wi.get("project_source") or ""
        subtitle_html = (
            f'<div style="font-size:10.5px; color:var(--muted); margin-top:2px;">{esc(subtitle)}</div>'
            if subtitle else ""
        )
        title_html = (
            '<div style="line-height:1.25;">'
            f'<span style="font-weight:600; color:#3b352e;">{esc(_localized_task_title(dict(wi)))}</span>'
            f'{link_html}'
        )
        if is_stale:
            title_html += (
                '<span style="margin-left:8px; color:#b32a2a; font-size:11px;" '
                f'title="{esc(T("tasks_p_stale_tip"))}">⚠</span>'
            )
        title_html += f"{subtitle_html}</div>"

        time_html = (
            f'<span style="font-variant-numeric:tabular-nums; font-weight:700;">'
            f'{_format_value(minutes / 60.0, "hours")}</span>'
            if minutes > 0 else
            '<span class="muted" style="font-variant-numeric:tabular-nums;">0</span>'
        )
        ev_html = (
            f'<span style="font-variant-numeric:tabular-nums; color:var(--muted);">'
            f'{ev_count}</span>'
            if ev_count > 0 else
            '<span class="muted">·</span>'
        )

        # Sortable data-* attrs: numeric where applicable, "" → fallback to bottom
        due_sort = wi.get("due_date") or "9999-12-31"
        priority_sort = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(priority, 9)
        status_sort = {"进行中": 0, "待办": 1, "完成": 2}.get(status, 9)
        last_sort = last_iso or "0000"
        rows_html.append(
            f'<tr class="task-row" data-status="{esc(status)}" '
            f'data-status-sort="{status_sort}" '
            f'data-priority-sort="{priority_sort}" '
            f'data-table="{esc(table_key)}" '
            f'data-hours="{minutes / 60.0:.4f}" '
            f'data-events="{ev_count}" '
            f'data-due-sort="{esc(due_sort)}" '
            f'data-last-sort="{esc(last_sort)}" '
            f'data-title="{esc(wi.get("title") or "")}">'
            f'<td>{_chip(table_labels.get(table_key, table_key), _TABLE_KEY_COLOR.get(table_key))}</td>'
            f'<td>{_chip(priority, _PRIORITY_COLOR.get(priority)) or chr(0x2014)}</td>'
            f'<td>{_chip(_localized_status(status), _STATUS_COLOR.get(status))}</td>'
            f'<td class="tasks-title-cell">{title_html}</td>'
            f'<td style="text-align:right;">{time_html}</td>'
            f'<td class="col-events" style="text-align:right;">{ev_html}</td>'
            f'<td class="col-last" style="font-size:11px; color:var(--muted);">{esc(_format_time_ago(last_iso))}</td>'
            f'<td>{_due_chip_html(wi.get("due_date"))}</td>'
            '</tr>'
        )

    active_p01_stale = sum(
        1 for wi in items
        if wi.get("status") in ("进行中", "待办")
        and wi.get("priority") in ("P0", "P1")
        and stats.get(wi["record_id"], {}).get("event_count", 0) == 0
    )
    completed_count = sum(1 for wi in items if wi.get("status") == "完成")
    summary_bits = [T("tasks_n_rows", n=len(items))]
    if active_p01_stale:
        summary_bits.append(f'<span style="color:#b32a2a;">{esc(T("tasks_p_zero", n=active_p01_stale))}</span>')
    summary_line = " · ".join(summary_bits)

    toggle_html = (
        '<label style="display:inline-flex; align-items:center; gap:6px; font-size:12px; color:var(--muted); cursor:pointer; user-select:none;">'
        '<input type="checkbox" data-role="tasks-show-completed" style="cursor:pointer;">'
        f'{esc(T("tasks_show_done"))} ({completed_count})'
        '</label>'
    )

    def thead_cell(lab: str, sort_key: str, *, align: str = "left", default_dir: str = "asc", cls: str = "") -> str:
        cls_attr = f' class="{cls}"' if cls else ""
        return (
            f'<th{cls_attr} data-sort="{sort_key}" data-default-dir="{default_dir}" '
            f'style="text-align:{align}; cursor:pointer; user-select:none;">'
            f'{esc(lab)} <span class="sort-arrow" style="color:var(--muted); font-size:10px;">↕</span>'
            '</th>'
        )
    # Columns marked col-events / col-last are hidden in compact (2-col) mode
    # via CSS, then shown when the user picks a single table.
    if table_key == "tasks":
        # data-col tokens drive the full-mode width overrides in CSS.
        colgroup = (
            '<colgroup>'
            '<col data-col="priority" style="width:48px">'                       # P (was 36 — chip "P..." truncated)
            '<col data-col="status"   style="width:64px">'                       # 状态
            '<col data-col="title">'                                              # 任务 (auto in compact, capped in full)
            '<col data-col="hours"    style="width:54px">'                       # 时长
            '<col data-col="events"   class="col-events" style="width:48px">'    # 事件
            '<col data-col="last"     class="col-last"   style="width:92px">'    # 最近活动
            '<col data-col="due"      style="width:104px">'                      # 截止
            '</colgroup>'
        )
        thead_html = (
            '<tr>'
            + thead_cell(T("tasks_col_p"),      "priority")
            + thead_cell(T("tasks_col_status"), "status")
            + thead_cell(T("tasks_col_title"),  "title")
            + thead_cell(T("tasks_col_hours"),  "hours", align="right", default_dir="desc")
            + thead_cell(T("tasks_col_events"), "events", align="right", default_dir="desc", cls="col-events")
            + thead_cell(T("tasks_col_last"),   "last", default_dir="desc", cls="col-last")
            + thead_cell(T("tasks_col_due"),    "due")
            + '</tr>'
        )
    else:
        colgroup = (
            '<colgroup>'
            '<col data-col="status"   style="width:64px">'                       # status
            '<col data-col="title">'                                              # title (auto in compact, capped in full)
            '<col data-col="hours"    style="width:54px">'                       # hours
            '<col data-col="events"   class="col-events" style="width:48px">'    # events
            '<col data-col="last"     class="col-last"   style="width:92px">'    # last activity
            '<col data-col="due"      style="width:104px">'                      # due
            '</colgroup>'
        )
        thead_html = (
            '<tr>'
            + thead_cell(T("tasks_col_status"), "status")
            + thead_cell(T("tasks_col_topic"),  "title")
            + thead_cell(T("tasks_col_hours"),  "hours", align="right", default_dir="desc")
            + thead_cell(T("tasks_col_events"), "events", align="right", default_dir="desc", cls="col-events")
            + thead_cell(T("tasks_col_last"),   "last", default_dir="desc", cls="col-last")
            + thead_cell(T("tasks_col_due"),    "due")
            + '</tr>'
        )

    # Per-card scoped JS (closes over its own panel root)
    panel_id = f"tasks-{table_key}"
    sort_filter_js = (
        '<script>(function(){'
        f'var panel=document.getElementById("{panel_id}");'
        'if(!panel)return;'
        'var tbody=panel.querySelector("tbody");if(!tbody)return;'
        'var cb=panel.querySelector(\'[data-role="tasks-show-completed"]\');'
        'function applyVis(){'
        'var show=cb&&cb.checked;'
        'panel.querySelectorAll(".task-row").forEach(function(r){'
        'r.style.display=(!show&&r.dataset.status==="完成")?"none":"";});'
        '}'
        'if(cb)cb.addEventListener("change",applyVis);'
        'var sortState={key:null,dir:1};'
        'function getKey(row,key){'
        'switch(key){'
        'case"hours":return parseFloat(row.dataset.hours||"0");'
        'case"events":return parseInt(row.dataset.events||"0",10);'
        'case"priority":return parseInt(row.dataset.prioritySort||"9",10);'
        'case"status":return parseInt(row.dataset.statusSort||"9",10);'
        'case"due":return row.dataset.dueSort||"9999";'
        'case"last":return row.dataset.lastSort||"0";'
        'case"title":return (row.dataset.title||"").toLowerCase();'
        '}return"";}'
        'function applySort(key,dir){'
        'var rows=Array.prototype.slice.call(panel.querySelectorAll(".task-row"));'
        'rows.sort(function(a,b){'
        'var va=getKey(a,key),vb=getKey(b,key);'
        'if(va===vb)return 0;'
        'return (va>vb?1:-1)*dir;});'
        'rows.forEach(function(r){tbody.appendChild(r);});'
        '}'
        'panel.querySelectorAll("th[data-sort]").forEach(function(th){'
        'th.addEventListener("click",function(){'
        'var key=th.dataset.sort;'
        'if(sortState.key===key){sortState.dir*=-1;}'
        'else{sortState.key=key;sortState.dir=(th.dataset.defaultDir==="desc")?-1:1;}'
        'panel.querySelectorAll(".sort-arrow").forEach(function(a){a.textContent="↕";});'
        'var arrow=th.querySelector(".sort-arrow");if(arrow)arrow.textContent=sortState.dir>0?"↑":"↓";'
        'applySort(sortState.key,sortState.dir);'
        '});});'
        'applyVis();'
        '})();</script>'
    )

    chip_palette = _TABLE_KEY_COLOR.get(table_key)
    chip_html = _chip(label, chip_palette)
    # Re-render rows but drop the now-redundant "源" cell since the card itself is scoped
    table_label = table_labels.get(table_key, table_key)
    # Strip the leading source-chip cell from each row (each row currently
    # opens with that <td>; remove the first <td>…</td> chunk)
    import re as _re
    if table_key == "tasks":
        rows_html_scoped = [_re.sub(r"^(<tr[^>]*>)<td>[^<]*<span[^>]*>[^<]*</span>[^<]*</td>", r"\1", r, count=1) for r in rows_html]
    else:
        # For reviews: drop source-chip cell AND priority cell (审稿 has no P)
        rows_html_scoped = []
        for r in rows_html:
            r = _re.sub(r"^(<tr[^>]*>)<td>[^<]*<span[^>]*>[^<]*</span>[^<]*</td>", r"\1", r, count=1)
            # Now drop next <td>...</td> (priority)
            r = _re.sub(r"^(<tr[^>]*>)<td>[^<]*(?:<span[^>]*>[^<]*</span>|—)?[^<]*</td>", r"\1", r, count=1)
            rows_html_scoped.append(r)

    return (
        f'<section class="card tasks-card" id="{panel_id}" data-table-key="{esc(table_key)}">'
        '<div style="display:flex; align-items:center; gap:10px; margin-bottom:8px; flex-wrap:wrap;">'
        f'<h3 style="margin:0;">{esc(T("tasks_panel_t") if table_key == "tasks" else T("tasks_panel_r"))}</h3>'
        f'{chip_html}'
        f'<span class="muted small">{summary_line}</span>'
        f'<span style="margin-left:auto;">{toggle_html}</span>'
        '</div>'
        '<table class="mini-table" style="width:100%; table-layout:fixed;">'
        f'{colgroup}'
        f'<thead>{thead_html}</thead>'
        f'<tbody>{"".join(rows_html_scoped)}</tbody>'
        '</table>'
        + sort_filter_js +
        '</section>'
    )


def _alignment_audit_card(con, days: list[str]) -> str:
    """Audit unmatched project_guess values + interactive dropdown to map
    each to an existing work_item. POST → /api/work-items/alias persists to
    config/work_item_aliases.yaml and rebuilds links so future catchups
    follow the user's manual mapping.

    Audit is a *config* surface, not a per-day stat — a single-day window
    rarely has enough unmatched events to make the audit useful. We
    always sweep at least the last 7 calendar days ending at max(days)
    so the daily page and the weekly page both surface meaningful
    alignment work to do."""
    if not days:
        return ""
    from datetime import date as _date_mod, timedelta as _td_mod
    end = max(days)
    try:
        end_dt = _date_mod.fromisoformat(end)
    except ValueError:
        end_dt = _date_mod.today()
    start = (end_dt - _td_mod(days=6)).isoformat()  # 7-day inclusive window
    # If the caller's window is already wider, honor it.
    if min(days) < start:
        start = min(days)
    rows = con.execute(
        """
        SELECT
            e.project_guess AS pg,
            COUNT(*)        AS n,
            MAX(e.start)    AS last_at,
            COUNT(DISTINCT substr(e.start, 1, 10)) AS active_days
          FROM events e
          LEFT JOIN event_work_item_links l ON l.event_id = e.id
         WHERE e.date BETWEEN ? AND ?
           AND l.event_id IS NULL
           AND e.project_guess IS NOT NULL
           AND e.project_guess != ''
         GROUP BY e.project_guess
        HAVING n >= 3
         ORDER BY n DESC
         LIMIT 20
        """, (start, end),
    ).fetchall()
    if not rows:
        return ""

    # Skip completed tasks/reviews whose deadline passed more than 7 days
    # ago — those are historical and shouldn't clutter the audit dropdown.
    # Tasks still 进行中/待办 always stay; recently-completed (≤7d) stay too
    # so you can retro-link an event to something you just finished.
    from datetime import date as _date_mod, timedelta as _td_mod
    _cutoff = (_date_mod.today() - _td_mod(days=7)).isoformat()
    wi_rows_all = con.execute(
        "SELECT record_id, title, title_en, table_key, status, due_date FROM work_items "
        "ORDER BY CASE table_key WHEN 'tasks' THEN 0 ELSE 1 END, title"
    ).fetchall()
    wi_rows = []
    for w in wi_rows_all:
        # 审稿 (reviews) items should already be auto-identifiable from the
        # paper title — they don't belong in the manual audit dropdown.
        if (w["table_key"] or "") == "reviews":
            continue
        if (w["status"] or "") == "完成":
            due = w["due_date"] or ""
            if not due or due < _cutoff:
                continue
        wi_rows.append(w)
    if not wi_rows:
        return ""

    table_labels = {"tasks": T("task_prefix"), "reviews": T("review_prefix")}

    # Existing aliases — pre-select in the dropdown if already mapped.
    from daytrace.work_items import load_aliases
    try:
        existing_aliases = load_aliases()
    except Exception:
        existing_aliases = {}

    def _suggest(pg: str) -> str | None:
        """Auto-suggested record_id (fuzzy title/word match). None if no
        decent match — user will leave dropdown at '-- 跳过 --'."""
        pg_l = pg.lower()
        for w in wi_rows:
            t = (w["title"] or "").lower()
            if not t:
                continue
            if pg_l in t or t in pg_l:
                return w["record_id"]
        import re as _re
        pg_words = {w for w in _re.findall(r"[\w]+", pg_l) if len(w) > 2}
        if not pg_words:
            return None
        best: tuple[float, str] | None = None
        for w in wi_rows:
            t = (w["title"] or "").lower()
            t_words = {x for x in _re.findall(r"[\w]+", t) if len(x) > 2}
            if not t_words:
                continue
            overlap = len(pg_words & t_words) / max(len(pg_words), 1)
            if overlap >= 0.5:
                if best is None or overlap > best[0]:
                    best = (overlap, w["record_id"])
        return best[1] if best else None

    def _options_html(selected_rid: str | None) -> str:
        parts = [f'<option value="">{esc(T("audit_skip"))}</option>']
        for w in wi_rows:
            rid = w["record_id"]
            ttl = _localized_task_title(dict(w))
            tk = w["table_key"] or "tasks"
            sel = ' selected' if rid == selected_rid else ''
            parts.append(
                f'<option value="{esc(rid)}"{sel}>'
                f'[{esc(table_labels.get(tk, tk))}] {esc(ttl[:60])}'
                f'</option>'
            )
        return "".join(parts)

    rows_html: list[str] = []
    total_unmatched = 0
    for r in rows:
        pg = r["pg"]
        n = r["n"]
        total_unmatched += n
        last_at = r["last_at"] or ""
        active_days = r["active_days"] or 0
        last_pretty = _format_time_ago(last_at) if last_at else "—"
        days_label = T("n_days", n=active_days) if active_days else "—"
        # Prefer existing alias > fuzzy suggestion > nothing
        preselect = existing_aliases.get(pg) or _suggest(pg)
        rows_html.append(
            '<tr>'
            f'<td class="audit-pg">{esc(pg)}</td>'
            f'<td class="audit-num">{n}</td>'
            f'<td class="audit-num muted">{esc(days_label)}</td>'
            f'<td class="audit-time muted">{esc(last_pretty)}</td>'
            '<td>'
            f'<input type="hidden" name="project[]" value="{esc(pg)}">'
            f'<select name="record[]" class="audit-select">'
            f'{_options_html(preselect)}'
            '</select>'
            '</td>'
            '</tr>'
        )

    window_label = f"{start} ~ {end}" if start != end else end
    # Success banner — populated client-side from ?audit_applied=A_R_L
    # the POST handler appends after a save. Hidden by default; the inline
    # script reveals + fills it if the query param is present, then strips
    # the param from the URL so refreshing doesn't re-show the toast.
    import json as _json
    banner_template = T("audit_applied", a="__A__", r="__R__", l="__L__")
    audit_banner = (
        '<div id="audit-applied-banner" hidden '
        'style="margin:0 0 10px; padding:8px 12px; border-radius:10px; '
        'background:#e7f4ec; border:1px solid #b8e0c9; color:#1f6b3b; '
        'font-size:13px; font-weight:600;"></div>'
        '<script>(function(){'
        'var p=new URLSearchParams(location.search).get("audit_applied");'
        'if(!p)return;'
        'var m=p.split("_");if(m.length<3)return;'
        'var el=document.getElementById("audit-applied-banner");'
        f'el.textContent={_json.dumps(banner_template, ensure_ascii=False)}'
        '.replace("__A__",m[0]).replace("__R__",m[1]).replace("__L__",m[2]);'
        'el.hidden=false;'
        'var u=new URL(location.href);u.searchParams.delete("audit_applied");'
        'history.replaceState(null,"",u.toString());'
        '})();</script>'
    )
    audit_summary_hint = f'{T("audit_summary", n=len(rows), ev=total_unmatched)} · {T("audit_window")} {window_label}'
    return (
        '<section class="card" id="alignment-audit">'
        + audit_banner
        + _card_head(T("audit_title"), tag=T("audit_tag"), tag_color="config", hint=audit_summary_hint)
        + '<form method="POST" action="/api/work-items/alias" '
        'style="margin:0;">'
        '<table class="mini-table audit-table" style="width:100%; table-layout:fixed;">'
        '<colgroup>'
        '<col style="width:30%">'    # project_guess
        '<col style="width:72px">'   # events
        '<col style="width:88px">'   # active days
        '<col style="width:128px">'  # last activity
        '<col>'                       # match-to-task dropdown (auto)
        '</colgroup>'
        '<thead><tr>'
        f'<th class="audit-pg">{esc(T("audit_pg"))}</th>'
        f'<th class="audit-num">{esc(T("audit_events"))}</th>'
        f'<th class="audit-num">{esc(T("audit_days"))}</th>'
        f'<th class="audit-time">{esc(T("audit_last"))}</th>'
        f'<th>{esc(T("audit_match"))}</th>'
        '</tr></thead>'
        f'<tbody>{"".join(rows_html)}</tbody>'
        '</table>'
        '<div style="display:flex; align-items:center; gap:12px; padding-top:10px; margin-top:8px; border-top:1px dashed var(--line);">'
        f'<span class="muted small">{esc(T("audit_save_hint"))}</span>'
        '<button type="submit" style="margin-left:auto; padding:6px 14px; background:var(--ink); color:white; border:none; border-radius:8px; font-weight:650; cursor:pointer; font-size:13px;">'
        f'{esc(T("audit_save_btn"))}'
        '</button>'
        '</div>'
        '</form>'
        '</section>'
    )


def _latest_ai_summary_per_work_item(
    con, days: list[str],
) -> dict[str, dict]:
    """For each work_item record_id, find its most recent ai_summary
    payload in the given shifted-day window. Joins event_work_item_links
    → events → day_project_channel via (date, project)."""
    if not days:
        return {}
    days_ph = ",".join("?" for _ in days)
    try:
        rows = con.execute(
            f"""
            SELECT
                l.record_id,
                c.date,
                c.project,
                c.value_json
              FROM event_work_item_links l
              JOIN events e ON e.id = l.event_id
              JOIN day_project_channel c
                ON c.date = e.date
               AND c.project = e.project_guess
               AND c.channel = 'ai_summary'
             WHERE e.date IN ({days_ph})
             ORDER BY l.record_id, c.date DESC
            """,
            days,
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        rid = r["record_id"]
        if rid in out:
            continue  # already kept the most recent row (ORDER BY date DESC)
        if r["value_json"]:
            try:
                out[rid] = json.loads(r["value_json"])
            except json.JSONDecodeError:
                continue
    return out


def _ai_list(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for x in items:
        text = _L_ai_project_text(x).strip() if isinstance(x, (dict, str)) else str(x or "").strip()
        if text:
            out.append(text)
    return out


def _L_ai_project_text(value: Any) -> str:
    """Project-summary cells should not leak Chinese fallback on EN pages.

    Legacy cached project summaries were plain zh strings. For the main
    daily/weekly narrative we intentionally fall back across languages, but
    these table cells sit in an otherwise-English table; showing zh here is
    more confusing than showing an empty cell until the v19 bilingual cache
    is regenerated.
    """
    lang = _CURRENT_LANG.get()
    if isinstance(value, dict):
        v = (value.get(lang) or "").strip()
        if v:
            return v
        if lang == "zh":
            return (value.get("en") or "").strip()
        return ""
    if isinstance(value, str):
        return value.strip() if lang == "zh" else ""
    return ""


def _ai_project_summary_view(latest: dict | None) -> dict[str, Any]:
    """Return compact leads plus one row-level expansion body for a
    project-summary payload."""
    if not isinstance(latest, dict):
        return {"progress": "", "next": "", "details": "", "has_details": False}

    summary = _L_ai_project_text(latest.get("summary")).strip()
    status = str(latest.get("status") or "").strip()
    done = _ai_list(latest.get("what_was_done"))
    next_steps = _ai_list(latest.get("next_steps"))

    progress_lead = summary or (done[0] if done else "")
    next_lead = next_steps[0] if next_steps else ""

    progress_sections: list[str] = []
    if summary:
        progress_sections.append(
            f'<div class="ai-section"><span class="ai-label">{esc(T("tasks_ai_summary"))}</span>'
            f'{esc(summary)}</div>'
        )
    if done:
        progress_sections.append(
            f'<div class="ai-section"><span class="ai-label">{esc(T("tasks_ai_done"))}</span>'
            '<ul>' + "".join(f"<li>{esc(x)}</li>" for x in done) + '</ul></div>'
        )
    if status:
        progress_sections.append(
            f'<div class="ai-section"><span class="ai-label">{esc(T("tasks_ai_status"))}</span>'
            f'{esc(status)}</div>'
        )

    next_sections: list[str] = []
    if next_steps:
        next_sections.append(
            f'<div class="ai-section"><span class="ai-label">{esc(T("tasks_ai_next"))}</span>'
            '<ul>' + "".join(f"<li>{esc(x)}</li>" for x in next_steps) + '</ul></div>'
        )
    if status:
        next_sections.append(
            f'<div class="ai-section"><span class="ai-label">{esc(T("tasks_ai_status"))}</span>'
            f'{esc(status)}</div>'
        )

    has_details = bool(progress_sections or next_sections)
    details = ""
    if has_details:
        details = (
            '<div class="task-ai-card">'
            '<div class="ai-cell-expanded">' + "".join(progress_sections) + '</div>'
            '<div class="ai-cell-expanded">' + "".join(next_sections) + '</div>'
            '</div>'
        )
    return {
        "progress": progress_lead,
        "next": next_lead,
        "details": details,
        "has_details": has_details,
    }


def _tasks_panel(con, days: list[str], boundary_hour: int) -> str:
    """┃ Tasks panel ┃ — unified single-table view of all work_items
    (任务 + 审稿 in one rows-list), with a top tab bar to filter by
    source (all/tasks/reviews). Each row now carries the per-(date,
    project) AI summary's latest progress + next-step as two extra
    columns, so per-project AI lives inline with its parent task
    instead of needing a separate Projects card."""
    has_wi = con.execute("SELECT 1 FROM work_items LIMIT 1").fetchone()
    if not has_wi:
        return ""
    from daytrace.work_items import list_work_items
    stats = _compute_task_stats(con, days, boundary_hour)
    ai_summaries = _latest_ai_summary_per_work_item(con, days)
    table_labels = {"tasks": T("tasks_table_t"), "reviews": T("tasks_table_r")}

    # Pull all work_items (任务 + 审稿 等) into a single ordered list.
    all_items = list_work_items(con)
    if not all_items:
        return ""

    # Stable sort: by table_key (tasks first, reviews next, others last),
    # then by event_count desc within the window, then by due_date.
    def _sort_key(wi):
        tk = wi.get("table_key") or "tasks"
        tk_order = {"tasks": 0, "reviews": 1}.get(tk, 2)
        rid = wi.get("record_id")
        ev = stats.get(rid, {}).get("event_count", 0) if rid else 0
        due = wi.get("due_date") or "9999-12-31"
        return (tk_order, -ev, due)
    all_items.sort(key=_sort_key)

    rows_html: list[str] = []
    for idx, wi in enumerate(all_items):
        rid = wi["record_id"]
        st = stats.get(rid, {})
        minutes = st.get("minutes", 0)
        ev_count = st.get("event_count", 0)
        last_iso = st.get("last_activity")
        status = wi.get("status") or ""
        priority = wi.get("priority") or ""
        tk = wi.get("table_key") or "tasks"

        ext = wi.get("external_links") or []
        link_html = ""
        if isinstance(ext, list) and ext:
            link_html = (
                f'<a href="{esc(ext[0])}" target="_blank" rel="noopener" '
                f'title="{esc(ext[0])}" style="color:#2f6fed; font-size:10px; '
                f'margin-left:6px;">↗</a>'
            )
        is_stale = (
            status in ("进行中", "待办")
            and priority in ("P0", "P1")
            and ev_count == 0
        )
        subtitle = wi.get("subtitle") or wi.get("project_source") or ""
        subtitle_html = (
            f'<div style="font-size:10.5px; color:var(--muted); margin-top:2px;">{esc(subtitle)}</div>'
            if subtitle else ""
        )
        title_html = (
            '<div style="line-height:1.25;">'
            f'<span style="font-weight:600; color:#3b352e;">{esc(_localized_task_title(dict(wi)))}</span>'
            f'{link_html}'
        )
        if is_stale:
            title_html += (
                '<span style="margin-left:8px; color:#b32a2a; font-size:11px;" '
                f'title="{esc(T("tasks_p_stale_tip"))}">⚠</span>'
            )
        title_html += f"{subtitle_html}</div>"

        time_html = (
            f'<span style="font-variant-numeric:tabular-nums; font-weight:700;">'
            f'{_format_value(minutes / 60.0, "hours")}</span>'
            if minutes > 0 else
            '<span class="muted" style="font-variant-numeric:tabular-nums;">0</span>'
        )

        # AI summary columns: pull from latest ai_summary for this record's
        # (date, project) over the window. The cell shows a compact lead
        # line and expands to the original generated fields.
        latest = ai_summaries.get(rid) if isinstance(ai_summaries.get(rid), dict) else None
        ai_view = _ai_project_summary_view(latest)
        progress_text = ai_view["progress"]
        next_text = ai_view["next"]
        progress_cell = (
            f'<div class="ai-cell-text" title="{esc(progress_text)}">{esc(progress_text)}</div>'
            if progress_text else '<span class="muted">—</span>'
        )
        next_cell = (
            f'<div class="ai-cell-text" title="{esc(next_text)}">{esc(next_text)}</div>'
            if next_text else '<span class="muted">—</span>'
        )
        ai_row_id = f"task-ai-{idx}"
        expandable_class = " expandable" if ai_view["has_details"] else ""
        ai_row_attr = f' data-ai-row="{ai_row_id}"' if ai_view["has_details"] else ""

        due_sort = wi.get("due_date") or "9999-12-31"
        priority_sort = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(priority, 9)
        status_sort = {"进行中": 0, "待办": 1, "完成": 2}.get(status, 9)
        last_sort = last_iso or "0000"

        rows_html.append(
            f'<tr class="task-row{expandable_class}" data-status="{esc(status)}" '
            f'data-status-sort="{status_sort}" '
            f'data-priority-sort="{priority_sort}" '
            f'data-table="{esc(tk)}" '
            f'data-hours="{minutes / 60.0:.4f}" '
            f'data-events="{ev_count}" '
            f'data-due-sort="{esc(due_sort)}" '
            f'data-last-sort="{esc(last_sort)}" '
            f'data-title="{esc(wi.get("title") or "")}"'
            f'{ai_row_attr}>'
            f'<td>{_chip(table_labels.get(tk, tk), _TABLE_KEY_COLOR.get(tk))}</td>'
            f'<td>{_chip(priority, _PRIORITY_COLOR.get(priority)) or chr(0x2014)}</td>'
            f'<td>{_chip(_localized_status(status), _STATUS_COLOR.get(status))}</td>'
            f'<td class="tasks-title-cell">{title_html}</td>'
            f'<td style="text-align:right;">{time_html}</td>'
            f'<td>{_due_chip_html(wi.get("due_date"))}</td>'
            f'<td class="col-ai-progress">{progress_cell}</td>'
            f'<td class="col-ai-next">{next_cell}</td>'
            '</tr>'
            + (
                f'<tr class="task-ai-row" id="{ai_row_id}" hidden '
                f'data-parent-table="{esc(tk)}" data-parent-status="{esc(status)}">'
                f'<td colspan="8">{ai_view["details"]}</td></tr>'
                if ai_view["has_details"] else ""
            )
        )

    # Tab bar (全部 / 任务 / 审稿) filters rows by data-table attr in JS.
    present_table_keys = list(dict.fromkeys(
        (wi.get("table_key") or "tasks") for wi in all_items
    ))
    pills = [f'<button type="button" class="dim-tab active" data-table-pick="all">{esc(T("tasks_all"))}</button>']
    for tk in present_table_keys:
        pills.append(
            f'<button type="button" class="dim-tab" data-table-pick="{esc(tk)}">'
            f'{esc(table_labels.get(tk, tk))}</button>'
        )
    pill_bar = (
        '<div class="dim-tabs" data-role="tasks-table-pick" '
        'style="margin-bottom:10px; display:inline-flex;">'
        + "".join(pills) +
        '</div>'
    )

    # Completed-tasks toggle
    completed_count = sum(1 for wi in all_items if wi.get("status") == "完成")
    completed_toggle = (
        '<label class="tasks-show-done" style="display:inline-flex; align-items:center; gap:6px; font-size:12px; color:var(--muted); cursor:pointer; user-select:none; margin-left:auto;">'
        '<input type="checkbox" data-role="tasks-show-completed" style="cursor:pointer;">'
        f'{esc(T("tasks_show_done"))} ({completed_count})'
        '</label>'
    )

    # Headers — sortable
    def thead_cell(lab: str, sort_key: str, *, align: str = "left", default_dir: str = "asc") -> str:
        return (
            f'<th data-sort="{sort_key}" data-default-dir="{default_dir}" '
            f'style="text-align:{align}; cursor:pointer; user-select:none;">'
            f'{esc(lab)} <span class="sort-arrow" style="color:var(--muted); font-size:10px;">↕</span>'
            '</th>'
        )

    colgroup = (
        '<colgroup>'
        '<col style="width:64px">'    # source chip
        '<col style="width:48px">'    # P
        '<col style="width:72px">'    # 状态
        '<col>'                       # 任务 (auto)
        '<col style="width:62px">'    # 时长
        '<col style="width:108px">'   # 截止
        '<col style="width:260px">'   # 本期进展
        '<col style="width:220px">'   # 下一步
        '</colgroup>'
    )
    thead = (
        '<tr>'
        + thead_cell(T("tasks_col_source") if "tasks_col_source" in _STRINGS else (T("dim_source") if _CURRENT_LANG.get() != "en" else "Source"), "table_key")
        + thead_cell(T("tasks_col_p"),      "priority")
        + thead_cell(T("tasks_col_status"), "status")
        + thead_cell(T("tasks_col_title"),  "title")
        + thead_cell(T("tasks_col_hours"),  "hours", align="right", default_dir="desc")
        + thead_cell(T("tasks_col_due"),    "due")
        + thead_cell(T("tasks_col_progress") if "tasks_col_progress" in _STRINGS else "本期进展", "progress")
        + thead_cell(T("tasks_col_next") if "tasks_col_next" in _STRINGS else "下一步",          "nextstep")
        + '</tr>'
    )

    sort_filter_js = (
        '<script>(function(){'
        'var panel=document.getElementById("tasks-unified");'
        'if(!panel)return;'
        'var tbody=panel.querySelector("tbody");'
        'var bar=document.querySelector(\'[data-role="tasks-table-pick"]\');'
        'var cb=panel.querySelector(\'[data-role="tasks-show-completed"]\');'
        'var pick="all";'
        'function detailRow(r){return r.dataset.aiRow?document.getElementById(r.dataset.aiRow):null;}'
        'function rowVisible(r){return r.style.display!=="none";}'
        'function applyVis(){'
        'var showDone=cb&&cb.checked;'
        'panel.querySelectorAll(".task-row").forEach(function(r){'
        'var t=r.dataset.table;'
        'var showTable=(pick==="all"||t===pick);'
        'var doneOk=(showDone||r.dataset.status!=="完成");'
        'var visible=showTable&&doneOk;'
        'r.style.display=visible?"":"none";'
        'var d=detailRow(r);if(d){d.hidden=!(visible&&r.classList.contains("row-expanded"));}'
        '});'
        '}'
        'if(bar){bar.querySelectorAll(".dim-tab").forEach(function(btn){'
        'btn.addEventListener("click",function(){'
        'pick=btn.dataset.tablePick;'
        'bar.querySelectorAll(".dim-tab").forEach(function(b){b.classList.toggle("active",b.dataset.tablePick===pick);});'
        'applyVis();});});}'
        'if(cb)cb.addEventListener("change",applyVis);'
        'var sortState={key:null,dir:1};'
        'function getKey(row,key){'
        'switch(key){'
        'case"hours":return parseFloat(row.dataset.hours||"0");'
        'case"events":return parseInt(row.dataset.events||"0",10);'
        'case"priority":return parseInt(row.dataset.prioritySort||"9",10);'
        'case"status":return parseInt(row.dataset.statusSort||"9",10);'
        'case"due":return row.dataset.dueSort||"9999";'
        'case"last":return row.dataset.lastSort||"0";'
        'case"title":return (row.dataset.title||"").toLowerCase();'
        'case"table_key":return row.dataset.table||"";'
        '}return"";}'
        'function applySort(key,dir){'
        'var rows=Array.prototype.slice.call(panel.querySelectorAll(".task-row"));'
        'rows.sort(function(a,b){var va=getKey(a,key),vb=getKey(b,key);if(va===vb)return 0;return (va>vb?1:-1)*dir;});'
        'rows.forEach(function(r){tbody.appendChild(r);var d=detailRow(r);if(d)tbody.appendChild(d);});}'
        'panel.querySelectorAll(".task-row.expandable").forEach(function(r){'
        'r.addEventListener("click",function(e){'
        'if(e.target.closest("a,button,input,select,label"))return;'
        'var d=detailRow(r);if(!d)return;'
        'var open=!r.classList.contains("row-expanded");'
        'r.classList.toggle("row-expanded",open);'
        'd.hidden=!(open&&rowVisible(r));'
        '});});'
        'panel.querySelectorAll("thead th[data-sort]").forEach(function(th){'
        'th.addEventListener("click",function(){'
        'var key=th.dataset.sort;'
        'if(sortState.key===key){sortState.dir*=-1;}'
        'else{sortState.key=key;sortState.dir=(th.dataset.defaultDir==="desc")?-1:1;}'
        'panel.querySelectorAll(".sort-arrow").forEach(function(a){a.textContent="↕";});'
        'var arrow=th.querySelector(".sort-arrow");if(arrow)arrow.textContent=sortState.dir>0?"↑":"↓";'
        'applySort(sortState.key,sortState.dir);});});'
        'applyVis();'
        '})();</script>'
    )

    summary_line = T("tasks_n_rows", n=len(all_items))
    # Place segment-pill bar AND completed-toggle in the right slot so
    # the header reads as one composed control row instead of two.
    right_html = pill_bar + completed_toggle
    return (
        '<section class="card tasks-card" id="tasks-unified">'
        + _card_head(
            T("tasks_panel_t"), tag="Tasks", tag_color="data",
            hint=summary_line, right_html=right_html,
        )
        + '<table class="mini-table" style="width:100%; table-layout:fixed;">'
        + colgroup
        + f'<thead>{thead}</thead>'
        + f'<tbody>{"".join(rows_html)}</tbody>'
        + '</table>'
        + sort_filter_js
        + '</section>'
    )


def weekly_page(
    db_path: Path, week: str | None,
    *, unit: str | None = None, mode: str | None = None,
    view: str | None = None, swim_filter: str | None = None,
    top_view: str | None = None,
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
    # Legacy bookmarks may carry ?mode=device_id — normalize to "device"
    if mode == "device_id":
        mode = "device"
    if mode not in valid_modes:
        mode = "project"  # match daily's default for consistency
    if view not in valid_views:
        view = "swim"
    valid_top_views = {"chart", "dist"}
    if top_view not in valid_top_views:
        top_view = "chart"

    con = connect(db_path); init_db(con)
    if not week:
        from datetime import datetime, timedelta
        now = datetime.now()
        ref = now.date() if now.hour >= _stats.DAY_BOUNDARY_HOUR else (now.date() - timedelta(days=1))
        week = date_to_iso_week((ref - timedelta(days=7)).isoformat())

    try:
        monday, sunday, days = iso_week_to_date_range(week)
    except ValueError as e:
        return layout(f"DayTrace · {T('nav_weekly')}", "format error", "weekly",
                      f'<section class="card"><div class="muted">{esc(str(e))}</div></section>')
    prev_week, next_week = iso_week_neighbors(week)
    bh = _stats.DAY_BOUNDARY_HOUR

    events = events_for_shifted_week(con, week)
    last_events = events_for_shifted_week(con, prev_week)
    # Stamp ev["task"] = linked work_item title (or None) so 任务 dim works.
    _enrich_events_with_tasks(con, events)
    _enrich_events_with_tasks(con, last_events)

    # Activity labels for stack_by=activity (bilingual since v15).
    if events and mode == "activity":
        _cur_lang = _CURRENT_LANG.get()
        labels = load_activity_labels_for_event_ids(con, [e["id"] for e in events], lang=_cur_lang)
        _unclassified = "Unclassified" if _cur_lang == "en" else "未分类"
        for ev in events:
            ev["activity"] = labels.get(ev["id"], _unclassified)

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

    # by_project is still needed to feed the AI summary's top-projects context.
    # We dropped the per-source breakdown table and the vs-last-week diff
    # table — the top-chart "分布" view + the AI's narrative cover those.
    by_project = _weekly_breakdown(events, "project", top=12)

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
    top_names, palette, overall_dim_totals = _compute_palette_for_week(per_day_stack)

    # AI summary — pass days + con + week-over-week refs so the prompt can
    # see per-day ai_overview rows (real Feishu task names) and quote a
    # baseline for work_pattern.
    ai_summary = _ai_weekly_summary(
        week=week, events=events, by_project=by_project,
        total_minutes=total_minutes, active_days=active_days,
        days=days, con=con,
        last_week_total=last_total,
        last_week_active_minutes=float(last_active_minutes),
    )

    # ── Build cards ────────────────────────────────────────────────────────
    header_controls = _weekly_header_controls(
        db_path=db_path, week=week, prev_week=prev_week, next_week=next_week,
        mode=mode, unit=unit, view=view, monday=monday, sunday=sunday,
    )

    stats_strip = _weekly_stats_strip(
        total_events=total_events, last_total=last_total,
        total_minutes=total_minutes, last_active_minutes=last_active_minutes,
        active_days=active_days, ai_cost=ai_cost,
    )

    # ┃ Report panel ┃ — Dashboard (stats) / 总览 / 趋势 / 推荐 sections,
    # parallel to the daily report card.
    weekly_report_card = (
        '<div class="card daily-report">'
        + _card_head(f"{T('nav_weekly')} · {week}", tag=T('report_tag'), tag_color='narrative')
        + stats_strip
        + _ai_summary_body(ai_summary)
        + '</div>'
    )

    # RIGHT card of top row: two views CSS-toggled inside one card.
    #   - 直方图 (default): per-day stacked bars with Y axis + grid
    #   - 分布:  the legend promoted to a real chart — top-N items as
    #            horizontal bars sorted by total; shares the palette with
    #            the histogram so the same name = the same color.
    main_chart_body = _main_chart_card(
        days=days, per_day=per_day_stack, per_day_totals=per_day_totals,
        unit=unit, stack_by=mode, top_names=top_names, palette=palette,
        chart_height_px=260,
    )
    dist_view_body = _distribution_view_body(
        overall=overall_dim_totals, palette=palette, unit=unit, mode=mode,
    )
    top_chart_switcher = (
        '<div class="dim-tabs" data-role="tc-switcher">'
        f'<button type="button" class="dim-tab{" active" if top_view == "chart" else ""}" data-view="chart">{esc(T("chart_view_hist"))}</button>'
        f'<button type="button" class="dim-tab{" active" if top_view == "dist" else ""}" data-view="dist">{esc(T("chart_view_dist"))}</button>'
        '</div>'
    )
    # Unit pills lived in the global dim-bar in v8; moved here because they
    # only affect the histogram/distribution views (swim + heat are
    # inherently per-event count).
    unit_pills = _pill_bar(
        css_class="unit-tab", options=_weekly_unit_opts(), current=unit,
        href_for=lambda v: _weekly_url(week=week, mode=mode, unit=v, view=view),
        param_name="unit",
    )
    unit_label = dict(_weekly_unit_opts()).get(unit, unit)
    dim_label  = dict(_weekly_dim_opts()).get(mode, mode)
    # ┃ Chart panel ┃ — histogram (7 daily bars) + distribution (donut+bars)
    chart_right = (
        f'<span class="muted small" style="font-weight:600;">{esc(T("unit_label"))}</span>'
        f'{unit_pills}'
        f'{top_chart_switcher}'
    )
    top_histogram_card = (
        f'<div class="card top-chart-card" id="top-chart" data-tc-view="{esc(top_view)}">'
        + _card_head(
            f"{T('chart_per_day')} {unit_label}",
            tag=T("chart_panel_tag"), tag_color="data",
            hint=f"· {T('dim_label')}: {dim_label}",
            right_html=chart_right,
        )
        + f'<div class="tc-pane" data-pane="chart">{main_chart_body}</div>'
        + f'<div class="tc-pane" data-pane="dist">{dist_view_body}</div>'
        + '</div>'
    )

    # Insights card (full-width row below the Report+Chart top row)
    insights_card_html = _ai_insights_card_weekly(ai_summary)

    # Bottom view-switcher card — CSS-driven (no reload), mirrors daily timeline-card.
    # All 3 view bodies are rendered to DOM; data-view attribute on the card root
    # decides which is visible via the .wv-* CSS rules.
    # Validate swim_filter — accept "all" or any top_name. Junk values fall
    # back to "all" silently (defensive against stale bookmarks).
    sf = swim_filter if (swim_filter in top_names or swim_filter == "all") else "all"
    swim_body = (
        _weekly_swimlane_card(events=events, days=days, boundary_hour=bh,
                              stack_by=mode, top_names=top_names, palette=palette,
                              swim_filter=sf)
        or '<div class="muted">本周无事件</div>'
    )
    heat_body = (
        _hour_heatmap_card(events, days, bh, stack_by=mode,
                           palette=palette, top_names=top_names)
        or '<div class="muted">本周无事件</div>'
    )

    # Shared filter pill bar — both swim and heat react to it. Sits above
    # the view-switcher inside the bottom card so it's visible regardless
    # of which view (swim/heat) is active.
    overall_counts = _compute_palette_for_week({d: per_day_stack.get(d, {}) for d in days})[2]
    filter_pills = [
        '<button type="button" class="dim-tab'
        + (' active' if sf == 'all' else '')
        + f'" data-filter="all">{esc(T("filter_all"))}</button>'
    ]
    for n in top_names:
        if overall_counts.get(n, 0) <= 0:
            continue
        color = palette.get(n, _WEEKLY_OTHER_COLOR)
        cls = "dim-tab active" if sf == n else "dim-tab"
        swatch = (
            f'<span style="display:inline-block; width:8px; height:8px; '
            f'border-radius:50%; background:{color}; margin-right:6px; '
            f'vertical-align:middle;"></span>'
        )
        filter_pills.append(
            f'<button type="button" class="{cls}" data-filter="{esc(n)}">'
            f'{swatch}{esc(n)}'
            f'<span class="muted" style="margin-left:6px; font-weight:500; font-size:11px;">×{int(overall_counts[n]) if isinstance(overall_counts[n], (int, float)) else overall_counts[n]}</span>'
            f'</button>'
        )
    shared_filter_bar = (
        '<div data-role="swim-filter" '
        'style="display:flex; flex-wrap:wrap; gap:6px; align-items:center; '
        'margin:8px 0 12px;">'
        f'<span class="muted small" style="margin-right:4px; font-weight:600;">{esc(T("weekly_filter"))}</span>'
        + "".join(filter_pills) +
        '</div>'
    )

    bottom_switcher_pills = (
        '<div class="dim-tabs" data-role="wv-switcher">'
        + "".join(
            f'<button type="button" class="dim-tab{" active" if v_id == view else ""}" '
            f'data-view="{v_id}">{esc(label)}</button>'
            for v_id, label in _weekly_view_opts()
        )
        + '</div>'
    )
    # ┃ Timeline panel ┃ — 24h swim-lane + heat 双视图 + 筛选 pill
    bottom_card = (
        f'<section class="card weekly-viz" id="chart" data-view="{esc(view)}" '
        f'data-filter="{esc(sf)}">'
        + _card_head(
            T("page_timeline"),
            tag=T("tag_timeline"), tag_color="narrative",
            right_html=bottom_switcher_pills,
        )
        + shared_filter_bar
        + '<div class="wv-pane" data-pane="swim">' + swim_body + '</div>'
        + '<div class="wv-pane" data-pane="heat">' + heat_body + '</div>'
        + '</section>'
    )

    # Page-level JS:
    #   (1) save/restore scrollY around dim/unit reloads
    #   (2) intercept dim/unit clicks to navigate via LIVE URL (so any state
    #       JS has updated via replaceState — view, top_view, swim_filter —
    #       is preserved across the reload)
    #   (3) bottom view switcher (CSS attr toggle, replaceState ?view=)
    #   (4) top-chart view switcher (CSS attr toggle, replaceState ?top_view=)
    view_sync_js = (
        '<script>(function(){'
        # ── (1) scroll restore + (2) live-URL navigation for dim/unit pills ──
        'var KEY="daytrace.weekly.scrollY";'
        'var saved=sessionStorage.getItem(KEY);'
        'if(saved!==null){window.scrollTo(0,parseInt(saved,10)||0);sessionStorage.removeItem(KEY);}'
        # Selector is page-wide: dim pills live in .dim-bar but unit pills
        # now live inside .top-chart-card. Both need live-URL navigation
        # so the other card's local state (view, top_view, swim_filter,
        # set via replaceState) survives the reload.
        'document.querySelectorAll("a[data-param]").forEach(function(a){'
        'a.addEventListener("click",function(e){'
        'if(a.classList.contains("active")){e.preventDefault();return;}'
        'e.preventDefault();'
        'sessionStorage.setItem(KEY,String(window.scrollY));'
        'try{var u=new URL(location.href);'
        'u.searchParams.set(a.dataset.param,a.dataset.value);'
        'location.href=u.toString();}catch(err){location.href=a.href;}'
        '});});'
        # ── (3) bottom view switcher (CSS attr toggle) ──
        'var card=document.querySelector(".weekly-viz");'
        'if(card){'
        'card.querySelectorAll("[data-role=\\"wv-switcher\\"] .dim-tab").forEach(function(btn){'
        'btn.addEventListener("click",function(){'
        'var v=btn.dataset.view;'
        'card.setAttribute("data-view",v);'
        'card.querySelectorAll("[data-role=\\"wv-switcher\\"] .dim-tab").forEach(function(b){'
        'b.classList.toggle("active",b.dataset.view===v);});'
        'try{var u=new URL(location.href);u.searchParams.set("view",v);'
        'history.replaceState({},"",u);}catch(e){}'
        '});});'
        # ── (3a) Shared filter: applies to swim ticks AND heat cells ──
        # Heatmap reads data-bins per cell + data-palette on the heat root
        # so it can recolor / re-alpha based on the active filter without
        # any reload.
        'function hexToRgba(h,a){h=String(h||"#2f6fed").replace("#","");'
        'if(h.length===3){h=h.split("").map(function(c){return c+c;}).join("");}'
        'var r=parseInt(h.slice(0,2),16),g=parseInt(h.slice(2,4),16),b=parseInt(h.slice(4,6),16);'
        'return "rgba("+r+","+g+","+b+","+a.toFixed(2)+")";}'
        'function escHtml(s){return String(s==null?"":s).replace(/[&<>\\"]/g,function(c){'
        'return({"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;"})[c];});}'
        'function repaintHeat(filter){'
        'var heat=card.querySelector(".weekly-heat");'
        'if(!heat)return;'
        'var palette={};try{palette=JSON.parse(heat.dataset.palette||"{}");}catch(e){}'
        'var OTHER="#cbd5e1";'
        'var cells=heat.querySelectorAll(".hm-cell");'
        # First pass: compute max for current filter mode
        'var maxC=0;'
        'cells.forEach(function(c){'
        'var bins={};try{bins=JSON.parse(c.dataset.bins||"{}");}catch(e){}'
        'var n=filter==="all"?parseInt(c.dataset.total||"0",10):(bins[filter]||0);'
        'if(n>maxC)maxC=n;});'
        'if(maxC<1)maxC=1;'
        # Second pass: rebuild each cell's innerHTML
        # filter=all  → segments per value, widths weighted by count
        # filter=X    → single segment for X (or empty if no X events)
        'cells.forEach(function(c){'
        'var bins={};try{bins=JSON.parse(c.dataset.bins||"{}");}catch(e){}'
        'var total=parseInt(c.dataset.total||"0",10);'
        'var entries;var n;'
        'if(filter==="all"){entries=Object.keys(bins).map(function(k){return [k,bins[k]];})'
        '.sort(function(a,b){return b[1]-a[1]||(a[0]<b[0]?-1:1);});n=total;}'
        'else{n=bins[filter]||0;entries=n>0?[[filter,n]]:[];}'
        'if(n===0){c.innerHTML="";return;}'
        'var a=0.15+0.85*(n/maxC);'
        'var segHtml=entries.map(function(e){'
        'var col=palette[e[0]]||OTHER;'
        'return \'<span class="hm-seg" data-value="\'+escHtml(e[0])+\'" style="background:\'+hexToRgba(col,a)+\'; flex:\'+e[1]+\'; height:100%;"></span>\';'
        '}).join("");'
        'var labelColor=a>0.55?"white":"var(--ink)";'
        'var labelHtml=\'<span class="hm-cell-label" style="position:absolute; inset:0; display:flex; align-items:center; justify-content:center; font-size:10px; font-weight:700; color:\'+labelColor+\'; text-shadow:0 1px 2px rgba(0,0,0,0.28); pointer-events:none;">\'+n+\'</span>\';'
        'c.innerHTML=segHtml+labelHtml;});}'
        'function applyFilter(v){'
        'card.setAttribute("data-filter",v);'
        # swim ticks
        'card.querySelectorAll(".tl-swim-tick").forEach(function(t){'
        't.style.display=(v==="all"||t.dataset.value===v)?"":"none";});'
        # per-row counts
        'card.querySelectorAll(".tl-swim-row").forEach(function(row){'
        'var c=row.querySelectorAll(\'.tl-swim-tick:not([style*="display: none"])\').length;'
        'var b=row.querySelector("[data-row-count]");if(b)b.textContent="×"+c;});'
        # heat cells
        'repaintHeat(v);'
        # active pill
        'card.querySelectorAll("[data-role=\\"swim-filter\\"] .dim-tab").forEach(function(b){'
        'b.classList.toggle("active",b.dataset.filter===v);});'
        'try{var u=new URL(location.href);'
        'if(v==="all"){u.searchParams.delete("swim_filter");}else{u.searchParams.set("swim_filter",v);}'
        'history.replaceState({},"",u);}catch(e){}}'
        'card.querySelectorAll("[data-role=\\"swim-filter\\"] .dim-tab").forEach(function(btn){'
        'btn.addEventListener("click",function(){applyFilter(btn.dataset.filter);});});'
        # Initial filter from server-rendered active state
        'var init=card.getAttribute("data-filter")||"all";'
        'if(init!=="all"){applyFilter(init);}'
        '}'
        # ── (4a) donut hover: conic-gradient is one DOM node, so we read
        # cursor angle from center, look up which segment that angle is in
        # via the data-segments JSON, then float a tooltip beside the cursor.
        'var donut=document.querySelector(".top-chart-card .cc-donut[data-segments]");'
        'if(donut){'
        'var dSegs=[];try{dSegs=JSON.parse(donut.dataset.segments||"[]");}catch(e){}'
        'var dWrap=donut.parentElement;dWrap.style.position="relative";'
        'var dTip=document.createElement("div");'
        'dTip.style.cssText="position:absolute;pointer-events:none;z-index:10;background:rgba(34,28,18,.95);color:#fff7e8;border-radius:10px;padding:7px 11px;box-shadow:0 10px 26px rgba(0,0,0,.28);font-size:12px;max-width:260px;line-height:1.45;display:none;white-space:nowrap;";'
        'dWrap.appendChild(dTip);'
        'function dEsc(s){return String(s==null?"":s).replace(/[&<>\\"]/g,function(c){return({"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;"})[c];});}'
        'donut.addEventListener("mousemove",function(ev){'
        'var r=donut.getBoundingClientRect();'
        'var cx=r.left+r.width/2,cy=r.top+r.height/2;'
        'var dx=ev.clientX-cx,dy=ev.clientY-cy;'
        'var dist=Math.sqrt(dx*dx+dy*dy);'
        'var outerR=r.width/2;var innerR=outerR*(124/210);'
        'if(dist<innerR||dist>outerR){dTip.style.display="none";return;}'
        'var a=Math.atan2(dx,-dy);if(a<0)a+=2*Math.PI;'
        'var pct=a/(2*Math.PI)*100;'
        'var seg=null;'
        'for(var i=0;i<dSegs.length;i++){if(pct>=dSegs[i].start&&pct<dSegs[i].end){seg=dSegs[i];break;}}'
        'if(!seg)seg=dSegs[dSegs.length-1];'
        'if(!seg){dTip.style.display="none";return;}'
        'dTip.innerHTML="<div style=\\"font-weight:700;margin-bottom:3px;\\"><span style=\\"display:inline-block;width:10px;height:10px;border-radius:2px;background:"+seg.color+";margin-right:6px;vertical-align:middle;\\"></span>"+dEsc(seg.name)+"</div>"+'
        '"<div>"+dEsc(seg.label)+" · "+(seg.share*100).toFixed(1)+"%</div>";'
        'dTip.style.display="block";'
        'var pr=dWrap.getBoundingClientRect();'
        'var x=ev.clientX-pr.left+14;var y=ev.clientY-pr.top+14;'
        'if(x+dTip.offsetWidth>pr.width-4)x=pr.width-dTip.offsetWidth-4;'
        'dTip.style.left=x+"px";dTip.style.top=y+"px";'
        '});'
        'donut.addEventListener("mouseleave",function(){dTip.style.display="none";});'
        '}'
        # ── (4) top chart card switcher (histogram vs distribution) ──
        'var tc=document.querySelector(".top-chart-card");'
        'if(tc){'
        'tc.querySelectorAll("[data-role=\\"tc-switcher\\"] .dim-tab").forEach(function(btn){'
        'btn.addEventListener("click",function(){'
        'var v=btn.dataset.view;'
        'tc.setAttribute("data-tc-view",v);'
        'tc.querySelectorAll("[data-role=\\"tc-switcher\\"] .dim-tab").forEach(function(b){'
        'b.classList.toggle("active",b.dataset.view===v);});'
        'try{var u=new URL(location.href);'
        'if(v==="chart"){u.searchParams.delete("top_view");}else{u.searchParams.set("top_view",v);}'
        'history.replaceState({},"",u);}catch(e){}'
        '});});}'
        '})();</script>'
    )

    # Per-day links — single horizontal row, each opens in a new tab.
    day_chips = "".join(
        f'<a href="/today?date={d}" target="_blank" rel="noopener" '
        f'class="day-jump">{d[5:]} {_short_weekday(d)}</a>'
        for d in days
    )
    day_links_html = (
        '<section class="card">'
        + _card_head(
            T("jump_to_day"), tag_color="narrative",
            right_html=f'<div class="day-jumps">{day_chips}</div>',
        )
        + '</section>'
    )

    # Right column: just the histogram card. Insights moved to a full-width
    # row below the top row.
    right_column_body = top_histogram_card

    tasks_panel_html = _tasks_panel(con, days, bh)
    audit_html = _alignment_audit_card(con, days)
    daily_timeline_card = _render_weekly_daily_timeline_card(con, days)
    body = (
        # Row 1: Report | Chart
        '<section class="report-grid">'
        + weekly_report_card
        + '<div class="right-column">' + right_column_body + '</div>'
        + '</section>'
        # Row 2: Insights (full-width 3-col)
        + insights_card_html
        # Timeline panel (swim/heat)
        + bottom_card
        + view_sync_js
        # Per-day timeline card — 7 cols horizontal, headline + narrative each
        + daily_timeline_card
        + tasks_panel_html
        + audit_html
        + day_links_html
    )

    if total_events == 0:
        body = (
            '<section class="card"><div class="muted">'
            f'本周（{monday} ~ {sunday}）暂无事件数据。'
            '可能是 catchup 还没跑到，或者这周确实没记录。'
            '</div></section>' + day_links_html
        )

    subtitle = (
        f"{monday} ~ {sunday} · {total_events} events · "
        f"{total_minutes/60:.1f}h active · {active_days}/7 days"
    )
    return layout(f"DayTrace · {week}", subtitle, "weekly", body, date_control=header_controls)


def _apply_audit_aliases(db_path: Path, picks: list[tuple[str, str]]) -> dict:
    """Persist audit dropdown picks to config/work_item_aliases.yaml + rebuild
    event links. `picks` is a list of (project_guess, record_id_or_empty).
    Empty record_id means "remove this alias if it exists".

    Returns {"added": N, "removed": M, "links_inserted": K}.
    """
    from daytrace.work_items import DEFAULT_ALIASES, load_aliases, rebuild_links

    existing = load_aliases()
    added = removed = 0
    for pg, rid in picks:
        pg = (pg or "").strip()
        rid = (rid or "").strip()
        if not pg:
            continue
        if rid:
            if existing.get(pg) != rid:
                existing[pg] = rid
                added += 1
        else:
            if pg in existing:
                existing.pop(pg)
                removed += 1

    # Write back. Preserve the header comment by reading the file first
    # and only replacing the aliases: section.
    path = Path(DEFAULT_ALIASES)
    if path.exists():
        original = path.read_text(encoding="utf-8")
        # Split into pre-aliases preamble + the aliases block
        lines = original.splitlines()
        keep_lines: list[str] = []
        in_aliases = False
        for ln in lines:
            stripped = ln.strip()
            if stripped.startswith("aliases:"):
                in_aliases = True
                continue
            if in_aliases:
                # Skip indented or commented continuation lines of the existing block
                if ln.startswith(" ") or ln.startswith("\t") or stripped.startswith("#") or stripped == "":
                    continue
                in_aliases = False
            keep_lines.append(ln)
        preamble = "\n".join(keep_lines).rstrip() + "\n\n" if keep_lines else ""
    else:
        preamble = ""

    out = preamble + "aliases:\n"
    if not existing:
        out += "  # (empty — use the audit panel on /today or /weekly to add mappings)\n"
    else:
        for k in sorted(existing.keys()):
            out += f'  "{k}": {existing[k]}\n'
    path.write_text(out, encoding="utf-8")

    # Rebuild links (covers the 30-day window). Uses fresh aliases.
    from daytrace.db import connect, init_db
    con = connect(db_path); init_db(con)
    stats = rebuild_links(con, lookback_days=30)
    return {"added": added, "removed": removed, "links_inserted": stats["links_inserted"]}


class Handler(BaseHTTPRequestHandler):
    db_path: Path = DEFAULT_DB

    def log_message(self, format, *args):
        print("[dashboard] " + format % args)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/work-items/alias":
            self.send_response(404); self.end_headers(); return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        form = parse_qs(body, keep_blank_values=True)
        projects = form.get("project[]", [])
        records = form.get("record[]", [])
        picks = list(zip(projects, records))
        try:
            stats = _apply_audit_aliases(self.db_path, picks)
        except Exception as exc:
            print(f"[dashboard] audit POST failed: {exc}")
            self.send_response(500); self.end_headers()
            self.wfile.write(str(exc).encode("utf-8"))
            return
        # Redirect back to the referring page with a success indicator
        # so the audit card can show "X added · Y removed · Z links rebuilt".
        # We deliberately point the fragment at #alignment-audit so the
        # page scrolls back to where the user was — without it, after the
        # 303 the browser lands at the top of /weekly and the user can't
        # tell that anything happened.
        referer = self.headers.get("Referer") or "/weekly"
        # Strip any existing fragment AND any prior audit_applied query
        # param (so successive saves don't pile them up).
        from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
        parts = urlsplit(referer)
        qs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
              if k != "audit_applied"]
        qs.append(("audit_applied",
                   f"{stats['added']}_{stats['removed']}_{stats['links_inserted']}"))
        target = urlunsplit((
            parts.scheme, parts.netloc, parts.path,
            urlencode(qs), "alignment-audit",
        ))
        print(f"[dashboard] audit POST ok: {stats} → {target}")
        self.send_response(303)
        self.send_header("Location", target)
        self.end_headers()

    def do_GET(self):
        # Set the per-request language context from the cookie, so every
        # T() call inside the renderer chain picks it up without
        # threading `lang` through every helper.
        _CURRENT_LANG.set(_lang_from_request(self))

        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        date = qs.get("date", [None])[0] or None
        try:
            if parsed.path == "/":
                redirect_params = {}
                if date:
                    redirect_params["date"] = date
                mode = qs.get("mode", [None])[0] or None
                if mode and mode != "project":
                    redirect_params["mode"] = mode
                self.send_response(302)
                self.send_header("Location", "/today" + (("?" + urlencode(redirect_params)) if redirect_params else ""))
                self.end_headers()
            elif parsed.path == "/today":
                mode = qs.get("mode", [None])[0] or None
                unit = qs.get("unit", [None])[0] or None
                style = qs.get("style", [None])[0] or None
                html_response(self, today_page(self.db_path, date, mode=mode, unit=unit, style=style))
            elif parsed.path == "/weekly":
                week = qs.get("week", [None])[0] or None
                # Accept ?date=YYYY-MM-DD too (calendar picker shares the
                # same URL shape as /today). Convert to ?week=… server-side.
                date_param = qs.get("date", [None])[0] or None
                if date_param and not week:
                    try:
                        from daytrace.db import date_to_iso_week
                        week = date_to_iso_week(date_param)
                    except Exception:
                        week = None
                w_unit = qs.get("unit", [None])[0] or None
                w_mode = qs.get("mode", [None])[0] or qs.get("stack_by", [None])[0] or None
                w_view = qs.get("view", [None])[0] or None
                w_swim = qs.get("swim_filter", [None])[0] or None
                w_top = qs.get("top_view", [None])[0] or None
                html_response(self, weekly_page(
                    self.db_path, week, unit=w_unit, mode=w_mode,
                    view=w_view, swim_filter=w_swim, top_view=w_top,
                ))
            elif parsed.path == "/sources":
                self.send_response(302)
                self.send_header("Location", "/events")
                self.end_headers()
            elif parsed.path == "/events":
                table_choice = qs.get("table", ["events"])[0] or "events"
                # Custom-rendered pages (kept as their own functions because
                # they have AI cells, calendar pickers, etc.); everything
                # else falls through to the generic table browser.
                if table_choice == "day":
                    html_response(self, day_report_table_page(self.db_path, qs))
                elif table_choice == "day_project":
                    html_response(self, day_project_report_table_page(self.db_path, qs))
                elif table_choice == "events":
                    html_response(self, events_page(self.db_path, qs))
                elif table_choice in TABLE_VIEWS and \
                        TABLE_VIEWS[table_choice].get("kind") == "generic":
                    html_response(self, generic_table_page(self.db_path, table_choice, qs))
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
