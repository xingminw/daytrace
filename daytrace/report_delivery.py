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


_CHART_ANCHORS = {
    "hist":  "任务时间分布(按时段)",
    "donut": "任务总览",
}


def import_md_to_feishu_docs(md_path: Path, *,
                             kind: str, key: str,
                             chart_paths: list[Path] | None = None,
                             quiet: bool = False) -> dict:
    """Import the Markdown summary as a Feishu *cloud docx* document.

    Images can't be inlined via `drive +import` because that endpoint
    embeds them with temporary signed stream URLs that **expire after
    ~1 hour** — opening the doc later shows ‘无法导入该图片’. So:

      1) Strip `![]()` references from the MD before importing (the
         chart sub-headings stay in place as anchors).
      2) Import the stripped MD → docx.
      3) For each chart PNG, run `docs +media-insert` with
         `--selection-with-ellipsis=<chart sub-heading>`. That endpoint
         uploads via Feishu's media API and embeds a persistent
         image_token, so the picture survives indefinitely.

    Returns {"docx": url} (or {} on failure)."""
    cfg = _ensure_folders()
    folder_token = cfg["daily_token"] if kind == "daily" else cfg["weekly_token"]
    if not md_path.exists():
        return {}

    # ── Step 1: strip image refs from MD into a sibling temp file ──
    import re as _re
    upload_path = md_path
    stripped_path: Path | None = None
    if chart_paths:
        original = md_path.read_text(encoding="utf-8")
        stripped = _re.sub(r"^!\[.*?\]\(\./[^)]+\)\s*\n?", "", original, flags=_re.M)
        stripped_path = md_path.parent / f".{md_path.stem}.feishu-stripped.md"
        stripped_path.write_text(stripped, encoding="utf-8")
        upload_path = stripped_path

    # ── Step 2: import the MD as docx ──
    try:
        resp = _import_one(upload_path, folder_token, doc_type="docx", name=key)
        data = resp.get("data") or resp
        url = data.get("url") or data.get("file_url")
        if not url and data.get("token"):
            url = f"https://www.feishu.cn/docx/{data['token']}"
    finally:
        if stripped_path and stripped_path.exists():
            try: stripped_path.unlink()
            except Exception: pass

    if not url:
        if not quiet:
            print("  ! docx import: no URL returned")
        return {}
    if not quiet:
        print(f"  ↑ docx  → {url}")

    # ── Step 3: insert each chart via media-insert (persistent token) ──
    if chart_paths:
        for cp in chart_paths:
            if not cp.exists():
                continue
            # Filename is "<key>-<chart_key>.png"; pull chart_key for the anchor
            chart_key = cp.stem.rsplit("-", 1)[-1]
            anchor = _CHART_ANCHORS.get(chart_key)
            if not anchor:
                continue
            try:
                _lark([
                    "docs", "+media-insert",
                    "--doc", url,
                    "--file", "./" + cp.name,
                    "--type", "image",
                    "--selection-with-ellipsis", anchor,
                ], cwd=cp.parent)
                if not quiet:
                    print(f"  ↑ chart {cp.name} → inserted after '{anchor}'")
            except Exception as e:
                print(f"  ! chart insert failed for {cp.name}: {e}")

    return {"docx": url}


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
  /* ───── Unified type scale ─────
       body   16 / 1.65   primary read
       li     16 / 1.65   same as body (no shrunk lists)
       bq     16 / 1.65   same as body, only color/border distinguish
       h3     17 bold     section break — only 1 step above body
       h2     21 bold     section heading
       h1     26 bold     title
       small  13 muted    captions only
     All margins on the same 12px grid; no random 6/7/9/14 hops. */

  body {
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", "PingFang SC", "Microsoft YaHei", sans-serif;
    color:#2b2722; font-size:16px; line-height:1.65; max-width:780px;
    margin:0 auto; padding:28px 28px 36px;
    background:#fdfaf2;
  }

  /* Brand strip */
  .brand { font-size:11px; font-weight:700; letter-spacing:0.22em; color:#9b8f7d; text-transform:uppercase; margin:0 0 12px; padding-bottom:8px; border-bottom:2px solid #f0d68b; }

  /* Links card */
  .links { background:#fff7e8; border:1px solid #f0d68b; border-radius:10px; padding:14px 18px; margin:0 0 24px; }
  .links a { display:block; margin:4px 0; color:#2f6fed; text-decoration:none; font-weight:600; font-size:15px; line-height:1.5; }
  .links a:hover { text-decoration:underline; }
  .links .lbl { display:inline-block; min-width:118px; color:#6b6052; font-weight:500; margin-right:8px; font-size:13px; }

  /* Headings — same family, only size + weight vary */
  h1 { font-size:26px; font-weight:700; color:#1a1814; line-height:1.3;  margin:0 0 12px; }
  h2 { font-size:21px; font-weight:700; color:#1a1814; line-height:1.35; margin:24px 0 12px; padding-bottom:8px; border-bottom:2px solid #f0d68b; }
  h3 { font-size:17px; font-weight:700; color:#3b352e; line-height:1.4;  margin:18px 0 8px; }

  /* Paragraphs */
  p  { margin:0 0 12px; }
  strong { color:#1a1814; font-weight:700; }
  em { color:#7a6f5f; font-style:normal; font-size:13px; }

  /* Blockquote — narrative emphasis (border + bg, NOT a different font size) */
  blockquote { margin:0 0 12px; padding:10px 16px; background:#fff7e8; border-left:3px solid #f59e0b; border-radius:0 6px 6px 0; color:#3b352e; }
  blockquote p { margin:4px 0; }

  /* Lists — body-size text */
  ul { margin:0 0 12px; padding-left:24px; }
  li { margin:4px 0; }

  /* Table */
  table { width:100%; border-collapse:separate; border-spacing:0; margin:0 0 16px; background:white; border:1px solid #ecdfc4; border-radius:8px; overflow:hidden; }
  thead { background:#fdf6e3; }
  th { text-align:left; padding:10px 14px; font-size:13px; font-weight:700; color:#6b6052; letter-spacing:0.04em; text-transform:uppercase; border-bottom:1px solid #ecdfc4; }
  td { padding:10px 14px; font-size:16px; border-bottom:1px solid #f3ecd9; vertical-align:middle; }
  tr:last-child td { border-bottom:0; }
  td strong { font-size:16px; font-weight:700; }

  /* Inline code */
  code { background:#f3ecd9; color:#5a4a2e; padding:2px 7px; border-radius:5px; font-family:ui-monospace, "SF Mono", Menlo, monospace; font-size:14px; font-weight:600; }

  /* Images */
  img { max-width:100%; height:auto; border-radius:8px; margin:8px 0 16px; border:1px solid #f3ecd9; }

  /* Dividers */
  hr { border:0; border-top:1px dashed #e0d7c5; margin:24px 0; }
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
