#!/bin/bash
# DayTrace daily wrapper — run by launchd at 04:30 (and re-runs on wake).
#
# SSH-direct model:
#   - This Mac is the hub. For every pending (device, shifted-day) pair
#     (per device_pull_log), it:
#       a) collects its own sources into ./inbox/<this-device>/<date>/
#       b) ssh's into each --remote, asks it to run collect_from_config locally
#       c) rsyncs the remote's inbox/<dev>/<date>/ slice back to ./inbox/
#       d) imports everything + regenerates day_report (incl. AI).
#
# If a remote is unreachable (WSL off, Tailscale not up, etc.) that
# (device, date) attempt is recorded as failed in device_pull_log and
# retried on the next run. Other devices / days still proceed.

set -u

REPO=/Users/xingminwang/Projects/daytrace
cd "$REPO"

# launchd seeds PATH minimally; restore enough to find python3, ssh, rsync.
export PATH="$HOME/.npm-global/bin:/opt/anaconda3/bin:/usr/local/bin:/usr/bin:/bin"

echo "=== $(date -Iseconds) DayTrace daily start ==="

python3 scripts/run_daily.py catchup \
  --config config/devices/mac.yaml \
  --remote omen-wsl=mtl-tail:/mnt/d/research-programs/daytrace

echo "=== $(date -Iseconds) DayTrace daily end ==="
