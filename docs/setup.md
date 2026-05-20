# DayTrace Setup

> 🌐 [中文版](setup.zh.md)

Installation, configuration, and scheduling — top to bottom.

## 1. Install

```bash
git clone https://github.com/xingminw/daytrace
cd daytrace
python3 -m pip install -r requirements.txt
```

Python ≥ 3.10. Tested on macOS Sonoma + Sequoia.

`lark-cli` is required for the Feishu sync features (work-items pull,
Feishu Docs delivery). Install per the
[`lark-cli` docs](https://github.com/larksuite/lark-cli); your first run
will be a one-time browser auth.

## 2. Configuration files

All config lives in `config/`. The repo ships `*.example.yaml` templates;
copy each to its real filename (gitignored) and fill in your own values:

```bash
make install-config
# or manually:
#   cp config/work_items.example.yaml        config/work_items.yaml
#   cp config/work_item_aliases.example.yaml config/work_item_aliases.yaml
#   cp config/remotes.example.yaml           config/remotes.yaml
```

Each file is described below.

### `config/devices/<device>.yaml` — what each machine collects

One file per machine. The hub Mac ships in `config/devices/mac.yaml`;
add a sibling file per remote.

```yaml
device:
  id: Mac                    # must match this machine's identity; events get this stamped on
  name: Mac Hub
  location_id: unknown
  collector_id: hub-local

sources:
  codex:        { enabled: true, home: ~/.codex,            limit: 600 }
  claude_code:  { enabled: true, home: ~/.claude/projects,  limit: 800 }
  hermes:       { enabled: true, sessions_dir: ~/.hermes/sessions, limit: 700 }
  git:          { enabled: true, roots: [~/Projects],       limit: 300 }
  # macos_activity, docs, etc. — toggle as relevant
```

### `config/remotes.yaml` — which other machines feed this hub

Copy `config/remotes.example.yaml` → `config/remotes.yaml` first.

```yaml
remotes:
  - device_id: omen-wsl              # must match the remote's device.id
    ssh: mtl                          # alias in ~/.ssh/config
    repo_path: /mnt/d/research-programs/daytrace
    config: config/devices/omen-wsl.yaml
```

The hub uses this for `run_daily.py deploy` (push code to each remote)
and `run_daily.py catchup` (SSH in, run the remote's collectors, rsync
events back).

### `config/work_items.yaml` — Feishu Bitables to sync

Copy `config/work_items.example.yaml` → `config/work_items.yaml` and fill
in your Feishu Bitable IDs. Each table tier has its own field-name →
DayTrace-column map. See the
heavily commented header of the file itself; the short version:

```yaml
tables:
  - key: tasks            # primary task table, drives the 任务 dim
    app_token: bsccTXXXX
    table_id: tblXXXX
    field_map:
      title: 任务
      status: 状态
      priority: 优先级
      # …
  - key: reviews
    collapse_in_dim: true
    collapsed_label: 审稿  # all rows fold to one bucket in the 任务 dim
```

`run_daily.py work-items-sync` pulls these into the `work_items` SQL
table and rebuilds `event_work_item_links` against existing events.

### `config/work_item_aliases.yaml` — manual project → task overrides

Copy `config/work_item_aliases.example.yaml` → `config/work_item_aliases.yaml`.

```yaml
aliases:
  "My project alias": rec_REPLACE_ME_1
  "Another alias":    rec_REPLACE_ME_2
```

Used by `work_items.rebuild_links()` when URL/path matching fails. The
dashboard's audit panel writes here when you map an unmatched project.

### `config/sources.yaml`, `config/rules.yaml`

Lightweight defaults from the prototype era — project aliases, privacy
toggles, the source-enable list. Most behavior now lives in the per-
device yaml; these stay for backward compat with `import_inbox.py`.

### `config/feishu_drive.yaml` (gitignored)

Auto-generated on the first `export_report.py --upload-feishu` run.
Holds the folder tokens for the auto-created `DayTrace 报告 /
{daily,weekly}/` structure in your Feishu drive.

## 3. Secrets

DeepSeek + Gmail credentials live in `~/.daytrace/secrets.env`,
chmod 600. **Never** committed.

```env
# DeepSeek (required for AI overviews)
DEEPSEEK_API_KEY=sk-...

# Optional: Gmail SMTP for weekly report delivery
DAYTRACE_GMAIL_USER=your-agent@gmail.com
DAYTRACE_GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx   # 16-char app password, NOT login password
DAYTRACE_EMAIL_TO=you@example.com

# Optional: language for Markdown / email / Feishu Docs exports.
# Independent of the dashboard's UI language (which is cookie-based).
# Default 'en'; set to 'zh' for Chinese reports.
DAYTRACE_REPORT_LANG=en
```

The 16-char app password comes from
<https://myaccount.google.com/apppasswords> (Google requires 2FA on the
account first). Use a dedicated agent Gmail account — that way the
password being on disk has minimal blast radius.

`daytrace/ai_client.py` lazily merges this file into `os.environ` at
runtime, so launchd-spawned processes (which don't inherit your shell
profile) get `DEEPSEEK_API_KEY` automatically.

## 4. First run — catchup + dashboard

```bash
# Pull data + regen yesterday's report (also good for ad-hoc backfill).
python3 scripts/run_daily.py catchup --config config/devices/mac.yaml

# Start the local dashboard.
python3 dashboard/server.py --db data/daytrace.sqlite --port 8765
open http://127.0.0.1:8765/today
```

## 5. Scheduled tasks (macOS launchd)

Three jobs ship as templates under `deploy/`:

| Job | Cadence | Script |
|---|---|---|
| `com.daytrace.dashboard` | always-on (`KeepAlive=true`) | dashboard server on `127.0.0.1:8765` |
| `com.daytrace.daily`     | every day, 04:30 | `scripts/daytrace-daily.sh` → catchup + Feishu Docs export |
| `com.daytrace.weekly`    | Monday, 06:00    | `scripts/daytrace-weekly.sh` → weekly render + Feishu + Gmail |

Install — `scripts/install_launchd.sh` substitutes `__REPO__` and
`__PYTHON__` in each `deploy/*.plist.template`, writes the rendered
plists to `~/Library/LaunchAgents/`, and bootstraps them. It's
idempotent (unloads any existing copies first).

```bash
bash scripts/install_launchd.sh
launchctl list | grep daytrace   # confirm
```

Manual run without waiting for the cadence:

```bash
launchctl kickstart gui/$(id -u)/com.daytrace.daily
```

Logs: `data/logs/{daily,weekly,dashboard}.log`. If a Mac is asleep at
fire time, launchd runs the job on next wake.

Uninstall:

```bash
for label in dashboard daily weekly; do
  launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.daytrace.${label}.plist
  rm ~/Library/LaunchAgents/com.daytrace.${label}.plist
done
```

## 6. Remote-access the live dashboard (Tailscale Serve)

To open `/today` or `/weekly` from your phone or another laptop, expose
the local dashboard over Tailscale (private to your tailnet — not the
public internet):

```bash
# One-time: enable Serve in the Tailscale admin panel.
#   Visit https://login.tailscale.com/admin/settings/features → toggle HTTPS.

tailscale serve --bg --https=443 http://127.0.0.1:8765
tailscale serve status   # confirm
```

You now get `https://<your-mac>.<tailnet>.ts.net/` accessible to any
device logged into your tailnet. Tailscale Serve config persists across
reboots.

`report_delivery.dashboard_url()` auto-detects this and includes the
URL in weekly emails as `🖥 完整 Dashboard`.

## 7. Delivery — Feishu Docs + Gmail

Once the secrets file + `lark-cli` auth are in place, the daily and
weekly jobs deliver automatically. To trigger an ad-hoc export:

```bash
# Markdown only (local file), no upload, no email
python3 scripts/export_report.py --week 2026-W20

# Full pipeline: render → Feishu Docs → email
python3 scripts/export_report.py --week 2026-W20 --upload-feishu --email

# Daily variant (no email by default; weekly handles delivery)
python3 scripts/export_report.py --date 2026-05-17 --upload-feishu
```

What each channel produces:

- **Feishu Docs**: Markdown imported via `lark-cli drive +import` →
  native cloud docx. Charts are then inserted via `lark-cli docs
  +media-insert` with `--selection-with-ellipsis=<chart heading>` so the
  underlying image_tokens are persistent (the `+import` path embeds
  temporary stream URLs that expire after ~1 hour — verified the hard
  way). Lives under `DayTrace 报告 / { daily, weekly }/`.
- **Gmail**: multipart/alternative — text/plain fallback + styled HTML
  body + PNG charts attached as `Content-ID` inline images. Subject
  auto-suffixes the AI headline. Links box at top points to:
  - `🖥 完整 Dashboard` — the Tailscale URL (if Serve is up)
  - `📄 飞书文档` — the docx URL from the same run

## 8. Cleanup helpers

```bash
# Inspect (dry-run): which Feishu drive files would be deleted?
python3 scripts/cleanup_feishu_reports.py

# Actually delete stale revisions (keeps the newest docx per name;
# drops obsolete .html / .md raw uploads from the pre-v8 era)
python3 scripts/cleanup_feishu_reports.py --apply
```
