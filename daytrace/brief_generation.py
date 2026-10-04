"""Normal daily/weekly generation: dynamic local plan -> approved draft -> publish.

No provider is called during planning, publishing, imports, or page rendering.
"""
from __future__ import annotations
from datetime import datetime
from decimal import Decimal, InvalidOperation
import fcntl
import json
import os
from pathlib import Path
import sqlite3
from . import ai_client
from .ai_budget import RequestStop
from .brief_budget import BriefLedger, POLICY, estimate, request_payload
from .brief_sources import build_source_pack
from .briefings import VERSION, digest, export_pack, prompt_for, save_brief, validate_brief, empty_brief

FORMAT = 'brief-plan-v1'


def readonly(db):
    con = sqlite3.connect('file:' + str(Path(db).resolve()) + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    return con


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)


def make_plan(db, targets):
    db = Path(db).resolve()
    jobs = []
    with readonly(db) as con:
        for kind, period in dict.fromkeys(targets):
            pack = build_source_pack(con, kind, period)
            # This normal plan explicitly exposes sanitized indexed Git subjects.
            # It never reads/imports the special local-review sample facts.
            prompt = prompt_for(pack, include_reviewed_facts=True)
            exported = export_pack(pack, include_reviewed_facts=True)
            has_claims = any(e['supports'] for e in exported['evidence'])
            cost = estimate(request_payload(prompt)) if has_claims else {'two_attempt_upper_rmb': 0}
            jobs.append({'kind': kind, 'period': period, 'pack': pack, 'prompt': prompt,
                         'mode': 'generate' if has_claims else 'no_evidence', 'estimate': cost})
    if not jobs:
        raise ValueError('at least one date or week required')
    plan = {'format': FORMAT, 'briefing_version': VERSION, 'db': str(db), 'policy': POLICY,
            'source_mode': 'indexed_git_and_filtered_events', 'jobs': jobs,
            'outbound_data': ['sanitized user request/discussion text', 'project labels and report dates', 'local indexed Git commit subjects'],
            'not_sent': ['absolute paths', 'document bodies', 'assistant/tool transcripts', 'credentials'],
            'two_attempt_upper_rmb': round(sum(j['estimate']['two_attempt_upper_rmb'] for j in jobs), 6)}
    plan['approval_hash'] = digest(plan)
    return plan


def check_plan(plan, approved_hash):
    content = {k:v for k,v in plan.items() if k != 'approval_hash'}
    if digest(content) != approved_hash or plan.get('approval_hash') != approved_hash:
        raise RequestStop('plan changed or approval hash does not match')
    if plan.get('format') != FORMAT or plan.get('briefing_version') != VERSION or plan.get('policy') != POLICY:
        raise RequestStop('generation policy changed; prepare and review a fresh plan')
    for job in plan['jobs']:
        if job['prompt'] != prompt_for(job['pack'], include_reviewed_facts=True):
            raise RequestStop('prompt does not match evidence; re-plan required')


def cap_micro(value):
    try:
        amount = Decimal(str(value)) * 1_000_000
        if not amount.is_finite() or amount <= 0 or amount != amount.to_integral_value():
            raise ValueError
        return int(amount)
    except (InvalidOperation, ValueError):
        raise RequestStop('budget must be a positive RMB amount with at most six decimals') from None


def execute_plan(plan, approved_hash, budget_rmb, *, transport=None):
    """Injected fake and real official transport use this exact same path.

    The approval applies only to this immutable outbound snapshot and cap.
    Returns draft artifacts. Publication is a separate local operation.
    """
    check_plan(plan, approved_hash)
    if os.environ.get('DAYTRACE_DISABLE_AI') == '1':
        raise RequestStop('AI disabled; scheduled collection cannot execute generation')
    db = Path(plan['db'])
    cap = cap_micro(budget_rmb)
    send = transport or ai_client.official_transport
    scope = json.dumps([(j['kind'],j['period']) for j in plan['jobs']])
    result = {'approval_hash': approved_hash, 'db': str(db), 'status': 'running', 'jobs': [], 'errors': []}
    result_dir = db.parent / 'brief-generations' / approved_hash
    result_path = result_dir / 'result.json'
    with (db.parent / 'brief-generation.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger = BriefLedger(db.parent / 'brief-generation-ledger.sqlite', approved_hash, cap, scope)
        try:
            for job in plan['jobs']:
                exported = export_pack(job['pack'], include_reviewed_facts=True)
                allowed = {e['id'] for e in exported['evidence']}
                visible = dict(job['pack'], evidence=[e for e in job['pack']['evidence'] if e['id'] in allowed])
                if job['mode'] == 'no_evidence':
                    value = empty_brief(visible)
                    origin = 'local_rules'
                else:
                    value = ledger.call(job['prompt'], send, lambda raw: validate_brief(raw, visible))
                    origin = 'ai_evidence_checked'
                value['origin'] = origin
                draft = {'kind': job['kind'], 'period': job['period'], 'value': value,
                         'pack': visible, 'origin': origin, 'approval_hash': approved_hash}
                draft['draft_hash'] = digest(draft)
                path = result_dir / (job['period'] + '-draft.json')
                write_json(path, draft)
                result['jobs'].append({'kind': job['kind'], 'period': job['period'], 'draft': str(path), 'draft_hash': draft['draft_hash'], 'origin': origin})
                result['budget'] = ledger.snapshot()
                write_json(result_path, result)
            result['status'] = 'completed'
        except Exception as error:
            # Known control errors contain only our own messages, not provider bodies.
            result['status'] = 'stopped'
            result['errors'].append(str(error) if isinstance(error, RequestStop) else type(error).__name__)
        finally:
            result['budget'] = ledger.snapshot()
            ledger.con.close()
            write_json(result_path, result)
    return result


def publish_draft(db, draft, accepted_hash):
    """Local-only explicit publication with backup, archive, and stale-source check."""
    if digest({k:v for k,v in draft.items() if k != 'draft_hash'}) != accepted_hash or draft['draft_hash'] != accepted_hash:
        raise RequestStop('draft changed since review')
    db = Path(db).resolve()
    if draft['origin'] not in {'ai_evidence_checked', 'local_rules'}:
        raise RequestStop('normal publisher only accepts generated or no-evidence drafts')
    with (db.parent / 'local-run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with readonly(db) as source:
            current = build_source_pack(source, draft['kind'], draft['period'])
            # Compare full source evidence, not capped export subset.
            if current['event_hash'] != draft['pack']['event_hash'] or current['evidence_hash'] != draft['pack']['evidence_hash']:
                raise RequestStop('source changed since plan; keep draft for review and prepare a new plan')
            backup = db.parent / 'backups' / ('before-brief-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.sqlite')
            backup.parent.mkdir(exist_ok=True)
            with sqlite3.connect(backup) as target:
                source.backup(target)
        con = sqlite3.connect(db)
        try:
            existing = con.execute("SELECT name FROM sqlite_master WHERE name='report_briefs'").fetchone()
            if existing:
                row = con.execute('SELECT value_json FROM report_briefs WHERE kind=? AND period=?', (draft['kind'], draft['period'])).fetchone()
                if row and json.loads(row[0]) == draft['value']:
                    return {'status': 'already_published', 'backup': str(backup)}
                if row and draft['origin'] == 'local_rules':
                    raise RequestStop('empty evidence cannot overwrite an existing report')
            save_brief(con, draft['kind'], draft['period'], draft['value'], draft['pack'], origin=draft['origin'])
        finally:
            con.close()
    return {'status': 'published', 'backup': str(backup), 'kind': draft['kind'], 'period': draft['period']}
