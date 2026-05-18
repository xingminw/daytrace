# DayTrace scheduled tasks (macOS launchd)

Three launchd jobs ship with this repo:

| Job | When | What |
|---|---|---|
| `com.daytrace.dashboard` | always-on (KeepAlive) | Local HTTP server on :8765. Tailscale Serve proxies to this for the live-dashboard email link. |
| `com.daytrace.daily`  | 04:30 every day | catchup → import → regen day_report → export yesterday's Markdown summary → import to Feishu Docs |
| `com.daytrace.weekly` | Monday 06:00    | render last completed ISO week → export Markdown → import to Feishu Docs → email rendered HTML body with links to Feishu Docs + Tailscale dashboard |

If the Mac is asleep at the scheduled time, launchd runs the job on next wake.

## Install

```bash
cp deploy/com.daytrace.dashboard.plist ~/Library/LaunchAgents/
cp deploy/com.daytrace.daily.plist     ~/Library/LaunchAgents/
cp deploy/com.daytrace.weekly.plist    ~/Library/LaunchAgents/

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.daytrace.dashboard.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.daytrace.daily.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.daytrace.weekly.plist
```

Verify:

```bash
launchctl list | grep daytrace
curl http://127.0.0.1:8765/today  # dashboard daemon is alive
```

## Tailscale Serve — live dashboard link

To get a clickable `https://...ts.net` URL that opens the live dashboard
from any device on your tailnet (phone, other Mac, work laptop):

```bash
# One-time: enable Serve in the Tailscale admin panel
# (visit https://login.tailscale.com/admin/settings/features and toggle 'HTTPS')

tailscale serve --bg --https=443 http://127.0.0.1:8765
tailscale serve status   # confirm
```

The email job auto-detects this and adds a "🖥 完整 Dashboard" link to
the message body. `tailscale serve` config is persistent across reboots.

## Inspect / run manually

```bash
# Tail logs
tail -f data/logs/daily.log
tail -f data/logs/weekly.log

# Trigger a job ad-hoc (does not wait for the calendar interval)
launchctl kickstart gui/$(id -u)/com.daytrace.daily
launchctl kickstart gui/$(id -u)/com.daytrace.weekly
```

## Uninstall

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.daytrace.daily.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.daytrace.weekly.plist

rm ~/Library/LaunchAgents/com.daytrace.daily.plist
rm ~/Library/LaunchAgents/com.daytrace.weekly.plist
```

## Secrets

Email delivery reads credentials from `~/.daytrace/secrets.env` (chmod 600):

```env
DAYTRACE_GMAIL_USER=your-agent@gmail.com
DAYTRACE_GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx
DAYTRACE_EMAIL_TO=you@example.com
```

The app password comes from https://myaccount.google.com/apppasswords (2FA must be enabled). Use a dedicated agent Gmail — never your primary account password.

## Feishu Docs

`config/feishu_drive.yaml` (gitignored) stores two folder tokens. First
`--upload-feishu` run auto-creates these in your Feishu drive root:

```
DayTrace 报告/
├── daily/       ← daily Markdown → native Feishu Docs (cloud docx)
└── weekly/      ← weekly Markdown → native Feishu Docs
```

We **import** the Markdown via `lark-cli drive +import --type docx`,
which produces a real Feishu cloud document — clickable, rendered
natively in the Feishu app and the web client. We don't upload raw HTML
files anymore; the live-dashboard URL (Tailscale Serve) covers the rich
view case.

To target an existing folder instead, populate `config/feishu_drive.yaml`
manually with the folder tokens from the Feishu URL (`https://.../folder/<token>`).
