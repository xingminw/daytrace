from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .schema import TraceEvent


def write_events(path: Path | str, events: Iterable[TraceEvent]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for event in events:
            f.write(
                json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
            )


def append_events(path: Path | str, events: Iterable[TraceEvent]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for event in events:
            f.write(
                json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
            )


def read_events(path: Path | str) -> list[TraceEvent]:
    p = Path(path)
    if not p.exists():
        return []
    events: list[TraceEvent] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(TraceEvent.from_dict(json.loads(line)))
    return events
