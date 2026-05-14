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
        confidence=0.9,
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


def test_trace_event_rejects_bad_confidence():
    try:
        sample_event(confidence=1.5)
    except ValueError as exc:
        assert "confidence" in str(exc)
    else:
        raise AssertionError("expected bad confidence to fail")


def test_jsonl_write_read_append(tmp_path):
    path = tmp_path / "events" / "test.jsonl"
    first = sample_event(id="evt-1")
    second = sample_event(id="evt-2", title="Second")
    write_events(path, [first])
    append_events(path, [second])
    assert read_events(path) == [first, second]
