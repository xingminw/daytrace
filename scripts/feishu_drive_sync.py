#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daytrace.collector_config import load_collector_config  # noqa: E402
from daytrace.feishu_drive_sync import (  # noqa: E402
    DEFAULT_INBOX_TOKEN,
    DEFAULT_STATE_PATH,
    DEFAULT_INBOX_TOKEN_ENV,
    LarkCli,
    build_machine_onboarding_bundle,
    cleanup_old_date_folders,
    ensure_date,
    ensure_safe_segment,
    pull_device_date,
    require_inbox_token,
    verify_uploaded_device_date,
)
from scripts.collect_from_config import collect_configured  # noqa: E402


def _json(obj: object) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))


def upload_date(args: argparse.Namespace) -> None:
    day = ensure_date(args.date)
    config_path = Path(args.config).expanduser()
    config = load_collector_config(config_path)
    device = ensure_safe_segment(str(config["device"]["id"]), "device.id")
    work_dir = Path(args.work_dir).expanduser()
    local_root = work_dir / "upload" / device / day
    staging_root = local_root / "drive-root"

    manifest = collect_configured(
        config_path,
        day,
        args.lookback_days,
        staging_root,
    )
    cli = LarkCli(executable=args.lark_cli, identity=args.as_identity, dry_run=args.dry_run)
    inbox_token = require_inbox_token(args.inbox_token)
    push_result = cli.push_dir(staging_root, inbox_token, if_exists=args.if_exists)
    if args.dry_run:
        verify_result = {
            "status": "skipped",
            "reason": "remote verification is skipped for dry-run uploads",
        }
    else:
        verify_result = verify_uploaded_device_date(
            cli=cli,
            inbox_token=inbox_token,
            staging_root=staging_root,
            device=device,
            day=day,
        )
    summary = {
        "status": "uploaded" if not args.dry_run else "dry_run",
        "device": device,
        "date": day,
        "lookback_days": args.lookback_days,
        "target_path": f"inbox/{device}/{day}/",
        "local_staging_root": str(staging_root),
        "manifest": manifest,
        "verification": verify_result,
        "lark_result": push_result.get("data", push_result),
    }
    summary_path = local_root / "upload-summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    summary["summary_path"] = str(summary_path)
    _json(summary)


def pull(args: argparse.Namespace) -> None:
    result = pull_device_date(
        cli=LarkCli(executable=args.lark_cli, identity=args.as_identity, dry_run=args.dry_run),
        inbox_token=require_inbox_token(args.inbox_token),
        device=args.device,
        day=args.date,
        local_inbox=Path(args.local_inbox).expanduser(),
        state_path=Path(args.state).expanduser(),
        force=args.force,
    )
    _json(result)


def cleanup(args: argparse.Namespace) -> None:
    result = cleanup_old_date_folders(
        cli=LarkCli(executable=args.lark_cli, identity=args.as_identity, dry_run=args.dry_run),
        inbox_token=require_inbox_token(args.inbox_token),
        before=args.before,
        keep_days=args.keep_days,
        devices=set(args.device) if args.device else None,
        dry_run=not args.delete,
    )
    _json(result)


def machine_onboarding(args: argparse.Namespace) -> None:
    result = build_machine_onboarding_bundle(
        machine_id=args.machine_id,
        client_id=args.client_id,
        bot_open_id=args.bot_open_id,
        inbox_token=require_inbox_token(args.inbox_token),
        config_path=args.config,
        date_value=args.date,
        upload_identity=args.upload_identity,
    )
    _json(result)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Programmatic DayTrace Feishu Drive inbox sync. Pass --inbox-token or set the environment variable used by the tool."
    )
    parser.add_argument(
        "--inbox-token",
        default=DEFAULT_INBOX_TOKEN,
        help=f"Feishu Drive shared inbox folder token; defaults to ${DEFAULT_INBOX_TOKEN_ENV}",
    )
    parser.add_argument("--lark-cli", default="lark-cli")
    parser.add_argument("--as", dest="as_identity", default="user", choices=["bot", "user"], help="lark-cli upload identity; CLI-first DayTrace sync defaults to user")
    parser.add_argument("--dry-run", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_upload = sub.add_parser("upload-date", help="collect one date from a device config and upload to inbox/<machine>/<date>/")
    p_upload.add_argument("--config", required=True, help="device collector YAML/JSON config")
    p_upload.add_argument("--date", default=datetime.now().date().isoformat())
    p_upload.add_argument("--lookback-days", type=int, default=1)
    p_upload.add_argument("--work-dir", default="outputs/feishu-drive-sync")
    p_upload.add_argument("--if-exists", default="skip", choices=["skip", "overwrite"])
    p_upload.set_defaults(func=upload_date)

    p_pull = sub.add_parser("pull", help="Hub-only: pull inbox/<machine>/<date>/ into local inbox with duplicate-pull ledger")
    p_pull.add_argument("--device", required=True)
    p_pull.add_argument("--date", required=True)
    p_pull.add_argument("--local-inbox", default="inbox")
    p_pull.add_argument("--state", default=str(DEFAULT_STATE_PATH))
    p_pull.add_argument("--force", action="store_true")
    p_pull.set_defaults(func=pull)

    p_cleanup = sub.add_parser("cleanup", help="Hub-only: list or delete old date folders under the shared Drive inbox")
    cutoff = p_cleanup.add_mutually_exclusive_group(required=True)
    cutoff.add_argument("--before", help="delete/list date folders older than YYYY-MM-DD")
    cutoff.add_argument("--keep-days", type=int, help="delete/list date folders older than today minus N days")
    p_cleanup.add_argument("--device", action="append", help="limit cleanup to this device; required when --delete is used")
    p_cleanup.add_argument("--delete", action="store_true", help="actually delete; default is dry-run/list only")
    p_cleanup.set_defaults(func=cleanup)

    p_onboard = sub.add_parser("machine-onboarding", help="generate CLI-first machine declaration, smoke-test command, and upload command")
    p_onboard.add_argument("--machine-id", required=True)
    p_onboard.add_argument("--client-id", help="optional lark-cli app/clientID; only needed for bot/app identities")
    p_onboard.add_argument("--bot-open-id", help="optional Feishu bot/user open_id to include in folder grant guidance")
    p_onboard.add_argument("--config", default="config/devices/omen-wsl.yaml")
    p_onboard.add_argument("--date", default="2026-05-14")
    p_onboard.add_argument("--upload-identity", choices=["user", "bot"], default="user")
    p_onboard.set_defaults(func=machine_onboarding)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
