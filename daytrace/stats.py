"""Deterministic per-day statistics for the daily report.

Every function in this module is pure: it takes a list of event dicts (as
returned by `daytrace.db.query_events`) and returns a structured value.
No I/O, no LLM, no time. Output is the value_json for a `day_channel` or
`day_project_channel` row.

The footprint model is the foundation. Each event is treated as occupying
a small interval; active time is the union of those intervals, so two
overlapping events don't double-count. For conversational sources
(codex / hermes) the footprint scales with character count, so a thoughtful
2-minute prompt outweighs an "ok" ack.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable

import os

MISC_PROJECT = "misc"

# Channel version. Bump any of these to invalidate cached rows.
# v2 = slot algo. v3 = shifted-day boundary (DAY_BOUNDARY_HOUR aware).
STATS_VERSION = "v4"  # v4 = drop misleading low_confidence (no confidence col)

# "A day" runs from DAY_BOUNDARY_HOUR:00 of the named date to the same hour
# of the next calendar date. Default 4 = 04:00, so a late-night session at
# 02:30 belongs to "yesterday" the way a human would feel it.
DAY_BOUNDARY_HOUR = int(os.environ.get("DAYTRACE_DAY_BOUNDARY_HOUR", "4"))


# ---- slot-based activity model ----------------------------------------
# We partition the day into fixed-size buckets ("slots"). A slot counts as
# active if *any* event falls inside it. Active time = active_slots * slot_min.
#
# This is cruder than per-event footprints (which we used in v1) but matches
# how humans actually feel time: "I was working between 09:00 and 09:05"
# regardless of whether you wrote one prompt or ten. It also avoids the
# under-counting bug where short footprints made quiet stretches look
# emptier than they really were.

ACTIVE_SLOT_MIN = 5          # bucket size in minutes (288 slots / day)
SLOTS_PER_DAY = (24 * 60) // ACTIVE_SLOT_MIN


# ---- low-level helpers -------------------------------------------------

def _minute_of_day(ts: str) -> int:
    """Parse HH:MM out of an ISO-ish timestamp; raise on malformed input."""
    if not ts or len(ts) < 16:
        raise ValueError(f"timestamp too short: {ts!r}")
    h = int(ts[11:13])
    m = int(ts[14:16])
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"out-of-range time: {ts!r}")
    return h * 60 + m


def _safe_minute(ts: str | None) -> int | None:
    try:
        return _minute_of_day(ts or "")
    except ValueError:
        return None


def _normalize_project(value: str | None) -> str:
    """Empty / None project becomes the literal 'misc' bucket."""
    if value is None:
        return MISC_PROJECT
    text = str(value).strip()
    return text or MISC_PROJECT


def _project_of(event: dict[str, Any]) -> str:
    # Tolerate both raw-DB shape and the dashboard's normalized shape.
    return _normalize_project(event.get("project") or event.get("project_guess"))


# ---- slot helpers ------------------------------------------------------

def _slot_set(events: Iterable[dict[str, Any]]) -> set[int]:
    """Return the set of slot indices [0, SLOTS_PER_DAY) that contain any event."""
    slots: set[int] = set()
    for ev in events:
        m = _safe_minute(ev.get("start"))
        if m is None:
            continue
        slots.add(m // ACTIVE_SLOT_MIN)
    return slots


# ---- channel: active_minutes ------------------------------------------

def channel_active_minutes(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Slot-based active time.

    Every 5-minute slot that contains at least one event counts as 5 minutes
    of active time. The day total is the union of slots across all sources;
    the per-source breakdown is each source's own slot count (so per-source
    totals can sum higher than the day total when two sources hit the same
    slot — that's the same property the old footprint-union algorithm had,
    and consumers already tolerate it).
    """
    total_slots = _slot_set(events)
    by_source: dict[str, int] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ev in events:
        grouped[ev.get("source") or "other"].append(ev)
    for src, src_events in grouped.items():
        by_source[src] = len(_slot_set(src_events)) * ACTIVE_SLOT_MIN
    return {
        "total": len(total_slots) * ACTIVE_SLOT_MIN,
        "by_source": by_source,
        "slot_min": ACTIVE_SLOT_MIN,
    }


def project_active_minutes(events: list[dict[str, Any]]) -> int:
    """Active minutes restricted to the given (already-filtered) events."""
    return len(_slot_set(events)) * ACTIVE_SLOT_MIN


# ---- channel: time_span ------------------------------------------------

def channel_time_span(events: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [m for m in (_safe_minute(e.get("start")) for e in events) if m is not None]
    if not valid:
        return {"first": None, "last": None, "span_min": 0}
    lo, hi = min(valid), max(valid)
    return {
        "first": f"{lo // 60:02d}:{lo % 60:02d}",
        "last": f"{hi // 60:02d}:{hi % 60:02d}",
        "span_min": hi - lo,
    }


# ---- channel: longest_focus_block --------------------------------------

FOCUS_GAP_MIN = 10  # events more than this far apart break a block


def channel_longest_focus_block(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Longest stretch with no gap > FOCUS_GAP_MIN between consecutive events.

    Returns None when the day has no events. The block's dominant source /
    project are by count within the block, tiebreaker by first occurrence.
    """
    timed = sorted(
        ((m, ev) for ev in events for m in [_safe_minute(ev.get("start"))] if m is not None),
        key=lambda p: p[0],
    )
    if not timed:
        return None
    best: list[tuple[int, dict[str, Any]]] = []
    cur: list[tuple[int, dict[str, Any]]] = [timed[0]]
    for prev, item in zip(timed, timed[1:]):
        prev_min, _ = prev
        cur_min, _ = item
        if cur_min - prev_min <= FOCUS_GAP_MIN:
            cur.append(item)
        else:
            if len(cur) > len(best):
                best = cur
            cur = [item]
    if len(cur) > len(best):
        best = cur
    start_min = best[0][0]
    end_min = best[-1][0]
    block_events = [ev for _, ev in best]
    dominant_source = _dominant(block_events, lambda e: e.get("source") or "other")
    dominant_project = _dominant(block_events, _project_of)
    return {
        "start": f"{start_min // 60:02d}:{start_min % 60:02d}",
        "end": f"{end_min // 60:02d}:{end_min % 60:02d}",
        "duration_min": end_min - start_min,
        "event_count": len(block_events),
        "dominant_source": dominant_source,
        "dominant_project": dominant_project,
    }


def _dominant(events: list[dict[str, Any]], key) -> str:
    counts = Counter(key(e) for e in events)
    if not counts:
        return ""
    return counts.most_common(1)[0][0]


# ---- channel: context_switches ----------------------------------------

def channel_context_switches(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Count adjacent project transitions where the gap is < FOCUS_GAP_MIN.

    Quiet gaps (e.g., lunch) are excluded so they don't inflate the metric.
    """
    timed = sorted(
        (
            (m, _project_of(ev))
            for ev in events
            for m in [_safe_minute(ev.get("start"))]
            if m is not None
        ),
        key=lambda p: p[0],
    )
    switches = 0
    per_hour = [0] * 24
    for (prev_min, prev_proj), (cur_min, cur_proj) in zip(timed, timed[1:]):
        if prev_proj != cur_proj and (cur_min - prev_min) <= FOCUS_GAP_MIN:
            switches += 1
            per_hour[cur_min // 60] += 1
    return {
        "count": switches,
        "per_hour": [{"hour": f"{h:02d}:00", "count": c} for h, c in enumerate(per_hour)],
    }


# ---- channel: peak_windows --------------------------------------------

def channel_peak_windows(events: list[dict[str, Any]], top_n: int = 3) -> list[dict[str, Any]]:
    """Top-N busiest hours by event count, descending."""
    counts = Counter()
    for ev in events:
        m = _safe_minute(ev.get("start"))
        if m is not None:
            counts[m // 60] += 1
    return [
        {"label": f"{hour:02d}:00-{(hour + 1) % 24:02d}:00", "count": c}
        for hour, c in counts.most_common(top_n)
    ]


# ---- channel: dimension_counts ----------------------------------------

def channel_dimension_counts(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Top-N count + share for each of the four dimensions."""
    total = len(events) or 1
    return {
        "by_source":   _dim_counts(events, lambda e: e.get("source") or "other",        total),
        "by_project":  _dim_counts(events, _project_of,                                  total),
        "by_device":   _dim_counts(events, lambda e: e.get("device_id") or "unknown",   total),
        "by_location": _dim_counts(events, lambda e: e.get("location_id") or "unknown", total),
    }


def _dim_counts(events, key, total: int) -> list[dict[str, Any]]:
    counts = Counter(key(e) for e in events)
    return [
        {"name": name, "count": c, "share": round(c / total, 4)}
        for name, c in counts.most_common()
    ]


# ---- channel: quality --------------------------------------------------

def channel_quality(events: list[dict[str, Any]]) -> dict[str, int]:
    """Quality / housekeeping counts.

    `low_confidence` was removed in v4: collectors emit a confidence float
    per event, but the `events.confidence` column was dropped in an earlier
    schema migration, so every event silently reads back as None → 0.0 →
    counted as low-confidence. The metric was therefore always = total,
    misleading anyone (including the AI summariser) who saw it.
    """
    sensitive = missing_project = 0
    for ev in events:
        if (ev.get("sensitivity") or "") in {"sensitive", "private"}:
            sensitive += 1
        if not (ev.get("project") or ev.get("project_guess")):
            missing_project += 1
    return {
        "sensitive": sensitive,
        "missing_project": missing_project,
    }


# ---- channel: source_mix / device_mix ---------------------------------

def channel_source_mix(events: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(e.get("source") or "other" for e in events))


def channel_device_mix(events: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(e.get("device_id") or "unknown" for e in events))


# ---- channel: top_titles ----------------------------------------------

def channel_top_titles(events: list[dict[str, Any]], k: int = 5) -> list[dict[str, Any]]:
    """Pick k representative titles. Ranked by title length desc (longer ≈
    more substantive), then start time asc as a stable tiebreaker. The old
    confidence-based ranking went away with the confidence field."""
    def rank(ev):
        return (-len(ev.get("title") or ""), ev.get("start") or "")
    seen: set[str] = set()
    out = []
    for ev in sorted(events, key=rank):
        title = (ev.get("title") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        out.append({"time": (ev.get("start") or "")[11:16], "title": title[:120]})
        if len(out) >= k:
            break
    return out


# ---- channel: event_density -------------------------------------------

def channel_event_density(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """24 entries, one per hour: {hour, count}."""
    counts = [0] * 24
    for ev in events:
        m = _safe_minute(ev.get("start"))
        if m is not None:
            counts[m // 60] += 1
    return [{"hour": f"{h:02d}:00", "count": counts[h]} for h in range(24)]


# ---- per-project helpers ----------------------------------------------

def split_events_by_project(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group events by project (None / empty → 'misc')."""
    by_project: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ev in events:
        by_project[_project_of(ev)].append(ev)
    return dict(by_project)


# ---- aggregate: compute every channel for one day ---------------------

def compute_day_stats_channels(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Run every day-level stats channel, return {channel_name: value}."""
    return {
        "time_span":           channel_time_span(events),
        "active_minutes":      channel_active_minutes(events),
        "longest_focus_block": channel_longest_focus_block(events),
        "context_switches":    channel_context_switches(events),
        "peak_windows":        channel_peak_windows(events),
        "dimension_counts":    channel_dimension_counts(events),
        "quality":             channel_quality(events),
    }


def compute_project_stats_channels(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Run every per-project stats channel for an already-filtered event list."""
    return {
        "time_span":      channel_time_span(events),
        "active_minutes": {"total": project_active_minutes(events)},
        "source_mix":     channel_source_mix(events),
        "device_mix":     channel_device_mix(events),
        "top_titles":     channel_top_titles(events),
        "event_density":  channel_event_density(events),
    }
