"""Tests for AI output shape validators and corrective-retry behavior.

Each AI channel has a `validate_*` function that pins the expected JSON
shape. When the live model returns something off-shape, we want:
  - first try: ShapeError raised → corrective retry sent
  - second try: validator passes → result returned with combined usage
  - if still bad: ShapeError surfaces, orchestrator stores it as channel error

Cost is accumulated across the original + retry call so accounting is honest.
"""

from __future__ import annotations

import pytest

from daytrace import ai_client
from daytrace.ai_client import LLMResponse, ShapeError, call_json_validated
from daytrace.ai_report import (
    validate_continuity,
    validate_overview,
    validate_project_continuity_batch,
    validate_project_summary_batch,
)


# ---- per-channel validators -----------------------------------------

def test_validate_overview_accepts_well_shaped_payload():
    payload = {
        "headline": "推进 A 项目",
        "narrative": "今天主要在 A 上推进。",
        "highlights": ["实现 X", "修了 Y"],
        "concerns": ["待跟进 Z"],
    }
    out = validate_overview(payload)
    # v14: legacy plain-string inputs get auto-wrapped into bilingual dicts
    # with the original value in 'zh' and empty 'en' (the renderer's
    # fallback handles the missing language).
    assert out["headline"]   == {"zh": "推进 A 项目", "en": ""}
    assert out["highlights"] == [
        {"zh": "实现 X", "en": ""}, {"zh": "修了 Y", "en": ""}
    ]


def test_validate_overview_rejects_missing_headline():
    with pytest.raises(ShapeError, match="headline"):
        validate_overview({"narrative": "hi"})


def test_validate_overview_rejects_non_object():
    with pytest.raises(ShapeError, match="top-level"):
        validate_overview([1, 2, 3])


def test_validate_overview_defaults_empty_lists_when_omitted():
    payload = {"headline": "h", "narrative": "n"}  # no highlights / suggestions
    out = validate_overview(payload)
    assert out["highlights"] == []
    assert out["suggestions"] == []


def test_validate_continuity_normalizes_unknown_momentum():
    out = validate_continuity({
        "relation_to_yesterday": "节奏类似",
        "momentum": "wobbly",  # not in allowed set
        "notable_changes": [],
    })
    assert out["momentum"] == "steady"


def test_validate_project_summary_batch_requires_by_project_object():
    with pytest.raises(ShapeError, match="by_project"):
        validate_project_summary_batch({"foo": "bar"})


def test_validate_project_summary_batch_accepts_and_normalizes():
    out = validate_project_summary_batch({
        "by_project": {
            "A": {"summary": "做了 X", "what_was_done": ["实现 X"], "status": "in_progress"},
            "B": {"summary": "继续 Y", "what_was_done": [], "next_steps": ["再跑一次"]},
        }
    })
    assert set(out["by_project"]) == {"A", "B"}
    assert out["by_project"]["A"]["status"] == "in_progress"
    assert out["by_project"]["B"]["status"] == "unknown"  # default
    assert out["by_project"]["B"]["summary"] == {"zh": "继续 Y", "en": ""}
    assert out["by_project"]["B"]["next_steps"] == [{"zh": "再跑一次", "en": ""}]


def test_validate_project_continuity_batch_keeps_relation_null():
    out = validate_project_continuity_batch({
        "by_project": {
            "A": {"relation_to_previous": None, "momentum": "new"},
        }
    })
    assert out["by_project"]["A"]["relation_to_previous"] is None
    assert out["by_project"]["A"]["momentum"] == "new"


# ---- call_json_validated: corrective retry ---------------------------

def _resp(payload, *, tokens_in=100, tokens_out=50, cost=0.0001):
    return LLMResponse(json=payload, tokens_in=tokens_in, tokens_out=tokens_out,
                       cost_usd=cost, model="test-model")


def test_call_json_validated_passes_through_on_good_shape(monkeypatch):
    calls = []

    def fake_call_json(**kwargs):
        calls.append(kwargs["user"])
        return _resp({"headline": "h", "narrative": "n"})

    monkeypatch.setattr(ai_client, "call_json", fake_call_json)
    resp = call_json_validated(
        system="s", user="u", validator=validate_overview, max_tokens=100,
    )
    # v14 wraps the plain "h" into a bilingual dict
    assert resp.json["headline"] == {"zh": "h", "en": ""}
    assert resp.tokens_in == 100
    assert len(calls) == 1   # no retry needed


def test_call_json_validated_retries_once_on_bad_shape(monkeypatch):
    calls = []

    def fake_call_json(**kwargs):
        calls.append(kwargs["user"])
        if len(calls) == 1:
            return _resp({"narrative": "n"})  # missing headline
        return _resp({"headline": "h", "narrative": "n"}, tokens_in=200, cost=0.0002)

    monkeypatch.setattr(ai_client, "call_json", fake_call_json)
    resp = call_json_validated(
        system="s", user="u", validator=validate_overview, max_tokens=100,
    )
    # Retry sent
    assert len(calls) == 2
    # Corrective prompt embedded the validator's reason
    assert "headline" in calls[1]
    # Final value is the second, valid one
    # v14 wraps the plain "h" into a bilingual dict
    assert resp.json["headline"] == {"zh": "h", "en": ""}
    # Tokens / cost accumulated from both calls
    assert resp.tokens_in == 100 + 200
    assert resp.tokens_out == 50 + 50
    assert resp.cost_usd == pytest.approx(0.0003, abs=1e-9)


def test_call_json_validated_surfaces_shape_error_after_retries(monkeypatch):
    def fake_call_json(**kwargs):
        return _resp({"narrative": "n"})  # never has headline

    monkeypatch.setattr(ai_client, "call_json", fake_call_json)
    with pytest.raises(ShapeError, match="headline"):
        call_json_validated(
            system="s", user="u", validator=validate_overview, max_tokens=100,
        )


def test_call_json_validated_can_disable_retry(monkeypatch):
    calls = []

    def fake_call_json(**kwargs):
        calls.append(1)
        return _resp({"narrative": "n"})  # bad

    monkeypatch.setattr(ai_client, "call_json", fake_call_json)
    with pytest.raises(ShapeError):
        call_json_validated(
            system="s", user="u", validator=validate_overview,
            shape_retries=0, max_tokens=100,
        )
    assert len(calls) == 1   # no retry attempted
