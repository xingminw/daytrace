# Frontend QA Pass · 2026-05-13

## Scope

Visual inspection and browser QA of the localhost DayTrace dashboard at:

```text
http://127.0.0.1:8765/?date=2026-05-13
```

## Issues found from screenshot / browser inspection

1. Sticky table header was misplaced / overlapping:
   - first data row appeared above the header row;
   - caused by page-level sticky `th { top: 88px }` inside a normal table.
2. Evidence column clipped at the right edge:
   - long JSON preview made the last column unreadable;
   - table width exceeded comfortable viewport width.
3. Evidence JSON was too noisy for the main row:
   - raw JSON preview made the table dense and horizontally unstable.
4. Timestamps / project pills wrapped awkwardly when columns squeezed.

## Fixes applied

1. Wrapped the event table in `.table-wrap`:
   - internal scroll container;
   - max height;
   - rounded border belongs to wrapper.
2. Changed sticky header to container-local sticky:
   - `th { position: sticky; top: 0; z-index: 3 }`.
3. Added explicit column widths via `colgroup`:
   - Time, Source, Project, Title, Conf, Evidence.
4. Replaced raw Evidence preview with compact disclosure label:
   - main table now shows `查看` instead of long JSON.
5. Improved wrapping:
   - title/summary use `overflow-wrap:anywhere`;
   - timestamps use tabular nums and nowrap;
   - pills truncate safely.

## Verification

Commands:

```bash
python -m compileall dashboard daytrace scripts
python -m pytest tests -q
```

Result:

```text
9 passed
```

Browser checks:

- dashboard loads;
- no JavaScript console errors;
- all six table columns visible;
- header no longer overlaps;
- Evidence column no longer clipped;
- rows remain readable.

## Remaining product/UI work

The current dashboard is acceptable as an original database viewer, but not yet a polished product dashboard.

Next improvements:

1. Add row detail drawer/modal for full evidence JSON.
2. Add filters:
   - source;
   - project;
   - low-confidence only;
   - kind.
3. Add pagination or virtual scrolling.
4. Add correction UI.
5. Add source health/status panel.
