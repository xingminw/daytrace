from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable, Any

from .schema import TraceEvent

SCHEMA_VERSION = 11
DEFAULT_DEVICE_ID = "Mac"
DEFAULT_LOCATION_ID = "unknown"
DEFAULT_COLLECTOR_ID = "hub-local"
DEFAULT_SOURCES = {
    "codex": "Codex",
    "docs": "Docs",
    "git": "Git",
    "github": "GitHub",
    "hermes": "Hermes",
    "ios_shortcuts": "iOS Shortcuts",
    "macos-activity": "macOS Activity",
    "macos_activity": "macOS Activity",
}

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  connection_type TEXT,
  upload_path TEXT,
  last_ingest_at TEXT,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS source_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT NOT NULL,
  rule_type TEXT NOT NULL,
  rule_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(source_id) REFERENCES sources(id)
);
CREATE TABLE IF NOT EXISTS devices (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS locations (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS event_corrections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL,
  field TEXT NOT NULL,
  old_value TEXT,
  new_value TEXT,
  note TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  date TEXT NOT NULL,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  start TEXT NOT NULL,
  end TEXT,
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  project_guess TEXT,
  sensitivity TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  raw_ref TEXT,
  device_id TEXT NOT NULL DEFAULT 'Mac',
  location_id TEXT NOT NULL DEFAULT 'unknown',
  collector_id TEXT NOT NULL DEFAULT 'hub-local',
  inserted_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(date);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(source);
CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_guess);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start);
CREATE TABLE IF NOT EXISTS ingest_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_type TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT,
  file_count INTEGER NOT NULL DEFAULT 0,
  event_count INTEGER NOT NULL DEFAULT 0,
  error_count INTEGER NOT NULL DEFAULT 0,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS imported_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT NOT NULL,
  archive_path TEXT,
  sha256 TEXT NOT NULL,
  source_device TEXT,
  batch_date TEXT,
  status TEXT NOT NULL,
  event_count INTEGER NOT NULL DEFAULT 0,
  ingest_run_id INTEGER,
  error TEXT,
  imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(sha256),
  FOREIGN KEY(ingest_run_id) REFERENCES ingest_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_imported_files_sha256 ON imported_files(sha256);
CREATE INDEX IF NOT EXISTS idx_imported_files_status ON imported_files(status);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  event_count INTEGER NOT NULL,
  notes TEXT
);
-- One row per date. Denormalized columns expose the common stats so cross-
-- day SQL queries don't have to crack the channel JSON.
CREATE TABLE IF NOT EXISTS day_report (
  date            TEXT PRIMARY KEY,
  events_hash     TEXT,
  total_events    INTEGER NOT NULL DEFAULT 0,
  active_minutes  INTEGER NOT NULL DEFAULT 0,
  updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS day_channel (
  date              TEXT NOT NULL,
  channel           TEXT NOT NULL,
  value_json        TEXT,
  generator         TEXT NOT NULL,
  generator_version TEXT NOT NULL,
  source_hash       TEXT,
  generated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
  tokens_in         INTEGER NOT NULL DEFAULT 0,
  tokens_out        INTEGER NOT NULL DEFAULT 0,
  cost_usd          REAL NOT NULL DEFAULT 0,
  error             TEXT,
  PRIMARY KEY (date, channel),
  FOREIGN KEY (date) REFERENCES day_report(date) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_day_channel_channel ON day_channel(channel);
-- One row per (date, project). project='misc' captures previously NULL projects.
CREATE TABLE IF NOT EXISTS day_project_report (
  date            TEXT NOT NULL,
  project         TEXT NOT NULL,
  events_hash     TEXT,
  event_count     INTEGER NOT NULL DEFAULT 0,
  active_minutes  INTEGER NOT NULL DEFAULT 0,
  share           REAL NOT NULL DEFAULT 0,
  updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (date, project),
  FOREIGN KEY (date) REFERENCES day_report(date) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_day_project_report_project ON day_project_report(project);
CREATE TABLE IF NOT EXISTS day_project_channel (
  date              TEXT NOT NULL,
  project           TEXT NOT NULL,
  channel           TEXT NOT NULL,
  value_json        TEXT,
  generator         TEXT NOT NULL,
  generator_version TEXT NOT NULL,
  source_hash       TEXT,
  generated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
  tokens_in         INTEGER NOT NULL DEFAULT 0,
  tokens_out        INTEGER NOT NULL DEFAULT 0,
  cost_usd          REAL NOT NULL DEFAULT 0,
  error             TEXT,
  PRIMARY KEY (date, project, channel),
  FOREIGN KEY (date, project) REFERENCES day_project_report(date, project) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_day_project_channel_channel ON day_project_channel(channel);
-- Per-event activity label. AI fills it in batch; `source='manual'` reserved
-- for future user override. Lives in its own table (not in events.evidence)
-- so it's cheap to JOIN and to UPDATE without touching the immutable event row.
CREATE TABLE IF NOT EXISTS event_activity_labels (
  event_id     TEXT PRIMARY KEY,
  label        TEXT NOT NULL,
  source       TEXT NOT NULL DEFAULT 'ai',  -- 'ai' | 'manual'
  confidence   REAL NOT NULL DEFAULT 0.0,
  model        TEXT,
  assigned_at  TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_event_activity_labels_label ON event_activity_labels(label);
-- Per-(device, shifted-day) pull bookkeeping for the SSH-direct catchup loop.
-- Hub records every attempted pull from a remote device; pending_dates uses
-- this to detect "remote was offline that day, must retry" independently of
-- the events_hash signal (which can't tell missing data from "no events").
-- date is the shifted day the pull was for (NOT the wall-clock day we ran).
CREATE TABLE IF NOT EXISTS device_pull_log (
  device_id        TEXT NOT NULL,
  date             TEXT NOT NULL,            -- shifted day, YYYY-MM-DD
  last_attempt_at  TEXT NOT NULL,
  last_success_at  TEXT,                     -- NULL until first success
  last_event_count INTEGER,
  last_error       TEXT,                     -- NULL on success
  PRIMARY KEY (device_id, date)
);
CREATE INDEX IF NOT EXISTS idx_device_pull_log_date ON device_pull_log(date);
-- Local snapshot of the Feishu "任务" Bitable (work items). DayTrace is a
-- read-only observer: this table is rewritten each sync from the upstream
-- snapshot; we never write back to Feishu in v11.
CREATE TABLE IF NOT EXISTS work_items (
  record_id        TEXT PRIMARY KEY,            -- 飞书 record id
  title            TEXT NOT NULL,               -- 任务
  status           TEXT,                        -- 待办 / 进行中 / 完成
  priority         TEXT,                        -- P0..P3
  tags             TEXT,                        -- JSON array
  project_source   TEXT,                        -- 项目来源 (free text)
  external_links   TEXT,                        -- JSON array of URLs
  due_date         TEXT,                        -- 截止时间 (YYYY-MM-DD)
  next_action_date TEXT,                        -- 下一步时间
  weekly_hours     REAL,                        -- 每周预计投入
  next_action      TEXT,                        -- 下一步动作
  agent_workspace  TEXT,                        -- Agent 工作区 (kept read-only)
  last_synced_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  raw_fields_json  TEXT                         -- full row dump for debugging
);
CREATE INDEX IF NOT EXISTS idx_work_items_status ON work_items(status);
CREATE INDEX IF NOT EXISTS idx_work_items_priority ON work_items(priority);
-- (event, work_item) bridge — multiple match strategies stamp distinct rows;
-- the consumer picks the highest-confidence link per event.
CREATE TABLE IF NOT EXISTS event_work_item_links (
  event_id   TEXT NOT NULL,
  record_id  TEXT NOT NULL,
  match_type TEXT NOT NULL,              -- github_url | local_path | alias | manual | ai
  confidence REAL NOT NULL DEFAULT 0.9,
  matched_at TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (event_id, record_id),
  FOREIGN KEY (event_id)  REFERENCES events(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ewil_record ON event_work_item_links(record_id);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def _ensure_column(con: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {
        row["name"] for row in con.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _drop_column_if_exists(con: sqlite3.Connection, table: str, column: str) -> None:
    columns = {
        row["name"] for row in con.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column in columns:
        con.execute(f"ALTER TABLE {table} DROP COLUMN {column}")


def seed_single_machine_defaults(con: sqlite3.Connection) -> None:
    for source_id, name in DEFAULT_SOURCES.items():
        con.execute(
            """
            INSERT OR IGNORE INTO sources(id, name, status, connection_type)
            VALUES (?, ?, ?, ?)
            """,
            (source_id, name, "active", "local"),
        )
    con.execute("DELETE FROM devices WHERE id IN (?, ?)", ("mac-hermes", "mac"))
    con.execute(
        "INSERT OR REPLACE INTO devices(id, name, type, status) VALUES (?, ?, ?, ?)",
        (DEFAULT_DEVICE_ID, "Mac", "mac", "active"),
    )
    con.execute(
        "INSERT OR IGNORE INTO locations(id, name, kind, status) VALUES (?, ?, ?, ?)",
        (DEFAULT_LOCATION_ID, "Unknown", "fallback", "active"),
    )


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA_SQL)
    # Lightweight migration for databases created by the prototype schema.
    _ensure_column(
        con,
        "events",
        "device_id",
        f"device_id TEXT NOT NULL DEFAULT '{DEFAULT_DEVICE_ID}'",
    )
    _ensure_column(
        con,
        "events",
        "location_id",
        f"location_id TEXT NOT NULL DEFAULT '{DEFAULT_LOCATION_ID}'",
    )
    _ensure_column(
        con,
        "events",
        "collector_id",
        f"collector_id TEXT NOT NULL DEFAULT '{DEFAULT_COLLECTOR_ID}'",
    )
    # char_count: cached length(title)+length(summary), used by the global
    # "条目 / 字数" unit toggle so per-source/-project aggregations can be
    # weighted by content size, not just event count. Backfilled lazily.
    _ensure_column(con, "events", "char_count", "char_count INTEGER NOT NULL DEFAULT 0")
    con.execute(
        "UPDATE events SET char_count = COALESCE(LENGTH(title), 0) + COALESCE(LENGTH(summary), 0)"
        " WHERE char_count = 0"
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_events_device ON events(device_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_events_location ON events(location_id)")
    con.execute(
        "UPDATE events SET device_id = ? WHERE device_id IN (?, ?)",
        (DEFAULT_DEVICE_ID, "mac-hermes", "mac"),
    )
    _drop_column_if_exists(con, "events", "category")
    _drop_column_if_exists(con, "events", "confidence")
    seed_single_machine_defaults(con)
    con.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION)),
    )
    con.commit()


def event_date(event: TraceEvent) -> str:
    return event.start[:10]


def upsert_events(
    con: sqlite3.Connection,
    events: Iterable[TraceEvent],
    run_date: str | None = None,
    *,
    commit: bool = True,
) -> int:
    rows = []
    for event in events:
        con.execute(
            "INSERT OR IGNORE INTO sources(id, name, status, connection_type) VALUES (?, ?, ?, ?)",
            (event.source, event.source, "active", "batch"),
        )
        con.execute(
            "INSERT OR IGNORE INTO devices(id, name, type, status) VALUES (?, ?, ?, ?)",
            (event.device_id, event.device_id, "branch", "active"),
        )
        con.execute(
            "INSERT OR IGNORE INTO locations(id, name, kind, status) VALUES (?, ?, ?, ?)",
            (event.location_id, event.location_id, "branch", "active"),
        )
        # Defensive: collectors that emit "YYYY-MM-DD HH:MM:SS" (space) break
        # our lexicographic range filters because ' ' (0x20) < 'T' (0x54).
        # Normalize on the write path so the column is always canonical.
        canon_start = event.start
        if canon_start and len(canon_start) >= 11 and canon_start[10] == " ":
            canon_start = canon_start[:10] + "T" + canon_start[11:]
        canon_end = event.end
        if canon_end and len(canon_end) >= 11 and canon_end[10] == " ":
            canon_end = canon_end[:10] + "T" + canon_end[11:]
        rows.append(
            (
                event.id,
                run_date or event_date(event),
                event.source,
                event.kind,
                canon_start,
                canon_end,
                event.title,
                event.summary,
                event.project_guess,
                event.sensitivity,
                json.dumps(event.evidence, ensure_ascii=False, sort_keys=True),
                event.raw_ref,
                event.device_id,
                event.location_id,
                event.collector_id,
                len(event.title or "") + len(event.summary or ""),
            )
        )
    con.executemany(
        """
        INSERT INTO events(id, date, source, kind, start, end, title, summary, project_guess, sensitivity, evidence_json, raw_ref, device_id, location_id, collector_id, char_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          date=excluded.date,
          source=excluded.source,
          kind=excluded.kind,
          start=excluded.start,
          end=excluded.end,
          title=excluded.title,
          summary=excluded.summary,
          project_guess=excluded.project_guess,
          sensitivity=excluded.sensitivity,
          evidence_json=excluded.evidence_json,
          raw_ref=excluded.raw_ref,
          device_id=excluded.device_id,
          location_id=excluded.location_id,
          collector_id=excluded.collector_id,
          char_count=excluded.char_count
        """,
        rows,
    )
    if run_date is not None:
        con.execute(
            "INSERT INTO runs(date, event_count, notes) VALUES (?, ?, ?)",
            (run_date, len(rows), "prototype import"),
        )
    if commit:
        con.commit()
    return len(rows)


def _where(filters: dict[str, Any]) -> tuple[str, list[Any]]:
    clauses = []
    params = []
    for key in ("date", "source", "kind", "device_id", "location_id"):
        value = filters.get(key)
        if value:
            clauses.append(f"{key} = ?")
            params.append(value)
    source_in = filters.get("source_in")
    if source_in:
        placeholders = ",".join("?" for _ in source_in)
        clauses.append(f"source IN ({placeholders})")
        params.extend(source_in)
    start_from = filters.get("start_from")
    if start_from:
        clauses.append("start >= ?")
        params.append(start_from)
    start_to = filters.get("start_to")
    if start_to:
        clauses.append("start <= ?")
        params.append(start_to)
    project = filters.get("project")
    if project:
        if project == "misc":
            clauses.append("project_guess IS NULL")
        else:
            clauses.append("project_guess = ?")
            params.append(project)
    if filters.get("low_confidence"):
        clauses.append("project_guess IS NULL")
    search = filters.get("search")
    if search:
        clauses.append("(title LIKE ? OR summary LIKE ? OR evidence_json LIKE ?)")
        needle = f"%{search}%"
        params.extend([needle, needle, needle])
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    return where, params


def _count_by(
    con: sqlite3.Connection, field: str, label: str, where: str, params: list[Any]
) -> list[dict[str, Any]]:
    rows = con.execute(
        f"SELECT COALESCE({field}, 'misc') AS {label}, COUNT(*) AS count FROM events {where} GROUP BY COALESCE({field}, 'misc') ORDER BY count DESC, {label}",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def query_summary(con: sqlite3.Connection, date: str | None = None) -> dict[str, Any]:
    where, params = _where({"date": date})
    total = con.execute(f"SELECT COUNT(*) AS c FROM events {where}", params).fetchone()[
        "c"
    ]
    sources = _count_by(con, "source", "source", where, params)
    projects = _count_by(con, "project_guess", "project", where, params)
    devices = _count_by(con, "device_id", "device_id", where, params)
    locations = _count_by(con, "location_id", "location_id", where, params)
    low = con.execute(
        f"SELECT COUNT(*) AS c FROM events {where + (' AND' if where else 'WHERE')} project_guess IS NULL",
        params,
    ).fetchone()["c"]
    return {
        "date": date,
        "total_events": total,
        "sources": sources,
        "projects": projects,
        "devices": devices,
        "locations": locations,
        "low_confidence": low,
    }


def query_events(
    con: sqlite3.Connection,
    date: str | None = None,
    source: str | None = None,
    project: str | None = None,
    kind: str | None = None,
    device_id: str | None = None,
    location_id: str | None = None,
    low_confidence: bool = False,
    search: str | None = None,
    source_in: list[str] | None = None,
    start_from: str | None = None,
    start_to: str | None = None,
    limit: int | None = 500,
    order: str = "desc",
) -> list[dict[str, Any]]:
    where, params = _where(
        {
            "date": date,
            "source": source,
            "project": project,
            "kind": kind,
            "device_id": device_id,
            "location_id": location_id,
            "low_confidence": low_confidence,
            "source_in": source_in,
            "start_from": start_from,
            "start_to": start_to,
            "search": search,
        }
    )
    direction = "ASC" if order.lower() == "asc" else "DESC"
    sql = f"""
        SELECT id, date, source, kind, start, end, title, summary, project_guess, sensitivity, evidence_json, raw_ref, device_id, location_id, collector_id, char_count
        FROM events
        {where}
        ORDER BY start {direction}, source, kind
        {"LIMIT ?" if limit is not None else ""}
        """
    query_params = (*params, limit) if limit is not None else tuple(params)
    rows = con.execute(sql, query_params).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        try:
            d["evidence"] = json.loads(d.pop("evidence_json") or "{}")
        except Exception:
            d["evidence"] = {}
        d["project"] = d.get("project_guess") or "misc"
        out.append(d)
    return out


def query_filter_options(
    con: sqlite3.Connection, filters: dict[str, Any] | None = None
) -> dict[str, list[dict[str, str]]]:
    filters = filters or {}

    def options(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, str]]:
        rows = con.execute(sql, params).fetchall()
        return [{"value": "", "label": "All"}] + [
            {"value": str(row[0]), "label": str(row[0])}
            for row in rows
            if row[0] is not None and str(row[0]) != ""
        ]

    project_filters = {
        "date": filters.get("date"),
        "source": filters.get("source"),
        "source_in": filters.get("source_in"),
        "device_id": filters.get("device_id"),
        "location_id": filters.get("location_id"),
        "start_from": filters.get("start_from"),
        "start_to": filters.get("start_to"),
        "search": filters.get("search"),
    }
    project_where, project_params = _where(project_filters)
    source_filters = {
        "date": filters.get("date"),
        "source_in": filters.get("source_in"),
        "start_from": filters.get("start_from"),
        "start_to": filters.get("start_to"),
        "project": filters.get("project"),
        "search": filters.get("search"),
    }
    source_where, source_params = _where(source_filters)

    return {
        "date": options("SELECT DISTINCT date FROM events ORDER BY date DESC"),
        "source": options(
            f"SELECT DISTINCT source FROM events {source_where} ORDER BY source",
            tuple(source_params),
        ),
        "device_id": options(
            "SELECT DISTINCT device_id FROM events ORDER BY device_id"
        ),
        "location_id": options(
            "SELECT DISTINCT location_id FROM events ORDER BY location_id"
        ),
        "project": options(
            f"SELECT DISTINCT COALESCE(project_guess, 'misc') AS project FROM events {project_where} ORDER BY project",
            tuple(project_params),
        ),
    }


def query_timeline(con: sqlite3.Connection, date: str) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT substr(start, 12, 2) || ':00' AS hour,
               COUNT(*) AS count,
               COUNT(DISTINCT source) AS source_count,
               COUNT(DISTINCT COALESCE(project_guess, 'misc')) AS project_count,
               GROUP_CONCAT(DISTINCT source) AS sources
        FROM events
        WHERE date = ?
        GROUP BY substr(start, 12, 2)
        ORDER BY hour
        """,
        (date,),
    ).fetchall()
    return [dict(r) for r in rows]


def events_for_shifted_day(
    con: sqlite3.Connection,
    date: str,
    *,
    boundary_hour: int | None = None,
    order: str = "asc",
    limit: int | None = 2000,
) -> list[dict[str, Any]]:
    """Return events for "the day named `date`" under a shifted-boundary
    day definition.

    A day named "YYYY-MM-DD" is the half-open interval
    [date T HH:00, next_date T HH:00) where HH = boundary_hour.

    Default boundary comes from `daytrace.stats.DAY_BOUNDARY_HOUR` (env var
    DAYTRACE_DAY_BOUNDARY_HOUR, default 4). Passing boundary_hour=0 collapses
    to the legacy calendar-day behavior.
    """
    if boundary_hour is None:
        from . import stats as _stats
        boundary_hour = _stats.DAY_BOUNDARY_HOUR
    from datetime import date as _date, timedelta
    base = _date.fromisoformat(date)
    if boundary_hour == 0:
        # Legacy calendar-day: [00:00, 23:59:59] of the named date.
        start = f"{date}T00:00:00"
        end = f"{date}T23:59:59"
    else:
        next_date = (base + timedelta(days=1)).isoformat()
        start = f"{date}T{boundary_hour:02d}:00:00"
        # query_events uses inclusive `start <= ?`; subtract one second to
        # keep the window half-open (no event lands in two days).
        end = f"{next_date}T{boundary_hour - 1:02d}:59:59"
    return query_events(
        con, start_from=start, start_to=end, order=order, limit=limit,
    )


def iso_week_to_date_range(week_label: str) -> tuple[str, str, list[str]]:
    """Parse an ISO week label like '2026-W20' → (monday_iso, sunday_iso, [7 day ISOs]).

    The ISO week starts Monday. We return calendar dates here; callers using
    the shifted-day boundary will pass each date through events_for_shifted_day.
    """
    from datetime import date as _date, timedelta
    if "-W" not in week_label:
        raise ValueError(f"expected YYYY-Www, got {week_label!r}")
    year_str, wk_str = week_label.split("-W", 1)
    year, week = int(year_str), int(wk_str)
    monday = _date.fromisocalendar(year, week, 1)
    days = [(monday + timedelta(days=i)).isoformat() for i in range(7)]
    return days[0], days[-1], days


def date_to_iso_week(d: str) -> str:
    """'2026-05-18' → '2026-W21'."""
    from datetime import date as _date
    y, w, _ = _date.fromisoformat(d).isocalendar()
    return f"{y}-W{w:02d}"


def iso_week_neighbors(week_label: str) -> tuple[str, str]:
    """Return (prev_week_label, next_week_label) for nav links."""
    from datetime import timedelta
    monday, _, _ = iso_week_to_date_range(week_label)
    from datetime import date as _date
    m = _date.fromisoformat(monday)
    prev = (m - timedelta(days=7))
    nxt = (m + timedelta(days=7))
    py, pw, _ = prev.isocalendar()
    ny, nw, _ = nxt.isocalendar()
    return f"{py}-W{pw:02d}", f"{ny}-W{nw:02d}"


def events_for_shifted_week(
    con: sqlite3.Connection,
    week_label: str,
    *,
    boundary_hour: int | None = None,
    limit: int | None = 20000,
) -> list[dict[str, Any]]:
    """All events whose shifted-day falls inside the 7 calendar dates of the
    ISO week. Concatenation of events_for_shifted_day for the 7 days."""
    _, _, days = iso_week_to_date_range(week_label)
    out: list[dict] = []
    per_day_cap = (limit // 7) if limit else None
    for d in days:
        out.extend(events_for_shifted_day(
            con, d, boundary_hour=boundary_hour, limit=per_day_cap,
        ))
    return out


def load_activity_labels_for_event_ids(
    con: sqlite3.Connection, event_ids: list[str], *, chunk: int = 900
) -> dict[str, str]:
    """Return {event_id: label} for the given event ids. Empty on schema race."""
    if not event_ids:
        return {}
    out: dict[str, str] = {}
    unique = list({eid for eid in event_ids if eid})
    try:
        for start in range(0, len(unique), chunk):
            sub = unique[start:start + chunk]
            ph = ",".join("?" for _ in sub)
            for r in con.execute(
                f"SELECT event_id, label FROM event_activity_labels WHERE event_id IN ({ph})",
                sub,
            ).fetchall():
                out[r["event_id"]] = r["label"]
    except sqlite3.OperationalError:
        return {}
    return out


def load_activity_labels_for_date(con: sqlite3.Connection, date: str) -> dict[str, str]:
    """Return {event_id: label} for all labeled events on the given date.

    Events without labels are simply omitted; callers default missing values
    to '未分类'. Falls back to empty dict on schema/migration races."""
    try:
        rows = con.execute(
            "SELECT eal.event_id, eal.label FROM event_activity_labels eal"
            " JOIN events e ON e.id = eal.event_id WHERE e.date = ?",
            (date,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {r["event_id"]: r["label"] for r in rows}


def upsert_activity_labels(
    con: sqlite3.Connection,
    rows: list[dict[str, Any]],
    *,
    commit: bool = True,
) -> int:
    """Insert / overwrite labels. `rows`: list of {event_id, label, source?, confidence?, model?}."""
    if not rows:
        return 0
    con.executemany(
        """
        INSERT INTO event_activity_labels(event_id, label, source, confidence, model, assigned_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(event_id) DO UPDATE SET
          label=excluded.label,
          source=excluded.source,
          confidence=excluded.confidence,
          model=excluded.model,
          assigned_at=CURRENT_TIMESTAMP
        """,
        [
            (
                r["event_id"],
                r["label"],
                r.get("source") or "ai",
                float(r.get("confidence") or 0.0),
                r.get("model"),
            )
            for r in rows
        ],
    )
    if commit:
        con.commit()
    return len(rows)


def query_today(con: sqlite3.Connection, date: str) -> dict[str, Any]:
    summary = query_summary(con, date)
    all_events = query_events(con, date=date, limit=2000, order="asc")
    timeline = []
    for bucket in query_timeline(con, date):
        hour = bucket["hour"]
        bucket_events = [e for e in all_events if e["start"][11:13] == hour[:2]][:5]
        bucket["events"] = bucket_events
        bucket["sources"] = [s for s in (bucket.get("sources") or "").split(",") if s]
        timeline.append(bucket)
    needs_review = query_events(
        con, date=date, low_confidence=True, limit=10, order="asc"
    )
    return {
        "date": date,
        "summary": summary,
        "timeline": timeline,
        "by_source": summary["sources"],
        "by_project": summary["projects"],
        "by_device": summary["devices"],
        "by_location": summary["locations"],
        "needs_review": needs_review,
    }
