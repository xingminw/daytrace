from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable, Any

from .schema import TraceEvent

SCHEMA_VERSION = 5
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
        rows.append(
            (
                event.id,
                run_date or event_date(event),
                event.source,
                event.kind,
                event.start,
                event.end,
                event.title,
                event.summary,
                event.project_guess,
                event.sensitivity,
                json.dumps(event.evidence, ensure_ascii=False, sort_keys=True),
                event.raw_ref,
                event.device_id,
                event.location_id,
                event.collector_id,
            )
        )
    con.executemany(
        """
        INSERT INTO events(id, date, source, kind, start, end, title, summary, project_guess, sensitivity, evidence_json, raw_ref, device_id, location_id, collector_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
          collector_id=excluded.collector_id
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
        if project == "未归因":
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
        f"SELECT COALESCE({field}, '未归因') AS {label}, COUNT(*) AS count FROM events {where} GROUP BY COALESCE({field}, '未归因') ORDER BY count DESC, {label}",
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
        SELECT id, date, source, kind, start, end, title, summary, project_guess, sensitivity, evidence_json, raw_ref, device_id, location_id, collector_id
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
        d["project"] = d.get("project_guess") or "未归因"
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
            f"SELECT DISTINCT COALESCE(project_guess, '未归因') AS project FROM events {project_where} ORDER BY project",
            tuple(project_params),
        ),
    }


def query_timeline(con: sqlite3.Connection, date: str) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT substr(start, 12, 2) || ':00' AS hour,
               COUNT(*) AS count,
               COUNT(DISTINCT source) AS source_count,
               COUNT(DISTINCT COALESCE(project_guess, '未归因')) AS project_count,
               GROUP_CONCAT(DISTINCT source) AS sources
        FROM events
        WHERE date = ?
        GROUP BY substr(start, 12, 2)
        ORDER BY hour
        """,
        (date,),
    ).fetchall()
    return [dict(r) for r in rows]


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
