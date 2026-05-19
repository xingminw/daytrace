# DayTrace Multi-Device Sync

## Decision

Use Feishu Drive as the shared cloud inbox for DayTrace multi-device data sync.

DayTrace should use a hub-and-spoke model:

```text
Branch collectors / devices
  → Feishu Drive DayTrace Inbox
  → DayTrace Hub
  → SQLite event database
  → Portal / reports / Feishu push
```

## Roles

### Hub

The Hub is the only process allowed to write the canonical DayTrace SQLite database.

For now, the Hub is:

```text
Hermes Mac
```

Hub responsibilities:

- list Feishu Drive inbox folders;
- download new immutable event batch files;
- validate TraceEvent schema;
- compute file checksum;
- compute / verify event dedupe keys;
- import new events into SQLite;
- record ingest runs and imported files;
- archive or clean old received batches;
- generate portal data and reports.

### Branch collectors

Branch collectors only produce event batches. They do not write SQLite directly.

Examples:

- Hermes Mac local collectors;
- iPhone shortcut/location collector;
- iPad collector;
- GitHub cloud collector;
- Feishu Calendar collector;
- future browser extension collector.

Branch responsibilities:

- generate immutable JSONL or JSONL.GZ batches;
- include device/source/location metadata;
- upload to the correct Feishu Drive inbox folder;
- never append to an existing uploaded batch;
- never modify canonical SQLite.

## Feishu Drive folder layout

Proposed layout:

```text
DayTrace/
  inbox/
    mac-hermes/
      2026-05-14/
        101500-macos_activity-a7f3.jsonl
        102000-docs-b91c.jsonl
    iphone/
      2026-05-14/
        101530-location-c28d.jsonl
    ipad/
      2026-05-14/
        110000-reading-d31e.jsonl
    cloud/
      2026-05-14/
        102000-github-e92a.jsonl

  archive/
    2026-05-14/
      ...

  errors/
    iphone/
      bad-batch-001.jsonl
```

## Batch file rule

Use immutable batch files.

Do not do this:

```text
inbox/iphone/events.jsonl   # repeatedly appended by device
```

Do this instead:

```text
inbox/iphone/2026-05-14/101530-location-c28d.jsonl
inbox/iphone/2026-05-14/103000-location-f45a.jsonl
```

Each uploaded file is final and never modified after upload.

If using a local temporary write path before upload, write as `.tmp` first and only upload the final `.jsonl` / `.jsonl.gz`.

## Event metadata requirements

Each event should include or allow Hub to infer:

```text
event_id
dedupe_key
source_id
device_id
collector_id
location_id / location_guess
occurred_at
collected_at
ingested_at
schema_version
```

Minimum v1 defaults:

```text
device_id: mac-hermes
location_id: unknown
collector_id: hub-local
```

## Imported file tracking

SQLite should include an imported file ledger:

```text
imported_files
  id
  remote_path
  sha256
  size_bytes
  modified_at
  imported_at
  status
  events_count
  error_message
```

Hub import logic:

```text
for each Feishu Drive inbox object:
  compute sha256
  if sha256 already imported:
    skip
  validate batch
  insert non-duplicate events
  record imported_files
```

## Cleanup policy

Daily cleanup should happen after successful import.

Recommended policy:

1. Import inbox batches.
2. Verify imported file ledger and event counts.
3. Move successfully imported batches to `archive/YYYY-MM-DD/`, or mark as imported and leave in place initially.
4. Move invalid batches to `errors/<device>/`.
5. Delete archived raw batches after a retention window.

Initial retention suggestion:

```text
archive raw batches: 7 days
errors: keep until manually reviewed
SQLite canonical data: keep indefinitely unless user deletes
```

Do not delete raw inbox files before Hub has recorded a successful import.

## Why Feishu Drive works here

Feishu Drive is acceptable because it is already part of the user's workflow and chat workspace.

The goal is not to make Feishu Drive the database. It is only the shared inbox / transport layer.

Canonical data remains:

```text
DayTrace SQLite on Hub
```

Feishu Drive stores:

```text
raw immutable event batches
```

## Portal implications

The portal should expose:

1. 今天做了啥
2. 来源是啥
3. 原始的数据库

And add cross-cutting filters:

- source;
- device;
- location;
- project;
- confidence;
- needs review.

The Source Board should show each source's collector device, upload path, last imported batch, events count, and error state.

The Device dimension should show which branch collectors are active and when they last uploaded.

The Location dimension should initially default to `unknown`, then improve via iPhone/location/calendar signals.
