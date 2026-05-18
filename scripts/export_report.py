#!/usr/bin/env python3
"""Export a daily / weekly DayTrace report as standalone HTML + Markdown.

Writes to data/reports/{daily,weekly}/<key>.{html,md}. Optionally uploads
the pair to a Feishu drive folder (auto-created on first run) and / or
sends the Markdown as an email body via SMTP.

Usage examples:

  # Daily report for yesterday (shifted-day boundary aware)
  python scripts/export_report.py --date 2026-05-17

  # Weekly report
  python scripts/export_report.py --week 2026-W20

  # Push to Feishu drive (writes folder token on first run)
  python scripts/export_report.py --week 2026-W20 --upload-feishu

  # Email (reads agent gmail credentials from ~/.daytrace/secrets.env)
  python scripts/export_report.py --week 2026-W20 --email

  # Cron-flavoured: do everything
  python scripts/export_report.py --week 2026-W20 --upload-feishu --email
"""
from __future__ import annotations

import argparse
import sys
from datetime import date as _date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from daytrace.report_export import (
    archive_markdown_for_date,
    archive_markdown_for_week,
)


DEFAULT_DB = REPO_ROOT / "data" / "daytrace.sqlite"
DEFAULT_OUT = REPO_ROOT / "data" / "reports"


def _yesterday_shifted(boundary_hour: int = 4) -> str:
    """The 'work day' that just ended at <boundary_hour>:00. If it's
    currently before the boundary, that's two days ago."""
    from datetime import datetime
    now = datetime.now()
    d = now.date() - timedelta(days=1)
    if now.hour < boundary_hour:
        d = d - timedelta(days=1)
    return d.isoformat()


def _last_iso_week() -> str:
    """Last completed ISO week label (YYYY-Www)."""
    today = _date.today()
    last_week_anchor = today - timedelta(days=7)
    iso_year, iso_week, _ = last_week_anchor.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = ap.add_mutually_exclusive_group()
    target.add_argument("--date", help="YYYY-MM-DD; defaults to yesterday (shifted-day)")
    target.add_argument("--week", help="YYYY-Www; defaults to last completed ISO week")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT,
                    help="Root output dir. Daily → <out>/daily/<date>.{html,md}; weekly → <out>/weekly/")
    ap.add_argument("--upload-feishu", action="store_true",
                    help="Upload HTML + MD to the configured Feishu drive folder")
    ap.add_argument("--email", action="store_true",
                    help="Email the Markdown to the configured recipient (weekly only by default)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    # Resolve target
    if args.date and args.week:
        ap.error("--date and --week are mutually exclusive")
    if args.date:
        kind, key = "daily", args.date
    elif args.week:
        kind, key = "weekly", args.week
    else:
        # Default depends on day of week. On Mondays, prefer the weekly.
        # Otherwise prefer yesterday's daily.
        today = _date.today()
        if today.weekday() == 0:  # Monday
            kind, key = "weekly", _last_iso_week()
        else:
            kind, key = "daily", _yesterday_shifted()

    if not args.db.exists():
        print(f"[export_report] db not found: {args.db}", file=sys.stderr)
        return 2

    sub_dir = args.out_dir / kind
    sub_dir.mkdir(parents=True, exist_ok=True)
    md_path = sub_dir / f"{key}.md"

    if not args.quiet:
        print(f"[export_report] rendering {kind} {key}")

    # ── Charts: per-task stacked histogram + donut, written as PNGs in
    #    the same dir as the MD so lark-cli +import inlines them, and
    #    email's add_related() picks them up via cid.
    chart_filenames: list[str] = []
    chart_paths: list = []
    try:
        from daytrace.report_charts import render_daily_charts, render_weekly_charts
        from daytrace.db import connect as _connect
        _con = _connect(args.db)
        charts = render_daily_charts(_con, key) if kind == "daily" else render_weekly_charts(_con, key)
        for chart_key, png_bytes in charts.items():
            fname = f"{key}-{chart_key}.png"  # e.g. 2026-W20-hist.png
            (sub_dir / fname).write_bytes(png_bytes)
            chart_filenames.append(fname)
            chart_paths.append(sub_dir / fname)
        if chart_filenames and not args.quiet:
            for f in chart_filenames:
                print(f"  ✓ chart {f} ({(sub_dir / f).stat().st_size/1024:.1f} KB)")
    except Exception as e:
        print(f"[export_report] chart render failed (continuing without): {e}", file=sys.stderr)

    if kind == "daily":
        md = archive_markdown_for_date(args.db, key, chart_names=chart_filenames)
    else:
        md = archive_markdown_for_week(args.db, key, chart_names=chart_filenames)

    md_path.write_text(md, encoding="utf-8")
    if not args.quiet:
        print(f"  ✓ {md_path.relative_to(REPO_ROOT)} ({len(md)/1024:.1f} KB)")

    # ── Optional: Feishu Docs import (MD → docx as native cloud doc) ──
    feishu_urls: dict = {}
    if args.upload_feishu:
        from daytrace.report_delivery import import_md_to_feishu_docs
        try:
            feishu_urls = import_md_to_feishu_docs(md_path, kind=kind, key=key, quiet=args.quiet) or {}
        except Exception as e:
            print(f"[export_report] feishu import failed: {e}", file=sys.stderr)
            return 3

    # ── Optional: email — HTML body, link to Feishu Docs + live dashboard ──
    if args.email:
        from daytrace.report_delivery import email_report, dashboard_url
        try:
            links = {**feishu_urls}
            ds_url = dashboard_url(kind=kind, key=key)
            if ds_url:
                links["dashboard"] = ds_url
            email_report(kind=kind, key=key, md_text=md,
                         links=links, chart_paths=chart_paths,
                         quiet=args.quiet)
        except Exception as e:
            print(f"[export_report] email failed: {e}", file=sys.stderr)
            return 4

    return 0


if __name__ == "__main__":
    sys.exit(main())
