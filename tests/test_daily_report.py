"""End-to-end orchestrator tests: schema migration, channel registration,
cache invalidation, and the cross-table invariants we promised in the
design doc."""

from __future__ import annotations

import json

import pytest

from daytrace.channels import compute_events_hash
from daytrace.daily_report import (
    load_day_report,
    regenerate_day_from_db,
    registered_channel_names,
)
from daytrace.db import connect, init_db, upsert_events
from daytrace.schema import TraceEvent


def _ev(eid, start, *, source="codex", project="DayTrace",
        device="Mac", location="home", title="t", summary="s"):
    return TraceEvent(
        id=eid, source=source, kind="user_input",
        start=start, end=None, title=title, summary=summary,
        project_guess=project, sensitivity="normal",
        evidence={}, device_id=device, location_id=location,
        collector_id="hub-local",
    )


def _seed(tmp_path, events):
    db_path = tmp_path / "daytrace.sqlite"
    con = connect(db_path)
    init_db(con)
    upsert_events(con, events)
    return con


# ---- schema -----------------------------------------------------------

def test_migration_creates_four_new_tables(tmp_path):
    con = _seed(tmp_path, [])
    names = {
        row["name"] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"day_report", "day_channel", "day_project_report",
            "day_project_channel"} <= names


def test_registered_channels_include_stats_and_ai():
    names = registered_channel_names()
    # spot-check a few stats channels
    for s in ("time_span", "active_minutes", "longest_focus_block",
              "context_switches", "peak_windows", "dimension_counts",
              "quality"):
        assert s in names["day"]
    for s in ("time_span", "active_minutes", "source_mix",
              "device_mix", "top_titles", "event_density"):
        assert s in names["day_project"]
    # AI channels register themselves on import
    for s in ("ai_overview", "ai_continuity_day",
              "ai_project_summary_batch", "ai_project_continuity_batch"):
        assert s in names["day"]
    for s in ("ai_summary", "ai_continuity"):
        assert s in names["day_project"]


# ---- orchestrator: happy path ----------------------------------------

def test_regenerate_writes_day_report_and_per_project_rows(tmp_path, monkeypatch):
    # Force ai_client to look unavailable so AI channels return None gracefully
    # without hitting the real DeepSeek API in this happy-path test.
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    con = _seed(tmp_path, [
        _ev("e1", "2026-05-15T09:00:00", project="A"),
        _ev("e2", "2026-05-15T09:05:00", project="A"),
        _ev("e3", "2026-05-15T14:00:00", project="B"),
        _ev("e4", "2026-05-15T14:30:00", project=None),  # → misc
    ])
    report = regenerate_day_from_db(con, "2026-05-15", include_ai=True)
    assert report.total_events == 4

    payload = load_day_report(con, "2026-05-15")
    assert payload["day"]["total_events"] == 4
    project_names = {p["project"] for p in payload["projects"]}
    assert project_names == {"A", "B", "misc"}

    # Every project has its stats channels populated
    for proj in payload["projects"]:
        for ch in ("time_span", "active_minutes", "source_mix",
                   "device_mix", "top_titles", "event_density"):
            assert ch in proj["channels"], f"{proj['project']} missing {ch}"

    # AI ran (stub-degraded path: value=None, no error) and rows exist
    assert "ai_overview" in payload["day"]["channels"]
    assert payload["day"]["channels"]["ai_overview"] is None
    for proj in payload["projects"]:
        assert "ai_summary" in proj["channels"]


def test_include_ai_false_skips_ai_channels(tmp_path):
    con = _seed(tmp_path, [_ev("e1", "2026-05-15T09:00:00", project="A")])
    report = regenerate_day_from_db(con, "2026-05-15", include_ai=False)
    assert "ai_overview" in report.day_channels_skipped
    assert "ai_overview" not in report.day_channels_run
    payload = load_day_report(con, "2026-05-15")
    assert "ai_overview" not in payload["day"]["channels"]


# ---- cache invalidation ----------------------------------------------

def test_unchanged_events_make_second_run_skip_everything(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    con = _seed(tmp_path, [_ev("e1", "2026-05-15T09:00:00")])
    regenerate_day_from_db(con, "2026-05-15", include_ai=True)
    second = regenerate_day_from_db(con, "2026-05-15", include_ai=True)
    # Nothing ran on the second pass — all channels were cache-fresh.
    assert second.day_channels_run == []
    for proj_skipped in second.project_channels_skipped.values():
        assert len(proj_skipped) > 0


def test_force_reruns_everything(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    con = _seed(tmp_path, [_ev("e1", "2026-05-15T09:00:00")])
    regenerate_day_from_db(con, "2026-05-15", include_ai=True)
    forced = regenerate_day_from_db(con, "2026-05-15", include_ai=True, force=True)
    assert len(forced.day_channels_run) > 0


def test_event_change_invalidates_cache(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    con = _seed(tmp_path, [_ev("e1", "2026-05-15T09:00:00", title="orig")])
    regenerate_day_from_db(con, "2026-05-15", include_ai=True)
    # Edit title — should change events_hash and invalidate everything
    con.execute("UPDATE events SET title = ? WHERE id = ?", ("edited", "e1"))
    con.commit()
    second = regenerate_day_from_db(con, "2026-05-15", include_ai=True)
    assert "active_minutes" in second.day_channels_run
    assert "ai_overview" in second.day_channels_run


# ---- invariants -------------------------------------------------------

def test_invariant_sum_of_project_event_counts_equals_day_total(tmp_path):
    """Σ day_project_report.event_count == day_report.total_events"""
    con = _seed(tmp_path, [
        _ev("e1", "2026-05-15T09:00:00", project="A"),
        _ev("e2", "2026-05-15T09:01:00", project="A"),
        _ev("e3", "2026-05-15T09:02:00", project="B"),
        _ev("e4", "2026-05-15T09:03:00", project=None),
    ])
    regenerate_day_from_db(con, "2026-05-15", include_ai=False)
    day_total = con.execute(
        "SELECT total_events FROM day_report WHERE date = ?", ("2026-05-15",)
    ).fetchone()["total_events"]
    project_sum = con.execute(
        "SELECT SUM(event_count) AS s FROM day_project_report WHERE date = ?",
        ("2026-05-15",),
    ).fetchone()["s"]
    assert day_total == project_sum == 4


def test_invariant_project_shares_sum_to_one(tmp_path):
    con = _seed(tmp_path, [
        _ev(f"e{i}", f"2026-05-15T09:{i:02d}:00", project=f"P{i % 3}")
        for i in range(13)
    ])
    regenerate_day_from_db(con, "2026-05-15", include_ai=False)
    rows = con.execute(
        "SELECT share FROM day_project_report WHERE date = ?", ("2026-05-15",)
    ).fetchall()
    assert abs(sum(r["share"] for r in rows) - 1.0) < 1e-3


def test_invariant_project_active_minutes_sum_ge_day_active(tmp_path):
    """Two projects sharing a 5-min slot → per-project total exceeds day total."""
    con = _seed(tmp_path, [
        _ev("e1", "2026-05-15T09:00:00", project="A"),
        _ev("e2", "2026-05-15T09:03:00", project="B"),  # same 5-min slot
    ])
    regenerate_day_from_db(con, "2026-05-15", include_ai=False)
    day_am = con.execute(
        "SELECT active_minutes FROM day_report WHERE date = ?", ("2026-05-15",)
    ).fetchone()["active_minutes"]
    project_am_sum = con.execute(
        "SELECT SUM(active_minutes) AS s FROM day_project_report WHERE date = ?",
        ("2026-05-15",),
    ).fetchone()["s"]
    assert day_am == 5
    assert project_am_sum == 10
    assert project_am_sum >= day_am


# ---- continuity stub --------------------------------------------------

def test_ai_continuity_day_returns_none_for_first_day(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    con = _seed(tmp_path, [_ev("e1", "2026-05-15T09:00:00")])
    regenerate_day_from_db(con, "2026-05-15", include_ai=True)
    row = con.execute(
        "SELECT value_json FROM day_channel"
        " WHERE date = ? AND channel = 'ai_continuity_day'",
        ("2026-05-15",),
    ).fetchone()
    # First day → no prior overview → value_json stored as JSON "null"
    assert row["value_json"] == "null"


def test_ai_path_uses_mocked_deepseek_and_records_cost(tmp_path, monkeypatch):
    """Mock ai_client.call_json so we exercise the AI pipeline (prompt
    construction, channel writes, slice reads, cost recording) without
    spending real API tokens."""
    from daytrace import ai_client, ai_report

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    captured: list[dict] = []

    def fake_call_json(*, system, user, max_tokens=2048, **kwargs):
        captured.append({"system": system, "user": user})
        if "项目进展助手" in system:
            return ai_client.LLMResponse(
                json={"by_project": {
                    "A": {"summary": "在 A 上推进了 X",
                          "what_was_done": ["实现 X", "修了 Y"],
                          "status": "in_progress", "next_steps": ["跑测试"]},
                    "misc": {"summary": "杂项整理",
                             "what_was_done": ["归档旧 ticket"],
                             "status": "done", "next_steps": []},
                }},
                tokens_in=2200, tokens_out=350, cost_usd=0.000539,
                model="deepseek-v4-flash",
            )
        if "跨天对比" in system:
            return ai_client.LLMResponse(
                json={"relation_to_yesterday": "节奏类似",
                      "momentum": "steady", "notable_changes": []},
                tokens_in=400, tokens_out=120, cost_usd=0.000160,
                model="deepseek-v4-flash",
            )
        if "项目跨天" in system:
            return ai_client.LLMResponse(
                json={"by_project": {
                    "A": {"relation_to_previous": "继续推进", "momentum": "rising"},
                    "misc": {"relation_to_previous": "首次出现", "momentum": "new"},
                }},
                tokens_in=600, tokens_out=180, cost_usd=0.000240,
                model="deepseek-v4-flash",
            )
        if "活动分类" in system:
            return ai_client.LLMResponse(
                json={"labels": {"e1": "开发", "e2": "开发", "e3": "杂项"}},
                tokens_in=500, tokens_out=150, cost_usd=0.000200,
                model="deepseek-v4-flash",
            )
        # Default: ai_overview
        return ai_client.LLMResponse(
            json={"headline": "推进 A, 顺手清杂项",
                  "narrative": "上午 A, 下午继续。整体平稳。",
                  "highlights": ["A 的 X 落地", "归档 5 条 misc"],
                  "concerns": []},
            tokens_in=1800, tokens_out=420, cost_usd=0.000588,
            model="deepseek-v4-flash",
        )

    monkeypatch.setattr(ai_client, "call_json", fake_call_json)
    monkeypatch.setattr(ai_report.ai_client, "call_json", fake_call_json)

    con = _seed(tmp_path, [
        _ev("e1", "2026-05-15T09:00:00", project="A", title="实现 X"),
        _ev("e2", "2026-05-15T09:30:00", project="A", title="修 Y"),
        _ev("e3", "2026-05-15T14:00:00", project=None, title="归档"),
    ])
    regenerate_day_from_db(con, "2026-05-15", include_ai=True)

    payload = load_day_report(con, "2026-05-15")
    # All four AI day channels populated with mocked content
    assert payload["day"]["channels"]["ai_overview"]["headline"] == "推进 A, 顺手清杂项"
    assert payload["day"]["channels"]["ai_project_summary_batch"]["by_project"]["A"]["status"] == "in_progress"

    # Per-project slices read the batch correctly
    by_proj = {p["project"]: p for p in payload["projects"]}
    assert by_proj["A"]["channels"]["ai_summary"]["summary"] == "在 A 上推进了 X"
    # No prior day exists → continuity short-circuits to "new" without API call.
    assert by_proj["A"]["channels"]["ai_continuity"]["momentum"] == "new"

    # Cost recorded
    cost_total = con.execute(
        "SELECT SUM(cost_usd) AS c FROM day_channel WHERE date = ? AND generator = 'ai'",
        ("2026-05-15",),
    ).fetchone()["c"]
    assert cost_total > 0

    # Cache: second run skips API entirely
    captured.clear()
    regenerate_day_from_db(con, "2026-05-15", include_ai=True)
    assert captured == [], "second run should hit cache, not the API"


def test_events_hash_changes_when_title_edited():
    events = [
        {"id": "x", "start": "2026-05-15T09:00:00", "title": "a", "summary": "s"},
    ]
    h1 = compute_events_hash(events)
    events[0]["title"] = "b"
    h2 = compute_events_hash(events)
    assert h1 != h2
