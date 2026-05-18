# DayTrace scheduled tasks (macOS launchd)

Two recurring jobs ship with this repo:

| Job | When | What |
|---|---|---|
| `com.daytrace.daily`  | 04:30 every day | catchup → import → regen day_report → export yesterday's HTML+MD → upload to Feishu drive |
| `com.daytrace.weekly` | Monday 06:00    | render last completed ISO week → export HTML+MD → upload to Feishu drive → email Markdown body to recipient |

If the Mac is asleep at the scheduled time, launchd runs the job on next wake.

## Install

```bash
cp deploy/com.daytrace.daily.plist  ~/Library/LaunchAgents/
cp deploy/com.daytrace.weekly.plist ~/Library/LaunchAgents/

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.daytrace.daily.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.daytrace.weekly.plist
```

Verify:

```bash
launchctl list | grep daytrace
```

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

## Feishu drive folder layout

`config/feishu_drive.yaml` (gitignored) stores three folder tokens. First `--upload-feishu` run auto-creates these in your Feishu drive root:

```
DayTrace 报告/
├── daily/       ← daily HTML + MD archives (every day)
└── weekly/      ← weekly HTML + MD archives (every Monday)
```

To target an existing folder instead, populate `config/feishu_drive.yaml` manually with the folder tokens from the Feishu URL (`https://.../folder/<token>`).
