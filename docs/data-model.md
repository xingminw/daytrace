# DayTrace Data Model

Everything lives in one SQLite file (`data/daytrace.sqlite`, schema
version 12 as of 2026-05-18). Definitions are in `daytrace/db.py`;
migrations are in `init_db()`. Bump `SCHEMA_VERSION` when adding a
table or changing a column type.

## Tables

### `events` — the raw timeline
The fundamental object. Every collector ends up writing rows here.

| Column            | Type | Notes |
|---|---|---|
| `id`              | TEXT PK | Stable string ID (collector-specific format) |
| `date`            | TEXT    | YYYY-MM-DD shifted-day bucket (04:00 boundary) |
| `source`          | TEXT    | `claude_code`, `codex`, `git`, `hermes`, `docs`, … |
| `kind`            | TEXT    | Source-specific event subtype |
| `start`, `end`    | TEXT    | ISO timestamps |
| `title`, `summary`| TEXT    | What the event was |
| `project_guess`   | TEXT    | Collector's project attribution (nullable; AI fills "misc") |
| `sensitivity`     | TEXT    | `normal` / `private` / `sensitive` |
| `evidence_json`   | TEXT    | Whatever the collector wants to attach (file path, repo, commit SHA, …) |
| `raw_ref`         | TEXT    | Pointer to the raw source row (debugging) |
| `device_id`       | TEXT    | Which machine produced this (`Mac`, `omen-wsl`, …) |
| `location_id`     | TEXT    | Coarse where (`home`, `office`, `unknown`) |
| `collector_id`    | TEXT    | Which collector wrote it (`hub-local`, `ssh-omen-wsl-rsync`) |
| `inserted_at`     | TEXT    | DB insertion timestamp |

Indexes on `date`, `source`, `project_guess`, `start`.

### `day_report` — per-day summary header
One row per shifted day. Aggregate counts + the `events_hash` used by
the orchestrator to detect "events changed, recompute."

### `day_channel` — per-day computed JSON
The polyglot cache. Stats + AI compute write here. Schema:

| Column              | Notes |
|---|---|
| `date`              | PK component |
| `channel`           | PK component — e.g. `time_span`, `ai_overview` |
| `value_json`        | The JSON payload (channel-specific shape) |
| `generator`         | `stats` or `ai` |
| `generator_version` | Bump to invalidate cached rows |
| `source_hash`       | `events_hash` at compute time |
| `tokens_in/out/cost_usd` | For AI rows, accounting |
| `error`             | Last failure message (if any) |

Channels currently registered (see [architecture.md](architecture.md)
for what each computes):
`time_span`, `active_minutes`, `longest_focus_block`, `context_switches`,
`peak_windows`, `dimension_counts`, `quality`, `ai_overview`,
`ai_continuity_day`, `ai_project_summary_batch`,
`ai_project_continuity_batch`, `ai_activity_labels`.

### `day_project_report`, `day_project_channel`
Same idea, but the PK is `(date, project, channel)`. `project='misc'`
captures previously-NULL project_guess events. Channels:
`time_span`, `active_minutes`, `source_mix`, `device_mix`, `top_titles`,
`event_density`.

### `event_activity_labels`
One row per labeled event. Filled by the `ai_activity_labels` channel.
JOIN'd into the 活动 dim view.

### `work_items` — Feishu Bitable mirror
Multi-table (the `table_key` column tags which Bitable a row came
from — `tasks`, `reviews`, …). Read-only relative to Feishu; updates
flow one way: Feishu → DayTrace, on `run_daily.py work-items-sync`.

| Column | Notes |
|---|---|
| `record_id` PK | Feishu record id |
| `table_key`    | Which configured Bitable |
| `title`        | 任务名 / 题目 |
| `subtitle`, `status`, `priority`, `tags`, `due_date`, … | Standard work-item fields |
| `external_links` | JSON array of URLs (GitHub, Overleaf, doc URLs) |
| `raw_fields_json` | Full row dump for debugging |

### `event_work_item_links` — bridge table
`(event_id, record_id, match_type, confidence)`. `match_type` is one of:

- `github_url` — collector evidence URL matches a `work_items.external_links`
- `local_path` — local repo path matches a known overleaf / git project
- `alias`      — `config/work_item_aliases.yaml` mapped this `project_guess`
- `manual`     — added by audit-panel POST
- `ai`         — (future) LLM-assigned

`rebuild_links()` recomputes the whole table from scratch over a
configurable lookback window.

### `device_pull_log` — per-(device, date) catchup state
What `run_daily.py catchup` reads to know which `(remote, date)` pairs
still need pulling. If a remote was offline, the row stays in
`pending`; next run retries.

### Infrastructure tables
`meta`, `sources`, `source_rules`, `devices`, `locations`,
`event_corrections`, `ingest_runs`, `imported_files`, `runs` — mostly
prototype-era; minimal current use. `meta` carries the schema version
number for migrations.

## events_hash — how "stale" is detected

```python
events_hash = sha256(sorted(event.id for event in date_events))[:16]
```

Each `day_channel` row records the `source_hash` it was computed
against. The orchestrator skips a channel iff:

  - row exists AND
  - `source_hash` matches today's events_hash AND
  - `generator_version` matches the registered spec's version

Any mismatch → recompute and overwrite.

## Schema migrations

Lightweight. `init_db()` does:

1. `CREATE TABLE IF NOT EXISTS` for every table (idempotent).
2. `_ensure_column()` for every column added in a later version (uses
   `PRAGMA table_info` to detect, `ALTER TABLE ADD COLUMN` if missing).
3. Update `meta.schema_version`.

So upgrading is just `git pull` + `python -c "from daytrace.db import
connect, init_db; init_db(connect('data/daytrace.sqlite'))"` — or just
let the next dashboard/cron call do it.
