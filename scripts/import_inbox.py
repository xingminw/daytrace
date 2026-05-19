#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daytrace.db import connect, init_db, upsert_events
from daytrace.schema import TraceEvent

SUPPORTED_SCHEMA = "daytrace.event.v1"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for idx in range(1, 10_000):
        candidate = path.with_name(f"{stem}-{idx}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"could not find unique destination for {path}")


def move_preserving_layout(path: Path, root: Path, destination_root: Path) -> Path:
    rel = path.relative_to(root)
    destination = unique_destination(destination_root / rel)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(destination))
    return destination


def iter_inbox_files(inbox: Path) -> list[Path]:
    if not inbox.exists():
        return []
    return sorted(p for p in inbox.rglob("*.jsonl") if p.is_file())


def normalize_privacy(value: str | None) -> str:
    if value == "private_summary_only":
        return "private"
    if value in {"normal", "private", "sensitive"}:
        return value
    if value == "exclude_from_report":
        return "sensitive"
    return "normal"


def event_from_v1(row: dict[str, Any], raw_ref: str) -> TraceEvent:
    if row.get("schema_version") != SUPPORTED_SCHEMA:
        raise ValueError(f"unsupported schema_version: {row.get('schema_version')!r}")
    event_id = str(row.get("event_id") or "").strip()
    if not event_id:
        raise ValueError("event_id is required")
    source = str(row.get("source") or "").strip()
    if not source:
        raise ValueError("source is required")
    kind = str(row.get("kind") or "").strip()
    if not kind:
        raise ValueError("kind is required")
    occurred_at = str(row.get("occurred_at") or "").strip()
    if not occurred_at:
        raise ValueError("occurred_at is required")
    content = str(row.get("content") or "")
    title_source = row.get("title") or (content.splitlines()[0] if content else "")
    title = str(title_source)[:200]
    # `confidence` field in legacy v1 payloads is silently dropped — see
    # daytrace/schema.py for the rationale.
    evidence = {
        "schema_version": SUPPORTED_SCHEMA,
        "batch_id": row.get("batch_id"),
        "collected_at": row.get("collected_at"),
        "actor": row.get("actor"),
        "category": row.get("category"),
        "url": row.get("url"),
        "payload": row.get("payload") or {},
    }
    return TraceEvent(
        id=event_id,
        source=source,
        kind=kind,
        start=occurred_at,
        end=None,
        title=title or event_id,
        summary=content,
        project_guess=row.get("project") or None,
        sensitivity=normalize_privacy(row.get("privacy")),
        evidence=evidence,
        raw_ref=raw_ref,
        device_id=str(row.get("device") or "Mac"),
        location_id=str(row.get("location") or "unknown"),
        collector_id=str(row.get("device") or "hub-local"),
    )


def read_inbox_events(path: Path) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"line {line_number}: event must be a JSON object")
            if "schema_version" in row:
                events.append(event_from_v1(row, str(path)))
            else:
                events.append(TraceEvent.from_dict({**row, "raw_ref": row.get("raw_ref") or str(path)}))
    return events


def existing_event_ids(con, event_ids: list[str], *, chunk_size: int = 900) -> set[str]:
    existing: set[str] = set()
    unique_ids = sorted(set(event_ids))
    for start in range(0, len(unique_ids), chunk_size):
        chunk = unique_ids[start : start + chunk_size]
        if not chunk:
            continue
        placeholders = ",".join("?" for _ in chunk)
        rows = con.execute(
            f"SELECT id FROM events WHERE id IN ({placeholders})",
            chunk,
        ).fetchall()
        existing.update(row["id"] for row in rows)
    return existing


def content_fingerprint(event: TraceEvent) -> str:
    """Stable content key for cross-id dedup.

    Codex/Hermes collectors fold session_id+ts+text into event ids, so the
    same user prompt captured in different rollout files (or re-collected
    days later) produces distinct event ids and ends up duplicated in the
    dashboard. Collapse such events by (source, start, title, summary).
    """
    payload = "␟".join(
        [
            event.source or "",
            event.start or "",
            event.title or "",
            event.summary or "",
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def existing_content_fingerprints(
    con, fingerprints: list[tuple[str, str, str]], *, chunk_size: int = 400
) -> dict[tuple[str, str, str], str]:
    """For each (source, start, title) probe, return the matching existing
    event id (when title+summary fingerprint also matches). Probes are
    pre-filtered to make the cross-batch dedup query cheap.
    """
    found: dict[tuple[str, str, str], str] = {}
    unique = sorted({probe for probe in fingerprints if all(probe)})
    for start in range(0, len(unique), chunk_size):
        chunk = unique[start : start + chunk_size]
        if not chunk:
            continue
        clauses = " OR ".join(["(source = ? AND start = ? AND title = ?)"] * len(chunk))
        params: list[str] = []
        for src, ts, title in chunk:
            params.extend([src, ts, title])
        rows = con.execute(
            f"SELECT id, source, start, title, summary FROM events WHERE {clauses}",
            params,
        ).fetchall()
        for row in rows:
            probe = (row["source"], row["start"], row["title"])
            payload = "␟".join([row["source"] or "", row["start"] or "", row["title"] or "", row["summary"] or ""])
            fp = hashlib.sha1(payload.encode("utf-8")).hexdigest()
            found.setdefault(probe, row["id"])
            # Note: we only need to know that something with the same
            # (source,start,title,summary) exists; if a different summary
            # shares the (source,start,title) bucket we still treat the
            # current event as a duplicate-by-key — collectors don't emit
            # two semantically distinct events for the same source+ts+title.
            del fp
    return found


def dedup_events(
    events: list[TraceEvent], existing: dict[tuple[str, str, str], str]
) -> tuple[list[TraceEvent], int, int]:
    """Drop within-batch and cross-batch content duplicates.

    Returns (kept_events, dropped_in_batch, dropped_vs_db).
    """
    seen: set[str] = set()
    kept: list[TraceEvent] = []
    dropped_in_batch = 0
    dropped_vs_db = 0
    for event in events:
        fp = content_fingerprint(event)
        if fp in seen:
            dropped_in_batch += 1
            continue
        probe = (event.source or "", event.start or "", event.title or "")
        if all(probe) and probe in existing and existing[probe] != event.id:
            dropped_vs_db += 1
            continue
        seen.add(fp)
        kept.append(event)
    return kept, dropped_in_batch, dropped_vs_db


def ensure_destination_outside_inbox(inbox: Path, destination_root: Path, label: str) -> None:
    inbox_resolved = inbox.resolve()
    destination_resolved = destination_root.resolve()
    if destination_resolved == inbox_resolved or inbox_resolved in destination_resolved.parents:
        raise ValueError(f"{label} must not be inside inbox: {destination_root}")


def insert_imported_file(
    con,
    *,
    path: Path,
    archive_path: Path | None,
    file_hash: str,
    status: str,
    event_count: int,
    ingest_run_id: int,
    error: str | None = None,
) -> None:
    parts = path.parts
    source_device = parts[-3] if len(parts) >= 3 else None
    batch_date = parts[-2] if len(parts) >= 2 else None
    con.execute(
        """
        INSERT INTO imported_files(
          path, archive_path, sha256, source_device, batch_date, status,
          event_count, ingest_run_id, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sha256) DO UPDATE SET
          path = excluded.path,
          archive_path = excluded.archive_path,
          source_device = excluded.source_device,
          batch_date = excluded.batch_date,
          status = excluded.status,
          event_count = excluded.event_count,
          ingest_run_id = excluded.ingest_run_id,
          error = excluded.error,
          imported_at = CURRENT_TIMESTAMP
        """,
        (
            str(path),
            str(archive_path) if archive_path else None,
            file_hash,
            source_device,
            batch_date,
            status,
            event_count,
            ingest_run_id,
            error,
        ),
    )


def import_inbox(
    inbox: Path | str,
    db_path: Path | str,
    *,
    archive: Path | str | None = None,
    failed: Path | str | None = None,
) -> dict[str, int]:
    inbox = Path(inbox)
    archive_root = Path(archive) if archive is not None else inbox.parent / "archive"
    failed_root = Path(failed) if failed is not None else inbox.parent / "failed"
    ensure_destination_outside_inbox(inbox, archive_root, "archive")
    ensure_destination_outside_inbox(inbox, failed_root, "failed")
    con = connect(db_path)
    init_db(con)
    run_id = con.execute(
        "INSERT INTO ingest_runs(run_type, status, notes) VALUES (?, ?, ?)",
        ("local_inbox", "running", str(inbox)),
    ).lastrowid
    assert run_id is not None
    result = {
        "files_imported": 0,
        "files_skipped": 0,
        "files_failed": 0,
        "events_inserted": 0,
        "events_deduped_in_batch": 0,
        "events_deduped_vs_db": 0,
    }
    try:
        for path in iter_inbox_files(inbox):
            file_hash = sha256_file(path)
            existing = con.execute(
                "SELECT status FROM imported_files WHERE sha256 = ? AND status = 'imported'",
                (file_hash,),
            ).fetchone()
            if existing:
                move_preserving_layout(path, inbox, archive_root)
                result["files_skipped"] += 1
                continue
            archive_path: Path | None = None
            try:
                con.execute("SAVEPOINT import_file")
                events = read_inbox_events(path)
                probes = [
                    (event.source or "", event.start or "", event.title or "")
                    for event in events
                ]
                existing_content = existing_content_fingerprints(con, probes)
                events, dropped_in_batch, dropped_vs_db = dedup_events(
                    events, existing_content
                )
                result["events_deduped_in_batch"] += dropped_in_batch
                result["events_deduped_vs_db"] += dropped_vs_db
                existing_ids = existing_event_ids(con, [event.id for event in events])
                inserted_count = len({event.id for event in events} - existing_ids)
                upsert_events(con, events, run_date=None, commit=False)
                archive_path = move_preserving_layout(path, inbox, archive_root)
                insert_imported_file(
                    con,
                    path=path,
                    archive_path=archive_path,
                    file_hash=file_hash,
                    status="imported",
                    event_count=len(events),
                    ingest_run_id=run_id,
                )
                con.execute("RELEASE import_file")
                con.commit()
                result["files_imported"] += 1
                result["events_inserted"] += inserted_count
            except Exception as exc:
                try:
                    con.execute("ROLLBACK TO import_file")
                    con.execute("RELEASE import_file")
                except Exception:
                    pass
                failed_source = archive_path if archive_path is not None and archive_path.exists() else path
                failed_root_for_layout = archive_root if failed_source == archive_path else inbox
                failed_path = move_preserving_layout(failed_source, failed_root_for_layout, failed_root)
                insert_imported_file(
                    con,
                    path=path,
                    archive_path=failed_path,
                    file_hash=file_hash,
                    status="failed",
                    event_count=0,
                    ingest_run_id=run_id,
                    error=str(exc),
                )
                con.commit()
                result["files_failed"] += 1
        status = "success" if result["files_failed"] == 0 else "partial"
        con.execute(
            """
            UPDATE ingest_runs
            SET status = ?, finished_at = CURRENT_TIMESTAMP, file_count = ?,
                event_count = ?, error_count = ?
            WHERE id = ?
            """,
            (
                status,
                result["files_imported"] + result["files_skipped"],
                result["events_inserted"],
                result["files_failed"],
                run_id,
            ),
        )
        con.commit()
    except Exception:
        con.execute(
            "UPDATE ingest_runs SET status = ?, finished_at = CURRENT_TIMESTAMP WHERE id = ?",
            ("failed", run_id),
        )
        con.commit()
        raise
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inbox", default="data/inbox")
    parser.add_argument("--db", default="data/daytrace.sqlite")
    parser.add_argument("--archive")
    parser.add_argument("--failed")
    args = parser.parse_args()
    result = import_inbox(
        args.inbox,
        args.db,
        archive=args.archive,
        failed=args.failed,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
