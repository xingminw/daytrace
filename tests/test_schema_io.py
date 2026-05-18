from daytrace.schema import TraceEvent
from daytrace.io import write_events, read_events, append_events


def sample_event(**overrides):
    data = dict(
        id="evt-1",
        source="git",
        kind="commit",
        start="2026-05-13T10:00:00",
        end=None,
        title="Initial commit",
        summary="Created project skeleton",
        project_guess="daytrace",
        sensitivity="normal",
        evidence={"repo": "daytrace", "hash": "abc123"},
        raw_ref=None,
    )
    data.update(overrides)
    return TraceEvent(**data)


def test_trace_event_roundtrip_dict():
    event = sample_event()
    restored = TraceEvent.from_dict(event.to_dict())
    assert restored == event
    assert restored.evidence["repo"] == "daytrace"


def test_trace_event_from_dict_silently_drops_legacy_confidence():
    """The confidence field was removed in v5 — TraceEvent.from_dict must
    keep ingesting legacy JSONL payloads that still carry it."""
    event = sample_event()
    restored = TraceEvent.from_dict({**event.to_dict(), "confidence": 0.7})
    assert restored == event


def test_jsonl_write_read_append(tmp_path):
    path = tmp_path / "events" / "test.jsonl"
    first = sample_event(id="evt-1")
    second = sample_event(id="evt-2", title="Second")
    write_events(path, [first])
    append_events(path, [second])
    assert read_events(path) == [first, second]
