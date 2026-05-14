#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daytrace.db import connect, init_db, upsert_events, query_summary
from daytrace.io import read_events


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--db", default="data/daytrace.sqlite")
    parser.add_argument("--events", nargs="+", required=True)
    args = parser.parse_args()
    all_events = []
    for pattern in args.events:
        matches = (
            list(Path().glob(pattern))
            if any(ch in pattern for ch in "*?[")
            else [Path(pattern)]
        )
        for path in matches:
            all_events.extend(read_events(path))
    con = connect(args.db)
    init_db(con)
    count = upsert_events(con, all_events, run_date=args.date)
    summary = query_summary(con, args.date)
    print(f"imported {count} events into {args.db}")
    print(
        f"summary: total={summary['total_events']} sources={len(summary['sources'])} projects={len(summary['projects'])} unattributed={summary['low_confidence']}"
    )


if __name__ == "__main__":
    main()
