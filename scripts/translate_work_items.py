#!/usr/bin/env python3
"""Bulk-translate Feishu work-item titles to English via DeepSeek.

Reads `work_items` rows where `title_en` is NULL/empty, sends a batched
prompt asking for natural English equivalents (not literal word-for-word
translations), writes results back to `work_items.title_en`.

Run once after every `work-items-sync` (or manually any time). Idempotent:
already-translated rows are skipped. ~50 titles → 1 DeepSeek call →
under $0.001.

Usage:
    python scripts/translate_work_items.py            # all untranslated
    python scripts/translate_work_items.py --redo     # overwrite all existing
    python scripts/translate_work_items.py --db data/daytrace.sqlite
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from daytrace.db import connect, init_db


SYSTEM = (
    "你是一位双语工作助手。你会收到一份飞书任务标题清单(中文为主, 可能夹"
    "英文术语和品牌名)。请为每个标题输出**简洁、地道的英文等价表达**, "
    "而不是逐字翻译。\n\n"
    "规则:\n"
    "- 保留专有名词、缩写、文章/项目代号(例: I-ITS Multiview simulation, "
    "Transportation Science, LOFT-Sim, baidu-signal-paper)\n"
    "- 中文动作词译成英语习惯说法 (例: 修改 → Revise, 推进 → Push / Advance, "
    "整理 → Organize, 帮学生改 → Help student revise)\n"
    "- 整个标题简洁(英文不超过 ~60 字符), 保留任务的可识别性\n"
    "- 不要加引号、句号、emoji\n\n"
    "严格只输出 JSON, 形如 {\"translations\": {\"<record_id>\": \"<en_title>\", ...}}"
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db",   default=str(REPO / "data" / "daytrace.sqlite"))
    ap.add_argument("--redo", action="store_true",
                    help="re-translate all rows, even ones that already have title_en")
    ap.add_argument("--limit", type=int, default=80,
                    help="max titles per call (DeepSeek throughput cap, default 80)")
    args = ap.parse_args()

    con = connect(args.db); init_db(con)

    # We batch-translate `title` AND `subtitle` into title_en. Subtitles
    # are tiny ("daytrace repo", "LOFT-Sim / 学生文章") and worth one extra
    # token each. Always treat subtitle as a hint, not a separate row.
    if args.redo:
        rows = con.execute(
            "SELECT record_id, title FROM work_items WHERE title IS NOT NULL AND title != ''"
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT record_id, title FROM work_items "
            "WHERE title IS NOT NULL AND title != '' "
            "  AND (title_en IS NULL OR title_en = '')"
        ).fetchall()
    if not rows:
        print("nothing to translate (all titles have title_en).")
        return 0

    from daytrace import ai_client
    if not ai_client.is_available():
        print("DEEPSEEK_API_KEY not set; cannot translate.", file=sys.stderr)
        return 2

    # Batch (the prompt + JSON fits easily within DeepSeek's window).
    total_written = 0
    for i in range(0, len(rows), args.limit):
        chunk = rows[i:i + args.limit]
        manifest = {r["record_id"]: r["title"] for r in chunk}
        user = (
            "请翻译以下任务标题:\n\n"
            + json.dumps(manifest, ensure_ascii=False, indent=2)
            + "\n\n严格输出 JSON, 顶层键 translations。"
        )

        def _validator(payload):
            from daytrace.ai_client import ShapeError
            if not isinstance(payload, dict):
                raise ShapeError("expected object")
            t = payload.get("translations")
            if not isinstance(t, dict):
                raise ShapeError("translations must be object")
            return payload

        print(f"[translate] batch {i // args.limit + 1}: {len(chunk)} titles…")
        resp = ai_client.call_json_validated(
            system=SYSTEM, user=user, validator=_validator,
            max_tokens=4000,
        )
        translations = resp.json.get("translations", {})
        for record_id, en_title in translations.items():
            if not isinstance(en_title, str) or not en_title.strip():
                continue
            con.execute(
                "UPDATE work_items SET title_en = ? WHERE record_id = ?",
                (en_title.strip(), record_id),
            )
            total_written += 1
        con.commit()
        print(f"  ↳ wrote {len(translations)} translations  (cost ~${resp.cost_usd:.4f})")

    print(f"\nDone. {total_written} titles now have title_en.")
    # Show a sample for sanity
    sample = con.execute(
        "SELECT title, title_en FROM work_items WHERE title_en IS NOT NULL AND title_en != '' "
        "ORDER BY last_synced_at DESC LIMIT 5"
    ).fetchall()
    if sample:
        print("\nSample:")
        for r in sample:
            print(f"  ZH: {r['title']}")
            print(f"  EN: {r['title_en']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
