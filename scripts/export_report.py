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
    archive_html_for_date,
    archive_html_for_week,
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
    html_path = sub_dir / f"{key}.html"
    md_path   = sub_dir / f"{key}.md"

    if not args.quiet:
        print(f"[export_report] rendering {kind} {key}")

    if kind == "daily":
        html = archive_html_for_date(args.db, key)
        md   = archive_markdown_for_date(args.db, key)
    else:
        html = archive_html_for_week(args.db, key)
        md   = archive_markdown_for_week(args.db, key)

    html_path.write_text(html, encoding="utf-8")
    md_path.write_text(md, encoding="utf-8")
    if not args.quiet:
        print(f"  ✓ {html_path.relative_to(REPO_ROOT)} ({len(html)/1024:.1f} KB)")
        print(f"  ✓ {md_path.relative_to(REPO_ROOT)}   ({len(md)/1024:.1f} KB)")

    # ── Optional: Feishu drive upload ────────────────────────────────
    if args.upload_feishu:
        from daytrace.report_delivery import upload_to_feishu_drive
        try:
            urls = upload_to_feishu_drive(html_path, md_path, kind=kind, key=key, quiet=args.quiet)
            if urls and not args.quiet:
                for label, url in urls.items():
                    print(f"  ↑ {label}: {url}")
        except Exception as e:
            print(f"[export_report] feishu upload failed: {e}", file=sys.stderr)
            return 3

    # ── Optional: email ──────────────────────────────────────────────
    if args.email:
        from daytrace.report_delivery import email_report
        try:
            email_report(kind=kind, key=key, md_text=md, html_path=html_path,
                         quiet=args.quiet)
        except Exception as e:
            print(f"[export_report] email failed: {e}", file=sys.stderr)
            return 4

    return 0


if __name__ == "__main__":
    sys.exit(main())
