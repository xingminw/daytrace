# DayTrace

A **local-first personal daily trace** — collects your day from your own
devices (git, IDE, AI chats, documents), aggregates it into a SQLite
event timeline, lets a language model write a short narrative + insights,
and delivers the result wherever you want to read it.

> Built for one person (the author) on macOS. Open-source so you can
> steal the parts you like.

```
collectors      →   SQLite events   →   AI overview   →   dashboard / docs / mail
─────────────       ──────────────       ───────────       ─────────────────────────
claude_code         day_report          DeepSeek          /today, /weekly
codex               day_channel         (narrative,       (live, via Tailscale)
git                 work_items           highlights,
docs                event_work_…         work_pattern,    每日 / 每周 Feishu Docs
hermes (Feishu)                          suggestions,
ssh from remotes                         trend)           Gmail (HTML body)
                                                          PNG charts inline
```

## What it actually does

- **Collects** activity from your machines:
  - local code (git commits, Claude Code / Codex sessions, file edits)
  - local & Overleaf documents
  - Feishu group chats relayed through Hermes
  - remote Linux/WSL boxes via SSH (one Mac is the hub; everything
    syncs back here)
- **Stores** every event as a row in `data/daytrace.sqlite` with a
  consistent schema (`source`, `start`, `project_guess`, `device_id`,
  `evidence`, …). One SQLite file is the entire system of record.
- **Links** events to *real Feishu tasks* (`work_items` table, synced
  via lark-cli) so the AI can talk in task names instead of generic
  project labels.
- **Summarizes** each day with a single DeepSeek call:
  - a 4-tile dashboard (events / active hours / longest focus / AI cost)
  - a short narrative ("早上一头扎进 X…")
  - three Insights columns: 🚀 关键任务进展 / ⏰ 时间安排回顾 / 🔔 任务跟进提醒
  - a trend chip ("rising / steady / blocked")
- **Renders** a live dashboard at `http://127.0.0.1:8765/today` and `…/weekly`
  with stacked-bar histograms, donut distributions, per-task swim lanes,
  and an audit panel for unmatched projects.
- **Exports** the weekly report to:
  - a Feishu **cloud document** (Markdown imported via `lark-cli`, with
    PNG charts embedded as persistent image blocks)
  - a **Gmail** message with HTML body + inline charts + a link back
    to the live dashboard (via Tailscale Serve)
- **Schedules** the whole thing with three macOS **launchd** jobs:
  04:30 daily catchup, Monday 06:00 weekly report, plus a 24/7 dashboard
  daemon.

## Quick start

```bash
git clone <this repo> daytrace
cd daytrace
python3 -m pip install -r requirements.txt

# 1. Configure data sources (devices, collectors, work-item tables)
$EDITOR config/sources.yaml
$EDITOR config/devices/mac.yaml
$EDITOR config/work_items.yaml

# 2. Optional secrets (DeepSeek + Gmail SMTP for delivery)
mkdir -p ~/.daytrace && chmod 700 ~/.daytrace
cat > ~/.daytrace/secrets.env <<'EOF'
DEEPSEEK_API_KEY=sk-...
DAYTRACE_GMAIL_USER=your-agent@gmail.com
DAYTRACE_GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx
DAYTRACE_EMAIL_TO=you@example.com
EOF
chmod 600 ~/.daytrace/secrets.env

# 3. First run — collect + regen
python3 scripts/run_daily.py catchup --config config/devices/mac.yaml

# 4. Start the dashboard
python3 dashboard/server.py --db data/daytrace.sqlite --host 127.0.0.1 --port 8765
open http://127.0.0.1:8765/today
```

For the full scheduled-task + Tailscale + Feishu + email setup, see
**[docs/delivery-setup.md](docs/delivery-setup.md)**.

## Layout

```
daytrace/          core library — schema, collectors, AI client,
                   report rendering (charts + markdown + email)
dashboard/         HTTP server + page renderers
scripts/           CLI entry points (collect_*, run_daily, export_report,
                   cleanup_feishu_reports, daytrace-{daily,weekly}.sh)
deploy/            launchd plists for macOS
config/            yaml — devices, sources, work_items, aliases
tests/             pytest suite (79 tests as of 2026-05-18)
docs/              design notes, delivery setup, architecture
data/              runtime (gitignored): sqlite, reports, logs
```

## Documentation

Architecture, vision, and one-off setup guides:

- **[Product Brief](docs/product-brief.md)** — what DayTrace is and isn't
- **[Information Design](docs/information-design.md)** — event schema + core objects
- **[Agent Architecture](docs/agent-architecture.md)** — agent / collector / AI separation
- **[Experience Design](docs/experience-design.md)** — UX principles
- **[Data Sources & Permissions](docs/data-sources-and-permissions.md)** — trust model
- **[Engineering Spec](docs/engineering-spec.md)** — v0 tech goals
- **[Multi-Device Sync](docs/multi-device-sync.md)** — SSH-direct hub model
- **[Feishu Machine Onboarding](docs/feishu-machine-onboarding.md)** — how to add a new machine
- **[Delivery Setup](docs/delivery-setup.md)** — cron, Tailscale Serve, Feishu Docs, Gmail (current)
- **[Output Examples](docs/output-examples.md)** — what reports look like

Earlier design drafts and point-in-time notes are kept under
[`docs/archive/`](docs/archive/) for context.

## License

[MIT](LICENSE) — do what you want; if it breaks, you keep both pieces.

DayTrace processes your personal data. By design, it never sends data
anywhere except the destinations you configure (DeepSeek for AI
summarization, Feishu Drive for the cloud doc, your own Gmail for
delivery). No DayTrace cloud, no telemetry.
