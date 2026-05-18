"""Public entrypoint for the daily report pipeline.

Importing this module triggers AI channel registration as a side-effect
(via `import daytrace.ai_report`), so callers get the full registry
without having to remember which sub-modules to import.

Typical use:

    from daytrace.db import connect, init_db
    from daytrace.daily_report import regenerate_day_from_db, load_day_report

    con = connect(db_path); init_db(con)
    regenerate_day_from_db(con, "2026-05-15", include_ai=True)
    payload = load_day_report(con, "2026-05-15")
    # payload = { "stats": {...}, "ai": {...}, "projects": [...] }
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import ai_report  # noqa: F401 — side-effect: registers AI channels
from .channels import (
    DAY_CHANNELS,
    PROJECT_CHANNELS,
    RegenerationReport,
    regenerate_day,
)
from .db import query_events


def regenerate_day_from_db(
    con: sqlite3.Connection,
    date: str,
    *,
    force: bool = False,
    include_ai: bool = True,
    boundary_hour: int | None = None,
) -> RegenerationReport:
    """Pull the day's events from the DB and run the full orchestrator.

    Uses the shifted-day window (default 04:00 → 04:00) so the cached stats
    and AI rows reflect what `today_page` shows. Pass boundary_hour=0 to
    force the legacy calendar-day window (used by tests).
    """
    from .db import events_for_shifted_day
    events = events_for_shifted_day(
        con, date, boundary_hour=boundary_hour, order="asc", limit=None,
    )
    return regenerate_day(con, date, events, force=force, include_ai=include_ai)


def load_day_report(con: sqlite3.Connection, date: str) -> dict[str, Any]:
    """Read the structured report for one day. Cheap; doesn't recompute.

    Returns a dict with three top-level keys:
      - day:      header + flat dict of {channel_name: value_json}
      - projects: list of {project, header_fields, channels: {...}}
      - meta:     where each channel came from (generator, version, hash)
    """
    header = con.execute(
        "SELECT date, events_hash, total_events, active_minutes, updated_at"
        " FROM day_report WHERE date = ?",
        (date,),
    ).fetchone()
    if header is None:
        return {"day": None, "projects": [], "meta": {}}

    day_channels: dict[str, Any] = {}
    day_meta: dict[str, dict[str, Any]] = {}
    for row in con.execute(
        "SELECT channel, value_json, generator, generator_version, source_hash,"
        " generated_at, error FROM day_channel WHERE date = ?",
        (date,),
    ).fetchall():
        day_channels[row["channel"]] = _maybe_json(row["value_json"])
        day_meta[row["channel"]] = {
            "generator": row["generator"],
            "version": row["generator_version"],
            "source_hash": row["source_hash"],
            "generated_at": row["generated_at"],
            "error": row["error"],
        }

    projects: list[dict[str, Any]] = []
    for prow in con.execute(
        "SELECT project, events_hash, event_count, active_minutes, share, updated_at"
        " FROM day_project_report WHERE date = ? ORDER BY event_count DESC",
        (date,),
    ).fetchall():
        proj = dict(prow)
        proj["channels"] = {}
        proj["meta"] = {}
        for row in con.execute(
            "SELECT channel, value_json, generator, generator_version, source_hash,"
            " generated_at, error FROM day_project_channel"
            " WHERE date = ? AND project = ?",
            (date, prow["project"]),
        ).fetchall():
            proj["channels"][row["channel"]] = _maybe_json(row["value_json"])
            proj["meta"][row["channel"]] = {
                "generator": row["generator"],
                "version": row["generator_version"],
                "source_hash": row["source_hash"],
                "generated_at": row["generated_at"],
                "error": row["error"],
            }
        projects.append(proj)

    return {
        "day": {**dict(header), "channels": day_channels},
        "projects": projects,
        "meta": {"day": day_meta},
    }


def registered_channel_names() -> dict[str, list[str]]:
    """Expose what's registered, for debugging / docs."""
    return {
        "day": list(DAY_CHANNELS.keys()),
        "day_project": list(PROJECT_CHANNELS.keys()),
    }


def _maybe_json(text: str | None):
    if text is None or text == "":
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
