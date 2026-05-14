# DayTrace Next Plan

## Current state

DayTrace already has a working local prototype:

- local collectors for Git, docs, Hermes sessions, and basic macOS activity;
- JSONL event files under `events/`;
- SQLite event database at `data/daytrace.sqlite`;
- localhost dashboard at `http://127.0.0.1:8765`;
- daily Feishu push cron script;
- documentation for connection inventory, prototype run, dashboard/database, frontend QA, and multi-device sync.

The product direction is now:

```text
1. 今天做了啥
2. 来源是啥
3. 原始的数据库
```

With cross-cutting dimensions:

```text
source + device + location + project + confidence
```

Multi-device sync direction:

```text
branch collectors/devices → Feishu Drive inbox → Hermes Mac Hub → SQLite → Portal/Report/Feishu push
```

## What has accumulated

### Product / UX backlog

1. Convert the single dashboard into three main interfaces:
   - Today: `今天做了啥`
   - Sources: `来源是啥`
   - Events: `原始的数据库`
2. Add source board cards:
   - status;
   - events today;
   - last ingest;
   - health;
   - filtering rules;
   - collector device;
   - upload path;
   - error state.
3. Add event filtering and sorting:
   - date;
   - source;
   - device;
   - location;
   - project;
   - kind;
   - confidence;
   - needs review;
   - text search.
4. Add event detail view:
   - full event JSON;
   - evidence;
   - inferred project/device/location;
   - future correction actions.
5. Improve Today panel:
   - top projects;
   - key artifacts;
   - timeline;
   - low-confidence review list;
   - Feishu summary preview.

### Data model backlog

1. Extend SQLite beyond `events`:
   - `sources`
   - `source_rules`
   - `ingest_runs`
   - `devices`
   - `locations`
   - `imported_files`
   - `event_corrections`
2. Extend event schema:
   - `event_id`
   - `dedupe_key`
   - `source_id`
   - `device_id`
   - `collector_id`
   - `location_id`
   - `occurred_at`
   - `collected_at`
   - `ingested_at`
   - `schema_version`
3. Add default dimensions now:
   - device: `mac-hermes`
   - location: `unknown`
   - collector: `hub-local`

### Sync / Hub backlog

1. Implement local Feishu Drive inbox simulator first:
   - `inbox/<device>/<date>/*.jsonl`
2. Implement `scripts/import_inbox.py`:
   - scan batch files;
   - validate events;
   - compute sha256;
   - skip imported files;
   - dedupe events;
   - insert SQLite;
   - record ingest runs.
3. Add Feishu Drive adapter later:
   - list files;
   - download batch;
   - upload/archive/move;
   - cleanup after import.
4. Define cleanup policy:
   - raw archive retention: 7 days;
   - errors retained until review;
   - canonical SQLite retained.

### Collector backlog

1. Update existing collectors to emit device/location metadata.
2. Convert current local event files into inbox-style immutable batches.
3. Improve Hermes session collector:
   - remove context compaction/system noise;
   - extract decisions/artifacts/projects more cleanly.
4. Improve macOS activity collector:
   - background sampling;
   - optional window title if Accessibility permission is granted.
5. Add future iPhone/location collector path:
   - initially via manual JSONL/sample batch;
   - later via Feishu Drive upload or shortcut.

### Delivery backlog

1. Update daily cron to import from inbox and rebuild database before generating report.
2. Include dashboard link and source health in Feishu push.
3. Keep Feishu push concise; use dashboard for detail.

## Recommended execution order

### Phase 1 — Make database future-proof

Goal: add source/device/location/import ledgers without changing product behavior too much.

Tasks:

1. Add SQLite tables:
   - `sources`
   - `source_rules`
   - `ingest_runs`
   - `devices`
   - `locations`
   - `imported_files`
   - `event_corrections`
2. Add migration/bootstrap logic in `daytrace/db.py`.
3. Backfill existing events with:
   - `source_id` from current source;
   - `device_id = mac-hermes`;
   - `location_id = unknown`;
   - `collector_id = hub-local`;
   - generated `dedupe_key` where missing.
4. Add tests for schema creation and idempotent imports.

Verification:

```bash
python -m pytest tests -q
python scripts/build_database.py --date $(date +%F) --db data/daytrace.sqlite ...
sqlite3 data/daytrace.sqlite '.tables'
```

### Phase 2 — Turn dashboard into three interfaces

Goal: make the portal match the product structure.

Tasks:

1. Add navigation:
   - `/today`
   - `/sources`
   - `/events`
   - `/api/today`
   - `/api/sources`
   - `/api/events`
2. Move current event table to `/events`.
3. Add source board at `/sources`.
4. Add overview/today page at `/today`.
5. Add filters to `/events`:
   - source;
   - device;
   - location;
   - project;
   - kind;
   - low confidence;
   - search.

Verification:

```bash
python dashboard/server.py --db data/daytrace.sqlite --port 8765
open http://127.0.0.1:8765/today
open http://127.0.0.1:8765/sources
open http://127.0.0.1:8765/events
```

### Phase 3 — Implement Hub inbox import

Goal: make multi-device branch → hub sync real, starting with local simulation.

Tasks:

1. Create local inbox layout:
   - `inbox/mac-hermes/YYYY-MM-DD/*.jsonl`
   - `inbox/iphone/YYYY-MM-DD/*.jsonl`
   - `inbox/cloud/YYYY-MM-DD/*.jsonl`
2. Implement `scripts/export_local_batches.py` or update collectors to write immutable batches.
3. Implement `scripts/import_inbox.py`.
4. Add `imported_files` and `ingest_runs` records.
5. Update dashboard Source Board to show ingest state.

Verification:

```bash
python scripts/import_inbox.py --inbox inbox --db data/daytrace.sqlite
sqlite3 data/daytrace.sqlite 'select * from imported_files limit 5;'
```

### Phase 4 — Feishu Drive adapter

Goal: replace local inbox simulation with Feishu Drive inbox.

Tasks:

1. Confirm Feishu Drive API permissions.
2. Create/locate DayTrace folder in Feishu Drive.
3. Implement Feishu Drive list/download/upload/move wrapper.
4. Add `scripts/import_feishu_drive_inbox.py`.
5. Add cleanup/archive step after successful import.

Verification:

```bash
python scripts/import_feishu_drive_inbox.py --dry-run
python scripts/import_feishu_drive_inbox.py --db data/daytrace.sqlite
```

### Phase 5 — Daily pipeline integration

Goal: one daily push that uses the Hub model.

Tasks:

1. Update `~/.hermes/scripts/daytrace_daily_push.sh`:
   - import inbox first;
   - run local collectors;
   - rebuild/refresh SQLite;
   - generate report;
   - push Feishu summary.
2. Include dashboard URLs:
   - `/today`
   - `/sources`
   - `/events`
3. Add cron status/health note.

Verification:

```bash
~/.hermes/scripts/daytrace_daily_push.sh
```

## Immediate next task

Start with Phase 1.

Why:

- dashboard/source/inbox work depends on the data model;
- adding device/location/import ledgers now prevents painful refactor;
- existing prototype remains usable while schema expands.

Concrete first task:

```text
Modify daytrace/db.py and tests/test_db.py to add sources/devices/locations/imported_files/ingest_runs/event_corrections tables and default seed rows.
```

## Commit policy

Local edits are allowed. Do not run `git commit` or `git push` without explicit user approval.
