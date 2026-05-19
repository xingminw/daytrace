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
AI_VERSION = "v16"  # v16 = en fields must be pure English (no CJK characters)


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


def _bilingual_str(v, *, allow_empty=False) -> dict | str | None:
    """Normalize a value that may be:
       • a {"zh": "...", "en": "..."} dict   → return as-is (cleaned)
       • a plain string (legacy v6-v13)       → wrap as {"zh": value}
       • None or missing                      → return None
    The renderer always reads via .get(lang) with fallback to other
    languages, so legacy single-language cache values keep rendering."""
    if isinstance(v, dict):
        zh = (v.get("zh") or "").strip()
        en = (v.get("en") or "").strip()
        if not zh and not en:
            return None if not allow_empty else {"zh": "", "en": ""}
        return {"zh": zh, "en": en}
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None if not allow_empty else {"zh": "", "en": ""}
        return {"zh": s, "en": ""}
    return None


def _bilingual_list(items) -> list[dict]:
    """List-of-strings or list-of-{zh,en} → list of {zh, en} dicts.
    Drops empties."""
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    for it in items:
        b = _bilingual_str(it)
        if b:
            out.append(b)
    return out


def validate_overview(payload):
    """v14 schema (bilingual):
        {
          "headline":   {"zh": str ≤30, "en": str ≤40},
          "overview":   {"narrative": {"zh": str, "en": str}},
          "trend":      {"direction": one_of,
                         "comparison": {"zh": str, "en": str}},
          "highlights":   [{"zh": str, "en": str}, ...],
          "work_pattern": [{"zh": str, "en": str}, ...],
          "suggestions":  [{"zh": str, "en": str}, ...]
        }

    Backward compat: any bare-string field (v6-v13) is auto-wrapped as
    {"zh": value, "en": ""} so cached single-language payloads render
    without re-running AI. The renderer's get-with-fallback handles
    missing language gracefully."""
    from .ai_client import ShapeError
    if not isinstance(payload, dict):
        raise ShapeError(f"top-level must be an object, got {type(payload).__name__}")

    headline = _bilingual_str(payload.get("headline"))
    if headline is None:
        raise ShapeError("missing 'headline' (need str or {zh,en})")

    raw_overview = payload.get("overview")
    if isinstance(raw_overview, dict):
        narrative = _bilingual_str(raw_overview.get("narrative"))
    else:
        # v6 legacy: top-level narrative string
        narrative = _bilingual_str(payload.get("narrative"))
    if narrative is None:
        raise ShapeError("missing 'overview.narrative'")
    overview_obj = {"narrative": narrative}

    raw_trend = payload.get("trend")
    if isinstance(raw_trend, dict):
        direction = (raw_trend.get("direction") or "steady").strip().lower()
        if direction not in _TREND_DIRECTIONS:
            direction = "steady"
        comparison = _bilingual_str(raw_trend.get("comparison"), allow_empty=True) or {"zh": "", "en": ""}
        trend_obj = {"direction": direction, "comparison": comparison}
    else:
        trend_obj = None

    highlights   = _bilingual_list(payload.get("highlights"))
    work_pattern = _bilingual_list(payload.get("work_pattern"))
    # v7 used `recommendations`; older v7 cache may have `concerns`.
    raw_suggestions = payload.get("suggestions")
    if raw_suggestions is None:
        raw_suggestions = payload.get("recommendations")
    suggestions = _bilingual_list(raw_suggestions)
    if not suggestions and isinstance(payload.get("concerns"), list):
        suggestions = _bilingual_list(payload["concerns"])

    return {
        "headline":     headline,
        "overview":     overview_obj,
        "trend":        trend_obj,
        "highlights":   highlights,
        "work_pattern": work_pattern,
        "suggestions":  suggestions,
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
            SELECT title, title_en, status, due_date
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
        title_en = (r["title_en"] or "").strip() if "title_en" in r.keys() else ""
        status = (r["status"] or "").strip() or "?"
        due = (r["due_date"] or "").strip()
        suffix = f" · {status}"
        if due:
            suffix += f" · due {due}"
        # When an English title is available, surface both so the AI
        # has a direct map for bilingual output (en bullets should use
        # the EN title, not embed the zh one).
        if title_en and title_en != title:
            lines.append(f"- {title} / {title_en}{suffix}")
        else:
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


def _compute_recent_baseline(con: sqlite3.Connection, date: str, days: int = 7) -> dict:
    """Average of key time-pattern stats over the `days` days strictly
    before `date`. Reads channels `time_span`, `longest_focus_block`,
    `context_switches`, `active_minutes` from day_channel. Returns:
        {first_hhmm, last_hhmm, longest_focus_min, switches, active_min,
         sample_days}  — sample_days < days when history is short.
    All values are integers (minutes) or HH:MM strings. None of these
    keys are present if zero history rows were found."""
    rows = con.execute(
        """
        SELECT date, channel, value_json
          FROM day_channel
         WHERE date < ?
           AND channel IN ('time_span','longest_focus_block','context_switches','active_minutes')
         ORDER BY date DESC
         LIMIT ?
        """,
        (date, days * 4),
    ).fetchall()
    if not rows:
        return {}
    from collections import defaultdict
    by_date: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in rows:
        try:
            v = json.loads(r["value_json"]) if r["value_json"] else None
        except Exception:
            continue
        if v is not None:
            by_date[r["date"]][r["channel"]] = v
    dated = sorted(by_date.items(), key=lambda kv: kv[0], reverse=True)[:days]
    if not dated:
        return {}

    def _to_min(hhmm: str) -> int | None:
        try:
            h, m = hhmm.split(":")
            return int(h) * 60 + int(m)
        except Exception:
            return None
    def _from_min(total: int) -> str:
        return f"{total // 60:02d}:{total % 60:02d}"

    firsts, lasts, focuses, switches, actives = [], [], [], [], []
    for _, channels in dated:
        ts = channels.get("time_span") or {}
        lfb = channels.get("longest_focus_block") or {}
        cs = channels.get("context_switches") or {}
        am = channels.get("active_minutes") or {}
        if (f := _to_min(ts.get("first") or "")) is not None: firsts.append(f)
        if (l := _to_min(ts.get("last")  or "")) is not None: lasts.append(l)
        if lfb.get("duration_min"): focuses.append(int(lfb["duration_min"]))
        if cs.get("count") is not None: switches.append(int(cs["count"]))
        if am.get("total") is not None: actives.append(int(am["total"]))

    def _avg(xs: list[int]) -> int | None:
        return round(sum(xs) / len(xs)) if xs else None

    avg_first = _avg(firsts);  avg_last = _avg(lasts)
    return {
        "sample_days":       len(dated),
        "first_hhmm":        _from_min(avg_first) if avg_first is not None else None,
        "last_hhmm":         _from_min(avg_last)  if avg_last  is not None else None,
        "longest_focus_min": _avg(focuses),
        "switches":          _avg(switches),
        "active_min":        _avg(actives),
    }


def _format_baseline(baseline: dict) -> str:
    """One-line baseline block for the prompt. Empty when no history."""
    if not baseline or not baseline.get("sample_days"):
        return ""
    sd = baseline["sample_days"]
    bits = []
    if baseline.get("first_hhmm"):        bits.append(f"首次活跃 ~{baseline['first_hhmm']}")
    if baseline.get("last_hhmm"):         bits.append(f"收工 ~{baseline['last_hhmm']}")
    if baseline.get("active_min") is not None:        bits.append(f"活跃 ~{baseline['active_min']} min")
    if baseline.get("longest_focus_min") is not None: bits.append(f"最长专注 ~{baseline['longest_focus_min']} min")
    if baseline.get("switches") is not None:          bits.append(f"切换 ~{baseline['switches']} 次")
    return f"近 {sd} 天均值: " + " · ".join(bits)


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

    # `top 项目` deliberately omitted — including it tempts the model to
    # name-drop project_guess values ('daytrace', 'daily-manager') instead
    # of the proper task titles from the 活跃任务清单. Project info is
    # already in event line prefixes [proj:Y] for events with no task.
    by_source = ", ".join(
        f"{r['name']}({r['count']})" for r in (dc.get("by_source") or [])[:5]
    )
    parts = [
        f"时间跨度 {ts.get('first', '?')}–{ts.get('last', '?')} (span {ts.get('span_min', 0)} min)",
        f"活跃总时长 {am.get('total', 0)} min",
        f"最长不间断块 {lfb.get('duration_min', 0)} min ({lfb.get('start','?')}–{lfb.get('end','?')}, "
        f"主导来源 {lfb.get('dominant_source','?')})"
        if lfb else "最长不间断块: 无",
        f"项目切换 {cs.get('count', 0)} 次",
        f"top 来源 {by_source}",
        f"质量: 敏感事件 {q.get('sensitive', 0)} 条, 缺项目归类 {q.get('missing_project', 0)} 条",
    ]
    return "\n".join(p for p in parts if p)


# ---- channel: ai_overview ---------------------------------------------

OVERVIEW_SYSTEM = (
    "你是一位软件工程师的私人工作复盘助手 / a personal work-recap assistant. "
    "读者是这位工程师本人 / the reader is the engineer themself.\n\n"
    "**双语输出 / Bilingual output**: 每个文本字段输出 {\"zh\": ..., \"en\": ...} "
    "对象。两种语言独立生成同一份内容(不是简单翻译),各自符合语言习惯。\n\n"
    "**en 字段是纯英文 / EN field MUST be pure English**: 任务清单里如果"
    "提供了 `中文名 / English name` 形式的对照, 在 en 输出里 **必须用英文名**, "
    "不要把中文名复制进英文 bullet。不要出现任何 CJK 汉字字符。如果某个名字没有"
    "英文对照, 用简短英文意译 (例: 'paper-review project', 'i18n rollout'), 不要"
    "保留汉字。Same rule for narrative, headline, work_pattern, suggestions: "
    "the `en` value contains zero Chinese characters.\n\n"
    "输入会按顺序给你:\n"
    "  1. 活跃任务清单 (飞书任务表)\n"
    "  2. 近 N 天基线 (用于和今天对比)\n"
    "  3. 今日骨架统计 (时长、专注块、切换、来源分布)\n"
    "  4. 事件清单 (按时间, 预标 [task:X] / [proj:Y])\n\n"
    "输出 6 个字段, 每个字段定位**严格不同**:\n"
    "  • headline + overview.narrative — 今天整体怎么过的, 叙事段落\n"
    "  • trend — 和昨天比的整体方向 (chip + 1 句)\n"
    "  • **highlights (🚀 关键任务进展)** — 今天**已经做了**的具体任务推进\n"
    "  • **work_pattern (⏰ 时间安排回顾)** — 今天的**时间数据 vs 基线**, "
    "必须 grounded 在数字 (例: ‘23:31 收工, 比平时晚 1h’); 至少 1 条\n"
    "  • **suggestions (🔔 任务跟进提醒)** — **明天/未来**该盯的任务 "
    "(deadline / N 天没碰 / 未提交); 不要回顾今天该做啥\n\n"
    "**写作风格**:\n"
    "  • narrative 要像跟熟人讲今天发生了什么, 不是写工作日报。带温度 "
    "(‘一头扎进 X’, ‘反复拉扯’, ‘临收工还在调’), 有画面 (具体提到某个工具/PR/"
    "deadline), 有节奏 (上午…下午…傍晚…)。**禁止**通报体 (‘今天主要做了…, "
    "完成了…’)。\n"
    "  • bullet 写法多样化, 不要每条都是 ‘任务名:动作’ 死板模板。可以 "
    "‘任务 X, 评分模型 v2 PR 合掉, scoring 终于稳了’ 这种带感觉的描述。\n\n"
    "**任务视角硬规则**:\n"
    "  • narrative / highlights / suggestions 里提到的活动, 必须用任务清单"
    "**完整标题** (例: ‘DayTrace 应用开发’, 不是 ‘DayTrace’ 或 ‘daytrace’)。\n"
    "  • [proj:Y] 是游离工作, narrative 一笔带过, 不进 highlights/suggestions。\n\n"
    "**禁止**:\n"
    "  ❌ highlights 和 work_pattern 内容重叠\n"
    "  ❌ suggestions 写回顾性内容\n"
    "  ❌ work_pattern 写空话 ('合理作息'、'减少切换') — 必须带基线对比\n"
    "  ❌ 用项目名代替任务名\n"
    "  ❌ 对数据本身/系统/工具提建议\n"
    "  ❌ 泛化效率说教、数字复述\n\n"
    "严格只输出 JSON, 不要 Markdown 代码块, 不要解释。"
)


def _overview_user(
    date: str, stats_text: str, events_text: str, tasks_text: str,
    baseline_text: str,
) -> str:
    tasks_block = (
        f"【活跃任务清单 — 来自飞书任务表, 优先以任务视角描述】\n{tasks_text}\n\n"
        if tasks_text else "【活跃任务清单】(无)\n\n"
    )
    baseline_block = (
        f"【基线 — 用于 work_pattern 对比, 不要照搬】\n{baseline_text}\n\n"
        if baseline_text else ""
    )
    return (
        f"【日期】{date}\n\n"
        f"{tasks_block}"
        f"{baseline_block}"
        f"【今日骨架统计】\n{stats_text}\n\n"
        f"【事件清单, 按时间; 前缀 [task:X] 表示已关联到任务 X, [proj:Y] 表示游离项目】\n{events_text}\n\n"
        "**重要 — EN 字段纯英文要求**: 上面任务清单里 `中文名 / English name` "
        "的行, en 输出里 **必须用 English name**, 禁止把中文名抄进英文 bullet。"
        "narrative/headline/highlights/work_pattern/suggestions 的 en 字段里 "
        "**不允许出现任何汉字**。\n\n"
        "【输出 JSON — 双语 schema】\n"
        "每个文本字段都用 {\"zh\": \"...\", \"en\": \"...\"} 的双语对象,"
        "**两种语言要表达同一份内容、同一个判断**,不是一个翻译另一个 ——"
        "都从原始数据独立生成,保持各自语言的自然表达。"
        "英文是给国际读者看的,中文是给本人看的,语气都要像跟熟人讲,"
        "都用具体任务全名/工具名/数字。\n\n"
        "Shape:\n"
        '{\n'
        '  "headline": {"zh": "≤30 字, 一句话抓住今天的主线",\n'
        '               "en": "≤40 chars, one sentence headline"},\n'
        '  "overview": {\n'
        '    "narrative": {\n'
        '      "zh": "**4-6 句, 150-260 字** 的叙事段落。像跟熟人讲今天发生了啥: 早上怎么进入, 中间在哪儿转弯/卡住/惊喜, 傍晚 / 临收工怎么收尾。具体提到任务全名、用到的工具(Codex / Claude Code / git)、某次提交或讨论。**禁止**: bullet 格式, 通报体, 重复 highlights 的具体产出。",\n'
        '      "en": "**4-6 sentences, 200-360 chars**: a story of the day — how you got into it, where it turned/stuck/surprised, how it wound down. Name the tasks (full Feishu titles), tools (Codex / Claude Code / git), specific commits or discussions. NO bullets, NO status-report tone (\'today I worked on X, Y, Z\'), and do not repeat the highlights bullets."\n'
        '    }\n'
        '  },\n'
        '  "trend": {\n'
        '    "direction": "rising | steady | dropping | new | paused | blocked",\n'
        '    "comparison": {"zh": "1 句 (≤60 字) 工作重心/节奏 vs 昨天怎么变",\n'
        '                   "en": "1 sentence (≤90 chars) on how focus/pace shifted vs yesterday"}\n'
        '  },\n'
        '  "highlights":   [\n'
        '    {"zh": "**2-4 条** 今天真正推进的飞书任务+动作 (用任务全名)。bullet 风格多样化, 每条 ≤50 字",\n'
        '     "en": "2-4 concrete advances on Feishu tasks (full task names), varied bullet phrasing, ≤80 chars each"}\n'
        '  ],\n'
        '  "work_pattern": [\n'
        '    {"zh": "**1-4 条** 基于【今日骨架统计 vs 近 N 天均值】的具体观察, 必须 grounded 在数字 (例: \'23:31 收工, 比平时晚 1h\')。每条 ≤60 字。❌ 空话禁止",\n'
        '     "en": "1-4 observations comparing today\'s time data vs the N-day baseline, MUST be grounded in numbers (e.g. \'wrapped at 23:31, 1h later than your average\'). ≤100 chars each. NO generic advice"}\n'
        '  ],\n'
        '  "suggestions":  [\n'
        '    {"zh": "**2-4 条** 前瞻性提醒, 只看任务清单 (未推进 / deadline 临近 / 未提交)。用任务全名 + 具体行动建议。每条 ≤60 字。❌ 不回顾今天",\n'
        '     "en": "2-4 forward-looking task watchpoints (untouched / deadline closing / uncommitted). Full task names + concrete next-step. ≤100 chars each. NO recap of what was done today"}\n'
        '  ]\n'
        '}'
    )


def compute_ai_overview(events: list[dict[str, Any]], ctx: ChannelContext) -> ChannelResult:
    if not events:
        return ChannelResult(value={
            "headline":  {"zh": f"{ctx.date} 无事件", "en": f"{ctx.date} — no events"},
            "overview":  {"narrative": {"zh": "今天没有记录到事件。",
                                         "en": "No events recorded today."}},
            "trend": None,
            "highlights": [], "work_pattern": [], "suggestions": [],
        })
    if not ai_client.is_available():
        return ChannelResult(value=None)  # written as JSON null, error=None
    task_map = _load_event_task_map(ctx.con, ctx.date)
    events_text = _format_events_inline(events, task_map=task_map)
    stats_text = _stats_summary(ctx.con, ctx.date)
    tasks_text = _load_active_task_context(ctx.con)
    baseline = _compute_recent_baseline(ctx.con, ctx.date)
    baseline_text = _format_baseline(baseline)
    resp = ai_client.call_json_validated(
        system=OVERVIEW_SYSTEM,
        user=_overview_user(ctx.date, stats_text, events_text, tasks_text, baseline_text),
        validator=validate_overview,
        max_tokens=5000,  # bilingual output roughly doubles tokens
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
    "你是 DayTrace 的事件活动分类助手。把当天每条事件按‘活动类型’归类。"
    "类别不预设, 你自己根据事件内容自由归纳; **每天用到的类别数尽量控制在 5-10 个**, "
    "同一天内重复使用相同的类别名(中文短词, 例: '开发' '学习' '写作' '沟通' '调试' "
    "'阅读' '规划' '杂项')。\n\n"
    "**双语输出**: 每个 event 的 label 是 {\"zh\": \"开发\", \"en\": \"Coding\"} 形式的对象。"
    "两种语言独立给出同一类别的自然短词(不是逐字翻译); 同一类别每次出现都用同样的 zh/en 短词。\n\n"
    "严格只输出 JSON, 不要 Markdown。"
)


ACTIVITY_LABEL_CHUNK = 80  # events per LLM call; big days are split + merged


def _classify_chunk(date: str, items: list[tuple[str, str]]) -> tuple[dict[str, dict], int, int, float, str]:
    """Send one chunk of events to the LLM, return (labels, tokens_in,
    tokens_out, cost, model). The labels map is event_id → {zh, en}
    (bilingual)."""
    lines = [line for _, line in items]
    user = (
        f"【日期】{date}\n\n"
        "【事件清单, 每行: id | 时间 | 来源/项目 | 标题】\n"
        + "\n".join(lines)
        + "\n\n"
        "【输出 JSON, 双语 schema】\n"
        "{\n"
        '  "labels": { "<event_id>": {"zh": "<中文短词>", "en": "<English short word>"} }\n'
        "}\n\n"
        "要求: 每个 event_id 必须有一个 label 对象, zh/en 都要给出, 同一天内重复使用"
        "相同的 zh/en 短词。**只输出 JSON, 不要思考说明。**"
    )
    resp = ai_client.call_json(
        system=ACTIVITY_LABEL_SYSTEM,
        user=user,
        max_tokens=8000,  # bumped: bilingual ~doubles output
    )
    labels_map: dict[str, dict] | None = None
    if isinstance(resp.json, dict):
        candidate = resp.json.get("labels")
        if isinstance(candidate, dict):
            labels_map = candidate
        else:
            # Top-level dict accepted iff values look like our bilingual shape
            if all(isinstance(v, (dict, str)) for v in resp.json.values()):
                labels_map = resp.json
    if not isinstance(labels_map, dict):
        raise ValueError(f"ai_activity_labels: bad shape from LLM: {type(resp.json).__name__}")
    # Normalize each value to {zh, en}; tolerate legacy string-shaped values
    # by treating them as zh.
    normalized: dict[str, dict] = {}
    for eid, val in labels_map.items():
        if isinstance(val, dict):
            normalized[eid] = {
                "zh": (val.get("zh") or "").strip(),
                "en": (val.get("en") or "").strip(),
            }
        elif isinstance(val, str):
            normalized[eid] = {"zh": val.strip(), "en": ""}
    return normalized, resp.tokens_in, resp.tokens_out, resp.cost_usd, resp.model


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
    all_labels: dict[str, dict] = {}  # eid → {zh, en}
    total_in = total_out = 0
    total_cost = 0.0
    model_used = ""
    for chunk in chunks:
        labels_map, tin, tout, cost, model = _classify_chunk(ctx.date, chunk)
        for eid, lab in labels_map.items():
            if not isinstance(lab, dict):
                continue
            zh = (lab.get("zh") or "").strip() or "未分类"
            en = (lab.get("en") or "").strip()  # may be empty; renderer falls back to zh
            all_labels[eid] = {"zh": zh, "en": en}
        total_in += tin
        total_out += tout
        total_cost += cost
        model_used = model

    seen_ids = {eid for eid, _ in items}
    rows = [
        {
            "event_id":   eid,
            "label":      lab["zh"],   # back-compat column = zh
            "label_json": lab,          # new bilingual map
            "source":     "ai",
            "confidence": 0.7,
            "model":      model_used,
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
