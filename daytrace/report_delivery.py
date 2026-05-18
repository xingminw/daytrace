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


# ───── Dashboard URL (Tailscale Serve) ───────────────────────────────────

def _tailscale_dnsname() -> str | None:
    """Best-effort: ask the local tailscaled for this machine's MagicDNS
    name. Empty string when Tailscale isn't installed / not in a tailnet."""
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        # `Self.DNSName` looks like `xingmins-macbook-air.tail24bb1.ts.net.`
        name = (data.get("Self") or {}).get("DNSName", "").rstrip(".")
        return name or None
    except Exception:
        return None


def dashboard_url(*, kind: str, key: str) -> str | None:
    """Compose the Tailscale-served dashboard URL that opens the *live*
    report for this date/week. None when no Tailscale Serve config exists."""
    host = _tailscale_dnsname()
    if not host:
        return None
    # Confirm `tailscale serve status` actually has something running so
    # we don't hand out broken links. Empty status output = no serve.
    try:
        st = subprocess.run(["tailscale", "serve", "status"], capture_output=True, text=True, timeout=5)
        if "127.0.0.1:8765" not in (st.stdout or "") and "localhost:8765" not in (st.stdout or ""):
            return None
    except Exception:
        return None
    if kind == "daily":
        return f"https://{host}/today?date={key}"
    return f"https://{host}/weekly?week={key}"


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


def _import_one(local_path: Path, folder_token: str, *,
                doc_type: str = "docx", name: str | None = None) -> dict:
    """Import a local file as a Feishu *cloud document* (rendered, editable
    in-app) — distinct from +upload which stores the file as a raw blob.
    Used for Markdown → docx so the email link opens a beautiful native
    Feishu doc instead of a 'download me' file."""
    local_path = local_path.resolve()
    args = [
        "drive", "+import",
        "--file", "./" + local_path.name,
        "--type", doc_type,
        "--folder-token", folder_token,
    ]
    if name:
        args += ["--name", name]
    return _lark(args, cwd=local_path.parent)


def import_md_to_feishu_docs(md_path: Path, *,
                             kind: str, key: str, quiet: bool = False) -> dict:
    """Import the Markdown summary as a Feishu *cloud docx* document.

    Docx renders natively in Feishu (in-app and in the browser) with real
    formatting + clickable links — unlike a raw .md file which would
    download. This is the link we want to surface in the email body.

    Returns {"docx": url} (or empty dict on failure)."""
    cfg = _ensure_folders()
    folder_token = cfg["daily_token"] if kind == "daily" else cfg["weekly_token"]
    if not md_path.exists():
        return {}
    resp = _import_one(md_path, folder_token, doc_type="docx", name=key)
    data = resp.get("data") or resp
    url = data.get("url") or data.get("file_url")
    if not url and data.get("token"):
        url = f"https://www.feishu.cn/docx/{data['token']}"
    if not quiet:
        print(f"  ↑ docx  → {url or '(no url)'}")
    return {"docx": url} if url else {}


# ───── Email (Gmail SMTP) ────────────────────────────────────────────────

def _load_secrets() -> dict:
    """Parse ~/.daytrace/secrets.env (KEY=VALUE per line, # comments) into
    a dict. We don't fall back to os.environ — keeping the contract small
    so misconfiguration surfaces loudly."""
    if not SECRETS_PATH.exists():
        raise RuntimeError(
            f"missing {SECRETS_PATH} — copy the template from "
            "docs/delivery-setup.md and fill in your Gmail app password"
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


_EMAIL_CSS = """
<style>
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", "PingFang SC", "Microsoft YaHei", sans-serif;
    color:#2b2722; font-size:16px; line-height:1.75; max-width:780px;
    margin:0 auto; padding:36px 28px 48px;
    background: linear-gradient(180deg, #fdfaf2 0%, #fafaf7 80px, #fafaf7 100%);
  }

  /* Top brand strip */
  .brand { font-size:11px; font-weight:700; letter-spacing:0.22em; color:#9b8f7d; text-transform:uppercase; margin-bottom:18px; padding-bottom:10px; border-bottom:2px solid #f0d68b; }

  /* Header card with the two action links */
  .links { background:linear-gradient(135deg, #fff7e8 0%, #fef3d4 100%); border:1px solid #f0d68b; border-radius:14px; padding:20px 22px; margin-bottom:28px; box-shadow:0 1px 3px rgba(180,140,40,0.06); }
  .links a { display:block; margin:6px 0; color:#2f6fed; text-decoration:none; font-weight:600; line-height:1.55; }
  .links a:hover { text-decoration:underline; }
  .links .lbl { display:inline-block; min-width:122px; color:#6b6052; font-weight:600; margin-right:10px; font-size:13px; }

  /* Headings */
  h1 { font-size:30px; margin:8px 0 14px; color:#1a1814; letter-spacing:-0.012em; font-weight:800; }
  h2 { font-size:21px; margin:36px 0 14px; color:#1a1814; padding:8px 0 8px 14px; border-left:4px solid #f59e0b; background:rgba(245,158,11,0.05); border-radius:0 6px 6px 0; }
  h3 { font-size:16.5px; margin:24px 0 8px; color:#3b352e; padding-left:8px; border-left:3px solid #2f6fed; }

  /* Paragraphs + emphasis */
  p  { margin:12px 0 16px; }
  strong { color:#1a1814; font-weight:700; }
  em { color:#7a6f5f; font-style:normal; font-size:13.5px; }

  /* Blockquote = narrative paragraph */
  blockquote { margin:14px 0 22px; padding:14px 20px; background:#fff7e8; border-left:3px solid #f59e0b; border-radius:0 8px 8px 0; color:#3b352e; font-size:15.5px; line-height:1.8; }
  blockquote p { margin:6px 0; }

  /* Lists */
  ul { margin:10px 0 18px; padding-left:24px; }
  li { margin:7px 0; line-height:1.7; }

  /* Table = dashboard stats grid */
  table { width:100%; border-collapse:separate; border-spacing:0; margin:12px 0 24px; background:white; border:1px solid #ecdfc4; border-radius:10px; overflow:hidden; box-shadow:0 1px 2px rgba(180,140,40,0.04); }
  thead { background:#fdf6e3; }
  th { text-align:left; padding:10px 14px; font-size:12.5px; font-weight:700; color:#6b6052; letter-spacing:0.04em; text-transform:uppercase; border-bottom:1px solid #ecdfc4; }
  td { padding:11px 14px; font-size:15px; border-bottom:1px solid #f3ecd9; vertical-align:middle; }
  tr:last-child td { border-bottom:0; }
  td strong { color:#1a1814; font-size:17px; font-weight:800; }

  /* Inline code chips (used for 变化趋势 label) */
  code { background:#f3ecd9; color:#5a4a2e; padding:3px 9px; border-radius:6px; font-family:ui-monospace, "SF Mono", Menlo, monospace; font-size:13px; font-weight:600; letter-spacing:0.02em; }

  /* Images = charts */
  img { max-width:100%; height:auto; border-radius:10px; margin:18px 0; box-shadow:0 2px 8px rgba(45,30,10,0.08); border:1px solid #f3ecd9; }

  /* Horizontal rules: barely there */
  hr { border:0; border-top:1px dashed #e0d7c5; margin:28px 0; }

  /* Footer */
  body > p:last-child em,
  body > p:last-of-type em { color:#9b8f7d; }
</style>
""".strip()


_BRAND_STRIP = '<div class="brand">DAYTRACE · 个人工作复盘</div>'


def _md_to_html(md_text: str, *, chart_names: list[str] | None = None) -> str:
    """Render report Markdown to a Gmail-friendly HTML body. Local image
    references like `![alt](./hist.png)` get rewritten to `<img src="cid:hist.png">`
    so they pair up with EmailMessage.add_related() entries."""
    try:
        import markdown as _md
    except ImportError:
        return f"<!doctype html><html><body><pre>{md_text}</pre></body></html>"
    body = _md.markdown(md_text, extensions=["extra", "sane_lists"])
    # Rewrite ./<name>.png references → cid:<name>.png (one for each chart)
    for name in (chart_names or []):
        body = body.replace(f'src="./{name}"', f'src="cid:{name}"')
        body = body.replace(f"src='./{name}'", f'src="cid:{name}"')
    return f"<!doctype html><html><head><meta charset='utf-8'>{_EMAIL_CSS}</head><body>{body}</body></html>"


def _links_block_html(links: dict | None) -> str:
    if not links:
        return ""
    parts = ['<div class="links">']
    if links.get("dashboard"):
        parts.append(f'<a href="{links["dashboard"]}"><span class="lbl">🖥 完整 Dashboard</span>{links["dashboard"]}</a>')
    if links.get("docx"):
        parts.append(f'<a href="{links["docx"]}"><span class="lbl">📄 飞书文档</span>{links["docx"]}</a>')
    parts.append('</div>')
    return "".join(parts)


def _links_block_md(links: dict | None) -> str:
    """Plain-text fallback version for the multipart/alternative text part."""
    if not links:
        return ""
    lines: list[str] = ["快速访问:"]
    if links.get("dashboard"):
        lines.append(f"  • 完整 Dashboard: {links['dashboard']}")
    if links.get("docx"):
        lines.append(f"  • 飞书文档: {links['docx']}")
    lines.append("")
    return "\n".join(lines)


def email_report(*, kind: str, key: str, md_text: str,
                 links: dict | None = None,
                 chart_paths: list[Path] | None = None,
                 quiet: bool = False) -> None:
    """Send a multipart/alternative email:
      • text/plain part = MD text + plain links list
      • text/html  part = rendered MD with styled link box at top +
                          inline PNG charts (cid: references)

    `chart_paths` are local PNG files referenced in the MD as
    `![name](./<filename>)`; each is attached as a related (inline) part
    so Gmail/Outlook render them inside the body, not as attachments."""
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

    # Build both parts. Prepend the links block to the MD before rendering
    # so both plain and HTML versions carry them at the top.
    chart_names = [p.name for p in (chart_paths or []) if p.exists()]
    md_with_links = _links_block_md(links) + md_text
    html_body = _md_to_html(md_text, chart_names=chart_names)
    # Inject brand strip + styled links box right after <body>
    inject = _BRAND_STRIP
    if links:
        inject += _links_block_html(links)
    html_body = html_body.replace("<body>", "<body>" + inject, 1)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.set_content(md_with_links)
    msg.add_alternative(html_body, subtype="html")

    # Inline-attach each chart PNG. add_related() goes on the HTML part
    # specifically so the cid: references resolve.
    if chart_paths:
        html_part = msg.get_payload()[-1]  # the html alternative we just added
        for p in chart_paths:
            if not p.exists():
                continue
            html_part.add_related(
                p.read_bytes(),
                maintype="image", subtype="png",
                cid=f"<{p.name}>",  # angle-bracket cid; Gmail/Outlook need it
                filename=p.name,
            )

    if not quiet:
        print(f"[email] sending to {to}: {subject}")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(user, pwd)
        smtp.send_message(msg)
    if not quiet:
        n_charts = sum(1 for p in (chart_paths or []) if p.exists())
        print(f"  ✓ sent (md {len(md_with_links)/1024:.1f}KB + html {len(html_body)/1024:.1f}KB"
              + (f" + {n_charts} inline charts" if n_charts else "")
              + ")")
