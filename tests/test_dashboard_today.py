from daytrace.db import (
    connect,
    init_db,
    upsert_events,
    query_today,
    query_events,
    query_filter_options,
)
from daytrace.schema import TraceEvent
from dashboard.server import (
    end_date_options,
    events_page,
    normalize_date_bound,
    parse_event_limit,
    resolve_date_range,
)


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

    assert event["device_id"] == "Mac"
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
    assert today["by_device"] == [{"device_id": "Mac", "count": 3}]
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
            make_event("e4", "2026-05-14T16:05:00", "codex", "user_prompt", "daily-briefing"),
            make_event("e5", "2026-05-14T17:05:00", "docs", "document_modified", "notes"),
        ],
        run_date=None,
    )

    options = query_filter_options(con)

    assert options["date"][0] == {"value": "", "label": "All"}
    assert {item["value"] for item in options["date"]} >= {"2026-05-13", "2026-05-14"}
    assert {item["value"] for item in options["source"]} >= {"docs", "git", "hermes"}
    assert {item["value"] for item in options["device_id"]} >= {"Mac"}
    assert {item["value"] for item in options["location_id"]} >= {"unknown"}
    assert {item["value"] for item in options["project"]} >= {"daytrace", "未归因"}

    visible_rows = query_events(
        con,
        source_in=["hermes", "codex", "github"],
        start_from="2026-05-14T15:30:00",
        start_to="2026-05-14T16:30:00",
        limit=20,
    )
    assert [row["id"] for row in visible_rows] == ["e4"]

    scoped_options = query_filter_options(
        con,
        {
            "source_in": ["hermes", "codex", "github"],
            "start_from": "2026-05-14T15:30:00",
            "start_to": "2026-05-14T16:30:00",
        },
    )
    assert {item["value"] for item in scoped_options["project"]} == {"", "daily-briefing"}
    assert {item["value"] for item in scoped_options["source"]} == {"", "codex"}


def test_events_page_ignores_malformed_start_without_clearing_valid_end(tmp_path):
    db_path = tmp_path / "daytrace.sqlite"
    con = connect(db_path)
    init_db(con)
    upsert_events(
        con,
        [
            make_event("before-end", "2026-05-13T09:10:00", "hermes", "user_input", "daytrace"),
            make_event("after-end", "2026-05-15T09:10:00", "hermes", "user_input", "daily-briefing"),
        ],
        run_date=None,
    )

    html = events_page(
        db_path,
        {"start_from": ["not-a-date"], "start_to": ["2026-05-14"], "source": ["hermes"]},
    )

    assert "daytrace user_input" in html
    assert "daily-briefing user_input" not in html


def test_parse_event_limit_supports_all_and_safe_defaults():
    assert parse_event_limit("100") == 100
    assert parse_event_limit("500") == 500
    assert parse_event_limit("1000") == 1000
    assert parse_event_limit("all") is None
    assert parse_event_limit("not-a-number") == 500
    assert parse_event_limit("999999") == 500


def test_normalize_date_bound_rejects_malformed_dates():
    assert normalize_date_bound("not-a-date") is None
    assert normalize_date_bound("2026-13-99") is None
    assert normalize_date_bound("2026-05-12") == "2026-05-12T00:00:00"
    assert normalize_date_bound("2026-05-12", end_of_day=True) == "2026-05-12T23:59:59"


def test_resolve_date_range_start_only_means_single_day():
    assert resolve_date_range("2026-05-12", None) == (
        "2026-05-12T00:00:00",
        "2026-05-12T23:59:59",
    )
    assert resolve_date_range("2026-05-12", "not-a-date") == (
        "2026-05-12T00:00:00",
        "2026-05-12T23:59:59",
    )
    assert resolve_date_range("not-a-date", "2026-05-14") == (
        None,
        "2026-05-14T23:59:59",
    )
    assert resolve_date_range("2026-05-12", "2026-05-14") == (
        "2026-05-12T00:00:00",
        "2026-05-14T23:59:59",
    )
    assert resolve_date_range(None, "2026-05-14") == (
        None,
        "2026-05-14T23:59:59",
    )


def test_end_date_options_are_on_or_after_start_date():
    options = [
        {"value": "2026-05-14", "label": "2026-05-14 · 10 events"},
        {"value": "2026-05-12", "label": "2026-05-12 · 5 events"},
        {"value": "2026-05-10", "label": "2026-05-10 · 3 events"},
    ]

    assert [item["value"] for item in end_date_options("2026-05-12", options)] == [
        "2026-05-14",
        "2026-05-12",
    ]
    assert end_date_options(None, options) == options
