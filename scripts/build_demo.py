#!/usr/bin/env python3
"""Build a static, GitHub-Pages-hostable demo of the DayTrace dashboard.

Generates a fresh `data/demo.sqlite` populated with believable fake data
for 2026-05-11 .. 2026-05-19 (9 days), then spins up the existing dashboard
server pointed at that DB, captures `/today?date=2026-05-19` and
`/weekly?week=2026-W20` (the fully-in-the-past ISO week May 11–17), in both
zh and en, post-processes the HTML to be self-contained (no broken nav links,
no leaked PII, language-switcher banner), and writes the result to
`docs/demo/{index,weekly}{,.en}.html`.

Run:    python scripts/build_demo.py
Preview: python -m http.server 8000 --directory docs && open http://localhost:8000/demo/

The script is idempotent (fixed RNG seed, deterministic ids/timestamps).
"""

from __future__ import annotations

import json
import os
import random
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from daytrace.db import (  # noqa: E402
    connect,
    init_db,
    upsert_events,
    upsert_activity_labels,
)
from daytrace.schema import TraceEvent  # noqa: E402
from daytrace.daily_report import regenerate_day_from_db  # noqa: E402


DB_PATH = REPO_ROOT / "data" / "demo.sqlite"
OUT_DIR = REPO_ROOT / "docs" / "demo"
PORT = 8799
TODAY = "2026-05-19"
# Weekly demo points at the fully-in-the-past ISO week (Mon 05-11 → Sun 05-17)
# so the weekly page has 7 full days of varied data to show.
WEEK = "2026-W20"
DAYS = [
    "2026-05-11",  # Mon — paper-revision lean (deadline pressure builds)
    "2026-05-12",  # Tue — paper-revision lean
    "2026-05-13",  # Wed — paper-revision lean
    "2026-05-14",  # Thu — infra-cleanup lean
    "2026-05-15",  # Fri — daytrace lean
    "2026-05-16",  # Sat — daytrace casual, light
    "2026-05-17",  # Sun — daytrace casual, light
    "2026-05-18",  # Mon — start of next week (W21), mixed
    "2026-05-19",  # Tue — today, mixed (the daily-report focal day)
]
# Project lean per day. Drives event-stream slot rotation so the weekly
# stacked-bar chart has visible variation across the 7-day window.
PROJECT_LEAN = {
    "2026-05-11": "paper-revision",
    "2026-05-12": "paper-revision",
    "2026-05-13": "paper-revision",
    "2026-05-14": "infra-cleanup",
    "2026-05-15": "daytrace",
    "2026-05-16": "daytrace",
    "2026-05-17": "daytrace",
}
# Days that get full hand-written AI batch payloads (overview is hand-written
# for every active day already, but project_summary_batch + project_continuity_batch
# only matter for the weekly aggregation and the per-project drill-down).
ACTIVE_DAYS = {
    "2026-05-11", "2026-05-12", "2026-05-13", "2026-05-14", "2026-05-15",
    "2026-05-18", "2026-05-19",
}
LIGHT_DAYS: set[str] = set()
WEEKEND_DAYS = {"2026-05-16", "2026-05-17"}  # sparse but not empty

PROJECTS = ["paper-revision", "daytrace", "infra-cleanup"]

# Bilingual activity labels mapped from a (source, kind) heuristic.
ACTIVITY_LABELS = {
    "code-commit":    {"zh": "代码提交", "en": "Code commit"},
    "code-iteration": {"zh": "代码迭代", "en": "Code iteration"},
    "ai-pair":        {"zh": "AI 结对", "en": "AI pairing"},
    "writing":        {"zh": "写作修订", "en": "Writing & revision"},
    "reading":        {"zh": "文献阅读", "en": "Literature reading"},
    "ops":            {"zh": "运维操作", "en": "Ops work"},
    "review":         {"zh": "代码评审", "en": "Code review"},
    "context":        {"zh": "上下文切换", "en": "Context switching"},
}


# ───────────────────────── work_items ─────────────────────────

WORK_ITEMS = [
    {
        "record_id": "rec_demo_001",
        "title": "修订 §4 实验设置",
        "title_en": "Revise §4 experimental setup",
        "project": "paper-revision",
        "status": "进行中",
        "priority": "P1",
        "due_date": "2026-05-30",
        "next_action_date": None,
        "subtitle": "JSAC 投稿",
    },
    {
        "record_id": "rec_demo_002",
        "title": "回复审稿人2意见",
        "title_en": "Reply to reviewer 2 comments",
        "project": "paper-revision",
        "status": "进行中",
        "priority": "P0",
        "due_date": "2026-05-22",
        "next_action_date": "2026-05-22",
        "subtitle": "JSAC 投稿",
    },
    {
        "record_id": "rec_demo_003",
        "title": "为文档站点生成 demo 数据",
        "title_en": "Demo data generator for docs site",
        "project": "daytrace",
        "status": "完成",
        "priority": "P2",
        "due_date": "2026-05-19",
        "next_action_date": None,
        "subtitle": "DayTrace v0.4",
    },
    {
        "record_id": "rec_demo_004",
        "title": "开源就绪性审查",
        "title_en": "Open-source readiness review",
        "project": "daytrace",
        "status": "待办",
        "priority": "P2",
        "due_date": "2026-06-05",
        "next_action_date": None,
        "subtitle": "DayTrace v0.4",
    },
    {
        "record_id": "rec_demo_005",
        "title": "迁移 staging 集群到 k8s 1.30",
        "title_en": "Migrate staging cluster to k8s 1.30",
        "project": "infra-cleanup",
        "status": "进行中",
        "priority": "P1",
        "due_date": "2026-05-28",
        "next_action_date": None,
        "subtitle": "平台升级",
    },
    {
        "record_id": "rec_demo_006",
        "title": "Terraform: 清理未使用 IAM 角色",
        "title_en": "Terraform: drop unused IAM roles",
        "project": "infra-cleanup",
        "status": "完成",
        "priority": "P3",
        "due_date": "2026-05-18",
        "next_action_date": None,
        "subtitle": "平台升级",
    },
]


def insert_work_items(con):
    for wi in WORK_ITEMS:
        con.execute(
            """
            INSERT OR REPLACE INTO work_items
              (record_id, table_key, title, title_en, subtitle, status, priority,
               tags, project_source, external_links, due_date, next_action_date,
               weekly_hours, next_action, agent_workspace, raw_fields_json)
            VALUES (?, 'tasks', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                wi["record_id"],
                wi["title"],
                wi["title_en"],
                wi["subtitle"],
                wi["status"],
                wi["priority"],
                json.dumps([wi["project"]], ensure_ascii=False),
                wi["subtitle"],
                json.dumps([], ensure_ascii=False),
                wi["due_date"],
                wi["next_action_date"],
                4.0,
                None,
                json.dumps({}, ensure_ascii=False),
            ),
        )
    con.commit()


# ───────────────────────── events ─────────────────────────

# Per-project preferred source + "topic" templates (bilingual flavor).
TEMPLATES = {
    "paper-revision": [
        ("codex", "chat", "Reword §4.2 setup paragraph",
         "Reworking the §4.2 paragraph: tighten the wording of the simulation setup, restate the assumptions list, and align the notation with §3."),
        ("codex", "chat", "Draft response to Reviewer 2",
         "Drafted the rebuttal to reviewer 2's main critique: clarified the boundary-condition derivation and added the requested sensitivity sweep."),
        ("docs", "edit", "Overleaf: edit experiments table",
         "Edited the experiments table in Overleaf; added baseline column, recomputed standard errors, refreshed caption."),
        ("docs", "read", "Reading: arxiv:2403.14250",
         "Skimmed arxiv:2403.14250 (control-theoretic robustness) — useful for §5 discussion of failure modes."),
        ("claude_code", "session", "Claude Code: regenerate figures",
         "Asked Claude Code to regenerate fig.7 and fig.9 with the updated dataset; verified seeds match."),
        ("macos_activity", "active", "Active in TeXShop",
         "Foreground app: TeXShop. Drafting the revised abstract."),
    ],
    "daytrace": [
        ("git", "commit", "commit: fake-data generator scaffold",
         "Added scripts/build_demo.py scaffold; wires up event seeding for 7 days; no AI calls yet."),
        ("git", "commit", "commit: bilingual activity labels in demo",
         "Demo data now writes bilingual label_json so the UI renders English on /en pages."),
        ("claude_code", "session", "Claude Code: design demo build script",
         "Discussed with Claude Code: what's the minimum AI-channel payload that survives validate_overview?"),
        ("codex", "chat", "Codex: tighten card head component",
         "Codex pass on _card_head — pulled inline color literals into the semantic palette."),
        ("docs", "edit", "README: add demo preview section",
         "Edited README.md to point readers at docs/demo/ for a no-install preview of the dashboard."),
        ("macos_activity", "active", "Active in VS Code",
         "Foreground app: Visual Studio Code. Working in daytrace/ tree."),
    ],
    "infra-cleanup": [
        ("git", "commit", "commit: bump kubectl client to 1.30",
         "Updated kubectl client image in the staging Helm chart; smoke test still green."),
        ("git", "commit", "commit: terraform drop 4 unused IAM roles",
         "Dropped 4 IAM roles flagged by AWS Access Analyzer; ran terraform plan locally before apply."),
        ("codex", "chat", "Codex: explain k8s upgrade gotchas",
         "Asked Codex about the 1.29→1.30 PodSecurity API changes; noted two CRDs that need manual migration."),
        ("claude_code", "session", "Claude Code: review terraform diff",
         "Claude Code reviewed the IAM cleanup diff; flagged one role still referenced by the legacy CI runner."),
        ("docs", "edit", "Runbook: staging upgrade checklist",
         "Updated the staging-cluster-upgrade runbook with the rollback step we missed last time."),
        ("macos_activity", "active", "Active in iTerm2",
         "Foreground app: iTerm2 — kubectl + terraform shells."),
    ],
}

MISC_TEMPLATES = [
    ("macos_activity", "active", "Active in Slack",
     "Foreground app: Slack — team threads, no specific project context."),
    ("macos_activity", "active", "Active in Mail",
     "Triaged inbox; nothing project-tagged."),
    ("docs", "read", "Personal: read RSS feed",
     "Skimmed the morning feed; saved 2 items for later."),
]


def _source_to_label_key(source: str, kind: str) -> str:
    if source == "git":
        return "code-commit"
    if source == "claude_code":
        return "ai-pair"
    if source == "codex":
        return "ai-pair"
    if source == "docs" and "read" in (kind or ""):
        return "reading"
    if source == "docs":
        return "writing"
    if source == "macos_activity":
        return "context"
    return "code-iteration"


def _evidence_for(source: str, kind: str, title: str, summary: str, project: str, idx: int) -> dict:
    if source == "git":
        sha = f"{abs(hash((title, idx))) & 0xFFFFFFFF:08x}"
        return {
            "repo": f"github.com/example/{project}",
            "branch": "main",
            "sha": sha,
            "files_changed": 3 + (idx % 5),
            "message_subject": title.split(":", 1)[-1].strip(),
        }
    if source == "claude_code":
        return {
            "session_id": f"cc-demo-{project}-{idx:03d}",
            "turns": 4 + (idx % 7),
            "tokens_in": 1800 + (idx * 90 % 1500),
            "tokens_out": 600 + (idx * 70 % 800),
        }
    if source == "codex":
        return {
            "session_id": f"cx-demo-{project}-{idx:03d}",
            "turns": 3 + (idx % 5),
            "model": "gpt-4o-mini",
        }
    if source == "docs":
        return {
            "doc": "Overleaf · revision-draft.tex" if project == "paper-revision" else "internal-runbook.md",
            "chars_delta": 120 + (idx * 17 % 900),
        }
    if source == "macos_activity":
        return {
            "app": title.split("Active in ", 1)[-1],
            "window_count": 1 + (idx % 4),
            "active_seconds": 240 + (idx * 53 % 600),
        }
    return {}


def _time_slots_for_day(date: str, density: str, rng: random.Random) -> list[tuple[int, int]]:
    """Return list of (hour, minute) start tuples for events on this day."""
    slots: list[tuple[int, int]] = []
    if density == "weekend":
        # Casual weekend coding: ~10 events scattered across afternoon/evening.
        for _ in range(rng.randint(8, 12)):
            h = rng.randint(11, 22)
            m = rng.randint(0, 59)
            slots.append((h, m))
        return sorted(slots)
    if density == "light":
        ranges = [(9, 12, 10), (14, 18, 14), (20, 22, 6)]
    else:  # full
        ranges = [(9, 12, 16), (14, 18, 20), (20, 22, 8)]
    for start_h, end_h, n in ranges:
        for _ in range(n):
            h = rng.randint(start_h, end_h - 1)
            m = rng.randint(0, 59)
            slots.append((h, m))
    return sorted(slots)


def generate_events_for_day(date: str, rng: random.Random) -> list[TraceEvent]:
    if date in WEEKEND_DAYS:
        density = "weekend"
    elif date in LIGHT_DAYS:
        density = "light"
    else:
        density = "full"

    slots = _time_slots_for_day(date, density, rng)
    events: list[TraceEvent] = []
    counter = 0
    lean = PROJECT_LEAN.get(date)
    for (h, m) in slots:
        counter += 1
        # Project mix per day: a daily lean (~65%) + the other two projects
        # share the rest (~25%), plus a ~10% misc bucket.
        roll = rng.random()
        if roll < 0.10:
            project = None  # misc
        elif date == "2026-05-19":
            # Story arc: morning paper-revision, afternoon daytrace, evening infra
            if h < 13:
                project = "paper-revision"
            elif h < 19:
                project = "daytrace"
            else:
                project = "infra-cleanup"
        elif date == "2026-05-18":
            if h < 13:
                project = "infra-cleanup"
            elif h < 19:
                project = "paper-revision"
            else:
                project = "daytrace"
        elif lean is not None:
            # Lean ~65% to the day's primary project, rest split among the others.
            if roll < 0.10 + 0.55:
                project = lean
            else:
                others = [p for p in PROJECTS if p != lean]
                project = rng.choice(others)
        else:
            project = rng.choice(PROJECTS)

        if project is None:
            tpl = rng.choice(MISC_TEMPLATES)
        else:
            tpl = rng.choice(TEMPLATES[project])
        source, kind, title, summary = tpl

        start_dt = datetime.fromisoformat(f"{date}T{h:02d}:{m:02d}:00")
        dur_min = rng.choice([3, 5, 8, 12, 15, 18, 25])
        end_dt = start_dt + timedelta(minutes=dur_min)

        eid = f"demo-{date}-{counter:03d}-{source}"
        ev = TraceEvent(
            id=eid,
            source=source,
            kind=kind,
            start=start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            end=end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            title=title,
            summary=summary,
            project_guess=project,
            sensitivity="normal",
            evidence=_evidence_for(source, kind, title, summary, project or "misc", counter),
            raw_ref=None,
            device_id="Mac",
            location_id="home",
            collector_id="demo-builder",
        )
        events.append(ev)
    return events


def insert_events_for_all_days(con):
    rng = random.Random(20260519)  # fixed seed → idempotent
    for d in DAYS:
        events = generate_events_for_day(d, rng)
        upsert_events(con, events, run_date=d, commit=False)
    con.commit()


def insert_activity_labels(con):
    rows = con.execute(
        "SELECT id, source, kind FROM events WHERE id LIKE 'demo-%'"
    ).fetchall()
    label_rows = []
    for r in rows:
        key = _source_to_label_key(r["source"], r["kind"] or "")
        bi = ACTIVITY_LABELS[key]
        label_rows.append({
            "event_id": r["id"],
            "label": bi["zh"],
            "label_json": bi,
            "source": "ai",
            "confidence": 0.9,
            "model": "demo-builder",
        })
    upsert_activity_labels(con, label_rows, commit=False)
    con.commit()


def insert_event_work_item_links(con):
    """Link ~60% of project-tagged events to a work_item from their project."""
    project_to_wis = {}
    for wi in WORK_ITEMS:
        project_to_wis.setdefault(wi["project"], []).append(wi["record_id"])

    rows = con.execute(
        "SELECT id, project_guess FROM events WHERE id LIKE 'demo-%' AND project_guess IS NOT NULL"
    ).fetchall()
    rng = random.Random(99)
    for r in rows:
        proj = r["project_guess"]
        candidates = project_to_wis.get(proj, [])
        if not candidates:
            continue
        if rng.random() > 0.60:
            continue
        record_id = rng.choice(candidates)
        con.execute(
            """
            INSERT OR IGNORE INTO event_work_item_links
              (event_id, record_id, match_type, confidence)
            VALUES (?, ?, 'alias', 0.9)
            """,
            (r["id"], record_id),
        )
    con.commit()


# ───────────────────────── AI channel payloads ─────────────────────────

def _bi(zh: str, en: str) -> dict:
    return {"zh": zh, "en": en}


def fake_overview_payload(date: str) -> dict:
    if date == "2026-05-19":
        return {
            "headline": _bi(
                "上午磨论文,午后造 demo,傍晚清 IAM",
                "Morning paper, afternoon demo, evening IAM cleanup",
            ),
            "overview": {
                "narrative": _bi(
                    "上午一头扎进 paper-revision,跟 Codex 来回拉扯 §4 实验设置的措辞,"
                    "顺手把审稿人2 的鲁棒性问题在 Overleaf 里草拟出回复;"
                    "午后切到 daytrace,盯着 demo 数据生成器,把假数据的味道调到尽量像真的,"
                    "中间让 Claude Code 帮忙审了一遍 build_demo 的骨架;"
                    "傍晚转头清理 infra-cleanup 里那批没人用的 IAM 角色,terraform plan 之前还是手动核了一遍 —"
                    "三条线都没收尾,但每条都往前挪了一格,paper 那边的 deadline 周五在压。",
                    "Spent the morning deep in paper-revision, going back and forth with Codex on the §4 "
                    "experimental-setup wording and drafting a Reviewer-2 rebuttal in Overleaf. After lunch "
                    "switched to daytrace and tuned the demo data generator so the fake events read like real "
                    "ones, with Claude Code reviewing the build_demo scaffold. Evening pivoted to "
                    "infra-cleanup — pruned four unused IAM roles via terraform, did a manual plan-review first. "
                    "Three threads moved a step each; none closed; the paper deadline is now Friday-close.",
                ),
            },
            "trend": {
                "direction": "rising",
                "comparison": _bi(
                    "活跃时长比昨天多 1.4 小时,切换次数下降,大块专注更长。",
                    "Active hours up 1.4h vs yesterday, fewer context switches, longer deep blocks.",
                ),
            },
            "highlights": [
                _bi("修订 §4 实验设置:Codex 对齐 §3 记号,准备投 Overleaf。",
                    "Revise §4 experimental setup: aligned notation with §3 via Codex, staged in Overleaf."),
                _bi("回复审稿人2意见:鲁棒性讨论草稿成型,缺敏感性扫描数字。",
                    "Reply to reviewer 2 comments: rebuttal draft ready, still need the sensitivity sweep numbers."),
                _bi("为文档站点生成 demo 数据:骨架跑通,假事件 7 天满了。",
                    "Demo data generator for docs site: scaffold runs, 7 days of fake events seeded."),
                _bi("Terraform: 清理未使用 IAM 角色:4 个角色已 drop,plan 通过。",
                    "Terraform: drop unused IAM roles: 4 roles dropped, plan clean."),
            ],
            "work_pattern": [
                _bi("活跃时长 5.2h,比 7 天均值 (4.3h) 多 21%。",
                    "Active 5.2h today vs 7-day average 4.3h (+21%)."),
                _bi("最长专注块 78 分钟,在 14:20-15:38(daytrace)。",
                    "Longest focus block 78 min, 14:20-15:38 (daytrace)."),
                _bi("上下文切换 14 次,比昨天 (22 次) 明显下降。",
                    "Context switches dropped to 14, from 22 yesterday."),
                _bi("收工时间 22:35,比近 7 天均值晚 40 分钟。",
                    "Wrapped at 22:35, 40 min later than the 7-day median."),
            ],
            "suggestions": [
                _bi("回复审稿人2意见:周五 (05-22) 到期,补完敏感性扫描数字。",
                    "Reply to reviewer 2 comments: due Fri (05-22) — fill in the sensitivity-sweep numbers."),
                _bi("开源就绪性审查:今天没动,该挑一块开始啃。",
                    "Open-source readiness review: no movement today; pick one slice to start."),
                _bi("迁移 staging 集群到 k8s 1.30:CRD 迁移步骤还没写进 runbook。",
                    "Migrate staging cluster to k8s 1.30: CRD migration steps still missing from runbook."),
            ],
        }
    if date == "2026-05-18":
        return {
            "headline": _bi("Infra 收尾日,paper 起步,daytrace 设计",
                            "Infra wind-down, paper restart, daytrace design"),
            "overview": {
                "narrative": _bi(
                    "周一上午一路在 infra-cleanup 上 —— 跑了一轮 staging 集群 1.30 的 dry-run,"
                    "顺便把 terraform plan 里那批 dead IAM 角色筛了一遍。"
                    "下午切回 paper-revision,把 §4 的实验段重读一遍,"
                    "在 Codex 里起了一个回复审稿人2的骨架;"
                    "晚上回到 daytrace 的开源准备,想清楚了 demo 数据生成器要长什么样。"
                    "节奏比上周末重一些,但都还在状态。",
                    "Spent the morning deep in infra-cleanup — dry-running the 1.30 staging upgrade and "
                    "auditing the dead IAM roles flagged by terraform plan. After lunch switched back to "
                    "paper-revision: re-read §4 carefully and started a Reviewer-2 rebuttal skeleton in Codex. "
                    "Evening on daytrace's open-source prep, scoping the demo-data generator. Heavier pace "
                    "than the weekend, but everything stayed on the rails.",
                ),
            },
            "trend": {
                "direction": "new",
                "comparison": _bi("从周末轻量节奏切到工作日重投入。",
                                  "Pivoted from a quiet weekend back into a full-day rhythm."),
            },
            "highlights": [
                _bi("迁移 staging 集群到 k8s 1.30:dry-run 跑通,记下两个 CRD。",
                    "Migrate staging cluster to k8s 1.30: dry-run green, two CRDs noted."),
                _bi("Terraform: 清理未使用 IAM 角色:plan 列出 4 个候选。",
                    "Terraform: drop unused IAM roles: plan surfaced 4 candidates."),
                _bi("修订 §4 实验设置:重读完成,确认要改两段。",
                    "Revise §4 experimental setup: re-read done, two paragraphs flagged."),
            ],
            "work_pattern": [
                _bi("活跃 3.8h,周末后回升。", "Active 3.8h, recovering after the weekend."),
                _bi("上下文切换 22 次,比平时略高 (3 条主线并行)。",
                    "22 context switches, a touch high (3 threads in parallel)."),
            ],
            "suggestions": [
                _bi("回复审稿人2意见:周五到期,明天定稿 rebuttal。",
                    "Reply to reviewer 2 comments: due Friday — finalize the rebuttal tomorrow."),
                _bi("Terraform: 清理未使用 IAM 角色:apply 前再核一次。",
                    "Terraform: drop unused IAM roles: re-verify before terraform apply."),
            ],
        }
    # Hand-written narratives for the W20 days (May 11–17). Lean to the day's
    # primary project per PROJECT_LEAN; voice modeled after the 05-19 narrative.
    PER_DAY = {
        "2026-05-11": {
            "headline": _bi(
                "周一开张,paper §4 实验段开切",
                "Monday kickoff: cracking open the §4 experiments",
            ),
            "narrative": _bi(
                "周一一早扎进 paper-revision —— §4 实验段已经在脑子里转了一周末,"
                "今天总算坐下来开始改。和 Codex 来回拉了几轮记号统一,"
                "把 §3 用到的符号表搬过来对齐;午后顺手起了 Reviewer-2 那条鲁棒性意见的回复骨架。"
                "infra 那边只是顺手 kubectl 看了眼 staging 集群,daytrace 没动。"
                "deadline 周五,节奏开始紧。",
                "Monday kickoff went straight into paper-revision — the §4 experiments section "
                "had been turning over all weekend, and today was the day to actually start rewriting. "
                "Several rounds with Codex on notation alignment with §3; an afternoon pass started "
                "the skeleton of the Reviewer-2 robustness rebuttal. Only a casual kubectl glance "
                "at the staging cluster on the infra side; daytrace untouched. Deadline is Friday — "
                "tempo is picking up.",
            ),
            "trend": ("rising", _bi("从周末轻量节奏切回工作日,paper 主线推进。",
                                     "Up from a quiet weekend into the work-week paper push.")),
            "highlights": [
                _bi("修订 §4 实验设置:§3/§4 记号统一基本完成。",
                    "Revise §4 experimental setup: §3/§4 notation mostly aligned."),
                _bi("回复审稿人2意见:鲁棒性回复骨架成型。",
                    "Reply to reviewer 2: robustness rebuttal skeleton drafted."),
            ],
            "work_pattern": [
                _bi("活跃 4.4h,绝大多数时间在 paper 主线上。",
                    "Active 4.4h, almost all on paper-revision."),
                _bi("上下文切换 9 次,深度块明显。",
                    "Only 9 context switches; deep blocks dominated."),
            ],
            "suggestions": [
                _bi("回复审稿人2意见:明天补具体数字段落。",
                    "Reply to reviewer 2: fill in concrete numbers tomorrow."),
            ],
        },
        "2026-05-12": {
            "headline": _bi("继续磨 paper,Reviewer-2 回复成型",
                             "More paper polish; Reviewer-2 rebuttal takes shape"),
            "narrative": _bi(
                "周二还是 paper-revision 主场。把 §4 实验表的 baseline 列重算了一遍,"
                "用 Claude Code 帮忙跑了几次 fig.7 / fig.9 的再生成,种子核对一致。"
                "Reviewer-2 那封回复填进了一段敏感性扫描的描述,但具体数字还得等仿真跑完。"
                "infra-cleanup 只是把 kubectl 命令历史翻了一下,看 1.30 升级有没有挂的工单 —— 没有。",
                "Tuesday stayed on paper-revision. Recomputed the baseline column in the §4 "
                "experiments table, then leaned on Claude Code to regenerate fig.7 / fig.9 with "
                "the updated dataset (seeds verified). The Reviewer-2 reply got a paragraph on "
                "sensitivity sweeps drafted — but the concrete numbers still need a simulation run. "
                "Just a kubectl history glance on the infra side; no open tickets on the 1.30 upgrade.",
            ),
            "trend": ("steady", _bi("和昨天近似,paper 主线持续。",
                                     "Similar to yesterday; paper thread keeps rolling.")),
            "highlights": [
                _bi("修订 §4 实验设置:baseline 列重算完成。",
                    "Revise §4 experimental setup: baseline column recomputed."),
                _bi("Claude Code 再生成 fig.7/fig.9 — 种子对齐。",
                    "Claude Code regenerated fig.7/fig.9 with seeds aligned."),
                _bi("回复审稿人2意见:加了敏感性扫描段。",
                    "Reply to reviewer 2: added a sensitivity-sweep paragraph."),
            ],
            "work_pattern": [
                _bi("活跃 4.6h,paper 占 80%。",
                    "Active 4.6h, paper-revision ~80%."),
                _bi("最长专注块 64 分钟。", "Longest focus block 64 min."),
            ],
            "suggestions": [
                _bi("回复审稿人2意见:跑出敏感性数字再回。",
                    "Reply to reviewer 2: run the sensitivity numbers before replying."),
            ],
        },
        "2026-05-13": {
            "headline": _bi("Paper 收口,准备转 infra",
                             "Wrapping the paper push; pivot to infra incoming"),
            "narrative": _bi(
                "周三 paper 这条线推到一个收口 —— §4 实验段最后通读了一遍,"
                "Reviewer-2 回复也定了七成稿;敏感性扫描结果跑出来了,数字已经填到回复里。"
                "下午开始把脑子挪去 infra:看了 k8s 1.30 的 release note,记下两个 PodSecurity API 的 break change。"
                "daytrace 还是没动,留给周末。",
                "Wednesday brought the paper thread to a partial close — final read-through "
                "of §4 done, and the Reviewer-2 reply is ~70% locked. The sensitivity-sweep "
                "results came back and the numbers are now in the rebuttal. Afternoon shifted "
                "mental gears toward infra: read the k8s 1.30 release notes and flagged two "
                "PodSecurity API breaking changes. daytrace stayed parked for the weekend.",
            ),
            "trend": ("steady", _bi("paper 收尾,infra 起步,节奏稳。",
                                     "Paper winding down, infra ramping up, steady pace.")),
            "highlights": [
                _bi("修订 §4 实验设置:通读完成。",
                    "Revise §4 experimental setup: final read-through done."),
                _bi("回复审稿人2意见:敏感性数字回填完成。",
                    "Reply to reviewer 2: sensitivity numbers backfilled."),
                _bi("k8s 1.30 release note 通读 — 两个 break change 记下。",
                    "Read k8s 1.30 release notes; two breaking changes flagged."),
            ],
            "work_pattern": [
                _bi("活跃 4.8h,跨两条主线。",
                    "Active 4.8h spanning two threads."),
                _bi("上下文切换 13 次,午后切换更明显。",
                    "13 context switches, mostly clustered in the afternoon pivot."),
            ],
            "suggestions": [
                _bi("迁移 staging 集群到 k8s 1.30:把 break change 写进 runbook。",
                    "Migrate staging cluster to k8s 1.30: write the breaking changes into the runbook."),
            ],
        },
        "2026-05-14": {
            "headline": _bi("Infra 日,kubectl + terraform 全开",
                             "Infra day — kubectl + terraform all day"),
            "narrative": _bi(
                "周四是 infra-cleanup 主场。上午把 staging 集群升级到 1.30 的 Helm chart 改完,"
                "用 dry-run 验了一轮没问题;午后跑 terraform plan 扫了一遍未使用的 IAM 角色,"
                "看到 4 个可以安全 drop 的候选。runbook 更新了 rollback 段落。"
                "paper 那边只是顺手把 Codex 回复又润了一句话;daytrace 静默。",
                "Thursday was infra-cleanup all day. Morning: finished the Helm chart bump for "
                "staging cluster 1.30 and ran a dry-run pass (clean). Afternoon: terraform plan "
                "swept the unused IAM roles and surfaced 4 safe-to-drop candidates. The runbook "
                "got a fresh rollback paragraph. One quick Codex polish on the paper reply; "
                "daytrace stayed silent.",
            ),
            "trend": ("rising", _bi("infra 投入显著上升。",
                                     "Infra investment clearly up.")),
            "highlights": [
                _bi("迁移 staging 集群到 k8s 1.30:Helm chart 改完,dry-run 通过。",
                    "Migrate staging cluster to k8s 1.30: Helm chart updated, dry-run green."),
                _bi("Terraform: 清理未使用 IAM 角色:plan 列出 4 个候选。",
                    "Terraform: drop unused IAM roles: plan surfaced 4 candidates."),
                _bi("Runbook 加 rollback 段落。",
                    "Runbook updated with a rollback paragraph."),
            ],
            "work_pattern": [
                _bi("活跃 5.0h,infra 占 70%。",
                    "Active 5.0h, infra-cleanup ~70%."),
                _bi("最长专注块 71 分钟(terraform plan)。",
                    "Longest focus block 71 min (terraform plan)."),
            ],
            "suggestions": [
                _bi("Terraform: 清理未使用 IAM 角色:apply 前再核一次依赖图。",
                    "Terraform: drop unused IAM roles: double-check dep graph before terraform apply."),
            ],
        },
        "2026-05-15": {
            "headline": _bi("Friday daytrace day — 开源准备起步",
                             "Friday daytrace day — open-source prep kicks off"),
            "narrative": _bi(
                "周五一整天切回 daytrace 这条 meta 主线。"
                "上午翻了一遍仓库结构,想清楚开源前要砍哪些内部 ref;"
                "午后跟 Claude Code 讨论了 demo 数据生成器要怎么设计才能既不漏 PII 又看起来真;"
                "傍晚动手起了 build_demo.py 的骨架,先把 work_items 和 events 的种子写进去。"
                "paper 周五截稿前还差敏感性扫描的最后一组数字,没赶上,周末补。",
                "Friday went fully into the daytrace meta-thread. Morning: walked the repo "
                "and scoped which internal references to scrub before open-sourcing. After "
                "lunch, talked through the demo-data generator design with Claude Code — how "
                "to avoid PII leaks while staying plausible. Evening: started the build_demo.py "
                "scaffold, seeding work_items and events first. The paper Friday deadline "
                "slipped on one final sensitivity-sweep table — pushed to the weekend.",
            ),
            "trend": ("new", _bi("从 infra 切到 daytrace meta 主线。",
                                  "Pivoted from infra over to the daytrace meta-thread.")),
            "highlights": [
                _bi("开源就绪性审查:仓库结构走查完成。",
                    "Open-source readiness review: repo walkthrough done."),
                _bi("为文档站点生成 demo 数据:build_demo.py 骨架起步。",
                    "Demo data generator for docs site: build_demo.py scaffold started."),
            ],
            "work_pattern": [
                _bi("活跃 5.1h,daytrace 占 75%。",
                    "Active 5.1h, daytrace ~75%."),
                _bi("最长专注块 82 分钟(写 build_demo)。",
                    "Longest focus block 82 min (writing build_demo)."),
            ],
            "suggestions": [
                _bi("回复审稿人2意见:周末补完最后一组敏感性数字。",
                    "Reply to reviewer 2: finish the last sensitivity numbers over the weekend."),
            ],
        },
        "2026-05-16": {
            "headline": _bi("周六轻量 — daytrace 收尾几条小路径",
                             "Quiet Saturday — a few small daytrace touch-ups"),
            "narrative": _bi(
                "周六状态偏轻,午后断断续续敲了几下 build_demo.py,"
                "把 AI channel 的 payload schema 走了一遍,确认 validate_overview 不会拦下假数据;"
                "顺便把 paper 的 Reviewer-2 回复又看了一眼,没有大改。",
                "A lighter Saturday. Afternoon brought a few intermittent passes on "
                "build_demo.py — walked the AI-channel payload schema and confirmed "
                "validate_overview won't reject the synthetic data. Glanced at the paper's "
                "Reviewer-2 reply again; nothing major to change.",
            ),
            "trend": ("steady", _bi("周末轻量节奏。", "Quiet weekend pace.")),
            "highlights": [
                _bi("为文档站点生成 demo 数据:验证 AI channel schema。",
                    "Demo data generator: AI-channel schema validation pass."),
            ],
            "work_pattern": [
                _bi("活跃 1.6h,均为零散小块。",
                    "Active 1.6h, scattered short blocks."),
            ],
            "suggestions": [],
        },
        "2026-05-17": {
            "headline": _bi("周日 — 把假数据填满 7 天",
                             "Sunday — backfill the fake-data window to 7 days"),
            "narrative": _bi(
                "周日还是 daytrace。下午一阵手快,把 build_demo.py 的 7 天事件生成填了进去,"
                "跑了一次本地预览,/today 和 /weekly 都能渲染出像样的页面,"
                "AI 文案还是空的,准备下周补。paper 那边敏感性扫描数字已经跑完,但 reply 没发出去 —— 周一再说。",
                "Sunday stayed on daytrace. An afternoon burst pushed the 7-day fake-events "
                "into build_demo.py and ran a local preview — both /today and /weekly now "
                "render real-looking pages. AI narratives are still empty, queued for next week. "
                "On the paper side, the sensitivity-sweep numbers finished running but the "
                "reply isn't out yet — Monday's task.",
            ),
            "trend": ("rising", _bi("周末轻投入但 daytrace 推进明显。",
                                     "Weekend stayed light but daytrace made visible progress.")),
            "highlights": [
                _bi("为文档站点生成 demo 数据:7 天事件跑通。",
                    "Demo data generator: 7-day events pipeline working end-to-end."),
            ],
            "work_pattern": [
                _bi("活跃 2.0h,集中在午后。",
                    "Active 2.0h, concentrated in the afternoon."),
            ],
            "suggestions": [
                _bi("回复审稿人2意见:周一一早把回复发出去。",
                    "Reply to reviewer 2: send the reply first thing Monday."),
            ],
        },
    }
    if date in PER_DAY:
        p = PER_DAY[date]
        return {
            "headline": p["headline"],
            "overview": {"narrative": p["narrative"]},
            "trend": {"direction": p["trend"][0], "comparison": p["trend"][1]},
            "highlights": p["highlights"],
            "work_pattern": p["work_pattern"],
            "suggestions": p["suggestions"],
        }
    # Fallback for any unexpected date
    return {
        "headline": _bi(f"{date} 当日工作", f"Work on {date}"),
        "overview": {
            "narrative": _bi(
                "当天投入较轻,以代码迭代和阅读为主,三条主线均有零星推进。",
                "A lighter day — mostly code iteration and reading, with small touches on each of the three threads.",
            ),
        },
        "trend": {
            "direction": "steady",
            "comparison": _bi("节奏与近 7 天平均接近。", "Pace close to the 7-day average."),
        },
        "highlights": [],
        "work_pattern": [],
        "suggestions": [],
    }


def fake_continuity_payload() -> dict:
    return {
        "relation_to_yesterday":
            "Picks up infra-cleanup from yesterday's dry-run, closes the IAM cleanup, "
            "and pushes paper-revision further than yesterday's re-read.",
        "momentum": "rising",
        "notable_changes": [
            "Longer deep-focus blocks compared to yesterday.",
            "Daytrace work shifted from design to implementation.",
        ],
    }


def fake_project_summary_batch(date: str) -> dict:
    if date == "2026-05-19":
        return {
            "by_project": {
                "paper-revision": {
                    "summary": _bi(
                        "上午对 §4 实验设置的措辞做了一轮收紧,顺带把审稿人2 的鲁棒性回复在 Overleaf 里草拟成型。",
                        "Tightened §4 experimental-setup wording in the morning and drafted the Reviewer-2 robustness rebuttal in Overleaf.",
                    ),
                    "what_was_done": [
                        _bi("Codex 对齐 §3/§4 记号", "Aligned §3/§4 notation via Codex"),
                        _bi("Overleaf 里草拟 Reviewer-2 回复", "Drafted Reviewer-2 reply in Overleaf"),
                        _bi("更新实验表的 baseline 列", "Refreshed the baseline column in the experiments table"),
                    ],
                    "status": "进行中",
                    "next_steps": [
                        _bi("补敏感性扫描数字 (周五前)", "Fill in sensitivity-sweep numbers before Friday"),
                        _bi("把回复发回审稿系统", "Submit the rebuttal back through the review system"),
                    ],
                },
                "daytrace": {
                    "summary": _bi(
                        "demo 数据生成器骨架成形,7 天假事件已经能驱动 /today 和 /weekly 渲染。",
                        "Demo-data generator scaffold is in place; 7 days of fake events now drive both /today and /weekly rendering.",
                    ),
                    "what_was_done": [
                        _bi("build_demo.py 骨架完成", "build_demo.py scaffold done"),
                        _bi("Claude Code 审了 AI channel payload 形状", "Claude Code reviewed AI-channel payload shapes"),
                    ],
                    "status": "进行中",
                    "next_steps": [
                        _bi("把生成的 HTML 写进 docs/demo/", "Write rendered HTML into docs/demo/"),
                        _bi("起 docs/demo/README.md", "Draft docs/demo/README.md"),
                    ],
                },
                "infra-cleanup": {
                    "summary": _bi(
                        "傍晚把 4 个无人引用的 IAM 角色清掉,staging 1.30 升级的 runbook 也补了 rollback 步骤。",
                        "Evening pass: dropped four orphan IAM roles and added a rollback step to the staging 1.30 upgrade runbook.",
                    ),
                    "what_was_done": [
                        _bi("Terraform plan 通过", "Terraform plan came back clean"),
                        _bi("Runbook 加上 rollback 步骤", "Runbook now has the rollback step"),
                    ],
                    "status": "进行中",
                    "next_steps": [
                        _bi("CRD 迁移步骤写进 runbook", "Write CRD migration steps into the runbook"),
                    ],
                },
                "misc": {
                    "summary": _bi("零散收件与 Slack 翻看,无明确主题。",
                                   "Inbox + Slack triage, no clear thread."),
                    "what_was_done": [],
                    "status": "unknown",
                    "next_steps": [],
                },
            }
        }
    if date == "2026-05-18":
        return {
            "by_project": {
                "paper-revision": {
                    "summary": _bi("把 §4 重读了一遍,定位到两段要改。",
                                    "Re-read §4; flagged two paragraphs needing rewrites."),
                    "what_was_done": [_bi("通读 §4", "Read through §4")],
                    "status": "进行中",
                    "next_steps": [_bi("起 Reviewer-2 回复骨架", "Sketch the Reviewer-2 rebuttal")],
                },
                "daytrace": {
                    "summary": _bi("讨论了 demo 数据生成器的形状。",
                                    "Scoped the demo-data generator."),
                    "what_was_done": [_bi("草拟生成器流程", "Drafted generator flow")],
                    "status": "进行中",
                    "next_steps": [_bi("开 build_demo.py 骨架", "Open build_demo.py scaffold")],
                },
                "infra-cleanup": {
                    "summary": _bi("跑了一轮 staging 1.30 的 dry-run,terraform 扫了 4 个 dead IAM 角色。",
                                    "Dry-ran the staging 1.30 upgrade; terraform surfaced 4 dead IAM roles."),
                    "what_was_done": [
                        _bi("Dry-run 通过", "Dry-run green"),
                        _bi("Terraform plan 输出候选列表", "Terraform plan listed candidates"),
                    ],
                    "status": "进行中",
                    "next_steps": [_bi("决定哪些角色 apply 时 drop", "Decide which roles to drop on apply")],
                },
                "misc": {
                    "summary": _bi("零散查邮件。", "Inbox triage."),
                    "what_was_done": [],
                    "status": "unknown",
                    "next_steps": [],
                },
            }
        }
    # W20 days — lighter but still bilingual + schema-valid.
    W20_BATCHES = {
        "2026-05-11": {
            "paper-revision": (
                _bi("§4 实验段开始改写,记号统一基本完成,Reviewer-2 回复起骨架。",
                    "Started rewriting the §4 experiments section; notation alignment mostly done; Reviewer-2 reply skeleton drafted."),
                [_bi("§3/§4 记号对齐", "Notation aligned across §3/§4"),
                 _bi("Reviewer-2 回复骨架", "Reviewer-2 reply skeleton")],
                "进行中",
                [_bi("把鲁棒性段落填进回复", "Flesh out the robustness paragraph in the reply")],
            ),
            "infra-cleanup": (
                _bi("只在午休时 kubectl 看了一眼 staging 集群。",
                    "Just a lunchtime kubectl glance at the staging cluster."),
                [], "进行中", [],
            ),
        },
        "2026-05-12": {
            "paper-revision": (
                _bi("§4 baseline 列重算,fig.7/fig.9 用 Claude Code 再生成,种子核对一致。",
                    "Recomputed the §4 baseline column; Claude Code regenerated fig.7/fig.9 with matching seeds."),
                [_bi("baseline 列重算", "Baseline column recomputed"),
                 _bi("Claude Code 再生成图", "Claude Code regenerated figures"),
                 _bi("敏感性扫描段写入回复", "Sensitivity-sweep paragraph drafted into reply")],
                "进行中",
                [_bi("跑出敏感性数字", "Run the sensitivity numbers")],
            ),
            "infra-cleanup": (
                _bi("kubectl history 翻一下,无新工单。",
                    "Skimmed kubectl history; no new tickets."),
                [], "进行中", [],
            ),
        },
        "2026-05-13": {
            "paper-revision": (
                _bi("§4 通读完成;Reviewer-2 回复定稿七成,敏感性数字回填。",
                    "§4 final read-through done; Reviewer-2 reply ~70% locked; sensitivity numbers backfilled."),
                [_bi("§4 通读", "§4 read-through"),
                 _bi("敏感性数字回填", "Sensitivity numbers backfilled")],
                "进行中",
                [_bi("剩 30% 回复定稿", "Lock the final 30% of the reply")],
            ),
            "infra-cleanup": (
                _bi("通读 k8s 1.30 release note,记下两个 PodSecurity break change。",
                    "Read the k8s 1.30 release notes; flagged two PodSecurity breaking changes."),
                [_bi("release note 通读", "Release notes read")],
                "进行中",
                [_bi("把 break change 写进 runbook", "Write breaking changes into the runbook")],
            ),
        },
        "2026-05-14": {
            "infra-cleanup": (
                _bi("Helm chart 升 1.30 完成,dry-run 通过;terraform 扫出 4 个可清理的 IAM 角色;runbook 加 rollback 段落。",
                    "Helm chart bumped to 1.30 with a clean dry-run; terraform plan surfaced 4 prune-able IAM roles; runbook gained a rollback paragraph."),
                [_bi("Helm chart 升 1.30", "Helm chart bumped to 1.30"),
                 _bi("dry-run 通过", "Dry-run green"),
                 _bi("terraform plan 列 4 个候选", "terraform plan listed 4 candidates"),
                 _bi("Runbook 加 rollback", "Runbook rollback paragraph added")],
                "进行中",
                [_bi("apply 前核依赖图", "Verify dep graph before apply")],
            ),
            "paper-revision": (
                _bi("Codex 又润了一句话,没大改。",
                    "Polished one more sentence via Codex; no major edits."),
                [], "进行中", [],
            ),
        },
        "2026-05-15": {
            "daytrace": (
                _bi("仓库走查完成,起 build_demo.py 骨架,work_items + events 种子写入。",
                    "Repo walkthrough done; build_demo.py scaffold started; work_items + events seeded."),
                [_bi("仓库走查", "Repo walkthrough"),
                 _bi("build_demo.py 骨架", "build_demo.py scaffold"),
                 _bi("写入 work_items 种子", "Seeded work_items")],
                "进行中",
                [_bi("AI channel payload 接进去", "Wire up the AI-channel payloads")],
            ),
            "paper-revision": (
                _bi("Friday 截稿差最后一组敏感性数字,延到周末。",
                    "Friday deadline slipped on the last sensitivity numbers; pushed to the weekend."),
                [], "进行中",
                [_bi("周末补敏感性数字", "Finish sensitivity numbers over the weekend")],
            ),
        },
    }
    if date in W20_BATCHES:
        by = {}
        for proj, (summary, what, status, nxt) in W20_BATCHES[date].items():
            by[proj] = {
                "summary": summary,
                "what_was_done": what,
                "status": status,
                "next_steps": nxt,
            }
        by["misc"] = {
            "summary": _bi("零散收件与 Slack 翻看,无明确主题。",
                           "Inbox + Slack triage, no clear thread."),
            "what_was_done": [],
            "status": "unknown",
            "next_steps": [],
        }
        return {"by_project": by}
    return {"by_project": {}}


def fake_project_continuity_batch(date: str) -> dict:
    if date == "2026-05-19":
        return {
            "by_project": {
                "paper-revision": {
                    "relation_to_previous": _bi(
                        "从昨天的『重读 §4』推进到『改写措辞 + 回复审稿』。",
                        "Moved from yesterday's §4 re-read into rewriting wording and drafting the rebuttal.",
                    ),
                    "momentum": "rising",
                },
                "daytrace": {
                    "relation_to_previous": _bi(
                        "从昨天的设计讨论推进到 build_demo.py 骨架成型。",
                        "Moved from yesterday's scoping into a working build_demo.py scaffold.",
                    ),
                    "momentum": "rising",
                },
                "infra-cleanup": {
                    "relation_to_previous": _bi(
                        "昨天 dry-run 通过,今天把 IAM 清理收尾。",
                        "Yesterday's dry-run cleared; today the IAM pass is closed out.",
                    ),
                    "momentum": "steady",
                },
                "misc": {
                    "relation_to_previous": _bi("和昨天一样的零散投入。",
                                                 "Same scattered touches as yesterday."),
                    "momentum": "steady",
                },
            }
        }
    # W20 days — short bilingual continuity blurbs.
    W20_CONT = {
        "2026-05-11": {
            "paper-revision": (_bi("从周末的轻翻阅切到正式改写。",
                                    "From weekend skim into active rewriting."), "rising"),
            "infra-cleanup": (_bi("和上周差不多,仅顺手一瞥。",
                                   "Similar to last week — only a glance."), "steady"),
        },
        "2026-05-12": {
            "paper-revision": (_bi("延续昨天的改写,补图表 + 敏感性段。",
                                    "Continuing yesterday's rewrite; figures + sensitivity paragraph added."), "steady"),
            "infra-cleanup": (_bi("没有变化。", "No change."), "steady"),
        },
        "2026-05-13": {
            "paper-revision": (_bi("昨天的改写推进到通读 + 数字回填。",
                                    "Yesterday's rewrite advanced into read-through + numbers."), "steady"),
            "infra-cleanup": (_bi("从顺手一瞥升级到正式调研。",
                                   "Upgraded from a glance to deliberate reading."), "rising"),
        },
        "2026-05-14": {
            "infra-cleanup": (_bi("昨天调研的 break change 今天落到 Helm chart + runbook。",
                                   "Yesterday's reading materialized into Helm chart + runbook updates today."), "rising"),
            "paper-revision": (_bi("和昨天比明显降温,仅微调。",
                                    "Visibly cooled vs yesterday; only a tiny polish."), "falling"),
        },
        "2026-05-15": {
            "daytrace": (_bi("从沉寂状态切回主线,build_demo 骨架成型。",
                              "From dormant back to the main thread — build_demo scaffold takes shape."), "rising"),
            "paper-revision": (_bi("周五截稿压力下,差最后一组数字。",
                                    "Under Friday-deadline pressure; one last batch of numbers outstanding."), "steady"),
        },
    }
    if date in W20_CONT:
        by = {}
        for proj, (rel, mom) in W20_CONT[date].items():
            by[proj] = {"relation_to_previous": rel, "momentum": mom}
        by["misc"] = {
            "relation_to_previous": _bi("和昨天一样的零散投入。",
                                         "Same scattered touches as yesterday."),
            "momentum": "steady",
        }
        return {"by_project": by}
    return {"by_project": {}}


def insert_fake_ai_channels(con):
    from daytrace.ai_report import (
        AI_VERSION,
        validate_overview,
        validate_continuity,
        validate_project_summary_batch,
        validate_project_continuity_batch,
    )

    now = datetime(2026, 5, 19, 23, 30, 0).isoformat(timespec="seconds")

    def write_day(date: str, channel: str, value: dict):
        con.execute(
            """
            INSERT INTO day_channel
              (date, channel, value_json, generator, generator_version,
               source_hash, generated_at, tokens_in, tokens_out, cost_usd, error)
            VALUES (?, ?, ?, 'ai', ?, 'demo', ?, 1200, 800, 0.0, NULL)
            ON CONFLICT(date, channel) DO UPDATE SET
              value_json=excluded.value_json,
              generator=excluded.generator,
              generator_version=excluded.generator_version,
              source_hash=excluded.source_hash,
              generated_at=excluded.generated_at,
              tokens_in=excluded.tokens_in,
              tokens_out=excluded.tokens_out,
              cost_usd=excluded.cost_usd,
              error=NULL
            """,
            (date, channel, json.dumps(value, ensure_ascii=False), AI_VERSION, now),
        )

    def write_project(date: str, project: str, channel: str, value):
        con.execute(
            """
            INSERT INTO day_project_channel
              (date, project, channel, value_json, generator, generator_version,
               source_hash, generated_at, tokens_in, tokens_out, cost_usd, error)
            VALUES (?, ?, ?, ?, 'ai', ?, 'demo', ?, 0, 0, 0.0, NULL)
            ON CONFLICT(date, project, channel) DO UPDATE SET
              value_json=excluded.value_json,
              generator=excluded.generator,
              generator_version=excluded.generator_version,
              source_hash=excluded.source_hash,
              generated_at=excluded.generated_at,
              tokens_in=excluded.tokens_in,
              tokens_out=excluded.tokens_out,
              cost_usd=excluded.cost_usd,
              error=NULL
            """,
            (date, project, channel, json.dumps(value, ensure_ascii=False),
             AI_VERSION, now),
        )

    for d in DAYS:
        ov = validate_overview(fake_overview_payload(d))
        write_day(d, "ai_overview", ov)
        if d > DAYS[0]:  # continuity needs a previous day
            cont = validate_continuity(fake_continuity_payload())
            write_day(d, "ai_continuity_day", cont)
        if d in ACTIVE_DAYS:
            batch = validate_project_summary_batch(fake_project_summary_batch(d))
            write_day(d, "ai_project_summary_batch", batch)
            cont_batch = validate_project_continuity_batch(fake_project_continuity_batch(d))
            write_day(d, "ai_project_continuity_batch", cont_batch)
            # Slice the batch into per-project rows
            for proj, body in batch["by_project"].items():
                write_project(d, proj, "ai_summary", body)
            for proj, body in cont_batch["by_project"].items():
                write_project(d, proj, "ai_continuity", body)
    con.commit()


# ───────────────────────── server + fetch ─────────────────────────


def _wait_for_port(port: int, timeout: float = 20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.connect(("127.0.0.1", port))
                return
            except OSError:
                time.sleep(0.25)
    raise RuntimeError(f"server didn't open port {port} within {timeout}s")


def _fetch(url: str, lang: str = "zh") -> str:
    # The dashboard reads the daytrace_lang cookie (see _lang_from_request in
    # dashboard/server.py). Set it to control language.
    req = urllib.request.Request(
        url,
        headers={
            "Accept-Language": "zh-CN" if lang == "zh" else "en-US",
            "Cookie": f"daytrace_lang={lang}",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


# ───────────────────────── HTML post-processing ─────────────────────────

# Map of in-page routes that should resolve to a peer file in docs/demo/.
# Per-language filename suffixes: zh → "", en → ".en"
_NAV_PAGES = {"/today": "index", "/weekly": "weekly"}


def _rewrite_href(href: str, lang: str) -> str:
    """Rewrite a server-rooted href ('/today?date=…') for static hosting.

    Demo defaults to English (international audience on GH Pages);
    Chinese gets the `.zh` suffix.

    /today  → index.html         (or index.zh.html for the zh variant)
    /weekly → weekly.html        (or weekly.zh.html for the zh variant)
    everything else server-rooted → '#' (disabled)
    """
    if not href.startswith("/"):
        return href
    path, _, _query = href.partition("?")
    if path in _NAV_PAGES:
        suffix = ".zh" if lang == "zh" else ""
        return f"{_NAV_PAGES[path]}{suffix}.html"
    return "#"


_HREF_RE = re.compile(r'href="(/[^"]*)"')
_ACTION_RE = re.compile(r'action="(/[^"]*)"')
_BODY_RE = re.compile(r"(<body[^>]*>)", re.IGNORECASE)


# Rewrite the dashboard's native 中/EN language pills so the inactive one
# becomes a real link to the other-language static page. The dashboard's
# click handler bails out when href != '#' (see server.py), letting the
# browser navigate. This removes the need for a separate language banner.
_LANG_OPT_RE = re.compile(
    r'<a class="lang-opt"\s+href="#"\s+data-lang="(?P<lang>[a-z]+)">'
)


def _rewrite_lang_pills(html: str, page: str) -> str:
    # English is the default static page (no suffix); Chinese gets `.zh`.
    other = {"en": "index.html"    if page == "today" else "weekly.html",
             "zh": "index.zh.html" if page == "today" else "weekly.zh.html"}
    def repl(m: re.Match) -> str:
        target_lang = m.group("lang")
        href = other.get(target_lang, "#")
        return f'<a class="lang-opt" href="{href}">'
    return _LANG_OPT_RE.sub(repl, html)


BRAND_STRIP = (
    '<div style="background:#0b1220;padding:8px 16px;'
    'display:flex;align-items:center;gap:10px;'
    'font-family:ui-monospace,SF Mono,Menlo,Consolas,monospace;'
    'font-size:12px;color:#94a3b8;border-bottom:1px solid #1f2937">'
    '<a href="../" style="display:inline-flex;align-items:center;gap:8px;'
    'text-decoration:none;color:#e2e8f0">'
    '<img src="../assets/logo.svg" width="18" height="18" alt="">'
    '<strong style="color:#e2e8f0">DayTrace</strong></a>'
    '<span style="color:#475569">·</span>'
    '<span>demo · this is fake data, generated by '
    '<a href="https://github.com/xingminw/daytrace" '
    'style="color:#7aa2f7;text-decoration:none">scripts/build_demo.py</a>'
    '</span></div>'
)

_HEAD_RE = re.compile(r"(<head[^>]*>)", re.IGNORECASE)


def scrub_html(html: str, lang: str, page: str) -> str:
    html = _HREF_RE.sub(lambda m: f'href="{_rewrite_href(m.group(1), lang)}"', html)
    html = _ACTION_RE.sub(lambda m: f'action="{_rewrite_href(m.group(1), lang)}"', html)
    # Make the dashboard's native 中/EN pills navigate to the other static
    # page (instead of relying on cookies + reload, which is a no-op on
    # GH Pages). Server JS lets a real href fall through.
    html = _rewrite_lang_pills(html, page)
    # Brand: dashboard already bakes a favicon (data URI) into its <head>;
    # we only inject the thin DayTrace demo strip above the dashboard
    # header so the demo never gets mistaken for the real thing.
    html = _BODY_RE.sub(lambda m: m.group(1) + BRAND_STRIP, html, count=1)
    return html


def assert_no_pii(html: str, label: str):
    # `xingminw` (the public GitHub handle) is intentionally allowed because
    # the brand strip links to https://github.com/xingminw/daytrace. The
    # needles below catch the things that are actually private: the unix
    # username's home path, internal tokens, and the author's institutional
    # email.
    needles = ["Xingmin Wang", "xingminwang", "/Users/", "F9p4bjm",
               "tail24bb1", "umich"]
    for n in needles:
        if n in html:
            raise RuntimeError(f"PII leak in {label}: found {n!r}")


# ───────────────────────── main ─────────────────────────


def main():
    print(f"[demo] Building {DB_PATH}")
    if DB_PATH.exists():
        DB_PATH.unlink()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    con = connect(DB_PATH)
    init_db(con)
    insert_work_items(con)
    insert_events_for_all_days(con)
    insert_activity_labels(con)
    insert_event_work_item_links(con)

    # Deterministic stats channels per day. include_ai=False so we don't
    # call DeepSeek; we'll inject AI payloads manually right after.
    for d in DAYS:
        regenerate_day_from_db(con, d, include_ai=False, force=True)
    insert_fake_ai_channels(con)
    con.close()

    # Pre-populate the weekly AI cache so /weekly doesn't try (and fail)
    # to call DeepSeek even when DEEPSEEK_API_KEY happens to be set.
    weekly_cache_dir = REPO_ROOT / "data" / "week_ai_cache"
    weekly_cache_dir.mkdir(parents=True, exist_ok=True)
    weekly_cache_path = weekly_cache_dir / f"{WEEK}.json"
    weekly_value = {
        "headline": _bi(
            "三条主线轮转 — paper → infra → daytrace",
            "Three-thread rotation — paper → infra → daytrace",
        ),
        "overview": {
            "narrative": _bi(
                "本周(05-11 一 → 05-17 日)节奏走了一个明显的轮转:周一到周三全在 paper-revision 上,"
                "§4 实验段从开切到通读、Reviewer-2 回复从骨架推到七成定稿、敏感性数字也填了进去;"
                "周四一整天切到 infra-cleanup,Helm chart 升 1.30 的 dry-run 通过,terraform plan 扫出 4 个可清理的 IAM 角色;"
                "周五开始转向 daytrace 这条 meta 主线,起了 build_demo.py 的骨架,周末以轻量节奏继续推。"
                "三条主线都各自往前挪了一大步,但都没有彻底结案;Friday 截稿在 paper 那条线上没赶上,延到下周一补。",
                "This week (05-11 Mon → 05-17 Sun) ran a clear three-thread rotation. Mon–Wed lived inside "
                "paper-revision: §4 went from open-and-cut to a full read-through, the Reviewer-2 reply moved "
                "from skeleton to ~70% locked, and the sensitivity numbers landed. Thursday pivoted fully into "
                "infra-cleanup — staging Helm chart bumped to 1.30 with a clean dry-run, terraform plan "
                "surfaced 4 prune-able IAM roles. Friday turned over to the daytrace meta-thread, kicking off "
                "the build_demo.py scaffold; the weekend kept that thread alive at a quieter pace. All three "
                "threads visibly advanced; none closed; the Friday paper deadline slipped one batch of numbers, "
                "pushed to the following Monday.",
            ),
        },
        "trend": {
            "direction": "rising",
            "comparison": _bi(
                "活跃时长比上上周 +1.4 小时,专注块更长,切换次数下降。",
                "Active hours +1.4h vs the prior week, longer deep blocks, fewer context switches.",
            ),
        },
        "highlights": [
            _bi("修订 §4 实验设置 + 回复审稿人2意见:草稿七成定稿,敏感性数字落位。",
                "Revise §4 + Reply to reviewer 2: ~70% locked, sensitivity numbers in place."),
            _bi("迁移 staging 集群到 k8s 1.30:Helm chart 升完 + dry-run 通过。",
                "Migrate staging cluster to k8s 1.30: Helm chart bumped + dry-run green."),
            _bi("Terraform: 清理未使用 IAM 角色:plan 列出 4 个候选,runbook 加 rollback。",
                "Terraform: drop unused IAM roles: plan surfaced 4 candidates, runbook gained a rollback paragraph."),
            _bi("为文档站点生成 demo 数据:build_demo.py 骨架成型,7 天事件跑通。",
                "Demo data generator for docs site: build_demo.py scaffold up, 7-day events pipeline working."),
        ],
        "work_pattern": [
            _bi("活跃 4.0h/天(工作日均值),周末 1.8h/天。",
                "Active 4.0h/day on weekdays, 1.8h/day on weekends."),
            _bi("活跃天数 7/7,无完全空白日。",
                "7/7 active days; no fully idle day."),
            _bi("最长专注块均值 68 分钟,集中在 paper 改写和 build_demo 段。",
                "Mean longest-focus block 68 min, clustered around paper rewriting and build_demo."),
            _bi("主线轮转明确:Mon–Wed paper、Thu infra、Fri+周末 daytrace。",
                "Clear thread rotation: Mon–Wed paper, Thu infra, Fri + weekend daytrace."),
        ],
        "suggestions": [
            _bi("回复审稿人2意见:下周一一早把最后一组数字补完、提交。",
                "Reply to reviewer 2 comments: finish the last sensitivity numbers Monday and submit."),
            _bi("迁移 staging 集群到 k8s 1.30:把 PodSecurity break change 写进 runbook 再 apply。",
                "Migrate staging cluster to k8s 1.30: write the PodSecurity breaking changes into the runbook before apply."),
            _bi("开源就绪性审查:挑 README 这一块先动起来。",
                "Open-source readiness review: start with the README slice."),
        ],
    }
    # Compute events_hash the same way the server does so the cache hits.
    import hashlib
    # Hash must match what the server computes for this week. The server
    # queries events_for_shifted_week(con, WEEK), so we hash event IDs
    # whose date falls within the W20 ISO range (05-11..05-17).
    con2 = connect(DB_PATH)
    week_event_ids = [
        r["id"] for r in con2.execute(
            "SELECT id FROM events WHERE date BETWEEN ? AND ? ORDER BY id",
            ("2026-05-11", "2026-05-17"),
        ).fetchall()
    ]
    con2.close()
    h = hashlib.sha1()
    for eid in sorted(week_event_ids):
        h.update(eid.encode("utf-8"))
    events_hash = h.hexdigest()[:16]
    weekly_cache_path.write_text(
        json.dumps({"events_hash": events_hash, "value": weekly_value,
                    "cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Spawn the dashboard server pointed at the demo DB.
    env = dict(os.environ)
    # Force the AI weekly summary to use our pre-seeded cache file rather than
    # calling DeepSeek. ai_client.is_available() must return True for the
    # cache lookup to even be reached, so we set a placeholder key (never
    # actually used because the events_hash in our cache matches).
    env["DEEPSEEK_API_KEY"] = "demo-placeholder-not-a-real-key"
    env["DAYTRACE_DAY_BOUNDARY_HOUR"] = "4"
    server_proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "dashboard" / "server.py"),
         "--db", str(DB_PATH), "--port", str(PORT)],
        env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    captures: dict[tuple[str, str], str] = {}
    try:
        _wait_for_port(PORT)
        for lang in ("zh", "en"):
            print(f"[demo] Fetching /today?date={TODAY} ({lang})")
            captures[("today", lang)] = _fetch(
                f"http://127.0.0.1:{PORT}/today?date={TODAY}", lang=lang,
            )
            print(f"[demo] Fetching /weekly?week={WEEK} ({lang})")
            captures[("weekly", lang)] = _fetch(
                f"http://127.0.0.1:{PORT}/weekly?week={WEEK}", lang=lang,
            )
    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()

    # English is the GH-Pages default (international audience); Chinese
    # variants live at *.zh.html.
    outputs = {
        ("today", "en"):  OUT_DIR / "index.html",
        ("today", "zh"):  OUT_DIR / "index.zh.html",
        ("weekly", "en"): OUT_DIR / "weekly.html",
        ("weekly", "zh"): OUT_DIR / "weekly.zh.html",
    }
    for (page, lang), raw in captures.items():
        html = scrub_html(raw, lang=lang, page=page)
        assert_no_pii(html, outputs[(page, lang)].name)
        outputs[(page, lang)].write_text(html, encoding="utf-8")
        print(f"[demo] Wrote {outputs[(page, lang)]} ({len(html):,} chars)")

    readme = OUT_DIR / "README.md"
    readme.write_text(
        "# DayTrace dashboard demo\n\n"
        "This folder is a **static snapshot** of the DayTrace dashboard\n"
        "rendered against synthetic data — no real activity is shown.\n\n"
        "Regenerate with `python scripts/build_demo.py`.\n\n"
        "Pages (English is the default; switch language via the 中/EN pills in the header):\n"
        "- Daily report — [English](index.html) · [中文](index.zh.html)\n"
        "- Weekly report — [English](weekly.html) · [中文](weekly.zh.html)\n",
        encoding="utf-8",
    )

    print("[demo] Done. Preview locally:")
    print("       python -m http.server 8000 --directory docs")
    print("       open http://localhost:8000/demo/")


if __name__ == "__main__":
    main()
