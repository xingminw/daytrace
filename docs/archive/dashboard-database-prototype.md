# Dashboard & Database Prototype

## Direction

DayTrace now uses an event-based SQLite database as the first durable data layer.

The design is intentionally event-first:

```text
Connectors → TraceEvent JSONL → SQLite events table → Dashboard / API → Reports
```

Reasoning:

- Connectors naturally produce time-stamped activity events.
- Raw-ish events are useful for inspection before committing to report logic.
- LLM reports and statistical summaries can both be derived from the same event store.
- Corrections can later be modeled as separate events or rule tables without losing original evidence.

## Database

Path:

```text
data/daytrace.sqlite
```

Core tables:

```text
meta
runs
events
```

`events` columns:

```text
id TEXT PRIMARY KEY
date TEXT
source TEXT
kind TEXT
start TEXT
end TEXT
title TEXT
summary TEXT
project_guess TEXT
confidence REAL
sensitivity TEXT
evidence_json TEXT
raw_ref TEXT
inserted_at TEXT
```

Current imported prototype data:

```text
128 events
sources:
  hermes: 62
  docs: 58
  git: 7
  macos_activity: 1
```

## Dashboard

Local server:

```text
http://127.0.0.1:8765
```

Server process is started with:

```bash
python dashboard/server.py --db data/daytrace.sqlite --host 127.0.0.1 --port 8765
```

The dashboard shows:

- total events
- source count
- project count
- low-confidence count
- source distribution
- project distribution
- original event database table

## JSON APIs

```text
/api/summary?date=YYYY-MM-DD
/api/events?date=YYYY-MM-DD
/api/events?date=YYYY-MM-DD&source=git
/api/events?date=YYYY-MM-DD&project=daytrace
```

## Build / refresh database

```bash
DAY=$(date +%F)
python scripts/build_database.py \
  --date "$DAY" \
  --db data/daytrace.sqlite \
  --events "events/git-$DAY.jsonl" "events/docs-$DAY.jsonl" "events/hermes-$DAY.jsonl" "events/macos-$DAY.jsonl"
```

## Current verification

Commands run:

```bash
python -m compileall daytrace scripts dashboard
python -m pytest tests -q
python scripts/build_database.py --date 2026-05-13 --db data/daytrace.sqlite --events events/git-2026-05-13.jsonl events/docs-2026-05-13.jsonl events/hermes-2026-05-13.jsonl events/macos-2026-05-13.jsonl
```

Result:

```text
9 passed
Dashboard reachable at http://127.0.0.1:8765
/api/summary works
/api/events works
```

## Next design choices

1. Add corrections table:
   - manual project override
   - source ignore rules
   - sensitivity override
   - location correction
2. Add daily source health table.
3. Improve Hermes session event extraction.
4. Add a report generation layer that reads from SQLite instead of raw JSONL.
5. Add optional LLM summarization over grouped events.
