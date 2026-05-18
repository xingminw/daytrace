"""AI-generated channels for the daily report — real DeepSeek calls.

Four channels, all registered at import time so the orchestrator picks them
up automatically:

  day_channel.ai_overview                — narrative on today's work state
  day_channel.ai_continuity_day          — today vs yesterday (skipped on day 1)
  day_channel.ai_project_summary_batch   — one call → dict {project: summary}
  day_channel.ai_project_continuity_batch— one call → dict {project: vs_prev}

Per-project rows (day_project_channel.ai_summary / ai_continuity) are
slice-reads of the day-level batch dicts — no extra API spend per project.

All four return `ChannelResult(value, tokens_in, tokens_out, cost_usd)` so
the orchestrator writes accurate cost/usage to the channel rows. JSON-mode
is requested at the API level; the prompt also pins the exact output shape
so DeepSeek's JSON mode has something to validate against.

Sensitivity redaction: events tagged `sensitive` are dropped from prompts
entirely; events tagged `private` are kept with title/summary replaced by
"[私密]" so AI still sees the temporal envelope.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import ai_client
from .channels import (
    ChannelContext,
    ChannelResult,
    ChannelSpec,
    register_day_channel,
    register_project_channel,
)

# Bump this when prompts change so existing cached rows get superseded.
AI_VERSION = "v9"  # v9 = task-context prompts ([task:X] / [proj:Y] prefixes + active-task list)


# ----- Shape validators ------------------------------------------------
# Each validator inspects the LLM's parsed JSON and either returns a
# normalized payload or raises ShapeError(reason). The corrective retry
# uses `reason` to nudge the model.

def _require_str(d: dict, key: str, *, max_len: int | None = None) -> str:
    if key not in d or not isinstance(d[key], str):
        from .ai_client import ShapeError
        raise ShapeError(f"missing or non-string '{key}'")
    val = d[key].strip()
    if max_len is not None and len(val) > max_len:
        val = val[:max_len]
    return val


def _require_list_of_str(d: dict, key: str, *, default_empty: bool = False) -> list[str]:
    from .ai_client import ShapeError
    if key not in d:
        if default_empty:
            return []
        raise ShapeError(f"missing '{key}' (expected array of strings)")
    val = d[key]
    if not isinstance(val, list):
        raise ShapeError(f"'{key}' must be an array, got {type(val).__name__}")
    out = []
    for i, item in enumerate(val):
        if not isinstance(item, str):
            raise ShapeError(f"'{key}[{i}]' must be a string, got {type(item).__name__}")
        out.append(item.strip())
    return out


_TREND_DIRECTIONS = {"rising", "steady", "dropping", "new", "paused", "blocked"}


def validate_overview(payload):
    """v8 schema (person-focused; 3-column Insights row):
        {
          "headline":   str ≤30,
          "overview":   {"narrative": str, "key_moves": [str, ...]},
          "trend":      {"direction": one_of, "comparison": str},  # → 变化趋势
          "highlights": [str, ...],                                 # → 关键进展
          "suggestions":[str, ...]                                  # → 建议
        }

    Backward compat:
      - v6 top-level `narrative` string is moved into `overview.narrative`.
      - v7 `recommendations` is renamed to `suggestions`.
      - v7 `concerns` is dropped (the model is now told to fold warnings
        into suggestions; cached v7 concerns are merged if suggestions is
        empty so we don't lose existing data on a re-render).
    The renderer hides any empty section."""
    from .ai_client import ShapeError
    if not isinstance(payload, dict):
        raise ShapeError(f"top-level must be an object, got {type(payload).__name__}")

    headline = _require_str(payload, "headline")

    raw_overview = payload.get("overview")
    if isinstance(raw_overview, dict):
        narrative = _require_str(raw_overview, "narrative")
        key_moves = _require_list_of_str(raw_overview, "key_moves", default_empty=True)
    else:
        if isinstance(payload.get("narrative"), str):
            narrative = payload["narrative"].strip()
        else:
            raise ShapeError("missing 'overview' (or legacy 'narrative')")
        key_moves = []
    overview_obj = {"narrative": narrative, "key_moves": key_moves}

    raw_trend = payload.get("trend")
    if isinstance(raw_trend, dict):
        direction = (raw_trend.get("direction") or "steady").strip().lower()
        if direction not in _TREND_DIRECTIONS:
            direction = "steady"
        comparison = (raw_trend.get("comparison") or "").strip()
        trend_obj = {"direction": direction, "comparison": comparison}
    else:
        trend_obj = None

    highlights = _require_list_of_str(payload, "highlights", default_empty=True)

    # suggestions is the v8 name; v7 used `recommendations`.
    if "suggestions" in payload:
        suggestions = _require_list_of_str(payload, "suggestions", default_empty=True)
    else:
        suggestions = _require_list_of_str(payload, "recommendations", default_empty=True)
    # v7 cached `concerns` — fold into suggestions if model didn't return any
    # of its own (lets stale cache stay useful through one render cycle).
    if not suggestions and isinstance(payload.get("concerns"), list):
        suggestions = [str(c).strip() for c in payload["concerns"] if isinstance(c, str) and c.strip()]

    return {
        "headline":    headline,
        "overview":    overview_obj,
        "trend":       trend_obj,
        "highlights":  highlights,
        "suggestions": suggestions,
    }


def validate_continuity(payload):
    from .ai_client import ShapeError
    if not isinstance(payload, dict):
        raise ShapeError(f"top-level must be an object, got {type(payload).__name__}")
    momentum = payload.get("momentum") or ""
    allowed = {"rising", "steady", "dropping", "new", "paused", "blocked"}
    if momentum and momentum not in allowed:
        # Don't reject — just normalize unknown to 'steady' so this never
        # blocks the pipeline.
        momentum = "steady"
    return {
        "relation_to_yesterday": _require_str(payload, "relation_to_yesterday"),
        "momentum": momentum or "steady",
        "notable_changes": _require_list_of_str(payload, "notable_changes", default_empty=True),
    }


def validate_project_summary_batch(payload):
    from .ai_client import ShapeError
    if not isinstance(payload, dict):
        raise ShapeError(f"top-level must be an object, got {type(payload).__name__}")
    by_project = payload.get("by_project")
    if not isinstance(by_project, dict):
        raise ShapeError("missing or wrong type 'by_project' (expected object {<project>: {...}})")
    cleaned: dict = {}
    for proj, body in by_project.items():
        if not isinstance(body, dict):
            raise ShapeError(f"by_project[{proj!r}] must be an object")
        cleaned[proj] = {
            "summary":       _require_str(body, "summary"),
            "what_was_done": _require_list_of_str(body, "what_was_done", default_empty=True),
            "status":        str(body.get("status") or "unknown"),
            "next_steps":    _require_list_of_str(body, "next_steps", default_empty=True),
        }
    return {"by_project": cleaned}


def validate_project_continuity_batch(payload):
    from .ai_client import ShapeError
    if not isinstance(payload, dict):
        raise ShapeError(f"top-level must be an object, got {type(payload).__name__}")
    by_project = payload.get("by_project")
    if not isinstance(by_project, dict):
        raise ShapeError("missing or wrong type 'by_project' (expected object {<project>: {...}})")
    cleaned: dict = {}
    for proj, body in by_project.items():
        if not isinstance(body, dict):
            raise ShapeError(f"by_project[{proj!r}] must be an object")
        cleaned[proj] = {
            "relation_to_previous": body.get("relation_to_previous"),
            "momentum": str(body.get("momentum") or "steady"),
        }
    return {"by_project": cleaned}


# ---- prompt-input preparation -----------------------------------------

def _redact_event(ev: dict[str, Any]) -> dict[str, Any] | None:
    """Sensitivity redaction policy. In v6 this is a pass-through:
    DayTrace is a single-user / single-machine product, and the user's
    DeepSeek API key is their own. Redacting their own private notes from
    their own AI assistant just hides the most informative content.

    The `sensitivity` field is still on each event; if you ever want to
    bring redaction back, this is the single place to switch."""
    return ev


def _format_events_inline(
    events: list[dict[str, Any]],
    summary_cap: int = 120,
    *,
    task_map: dict[str, str] | None = None,
) -> str:
    """One event per line. Prefix priority: `[task:<title>]` if the event
    is linked to a Feishu work_item, otherwise `[proj:<project>]`. The
    task prefix makes the AI talk in terms of *tasks* rather than raw
    project buckets.

    Format: `HH:MM source [task:Foo] title — summary[:cap]`
    """
    lines = []
    task_map = task_map or {}
    for ev in events:
        red = _redact_event(ev)
        if red is None:
            continue
        time = (red.get("start") or "")[11:16]
        src = red.get("source") or "other"
        eid = red.get("id") or ""
        task_title = task_map.get(eid)
        if task_title:
            label = f"[task:{task_title}]"
        else:
            proj = red.get("project") or red.get("project_guess") or "misc"
            label = f"[proj:{proj}]"
        title = (red.get("title") or "").strip()
        summary = (red.get("summary") or "").strip().replace("\n", " ")
        line = f"{time} {src} {label} {title}"
        if summary and summary != title:
            line += f" — {summary[:summary_cap]}"
        lines.append(line)
    return "\n".join(lines)


def _load_event_task_map(con: sqlite3.Connection, date: str) -> dict[str, str]:
    """For all events on `date` that have a row in event_work_item_links,
    return {event_id: task_title}. Used to prefix events with their
    Feishu task label."""
    try:
        rows = con.execute(
            """
            SELECT l.event_id AS eid, w.title AS title
              FROM events e
              JOIN event_work_item_links l ON l.event_id = e.id
              JOIN work_items w           ON w.record_id = l.record_id
             WHERE e.date = ?
             """,
            (date,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {r["eid"]: r["title"] for r in rows if r["title"]}


def _load_active_task_context(con: sqlite3.Connection) -> str:
    """Build a compact bullet list of *active* tasks (status ≠ 完成, OR
    completed within last 7 days). Excludes the `reviews` table — review
    items are auto-identifiable from paper titles and would just bloat
    the prompt. Returns "" when there are no tasks to show."""
    from datetime import date as _date_mod, timedelta as _td_mod
    cutoff = (_date_mod.today() - _td_mod(days=7)).isoformat()
    try:
        rows = con.execute(
            """
            SELECT title, status, due_date
              FROM work_items
             WHERE table_key = 'tasks'
               AND (
                    (status IS NOT NULL AND status != '完成')
                 OR (status = '完成' AND due_date >= ?)
               )
             ORDER BY
               CASE status
                 WHEN '进行中' THEN 0
                 WHEN '待办'   THEN 1
                 WHEN '完成'   THEN 2
                 ELSE 3
               END,
               COALESCE(due_date, '9999-12-31')
            """,
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return ""
    if not rows:
        return ""
    lines = []
    for r in rows:
        title = (r["title"] or "").strip()
        status = (r["status"] or "").strip() or "?"
        due = (r["due_date"] or "").strip()
        suffix = f" · {status}"
        if due:
            suffix += f" · due {due}"
        lines.append(f"- {title}{suffix}")
    return "\n".join(lines)


def _read_day_channel(con: sqlite3.Connection, date: str, channel: str):
    row = con.execute(
        "SELECT value_json FROM day_channel WHERE date = ? AND channel = ?",
        (date, channel),
    ).fetchone()
    if row is None or row["value_json"] is None:
        return None
    try:
        return json.loads(row["value_json"])
    except json.JSONDecodeError:
        return None


def _stats_summary(con: sqlite3.Connection, date: str) -> str:
    """A one-shot text digest of the day-level stats channels, for LLM prompt.

    Reads the already-computed channels (stats run before AI in the topo
    order), so we don't recompute. Falls back to short placeholders when a
    channel hasn't run."""
    am = _read_day_channel(con, date, "active_minutes") or {}
    ts = _read_day_channel(con, date, "time_span") or {}
    lfb = _read_day_channel(con, date, "longest_focus_block") or {}
    cs = _read_day_channel(con, date, "context_switches") or {}
    pw = _read_day_channel(con, date, "peak_windows") or []
    dc = _read_day_channel(con, date, "dimension_counts") or {}
    q = _read_day_channel(con, date, "quality") or {}

    by_source = ", ".join(
        f"{r['name']}({r['count']})" for r in (dc.get("by_source") or [])[:5]
    )
    by_project = ", ".join(
        f"{r['name']}({r['count']})" for r in (dc.get("by_project") or [])[:5]
    )
    peak = ", ".join(f"{p['label']}={p['count']}" for p in pw[:3])
    parts = [
        f"时间跨度 {ts.get('first', '?')}–{ts.get('last', '?')} (span {ts.get('span_min', 0)} min)",
        f"活跃总时长 {am.get('total', 0)} min",
        f"最长不间断块 {lfb.get('duration_min', 0)} min ({lfb.get('start','?')}–{lfb.get('end','?')}, "
        f"主导项目 {lfb.get('dominant_project','?')}, 主导来源 {lfb.get('dominant_source','?')})"
        if lfb else "最长不间断块: 无",
        f"项目切换 {cs.get('count', 0)} 次",
        f"峰值时段 {peak}" if peak else "",
        f"top 来源 {by_source}",
        f"top 项目 {by_project}",
        f"质量: 敏感事件 {q.get('sensitive', 0)} 条, 缺项目归类 {q.get('missing_project', 0)} 条",
    ]
    return "\n".join(p for p in parts if p)


# ---- channel: ai_overview ---------------------------------------------

OVERVIEW_SYSTEM = (
    "你是一位软件工程师的私人工作复盘助手。\n\n"
    "你的读者是这位工程师本人。输入会先给你一份**活跃任务清单**(飞书任务表),"
    "再给事件清单 — 事件已预标 [task:X] 或 [proj:Y] 前缀。\n\n"
    "**优先级**: 任务 > 项目。看到 [task:X] 就**以任务为单位**描述, 例如 "
    "‘今天主要推进了 X 任务, 完成了…’; 看到 [proj:Y] 是没关联任务的游离工作, "
    "可以一笔带过。\n\n"
    "你要写的是: ta 今天**作为一个人**在做什么任务、有什么产出、接下来该怎么走。\n\n"
    "**禁止**:\n"
    "  ❌ 对数据本身提建议 (例: '梳理 misc 类别'、'合并项目名')\n"
    "  ❌ 对系统/工具提建议 (例: '配置 webhook'、'增加分类规则')\n"
    "  ❌ 泛化效率说教 (例: '减少 context switching'、'提升专注度')\n"
    "  ❌ 数字复述 (例: '今天 191 个事件、69 次切换')\n\n"
    "**鼓励**: 具体任务名 / 具体动作 / 具体产出。可以在 suggestions 里指出"
    "任务清单上 N 天没动的任务, 提醒回来推进。\n\n"
    "严格只输出 JSON, 不要 Markdown 代码块, 不要解释。"
)


def _overview_user(date: str, stats_text: str, events_text: str, tasks_text: str) -> str:
    tasks_block = (
        f"【活跃任务清单 — 来自飞书任务表, 优先以任务视角描述】\n{tasks_text}\n\n"
        if tasks_text else "【活跃任务清单】(无)\n\n"
    )
    return (
        f"【日期】{date}\n\n"
        f"{tasks_block}"
        f"【骨架统计 — 供你参考节奏, 不要照搬数字】\n{stats_text}\n\n"
        f"【事件清单, 按时间; 前缀 [task:X] 表示已关联到任务 X, [proj:Y] 表示游离项目】\n{events_text}\n\n"
        "【输出 JSON, 严格按此 shape, 每个字段都要有】\n"
        '{\n'
        '  "headline": "≤30 字, 一句话概括今天的主线 (例: \'双线推进 daytrace UI 与评分模型\')",\n'
        '  "overview": {\n'
        '    "narrative": "2-3 句 (60-120 字): ta 今天具体做了什么 + 呈现什么工作模式 (深度块 / 多线 / 元工作 / 探索...), 不要堆砌数字",\n'
        '    "key_moves": ["3-5 条具体动作或产出, 每条 ≤30 字, 例: \'完成评分模型 PR\', \'重构泳道布局\'"]\n'
        '  },\n'
        '  "trend": {\n'
        '    "direction": "rising | steady | dropping | new | paused | blocked",\n'
        '    "comparison": "1 句 (≤60 字) 描述工作重心/节奏 vs 昨天有什么变化, 不要复述事件量"\n'
        '  },\n'
        '  "highlights":  ["1-3 条今天真正完成或推进的事 (合并 PR、提交、上线...), 每条 ≤40 字; 不要写\'高频活动\'之类"],\n'
        '  "suggestions": ["1-3 条针对 ta 个人的下一步行动 (具体到项目和动作); 可以是\'明天继续推 X\'、\'Y 已 3 天没碰, 该回来看看\'; 每条 ≤50 字"]\n'
        '}'
    )


def compute_ai_overview(events: list[dict[str, Any]], ctx: ChannelContext) -> ChannelResult:
    if not events:
        return ChannelResult(value={
            "headline": f"{ctx.date} 无事件",
            "overview": {"narrative": "今天没有记录到事件。", "key_moves": []},
            "trend": None,
            "highlights": [], "suggestions": [],
        })
    if not ai_client.is_available():
        return ChannelResult(value=None)  # written as JSON null, error=None
    task_map = _load_event_task_map(ctx.con, ctx.date)
    events_text = _format_events_inline(events, task_map=task_map)
    stats_text = _stats_summary(ctx.con, ctx.date)
    tasks_text = _load_active_task_context(ctx.con)
    resp = ai_client.call_json_validated(
        system=OVERVIEW_SYSTEM,
        user=_overview_user(ctx.date, stats_text, events_text, tasks_text),
        validator=validate_overview,
        max_tokens=1200,
    )
    return ChannelResult(
        value=resp.json,
        tokens_in=resp.tokens_in, tokens_out=resp.tokens_out, cost_usd=resp.cost_usd,
    )


# ---- channel: ai_continuity_day ---------------------------------------

CONTINUITY_DAY_SYSTEM = (
    "你是 DayTrace 跨天对比助手。给定昨天和今天的 overview, 输出今天相对昨天的"
    "工作状态变化。严格只输出 JSON, 不要 Markdown。"
)


def compute_ai_continuity_day(events, ctx: ChannelContext) -> ChannelResult:
    """Returns None as value if there's no previous day to compare to."""
    prev = _previous_day_overview(ctx.con, ctx.date)
    if prev is None:
        return ChannelResult(value=None)
    today = _read_day_channel(ctx.con, ctx.date, "ai_overview")
    if today is None:
        return ChannelResult(value=None)
    if not ai_client.is_available():
        return ChannelResult(value=None)
    user = (
        f"【昨天】\n{json.dumps(prev, ensure_ascii=False)}\n\n"
        f"【今天】\n{json.dumps(today, ensure_ascii=False)}\n\n"
        "【输出 JSON】\n"
        '{\n'
        '  "relation_to_yesterday": "1-2 句, 今天与昨天的关系",\n'
        '  "momentum": "rising|steady|dropping",\n'
        '  "notable_changes": ["0-3 条显著变化, 每条 ≤40 字"]\n'
        '}'
    )
    resp = ai_client.call_json_validated(
        system=CONTINUITY_DAY_SYSTEM, user=user, validator=validate_continuity,
        max_tokens=600,
    )
    return ChannelResult(
        value=resp.json,
        tokens_in=resp.tokens_in, tokens_out=resp.tokens_out, cost_usd=resp.cost_usd,
    )


def _previous_day_overview(con: sqlite3.Connection, date: str):
    row = con.execute(
        "SELECT value_json FROM day_channel"
        " WHERE channel = 'ai_overview' AND date < ?"
        " ORDER BY date DESC LIMIT 1",
        (date,),
    ).fetchone()
    if row is None or row["value_json"] is None:
        return None
    try:
        return json.loads(row["value_json"])
    except json.JSONDecodeError:
        return None


# ---- channel: ai_project_summary_batch --------------------------------

PROJECT_SUMMARY_SYSTEM = (
    "你是 DayTrace 项目进展助手。基于当日按项目分组的事件清单, 对每个项目"
    "输出当天进展。严格只输出 JSON, 键为项目名, 不要 Markdown。"
)


def compute_ai_project_summary_batch(events, ctx: ChannelContext) -> ChannelResult:
    if not events:
        return ChannelResult(value={"by_project": {}})
    if not ai_client.is_available():
        return ChannelResult(value=None)
    from . import stats
    by_project = stats.split_events_by_project(events)
    # Cap per-project to 30 events × summary[:80] to keep prompts cheap.
    groups_text_parts = []
    for project, project_events in sorted(by_project.items(), key=lambda kv: -len(kv[1])):
        active = stats.project_active_minutes(project_events)
        truncated = project_events[:30]
        block = _format_events_inline(truncated, summary_cap=80)
        more = f"\n  ... 还有 {len(project_events) - 30} 条" if len(project_events) > 30 else ""
        groups_text_parts.append(
            f"== {project} ({len(project_events)} events, {active} active min) ==\n{block}{more}"
        )
    user = (
        f"【日期】{ctx.date}\n\n"
        f"【项目分组事件】\n" + "\n\n".join(groups_text_parts) + "\n\n"
        "【输出 JSON, 顶层键为 by_project, 值为以项目名为键的字典】\n"
        '{\n'
        '  "by_project": {\n'
        '    "<项目名>": {\n'
        '      "summary": "≤50 字, 这个项目今天做了什么",\n'
        '      "what_was_done": ["2-5 条要点, 每条 ≤30 字"],\n'
        '      "status": "in_progress|done|blocked|explored",\n'
        '      "next_steps": ["0-3 条, 每条 ≤30 字"]\n'
        '    }\n'
        '  }\n'
        '}'
    )
    resp = ai_client.call_json_validated(
        system=PROJECT_SUMMARY_SYSTEM, user=user, validator=validate_project_summary_batch,
        max_tokens=3500,
    )
    return ChannelResult(
        value=resp.json,
        tokens_in=resp.tokens_in, tokens_out=resp.tokens_out, cost_usd=resp.cost_usd,
    )


# ---- channel: ai_project_continuity_batch -----------------------------

PROJECT_CONTINUITY_SYSTEM = (
    "你是 DayTrace 项目跨天助手。对比每个项目今天和它上一次活跃时的情况, "
    "输出连续性判断。严格只输出 JSON, 不要 Markdown。"
)


def compute_ai_project_continuity_batch(events, ctx: ChannelContext) -> ChannelResult:
    if not events:
        return ChannelResult(value={"by_project": {}})
    if not ai_client.is_available():
        return ChannelResult(value=None)
    today_batch = _read_day_channel(ctx.con, ctx.date, "ai_project_summary_batch")
    if not today_batch or "by_project" not in today_batch:
        return ChannelResult(value=None)
    today_by_project = today_batch["by_project"]
    prev_by_project = {
        p: _previous_project_summary(ctx.con, ctx.date, p)
        for p in today_by_project.keys()
    }
    # Skip projects that have no prior — orchestrator can mark them as "new"
    # without an API call. If *no* project has prior history, don't call.
    if not any(v for v in prev_by_project.values()):
        return ChannelResult(value={
            "by_project": {
                p: {"relation_to_previous": None, "momentum": "new"}
                for p in today_by_project
            }
        })
    user = (
        f"【今天每个项目的总结】\n{json.dumps(today_by_project, ensure_ascii=False)}\n\n"
        f"【每个项目上一次活跃时的总结 (null 表示首次出现)】\n"
        f"{json.dumps(prev_by_project, ensure_ascii=False)}\n\n"
        "【输出 JSON】\n"
        '{\n'
        '  "by_project": {\n'
        '    "<项目名>": {\n'
        '      "relation_to_previous": "1-2 句; 若 null 上下文则写 ‘项目首次出现’",\n'
        '      "momentum": "rising|steady|dropping|new|paused"\n'
        '    }\n'
        '  }\n'
        '}'
    )
    resp = ai_client.call_json_validated(
        system=PROJECT_CONTINUITY_SYSTEM, user=user, validator=validate_project_continuity_batch,
        max_tokens=1500,
    )
    return ChannelResult(
        value=resp.json,
        tokens_in=resp.tokens_in, tokens_out=resp.tokens_out, cost_usd=resp.cost_usd,
    )


def _previous_project_summary(con: sqlite3.Connection, date: str, project: str):
    """Find this project's most recent ai_summary before `date`. Returns dict or None."""
    row = con.execute(
        "SELECT value_json FROM day_project_channel"
        " WHERE channel = 'ai_summary' AND project = ? AND date < ?"
        " ORDER BY date DESC LIMIT 1",
        (project, date),
    ).fetchone()
    if row is None or row["value_json"] is None:
        return None
    try:
        return json.loads(row["value_json"])
    except json.JSONDecodeError:
        return None


# ---- channel: ai_activity_labels --------------------------------------

ACTIVITY_LABEL_SYSTEM = (
    "你是 DayTrace 的事件活动分类助手。把当天每条事件按‘活动类型’归类，类别"
    "不要预设, 你自己根据事件内容自由归纳; 但**每天用到的类别数尽量控制在 5-10 个**, "
    "重复使用相同的类别名字（用中文短词, 如 ‘开发’、‘学习’、‘写作’、‘沟通’、‘调试’、"
    "‘阅读’、‘规划’、‘杂项’ 等）。严格只输出 JSON, 不要 Markdown。"
)


ACTIVITY_LABEL_CHUNK = 80  # events per LLM call; big days are split + merged


def _classify_chunk(date: str, items: list[tuple[str, str]]) -> tuple[dict[str, str], int, int, float, str]:
    """Send one chunk of events to the LLM, return (labels, tokens_in, tokens_out, cost, model).

    items: list of (event_id, prompt_line).
    """
    lines = [line for _, line in items]
    user = (
        f"【日期】{date}\n\n"
        "【事件清单, 每行: id | 时间 | 来源/项目 | 标题】\n"
        + "\n".join(lines)
        + "\n\n"
        "【输出 JSON】\n"
        "{\n"
        '  "labels": { "<event_id>": "<活动类型中文>" }\n'
        "}\n\n"
        "要求: 每个 event_id 必须有一个 label, label 用中文短词, 同一天内重复使用。"
        "**只输出 JSON, 不要思考说明。**"
    )
    resp = ai_client.call_json(
        system=ACTIVITY_LABEL_SYSTEM,
        user=user,
        max_tokens=6000,
    )
    # The model sometimes ignores the "labels" wrapper and just returns
    # {event_id: label} at the top level. Accept either shape.
    labels_map: dict[str, str] | None = None
    if isinstance(resp.json, dict):
        candidate = resp.json.get("labels")
        if isinstance(candidate, dict):
            labels_map = candidate
        else:
            # Treat top-level dict as the map iff every value is a string
            # (event_ids look like 'codex-input-...' so they're safe keys).
            if all(isinstance(v, str) for v in resp.json.values()):
                labels_map = resp.json
    if not isinstance(labels_map, dict):
        raise ValueError(f"ai_activity_labels: bad shape from LLM: {type(resp.json).__name__}")
    return labels_map, resp.tokens_in, resp.tokens_out, resp.cost_usd, resp.model


def compute_ai_activity_labels(events: list[dict[str, Any]], ctx: ChannelContext) -> ChannelResult:
    """Batch-classify each event into a free-form activity label.

    Writes per-event rows to `event_activity_labels` (one row per event_id).
    Returns a summary value (taxonomy + by_activity counts + labeled_count) for
    the channel JSON — the per-event mapping lives in the side table where SQL
    can JOIN it cheaply.

    For days with many events we chunk by ACTIVITY_LABEL_CHUNK so each LLM
    call's output JSON fits well under the model's max_tokens. Chunks are
    merged into a single taxonomy at the end."""
    if not events:
        return ChannelResult(value={"by_activity": [], "labeled_count": 0, "taxonomy": []})
    if not ai_client.is_available():
        return ChannelResult(value=None)

    # Build per-event prompt line.
    items: list[tuple[str, str]] = []
    for ev in events:
        eid = ev.get("id") or ""
        if not eid:
            continue
        red = _redact_event(ev)
        if red is None:
            continue
        time = (red.get("start") or "")[11:16]
        src = red.get("source") or "other"
        proj = red.get("project") or red.get("project_guess") or "misc"
        title = (red.get("title") or "").strip().replace("\n", " ")[:80] or "(无标题)"
        items.append((eid, f"{eid} | {time} | {src}/{proj} | {title}"))

    if not items:
        return ChannelResult(value={"by_activity": [], "labeled_count": 0, "taxonomy": []})

    # Sort by time so chunked calls see contiguous context (helps the model
    # reuse the same taxonomy across chunks).
    items.sort(key=lambda p: p[1])

    chunks = [items[i:i + ACTIVITY_LABEL_CHUNK] for i in range(0, len(items), ACTIVITY_LABEL_CHUNK)]
    all_labels: dict[str, str] = {}
    total_in = total_out = 0
    total_cost = 0.0
    model_used = ""
    for chunk in chunks:
        labels_map, tin, tout, cost, model = _classify_chunk(ctx.date, chunk)
        # Defensive normalize
        for eid, lab in labels_map.items():
            if not lab:
                continue
            all_labels[eid] = str(lab).strip() or "未分类"
        total_in += tin
        total_out += tout
        total_cost += cost
        model_used = model

    seen_ids = {eid for eid, _ in items}
    rows = [
        {
            "event_id": eid,
            "label": lab,
            "source": "ai",
            "confidence": 0.7,
            "model": model_used,
        }
        for eid, lab in all_labels.items()
        if eid in seen_ids
    ]
    from .db import upsert_activity_labels
    upsert_activity_labels(ctx.con, rows, commit=False)

    from collections import Counter
    counter = Counter(r["label"] for r in rows)
    by_activity = [{"name": n, "count": c} for n, c in counter.most_common()]
    return ChannelResult(
        value={
            "by_activity": by_activity,
            "labeled_count": len(rows),
            "taxonomy": [n for n, _ in counter.most_common()],
            "chunks": len(chunks),
        },
        tokens_in=total_in,
        tokens_out=total_out,
        cost_usd=round(total_cost, 6),
    )


# ---- per-project channels: pull from day-level batch ------------------

def _slice_project_summary(events, ctx: ChannelContext) -> dict[str, Any] | None:
    return _read_batch_slice(ctx, "ai_project_summary_batch")


def _slice_project_continuity(events, ctx: ChannelContext) -> dict[str, Any] | None:
    return _read_batch_slice(ctx, "ai_project_continuity_batch")


def _read_batch_slice(ctx: ChannelContext, day_channel: str):
    batch = _read_day_channel(ctx.con, ctx.date, day_channel)
    if not batch:
        return None
    by_project = batch.get("by_project") or {}
    return by_project.get(ctx.project or "")


# ---- Registration -----------------------------------------------------

register_day_channel(
    ChannelSpec(
        name="ai_overview", table="day", generator="ai",
        version=AI_VERSION,
        dependencies=("active_minutes", "longest_focus_block",
                      "context_switches", "dimension_counts"),
        cost_estimate="~5K in / ~0.8K out",
        description="Day-level work-state narrative (DeepSeek).",
    ),
    compute_ai_overview,
)

register_day_channel(
    ChannelSpec(
        name="ai_continuity_day", table="day", generator="ai",
        version=AI_VERSION,
        dependencies=("ai_overview",),
        cost_estimate="~1K in / ~0.5K out",
        description="Today vs previous day (DeepSeek).",
    ),
    compute_ai_continuity_day,
)

register_day_channel(
    ChannelSpec(
        name="ai_project_summary_batch", table="day", generator="ai",
        version=AI_VERSION,
        dependencies=("dimension_counts",),
        cost_estimate="~6K in / ~2.5K out (batched)",
        description="Per-project summaries, batched (DeepSeek).",
    ),
    compute_ai_project_summary_batch,
)

register_day_channel(
    ChannelSpec(
        name="ai_project_continuity_batch", table="day", generator="ai",
        version=AI_VERSION,
        dependencies=("ai_project_summary_batch",),
        cost_estimate="~3K in / ~1K out (batched)",
        description="Per-project continuity vs prev active day (DeepSeek).",
    ),
    compute_ai_project_continuity_batch,
)

register_day_channel(
    ChannelSpec(
        name="ai_activity_labels", table="day", generator="ai",
        version=AI_VERSION,
        dependencies=(),  # works directly off raw events
        cost_estimate="~5K in / ~2K out (free-form taxonomy)",
        description="Per-event free-form activity labels; writes to event_activity_labels.",
    ),
    compute_ai_activity_labels,
)

register_project_channel(
    ChannelSpec(
        name="ai_summary", table="day_project", generator="ai",
        version=AI_VERSION, dependencies=(),
        cost_estimate="free (slice of batch)",
        description="This project's summary slice, read from the day batch.",
    ),
    _slice_project_summary,
)
register_project_channel(
    ChannelSpec(
        name="ai_continuity", table="day_project", generator="ai",
        version=AI_VERSION, dependencies=(),
        cost_estimate="free (slice of batch)",
        description="This project's continuity slice, read from the day batch.",
    ),
    _slice_project_continuity,
)
