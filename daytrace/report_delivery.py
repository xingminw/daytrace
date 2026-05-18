"""Delivery channels for offline DayTrace reports — Feishu drive upload
and SMTP email. Both are optional; cron/CLI decides whether to invoke.

Configuration:
  • Feishu folder tokens live in `config/feishu_drive.yaml` (created on
    first --upload-feishu run; folder is auto-created in your Feishu
    drive root). Lark identity uses --as user (assumes lark-cli is
    already auth'd).
  • SMTP credentials live in `~/.daytrace/secrets.env`, chmod 600:
        DAYTRACE_GMAIL_USER=...
        DAYTRACE_GMAIL_APP_PASSWORD=...
        DAYTRACE_EMAIL_TO=...

Errors raise; the CLI converts them to non-zero exit codes.
"""
from __future__ import annotations

import json
import os
import smtplib
import subprocess
from email.message import EmailMessage
from pathlib import Path

try:
    import yaml  # PyYAML is already a transitive dep of other daytrace modules
except ImportError:  # pragma: no cover
    yaml = None


REPO_ROOT = Path(__file__).resolve().parents[1]
FEISHU_CONFIG = REPO_ROOT / "config" / "feishu_drive.yaml"
SECRETS_PATH  = Path.home() / ".daytrace" / "secrets.env"


# ───── Feishu drive ──────────────────────────────────────────────────────

def _load_feishu_config() -> dict:
    if not FEISHU_CONFIG.exists():
        return {}
    if yaml is None:
        raise RuntimeError("PyYAML missing; install it to use Feishu upload")
    return yaml.safe_load(FEISHU_CONFIG.read_text(encoding="utf-8")) or {}


def _save_feishu_config(cfg: dict) -> None:
    FEISHU_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    if yaml is None:
        raise RuntimeError("PyYAML missing")
    FEISHU_CONFIG.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _lark(args: list[str], *, cwd: Path | None = None) -> dict:
    """Run lark-cli with --as user and return parsed JSON. Raises on
    non-zero exit. Stdout is expected to be JSON."""
    cmd = ["lark-cli", *args, "--as", "user"]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(
            f"lark-cli failed ({' '.join(args[:2])}): "
            f"exit {result.returncode}\nstderr: {result.stderr.strip()}"
        )
    out = result.stdout.strip()
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        raise RuntimeError(f"lark-cli returned non-JSON: {out[:300]}")


def _ensure_subfolder(parent_token: str | None, name: str) -> str:
    """Create the named folder if it doesn't already exist under parent;
    return the resulting folder token. lark-cli doesn't expose a stable
    'find by name' so we create-and-let-feishu-dedupe (Feishu actually
    creates a duplicate, so we cache the token in feishu_drive.yaml)."""
    args = ["drive", "+create-folder", "--name", name]
    if parent_token:
        args += ["--folder-token", parent_token]
    resp = _lark(args)
    # Response shape:  {"data": {"token": "...", "url": "..."}}
    data = resp.get("data") or resp
    token = data.get("token") or data.get("folder_token")
    if not token:
        raise RuntimeError(f"create-folder response missing token: {resp}")
    return token


def _ensure_folders() -> dict:
    """Make sure config/feishu_drive.yaml has root_token + daily_token +
    weekly_token. Creates folders on first run. Returns updated config."""
    cfg = _load_feishu_config()
    if not cfg.get("root_token"):
        print("[feishu] creating root folder 'DayTrace 报告' in Drive root...")
        cfg["root_token"] = _ensure_subfolder(None, "DayTrace 报告")
    if not cfg.get("daily_token"):
        print("[feishu] creating subfolder 'daily'...")
        cfg["daily_token"] = _ensure_subfolder(cfg["root_token"], "daily")
    if not cfg.get("weekly_token"):
        print("[feishu] creating subfolder 'weekly'...")
        cfg["weekly_token"] = _ensure_subfolder(cfg["root_token"], "weekly")
    _save_feishu_config(cfg)
    return cfg


def _upload_one(local_path: Path, folder_token: str, remote_name: str | None = None) -> dict:
    # lark-cli refuses absolute --file paths; pass just the filename and
    # cd into the parent dir for the subprocess.
    local_path = local_path.resolve()
    args = [
        "drive", "+upload",
        "--file", "./" + local_path.name,
        "--folder-token", folder_token,
    ]
    if remote_name:
        args += ["--name", remote_name]
    return _lark(args, cwd=local_path.parent)


def upload_to_feishu_drive(html_path: Path, md_path: Path, *,
                           kind: str, key: str, quiet: bool = False) -> dict:
    """Upload both files. Returns {html_url, md_url} (URLs may be None
    if lark-cli's response shape differs)."""
    cfg = _ensure_folders()
    folder_token = cfg["daily_token"] if kind == "daily" else cfg["weekly_token"]
    urls: dict[str, str] = {}
    for label, path in [("html", html_path), ("md", md_path)]:
        if not path.exists():
            continue
        resp = _upload_one(path, folder_token)
        data = resp.get("data") or resp
        url = data.get("url") or data.get("file_url")
        if url:
            urls[label] = url
    return urls


# ───── Email (Gmail SMTP) ────────────────────────────────────────────────

def _load_secrets() -> dict:
    """Parse ~/.daytrace/secrets.env (KEY=VALUE per line, # comments) into
    a dict. We don't fall back to os.environ — keeping the contract small
    so misconfiguration surfaces loudly."""
    if not SECRETS_PATH.exists():
        raise RuntimeError(
            f"missing {SECRETS_PATH} — copy the template from "
            "docs/cron-setup.md and fill in your Gmail app password"
        )
    out: dict[str, str] = {}
    for line in SECRETS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def email_report(*, kind: str, key: str, md_text: str, html_path: Path | None = None,
                 quiet: bool = False) -> None:
    """Send the Markdown content as an email body; attach the HTML
    archive (when present) so the recipient also has the rich version.

    Uses Gmail SMTP over SSL on port 465 with an app password."""
    secrets = _load_secrets()
    user = secrets.get("DAYTRACE_GMAIL_USER")
    pwd  = secrets.get("DAYTRACE_GMAIL_APP_PASSWORD")
    to   = secrets.get("DAYTRACE_EMAIL_TO")
    if not (user and pwd and to):
        raise RuntimeError(
            "secrets.env missing one of: DAYTRACE_GMAIL_USER, "
            "DAYTRACE_GMAIL_APP_PASSWORD, DAYTRACE_EMAIL_TO"
        )

    label = "每日 Report" if kind == "daily" else "每周 Report"
    # Mine a 1-line subject suffix out of the first heading in the MD body.
    subject_suffix = ""
    for line in md_text.splitlines():
        line = line.strip()
        if line.startswith("## 📰"):
            subject_suffix = line.removeprefix("## 📰").strip()
            break
    subject = f"DayTrace {label} · {key}"
    if subject_suffix:
        subject = f"{subject} · {subject_suffix}"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.set_content(md_text)

    # Attach HTML so the recipient gets the rich version too.
    if html_path and html_path.exists():
        msg.add_attachment(
            html_path.read_bytes(),
            maintype="text", subtype="html",
            filename=f"{key}.html",
        )

    if not quiet:
        print(f"[email] sending to {to}: {subject}")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(user, pwd)
        smtp.send_message(msg)
    if not quiet:
        print(f"  ✓ sent ({len(md_text)/1024:.1f} KB body"
              + (f" + {html_path.stat().st_size/1024:.0f} KB attachment" if html_path else "")
              + ")")
