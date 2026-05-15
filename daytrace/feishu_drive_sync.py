from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

DEFAULT_INBOX_TOKEN_ENV = "DAYTRACE_FEISHU_INBOX_TOKEN"
DEFAULT_INBOX_TOKEN = os.environ.get(DEFAULT_INBOX_TOKEN_ENV, "")
DEFAULT_STATE_PATH = Path(".daytrace/feishu_drive_state.json")
REQUIRED_MACHINE_SCOPES = [
    "space:document:retrieve",
    "space:folder:create",
    "drive:drive",
    "drive:drive:readonly",
    "drive:file:upload",
    "drive:file",
    "drive:file:readonly",
    "drive:drive.metadata:readonly",
    "space:document:delete",
    "docs:permission.member:create",
]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class FeishuDriveSyncError(RuntimeError):
    pass


def build_scope_apply_url(client_id: str, scopes: list[str] | None = None) -> str:
    if not client_id:
        raise FeishuDriveSyncError("client_id is required")
    selected = scopes or REQUIRED_MACHINE_SCOPES
    encoded = quote(",".join(selected), safe="")
    return f"https://open.feishu.cn/page/scope-apply?clientID={client_id}&scopes={encoded}"


def build_machine_onboarding_bundle(
    *,
    machine_id: str,
    inbox_token: str,
    client_id: str | None = None,
    bot_open_id: str | None = None,
    config_path: str = "config/devices/omen-wsl.yaml",
    date_value: str = "2026-05-14",
    upload_identity: str = "user",
) -> dict[str, Any]:
    machine = ensure_safe_segment(machine_id, "machine_id")
    token = require_inbox_token(inbox_token)
    if upload_identity not in {"user", "bot"}:
        raise FeishuDriveSyncError("upload_identity must be 'user' or 'bot'")
    bundle: dict[str, Any] = {
        "machine_id": machine,
        "protocol": "daytrace.cli_upload.v1",
        "machine_declaration": {
            "machine_id": machine,
            "config_path": config_path,
            "remote_path_template": "inbox/<machine>/<date>/",
            "target_path": f"inbox/{machine}/{date_value}/",
            "responsibility": "collect local events and upload only; Hub owns pull/import/cleanup",
        },
        "upload_identity": upload_identity,
        "client_id": client_id,
        "bot_open_id": bot_open_id,
        "scope_url": build_scope_apply_url(client_id) if client_id else None,
        "scopes": REQUIRED_MACHINE_SCOPES if client_id else [],
        "feishu_cli_authorization": {
            "default_identity": "user",
            "selected_identity": upload_identity,
            "folder_token": token,
            "folder_url": f"https://my.feishu.cn/drive/folder/{token}",
            "rule": "Authorize lark-cli on this machine with an identity that can edit the shared inbox. Machine identity comes from config/path, not the Drive uploader.",
        },
        "optional_bot_folder_acl": {
            "grant_endpoint": f"POST /open-apis/drive/v1/permissions/{token}/members?type=folder&need_notification=false",
            "grant_app_member": {
                "member_type": "appid",
                "member_id": client_id,
                "perm": "edit",
                "type": "user",
            },
            "grant_bot_member": {
                "member_type": "openid",
                "member_id": bot_open_id,
                "perm": "edit",
                "type": "user",
            }
            if bot_open_id
            else None,
            "fallback": "Only needed for bot/app upload identities. CLI-first machines should prefer --as user or a service user.",
        }
        if client_id or bot_open_id
        else None,
        "smoke_test_command": "\n".join(
            [
                "lark-cli drive files list \\",
                f"  --params '{{\"folder_token\":\"{token}\",\"page_size\":5}}' \\",
                f"  --as {upload_identity} \\",
                "  --page-all",
            ]
        ),
        "upload_command": "\n".join(
            [
                f"{DEFAULT_INBOX_TOKEN_ENV}='{token}' \\",
                f"python scripts/feishu_drive_sync.py --as {upload_identity} upload-date \\",
                f"  --config {config_path} \\",
                f"  --date {date_value} \\",
                "  --lookback-days 1",
            ]
        ),
        "hub_responsibilities": [
            "pull from shared inbox",
            "import into data/daytrace.sqlite",
            "deduplicate imported files/events",
            "archive or quarantine local files",
            "check missing machines/dates/sources",
            "apply retention cleanup to remote inbox",
        ],
        "error_interpretation": {
            "99991672": "Open Platform scope missing or not effective; mostly relevant for app/bot identities.",
            "1061004": "The selected lark-cli identity cannot access the inbox folder; authorize/share the inbox to that user/service identity.",
        },
    }
    return bundle


@dataclass(frozen=True)
class DriveEntry:
    name: str
    token: str
    type: str
    modified_time: str | None = None


class DriveCli(Protocol):
    def list_folder(self, folder_token: str) -> list[DriveEntry]: ...

    def push_dir(self, local_dir: Path, folder_token: str, *, if_exists: str = "skip") -> dict[str, Any]: ...

    def pull_dir(self, folder_token: str, local_dir: Path, *, if_exists: str = "overwrite") -> dict[str, Any]: ...

    def delete_folder(self, folder_token: str) -> dict[str, Any]: ...


@dataclass
class LarkCli:
    executable: str = "lark-cli"
    identity: str = "bot"
    dry_run: bool = False

    def require_available(self) -> None:
        if shutil.which(self.executable) is None:
            raise FeishuDriveSyncError(
                f"{self.executable!r} not found. Install/configure lark-cli first."
            )

    def run_json(self, args: list[str], *, high_risk: bool = False) -> dict[str, Any]:
        self.require_available()
        cmd = [self.executable, *args, "--as", self.identity]
        if self.dry_run:
            cmd.append("--dry-run")
        if high_risk:
            cmd.append("--yes")
        proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
        if proc.returncode != 0:
            raise FeishuDriveSyncError(
                f"lark-cli failed ({proc.returncode}): {' '.join(cmd)}\n"
                f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
            )
        text = proc.stdout.strip()
        if not text:
            return {"ok": True}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Some lark-cli shortcuts print progress lines before the final JSON.
            start = text.find("{")
            if start == -1:
                raise FeishuDriveSyncError(f"lark-cli did not return JSON:\n{text}")
            data = json.loads(text[start:])
        if data.get("ok") is False:
            raise FeishuDriveSyncError(f"lark-cli API error: {json.dumps(data, ensure_ascii=False)}")
        return data

    def list_folder(self, folder_token: str) -> list[DriveEntry]:
        data = self.run_json(
            [
                "drive",
                "files",
                "list",
                "--params",
                json.dumps({"folder_token": folder_token, "page_size": 200}),
                "--page-all",
            ]
        )
        files = data.get("data", {}).get("files", [])
        return [
            DriveEntry(
                name=str(item.get("name")),
                token=str(item.get("token")),
                type=str(item.get("type")),
                modified_time=item.get("modified_time"),
            )
            for item in files
        ]

    def push_dir(self, local_dir: Path, folder_token: str, *, if_exists: str = "skip") -> dict[str, Any]:
        return self.run_json(
            [
                "drive",
                "+push",
                "--folder-token",
                folder_token,
                "--local-dir",
                str(local_dir),
                "--if-exists",
                if_exists,
            ]
        )

    def pull_dir(self, folder_token: str, local_dir: Path, *, if_exists: str = "overwrite") -> dict[str, Any]:
        local_dir.mkdir(parents=True, exist_ok=True)
        return self.run_json(
            [
                "drive",
                "+pull",
                "--folder-token",
                folder_token,
                "--local-dir",
                str(local_dir),
                "--if-exists",
                if_exists,
            ]
        )

    def delete_folder(self, folder_token: str) -> dict[str, Any]:
        return self.run_json(
            ["drive", "+delete", "--file-token", folder_token, "--type", "folder"],
            high_risk=True,
        )


def ensure_safe_segment(value: str, label: str) -> str:
    if not SAFE_SEGMENT_RE.fullmatch(value):
        raise FeishuDriveSyncError(f"{label} must be a safe path segment: {value!r}")
    return value


def require_inbox_token(value: str) -> str:
    if not value:
        raise FeishuDriveSyncError(
            "missing Feishu Drive inbox token; pass --inbox-token or set "
            f"{DEFAULT_INBOX_TOKEN_ENV}"
        )
    return value


def ensure_remote_name(value: str) -> str:
    if value in {"", ".", ".."} or "/" in value or "\\" in value:
        raise FeishuDriveSyncError(f"unsafe remote Drive entry name: {value!r}")
    return ensure_safe_segment(value, "remote Drive entry name")


def ensure_child_path(root: Path, rel_path: str) -> Path:
    parts = [ensure_remote_name(part) for part in Path(rel_path).parts]
    child = root.joinpath(*parts).resolve()
    root_resolved = root.resolve()
    if child != root_resolved and root_resolved not in child.parents:
        raise FeishuDriveSyncError(f"remote path escapes local root: {rel_path!r}")
    return child


def ensure_date(value: str) -> str:
    if not DATE_RE.fullmatch(value):
        raise FeishuDriveSyncError(f"date must be YYYY-MM-DD: {value!r}")
    date.fromisoformat(value)
    return value


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": "daytrace.feishu_drive_state.v1", "pulled_files": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_child_folder(cli: DriveCli, parent_token: str, name: str) -> DriveEntry | None:
    ensure_safe_segment(name, "folder name")
    matches = [entry for entry in cli.list_folder(parent_token) if entry.name == name and entry.type == "folder"]
    if len(matches) > 1:
        raise FeishuDriveSyncError(f"ambiguous remote folder name under parent: {name!r}")
    return matches[0] if matches else None


def get_folder_by_path(cli: DriveCli, parent_token: str, parts: list[str]) -> DriveEntry | None:
    current_token = parent_token
    current_entry: DriveEntry | None = None
    for part in parts:
        current_entry = find_child_folder(cli, current_token, part)
        if current_entry is None:
            return None
        current_token = current_entry.token
    return current_entry


def collect_remote_files(
    cli: DriveCli,
    folder_token: str,
    prefix: str = "",
) -> list[dict[str, str | None]]:
    out: list[dict[str, str | None]] = []
    for entry in cli.list_folder(folder_token):
        name = ensure_remote_name(entry.name)
        rel = f"{prefix}/{name}" if prefix else name
        if entry.type == "folder":
            out.extend(collect_remote_files(cli, entry.token, rel))
        elif entry.type == "file":
            out.append(
                {
                    "rel_path": rel,
                    "token": entry.token,
                    "modified_time": entry.modified_time,
                }
            )
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in out:
        rel_path = str(item["rel_path"])
        if rel_path in seen:
            duplicates.add(rel_path)
        seen.add(rel_path)
    if duplicates:
        raise FeishuDriveSyncError(
            "duplicate remote Drive file paths: " + ", ".join(sorted(duplicates))
        )
    return out


def remote_file_version(item: dict[str, str | None]) -> str:
    return f"{item.get('token')}:{item.get('modified_time') or ''}"


def pulled_state_key(device: str, day: str, rel_path: str) -> str:
    return f"{device}/{day}/{rel_path}"


def verify_uploaded_device_date(
    *,
    cli: DriveCli,
    inbox_token: str,
    staging_root: Path,
    device: str,
    day: str,
) -> dict[str, Any]:
    """Verify that push created <inbox>/<device>/<day>/ with the staged files."""
    device = ensure_safe_segment(device, "device")
    day = ensure_date(day)
    local_date_dir = staging_root / device / day
    if not local_date_dir.is_dir():
        raise FeishuDriveSyncError(f"local staging date folder is missing: {local_date_dir}")
    expected = sorted(
        str(path.relative_to(local_date_dir))
        for path in local_date_dir.rglob("*")
        if path.is_file()
    )
    remote = get_folder_by_path(cli, inbox_token, [device, day])
    if remote is None:
        raise FeishuDriveSyncError(f"uploaded remote folder not found: {device}/{day}")
    remote_files = collect_remote_files(cli, remote.token)
    actual = sorted(str(item["rel_path"]) for item in remote_files)
    missing = [path for path in expected if path not in actual]
    unexpected = [path for path in actual if path not in expected]
    problems = []
    if missing:
        problems.append("missing expected files: " + ", ".join(missing))
    if unexpected:
        problems.append("unexpected remote files: " + ", ".join(unexpected))
    if problems:
        raise FeishuDriveSyncError("uploaded remote folder file set mismatch; " + "; ".join(problems))
    return {
        "status": "verified",
        "device": device,
        "date": day,
        "remote_folder_token": remote.token,
        "expected_files": expected,
        "remote_files": actual,
    }


def pull_device_date(
    *,
    cli: DriveCli,
    inbox_token: str,
    device: str,
    day: str,
    local_inbox: Path,
    state_path: Path,
    force: bool = False,
) -> dict[str, Any]:
    device = ensure_safe_segment(device, "device")
    day = ensure_date(day)
    remote = get_folder_by_path(cli, inbox_token, [device, day])
    if remote is None:
        raise FeishuDriveSyncError(f"remote folder not found: {device}/{day}")

    remote_files = collect_remote_files(cli, remote.token)
    state = load_state(state_path)
    pulled = state.setdefault("pulled_files", {})
    already = [
        item
        for item in remote_files
        if pulled.get(pulled_state_key(device, day, str(item["rel_path"])))
        == remote_file_version(item)
    ]
    if already and len(already) == len(remote_files) and not force:
        missing_local = [
            str(item["rel_path"])
            for item in remote_files
            if not ensure_child_path(local_inbox / device / day, str(item["rel_path"])).is_file()
        ]
        if not missing_local:
            return {
                "status": "skipped",
                "reason": "all remote files already pulled",
                "device": device,
                "date": day,
                "remote_folder_token": remote.token,
                "file_count": len(remote_files),
            }

    target_dir = local_inbox / device / day
    result = cli.pull_dir(remote.token, target_dir)
    missing = [
        str(item["rel_path"])
        for item in remote_files
        if not ensure_child_path(target_dir, str(item["rel_path"])).is_file()
    ]
    if missing:
        raise FeishuDriveSyncError(
            "pull completed but expected files are missing locally: " + ", ".join(missing)
        )
    hashes = {}
    for path in sorted(target_dir.rglob("*")):
        if path.is_file():
            hashes[str(path.relative_to(target_dir))] = sha256_file(path)
    for item in remote_files:
        pulled[pulled_state_key(device, day, str(item["rel_path"]))] = remote_file_version(item)
    state["last_pull"] = {
        "device": device,
        "date": day,
        "remote_folder_token": remote.token,
        "pulled_at": datetime.now().isoformat(timespec="seconds"),
        "file_count": len(remote_files),
    }
    save_state(state_path, state)
    return {
        "status": "pulled",
        "device": device,
        "date": day,
        "remote_folder_token": remote.token,
        "local_dir": str(target_dir),
        "remote_file_count": len(remote_files),
        "hashes": hashes,
        "lark_result": result.get("data", result),
    }


def cleanup_old_date_folders(
    *,
    cli: DriveCli,
    inbox_token: str,
    before: str | None = None,
    keep_days: int | None = None,
    today: date | None = None,
    devices: set[str] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    if before is None:
        if keep_days is None:
            raise FeishuDriveSyncError("cleanup requires --before or --keep-days")
        if keep_days < 0:
            raise FeishuDriveSyncError("cleanup --keep-days must be non-negative")
        today = today or date.today()
        before = (today - timedelta(days=keep_days)).isoformat()
    before = ensure_date(before)
    cutoff = date.fromisoformat(before)

    if devices is not None:
        devices = {ensure_safe_segment(device, "cleanup device") for device in devices}
    if not dry_run and not devices:
        raise FeishuDriveSyncError("live cleanup requires at least one explicit device")

    candidates = []
    for device_entry in cli.list_folder(inbox_token):
        if device_entry.type != "folder":
            continue
        device_name = ensure_safe_segment(device_entry.name, "remote device folder")
        if devices is not None and device_name not in devices:
            continue
        for day_entry in cli.list_folder(device_entry.token):
            if day_entry.type != "folder" or not DATE_RE.fullmatch(day_entry.name):
                continue
            if date.fromisoformat(day_entry.name) < cutoff:
                candidates.append(
                    {
                        "device": device_name,
                        "date": day_entry.name,
                        "token": day_entry.token,
                    }
                )

    deleted = []
    if not dry_run:
        for item in candidates:
            deleted.append({**item, "result": cli.delete_folder(str(item["token"])).get("data", {})})
    return {"before": before, "dry_run": dry_run, "candidates": candidates, "deleted": deleted}
