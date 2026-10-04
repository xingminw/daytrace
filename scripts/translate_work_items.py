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
    print("Legacy task translation disabled; local repository names are used")
    return 2


if __name__ == "__main__":
    sys.exit(main())
