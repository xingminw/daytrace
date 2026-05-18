#!/bin/bash
# DayTrace weekly wrapper — run by launchd at Monday 06:00.
#
# Renders the last completed ISO week's report (HTML + MD), uploads to
# Feishu drive, and emails the Markdown body to the configured recipient.
#
# Assumes scripts/daytrace-daily.sh has already populated the week's
# per-day data + AI overviews. We additionally trigger the weekly AI
# summary by hitting the dashboard /weekly endpoint (it caches to disk
# on first render — so daily's catchup doesn't need to know about it).

set -u

REPO=/Users/xingminwang/Projects/daytrace
cd "$REPO"

# launchd seeds PATH minimally; restore enough to find python3, curl.
export PATH="$HOME/.npm-global/bin:/opt/anaconda3/bin:/usr/local/bin:/usr/bin:/bin"

echo "=== $(date -Iseconds) DayTrace weekly start ==="

# Compute last completed ISO week (YYYY-Www)
WEEK=$(python3 -c "
from datetime import date, timedelta
anchor = date.today() - timedelta(days=7)
y, w, _ = anchor.isocalendar()
print(f'{y}-W{w:02d}')
")
echo "target week: $WEEK"

# Make sure the weekly AI summary cache exists (the renderer writes it
# on the first GET). Start a short-lived dashboard server if one isn't
# already running.
DASH_PID=""
if ! lsof -tiTCP:8765 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "starting dashboard server on :8765 (temporary)..."
  python3 dashboard/server.py --db data/daytrace.sqlite --host 127.0.0.1 --port 8765 \
    > "data/logs/weekly-dashboard-$(date +%Y%m%d).log" 2>&1 &
  DASH_PID=$!
  sleep 2
fi

# Trigger weekly summary generation (writes cache to disk).
curl -s -o /dev/null "http://127.0.0.1:8765/weekly?week=${WEEK}"

# Render archive HTML + MD, upload to Feishu, send email.
python3 scripts/export_report.py --week "$WEEK" --upload-feishu --email

# Stop the temporary dashboard if we started it.
if [ -n "$DASH_PID" ]; then
  kill "$DASH_PID" 2>/dev/null || true
  echo "stopped temporary dashboard (pid $DASH_PID)"
fi

echo "=== $(date -Iseconds) DayTrace weekly end ==="
