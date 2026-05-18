"""Feishu Bitable "任务" sync + event ↔ work_item linkage.

Boundary contract:
- DayTrace is a read-only observer of the Feishu table. Each sync replaces
  the local `work_items` snapshot.
- We never write back to Feishu in this version (Phase 5 may add a new
  column owned by DayTrace; until then, strictly read-only).
- If lark-cli is unavailable or the call fails, this module raises but
  the daily catchup pipeline catches and continues — DayTrace stays
  fully functional with stale or empty work_items.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "config" / "work_items.yaml"
DEFAULT_ALIASES = REPO_ROOT / "config" / "work_item_aliases.yaml"


# ───────────────────────── config loading ─────────────────────────

def load_config(path: str | Path = DEFAULT_CONFIG) -> dict | None:
    """Returns the work_items config dict, or None if the feature is
    disabled / config missing. Schema:
        enabled: bool
        app_token: str
        table_id: str
        as: "user" | "bot"   (default "user")
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    if not raw.get("enabled"):
        return None
    if not raw.get("app_token") or not raw.get("table_id"):
        return None
    raw.setdefault("as", "user")
    return raw


def load_aliases(path: str | Path = DEFAULT_ALIASES) -> dict[str, str]:
    """Maps event project_guess (or other free-text keys) → Feishu record_id.
    User-curated yaml; missing/empty file = no aliases."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    aliases = raw.get("aliases") or {}
    return {str(k): str(v) for k, v in aliases.items() if k and v}


# ───────────────────────── markdown parser ─────────────────────────

_MD_LINK_RE = re.compile(r"\[(?P<label>[^\]]*)\]\((?P<url>[^)]+)\)")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _parse_md_value(raw: str) -> Any:
    """Unwrap a single markdown-table cell into a Python value.

    Handles:
      - empty → ""
      - JSON arrays like ["P0"] or ["paper","writing"] → list[str]
      - markdown links [text](url) → str url (or list[str] of urls when multiple)
      - <br> sequences → \\n
      - everything else → stripped string
    """
    s = (raw or "").strip()
    if not s:
        return ""
    # Try JSON array first (no nested markdown inside select fields)
    if s.startswith("[") and s.endswith("]") and not s.startswith("[!"):
        try:
            v = json.loads(s)
            if isinstance(v, list):
                return v
        except Exception:
            pass
    # Extract markdown links (handle multi-link cells)
    links = _MD_LINK_RE.findall(s)
    if links:
        urls = [u for _, u in links if u]
        if urls:
            return urls if len(urls) > 1 else urls[0]
    # Plain text — restore line breaks
    return _BR_RE.sub("\n", s).strip()


def _parse_md_table(text: str) -> list[dict[str, Any]]:
    """Parse a Feishu lark-cli `base +record-list` markdown response into
    list-of-dicts keyed by column header (first row of the table)."""
    rows_md = [ln for ln in text.splitlines() if ln.startswith("|")]
    if len(rows_md) < 3:
        return []
    header_row = rows_md[0]
    # Strip leading + trailing `|`, then split. .split("|") on the
    # internal markdown is robust because cell content can't contain `|`
    # in practice (Feishu escapes).
    header_cells = [c.strip() for c in header_row.strip().strip("|").split("|")]
    out: list[dict[str, Any]] = []
    for line in rows_md[2:]:  # skip separator line
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != len(header_cells):
            continue  # malformed; skip
        row: dict[str, Any] = {}
        for h, c in zip(header_cells, cells):
            row[h] = _parse_md_value(c)
        out.append(row)
    return out


# ───────────────────────── lark-cli runner ─────────────────────────

def _run_lark(args: list[str], *, timeout: float = 60.0) -> str:
    """Invoke lark-cli, return stdout. Raises RuntimeError on failure."""
    cmd = ["lark-cli", *args]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "lark-cli not on PATH. Install with: npm i -g @larksuiteoapi/cli"
        )
    if proc.returncode != 0:
        raise RuntimeError(
            f"lark-cli failed (rc={proc.returncode}): {proc.stderr[:500]}"
        )
    return proc.stdout


# ───────────────────────── sync ─────────────────────────

def _normalize_select(v: Any) -> str | None:
    """Feishu select fields come as JSON arrays of strings (single-select
    still wrapped in []). Take the first value as the canonical string."""
    if isinstance(v, list):
        return str(v[0]) if v else None
    if isinstance(v, str) and v:
        return v
    return None


def _normalize_links(*values: Any) -> list[str]:
    """Take 1-3 'external link' cells and flatten into a deduped URL list."""
    out: list[str] = []
    for v in values:
        if not v:
            continue
        if isinstance(v, list):
            out.extend(str(u) for u in v if u)
        else:
            out.append(str(v))
    # Dedupe preserving order
    seen: set[str] = set()
    dedup: list[str] = []
    for u in out:
        if u and u not in seen:
            seen.add(u)
            dedup.append(u)
    return dedup


def sync_from_feishu(
    con: sqlite3.Connection,
    *,
    app_token: str,
    table_id: str,
    as_identity: str = "user",
    page_limit: int = 200,
) -> dict[str, int]:
    """Pull the whole Feishu table and replace the local work_items snapshot.

    Returns {"fetched": N, "upserted": M}. Old records that disappeared
    upstream are NOT auto-deleted in v11 — they get stale_marked instead
    (no-op for now since we don't track that yet)."""
    md = _run_lark([
        "base", "+record-list",
        "--as", as_identity,
        "--base-token", app_token,
        "--table-id", table_id,
        "--limit", str(page_limit),
    ])
    rows = _parse_md_table(md)
    upserted = 0
    for r in rows:
        rid = r.get("_record_id") or ""
        if not isinstance(rid, str) or not rid:
            continue
        title = r.get("任务") or ""
        if not isinstance(title, str):
            title = str(title)
        status   = _normalize_select(r.get("状态"))
        priority = _normalize_select(r.get("重要程度"))
        tags_v   = r.get("标签")
        tags_json = json.dumps(tags_v, ensure_ascii=False) if isinstance(tags_v, list) else None
        project_source = (
            r.get("项目来源")
            if isinstance(r.get("项目来源"), str)
            else (json.dumps(r.get("项目来源"), ensure_ascii=False) if r.get("项目来源") else None)
        )
        links = _normalize_links(r.get("外部链接 1"), r.get("外部链接 2"), r.get("外部链接 3"))
        links_json = json.dumps(links, ensure_ascii=False) if links else None
        due_date = r.get("截止时间") if isinstance(r.get("截止时间"), str) else None
        if due_date:
            due_date = due_date.split(" ", 1)[0]  # keep YYYY-MM-DD
        next_action_date = r.get("下一步时间") if isinstance(r.get("下一步时间"), str) else None
        if next_action_date:
            next_action_date = next_action_date.split(" ", 1)[0]
        weekly_hours_v = r.get("每周预计投入")
        try:
            weekly_hours = float(weekly_hours_v) if weekly_hours_v not in (None, "") else None
        except (TypeError, ValueError):
            weekly_hours = None
        next_action = r.get("下一步动作") if isinstance(r.get("下一步动作"), str) else None
        agent_workspace = r.get("Agent 工作区") if isinstance(r.get("Agent 工作区"), str) else None
        raw_json = json.dumps(r, ensure_ascii=False, default=str)
        con.execute(
            """
            INSERT INTO work_items (
                record_id, title, status, priority, tags, project_source,
                external_links, due_date, next_action_date, weekly_hours,
                next_action, agent_workspace, last_synced_at, raw_fields_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(record_id) DO UPDATE SET
                title=excluded.title,
                status=excluded.status,
                priority=excluded.priority,
                tags=excluded.tags,
                project_source=excluded.project_source,
                external_links=excluded.external_links,
                due_date=excluded.due_date,
                next_action_date=excluded.next_action_date,
                weekly_hours=excluded.weekly_hours,
                next_action=excluded.next_action,
                agent_workspace=excluded.agent_workspace,
                last_synced_at=CURRENT_TIMESTAMP,
                raw_fields_json=excluded.raw_fields_json
            """,
            (rid, title, status, priority, tags_json, project_source,
             links_json, due_date, next_action_date, weekly_hours,
             next_action, agent_workspace, raw_json),
        )
        upserted += 1
    con.commit()
    return {"fetched": len(rows), "upserted": upserted}


# ───────────────────────── linker ─────────────────────────

_GITHUB_RE = re.compile(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?(?:[/?#]|$)", re.IGNORECASE)
_OVERLEAF_RE = re.compile(r"overleaf\.com/project/([\w-]+)", re.IGNORECASE)


def _canon_url(u: str) -> str | None:
    """Extract a canonical key from a URL — github: `owner/repo`, overleaf:
    project_id. Lets us match across .git suffix, https vs git@, etc."""
    if not u:
        return None
    m = _GITHUB_RE.search(u)
    if m:
        return f"github:{m.group(1).lower()}/{m.group(2).lower()}"
    m = _OVERLEAF_RE.search(u)
    if m:
        return f"overleaf:{m.group(1)}"
    return None


# Common parent dirs that contain repo siblings (cross-platform — Mac & WSL).
_REPO_PARENT_HINTS = {"projects", "research-programs", "code", "repos", "src", "work"}


def _canon_localpath(p: str) -> str | None:
    """For a local filesystem path, guess the repo basename.

    Heuristic: look for a `Projects/` / `research-programs/` etc segment
    and take the next dir; otherwise fall back to the leaf directory
    (or parent if the path looks like a file). Returns `localrepo:<name>`.
    """
    if not p:
        return None
    parts = [s for s in p.replace("\\", "/").split("/") if s]
    for i, seg in enumerate(parts):
        if seg.lower() in _REPO_PARENT_HINTS and i + 1 < len(parts):
            return f"localrepo:{parts[i+1].lower()}"
    # Fallback: if path ends with a filename (has `.`) drop the last segment
    if parts and "." in parts[-1] and "/" not in parts[-1]:
        parts = parts[:-1]
    if parts:
        return f"localrepo:{parts[-1].lower()}"
    return None


def _work_item_canon_keys(con: sqlite3.Connection) -> dict[str, str]:
    """Map every canon key (github URL OR github-derived repo basename OR
    overleaf id) → record_id. The repo-basename aliases let us match git /
    codex / claude_code events whose evidence carries a local path but no
    actual remote URL string."""
    out: dict[str, str] = {}
    for r in con.execute(
        "SELECT record_id, external_links FROM work_items ORDER BY record_id"
    ).fetchall():
        if not r["external_links"]:
            continue
        try:
            urls = json.loads(r["external_links"])
        except Exception:
            continue
        for u in urls:
            key = _canon_url(u)
            if not key:
                continue
            if key not in out:
                out[key] = r["record_id"]
            # Cross-bridge: github URL → also accept "localrepo:<repo>" as
            # a hit. So /Projects/baidu-signal-paper matches the github
            # URL github.com/xingminw/baidu-signal-paper.
            if key.startswith("github:"):
                repo = key.split("/", 1)[1]
                local_key = f"localrepo:{repo}"
                if local_key not in out:
                    out[local_key] = r["record_id"]
    return out


def rebuild_links(
    con: sqlite3.Connection,
    *,
    aliases_path: str | Path = DEFAULT_ALIASES,
    lookback_days: int = 30,
) -> dict[str, int]:
    """Recompute event ↔ work_item links from scratch (deterministic only —
    URL and alias). AI inference would be a separate optional pass.

    Strategy:
      1. github_url match: event's evidence.repo_url or evidence.path
         canonicalized → look up in work_item canon-urls
      2. alias match: aliases.yaml maps event.project_guess → record_id
      3. (future) keyword / AI

    Returns {"events_scanned": N, "links_inserted": M, "by_type": {...}}."""
    canon = _work_item_canon_keys(con)
    aliases = load_aliases(aliases_path)

    # Clear previous deterministic links (keep manual / ai if added later)
    con.execute(
        "DELETE FROM event_work_item_links WHERE match_type IN ('github_url','local_path','alias')"
    )

    # Scan recent events (full history is fine but the working set is
    # always recent days).
    rows = con.execute(
        f"""
        SELECT id, project_guess, evidence_json
          FROM events
         WHERE date >= date('now', '-{int(lookback_days)} days')
        """
    ).fetchall()

    inserted = 0
    by_type: dict[str, int] = {"github_url": 0, "local_path": 0, "alias": 0}
    for row in rows:
        ev_id = row["id"]
        chosen_record: str | None = None
        chosen_match: str | None = None

        # 1) URL match via evidence's URL-ish OR path-ish fields. Real evidence
        # field names in our DB: repo (git collector), cwd (codex/claude_code),
        # path (hermes session file), rollout_path (codex). We try each path
        # both as a literal URL (in case it contains github.com) AND as a
        # local path that we map to a repo basename.
        ev_json = row["evidence_json"] or "{}"
        try:
            ev = json.loads(ev_json)
        except Exception:
            ev = {}
        candidates: list[str] = []
        for k in ("repo", "cwd", "repo_url", "remote_url", "url",
                  "path", "rollout_path", "session_path"):
            v = ev.get(k)
            if isinstance(v, str) and v:
                candidates.append(v)
        for c in candidates:
            # Try URL form first (catches github.com / overleaf.com strings)
            key = _canon_url(c)
            if key and key in canon:
                chosen_record = canon[key]
                chosen_match = "github_url" if key.startswith("github:") else "local_path"
                break
            # Then try local-path form (looks for Projects/<repo>/...)
            key = _canon_localpath(c)
            if key and key in canon:
                chosen_record = canon[key]
                chosen_match = "local_path"
                break

        # 2) alias table — event.project_guess → record_id
        if not chosen_record and row["project_guess"]:
            rec = aliases.get(str(row["project_guess"]))
            if rec:
                chosen_record = rec
                chosen_match = "alias"

        if chosen_record:
            con.execute(
                """
                INSERT OR REPLACE INTO event_work_item_links
                    (event_id, record_id, match_type, confidence, matched_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (ev_id, chosen_record, chosen_match,
                 1.0 if chosen_match in ("github_url", "alias") else 0.9),
            )
            inserted += 1
            by_type[chosen_match] = by_type.get(chosen_match, 0) + 1

    con.commit()
    return {"events_scanned": len(rows), "links_inserted": inserted, "by_type": by_type}


# ───────────────────────── helpers for consumers ─────────────────────────

def load_links_for_event_ids(
    con: sqlite3.Connection, event_ids: list[str], *, chunk: int = 900,
) -> dict[str, tuple[str, str]]:
    """{event_id: (record_id, match_type)} for the given event ids."""
    if not event_ids:
        return {}
    out: dict[str, tuple[str, str]] = {}
    unique = list({e for e in event_ids if e})
    for i in range(0, len(unique), chunk):
        sub = unique[i:i+chunk]
        ph = ",".join("?" * len(sub))
        for r in con.execute(
            f"SELECT event_id, record_id, match_type FROM event_work_item_links "
            f"WHERE event_id IN ({ph})", sub
        ).fetchall():
            out[r["event_id"]] = (r["record_id"], r["match_type"])
    return out


def list_work_items(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all work_items as dicts, ordered by status (active first) +
    priority + due_date."""
    rows = con.execute(
        """
        SELECT * FROM work_items
        ORDER BY
            CASE status WHEN '进行中' THEN 0 WHEN '待办' THEN 1 WHEN '完成' THEN 2 ELSE 3 END,
            CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 WHEN 'P3' THEN 3 ELSE 4 END,
            COALESCE(due_date, '9999-12-31') ASC,
            title
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        # Decode JSON sidecars
        for k in ("tags", "external_links"):
            if d.get(k):
                try: d[k] = json.loads(d[k])
                except Exception: pass
        out.append(d)
    return out
