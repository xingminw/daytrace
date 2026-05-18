"""Algorithm tests for daytrace/stats.py.

Each test pins a specific algorithm decision so we notice if the math
changes accidentally. The invariant tests at the bottom assert the
cross-table relationships that day_report and day_project_report rely on.
"""

from __future__ import annotations

import pytest

from daytrace import stats


def _ev(start, source="codex", project="DayTrace", device="Mac",
        location="home", title="t", summary="s",
        sensitivity="normal", end=None):
    return {
        "start": start, "end": end,
        "source": source,
        "project": project, "project_guess": project,
        "device_id": device, "location_id": location,
        "title": title, "summary": summary,
        "sensitivity": sensitivity,
    }


# ---- active_minutes (slot algorithm) --------------------------------

def test_active_minutes_slot_counts_each_5min_bucket_once():
    """Multiple events in the same 5-min slot collapse to one 5-min count.
    Events in different slots add 5 min each."""
    events = [
        _ev("2026-05-15T09:00:00", source="git"),    # slot 09:00-09:05
        _ev("2026-05-15T09:02:00", source="git"),    # same slot
        _ev("2026-05-15T09:05:00", source="git"),    # slot 09:05-09:10
        _ev("2026-05-15T09:30:00", source="git"),    # slot 09:30-09:35
    ]
    r = stats.channel_active_minutes(events)
    assert r["total"] == 15  # three distinct slots × 5 min
    assert r["by_source"] == {"git": 15}
    assert r["slot_min"] == 5


def test_active_minutes_per_source_can_exceed_total_when_slots_overlap():
    """Same slot hit by two sources → day total still 5, per-source sum 10."""
    events = [
        _ev("2026-05-15T09:00:00", source="codex"),
        _ev("2026-05-15T09:01:00", source="hermes"),  # same slot
    ]
    r = stats.channel_active_minutes(events)
    assert r["total"] == 5
    assert sum(r["by_source"].values()) == 10


def test_active_minutes_one_event_makes_whole_slot_active():
    """One event in the day = the whole 5-min slot counts (slot model)."""
    r = stats.channel_active_minutes([_ev("2026-05-15T09:00:00")])
    assert r["total"] == 5


# ---- time_span -------------------------------------------------------

def test_time_span_returns_first_last_and_minutes():
    events = [
        _ev("2026-05-15T08:14:00"),
        _ev("2026-05-15T23:47:00"),
        _ev("2026-05-15T12:00:00"),
    ]
    span = stats.channel_time_span(events)
    assert span == {"first": "08:14", "last": "23:47", "span_min": (23 * 60 + 47) - (8 * 60 + 14)}


def test_time_span_empty_day():
    assert stats.channel_time_span([]) == {"first": None, "last": None, "span_min": 0}


# ---- longest_focus_block --------------------------------------------

def test_longest_focus_block_finds_longest_uninterrupted_stretch():
    # Two stretches: [09:00, 09:05, 09:08] (len 3) and [11:00, 11:09, 11:18, 11:25] (len 4)
    events = [
        _ev("2026-05-15T09:00:00", project="A"),
        _ev("2026-05-15T09:05:00", project="A"),
        _ev("2026-05-15T09:08:00", project="A"),
        _ev("2026-05-15T11:00:00", project="B"),
        _ev("2026-05-15T11:09:00", project="B"),
        _ev("2026-05-15T11:18:00", project="B"),
        _ev("2026-05-15T11:25:00", project="B"),
    ]
    block = stats.channel_longest_focus_block(events)
    assert block["start"] == "11:00"
    assert block["end"] == "11:25"
    assert block["duration_min"] == 25
    assert block["event_count"] == 4
    assert block["dominant_project"] == "B"


def test_longest_focus_block_none_when_empty():
    assert stats.channel_longest_focus_block([]) is None


# ---- context_switches -----------------------------------------------

def test_context_switches_count_close_project_transitions_only():
    events = [
        _ev("2026-05-15T09:00:00", project="A"),
        _ev("2026-05-15T09:02:00", project="B"),  # close switch ✓
        _ev("2026-05-15T09:30:00", project="A"),  # gap > 10min, doesn't count
        _ev("2026-05-15T09:35:00", project="B"),  # close switch ✓
        _ev("2026-05-15T09:38:00", project="B"),  # same project
    ]
    cs = stats.channel_context_switches(events)
    assert cs["count"] == 2


# ---- dimension_counts -----------------------------------------------

def test_dimension_counts_normalize_misc_for_none_project():
    events = [
        _ev("2026-05-15T09:00:00", project=None),
        _ev("2026-05-15T09:01:00", project=""),
        _ev("2026-05-15T09:02:00", project="A"),
    ]
    dc = stats.channel_dimension_counts(events)
    names = {row["name"]: row["count"] for row in dc["by_project"]}
    assert names == {"misc": 2, "A": 1}


# ---- quality --------------------------------------------------------

def test_quality_counts_sensitive_and_missing_project():
    """low_confidence was removed in v4 — see channel_quality docstring."""
    events = [
        _ev("2026-05-15T09:00:00"),
        _ev("2026-05-15T09:01:00", sensitivity="sensitive"),
        _ev("2026-05-15T09:02:00", project=None),
        _ev("2026-05-15T09:03:00"),
    ]
    q = stats.channel_quality(events)
    assert q == {"sensitive": 1, "missing_project": 1}


# ---- top_titles -----------------------------------------------------

def test_top_titles_dedupes_and_keeps_k():
    events = [
        _ev("2026-05-15T09:00:00", title="repeat"),
        _ev("2026-05-15T09:01:00", title="repeat"),
        _ev("2026-05-15T09:02:00", title="other"),
        _ev("2026-05-15T09:03:00", title=""),
    ]
    titles = stats.channel_top_titles(events, k=5)
    titles_only = [row["title"] for row in titles]
    assert "repeat" in titles_only
    assert "other" in titles_only
    assert len(set(titles_only)) == len(titles_only)


# ---- per-project + cross-table invariants ---------------------------

def test_invariant_per_project_event_counts_sum_to_day_total():
    """Σ day_project.event_count == day.total_events.
    This is the strictest of the cross-table invariants."""
    events = [
        _ev("2026-05-15T09:00:00", project="A"),
        _ev("2026-05-15T09:01:00", project="B"),
        _ev("2026-05-15T09:02:00", project="A"),
        _ev("2026-05-15T09:03:00", project=None),  # misc
    ]
    by_project = stats.split_events_by_project(events)
    assert sum(len(es) for es in by_project.values()) == len(events)
    assert set(by_project.keys()) == {"A", "B", "misc"}


def test_invariant_per_project_active_minutes_sum_ge_day_total():
    """Σ day_project.active_minutes ≥ day.active_minutes — equality only
    when projects never share a 5-min slot."""
    events = [
        _ev("2026-05-15T09:00:00", project="A", source="codex"),  # slot 09:00
        _ev("2026-05-15T09:03:00", project="B", source="codex"),  # SAME slot
    ]
    day_total = stats.channel_active_minutes(events)["total"]  # 5 (one slot)
    by_project = stats.split_events_by_project(events)
    project_sum = sum(
        stats.project_active_minutes(events_in)
        for events_in in by_project.values()
    )
    assert day_total == 5
    assert project_sum == 10  # each project owns the slot independently
    assert project_sum >= day_total


def test_invariant_dimension_shares_sum_to_one():
    """Σ by_project[*].share ≈ 1.0 — sanity check for the shares column."""
    events = [_ev("2026-05-15T09:00:00", project=f"P{i % 3}") for i in range(11)]
    dc = stats.channel_dimension_counts(events)
    total_share = sum(row["share"] for row in dc["by_project"])
    assert abs(total_share - 1.0) < 1e-3
