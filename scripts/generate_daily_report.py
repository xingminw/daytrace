#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daytrace.io import read_events
from daytrace.summarize import (
    aggregate_events,
    render_feishu_summary,
    render_markdown_report,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--events", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--feishu-out")
    args = parser.parse_args()
    events = []
    for pattern in args.events:
        matches = (
            list(Path().glob(pattern))
            if any(ch in pattern for ch in "*?[")
            else [Path(pattern)]
        )
        for path in matches:
            events.extend(read_events(path))
    daily = aggregate_events(args.date, events)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown_report(daily), encoding="utf-8")
    feishu_path = (
        Path(args.feishu_out) if args.feishu_out else out.with_suffix(".feishu.md")
    )
    feishu_path.write_text(render_feishu_summary(daily, str(out)), encoding="utf-8")
    print(
        f"wrote report to {out} and Feishu summary to {feishu_path} ({len(events)} events)"
    )


if __name__ == "__main__":
    main()
