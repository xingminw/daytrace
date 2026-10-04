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
    raise RuntimeError("Feishu integration is disconnected; remote content must remain untouched")


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
    print("Feishu integration is disconnected; no remote cleanup performed", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
