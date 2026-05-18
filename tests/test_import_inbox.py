import json

from daytrace.db import connect, query_events
from scripts.import_inbox import import_inbox


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_import_inbox_imports_v1_batch_records_ledger_and_archives(tmp_path):
    db_path = tmp_path / "daytrace.sqlite"
    inbox = tmp_path / "inbox"
    archive = tmp_path / "archive"
    failed = tmp_path / "failed"
    batch = inbox / "iphone" / "2026-05-14" / "batch-1.jsonl"
    write_jsonl(
        batch,
        [
            {
                "schema_version": "daytrace.event.v1",
                "event_id": "evt-iphone-1",
                "batch_id": "batch-1",
                "occurred_at": "2026-05-14T09:00:00",
                "collected_at": "2026-05-14T09:01:00",
                "source": "ios_shortcuts",
                "kind": "manual_note",
                "device": "iphone",
                "location": "home",
                "actor": "xingmin",
                "project": "daytrace",
                "category": "personal",
                "title": "记了一条手机笔记",
                "content": "从 iPhone branch 上传的测试事件",
                "url": "https://example.com/note",
                "privacy": "private_summary_only",
                "payload": {"note_id": "n1"},
            }
        ],
    )

    result = import_inbox(inbox, db_path, archive=archive, failed=failed)

    assert result == {
        "files_imported": 1,
        "files_skipped": 0,
        "files_failed": 0,
        "events_inserted": 1,
        "events_deduped_in_batch": 0,
        "events_deduped_vs_db": 0,
    }
    assert not batch.exists()
    assert (archive / "iphone" / "2026-05-14" / "batch-1.jsonl").exists()

    con = connect(db_path)
    events = query_events(con, source="ios_shortcuts", limit=None)
    assert len(events) == 1
    assert events[0]["id"] == "evt-iphone-1"
    assert events[0]["device_id"] == "iphone"
    assert events[0]["location_id"] == "home"
    assert events[0]["collector_id"] == "iphone"
    assert events[0]["summary"] == "从 iPhone branch 上传的测试事件"
    assert events[0]["evidence"]["batch_id"] == "batch-1"
    assert events[0]["evidence"]["payload"] == {"note_id": "n1"}

    imported = con.execute("SELECT * FROM imported_files").fetchall()
    assert len(imported) == 1
    assert imported[0]["status"] == "imported"
    assert imported[0]["event_count"] == 1
    runs = con.execute("SELECT * FROM ingest_runs").fetchall()
    assert len(runs) == 1
    assert runs[0]["status"] == "success"
    assert runs[0]["file_count"] == 1
    assert runs[0]["event_count"] == 1


def test_import_inbox_is_idempotent_by_file_hash_and_event_id(tmp_path):
    db_path = tmp_path / "daytrace.sqlite"
    inbox = tmp_path / "inbox"
    archive = tmp_path / "archive"
    failed = tmp_path / "failed"
    rows = [
        {
            "schema_version": "daytrace.event.v1",
            "event_id": "evt-repeat",
            "occurred_at": "2026-05-14T10:00:00",
            "collected_at": "2026-05-14T10:01:00",
            "source": "cloud-worker",
            "kind": "health_check",
            "device": "cloud-worker",
            "location": "unknown",
            "actor": "system",
            "project": None,
            "category": "unknown",
            "title": "health",
            "content": "ok",
            "privacy": "normal",
            "payload": {},
        }
    ]
    write_jsonl(inbox / "cloud-worker" / "2026-05-14" / "a.jsonl", rows)
    first = import_inbox(inbox, db_path, archive=archive, failed=failed)
    write_jsonl(inbox / "cloud-worker" / "2026-05-14" / "a-copy.jsonl", rows)
    second = import_inbox(inbox, db_path, archive=archive, failed=failed)

    con = connect(db_path)
    assert first["events_inserted"] == 1
    assert second["events_inserted"] == 0
    assert second["files_skipped"] == 1
    assert len(query_events(con, source="cloud-worker", limit=None)) == 1
    assert con.execute("SELECT name FROM sources WHERE id = 'cloud-worker'").fetchone()["name"] == "cloud-worker"
    assert con.execute("SELECT COUNT(*) AS c FROM imported_files").fetchone()["c"] == 1


def test_import_inbox_moves_invalid_jsonl_to_failed(tmp_path):
    db_path = tmp_path / "daytrace.sqlite"
    inbox = tmp_path / "inbox"
    failed = tmp_path / "failed"
    bad = inbox / "iphone" / "2026-05-14" / "bad.jsonl"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("not json\n", encoding="utf-8")

    result = import_inbox(inbox, db_path, failed=failed)

    assert result["files_failed"] == 1
    assert not bad.exists()
    assert (failed / "iphone" / "2026-05-14" / "bad.jsonl").exists()
    con = connect(db_path)
    row = con.execute("SELECT status, error FROM imported_files").fetchone()
    assert row["status"] == "failed"
    assert "Expecting value" in row["error"]


def test_import_inbox_hub_imports_multiple_mock_branch_devices(tmp_path):
    db_path = tmp_path / "hub" / "daytrace.sqlite"
    inbox = tmp_path / "hub" / "inbox"
    archive = tmp_path / "hub" / "archive"
    failed = tmp_path / "hub" / "failed"

    def event(event_id, device, source, kind, title, location="home"):
        return {
            "schema_version": "daytrace.event.v1",
            "event_id": event_id,
            "batch_id": f"{device}-2026-05-14-a",
            "occurred_at": "2026-05-14T09:00:00-04:00",
            "collected_at": "2026-05-14T09:05:00-04:00",
            "source": source,
            "kind": kind,
            "device": device,
            "location": location,
            "project": "DayTrace",
            "title": title,
            "content": f"{title} from {device}",
            "privacy": "normal",
            "payload": {"mock_branch": device},
        }

    write_jsonl(
        inbox / "macbook-air" / "2026-05-14" / "codex.jsonl",
        [
            event("mac-001", "macbook-air", "codex", "message", "Mac Codex planning"),
            event("shared-001", "macbook-air", "hermes", "message", "Shared conversation event"),
        ],
    )
    write_jsonl(
        inbox / "iphone-15" / "2026-05-14" / "shortcuts.jsonl",
        [event("ios-001", "iphone-15", "ios_shortcuts", "focus", "iPhone focus mode", location="commute")],
    )
    write_jsonl(
        inbox / "office-mac" / "2026-05-14" / "activity.jsonl",
        [
            event("office-001", "office-mac", "macos-activity", "app", "Office app usage", location="office"),
            event("shared-001", "office-mac", "hermes", "message", "Duplicate shared event", location="office"),
        ],
    )
    bad = inbox / "iphone-15" / "2026-05-14" / "bad.jsonl"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text('{"schema_version": "daytrace.event.v1", "event_id": \n', encoding="utf-8")

    first = import_inbox(inbox, db_path, archive=archive, failed=failed)

    assert first == {
        "files_imported": 3,
        "files_skipped": 0,
        "files_failed": 1,
        "events_inserted": 4,
        "events_deduped_in_batch": 0,
        "events_deduped_vs_db": 0,
    }
    assert not list(inbox.rglob("*.jsonl"))
    assert (archive / "macbook-air" / "2026-05-14" / "codex.jsonl").exists()
    assert (archive / "iphone-15" / "2026-05-14" / "shortcuts.jsonl").exists()
    assert (archive / "office-mac" / "2026-05-14" / "activity.jsonl").exists()
    assert (failed / "iphone-15" / "2026-05-14" / "bad.jsonl").exists()

    redelivered = inbox / "lab-mac" / "2026-05-15" / "redelivered-codex.jsonl"
    redelivered.parent.mkdir(parents=True, exist_ok=True)
    redelivered.write_text(
        (archive / "macbook-air" / "2026-05-14" / "codex.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    second = import_inbox(inbox, db_path, archive=archive, failed=failed)

    assert second == {
        "files_imported": 0,
        "files_skipped": 1,
        "files_failed": 0,
        "events_inserted": 0,
        "events_deduped_in_batch": 0,
        "events_deduped_vs_db": 0,
    }
    assert not list(inbox.rglob("*.jsonl"))
    assert (archive / "lab-mac" / "2026-05-15" / "redelivered-codex.jsonl").exists()

    con = connect(db_path)
    assert con.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"] == 4
    assert con.execute("SELECT COUNT(*) AS c FROM imported_files WHERE status = 'imported'").fetchone()["c"] == 3
    assert con.execute("SELECT COUNT(*) AS c FROM imported_files WHERE status = 'failed'").fetchone()["c"] == 1
    assert con.execute("SELECT COUNT(*) AS c FROM imported_files WHERE source_device = 'macbook-air'").fetchone()["c"] == 1
    assert con.execute("SELECT COUNT(*) AS c FROM devices WHERE id IN ('macbook-air', 'iphone-15', 'office-mac')").fetchone()["c"] == 3
    assert con.execute("SELECT COUNT(*) AS c FROM locations WHERE id IN ('home', 'commute', 'office')").fetchone()["c"] == 3
    assert con.execute("SELECT COUNT(*) AS c FROM sources WHERE id IN ('codex', 'hermes', 'ios_shortcuts', 'macos-activity')").fetchone()["c"] == 4
    runs = con.execute("SELECT status, file_count, event_count, error_count FROM ingest_runs ORDER BY id").fetchall()
    assert [dict(run) for run in runs] == [
        {"status": "partial", "file_count": 3, "event_count": 4, "error_count": 1},
        {"status": "success", "file_count": 1, "event_count": 0, "error_count": 0},
    ]


def test_import_inbox_counts_duplicate_event_ids_once(tmp_path):
    db_path = tmp_path / "daytrace.sqlite"
    inbox = tmp_path / "inbox"
    archive = tmp_path / "archive"
    failed = tmp_path / "failed"
    rows = [
        {
            "schema_version": "daytrace.event.v1",
            "event_id": "evt-duplicate",
            "occurred_at": "2026-05-14T10:00:00",
            "source": "codex",
            "kind": "message",
            "device": "Mac",
            "location": "unknown",
            "title": title,
            "content": title,
        }
        for title in ("first", "second")
    ]
    write_jsonl(inbox / "Mac" / "2026-05-14" / "dupes.jsonl", rows)

    result = import_inbox(inbox, db_path, archive=archive, failed=failed)

    assert result["events_inserted"] == 1
    con = connect(db_path)
    assert con.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"] == 1
    assert con.execute("SELECT event_count FROM imported_files").fetchone()["event_count"] == 2


def test_import_inbox_rolls_back_events_when_archive_move_fails(tmp_path):
    db_path = tmp_path / "daytrace.sqlite"
    inbox = tmp_path / "inbox"
    archive = tmp_path / "archive-as-file"
    failed = tmp_path / "failed"
    archive.write_text("not a directory", encoding="utf-8")
    write_jsonl(
        inbox / "Mac" / "2026-05-14" / "a.jsonl",
        [
            {
                "schema_version": "daytrace.event.v1",
                "event_id": "evt-rollback",
                "occurred_at": "2026-05-14T10:00:00",
                "source": "codex",
                "kind": "message",
                "device": "Mac",
                "location": "unknown",
                "title": "rollback",
                "content": "rollback",
            }
        ],
    )

    result = import_inbox(inbox, db_path, archive=archive, failed=failed)

    assert result["files_failed"] == 1
    con = connect(db_path)
    assert con.execute("SELECT COUNT(*) AS c FROM events WHERE id = 'evt-rollback'").fetchone()["c"] == 0
    row = con.execute("SELECT status, event_count FROM imported_files").fetchone()
    assert dict(row) == {"status": "failed", "event_count": 0}
    assert (failed / "Mac" / "2026-05-14" / "a.jsonl").exists()

    archive.unlink()
    retry = inbox / "Mac" / "2026-05-14" / "retry.jsonl"
    write_jsonl(
        retry,
        [
            {
                "schema_version": "daytrace.event.v1",
                "event_id": "evt-rollback",
                "occurred_at": "2026-05-14T10:00:00",
                "source": "codex",
                "kind": "message",
                "device": "Mac",
                "location": "unknown",
                "title": "rollback",
                "content": "rollback",
            }
        ],
    )
    retry_result = import_inbox(inbox, db_path, archive=archive, failed=failed)
    assert retry_result["files_imported"] == 1
    assert retry_result["events_inserted"] == 1
    assert con.execute("SELECT status FROM imported_files").fetchone()["status"] == "imported"

    redelivery = inbox / "Mac" / "2026-05-15" / "redelivery.jsonl"
    redelivery.parent.mkdir(parents=True, exist_ok=True)
    redelivery.write_text((archive / "Mac" / "2026-05-14" / "retry.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    third = import_inbox(inbox, db_path, archive=archive, failed=failed)
    assert third["files_skipped"] == 1


def test_import_inbox_rejects_archive_inside_inbox(tmp_path):
    db_path = tmp_path / "daytrace.sqlite"
    inbox = tmp_path / "inbox"
    archive = inbox / "archive"
    failed = tmp_path / "failed"
    write_jsonl(inbox / "Mac" / "2026-05-14" / "a.jsonl", [])

    try:
        import_inbox(inbox, db_path, archive=archive, failed=failed)
    except ValueError as exc:
        assert "archive must not be inside inbox" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_import_inbox_dedupes_by_content_fingerprint_within_and_across_batches(tmp_path):
    """Codex/Hermes collectors fold session_id+ts+text into ids, so re-running
    a collector can produce different event ids for the same user prompt.
    import_inbox must collapse those: in-batch via fingerprint dedup, and
    cross-batch via a DB lookup on (source, start, title, summary)."""
    db_path = tmp_path / "daytrace.sqlite"
    inbox = tmp_path / "inbox"
    archive = tmp_path / "archive"
    failed = tmp_path / "failed"

    def codex_event(event_id):
        return {
            "schema_version": "daytrace.event.v1",
            "event_id": event_id,
            "occurred_at": "2026-05-15T16:04:26",
            "source": "codex",
            "kind": "user_input",
            "device": "omen-wsl",
            "location": "unknown",
            "title": "你现在能连上我的飞书吗",
            "content": "你现在能连上我的飞书吗",
            "privacy": "normal",
            "payload": {},
        }

    # First batch: three rows with the same content but distinct ids
    # (simulates the same prompt landing in two different rollout files).
    write_jsonl(
        inbox / "omen-wsl" / "2026-05-15" / "codex.jsonl",
        [codex_event("codex-input-aaa"), codex_event("codex-input-bbb"), codex_event("codex-input-ccc")],
    )
    first = import_inbox(inbox, db_path, archive=archive, failed=failed)
    assert first["events_inserted"] == 1
    assert first["events_deduped_in_batch"] == 2
    assert first["events_deduped_vs_db"] == 0

    # Second batch (re-collected): yet another fresh id for the same prompt.
    write_jsonl(
        inbox / "omen-wsl" / "2026-05-15" / "codex-recollect.jsonl",
        [codex_event("codex-input-ddd")],
    )
    second = import_inbox(inbox, db_path, archive=archive, failed=failed)
    assert second["events_inserted"] == 0
    assert second["events_deduped_vs_db"] == 1

    con = connect(db_path)
    rows = query_events(con, source="codex", limit=None)
    assert len(rows) == 1
    assert rows[0]["title"] == "你现在能连上我的飞书吗"
