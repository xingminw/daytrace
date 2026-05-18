"""Feishu Bitable sync + event ↔ work_item linkage.

Multi-table aware: config/work_items.yaml lists 1..N Feishu Bitables,
each with its own field-name map. All rows land in a single SQLite
table (`work_items`) keyed by `table_key`. Cards / panels can filter
by table_key to keep the visual grouping clear.

Boundary contract:
- DayTrace is a read-only observer. Each sync upserts the local snapshot.
- We never write back to Feishu in this version.
- lark-cli missing or any single table failing should NOT block the
  daily catchup pipeline — failures are isolated per table.
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
    """Returns the work_items config dict, or None if feature is
    disabled / config missing. New schema (v12):
        enabled: bool
        tables:
          - key: str            # local id ("tasks" / "reviews" / …)
            name: str           # human label (任务 / 审稿)
            app_token: str
            table_id: str
            as: user | bot
            field_map:
              title: <feishu col>
              status: <feishu col>
              priority: <feishu col>   # optional
              subtitle: <feishu col>   # optional
              tags: <feishu col>       # optional
              due_date: <feishu col>   # optional
              external_links: [<feishu col>, ...]
              ...
    Old single-table schema (top-level app_token / table_id) is auto-upgraded
    to a one-entry tables list so existing configs keep working.
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

    # Auto-upgrade legacy single-table format
    tables = raw.get("tables")
    if not tables and raw.get("app_token") and raw.get("table_id"):
        tables = [{
            "key": "tasks",
            "name": raw.get("name") or "任务",
            "app_token": raw["app_token"],
            "table_id": raw["table_id"],
            "as": raw.get("as", "user"),
            "field_map": {
                "title": "任务",
                "status": "状态",
                "priority": "重要程度",
                "tags": "标签",
                "subtitle": "项目来源",
                "due_date": "截止时间",
                "next_action_date": "下一步时间",
                "weekly_hours": "每周预计投入",
                "next_action": "下一步动作",
                "agent_workspace": "Agent 工作区",
                "external_links": ["外部链接 1", "外部链接 2", "外部链接 3"],
            },
        }]
    if not tables:
        return None

    # Validate each table
    cleaned: list[dict] = []
    for t in tables:
        if not t.get("app_token") or not t.get("table_id"):
            continue
        t.setdefault("key", "tasks")
        t.setdefault("name", t["key"])
        t.setdefault("as", "user")
        t.setdefault("field_map", {})
        cleaned.append(t)
    if not cleaned:
        return None

    return {"enabled": True, "tables": cleaned}


def load_aliases(path: str | Path = DEFAULT_ALIASES) -> dict[str, str]:
    """Maps event project_guess (or other free-text keys) → Feishu record_id."""
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
_URL_RE = re.compile(r"https?://[^\s<>\"\]]+")


def _parse_md_value(raw: str) -> Any:
    """Unwrap a markdown-table cell into a Python value."""
    s = (raw or "").strip()
    if not s:
        return ""
    # JSON-encoded select / multi-select
    if s.startswith("[") and s.endswith("]") and not s.startswith("[!"):
        try:
            v = json.loads(s)
            if isinstance(v, list):
                return v
        except Exception:
            pass
    # Markdown links → URL (or list of URLs)
    links = _MD_LINK_RE.findall(s)
    if links:
        urls = [u for _, u in links if u]
        if urls:
            return urls if len(urls) > 1 else urls[0]
    # Plain text — restore line breaks
    return _BR_RE.sub("\n", s).strip()


def _parse_md_table(text: str) -> list[dict[str, Any]]:
    """Parse the markdown response from `lark-cli base +record-list`."""
    rows_md = [ln for ln in text.splitlines() if ln.startswith("|")]
    if len(rows_md) < 3:
        return []
    header_cells = [c.strip() for c in rows_md[0].strip().strip("|").split("|")]
    out: list[dict[str, Any]] = []
    for line in rows_md[2:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != len(header_cells):
            continue
        row: dict[str, Any] = {}
        for h, c in zip(header_cells, cells):
            row[h] = _parse_md_value(c)
        out.append(row)
    return out


def _extract_urls(value: Any) -> list[str]:
    """Pull URL strings out of a parsed cell value (already-parsed URL,
    list of URLs, or free text containing URLs inline)."""
    out: list[str] = []
    if not value:
        return out
    if isinstance(value, list):
        for v in value:
            out.extend(_extract_urls(v))
        return out
    if isinstance(value, str):
        # Already a URL?
        if value.startswith("http"):
            out.append(value)
            return out
        # Free text — find URLs inside
        for u in _URL_RE.findall(value):
            # Trim trailing punctuation a regex might miss
            out.append(u.rstrip(".,);"))
        return out
    return out


# ───────────────────────── lark-cli runner ─────────────────────────

def _run_lark(args: list[str], *, timeout: float = 60.0) -> str:
    cmd = ["lark-cli", *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise RuntimeError(
            "lark-cli not on PATH. Install with: npm i -g @larksuiteoapi/cli"
        )
    if proc.returncode != 0:
        raise RuntimeError(f"lark-cli failed (rc={proc.returncode}): {proc.stderr[:500]}")
    return proc.stdout


# ───────────────────────── sync ─────────────────────────

def _normalize_select(v: Any) -> str | None:
    if isinstance(v, list):
        return str(v[0]) if v else None
    if isinstance(v, str) and v:
        return v
    return None


def _get(row: dict, field_map: dict, key: str) -> Any:
    """Look up a logical key via field_map then return the row's value."""
    mapped = field_map.get(key)
    if mapped is None:
        return None
    if isinstance(mapped, list):
        # List of column names — return them as parallel values
        return [row.get(c) for c in mapped if c in row]
    return row.get(mapped)


def sync_table(
    con: sqlite3.Connection,
    table_cfg: dict,
) -> dict[str, int]:
    """Sync one configured Feishu table into work_items. Returns
    {"fetched": N, "upserted": M}."""
    field_map = table_cfg.get("field_map", {})
    table_key = table_cfg["key"]
    md = _run_lark([
        "base", "+record-list",
        "--as", table_cfg.get("as", "user"),
        "--base-token", table_cfg["app_token"],
        "--table-id", table_cfg["table_id"],
        "--limit", "200",
    ])
    rows = _parse_md_table(md)
    upserted = 0
    for r in rows:
        rid = r.get("_record_id") or ""
        if not isinstance(rid, str) or not rid:
            continue

        title_v = _get(r, field_map, "title")
        title = (title_v if isinstance(title_v, str) else "") or ""
        if not title:
            continue  # skip rows without a title

        subtitle_v = _get(r, field_map, "subtitle")
        subtitle = subtitle_v if isinstance(subtitle_v, str) else None

        status   = _normalize_select(_get(r, field_map, "status"))
        priority = _normalize_select(_get(r, field_map, "priority"))

        tags_v = _get(r, field_map, "tags")
        tags_json = json.dumps(tags_v, ensure_ascii=False) if isinstance(tags_v, list) else None

        # External links can come from multiple columns; some may be plain
        # text containing URLs (e.g. 备注 in 审稿). Use _extract_urls to
        # cover both markdown-link cells and inline-URL text cells.
        link_cells = field_map.get("external_links", [])
        if isinstance(link_cells, str):
            link_cells = [link_cells]
        urls: list[str] = []
        seen: set[str] = set()
        for col in link_cells:
            for u in _extract_urls(r.get(col)):
                if u and u not in seen:
                    seen.add(u)
                    urls.append(u)
        links_json = json.dumps(urls, ensure_ascii=False) if urls else None

        def _date_yyyy_mm_dd(v):
            if isinstance(v, str) and v:
                return v.split(" ", 1)[0]
            return None
        due_date = _date_yyyy_mm_dd(_get(r, field_map, "due_date"))
        next_action_date = _date_yyyy_mm_dd(_get(r, field_map, "next_action_date"))

        weekly_v = _get(r, field_map, "weekly_hours")
        try:
            weekly_hours = float(weekly_v) if weekly_v not in (None, "") else None
        except (TypeError, ValueError):
            weekly_hours = None

        next_action_v = _get(r, field_map, "next_action")
        next_action = next_action_v if isinstance(next_action_v, str) else None

        ws_v = _get(r, field_map, "agent_workspace")
        agent_workspace = ws_v if isinstance(ws_v, str) else None

        raw_json = json.dumps(r, ensure_ascii=False, default=str)
        con.execute(
            """
            INSERT INTO work_items (
                record_id, table_key, title, subtitle, status, priority,
                tags, project_source, external_links, due_date,
                next_action_date, weekly_hours, next_action,
                agent_workspace, last_synced_at, raw_fields_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(record_id) DO UPDATE SET
                table_key=excluded.table_key,
                title=excluded.title,
                subtitle=excluded.subtitle,
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
            (rid, table_key, title, subtitle, status, priority,
             tags_json, subtitle, links_json, due_date,
             next_action_date, weekly_hours, next_action,
             agent_workspace, raw_json),
        )
        upserted += 1
    con.commit()
    return {"fetched": len(rows), "upserted": upserted}


def sync_from_feishu(
    con: sqlite3.Connection,
    cfg: dict | None = None,
    *,
    # legacy single-table args (kept for backwards compat with phase-1 callers)
    app_token: str | None = None,
    table_id: str | None = None,
    as_identity: str = "user",
    page_limit: int = 200,
) -> dict[str, Any]:
    """Sync every configured table. Returns {"tables": [{"key", "fetched",
    "upserted", "error"?}, ...]}. Errors in one table don't block others."""
    if cfg is None and app_token and table_id:
        # legacy single-table mode
        cfg = {"tables": [{
            "key": "tasks", "name": "任务",
            "app_token": app_token, "table_id": table_id,
            "as": as_identity, "field_map": {
                "title": "任务", "status": "状态", "priority": "重要程度",
                "tags": "标签", "subtitle": "项目来源",
                "due_date": "截止时间", "next_action_date": "下一步时间",
                "weekly_hours": "每周预计投入", "next_action": "下一步动作",
                "agent_workspace": "Agent 工作区",
                "external_links": ["外部链接 1", "外部链接 2", "外部链接 3"],
            },
        }]}
    if not cfg or not cfg.get("tables"):
        return {"tables": []}

    results = []
    for tcfg in cfg["tables"]:
        entry = {"key": tcfg["key"], "name": tcfg.get("name", "")}
        try:
            stats = sync_table(con, tcfg)
            entry.update(stats)
        except Exception as e:
            entry["error"] = f"{type(e).__name__}: {e}"
            entry["fetched"] = 0
            entry["upserted"] = 0
        results.append(entry)
    return {"tables": results}


# ───────────────────────── linker ─────────────────────────

_GITHUB_RE = re.compile(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?(?:[/?#]|$)", re.IGNORECASE)
_OVERLEAF_RE = re.compile(r"overleaf\.com/project/([\w-]+)", re.IGNORECASE)


def _canon_url(u: str) -> str | None:
    if not u:
        return None
    m = _GITHUB_RE.search(u)
    if m:
        return f"github:{m.group(1).lower()}/{m.group(2).lower()}"
    m = _OVERLEAF_RE.search(u)
    if m:
        return f"overleaf:{m.group(1)}"
    return None


_REPO_PARENT_HINTS = {"projects", "research-programs", "code", "repos", "src", "work"}


def _canon_localpath(p: str) -> str | None:
    if not p:
        return None
    parts = [s for s in p.replace("\\", "/").split("/") if s]
    for i, seg in enumerate(parts):
        if seg.lower() in _REPO_PARENT_HINTS and i + 1 < len(parts):
            return f"localrepo:{parts[i+1].lower()}"
    if parts and "." in parts[-1] and "/" not in parts[-1]:
        parts = parts[:-1]
    if parts:
        return f"localrepo:{parts[-1].lower()}"
    return None


def _work_item_canon_keys(con: sqlite3.Connection) -> dict[str, str]:
    """canon_key → primary record_id. When multiple records share the same
    repo (e.g. all 审稿 rows point at the same paper-review repo), we keep
    the most recent active record so events link to a meaningful row."""
    candidates: dict[str, list[tuple[str, str, str]]] = {}
    rows = con.execute(
        "SELECT record_id, status, external_links, last_synced_at "
        "FROM work_items ORDER BY last_synced_at DESC"
    ).fetchall()
    for r in rows:
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
            candidates.setdefault(key, []).append(
                (r["record_id"], r["status"] or "", r["last_synced_at"] or "")
            )
            if key.startswith("github:"):
                repo = key.split("/", 1)[1]
                lkey = f"localrepo:{repo}"
                candidates.setdefault(lkey, []).append(
                    (r["record_id"], r["status"] or "", r["last_synced_at"] or "")
                )

    # Pick best record per key: prefer non-完成 status, then most recent
    def score(item):
        _, status, ts = item
        # Lower score = better
        status_rank = {"进行中": 0, "待办": 1}.get(status, 2 if status == "完成" else 3)
        return (status_rank, -len(ts))  # newer ts string sorts first lexically
    out: dict[str, str] = {}
    for key, lst in candidates.items():
        lst.sort(key=score)
        out[key] = lst[0][0]
    return out


def rebuild_links(
    con: sqlite3.Connection,
    *,
    aliases_path: str | Path = DEFAULT_ALIASES,
    lookback_days: int = 30,
) -> dict[str, int]:
    canon = _work_item_canon_keys(con)
    aliases = load_aliases(aliases_path)
    con.execute(
        "DELETE FROM event_work_item_links "
        "WHERE match_type IN ('github_url','local_path','alias')"
    )
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
        ev_json = row["evidence_json"] or "{}"
        try:
            ev = json.loads(ev_json)
        except Exception:
            ev = {}
        candidates_paths: list[str] = []
        for k in ("repo", "cwd", "repo_url", "remote_url", "url",
                  "path", "rollout_path", "session_path"):
            v = ev.get(k)
            if isinstance(v, str) and v:
                candidates_paths.append(v)
        for c in candidates_paths:
            key = _canon_url(c)
            if key and key in canon:
                chosen_record = canon[key]
                chosen_match = "github_url" if key.startswith("github:") else "local_path"
                break
            key = _canon_localpath(c)
            if key and key in canon:
                chosen_record = canon[key]
                chosen_match = "local_path"
                break
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


def list_work_items(con: sqlite3.Connection, *, table_key: str | None = None) -> list[dict[str, Any]]:
    """Return work_items as dicts. Filter by table_key when provided."""
    if table_key:
        rows = con.execute(
            """
            SELECT * FROM work_items WHERE table_key = ?
            ORDER BY
                CASE status WHEN '进行中' THEN 0 WHEN '待办' THEN 1 WHEN '完成' THEN 2 ELSE 3 END,
                CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 WHEN 'P3' THEN 3 ELSE 4 END,
                COALESCE(due_date, '9999-12-31') ASC,
                title
            """, (table_key,)
        ).fetchall()
    else:
        rows = con.execute(
            """
            SELECT * FROM work_items
            ORDER BY
                table_key,
                CASE status WHEN '进行中' THEN 0 WHEN '待办' THEN 1 WHEN '完成' THEN 2 ELSE 3 END,
                CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 WHEN 'P3' THEN 3 ELSE 4 END,
                COALESCE(due_date, '9999-12-31') ASC,
                title
            """
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        for k in ("tags", "external_links"):
            if d.get(k):
                try: d[k] = json.loads(d[k])
                except Exception: pass
        out.append(d)
    return out
