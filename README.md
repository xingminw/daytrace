# DayTrace

A local-first work-trace dashboard: Codex user messages and Git activity are
stored in SQLite, organized by local repository identity, and displayed as daily
and weekly reports. Historical Feishu snapshots remain readable; active task
sync, uploads and cleanup are disconnected.

```sh
cp config/devices/mac-local.example.yaml config/devices/mac-local.yaml
# Edit the local configuration to point to your repositories.
make daily           # collection, backup, import and deterministic statistics
make sync-projects   # local repository identities and attribution
make dashboard       # loopback server; no page-triggered AI
make test
```

AI generation is explicit and budgeted. Use the offline-plan, approved-execution
and reviewed-publication workflow in [evidence briefs](docs/evidence-briefs.md).
Normal collection and dashboard launchers disable AI. Optional SSH collection
uses an explicitly configured local registry; there is no remote discovery or
automatic mail delivery.

See [local repository workflow](docs/local-projects.md), [setup](docs/setup.md),
and [中文说明](README.zh.md). Runtime data, machine configurations, backups and
private review samples are ignored by Git. Credentials remain outside the repo.
Earlier architecture documents and demos describe historical integrations.
