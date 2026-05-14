# DayTrace Delivery Setup

## Current decision

For the first usable prototype, DayTrace will be delivered by Feishu push instead of a hosted dashboard.

Reason:

- The user already works with Hermes in Feishu.
- Feishu push makes the loop immediately useful.
- Dashboard design is still unclear and should be informed by real daily reports.
- Local Markdown output remains the archive of record.

## Active delivery job

Cron job:

```text
Name: DayTrace daily Feishu push
Job ID: 865f0e80eb15
Schedule: 30 23 * * *
Delivery: origin Feishu group
Mode: script-only / no_agent
Script: ~/.hermes/scripts/daytrace_daily_push.sh
```

Next scheduled run at creation time:

```text
2026-05-13T23:30:00-04:00
```

## Script behavior

The script runs:

```text
scripts/collect_git.py
scripts/collect_docs.py
scripts/collect_hermes_sessions.py
scripts/collect_macos_activity.py
scripts/generate_daily_report.py
```

Then it prints the Feishu summary from:

```text
outputs/YYYY-MM-DD.feishu.md
```

Hermes cron delivers that stdout directly to the Feishu group.

## Local outputs

Each run writes:

```text
events/git-YYYY-MM-DD.jsonl
events/docs-YYYY-MM-DD.jsonl
events/hermes-YYYY-MM-DD.jsonl
events/macos-YYYY-MM-DD.jsonl
outputs/YYYY-MM-DD.md
outputs/YYYY-MM-DD.feishu.md
logs/daytrace-YYYY-MM-DD.log
```

## Dashboard direction

Do not overbuild the dashboard yet. Use Feishu push + Markdown reports first.

After several daily reports, design a local dashboard around the actual needs:

- timeline
- projects
- artifacts
- evidence
- low-confidence corrections
- source health
- permission/source controls
