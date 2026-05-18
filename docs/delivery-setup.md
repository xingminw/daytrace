# DayTrace Delivery Setup

How DayTrace reports leave the local machine and reach you: cron, Feishu
Docs, email, and the live dashboard.

## At a glance

```
                ┌─ Mac (always-on dashboard daemon) ─────────┐
   collectors → │ daytrace.sqlite                            │
   ssh catchup  │  → run_daily.py catchup       (04:30 daily)│
                │     └ regen day_report (AI overview)       │
                │  → export_report.py           (daily/weekly)│
                │     ├ daytrace.report_charts  (PNG charts)  │
                │     ├ daytrace.report_export  (Markdown body)│
                │     └ daytrace.report_delivery              │
                │         ├ Feishu Docs (MD → docx + images) │
                │         ├ Gmail SMTP  (HTML body, inline)  │
                │         └ Tailscale Serve URL              │
                └────────────────────────────────────────────┘
                                       │
       ┌───────────────────────────────┼───────────────────────────────┐
       ▼                               ▼                               ▼
  Feishu app                     Gmail inbox                  Tailscale device
   (cloud doc)                   (rich HTML body)              (live dashboard)
```

Three delivery channels, all from the same MD source:

| Channel | What you get | Where |
|---|---|---|
| **Feishu Docs** | Native cloud document with tables, blockquote, embedded charts. Renders in-app and in browser. | `DayTrace 报告/{daily,weekly}/` |
| **Gmail** | Rich HTML body with magazine styling — table dashboard, blockquote narrative, two inline charts. No attachments. | `DAYTRACE_EMAIL_TO` recipient |
| **Tailscale Serve** | Live dashboard URL (`https://...ts.net/today?date=…`). Full interactivity. | Any tailnet device |

## Pieces

### 1. launchd jobs (`deploy/com.daytrace.*.plist`)

| Job | Cadence | What it does |
|---|---|---|
| `com.daytrace.dashboard` | always-on (`KeepAlive=true`) | Local HTTP server on `127.0.0.1:8765` so Tailscale Serve has something to proxy 24/7. |
| `com.daytrace.daily` | every day, 04:30 | catchup → import → regen → render MD + 2 charts → import to Feishu Docs `daily/`. No email. |
| `com.daytrace.weekly` | Monday, 06:00 | render last completed ISO week → MD + 2 charts → Feishu Docs `weekly/` → Gmail to recipient. |

Install:

```bash
for plist in deploy/com.daytrace.{dashboard,daily,weekly}.plist; do
  cp "$plist" ~/Library/LaunchAgents/
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/$(basename "$plist")
done
```

Verify:

```bash
launchctl list | grep daytrace
curl http://127.0.0.1:8765/today   # 200
```

### 2. Tailscale Serve (live dashboard link)

One-time setup in the Tailscale admin panel: enable Serve under
`Settings → Features` (or visit
`https://login.tailscale.com/f/serve` from the device).

Then once on the Mac:

```bash
tailscale serve --bg --https=443 http://127.0.0.1:8765
```

The URL `https://<your-mac>.<tailnet>.ts.net/` is now reachable from any
device logged into your tailnet. `dashboard_url()` in
`daytrace/report_delivery.py` auto-detects it via `tailscale serve
status` and includes it in the weekly email's link box.

### 3. Feishu Docs

Folder tokens live in `config/feishu_drive.yaml` (gitignored). First
`--upload-feishu` run auto-creates `DayTrace 报告 / { daily, weekly }/`
in your Feishu drive root.

We use `lark-cli drive +import --type docx`, which converts Markdown to
a real Feishu cloud document **and inlines any `![](./local.png)`
references** from the same directory. So charts live in the same dir as
the MD and get bundled into the docx automatically.

Want to target an existing folder? Populate `config/feishu_drive.yaml`
manually with the tokens from the folder URLs.

### 4. Gmail SMTP

Credentials live in `~/.daytrace/secrets.env` (chmod 600):

```env
DAYTRACE_GMAIL_USER=your-agent@gmail.com
DAYTRACE_GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx
DAYTRACE_EMAIL_TO=you@example.com
```

The app password comes from https://myaccount.google.com/apppasswords
(2FA on the Gmail account is required). Use a dedicated agent Gmail so
the password being on disk has tiny blast radius.

We send `multipart/alternative` with a plain-text fallback + HTML body.
The HTML has a `_BRAND_STRIP` header, a styled links box, table-rendered
dashboard, blockquote narrative, and inline chart PNGs (via `cid:`
references on `EmailMessage.add_related()`). No file attachments.

## Manual operation

```bash
# One-off: weekly with all channels (Feishu Docs + email)
python scripts/export_report.py --week 2026-W20 --upload-feishu --email

# One-off: daily, MD + charts only (default daily cron behavior)
python scripts/export_report.py --date 2026-05-17 --upload-feishu

# Kickstart a launchd job without waiting for its scheduled time
launchctl kickstart gui/$(id -u)/com.daytrace.weekly
```

Logs land in `data/logs/{daily,weekly,dashboard}.log`.

## Uninstall

```bash
for label in dashboard daily weekly; do
  launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.daytrace.${label}.plist
  rm ~/Library/LaunchAgents/com.daytrace.${label}.plist
done
```

## Files

```
daytrace/
  report_export.py       # MD body composition (table, blockquote, insights)
  report_charts.py       # matplotlib PNGs (stacked histogram + donut)
  report_delivery.py     # Feishu Docs import + Gmail SMTP + Tailscale URL detect
scripts/
  export_report.py       # CLI entry called by cron
  daytrace-daily.sh      # launchd wrapper: catchup + export
  daytrace-weekly.sh     # launchd wrapper: weekly render + email
deploy/
  com.daytrace.dashboard.plist
  com.daytrace.daily.plist
  com.daytrace.weekly.plist
config/
  feishu_drive.yaml      # folder tokens (gitignored)
~/.daytrace/
  secrets.env            # Gmail credentials (chmod 600, not in repo)
```

## History

The first prototype delivered reports via a Feishu-push cron (Hermes
agent script). That was replaced by the current direct DayTrace pipeline
(Tailscale + Feishu Docs + Gmail) — no agent in the loop, all secrets
local, all logs under `data/logs/`. See `docs/archive/` for the
prior design notes.
