# DayTrace Architecture

How the pieces actually fit together — module by module, channel by
channel. Read top to bottom and you'll know where to look when something
needs changing.

## Pipeline

```
┌─ collectors ─────────────┐   ┌─ orchestrator ──┐   ┌─ delivery ─────────────┐
│ scripts/collect_*.py     │   │                 │   │                        │
│ scripts/run_daily.py     │   │ regenerate_day_ │   │ /today, /weekly        │
│  catchup                 │   │ from_db()       │   │   (dashboard/server.py)│
│   ├ collect_from_config  │   │                 │   │ /api/* JSON            │
│   ├ rsync ssh remotes →  │ → │ runs channels   │ → │                        │
│   │   inbox/<dev>/<date> │   │ in dep order:   │ → │ Markdown + PNG charts  │
│   └ import_inbox.py      │   │  stats (cheap)  │   │  + Feishu Docs import  │
│                          │   │  ai (DeepSeek)  │   │  + Gmail SMTP          │
│ → events table           │   │ → day_channel   │   │ (scripts/export_report)│
└──────────────────────────┘   └─────────────────┘   └────────────────────────┘
```

One SQLite file (`data/daytrace.sqlite`) is the system of record. Every
step reads from it or writes to it; there is no separate cache layer.

## Modules (where to look)

| Path | Lines | What it does |
|---|---|---|
| `daytrace/schema.py`         | 74   | `TraceEvent` dataclass — the canonical event shape that collectors must produce. |
| `daytrace/db.py`             | 817  | All SQL: `init_db()`, `connect()`, `upsert_events()`, `query_events()`, `query_today()`, schema migrations, `iso_week_to_date_range()` etc. Single source of truth for the 18 tables (see [data-model.md](data-model.md)). |
| `daytrace/io.py`             | tiny | JSONL read/write helpers for `inbox/` and `events/`. |
| `daytrace/collector_config.py` | 133  | Parses `config/devices/<device>.yaml` (per-device source enable/disable + paths). |
| `daytrace/stats.py`          | 355  | Pure deterministic stats channels (time_span, active_minutes, longest_focus_block, context_switches, peak_windows, dimension_counts, quality). No I/O, no LLM. |
| `daytrace/channels.py`       | 446  | Channel registry + dependency ordering + `regenerate_day()` orchestrator. Stats channels register at import; AI channels register at import of `ai_report`. |
| `daytrace/ai_client.py`      | 251  | Thin DeepSeek HTTPS client (stdlib only). JSON-mode, shape validator, 1 retry. Auto-loads `~/.daytrace/secrets.env` so launchd-spawned processes get `DEEPSEEK_API_KEY`. |
| `daytrace/ai_report.py`      | 955  | Five AI channels: `ai_overview`, `ai_continuity_day`, `ai_project_summary_batch`, `ai_project_continuity_batch`, `ai_activity_labels`. Prompts + JSON validators + 7-day baseline computation. `AI_VERSION` bumps invalidate the whole cache. |
| `daytrace/daily_report.py`   | 415  | Thin façade: `regenerate_day_from_db(con, date, include_ai=True)` and `load_day_report(con, date)`. Imports `ai_report` as a side effect to register AI channels. |
| `daytrace/work_items.py`     | 577  | Feishu Bitable sync (read-only) + event ↔ work_item linker (URL match + alias yaml + AI). `rebuild_links()` is called by catchup and the audit-panel POST handler. |
| `daytrace/remotes.py`        | 74   | Loader for `config/remotes.yaml`. |
| `daytrace/report_export.py`  | 376  | `archive_markdown_for_date()` / `archive_markdown_for_week()` — turn a day or ISO week into a styled Markdown string suitable for email body or Feishu Docs import. |
| `daytrace/report_charts.py`  | 429  | matplotlib PNGs that mirror the dashboard's stacked-bar + donut views. Same TIMELINE_PALETTE so colors match. |
| `daytrace/report_delivery.py`| 470  | Feishu cloud-doc import (via `lark-cli drive +import` then `docs +media-insert` for charts) and Gmail SMTP (multipart HTML body with inline charts). |
| `dashboard/server.py`        | 5086 | All HTTP routes + page renderers. Stateless except for the SQLite connection. |

CLI entry points:

| Path | Purpose |
|---|---|
| `scripts/run_daily.py {status,catchup,work-items-sync,deploy}` | The umbrella entry point. `catchup` is what launchd runs each day. |
| `scripts/collect_*.py`        | One per source — invoked indirectly via `collect_from_config`. |
| `scripts/import_inbox.py`     | JSONL → `events` table. Idempotent. |
| `scripts/export_report.py`    | Render + optionally upload + optionally email. CLI version of the delivery pipeline. |
| `scripts/cleanup_feishu_reports.py` | Housekeeping: prune old revisions in the Feishu drive folder. |
| `scripts/daytrace-{daily,weekly}.sh` | The launchd wrappers (set PATH, call the Python entry, log to `data/logs/`). |

## The orchestrator (channels.py)

Each unit of computed-and-cached state is a **channel** — a row in
`day_channel` or `day_project_channel`. A channel has:

- `name` — `time_span`, `ai_overview`, etc.
- `table` — `day` or `day_project`.
- `generator` — `stats` (cheap, recompute liberally) or `ai` (costs
  money, gated by `include_ai`).
- `version` — bump to invalidate cached rows across the fleet.
- `dependencies` — names of channels that must run first.
- `compute` — pure function returning the JSON value (and, for AI,
  token usage + cost).

`regenerate_day()` walks the registered channels in dependency order.
For each it checks whether a row exists with a matching events hash and
generator version; if yes, skip; if no, recompute and overwrite. Stats
channels recompute every time the day's events change. AI channels do
the same but the cost gate (`include_ai=False`) lets cron skip them in
status mode.

### Stats channels (always run)

| Channel | What it computes |
|---|---|
| `time_span`           | First / last event time of the shifted day |
| `active_minutes`      | Sum of 5-minute slots that had any event |
| `longest_focus_block` | Longest gap-free 5-min-slot run, with dominant source/project |
| `context_switches`    | Number of project transitions across slots |
| `peak_windows`        | Top hours by event count |
| `dimension_counts`    | Per (source, project, device, location) event counts |
| `quality`             | Counts of `sensitive` / `missing_project` rows |

Per-project versions live in `day_project_channel`: `time_span`,
`active_minutes`, `source_mix`, `device_mix`, `top_titles`,
`event_density`.

### AI channels (`include_ai=True`, costs ~$0.01-0.03/day)

| Channel | Cost | What it produces |
|---|---|---|
| `ai_overview`                 | ~5K in / ~1K out | `{headline, overview.narrative, trend, highlights, work_pattern, suggestions}` — the 3-column Insights panel + dashboard narrative. Sees: active task list, 7-day baseline, today's stats, full event list with `[task:X]` / `[proj:Y]` prefixes. |
| `ai_continuity_day`           | ~1K in / ~0.5K out | Today vs yesterday momentum chip + relation sentence. |
| `ai_project_summary_batch`    | ~6K in / ~2K out | One LLM call → dict `{project → summary}` for every active project. |
| `ai_project_continuity_batch` | ~3K in / ~1K out | Per-project momentum vs the project's previous active day. |
| `ai_activity_labels`          | ~5K in / ~2K out | Per-event activity label (free-form taxonomy capped at 5–10 categories/day). Writes to `event_activity_labels` table. |

All five channels run inside one `regenerate_day_from_db()` call.

## Multi-device hub model

One Mac is the hub. Other Linux/Windows-WSL machines are listed in
`config/remotes.yaml`. The daily pipeline:

1. `run_daily.py deploy` (optional, fast no-op if nothing changed):
   `rsync scripts/ daytrace/ config/` → every remote's `repo_path`.
2. `run_daily.py catchup` per `(remote, pending date)` pair:
   - SSH in, `cd repo_path`, `python scripts/collect_from_config.py
     --config <device.yaml> --date <date> --output inbox/<dev>/<date>/`.
   - `rsync` that slice back to the hub's `inbox/<dev>/<date>/`.
   - On the hub: `import_inbox.py inbox/<dev>/<date>/` → `events` table.
   - `regenerate_day_from_db(con, date, include_ai=True)`.
   - Failed (remote offline, ssh timeout) gets logged in `device_pull_log`
     and retried on the next run. Other remotes / days proceed.

## Dashboard

`dashboard/server.py` is a plain `http.server.BaseHTTPRequestHandler`,
no framework. Pages render HTML strings; APIs return JSON. All state
comes from `data/daytrace.sqlite`.

| Path | Returns |
|---|---|
| `/`              | Redirects to `/today` |
| `/today?date=…`  | Daily report card + chart + insights + per-day tasks + audit |
| `/weekly?week=…` | Weekly report card + chart + insights + swim/heat + per-day timeline + tasks + audit |
| `/events?…`      | Raw event browser (filterable) |
| `/sources`       | Per-source health |
| `/api/today`     | JSON view of `/today` data |
| `/api/events`    | JSON event query |
| `/api/summary`   | Top-of-day numbers |
| `POST /api/work-items/alias` | Persists audit-panel picks → `config/work_item_aliases.yaml` and calls `rebuild_links()` |

For delivery (Feishu Docs + email), see [setup.md](setup.md).
