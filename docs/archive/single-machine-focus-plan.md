# DayTrace Single-Machine Focus Plan

## Decision

For the next implementation cycle, DayTrace should focus on the single-machine version.

Multi-device sync is an important direction, and the technical route is considered clear enough for now:

```text
branch devices / collectors
  → Feishu Drive inbox
  → Hermes Mac Hub
  → SQLite
  → Portal / reports
```

But multi-device end-to-end testing should wait until there are real branch devices/collectors to test with.

## Deferred TODO: multi-device sync

Keep the multi-device work as an explicit TODO, not the immediate focus.

TODO later:

1. Implement Feishu Drive inbox adapter.
2. Add branch collector upload protocol.
3. Add imported file ledger.
4. Add device-specific inbox folders.
5. Run true multi-device E2E test when iPhone/iPad/another Mac collector is available.

Reference doc:

```text
docs/multi-device-sync.md
```

## Current focus: single-machine DayTrace

The single-machine product should become useful before adding more devices.

Current primary machine:

```text
mac-hermes
```

Current local sources:

```text
git
docs / markdown / latex
hermes_sessions
macos_activity
```

## Portal priorities

### 1. Today page: spatial-temporal organization

The homepage / Today page should not just show raw counts.

It should sort and summarize the day by time and context:

```text
什么时候
在哪个上下文 / 项目 / source
做了什么
证据是什么
置信度如何
```

Initial blocks:

1. Day overview stats
   - total events;
   - active sources;
   - projects touched;
   - low-confidence events.
2. Time-based timeline
   - events sorted by time;
   - grouped into rough sessions or time buckets.
3. Project / source composition
   - which project consumed attention;
   - which source contributed evidence.
4. Space/context stats
   - location if known;
   - otherwise `unknown` with room for manual annotation.

### 2. Timeline visualization

Add a visual timeline to make DayTrace feel like an actual daily trace product.

First version can be simple HTML/CSS:

```text
hour rail
  ├─ git event
  ├─ docs event
  ├─ hermes event
  └─ macos activity sample
```

Later versions can add:

- stacked bars by source;
- session blocks;
- project color coding;
- hover details;
- confidence markers.

### 3. Fancy but useful visualizations

Add visual summaries after data is organized:

- pie chart / donut chart by source;
- pie chart / donut chart by project;
- bar chart by event kind;
- timeline by hour;
- low-confidence review list.

Keep visualizations useful, not decorative.

### 4. Manual annotation as bonus

If the database contains uncertain items, the portal should eventually allow manual labels:

- assign project;
- assign location;
- mark event as noise;
- edit title/summary;
- add note.

This is a bonus after the single-machine dashboard is organized.

## Recommended implementation order

### Phase A — stabilize single-machine schema

1. Add device/location defaults to event/database model.
2. Keep device as `mac-hermes`.
3. Keep location as `unknown` unless manually annotated.
4. Keep multi-device tables minimal or deferred.

### Phase B — Today page timeline

1. Create `/today` route.
2. Query events for selected date.
3. Sort by timestamp.
4. Group events by hour or session.
5. Render timeline blocks with source/project colors.
6. Add counts by source/project.

### Phase C — Visual summaries

1. Add simple CSS/SVG donut or bar charts.
2. Show source composition.
3. Show project composition.
4. Show event kind composition.
5. Keep raw table in `/events`.

### Phase D — Manual annotation bonus

1. Add `event_corrections` table.
2. Add simple form/button in event detail.
3. Store corrections locally.
4. Apply corrections in Today summary.

## Not now

Do not prioritize these until the single-machine portal is useful:

- Feishu Drive E2E sync;
- iPhone collector;
- iPad collector;
- multi-device conflict resolution;
- full cloud upload protocol;
- production deployment.

## Success criteria for the next iteration

The next useful DayTrace prototype should let the user open the portal and answer:

1. 今天大概什么时候在做什么？
2. 哪些项目占了主要注意力？
3. 数据主要来自哪些 source？
4. 哪些事件不确定，需要人工标注？
5. 原始数据库能否继续追溯和 debug？
