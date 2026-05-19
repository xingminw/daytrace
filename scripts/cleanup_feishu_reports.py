#!/usr/bin/env python3
"""Sweep the DayTrace Feishu drive folders, keeping only the newest
docx per name. Useful after a run of failed/duplicate exports left
many revisions behind.

Usage:
    python scripts/cleanup_feishu_reports.py            # dry-run preview
    python scripts/cleanup_feishu_reports.py --apply    # actually delete

Targets the daily_token + weekly_token folders from
config/feishu_drive.yaml.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

try:
    import yaml  # PyYAML
except ImportError:
    yaml = None


def _lark(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["lark-cli", *args, "--as", "user"],
                          capture_output=True, text=True)


def _list_folder(folder_token: str) -> list[dict]:
    r = _lark(["drive", "files", "list",
               "--params", f'{{"folder_token":"{folder_token}"}}'])
    if r.returncode != 0:
        print(f"  ! list failed: {r.stderr.strip()}", file=sys.stderr)
        return []
    try:
        return (json.loads(r.stdout).get("data") or {}).get("files") or []
    except Exception:
        return []


def _delete(token: str, type_: str) -> bool:
    r = _lark(["drive", "+delete", "--file-token", token,
               "--type", type_, "--yes"])
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="Actually delete (default is dry-run)")
    ap.add_argument("--drop-raw-files", action="store_true", default=True,
                    help="Also drop .html / .md raw uploads (default: yes — only docx is current)")
    args = ap.parse_args()

    cfg_path = REPO / "config" / "feishu_drive.yaml"
    if not cfg_path.exists():
        print("config/feishu_drive.yaml missing — nothing to clean", file=sys.stderr)
        return 2
    if yaml is None:
        print("PyYAML not installed", file=sys.stderr)
        return 2
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    targets = [
        ("daily",  cfg.get("daily_token")),
        ("weekly", cfg.get("weekly_token")),
    ]

    total_deleted = 0
    for label, token in targets:
        if not token:
            print(f"[{label}] no folder token; skip")
            continue
        items = _list_folder(token)
        print(f"\n[{label}] {len(items)} files in folder")

        # Always purge raw uploads (.html / .md) — current pipeline only
        # produces docx; any 'file' type entry is a leftover from earlier
        # runs and just confuses the user.
        to_delete: list[dict] = []
        keep: list[dict] = []
        if args.drop_raw_files:
            non_files = []
            for it in items:
                if it.get("type") == "file":
                    to_delete.append(it)
                else:
                    non_files.append(it)
            items = non_files

        # Group remaining (docx, sheet, etc) by name, keep newest per name.
        by_name: dict[str, list[dict]] = defaultdict(list)
        for it in items:
            by_name[it["name"]].append(it)
        for name, fs in by_name.items():
            fs_sorted = sorted(fs, key=lambda x: int(x.get("modified_time", 0) or 0), reverse=True)
            keep.append(fs_sorted[0])
            to_delete.extend(fs_sorted[1:])

        for it in keep:
            print(f"  ✓ keep   {it['type']:<8} {it['name']:<22} {it['url']}")
        for it in to_delete:
            print(f"  ✗ delete {it['type']:<8} {it['name']:<22} {it['token']}")
            if args.apply:
                ok = _delete(it["token"], it["type"])
                total_deleted += 1 if ok else 0
                if not ok:
                    print("    FAILED")

    if args.apply:
        print(f"\nDeleted {total_deleted} files.")
    else:
        print("\n(dry-run; re-run with --apply to actually delete)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
