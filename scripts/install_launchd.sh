#!/bin/sh
# Dashboard only. Daily collection configuration is prepared but not activated.
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec "$REPO/.venv/bin/python" "$REPO/scripts/install_local_launchd.py" "$@"
