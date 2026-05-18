#!/bin/bash
# One-shot script: organically merge ~/Projects/daily-manager into this
# daytrace repo. Run from the daytrace repo root, on a clean working tree.
#
# Strategy: copy files by FUNCTION into daytrace's existing layout, NOT
# under a `workflows/` umbrella. Skills become top-level skills/,
# automations become top-level automations/, docs and config merge into
# the existing dirs.
#
# History: daily-manager's 9 commits are NOT preserved (git subtree can't
# split content across multiple target dirs). The old repo at
# github.com/xingminw/daily-assistant should be archived after this runs.
#
# Idempotent-ish: each cp uses -n (no-clobber) so re-running won't
# overwrite, but it WILL create duplicate-but-same files if you delete
# targets in between. Inspect git status before committing.

set -euo pipefail

DM="${HOME}/Projects/daily-manager"
DT="$(pwd)"

if [ ! -d "$DM" ]; then
  echo "!!! daily-manager not found at $DM"
  exit 1
fi
if [ ! -d "$DT/daytrace" ]; then
  echo "!!! run from daytrace repo root (no ./daytrace package found)"
  exit 1
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "!!! working tree dirty — commit or stash first"
  exit 1
fi

echo "=== merging $DM → $DT ==="

# 1. Skills: rename daily-manager/skills/daily-manager → skills/work-items
mkdir -p "$DT/skills"
cp -Rn "$DM/skills/personal-feishu-interface" "$DT/skills/personal-feishu-interface"
cp -Rn "$DM/skills/daily-manager"             "$DT/skills/work-items"

# 2. Automations: 4 specs + codex/ subdir
mkdir -p "$DT/automations"
cp -Rn "$DM/automations/." "$DT/automations/"

# 3. Docs: feishu workflow + workspace (workflow.md renamed to avoid collision)
cp -n "$DM/docs/feishu-workspace.md"  "$DT/docs/feishu-workspace.md"
cp -n "$DM/docs/workflow.md"          "$DT/docs/feishu-workflow.md"
# docs/README.md stays out — daytrace docs don't have its own README

# 4. Config: lark workspace template + filled config
cp -n "$DM/config/lark_daily_workspace.example.json" \
      "$DT/config/lark_daily_workspace.example.json"
cp -n "$DM/config/lark_daily_workspace.json" \
      "$DT/config/lark_daily_workspace.json"

# 5. Maintenance script
cp -n "$DM/scripts/sync_skills.sh" "$DT/scripts/sync_skills.sh"
chmod +x "$DT/scripts/sync_skills.sh"

echo
echo "=== unstaged changes after copy ==="
git status --short

echo
echo "Next manual steps (NOT done automatically):"
echo "  1. Review changes:        git diff --stat"
echo "  2. Rewrite README.md:     mention skills/, automations/, workflows"
echo "  3. Write AGENTS.md:       top-level Agent module map (see below)"
echo "  4. Commit + push:         git add -A && git commit -m '...'"
echo "  5. Archive old repo:      mark xingminw/daily-assistant Archived on GitHub"
echo "  6. Local cleanup:         rm -rf ~/Projects/daily-manager  # only after push"
echo
echo "Suggested AGENTS.md content:"
echo "  - 'daytrace/, dashboard/, scripts/' → observer code (Python)"
echo "  - 'skills/, automations/'           → portable Agent specs (declarative)"
echo "  - 'config/'                         → shared yaml/json configs"
echo "  - 'docs/'                           → architecture + Feishu workflow docs"
