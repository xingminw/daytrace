#!/bin/sh
# Local collection only. Scheduling requires explicit user approval.
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
export DAYTRACE_TIMEZONE="${DAYTRACE_TIMEZONE:-Asia/Shanghai}"
export DAYTRACE_DISABLE_AI=1
exec "$REPO/.venv/bin/python" "$REPO/scripts/run_local.py" "$@"
