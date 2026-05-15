# DayTrace CLI Machine Onboarding

> Status: draft. DayTrace multi-machine sync is CLI-first: a machine does not need to run Hermes, join a Feishu group, or own a Feishu bot. Hermes/MTL can help execute commands during testing, but it is not part of the durable sync architecture.

## Core model

DayTrace separates two identities:

1. **Machine identity** — where the data came from. This is declared in the local collector config and encoded in the Drive path, manifest, and events.
2. **Upload identity** — which Feishu credential writes files into Drive. The default is `lark-cli --as user` on each machine, authorized to the same Feishu user/service identity that can edit the shared inbox.

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
- reports command output or exits with a non-zero status if upload/verification fails.

It must not:

- pull from Drive;
- import SQLite;
- clean up old remote data;
- decide missing-machine/date policy;
- mutate Hub archive/failed/database state.

### Hub / Mac

The Hub owns everything after upload:

- pulls from the shared inbox;
- imports into `data/daytrace.sqlite`;
- deduplicates already-imported files/events;
- archives or quarantines local files;
- checks missing machines/dates/sources;
- applies retention cleanup to the remote inbox;
- verifies dashboard/database state.

## New machine declaration

Record non-secret machine metadata in a local config and, if desired, an onboarding note. Do **not** store Feishu user tokens or app secrets in the repo.

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
  --date 2026-05-15 \
  --upload-identity user
```

The bundle includes:

1. the machine declaration;
2. the target Drive path `inbox/<machine>/<date>/`;
3. a minimal Feishu CLI list smoke test;
4. the upload command;
5. the Hub-side pull/import reminder;
6. optional app-scope URL only if `--client-id` is supplied for a bot/app style identity.

## Feishu authorization model

Preferred default:

```text
lark-cli --as user
```

Each machine authorizes its local `lark-cli` user identity. The shared inbox folder only needs to be editable by that user/service identity. In this model, Feishu Drive may show the same uploader for all machines; DayTrace machine identity comes from path/config/manifest/events, not from Drive uploader identity.

Use bot/app identity only for special automation tests. If using `--as bot`, the machine's app still needs both:

1. Open Platform Drive scopes; and
2. resource access to the inbox folder.

That bot/app path is optional and should not be the default DayTrace architecture.

## Smoke test before full upload

First prove the local Feishu CLI identity can list the inbox root:

```bash
lark-cli drive files list \
  --params '{"folder_token":"<DAYTRACE_FEISHU_INBOX_TOKEN>","page_size":5}' \
  --as user \
  --page-all
```

Interpretation:

- `code=0`: local Feishu CLI identity can see the inbox. Proceed to upload.
- `99991672`: app/API scope problem, relevant mainly for app/bot identities.
- `1061004`: identity lacks resource access to the inbox folder.

## Upload one date

```bash
DAYTRACE_FEISHU_INBOX_TOKEN='<DAYTRACE_FEISHU_INBOX_TOKEN>' \
python scripts/feishu_drive_sync.py \
  --as user \
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

Hub pulls explicitly per machine/date:

```bash
DAYTRACE_FEISHU_INBOX_TOKEN='<DAYTRACE_FEISHU_INBOX_TOKEN>' \
python scripts/feishu_drive_sync.py \
  --as user \
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
  --as user \
  cleanup \
  --keep-days 30
```

Live deletion requires explicit machine filters:

```bash
python scripts/feishu_drive_sync.py \
  --as user \
  cleanup \
  --before 2026-05-01 \
  --device omen-wsl \
  --delete
```

Do not run cleanup from branch machines.
