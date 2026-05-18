# AI-Augmented Daily Report — Design

Status: design draft (2026-05-16), no code yet.
Owners: dashboard + ai layer
Supersedes the ad-hoc daily report bullets currently rendered in `today_page`.

## 1. Goal

Replace the deterministic four-bullet "daily report" inside the report card
with a **three-layer report** that lives in the same card:

1. **Layer 1 — hard stats** (no LLM): facts you can count
2. **Layer 2 — AI overview** (one LLM call): a narrative on the day as a whole
3. **Layer 3 — AI per-slice summaries** (one batched LLM call): per-category
   write-ups (per project, per source, per device, per location) so any
   dimension you slice the day by has a paragraph attached to each slice

The report is **one structured JSON document per date**, persisted in SQLite.
The dashboard renders parts of it; consumers (future LLM tools, CLI exports,
weekly roll-ups) can read the same JSON.

Out of scope of this doc: front-end layout, hover/click UX. We design the
data first and let the front-end consume it.

## 2. Non-goals

- Real-time streaming of partial AI output. The whole report is generated
  in one shot and cached.
- Multi-day rollups (weekly / monthly summaries). Separate concern; this
  document will be a building block.
- Personalised tone/style configuration. We ship one prompt; we iterate.

## 3. Why three layers

| Layer | Cost | Truthfulness | When to (re)generate |
|---|---|---|---|
| 1 stats | µs | guaranteed (it's just counting) | every render |
| 2 overview | one LLM call (~$0.01) | best-effort narrative | when events_hash changes |
| 3 slice summaries | one LLM call (~$0.02) | best-effort per-slice | when events_hash changes |

Splitting them means:
- Stats can never lie — they always reflect the current DB. If AI is missing
  or stale, the card still shows real numbers.
- AI can degrade independently. API down? Card shows stats only.
- Cache invalidation is per-layer, not all-or-nothing.

## 4. Layer 1 — hard stats

Source: `daytrace/stats.py::compute_daily_stats(con, date) -> dict`.

Pure function. Reads from `events` table only. No LLM, no network.

### 4.1 Output schema

```json
{
  "date": "2026-05-15",
  "total_events": 248,
  "first_event_at": "08:14",
  "last_event_at": "23:47",
  "active_minutes": 382,
  "longest_focus_block": {
    "duration_min": 78,
    "start": "10:12", "end": "11:30",
    "dominant_source": "codex",
    "dominant_project": "DayTrace Joint",
    "event_count": 34
  },
  "peak_window": {"label": "10:00-11:00", "count": 42},
  "context_switches": 14,
  "low_confidence_count": 3,
  "by_project":  [{"name": "DayTrace Joint", "count": 141, "active_min": 188, "share": 0.57}, ...],
  "by_source":   [{"name": "hermes",          "count": 153, "active_min": 218, "share": 0.62}, ...],
  "by_device":   [...],
  "by_location": [...]
}
```

### 4.2 Algorithms

**active_minutes** — replaces the misleading "covered 13 hour bands" metric.

```
for each event, assign a footprint interval [start, start + footprint(source)]
union all intervals
active_minutes = sum of merged interval lengths
```

`footprint(source)` defaults:

| source | default footprint |
|---|---|
| codex      | 5 min |
| hermes     | 5 min |
| docs       | 3 min |
| git        | 1 min |
| github     | 1 min |
| macos-activity | use event's `end - start` if present, else 5 min |
| (other)    | 5 min |

Configurable via `config/stats.yaml` later; ship with hard-coded defaults.
Footprints chosen so a quiet 30-event day reads as ~2 active hours, not
"24 minutes". We can tune; the algorithm shape doesn't change.

**longest_focus_block** — "longest stretch you didn't drop off."

```
sort events by start time
walk: start a new block whenever gap_to_prev > 10 min
keep the longest block, plus its dominant source/project (by count)
```

The 10-min threshold matches the histogram's 20-min bin scale: anything
quieter than ~10 min between events is treated as a break.

**peak_window** — busiest hour of the day.

```
group events by hour-of-day, return the max bucket
```

**context_switches** — proxy for "how fragmented was the day."

```
walk events in time order
count each transition where prev.project != current.project AND gap < 10 min
```

(Ignore quiet gaps so a normal lunch break doesn't count as a switch.)

**by_project / by_source / by_device / by_location**

For each dimension:
- `count` — number of events tagged with that value
- `active_min` — the active_minutes algorithm restricted to events of that
  value (so per-project active time is comparable to total active time and
  the per-project shares can be reported honestly)
- `share` — count / total_events (rounded to 2 decimals)

Ordered by count descending.

### 4.3 Performance

Daily event count rarely exceeds 1000. All of the above runs in tens of ms
in pure Python. No need to cache stats — compute on every page render.

## 5. Layer 2 — AI overview

Source: `daytrace/ai_report.py::generate_overview(stats, events) -> dict`.

### 5.1 Output schema

```json
{
  "headline": "时间轴重构 + Feishu sync 调 bug 的一天",
  "narrative": "上午围绕 dashboard 时间轴卡片做了一轮密集迭代…",
  "highlights": [
    "时间轴卡片改成 泳道+直方图，去掉刻度图",
    "全局维度选择器抽离到顶部",
    "Feishu Drive 空文件 1061002 bug 修复，omen-wsl 多设备 e2e 跑通"
  ],
  "concerns": [
    "11:30-12:00 一段未归因 codex 对话，建议补 source rule"
  ]
}
```

Constraints (enforced via prompt + JSON schema validation):

- `headline`: ≤ 30 字, no punctuation cliffhangers, present-tense
- `narrative`: 150-300 字, 2-3 段
- `highlights`: 3-6 items, each ≤ 40 字
- `concerns`: 0-4 items, optional (omit if none); each ≤ 50 字

### 5.2 Prompt

```
你是 DayTrace 的私人日报助手。基于以下当日活动事件和统计骨架，输出一份 JSON
格式的速读。要求：

- headline: 一句话标题, ≤30字, 抓住当天主线
- narrative: 2-3段, 150-300字, 描述时间分布形态和上下文切换
- highlights: 3-6条, 每条≤40字, 当天的实质性进展（不是琐事）
- concerns: 0-4条, 异常 / 未归因 / 待跟进。没有就省略

避免逐字罗列事件。要"读懂"它们。中文输出。

【骨架】
{stats_summary}

【事件清单（按时间, 已脱敏）】
{event_list}

只输出 JSON, 不要 Markdown 包裹, 不要解释。
```

`stats_summary` = a flat one-line digest of Layer 1 stats.
`event_list` = each event as `HH:MM source/project title (summary[:120])`.

### 5.3 Input size

~250 events × ~100 字 each ≈ 25K input tokens.

## 6. Layer 3 — AI per-slice summaries

Source: `daytrace/ai_report.py::generate_slice_summaries(stats, events) -> dict`.

**One LLM call** produces all four dimensions' slice summaries together (chosen
in design discussion 2026-05-16). Cheaper than four calls and keeps related
context shared.

### 6.1 Output schema

```json
{
  "by_project": {
    "DayTrace Joint": {
      "summary": "重做 dashboard 时间轴卡片，从单一刻度视图扩展到泳道+直方图",
      "what_was_done": [
        "新增三视图时间轴",
        "全局维度选择器抽离",
        "饼图与时间轴用统一调色板"
      ]
    },
    "daily-manager": {...},
    ...
  },
  "by_source":   {"codex":  {"summary": "...", "what_was_done": [...]}, ...},
  "by_device":   {"Mac":    {"summary": "...", "what_was_done": [...]}, ...},
  "by_location": {"home":   {"summary": "...", "what_was_done": [...]}, ...}
}
```

Stats (count, active_min, share) for each slice come from Layer 1 — AI does
not re-emit numbers. Front-end merges by name when rendering.

### 6.2 Slice eligibility

Only slices with `count >= 3` get an AI summary. Tiny slices (`< 3 events`)
get a one-line auto-summary from titles: `"3 条事件：<title1>; <title2>..."` —
not worth burning tokens.

Top 10 slices per dimension max; rest grouped into "其他" (not summarised).

### 6.3 Prompt

```
你是 DayTrace 的私人切片分析助手。对当天的事件按下面四个维度分组,
对每个 top-10 切片给出一段简短总结。要求 JSON 输出。

每个切片:
- summary: 一句话, ≤50字, 描述这个切片下"做了什么"
- what_was_done: 2-5条要点, 每条≤30字, 具体动作 / 产出

维度: by_project, by_source, by_device, by_location

【事件清单, 已按维度预聚合】
{grouped_events}

只输出 JSON, 不要解释。结构:
{ "by_project": {<slice_name>: {summary, what_was_done}, ...}, "by_source": {...}, ... }
```

`grouped_events`: pre-sliced server-side as
```
== by_project / DayTrace Joint (141 events) ==
09:30 codex "morning prompt: ..."
09:31 codex "follow up: ..."
...
== by_project / daily-manager (58 events) ==
...
== by_source / hermes (153 events) ==
...
```

Repeats events across dimensions — duplication is intentional because each
dimension wants its own slicing context.

### 6.4 Input size

~250 events × 4 dimensions ≈ 100K input tokens at worst, ~$0.10 with Haiku.
If we truncate per-slice to 30 events with `summary[:80]`, we land closer
to 40K → ~$0.04. Default: truncate.

## 7. Sensitivity & privacy

Each event has `sensitivity ∈ {normal, private, sensitive}`.

| sensitivity | what's sent to LLM |
|---|---|
| `normal`    | full title + summary[:120] |
| `private`   | redacted: time + source only, title → `[私密]` |
| `sensitive` | skipped entirely (event never enters prompt) |

Configurable via `config/ai.yaml::privacy_mode` for users who want all-in.

## 8. Storage

```sql
CREATE TABLE daily_reports (
  date              TEXT PRIMARY KEY,
  stats_json        TEXT,   -- Layer 1 snapshot (informational; truth still lives in events)
  ai_overview_json  TEXT,   -- Layer 2
  ai_slices_json    TEXT,   -- Layer 3 (all four dimensions in one document)
  events_hash       TEXT,   -- sha1(sorted event ids + their start)
  model             TEXT,
  prompt_version    TEXT,   -- bump to invalidate cache when we change prompts
  tokens_in         INTEGER,
  tokens_out        INTEGER,
  cost_usd          REAL,
  generated_at      TIMESTAMP,
  error             TEXT,   -- last failure message if any
  UNIQUE(date)
);
```

`events_hash` invalidates AI when the day's events change. Past days never
change → hash stable → cache always hits → zero ongoing cost for history.

`prompt_version` invalidates everyone when we iterate prompts.

## 9. Generation flow

```
GET /api/daily-report?date=YYYY-MM-DD

1. compute_daily_stats(con, date)   -- always fresh
2. read daily_reports row for date
   if ai_overview_json AND events_hash matches AND prompt_version matches:
       use cached AI
   else:
       generate_overview(stats, events)
       generate_slice_summaries(stats, events)
       store both into daily_reports
3. return { stats, ai: { overview, slices }, meta: { cached, generated_at } }
```

```
POST /api/daily-report?date=YYYY-MM-DD&force=1
   always regenerate AI, overwrite row
```

```
GET /api/daily-report?date=YYYY-MM-DD&include=stats
   skip AI entirely, return stats only (cheap; for stats-only consumers)
```

## 10. Module breakdown

```
daytrace/
├── stats.py              -- compute_daily_stats, footprint constants
├── ai_client.py          -- wraps anthropic SDK; retry, timeout, accounting
├── ai_report.py          -- generate_overview, generate_slice_summaries
│                            both call ai_client; both validate JSON output
├── daily_report.py       -- the GET/POST orchestrator above
└── db.py                 -- add daily_reports migration + read/write helpers

config/
├── ai.yaml               -- model, privacy_mode, slice cap, footprint overrides

dashboard/server.py       -- add /api/daily-report endpoint
                          -- daily_report card reads the structured doc and
                             renders Layer 1 directly, Layer 2 as narrative,
                             Layer 3 attached to whichever slice the user
                             is hovering on the donut / swimlane
```

## 11. Cost model

Per day, both AI calls, Haiku 4.5 default:

| call | input | output | cost |
|---|---|---|---|
| overview (Layer 2)        | ~25K | ~0.8K | ~$0.03 |
| slice summaries (Layer 3) | ~40K | ~3K   | ~$0.05 |
| **total**                 |       |       | **~$0.08** |

365 days fully cached → one-time ~$30 for a full year backfill.
Day-of regenerations (events arrive over the day, hash flips) → ~5 regens per
active day → ~$0.40/day worst case if you keep refreshing manually.

Sonnet 4.5 is ~5× more expensive; ship Haiku default, expose `model:` knob in
ai.yaml for one-off Sonnet runs.

## 12. Failure modes

| failure | behavior |
|---|---|
| LLM API down / timeout | save `error` field, return stats-only; UI hides AI block |
| LLM returns invalid JSON | retry once with `Repair the following JSON:` ; on second failure same as above |
| Empty day (0 events) | skip AI calls entirely; emit a literal placeholder |
| Single event day | skip AI calls; emit a one-line auto-summary from that event |
| events_hash collision (shouldn't happen) | accept cache; users can force-regen |

## 13. Open questions

1. **Per-day vs per-week**: should we generate a weekly rollup from cached
   `daily_reports` rows? Out of scope here, but the schema supports it.
2. **Streaming vs batch**: streaming for narrative feels nice but adds UI
   complexity. Defer.
3. **Footprint per source**: defaults above are guesses. Should we make
   `footprint` part of `source_rules` so it's per-source overridable?
4. **Model choice surfaced**: do we want users to switch Haiku ↔ Sonnet
   from the UI, or just `ai.yaml`? Suggest: yaml-only for v1.
5. **Sensitivity defaults**: redact vs skip for `private`. Suggest: redact
   default (gives AI temporal context) — but ask user.

## 14. Implementation order (when we start coding)

1. `daytrace/stats.py` — pure stats + tests; integrate into `today_page` so
   the existing card uses better numbers (`active_minutes`,
   `longest_focus_block`, `context_switches`) immediately. No AI yet.
2. Migration: add `daily_reports` table to `daytrace/db.py`.
3. `daytrace/ai_client.py` — thin wrapper, plus mock-able for tests.
4. `daytrace/ai_report.py` — `generate_overview` + tests using mock client.
5. `generate_slice_summaries` + tests.
6. `daytrace/daily_report.py` — orchestrator; tests cover cache hit/miss,
   force regen, partial failure.
7. `/api/daily-report` endpoint; wire to existing `today_page` to consume
   structured output (front-end work — separate doc).
8. Backfill script: walk all dates with events, generate-and-cache. One-shot.

## 15. Tangential follow-ups (not AI but related)

These came up in the discussion that produced this doc but are independent
small changes — they should land separately, not bundled with AI work:

- Remove click navigation from `.tl-swim-tick` and `.tl-bin` in
  `dashboard/server.py::event_timeline_card`. Keep only hover tooltip.
  Rationale: clicking a single tick to filter `/events` by title isn't
  useful; clicking a bin to filter to that 20-min window is mildly useful
  but creates surprise navigation. Hover already shows everything needed.
- Replace `daily_report_text` in `dashboard/server.py` with consumption of
  `compute_daily_stats` (item 1 above) once `stats.py` lands. The 4 bullets
  become: total + active_minutes + first/last + longest_focus_block.

---

End of design draft. Next action: review, decide on the open questions in
§13, then start implementation in the order of §14.
