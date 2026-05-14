# DayTrace Connection Inventory

Updated: 2026-05-14

This document records the first local probe of which data sources DayTrace can connect to right now, which need additional permission, and which are good candidates for an end-to-end closed-loop test.

## Summary

DayTrace can already form a useful closed loop with local data:

1. Local Git repositories under `~/Projects` / `~/projects`.
2. GitHub via authenticated `gh` CLI.
3. Local Markdown / LaTeX / text files.
4. Local Hermes sessions.
5. Basic macOS activity: frontmost app and idle time.
6. Chrome history, technically readable, but should be treated as Rich Data Mode.

The main missing permission is Calendar:

- Feishu app credentials are present and `lark_oapi` is installed.
- Feishu Calendar API currently lacks required Calendar scopes.
- Apple Calendar local directory exists, but direct useful calendar DB access was not confirmed.

## Probe Results

### Local Git

Status: ready.

Findings:

- Git is installed.
- 14 top-level git repos were detected across `~/Projects` and `~/projects`.
- Active repos today included examples such as:
  - `baidu-signal-paper`
  - `daily-briefing`
  - `daytrace`
  - `LOFT-Sim`

Useful signals:

- commits today
- uncommitted changes
- active repo list
- changed files

Recommended connector priority: P0.

### GitHub

Status: ready.

Findings:

- `gh` CLI is installed.
- User is logged in as `xingminw`.
- GitHub API call works.

Useful signals:

- PRs
- issues
- reviews
- repo metadata
- Actions status

Recommended connector priority: P1 after local Git, because local Git already covers code activity.

### Overleaf / LaTeX

Status: partially ready.

Findings:

- No git remote containing `overleaf` was detected in current top-level repos.
- LaTeX files exist under:
  - `~/Projects/overleaf/multi-scale-sim/main.tex`
  - `~/Projects/overleaf/service-mode/main.tex`
  - `~/Projects/LOFT-Sim/...`
- No `.tex` file appeared modified today during the probe.

Useful signals:

- local `.tex` modification time
- project-level writing activity
- future Overleaf Git sync if enabled

Recommended connector priority: P0 for local `.tex`; P1 for Overleaf Git integration.

### Local Documents

Status: ready.

Findings:

- Many `.md` files under `~/Projects` were modified today.
- This includes DayTrace, Daily Briefing, and paper-related docs.

Useful signals:

- writing activity
- project docs
- article/draft progress
- generated reports

Recommended connector priority: P0.

### Hermes Sessions

Status: ready.

Findings:

- `~/.hermes/sessions` exists.
- 57 session files were modified today during the probe.

Useful signals:

- AI collaboration
- product decisions
- task progress
- conversation-derived work traces

Risk:

- Session files can be large and may contain sensitive context.
- Connector should extract summaries/previews, not dump full transcripts by default.

Recommended connector priority: P0.

### macOS Activity

Status: basic mode ready; rich mode needs permission.

Findings:

- Frontmost app probe works. It returned `Feishu` during the probe.
- Idle time probe via `ioreg` works.
- Window title probe failed because `osascript` is not allowed assistive access.

Implications:

- DayTrace can immediately record frontmost app + active/idle state.
- To record window titles, grant Accessibility permission to the process/app running the collector.

Recommended connector priority:

- P0 for frontmost app + idle time.
- P1 for window title after explicit permission.

### Browser History

Status: Chrome readable; Safari blocked by macOS privacy.

Findings:

- Chrome History DB exists and is readable by copying the SQLite DB.
- Safari History DB exists but read failed with `Operation not permitted`.

Implications:

- Chrome can be connected technically, but should be Rich Data Mode because browser data is high-noise and sensitive.
- Safari requires additional macOS privacy permission / Full Disk Access or another access path.

Recommended connector priority: P2 / Rich Data Mode, not first closed-loop dependency.

### Calendar

Status: not ready through Feishu yet; local Apple Calendar not confirmed.

Findings:

- Feishu credentials are present.
- `lark_oapi` is installed.
- Feishu Calendar API returned access denied because the app lacks required scopes:
  - `calendar:calendar:readonly`
  - `calendar:calendar`
  - `calendar:calendar.calendar:readonly`
  - `calendar:calendar:read`
- Local `~/Library/Calendars` exists, but no directly useful SQLite DB was confirmed in this probe.

Recommended action:

- Enable Feishu Calendar read scope in the Feishu app if Calendar is desired for v0.
- Alternatively use `.ics` feed/export if available.

Recommended connector priority: P1 once permission is granted.

### Wi-Fi / Location

Status: inconclusive.

Findings:

- Wi-Fi SSID probe returned empty output in this run.

Recommended priority: P2. Work location can be manual correction for v0.

### Mail / iCloud Documents

Status: local paths exist, but not probed deeply.

Findings:

- `~/Library/Mail` exists.
- `~/Library/Mobile Documents` exists.

Recommended priority: P2 / Rich Data Mode.

## Recommended Closed-Loop v0 Sources

Use these first because they are already available and enough to test the full DayTrace pipeline:

1. Local Git
2. Local docs / LaTeX
3. Hermes sessions
4. macOS basic activity
5. Optional GitHub API enrichment

This gives DayTrace enough to generate:

- active projects
- code commits and uncommitted changes
- written/modified documents
- AI collaboration context
- rough working time / app activity

Calendar and browser can be added after the first closed-loop report works.

## Next Implementation Step

Implement connector skeletons for:

```text
scripts/collect_git.py
scripts/collect_docs.py
scripts/collect_hermes_sessions.py
scripts/collect_macos_activity.py
```

All connectors should emit the same `TraceEvent` JSONL schema, then DayTrace can generate the first real daily report from current local data.
