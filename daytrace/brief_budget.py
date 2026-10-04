"""Independent persistent budgets for explicitly approved evidence-brief plans.

Unknown network outcomes retain the entire reservation. Neither restarts nor
changing a cap can renew an existing authorization or its attempt allowance.
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from .ai_budget import BudgetStop, RequestStop
from .ai_privacy import CREDENTIAL, final_guard
from .briefings import digest

MODEL = 'deepseek-flash'
INPUT_RATE = 2  # micro RMB/token; peak cache-miss price used by the existing deployment
OUTPUT_RATE = 8
ATTEMPTS = 2
POLICY = {'version': 'brief-budget-v1', 'model': MODEL, 'input_micro_rmb': INPUT_RATE,
          'output_micro_rmb': OUTPUT_RATE, 'attempts_per_request': ATTEMPTS, 'thinking': 'disabled'}


def request_payload(prompt):
    if prompt['model'] != MODEL or prompt['thinking'] != {'type': 'disabled'}:
        raise RequestStop('unsupported model or thinking policy')
    system, user = prompt['system'], prompt['user']
    # Validate JSON strings individually. Regex replacement on serialized JSON
    # can mistake escaped quotes/slashes for a path and corrupt reviewed data.
    def check(value):
        if isinstance(value, str):
            if final_guard('', value)[1] != value:
                raise RequestStop('outbound field needs privacy filtering; re-plan required')
        elif isinstance(value, dict):
            for key, item in value.items():check(key);check(item)
        elif isinstance(value, list):
            for item in value:check(item)
    check(system)
    check(json.loads(user))
    if not 1 <= prompt['max_tokens'] <= 4500:
        raise RequestStop('unsupported output limit')
    return {'model': MODEL, 'messages': [{'role': 'system', 'content': system},
            {'role': 'user', 'content': user}], 'thinking': {'type': 'disabled'},
            'max_tokens': prompt['max_tokens'], 'response_format': {'type': 'json_object'}, 'stream': False}


def estimate(payload):
    input_cap = len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()) + 4096
    amount = input_cap * INPUT_RATE + payload['max_tokens'] * OUTPUT_RATE
    return {'input_token_upper': input_cap, 'output_cap': payload['max_tokens'],
            'one_attempt_micro_rmb': amount, 'two_attempt_upper_rmb': amount * ATTEMPTS / 1e6}


class BriefLedger:
    def __init__(self, path, approval_hash, cap_micro, scope):
        if type(cap_micro) is not int or cap_micro <= 0:
            raise BudgetStop('positive explicit budget required')
        self.path = Path(path)
        self.con = sqlite3.connect(path, timeout=30)
        self.con.row_factory = sqlite3.Row
        self.con.executescript('''
          PRAGMA journal_mode=WAL;
          PRAGMA synchronous=FULL;
          CREATE TABLE IF NOT EXISTS approvals(hash TEXT PRIMARY KEY,cap INTEGER NOT NULL,policy TEXT NOT NULL,scope TEXT NOT NULL,halted INTEGER DEFAULT 0);
          CREATE TABLE IF NOT EXISTS brief_attempts(id INTEGER PRIMARY KEY,approval TEXT NOT NULL,request_key TEXT NOT NULL,reserved INTEGER NOT NULL,charged INTEGER NOT NULL,input_cap INTEGER,output_cap INTEGER,tokens_in INTEGER,tokens_out INTEGER,status TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS brief_responses(request_key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
        ''')
        self.approval = approval_hash
        policy = json.dumps(POLICY, sort_keys=True)
        self.con.execute('BEGIN IMMEDIATE')
        try:
            row = self.con.execute('SELECT cap,policy,scope FROM approvals WHERE hash=?', (approval_hash,)).fetchone()
            if row and tuple(row) != (cap_micro, policy, scope):
                raise BudgetStop('existing approval scope or cap cannot be changed; preserve the ledger')
            self.con.execute('INSERT OR IGNORE INTO approvals(hash,cap,policy,scope) VALUES(?,?,?,?)', (approval_hash, cap_micro, policy, scope))
            self.con.commit()
        except BaseException:
            self.con.rollback()
            self.con.close()
            raise

    def snapshot(self):
        count, charged, unknown = self.con.execute('SELECT count(*),coalesce(sum(charged),0),coalesce(sum(CASE WHEN status=? THEN charged ELSE 0 END),0) FROM brief_attempts WHERE approval=?', ('reserved',self.approval)).fetchone()
        cap = self.con.execute('SELECT cap FROM approvals WHERE hash=?', (self.approval,)).fetchone()[0]
        return {'attempts': count, 'committed_upper_rmb': charged / 1e6, 'unknown_reserved_rmb': unknown / 1e6, 'cap_rmb': cap / 1e6, 'remaining_rmb': (cap - charged) / 1e6}

    def reserve(self, key, limits):
        self.con.execute('BEGIN IMMEDIATE')
        try:
            cap, halted = self.con.execute('SELECT cap,halted FROM approvals WHERE hash=?', (self.approval,)).fetchone()
            if halted:
                raise RequestStop('approval halted after provider contract mismatch')
            # Global request-key count: copying/replanning cannot reset retries.
            attempts = self.con.execute('SELECT count(*) FROM brief_attempts WHERE request_key=?', (key,)).fetchone()[0]
            if attempts >= ATTEMPTS:
                raise RequestStop('persisted request attempt limit reached')
            used = self.con.execute('SELECT coalesce(sum(charged),0) FROM brief_attempts WHERE approval=?', (self.approval,)).fetchone()[0]
            amount = limits['one_attempt_micro_rmb']
            if used + amount > cap:
                raise BudgetStop('budget cannot cover the next worst-case attempt')
            row = self.con.execute('INSERT INTO brief_attempts(approval,request_key,reserved,charged,input_cap,output_cap,status) VALUES(?,?,?,?,?,?,"reserved")', (self.approval,key,amount,amount,limits['input_token_upper'],limits['output_cap']))
            self.con.commit()
            return row.lastrowid
        except BaseException:
            self.con.rollback()
            raise

    def settle(self, attempt, envelope):
        usage = envelope.get('usage') or {}
        tin, tout = usage.get('prompt_tokens'), usage.get('completion_tokens')
        if type(tin) is not int or type(tout) is not int or tin < 0 or tout < 0:
            return  # leave full reserve, including crash/unknown-cost cases
        row = self.con.execute('SELECT input_cap,output_cap FROM brief_attempts WHERE id=?', (attempt,)).fetchone()
        reasoning = (usage.get('completion_tokens_details') or {}).get('reasoning_tokens', 0)
        if tin > row[0] or tout > row[1] or reasoning not in (None, 0):
            self.con.execute('UPDATE approvals SET halted=1 WHERE hash=?', (self.approval,))
            self.con.commit()
            raise RequestStop('provider token contract mismatch; full reserve retained, approval halted')
        self.con.execute('UPDATE brief_attempts SET charged=?,tokens_in=?,tokens_out=?,status="known_peak_upper" WHERE id=?', (tin*INPUT_RATE+tout*OUTPUT_RATE,tin,tout,attempt))
        self.con.commit()

    def call(self, prompt, transport, validator):
        payload = request_payload(prompt)
        key = digest({'payload': payload, 'policy': POLICY})
        cached = self.con.execute('SELECT value_json FROM brief_responses WHERE request_key=?', (key,)).fetchone()
        if cached:
            return validator(json.loads(cached[0]))
        limits = estimate(payload)
        for _ in range(ATTEMPTS):
            attempt = self.reserve(key, limits)
            try:
                envelope = transport(payload)  # exactly one HTTP attempt, no hidden retry
                self.settle(attempt, envelope)
                choice = envelope['choices'][0]
                if choice.get('finish_reason') != 'stop':
                    raise ValueError('incomplete completion')
                if choice['message'].get('reasoning_content'):
                    self.con.execute('UPDATE approvals SET halted=1 WHERE hash=?', (self.approval,)); self.con.commit()
                    raise RequestStop('unexpected reasoning output')
                raw = json.loads(choice['message']['content'])
                if CREDENTIAL.search(json.dumps(raw, ensure_ascii=False)):
                    raise RequestStop('unsafe response withheld')
                value = validator(raw)
                self.con.execute('INSERT OR REPLACE INTO brief_responses VALUES(?,?)', (key,json.dumps(raw,ensure_ascii=False)))
                self.con.commit()
                return value
            except (BudgetStop, RequestStop):
                raise
            except Exception:
                # No raw provider errors or response bodies reach logs.
                continue
        raise RequestStop('generation failed after two bounded attempts; previous report preserved')
