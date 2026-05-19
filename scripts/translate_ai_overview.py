#!/usr/bin/env python3
"""One-shot backfill: walk day_channel/ai_overview rows and either
(a) upgrade legacy single-language payloads to the bilingual v14+ shape
    by translating the zh side into en, or
(b) fix v14+ rows whose en bullets/narrative still contain CJK characters
    (the AI sometimes embedded zh task names verbatim).

Doesn't touch rows that are already fully bilingual and CJK-free.

Run:
    python scripts/translate_ai_overview.py
    python scripts/translate_ai_overview.py --date 2026-05-11
    python scripts/translate_ai_overview.py --dry-run

Requires DEEPSEEK_API_KEY (loaded from ~/.daytrace/secrets.env)."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Make repo modules importable
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from daytrace import ai_client
from daytrace.db import connect, init_db  # noqa: E402

DEFAULT_DB_PATH = REPO_ROOT / "data" / "daytrace.sqlite"


CJK_RE = re.compile(r"[一-鿿㐀-䶿]")


def has_cjk(s) -> bool:
    if not isinstance(s, str):
        return False
    return bool(CJK_RE.search(s))


def needs_translation(payload: dict) -> bool:
    """Return True if any en field is missing or contains CJK."""
    def _check_field(v) -> bool:
        if isinstance(v, str):
            # legacy single-language: definitely needs upgrade
            return True
        if isinstance(v, dict):
            en = v.get("en") or ""
            if not en.strip():
                return True
            if has_cjk(en):
                return True
        return False

    for key in ("headline",):
        if _check_field(payload.get(key)):
            return True
    ov = payload.get("overview")
    if isinstance(ov, dict):
        if _check_field(ov.get("narrative")):
            return True
    elif payload.get("narrative") is not None:
        if _check_field(payload.get("narrative")):
            return True
    trend = payload.get("trend")
    if isinstance(trend, dict) and _check_field(trend.get("comparison")):
        return True
    for key in ("highlights", "work_pattern", "suggestions",
                "concerns", "recommendations"):
        items = payload.get(key)
        if not isinstance(items, list):
            continue
        for it in items:
            if _check_field(it):
                return True
    return False


TRANSLATE_SYSTEM = (
    "You translate Chinese workday-recap text into natural English. "
    "Requirements: "
    "(1) en output is PURE English — zero Chinese characters, no Pinyin, "
    "translate every term including Feishu task names "
    "(use English task names from the reference list when given, otherwise "
    "render a short faithful English equivalent — e.g. '论文 review' → "
    "'paper review'); "
    "(2) preserve tool/library names verbatim (Codex, Claude Code, git, "
    "DayTrace, DeepSeek, Tailscale, Feishu, Hermes, etc.); "
    "(3) preserve numbers and timestamps; "
    "(4) keep the tone informal, like recounting your day to a peer — "
    "do NOT produce status-report prose; "
    "(5) keep length proportional to the zh version. "
    "Output strictly valid JSON matching the input shape."
)


def _translate_payload(zh_payload: dict, task_map: dict[str, str]) -> dict:
    """Send the entire payload's zh fields to DeepSeek, get an en mirror."""
    # Build a flat shape so the model has to fill in each en field.
    user_blocks: list[str] = []
    if task_map:
        user_blocks.append(
            "Reference — Feishu task names (中文 → English). USE the English "
            "version verbatim when these tasks appear:\n"
            + "\n".join(f"  {zh} → {en}" for zh, en in task_map.items())
        )

    user_blocks.append(
        "Translate the following zh content into en, returning a JSON object "
        "with the same key shape. Each input value is the zh source; produce "
        "the en counterpart. Keep arrays the same length."
    )
    # Surface only the zh values to keep the prompt focused.
    zh_only = _strip_to_zh(zh_payload)
    user_blocks.append("Input (zh):\n" + json.dumps(zh_only, ensure_ascii=False, indent=2))
    user_blocks.append("Output (en):\n(start the response with {)")
    user_prompt = "\n\n".join(user_blocks)

    resp = ai_client.call_json(
        system=TRANSLATE_SYSTEM,
        user=user_prompt,
        temperature=0.3,
        max_tokens=4000,
    )
    return resp.json


def _strip_to_zh(payload: dict) -> dict:
    """Return a dict with only zh values, mirroring the bilingual shape."""
    def pick_zh(v):
        if isinstance(v, dict) and ("zh" in v or "en" in v):
            return v.get("zh") or ""
        if isinstance(v, str):
            return v
        return None

    out: dict = {}
    if "headline" in payload:
        out["headline"] = pick_zh(payload["headline"])
    if isinstance(payload.get("overview"), dict):
        out["narrative"] = pick_zh(payload["overview"].get("narrative"))
    elif "narrative" in payload:
        out["narrative"] = pick_zh(payload["narrative"])
    if isinstance(payload.get("trend"), dict):
        out["trend_comparison"] = pick_zh(payload["trend"].get("comparison"))
    for key in ("highlights", "work_pattern", "suggestions",
                "concerns", "recommendations"):
        items = payload.get(key)
        if isinstance(items, list):
            out[key] = [pick_zh(it) for it in items]
    return out


def _merge_en_into_payload(payload: dict, en: dict) -> dict:
    """Merge translated en values back into the bilingual payload shape."""
    def bilingual(zh_val, en_val):
        if isinstance(zh_val, dict) and ("zh" in zh_val or "en" in zh_val):
            zh_text = zh_val.get("zh") or ""
        else:
            zh_text = zh_val if isinstance(zh_val, str) else ""
        return {"zh": zh_text, "en": en_val or ""}

    new = dict(payload)
    if "headline" in payload and "headline" in en:
        new["headline"] = bilingual(payload["headline"], en["headline"])

    # narrative may live under overview.narrative OR payload.narrative
    if "narrative" in en:
        if isinstance(new.get("overview"), dict):
            new["overview"] = dict(new["overview"])
            zv = new["overview"].get("narrative")
            new["overview"]["narrative"] = bilingual(zv, en["narrative"])
        else:
            new["overview"] = {
                "narrative": bilingual(payload.get("narrative"), en["narrative"]),
            }

    if "trend_comparison" in en and isinstance(new.get("trend"), dict):
        new["trend"] = dict(new["trend"])
        zv = new["trend"].get("comparison")
        new["trend"]["comparison"] = bilingual(zv, en["trend_comparison"])

    for key in ("highlights", "work_pattern", "suggestions",
                "concerns", "recommendations"):
        if key not in en:
            continue
        zh_items = payload.get(key)
        if not isinstance(zh_items, list):
            continue
        en_items = en[key]
        if not isinstance(en_items, list):
            continue
        merged = []
        for i, zh_it in enumerate(zh_items):
            en_text = en_items[i] if i < len(en_items) else ""
            merged.append(bilingual(zh_it, en_text))
        new[key] = merged
    return new


def _load_task_map(con) -> dict[str, str]:
    """work_items.title → work_items.title_en map (only when both present)."""
    out: dict[str, str] = {}
    try:
        for r in con.execute(
            "SELECT title, title_en FROM work_items WHERE title IS NOT NULL "
            "AND title_en IS NOT NULL AND title_en != ''"
        ).fetchall():
            zh = (r["title"] or "").strip()
            en = (r["title_en"] or "").strip()
            if zh and en and zh != en:
                out[zh] = en
    except Exception:
        pass
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--date", help="Only process this YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change, don't write")
    parser.add_argument("--limit", type=int, default=0,
                        help="Stop after N translations (debug)")
    args = parser.parse_args()

    if not ai_client.is_available():
        print("DEEPSEEK_API_KEY not set. Export it or add to ~/.daytrace/secrets.env",
              file=sys.stderr)
        return 1

    con = connect(Path(args.db))
    init_db(con)
    task_map = _load_task_map(con)
    print(f"loaded {len(task_map)} zh→en task name mappings")

    if args.date:
        sql = ("SELECT date, value_json, generator_version FROM day_channel "
               "WHERE channel='ai_overview' AND date=? ORDER BY date")
        rows = con.execute(sql, (args.date,)).fetchall()
    else:
        sql = ("SELECT date, value_json, generator_version FROM day_channel "
               "WHERE channel='ai_overview' ORDER BY date")
        rows = con.execute(sql).fetchall()

    print(f"scanning {len(rows)} ai_overview rows...")
    translated = skipped = failed = 0
    for r in rows:
        date = r["date"]
        raw = r["value_json"]
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            print(f"  {date}: bad JSON, skip")
            continue
        if not needs_translation(payload):
            skipped += 1
            continue
        print(f"  {date} ({r['generator_version']}): translating...", flush=True)
        if args.dry_run:
            translated += 1
            continue
        try:
            en = _translate_payload(payload, task_map)
            merged = _merge_en_into_payload(payload, en)
            new_json = json.dumps(merged, ensure_ascii=False)
            con.execute(
                "UPDATE day_channel SET value_json=?, generator_version=? "
                "WHERE date=? AND channel='ai_overview'",
                (new_json, "v16-backfill", date),
            )
            con.commit()
            translated += 1
            print(f"     -> ok (cost ~${en.get('_cost', 0):.4f})" if False else "     -> ok")
        except Exception as exc:
            failed += 1
            print(f"     -> FAILED: {exc}")
        if args.limit and translated >= args.limit:
            print(f"reached --limit {args.limit}, stopping")
            break

    print(f"done: translated={translated}, skipped={skipped}, failed={failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
