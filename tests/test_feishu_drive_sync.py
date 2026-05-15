from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from daytrace.feishu_drive_sync import (
    DriveEntry,
    FeishuDriveSyncError,
    build_machine_onboarding_bundle,
    collect_remote_files,
    cleanup_old_date_folders,
    ensure_date,
    ensure_remote_name,
    ensure_safe_segment,
    pull_device_date,
    require_inbox_token,
    verify_uploaded_device_date,
)


def test_require_inbox_token_rejects_empty_value():
    with pytest.raises(FeishuDriveSyncError):
        require_inbox_token("")
    assert require_inbox_token("folder-token") == "folder-token"


def test_machine_onboarding_bundle_generates_scope_link_acl_guidance_and_smoke_commands():
    bundle = build_machine_onboarding_bundle(
        machine_id="omen-wsl",
        inbox_token="folder-token",
    )

    assert bundle["machine_id"] == "omen-wsl"
    assert bundle["protocol"] == "daytrace.cli_upload.v1"
    assert bundle["machine_declaration"]["target_path"] == "inbox/omen-wsl/2026-05-14/"
    assert bundle["upload_identity"] == "bot"
    assert bundle["scope_url"] is None
    assert bundle["feishu_cli_authorization"]["folder_token"] == "folder-token"
    assert bundle["optional_bot_folder_acl"] is None
    assert "lark-cli drive files list" in bundle["smoke_test_command"]
    assert "--as bot" in bundle["smoke_test_command"]
    assert "upload-date" in bundle["upload_command"]
    assert "--as bot" in bundle["upload_command"]


def test_machine_onboarding_bundle_can_include_optional_bot_scope_guidance():
    bundle = build_machine_onboarding_bundle(
        machine_id="omen-wsl",
        client_id="cli_aa88cce2d7389bb5",
        inbox_token="folder-token",
        bot_open_id="ou_bot",
        upload_identity="bot",
    )

    assert bundle["scope_url"].startswith("https://open.feishu.cn/page/scope-apply?clientID=cli_aa88cce2d7389bb5")
    assert "space%3Adocument%3Aretrieve" in bundle["scope_url"]
    assert bundle["optional_bot_folder_acl"]["grant_app_member"]["member_type"] == "appid"
    assert "--as bot" in bundle["smoke_test_command"]


class FakeCli:
    def __init__(self, write_all_files=True):
        self.tree = {
            "inbox": [DriveEntry("omen", "omen-token", "folder")],
            "omen-token": [DriveEntry("2026-05-14", "date-token", "folder")],
            "date-token": [
                DriveEntry("manifest.json", "manifest-token", "file", "100"),
                DriveEntry("codex.jsonl", "codex-token", "file", "101"),
            ],
        }
        self.pull_calls = []
        self.deleted = []
        self.write_all_files = write_all_files

    def list_folder(self, folder_token: str):
        return self.tree.get(folder_token, [])

    def push_dir(self, local_dir: Path, folder_token: str, *, if_exists: str = "skip"):
        return {"data": {"summary": {"uploaded": 0}}}

    def pull_dir(self, folder_token, local_dir, *, if_exists="overwrite"):
        self.pull_calls.append((folder_token, Path(local_dir), if_exists))
        Path(local_dir).mkdir(parents=True, exist_ok=True)
        (Path(local_dir) / "manifest.json").write_text("{}\n", encoding="utf-8")
        if self.write_all_files:
            (Path(local_dir) / "codex.jsonl").write_text('{"ok": true}\n', encoding="utf-8")
        return {"data": {"summary": {"downloaded": 2}}}

    def delete_folder(self, folder_token):
        self.deleted.append(folder_token)
        return {"data": {"deleted": folder_token}}


def test_safe_segment_and_date_validation():
    assert ensure_safe_segment("omen-wsl", "device") == "omen-wsl"
    assert ensure_date("2026-05-14") == "2026-05-14"
    with pytest.raises(FeishuDriveSyncError):
        ensure_safe_segment("../escape", "device")
    with pytest.raises(FeishuDriveSyncError):
        ensure_date("20260514")
    for value in ["", ".", "..", "../escape", "folder/name", "folder\\name"]:
        with pytest.raises(FeishuDriveSyncError):
            ensure_remote_name(value)


def test_verify_uploaded_device_date_checks_remote_hierarchy_and_files(tmp_path):
    cli = FakeCli()
    staging = tmp_path / "staging"
    date_dir = staging / "omen" / "2026-05-14"
    date_dir.mkdir(parents=True)
    (date_dir / "manifest.json").write_text("{}\n", encoding="utf-8")
    (date_dir / "codex.jsonl").write_text('{"ok": true}\n', encoding="utf-8")

    result = verify_uploaded_device_date(
        cli=cli,
        inbox_token="inbox",
        staging_root=staging,
        device="omen",
        day="2026-05-14",
    )

    assert result["status"] == "verified"
    assert result["remote_folder_token"] == "date-token"
    assert result["expected_files"] == ["codex.jsonl", "manifest.json"]


def test_verify_uploaded_device_date_rejects_missing_remote_file(tmp_path):
    cli = FakeCli()
    staging = tmp_path / "staging"
    date_dir = staging / "omen" / "2026-05-14"
    date_dir.mkdir(parents=True)
    (date_dir / "manifest.json").write_text("{}\n", encoding="utf-8")
    (date_dir / "hermes.jsonl").write_text('{"ok": true}\n', encoding="utf-8")

    with pytest.raises(FeishuDriveSyncError):
        verify_uploaded_device_date(
            cli=cli,
            inbox_token="inbox",
            staging_root=staging,
            device="omen",
            day="2026-05-14",
        )


def test_verify_uploaded_device_date_rejects_extra_remote_file(tmp_path):
    cli = FakeCli()
    staging = tmp_path / "staging"
    date_dir = staging / "omen" / "2026-05-14"
    date_dir.mkdir(parents=True)
    (date_dir / "manifest.json").write_text("{}\n", encoding="utf-8")
    (date_dir / "codex.jsonl").write_text('{"ok": true}\n', encoding="utf-8")
    cli.tree["date-token"].append(DriveEntry("extra.jsonl", "extra-token", "file", "102"))

    with pytest.raises(FeishuDriveSyncError):
        verify_uploaded_device_date(
            cli=cli,
            inbox_token="inbox",
            staging_root=staging,
            device="omen",
            day="2026-05-14",
        )


def test_collect_remote_files_rejects_duplicate_relative_paths():
    cli = FakeCli()
    cli.tree["date-token"].append(DriveEntry("codex.jsonl", "codex-token-2", "file", "102"))

    with pytest.raises(FeishuDriveSyncError):
        collect_remote_files(cli, "date-token")


def test_pull_device_date_records_ledger_and_skips_duplicate(tmp_path):
    cli = FakeCli()
    state = tmp_path / "state.json"
    inbox = tmp_path / "inbox"

    first = pull_device_date(
        cli=cli,
        inbox_token="inbox",
        device="omen",
        day="2026-05-14",
        local_inbox=inbox,
        state_path=state,
    )
    assert first["status"] == "pulled"
    assert len(cli.pull_calls) == 1
    assert (inbox / "omen" / "2026-05-14" / "manifest.json").exists()

    second = pull_device_date(
        cli=cli,
        inbox_token="inbox",
        device="omen",
        day="2026-05-14",
        local_inbox=inbox,
        state_path=state,
    )
    assert second["status"] == "skipped"
    assert len(cli.pull_calls) == 1


def test_pull_device_date_refetches_when_ledger_matches_but_local_file_missing(tmp_path):
    cli = FakeCli()
    state = tmp_path / "state.json"
    inbox = tmp_path / "inbox"

    pull_device_date(
        cli=cli,
        inbox_token="inbox",
        device="omen",
        day="2026-05-14",
        local_inbox=inbox,
        state_path=state,
    )
    (inbox / "omen" / "2026-05-14" / "codex.jsonl").unlink()
    pull_device_date(
        cli=cli,
        inbox_token="inbox",
        device="omen",
        day="2026-05-14",
        local_inbox=inbox,
        state_path=state,
    )
    assert len(cli.pull_calls) == 2


def test_pull_device_date_does_not_record_ledger_when_expected_file_missing(tmp_path):
    cli = FakeCli(write_all_files=False)
    state = tmp_path / "state.json"

    with pytest.raises(FeishuDriveSyncError):
        pull_device_date(
            cli=cli,
            inbox_token="inbox",
            device="omen",
            day="2026-05-14",
            local_inbox=tmp_path / "inbox",
            state_path=state,
        )
    assert not state.exists()


def test_cleanup_rejects_negative_keep_days():
    with pytest.raises(FeishuDriveSyncError):
        cleanup_old_date_folders(
            cli=FakeCli(),
            inbox_token="inbox",
            keep_days=-1,
            today=date(2026, 5, 15),
        )


def test_cleanup_live_delete_requires_explicit_device():
    with pytest.raises(FeishuDriveSyncError):
        cleanup_old_date_folders(
            cli=FakeCli(),
            inbox_token="inbox",
            before="2026-05-12",
            dry_run=False,
        )


def test_cleanup_old_date_folders_dry_run_and_delete():
    cli = FakeCli()
    cli.tree["omen-token"].extend(
        [
            DriveEntry("2026-05-10", "old-token", "folder"),
            DriveEntry("notes", "notes-token", "folder"),
            DriveEntry("2026-05-20", "new-token", "folder"),
        ]
    )
    dry = cleanup_old_date_folders(
        cli=cli,
        inbox_token="inbox",
        keep_days=3,
        today=date(2026, 5, 15),
        dry_run=True,
    )
    assert {item["token"] for item in dry["candidates"]} == {"old-token"}
    assert cli.deleted == []

    live = cleanup_old_date_folders(
        cli=cli,
        inbox_token="inbox",
        before="2026-05-12",
        devices={"omen"},
        dry_run=False,
    )
    assert [item["token"] for item in live["deleted"]] == ["old-token"]
    assert cli.deleted == ["old-token"]
