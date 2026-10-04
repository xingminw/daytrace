#!/usr/bin/env python3
"""Explicit daily/weekly evidence briefs. plan/publish are offline; execute is paid.

Plan examples:
  .venv/bin/python scripts/generate_brief.py plan --date 2026-10-03 --week 2026-W40 --out data/brief-plan.json
After separately approving this exact plan, dates, outbound data and budget:
  .venv/bin/python scripts/generate_brief.py execute --plan data/brief-plan.json --approve-plan HASH --budget-rmb 1
Review the generated draft, then publish locally:
  .venv/bin/python scripts/generate_brief.py publish --draft PATH --accept-draft HASH
No implicit authorization is inferred from an API key or old backfill budget.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DAYTRACE_TIMEZONE', 'Asia/Shanghai')
os.umask(0o077)
from daytrace.brief_generation import make_plan, execute_plan, publish_draft, write_json
from daytrace.ai_budget import RequestStop, BudgetStop


def main(argv=None, *, transport=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    modes = parser.add_subparsers(dest='mode', required=True)
    p = modes.add_parser('plan', help='offline dynamic source collection and cost preview')
    p.add_argument('--db', type=Path, default=ROOT/'data/daytrace.sqlite')
    p.add_argument('--date', action='append', default=[])
    p.add_argument('--week', action='append', default=[])
    p.add_argument('--out', type=Path, required=True)
    p = modes.add_parser('execute', help='explicit approved paid generation, drafts only')
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--approve-plan', required=True)
    p.add_argument('--budget-rmb', required=True)
    p = modes.add_parser('publish', help='offline reviewed draft publication, with backup')
    p.add_argument('--db', type=Path, default=ROOT/'data/daytrace.sqlite')
    p.add_argument('--draft', type=Path, required=True)
    p.add_argument('--accept-draft', required=True)
    args = parser.parse_args(argv)
    try:
        if args.mode == 'plan':
            plan = make_plan(args.db, [('daily',d) for d in args.date] + [('weekly',w) for w in args.week])
            write_json(args.out, plan)
            result = {'status': 'planned_offline', 'plan': str(args.out), 'approval_hash': plan['approval_hash'],
                      'two_attempt_upper_rmb': plan['two_attempt_upper_rmb'],
                      'jobs': [{'kind':j['kind'],'period':j['period'],'mode':j['mode'],'coverage':j['pack']['coverage']} for j in plan['jobs']]}
        elif args.mode == 'execute':
            result = execute_plan(json.loads(args.plan.read_text()), args.approve_plan, args.budget_rmb, transport=transport)
        else:
            result = publish_draft(args.db, json.loads(args.draft.read_text()), args.accept_draft)
    except (RequestStop, BudgetStop, ValueError) as error:
        print(json.dumps({'status':'refused', 'reason':str(error)},ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] != 'stopped' else 2


if __name__ == '__main__':
    raise SystemExit(main())
