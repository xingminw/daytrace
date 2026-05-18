# Prototype Run · 2026-05-13

## Goal

Create the first closed-loop DayTrace prototype using data sources that are already accessible locally.

## Implemented

Prototype code paths:

```text
daytrace/schema.py
daytrace/io.py
daytrace/summarize.py
scripts/collect_git.py
scripts/collect_docs.py
scripts/collect_hermes_sessions.py
scripts/collect_macos_activity.py
scripts/generate_daily_report.py
config/sources.yaml
config/rules.yaml
```

Tests:

```text
tests/test_schema_io.py
tests/test_summarize_report.py
tests/test_collectors.py
```

## Sources Connected

- Local Git repositories under `~/Projects`
- Local Markdown / LaTeX / text files
- Hermes session files under `~/.hermes/sessions`
- Basic macOS activity: frontmost app + idle seconds

## Verification

Command:

```bash
python -m compileall daytrace scripts
python -m pytest tests -q
```

Result:

```text
8 passed
```

## Prototype Run

Commands:

```bash
DAY=$(date +%F)
python scripts/collect_git.py --date "$DAY" --root ~/Projects --out "events/git-$DAY.jsonl" --limit 120
python scripts/collect_docs.py --date "$DAY" --root ~/Projects --out "events/docs-$DAY.jsonl" --limit 120
python scripts/collect_hermes_sessions.py --date "$DAY" --out "events/hermes-$DAY.jsonl" --limit 40
python scripts/collect_macos_activity.py --duration-seconds 5 --interval-seconds 5 --out "events/macos-$DAY.jsonl"
python scripts/generate_daily_report.py --date "$DAY" --events "events/git-$DAY.jsonl" "events/docs-$DAY.jsonl" "events/hermes-$DAY.jsonl" "events/macos-$DAY.jsonl" --out "outputs/$DAY.md" --feishu-out "outputs/$DAY.feishu.md"
```

Outputs:

```text
events/git-2026-05-13.jsonl       7 events
events/docs-2026-05-13.jsonl      57 events
events/hermes-2026-05-13.jsonl    40 events
events/macos-2026-05-13.jsonl     1 event
outputs/2026-05-13.md             full report
outputs/2026-05-13.feishu.md      short Feishu summary
```

Total events in the first prototype report: 105.

## Current Prototype Summary

```text
DayTrace · 2026-05-13
已生成第一版 Prototype 日报：105 条事件，来源：docs:57、hermes:40、git:7、macos_activity:1。
主要项目/归因：daily-briefing、daytrace、baidu-signal-paper。
低置信度/待修正：2 条。
完整报告：outputs/2026-05-13.md
```

## Known Issues

- The Hermes session connector still uses simple previews and can include noisy context-compaction/system fragments from JSONL sessions.
- macOS activity currently samples only one point in the prototype run; for real usage it should run in the background over the day.
- Work location remains unknown/manual for now.
- Calendar is not connected yet because Feishu Calendar scope is missing.
- Window title capture requires macOS Accessibility permission.

## Next Steps

1. Improve Hermes session summarization to extract user/assistant work decisions instead of raw previews.
2. Run macOS activity collector as a longer background sampler.
3. Add a lightweight local dashboard or HTML report view.
4. Add Feishu delivery once the report shape is approved.
5. Add Calendar after Feishu app Calendar read permission is enabled.
