# DayTrace Feishu App/Bot Machine Onboarding

> Status: draft. DayTrace multi-machine sync is CLI-first but Feishu Drive writes are unified around the local `lark-cli` Feishu App/Bot entity. A machine does not need to run Hermes or join a Feishu group for durable sync; Hermes/MTL can help execute commands during testing, but it is only control-plane automation.

## Core model

DayTrace separates two identities:

1. **Machine identity** — where the data came from. This is declared in the local collector config and encoded in the Drive path, manifest, and events.
2. **Upload entity** — the Feishu App/Bot entity behind the local `lark-cli` profile. Hub and branch machines use the same upload口径: `lark-cli --as bot`.

The shared Drive surface is intentionally just an inbox root:

```text
inbox/
  <machine>/
    <YYYY-MM-DD>/
      codex.jsonl
      hermes.jsonl
      git.jsonl
      manifest.json
```

Branch machines treat the provided folder token as the inbox root. They should not know about or create `DayTrace/hub`, `archive`, `failed`, database files, or cleanup folders.

## Responsibilities

### Branch machine / uploader

A branch machine is any computer that can run the DayTrace CLI. It may be a laptop, desktop, server, cron job, CI runner, or a Hermes-controlled shell. It only does upload-side work:

- stores a local machine config, for example `config/devices/omen-wsl.yaml`;
- stamps every event with the configured machine/device identity;
- writes one date batch to `inbox/<machine>/<date>/`;
- uploads through the local `lark-cli` App/Bot entity using `--as bot`;
- reports command output or exits with a non-zero status if upload/verification fails.

It must not:

- pull from Drive;
- import SQLite;
- clean up old remote data;
- decide missing-machine/date policy;
- mutate Hub archive/failed/database state.

### Hub / Mac

The Hub uses the same `--as bot` upload/download口径 for Drive access, then owns everything after upload:

- pulls from the shared inbox;
- imports into `data/daytrace.sqlite`;
- deduplicates already-imported files/events;
- archives or quarantines local files;
- checks missing machines/dates/sources;
- applies retention cleanup to the remote inbox;
- verifies dashboard/database state.

## New machine declaration

Record non-secret machine metadata in a local config and, if desired, an onboarding note. Do **not** store Feishu app secrets or tokens in the repo.

Example device config:

```yaml
device:
  id: omen-wsl
  name: OMEN WSL2
  location_id: home
  collector_id: daytrace-cli

sources:
  codex:
    enabled: true
    home: ~/.codex
  hermes:
    enabled: true
    sessions_dir: ~/.hermes/sessions
  git:
    enabled: true
    repos:
      - /mnt/d/research-programs/daytrace
```

Generate an onboarding command bundle:

```bash
python scripts/feishu_drive_sync.py \
  --inbox-token "$DAYTRACE_FEISHU_INBOX_TOKEN" \
  machine-onboarding \
  --machine-id omen-wsl \
  --config config/devices/omen-wsl.yaml \
  --date 2026-05-15
```

The bundle includes:

1. the machine declaration;
2. the target Drive path `inbox/<machine>/<date>/`;
3. a minimal Feishu CLI list smoke test;
4. the upload command;
5. the Hub-side pull/import reminder;
6. optional app-scope and folder-ACL guidance if `--client-id` or `--bot-open-id` is supplied.

## Feishu upload model

Preferred and default口径:

```text
lark-cli --as bot
```

For DayTrace we treat the CLI-visible Feishu App/Bot as the upload entity. The uploader's job is only to place files in Drive. It does **not** express which machine produced the data.

Machine identity comes from:

- `config/devices/<machine>.yaml`;
- `inbox/<machine>/<date>/`;
- `manifest.json`;
- each event's `device_id` / device fields.

Because this is a personal recording workflow running on the user's own machines, DayTrace does not model complex multi-tenant permissions. Operationally, each participating local `lark-cli` App/Bot only needs enough Feishu Drive access to list, create folders, upload, pull, and optionally clean up within the shared inbox.

If Drive access fails, interpret it mechanically:

1. Open Platform Drive scopes may be missing for the actual `clientID` used by the failing command.
2. The shared inbox folder may not yet be accessible to that App/Bot entity.

Do not switch to `--as user` as a silent fallback. If a user-token flow is needed for a special one-off, make it explicit and report it as a different identity mode.

## Smoke test before full upload

First prove the local Feishu CLI App/Bot can list the inbox root:

```bash
lark-cli drive files list \
  --params '{"folder_token":"<DAYTRACE_FEISHU_INBOX_TOKEN>","page_size":5}' \
  --as bot \
  --page-all
```

Interpretation:

- `code=0` / `ok=true`: local Feishu CLI App/Bot can see the inbox. Proceed to upload.
- `99991672`: app/API scope problem.
- `1061004`: App/Bot entity lacks resource access to the inbox folder.
- `strict_mode` requiring bot: OK for the DayTrace default model; rerun with `--as bot`.
- `strict_mode` requiring user: local policy conflicts with the DayTrace bot口径; switch only after explicit user approval.

## Upload one date

```bash
DAYTRACE_FEISHU_INBOX_TOKEN='<DAYTRACE_FEISHU_INBOX_TOKEN>' \
python scripts/feishu_drive_sync.py \
  upload-date \
  --config config/devices/omen-wsl.yaml \
  --date 2026-05-15 \
  --lookback-days 1
```

`--as bot` is the default. It is fine to include it explicitly in tests:

```bash
DAYTRACE_FEISHU_INBOX_TOKEN='<DAYTRACE_FEISHU_INBOX_TOKEN>' \
python scripts/feishu_drive_sync.py \
  --as bot \
  upload-date \
  --config config/devices/omen-wsl.yaml \
  --date 2026-05-15 \
  --lookback-days 1
```

Expected target:

```text
inbox/omen-wsl/2026-05-15/
```

Expected output contains `verification.status = verified` after the remote folder's file set matches the local staging folder.

## Hub pull/import

Hub pulls explicitly per machine/date, also using the default bot口径:

```bash
DAYTRACE_FEISHU_INBOX_TOKEN='<DAYTRACE_FEISHU_INBOX_TOKEN>' \
python scripts/feishu_drive_sync.py \
  pull \
  --device omen-wsl \
  --date 2026-05-15 \
  --local-inbox inbox

python scripts/import_inbox.py \
  --inbox inbox \
  --archive archive \
  --failed failed \
  --db data/daytrace.sqlite
```

## Cleanup and retention

Cleanup is Hub-only and dry-run by default:

```bash
python scripts/feishu_drive_sync.py \
  cleanup \
  --keep-days 30
```

Live deletion requires explicit machine filters:

```bash
python scripts/feishu_drive_sync.py \
  cleanup \
  --before 2026-05-01 \
  --device omen-wsl \
  --delete
```

Do not run cleanup from branch machines.
