"""Channel registry, dependency ordering, and the daily orchestrator.

A *channel* is a single named piece of structured data attached to either
a day or a (day, project) pair. Each channel has a generator (`stats` or
`ai`), a version (bump to invalidate cached rows), a list of dependencies
on other channels, and a compute callable.

The orchestrator (`regenerate_day`) walks the registry in dependency
order. For each channel it checks:
- does a row exist for this (date, [project], channel)?
- does its source_hash match the current events_hash?
- does its generator_version match the registry's current version?
If all three match → skip. Otherwise → recompute and overwrite.

Stats channels are always cheap, so we recompute liberally. AI channels
gate themselves on the same hash check but each call costs real money; an
`include_ai=False` knob lets callers skip them entirely.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal

from . import stats

GeneratorKind = Literal["stats", "ai"]
TableKind = Literal["day", "day_project"]


@dataclass
class ChannelResult:
    """Wrapper for compute fn return when the caller wants to attach
    cost/usage metadata (AI channels). Stats channels can keep returning
    plain dicts — the orchestrator normalizes both shapes."""
    value: Any
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True)
class ChannelSpec:
    name: str
    table: TableKind
    generator: GeneratorKind
    version: str
    dependencies: tuple[str, ...] = ()
    cost_estimate: str = "free"
    description: str = ""


# ---- Registry ---------------------------------------------------------
# Stats channels are populated below. AI channel registration happens in
# daytrace/ai_report.py at import time so the AI module can stay optional
# (e.g., a stats-only build without anthropic SDK installed).

DAY_CHANNELS: dict[str, ChannelSpec] = {}
PROJECT_CHANNELS: dict[str, ChannelSpec] = {}

# Compute functions live in their own dicts so we can override them in
# tests without touching the registry metadata.
DAY_COMPUTE: dict[str, Callable[[list[dict[str, Any]], "ChannelContext"], Any]] = {}
PROJECT_COMPUTE: dict[str, Callable[[list[dict[str, Any]], "ChannelContext"], Any]] = {}


def register_day_channel(spec: ChannelSpec, compute) -> None:
    assert spec.table == "day"
    DAY_CHANNELS[spec.name] = spec
    DAY_COMPUTE[spec.name] = compute


def register_project_channel(spec: ChannelSpec, compute) -> None:
    assert spec.table == "day_project"
    PROJECT_CHANNELS[spec.name] = spec
    PROJECT_COMPUTE[spec.name] = compute


# Stats day channels — each compute fn takes (events, ctx) for a uniform shape.
def _wrap_day(fn):
    return lambda events, ctx: fn(events)


def _wrap_project(fn):
    return lambda events, ctx: fn(events)


for _name, _fn in {
    "time_span":           stats.channel_time_span,
    "active_minutes":      stats.channel_active_minutes,
    "longest_focus_block": stats.channel_longest_focus_block,
    "context_switches":    stats.channel_context_switches,
    "peak_windows":        stats.channel_peak_windows,
    "dimension_counts":    stats.channel_dimension_counts,
    "quality":             stats.channel_quality,
}.items():
    register_day_channel(
        ChannelSpec(name=_name, table="day", generator="stats",
                    version=stats.STATS_VERSION),
        _wrap_day(_fn),
    )

for _name, _fn in {
    "time_span":      stats.channel_time_span,
    "active_minutes": lambda events: {"total": stats.project_active_minutes(events)},
    "source_mix":     stats.channel_source_mix,
    "device_mix":     stats.channel_device_mix,
    "top_titles":     stats.channel_top_titles,
    "event_density":  stats.channel_event_density,
}.items():
    register_project_channel(
        ChannelSpec(name=_name, table="day_project", generator="stats",
                    version=stats.STATS_VERSION),
        _wrap_project(_fn),
    )


# ---- Context passed to each compute -----------------------------------

@dataclass
class ChannelContext:
    """Read-only context handed to compute functions.

    Carries the date, the connection (for cross-channel reads — e.g., AI
    channels may want to read the freshly computed stats), and the
    pre-computed events_hash (so compute can include it in their output
    if useful)."""
    date: str
    con: sqlite3.Connection
    events_hash: str
    project: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


# ---- Hashing ----------------------------------------------------------

def compute_events_hash(events: Iterable[dict[str, Any]]) -> str:
    """sha1 over (id, start, title, summary) — manual edits invalidate cache.

    Stable across runs: sort by id so collection order doesn't matter.
    """
    pieces = sorted(
        (
            str(ev.get("id") or ""),
            str(ev.get("start") or ""),
            str(ev.get("title") or ""),
            str(ev.get("summary") or ""),
        )
        for ev in events
    )
    h = hashlib.sha1()
    for p in pieces:
        h.update("␟".join(p).encode("utf-8"))
        h.update("␞".encode("utf-8"))
    return h.hexdigest()


# ---- Topo sort --------------------------------------------------------

def topo_sort(specs: dict[str, ChannelSpec]) -> list[ChannelSpec]:
    """Stable topological ordering. Raises if a cycle exists."""
    order: list[ChannelSpec] = []
    seen: set[str] = set()
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name in seen:
            return
        if name in visiting:
            raise ValueError(f"cycle detected at channel {name!r}")
        spec = specs.get(name)
        if spec is None:
            raise KeyError(f"unknown channel dependency {name!r}")
        visiting.add(name)
        for dep in spec.dependencies:
            visit(dep)
        visiting.discard(name)
        seen.add(name)
        order.append(spec)

    for name in specs:
        visit(name)
    return order


# ---- Cache check ------------------------------------------------------

def _is_fresh(row: sqlite3.Row | None, spec: ChannelSpec, source_hash: str) -> bool:
    if row is None:
        return False
    if row["error"]:
        return False
    if row["generator_version"] != spec.version:
        return False
    if row["source_hash"] != source_hash:
        return False
    if row["value_json"] is None:
        return False
    return True


# ---- Orchestrator -----------------------------------------------------

@dataclass
class RegenerationReport:
    date: str
    events_hash: str
    total_events: int
    day_channels_run: list[str] = field(default_factory=list)
    day_channels_skipped: list[str] = field(default_factory=list)
    project_channels_run: dict[str, list[str]] = field(default_factory=dict)
    project_channels_skipped: dict[str, list[str]] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "events_hash": self.events_hash,
            "total_events": self.total_events,
            "day_channels_run": self.day_channels_run,
            "day_channels_skipped": self.day_channels_skipped,
            "project_channels_run": self.project_channels_run,
            "project_channels_skipped": self.project_channels_skipped,
            "errors": self.errors,
        }


def regenerate_day(
    con: sqlite3.Connection,
    date: str,
    events: list[dict[str, Any]],
    *,
    force: bool = False,
    include_ai: bool = True,
) -> RegenerationReport:
    """Recompute all (or stale) channels for one day.

    Caller passes the day's events explicitly — the orchestrator doesn't
    re-query so tests can inject synthetic events, and so callers can
    pre-filter by sensitivity if they want.
    """
    events_hash = compute_events_hash(events)
    total_events = len(events)
    report = RegenerationReport(date=date, events_hash=events_hash, total_events=total_events)

    # 1) Day-level header row
    _upsert_day_report(con, date, events_hash, total_events, events)

    # 2a) Day-level NON-AI (stats) channels first — cheap, and the AI
    # day-level channels (ai_overview) consume per-project ai_summary
    # rows as input, so we want to defer those AI ones until after
    # per-project channels have run. Stats channels are split out and
    # finish here.
    day_specs_sorted = topo_sort(DAY_CHANNELS)
    day_stats_specs = [s for s in day_specs_sorted if s.generator != "ai"]
    day_ai_specs   = [s for s in day_specs_sorted if s.generator == "ai"]

    def _run_day_spec(spec):
        if spec.generator == "ai" and not include_ai:
            report.day_channels_skipped.append(spec.name)
            return
        existing = con.execute(
            "SELECT value_json, source_hash, generator_version, error FROM day_channel"
            " WHERE date = ? AND channel = ?",
            (date, spec.name),
        ).fetchone()
        if not force and _is_fresh(existing, spec, events_hash):
            report.day_channels_skipped.append(spec.name)
            return
        try:
            raw = DAY_COMPUTE[spec.name](
                events,
                ChannelContext(date=date, con=con, events_hash=events_hash),
            )
            value, tin, tout, cost = _unwrap(raw)
            _write_channel(
                con, "day_channel",
                {"date": date, "channel": spec.name},
                value, spec, events_hash,
                tokens_in=tin, tokens_out=tout, cost_usd=cost,
            )
            report.day_channels_run.append(spec.name)
        except Exception as exc:  # never let one channel break the rest
            _write_channel_error(
                con, "day_channel",
                {"date": date, "channel": spec.name},
                spec, events_hash, str(exc),
            )
            report.errors.append({"channel": spec.name, "scope": "day", "error": str(exc)})

    for spec in day_stats_specs:
        _run_day_spec(spec)

    # 3) Per-project rows + channels.
    # Clean up rows for projects that no longer appear in this day's events
    # (e.g. after a shifted-day boundary change or a manual re-attribution).
    # The FK CASCADE on day_project_channel removes the slice rows too.
    by_project = stats.split_events_by_project(events)
    keep = set(by_project.keys())
    # FK CASCADE isn't enforced by default in SQLite, so delete from both
    # tables explicitly.
    if keep:
        ph = ",".join("?" for _ in keep)
        con.execute(
            f"DELETE FROM day_project_channel WHERE date = ? AND project NOT IN ({ph})",
            (date, *keep),
        )
        con.execute(
            f"DELETE FROM day_project_report WHERE date = ? AND project NOT IN ({ph})",
            (date, *keep),
        )
    else:
        con.execute("DELETE FROM day_project_channel WHERE date = ?", (date,))
        con.execute("DELETE FROM day_project_report WHERE date = ?", (date,))
    for project, project_events in by_project.items():
        _upsert_project_report(con, date, project, project_events, total_events, events)
        report.project_channels_run.setdefault(project, [])
        report.project_channels_skipped.setdefault(project, [])
        project_events_hash = compute_events_hash(project_events)
        for spec in topo_sort(PROJECT_CHANNELS):
            if spec.generator == "ai" and not include_ai:
                report.project_channels_skipped[project].append(spec.name)
                continue
            existing = con.execute(
                "SELECT value_json, source_hash, generator_version, error FROM day_project_channel"
                " WHERE date = ? AND project = ? AND channel = ?",
                (date, project, spec.name),
            ).fetchone()
            if not force and _is_fresh(existing, spec, project_events_hash):
                report.project_channels_skipped[project].append(spec.name)
                continue
            try:
                raw = PROJECT_COMPUTE[spec.name](
                    project_events,
                    ChannelContext(
                        date=date, con=con, events_hash=project_events_hash,
                        project=project,
                    ),
                )
                value, tin, tout, cost = _unwrap(raw)
                _write_channel(
                    con, "day_project_channel",
                    {"date": date, "project": project, "channel": spec.name},
                    value, spec, project_events_hash,
                    tokens_in=tin, tokens_out=tout, cost_usd=cost,
                )
                report.project_channels_run[project].append(spec.name)
            except Exception as exc:
                _write_channel_error(
                    con, "day_project_channel",
                    {"date": date, "project": project, "channel": spec.name},
                    spec, project_events_hash, str(exc),
                )
                report.errors.append({
                    "channel": spec.name, "scope": "day_project",
                    "project": project, "error": str(exc),
                })

    # 4) Day-level AI channels (deferred from step 2 so they can read the
    # per-project ai_summary rows from step 3 as input). ai_overview
    # specifically pulls per-project summaries for its narrative now.
    for spec in day_ai_specs:
        _run_day_spec(spec)

    con.commit()
    return report


def _upsert_day_report(con, date, events_hash, total_events, events):
    am = stats.channel_active_minutes(events)["total"]
    con.execute(
        """
        INSERT INTO day_report(date, events_hash, total_events, active_minutes, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(date) DO UPDATE SET
          events_hash=excluded.events_hash,
          total_events=excluded.total_events,
          active_minutes=excluded.active_minutes,
          updated_at=CURRENT_TIMESTAMP
        """,
        (date, events_hash, total_events, am),
    )


def _upsert_project_report(con, date, project, project_events, day_total, all_events):
    events_hash = compute_events_hash(project_events)
    event_count = len(project_events)
    am = stats.project_active_minutes(project_events)
    share = (event_count / day_total) if day_total else 0.0
    # Collect distinct work_item titles linked to this slice's events. The
    # join is cheap (≤ a few hundred events per project per day), and stuffing
    # the result here means the dashboard doesn't need to re-join on every
    # render. Order by event count desc so the top task surfaces first.
    tasks_json = None
    event_ids = [e.get("id") for e in project_events if e.get("id")]
    if event_ids:
        ph = ",".join("?" * len(event_ids))
        try:
            rows = con.execute(
                f"""
                SELECT w.title, w.title_en, COUNT(*) AS n
                  FROM event_work_item_links l
                  JOIN work_items w ON w.record_id = l.record_id
                 WHERE l.event_id IN ({ph})
                 GROUP BY w.record_id
                 ORDER BY n DESC, w.title
                """, event_ids,
            ).fetchall()
            tasks = [
                {"title": r["title"], "title_en": r["title_en"] or "", "n": r["n"]}
                for r in rows if r["title"]
            ]
            if tasks:
                tasks_json = json.dumps(tasks, ensure_ascii=False)
        except sqlite3.OperationalError:
            # work_items / event_work_item_links may not exist yet
            tasks_json = None
    con.execute(
        """
        INSERT INTO day_project_report(date, project, events_hash, event_count,
          active_minutes, share, tasks, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(date, project) DO UPDATE SET
          events_hash=excluded.events_hash,
          event_count=excluded.event_count,
          active_minutes=excluded.active_minutes,
          share=excluded.share,
          tasks=excluded.tasks,
          updated_at=CURRENT_TIMESTAMP
        """,
        (date, project, events_hash, event_count, am, share, tasks_json),
    )


def _unwrap(raw) -> tuple[Any, int, int, float]:
    """Normalize a compute return to (value, tokens_in, tokens_out, cost)."""
    if isinstance(raw, ChannelResult):
        return raw.value, raw.tokens_in, raw.tokens_out, raw.cost_usd
    return raw, 0, 0, 0.0


def _write_channel(con, table, key, value, spec: ChannelSpec, source_hash,
                   *, tokens_in: int = 0, tokens_out: int = 0, cost_usd: float = 0.0):
    cols = list(key) + ["value_json", "generator", "generator_version",
                        "source_hash", "generated_at", "tokens_in", "tokens_out",
                        "cost_usd", "error"]
    placeholders = ",".join("?" for _ in cols)
    set_clause = ",".join(f"{c}=excluded.{c}" for c in
                          ["value_json", "generator", "generator_version",
                           "source_hash", "generated_at", "tokens_in",
                           "tokens_out", "cost_usd", "error"])
    pk_cols = list(key)
    values = list(key.values()) + [
        json.dumps(value, ensure_ascii=False),
        spec.generator, spec.version, source_hash,
        _now(), tokens_in, tokens_out, cost_usd, None,
    ]
    con.execute(
        f"INSERT INTO {table}({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT({','.join(pk_cols)}) DO UPDATE SET {set_clause}",
        values,
    )


def _write_channel_error(con, table, key, spec: ChannelSpec, source_hash, error):
    _write_channel_raw(con, table, key, None, spec, source_hash, error)


def _write_channel_raw(con, table, key, value_json, spec: ChannelSpec, source_hash, error):
    cols = list(key) + ["value_json", "generator", "generator_version",
                        "source_hash", "generated_at", "error"]
    placeholders = ",".join("?" for _ in cols)
    set_clause = ",".join(f"{c}=excluded.{c}" for c in
                          ["value_json", "generator", "generator_version",
                           "source_hash", "generated_at", "error"])
    pk_cols = list(key)
    values = list(key.values()) + [
        value_json, spec.generator, spec.version, source_hash, _now(), error,
    ]
    con.execute(
        f"INSERT INTO {table}({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT({','.join(pk_cols)}) DO UPDATE SET {set_clause}",
        values,
    )


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
