# Local repository workflow

DayTrace now uses local Git repositories as the project source of truth. Feishu task sync, report upload, task-title translation and remote cleanup are disconnected. Nothing is deleted from Feishu, and account/OAuth permissions are unchanged.

## Identity and attribution

- `repo_projects` holds stable IDs derived from sanitized remote identities. SSH/HTTPS forms of the same remote merge, including multiple clones/checkouts. Remote credentials and query parameters are not stored.
- For Git repositories without a usable remote, identity uses the Git common directory. Worktrees therefore share one project. Plain directories without Git are not silently turned into projects.
- `repo_checkouts` retains known local paths. Existing historical checkout paths can be supplied in `config/projects.yaml` under `paths`.
- Attribution uses explicit aliases, longest matching checkout path, then an unambiguous repository name. Repositories with identical names but different remotes stay separate. Uncertain events stay unlinked.
- Events keep their original `project_guess` in `original_project_guess`; `repo_project_id` is additive. The current `project_guess` is the indexed display name for compatibility with existing chart/stat code. Raw event IDs, timestamps, text and evidence are retained.
- Legacy `work_items` and `event_work_item_links` are retained as read-only historical database views; active charts and AI context do not use them.

Local config (gitignored):

```yaml
roots:
  - ~/Projects
aliases:
  # Old project label: repo:<ID shown in the repository index>
paths:
  # /old/path/to/checkout: repo:<ID>
```

Refresh with `make sync-projects`. The command creates a SQLite backup, indexes local Git metadata without network access, remaps existing events and rebuilds deterministic reports. The dashboard audit form writes local repository aliases. The retired `/api/work-items/alias` form returns HTTP 410; current forms use `/api/projects/alias` and reject old Feishu record IDs.

## Collection, reports and deployment

`make daily` is a manual local run. It backs up the database, collects Codex/Git, indexes repositories, imports idempotently and rebuilds local statistics. No SSH, Feishu, email or AI calls occur. `DAYTRACE_DISABLE_AI=1` is enforced in the runner. Optional original collector sources are not enabled on this installation.

`bash scripts/install_launchd.sh` installs only the loopback dashboard using a launcher under `~/.local/bin`, a user-home working directory and logs under `~/Library/Logs/daytrace/`. This avoids the observed launchd failure when its working directory/log initialization touched the external SSD. It does not alter macOS privacy permissions. The SSD must remain mounted.

Optional scheduled collection uses `scripts/install_daily_collection.py`, which
requires a matching host timezone and an explicit installation decision. Its
host runner performs local collection first, then best-effort read-only SSH
collection from the locally configured registry. Unconfigured devices are not
contacted. AI remains disabled. The weekly wrapper exports saved reports only.
Feishu flags and cleanup commands fail before external operations. SMTP remains
an explicit manual export option; scheduled delivery is not installed.

For current AI planning, budgets, source limitations and publication, use the
[evidence-brief workflow](evidence-briefs.md). Retired historical AI entry points
cannot bypass it.
