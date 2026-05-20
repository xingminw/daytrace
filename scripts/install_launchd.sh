#!/bin/bash
# Render deploy/*.plist.template → ~/Library/LaunchAgents/<name>.plist and
# bootstrap each agent. Idempotent: unloads any previously loaded copy
# before bootstrapping the freshly rendered one.
#
# Substitutions:
#   __REPO__   → absolute path to this repo's root
#   __PYTHON__ → output of `which python3` (falls back to `which python`)

set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON" ]; then
  echo "error: neither python3 nor python found on PATH" >&2
  exit 1
fi

AGENTS_DIR="$HOME/Library/LaunchAgents"
mkdir -p "$AGENTS_DIR"
mkdir -p "$REPO/data/logs"

UID_NUM="$(id -u)"
COUNT=0

for tpl in "$REPO"/deploy/*.plist.template; do
  [ -f "$tpl" ] || continue
  name="$(basename "$tpl" .template)"
  dst="$AGENTS_DIR/$name"

  # Render template with sed; use a delimiter unlikely to appear in paths.
  sed -e "s|__REPO__|$REPO|g" -e "s|__PYTHON__|$PYTHON|g" "$tpl" > "$dst"

  # If already loaded, unload first so the new copy takes effect.
  if launchctl print "gui/$UID_NUM/${name%.plist}" >/dev/null 2>&1; then
    launchctl bootout "gui/$UID_NUM" "$dst" 2>/dev/null || true
  fi

  launchctl bootstrap "gui/$UID_NUM" "$dst"
  echo "  installed: $dst"
  COUNT=$((COUNT + 1))
done

echo
echo "Installed $COUNT launchd agents. Logs: $REPO/data/logs/"
