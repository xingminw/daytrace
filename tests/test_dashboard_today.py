from daytrace.db import (
    connect,
    init_db,
    upsert_events,
    query_today,
    query_events,
    query_filter_options,
)
from daytrace.schema import TraceEvent


def make_event(id, start, source, kind, project, confidence=0.9):
    return TraceEvent(
        id=id,
        source=source,
        kind=kind,
        start=start,
        end=None,
        title=f"{project or 'unknown'} {kind}",
        summary="summary",
        project_guess=project,
        confidence=confidence,
        sensitivity="normal",
        evidence={"path": f"/tmp/{id}"},
    )


def test_events_have_single_machine_device_and_location_defaults(tmp_path):
    con = connect(tmp_path / "daytrace.sqlite")
    init_db(con)
    upsert_events(
        con,
        [
            make_event(
                "e1", "2026-05-13T09:00:00", "docs", "document_modified", "daytrace"
            )
        ],
        run_date="2026-05-13",
    )

    event = query_events(con, date="2026-05-13", limit=1)[0]

    assert event["device_id"] == "mac-hermes"
    assert event["location_id"] == "unknown"
    assert event["collector_id"] == "hub-local"


def test_query_today_returns_timeline_and_composition(tmp_path):
    con = connect(tmp_path / "daytrace.sqlite")
    init_db(con)
    upsert_events(
        con,
        [
            make_event(
                "e1", "2026-05-13T09:10:00", "docs", "document_modified", "daytrace"
            ),
            make_event("e2", "2026-05-13T09:45:00", "git", "commit", "daytrace"),
            make_event(
                "e3",
                "2026-05-13T15:05:00",
                "hermes",
                "session_activity",
                None,
                confidence=0.4,
            ),
        ],
        run_date="2026-05-13",
    )

    today = query_today(con, "2026-05-13")

    assert today["summary"]["total_events"] == 3
    assert [bucket["hour"] for bucket in today["timeline"]] == ["09:00", "15:00"]
    assert today["timeline"][0]["count"] == 2
    assert today["by_device"] == [{"device_id": "mac-hermes", "count": 3}]
    assert today["by_location"] == [{"location_id": "unknown", "count": 3}]
    assert any(item["source"] == "docs" for item in today["summary"]["sources"])
    assert len(today["needs_review"]) == 1


def test_query_today_keeps_late_hour_examples_when_early_day_has_many_events(tmp_path):
    con = connect(tmp_path / "daytrace.sqlite")
    init_db(con)
    events = [
        make_event(
            f"early-{i}",
            f"2026-05-13T09:{i:02d}:00",
            "docs",
            "document_modified",
            "daytrace",
        )
        for i in range(12)
    ]
    events.append(
        make_event("late", "2026-05-13T20:05:00", "git", "commit", "daily-briefing")
    )
    upsert_events(con, events, run_date="2026-05-13")

    today = query_today(con, "2026-05-13")

    late_bucket = next(
        bucket for bucket in today["timeline"] if bucket["hour"] == "20:00"
    )
    assert [event["id"] for event in late_bucket["events"]] == ["late"]


def test_query_filter_options_returns_all_label_and_distinct_values(tmp_path):
    con = connect(tmp_path / "daytrace.sqlite")
    init_db(con)
    upsert_events(
        con,
        [
            make_event(
                "e1", "2026-05-13T09:10:00", "docs", "document_modified", "daytrace"
            ),
            make_event("e2", "2026-05-14T09:45:00", "git", "commit", "daytrace"),
            make_event("e3", "2026-05-14T15:05:00", "hermes", "session_activity", None),
        ],
        run_date=None,
    )

    options = query_filter_options(con)

    assert options["date"][0] == {"value": "", "label": "All"}
    assert {item["value"] for item in options["date"]} >= {"2026-05-13", "2026-05-14"}
    assert {item["value"] for item in options["source"]} >= {"docs", "git", "hermes"}
    assert {item["value"] for item in options["device_id"]} >= {"mac-hermes"}
    assert {item["value"] for item in options["location_id"]} >= {"unknown"}
    assert {item["value"] for item in options["project"]} >= {"daytrace", "未归因"}
