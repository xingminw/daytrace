from daytrace.db import connect, init_db, upsert_events, query_summary, query_events
from daytrace.schema import TraceEvent


def event(id="e1", source="git", project="daytrace"):
    return TraceEvent(
        id=id,
        source=source,
        kind="commit",
        start="2026-05-13T10:00:00",
        end=None,
        title="test event",
        summary="summary",
        project_guess=project,
        confidence=0.9,
        sensitivity="normal",
        evidence={"x": 1},
    )


def test_sqlite_event_store_roundtrip(tmp_path):
    con = connect(tmp_path / "daytrace.sqlite")
    init_db(con)
    assert (
        upsert_events(
            con,
            [event(), event(id="e2", source="docs", project=None)],
            run_date="2026-05-13",
        )
        == 2
    )
    summary = query_summary(con, "2026-05-13")
    assert summary["total_events"] == 2
    assert summary["low_confidence"] == 1
    events = query_events(con, "2026-05-13")
    assert len(events) == 2
    assert events[0]["evidence"]["x"] == 1
