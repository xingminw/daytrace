#!/bin/sh
# Manual local export only: no remote upload, email, or paid API.
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
export DAYTRACE_DISABLE_AI=1
exec "$REPO/.venv/bin/python" "$REPO/scripts/export_report.py" "$@"
