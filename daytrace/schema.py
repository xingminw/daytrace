from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TraceEvent:
    id: str
    source: str
    kind: str
    start: str
    end: str | None
    title: str
    summary: str
    project_guess: str | None
    confidence: float
    sensitivity: str
    evidence: dict[str, Any] = field(default_factory=dict)
    raw_ref: str | None = None
    device_id: str = "Mac"
    location_id: str = "unknown"
    collector_id: str = "hub-local"

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if not self.id:
            raise ValueError("id is required")
        if not self.source:
            raise ValueError("source is required")
        if not self.kind:
            raise ValueError("kind is required")
        if not self.start:
            raise ValueError("start is required")
        if not self.device_id:
            raise ValueError("device_id is required")
        if not self.location_id:
            raise ValueError("location_id is required")
        if not self.collector_id:
            raise ValueError("collector_id is required")
        if self.sensitivity not in {"normal", "private", "sensitive"}:
            raise ValueError("sensitivity must be normal/private/sensitive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "kind": self.kind,
            "start": self.start,
            "end": self.end,
            "title": self.title,
            "summary": self.summary,
            "project_guess": self.project_guess,
            "confidence": self.confidence,
            "sensitivity": self.sensitivity,
            "evidence": self.evidence,
            "raw_ref": self.raw_ref,
            "device_id": self.device_id,
            "location_id": self.location_id,
            "collector_id": self.collector_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TraceEvent":
        # Backward compatible with prototype JSONL events created before
        # device/location became first-class single-machine dimensions.
        data = {
            "device_id": "Mac",
            "location_id": "unknown",
            "collector_id": "hub-local",
            **data,
        }
        if data.get("device_id") in {"mac-hermes", "mac"}:
            data["device_id"] = "Mac"
        return cls(**data)
