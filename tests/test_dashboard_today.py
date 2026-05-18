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
    display_title_content,
    end_date_options,
    event_timeline_card,
    events_page,
    normalize_date_bound,
    parse_event_limit,
    resolve_date_range,
)


def make_event(id, start, source, kind, project):
    return TraceEvent(
        id=id,
        source=source,
        kind=kind,
        start=start,
        end=None,
        title=f"{project or 'unknown'} {kind}",
        summary="summary",
        project_guess=project,
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
    assert {item["value"] for item in options["project"]} >= {"daytrace", "misc"}

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


def test_display_title_content_truncates_long_values_with_remaining_count():
    html = display_title_content("T" * 140, "C" * 400)

    assert "T" * 120 in html
    assert "C" * 320 in html
    assert "后面还有 20 字符" in html
    assert "后面还有 80 字符" in html
    assert 'class="more-note"' in html


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


def test_event_timeline_card_emits_histogram_and_swimlane_with_per_dim_color_rules():
    """The card defaults to swimlane view, exposes a histogram view, and
    keeps three color-by dimensions (source/project/device). The dropped
    "ticks" view must no longer be in the markup."""
    events = [
        {"start": "2026-05-14T09:30:00", "source": "codex", "project": "daytrace",
         "device_id": "Mac", "title": "morning prompt"},
        {"start": "2026-05-14T09:31:30", "source": "codex", "project": "daytrace",
         "device_id": "Mac", "title": "follow up"},
        {"start": "2026-05-14T14:05:00", "source": "git", "project": "daytrace",
         "device_id": "Mac", "title": "commit foo"},
        {"start": "2026-05-14T22:48:00", "source": "codex", "project": "mtl",
         "device_id": "omen-wsl", "title": "evening review"},
        # Malformed rows must be tolerated, not crash.
        {"start": "", "source": "codex", "title": "no time"},
        {"start": "2026-05-14T25:00:00", "source": "codex", "title": "bad hour"},
    ]
    # Pin legacy 00:00-24:00 axis so the position math and label assertions
    # below stay easy to read. Production default is boundary_hour=4.
    html = event_timeline_card(events, "2026-05-14", boundary_hour=0)

    # Container, default style is swimlane (user preference), default mode source
    assert 'class="card wide-card timeline-card"' in html
    assert 'data-mode="source"' in html
    assert 'data-style="swimlane"' in html
    assert 'data-date="2026-05-14"' in html

    # Style tabs: ticks view is gone, only swimlane + histogram remain
    assert 'data-style="swimlane">泳道' in html
    assert 'data-style="histogram">直方图' in html
    assert 'data-style="ticks"' not in html

    # In-card color-by tabs were moved out to the global dim-bar; the card
    # no longer ships its own data-target buttons.
    assert 'data-target=' not in html

    # Hour grid labels every 2h (last tick now wraps back to 00 instead of 24
    # so the same labels work for shifted-boundary days).
    for label in ("00", "06", "12", "18"):
        assert f">{label}<" in html

    # Histogram + swimlane now cover 5 dimensions (source/project/device/location/activity).
    # 4 valid events fall in 3 distinct 20-min bins per dim → 3 × 5 = 15 bins.
    assert html.count('class="tl-bin"') == 3 * 5

    # Swimlane: 5 panes (one per dim) + 1 always-visible Overall lane.
    # Per-pane ticks use exact class "tl-swim-tick"; overall ticks have a
    # second class so they're counted separately.
    assert html.count('class="tl-swim-pane"') == 5
    assert html.count('class="tl-swim-tick"') == 4 * 5
    assert html.count('class="tl-swim-row tl-swim-overall"') == 1
    # The class name appears in BOTH the tick elements AND in the generated
    # CSS color rules; count only the actual tick elements.
    assert html.count('class="tl-swim-tick tl-swim-tick-overall"') == 4
    # And the CSS rules that recolor overall ticks per active dim exist
    # for the dominant value of each dimension.
    assert '.tl-swim-tick-overall[data-source="codex"]' in html
    assert '.tl-swim-tick-overall[data-project="daytrace"]' in html
    assert '.tl-swim-tick-overall[data-device="Mac"]' in html

    # Tooltip wired
    assert 'class="tl-tooltip"' in html

    # Default-visible legend tracks the default mode
    assert 'class="tl-legend show" data-for="source"' in html
    assert 'class="tl-legend" data-for="project"' in html

    # Legend counts reflect per-dimension grouping (codex has 3 events)
    assert "×3" in html

    # Empty input falls back to a placeholder without crashing
    empty_html = event_timeline_card([], "2026-05-14", boundary_hour=0)
    assert "当天暂无事件" in empty_html
    assert empty_html.count('class="tl-bin"') == 0
    assert empty_html.count('class="tl-swim-tick"') == 0
