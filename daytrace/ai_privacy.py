"""Conservative, local-only preparation for an explicitly approved AI export.

Heuristics cannot identify every semantic secret. Ambiguous multiline/quoted
user content is excluded, not truncated into apparently safe snippets.
"""
import hashlib
import json
import re

POLICY_VERSION='private-export-v1'
CREDENTIAL=re.compile(r'(?i)(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]+|AKIA[A-Z0-9]{12,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)|(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|passwd|secret|authorization|密码|密钥|令牌)\s*[=:：]\s*\S+|\bBearer\s+\S+)')
EMBEDDED=re.compile(r'(?i)(?:```|<\|(?:im_start|im_end)|<(?:assistant|tool|function|system|codex_delegation|environment_context)\b|[\"\x27]role[\"\x27]\s*:\s*[\"\x27](?:assistant|tool|system)|(?:^|\n)\s*(?:assistant|tool|system|function)\s*:|(?:助手|工具|模型|终端)(?:回复|输出|结果)|(?:assistant|tool)\s+(?:response|output)|^\s*>|diff --git|BEGIN (?:RSA |OPENSSH )?PRIVATE KEY)')
OPAQUE=re.compile(r'(?<![\w-])(?=[A-Za-z0-9_+/=-]{28,}(?![\w-]))(?=[A-Za-z0-9_+/=-]*[A-Za-z])(?=[A-Za-z0-9_+/=-]*[0-9])[A-Za-z0-9_+/=-]{28,}')
URL=re.compile(r'(?:https?|ssh|file|ftp)://[^\s<>\"\x27]+',re.I)
# Consume through spaces to end-of-line rather than leak a volume/user path suffix.
ABS_PATH=re.compile(r'(?:[A-Za-z]:[\\/]|\\\\|~/|/(?=[A-Za-z_.]))[^\n<>\"\x27]*')
IDENTIFIER=re.compile(r'(?<!\d)(?:\d{17}[\dXx]|1[3-9]\d{9})(?!\d)')
EMAIL=re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')


def clean_text(text):
    text=URL.sub('[URL]',str(text))
    text=ABS_PATH.sub('[PATH]',text)
    text=EMAIL.sub('[EMAIL]',text)
    text=IDENTIFIER.sub('[IDENTIFIER]',text)
    return text


def unsafe_reason(text):
    if CREDENTIAL.search(text):return 'credential_pattern'
    if EMBEDDED.search(text):return 'embedded_transcript_or_code'
    # Remove paths/URLs first so a Git object path is not mistaken for a token.
    if OPAQUE.search(clean_text(text)):return 'opaque_token'
    return None


def prepare_event(event):
    if event.get('sensitivity','normal')!='normal':return None,'sensitivity_flag'
    if event.get('source') not in {'codex','git'}:return None,'unsupported_source'
    evidence=event.get('evidence') or {}
    if not evidence and event.get('evidence_json'):
        try:evidence=json.loads(event['evidence_json'])
        except (ValueError,TypeError):return None,'invalid_evidence'
    # Inspect the complete user text before title/summary truncation.
    raw=str(evidence.get('raw_text') or event.get('summary') or '')
    all_text='\n'.join([str(event.get('title') or ''),raw,str(event.get('project_guess') or '')])
    reason=unsafe_reason(all_text)
    if reason:return None,reason
    if '\n' in raw.strip() or '\r' in raw.strip():return None,'multiline_ambiguous'
    if len(raw)>1000:return None,'long_ambiguous'
    if evidence.get('role') in {'assistant','tool','system'}:return None,'non_user_role'
    project=clean_text(event.get('project_guess') or 'misc')
    safe={k:event.get(k) for k in ('id','source','kind','start','end','device_id','location_id','collector_id')}
    safe.update(title=clean_text(event.get('title') or '')[:96],summary=clean_text(raw)[:120],project_guess=project,sensitivity='normal',evidence={})
    return safe,None


def fingerprint(events):
    stable=sorted(events,key=lambda e:e['id'])
    return hashlib.sha256(json.dumps(stable,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def final_guard(system,user):
    # Prompts have newlines/JSON by construction; only content danger patterns
    # are checked here. Full raw user content was inspected per event earlier.
    for text in (system,user):
        if CREDENTIAL.search(text):raise ValueError('outbound_credential_pattern')
    path_boundary=re.compile(r'(?:[A-Za-z]:[\\/]|\\\\|~/|(?<![A-Za-z0-9_])/(?:[A-Za-z_.-]+/)+)[^\n<>\"\x27]*')
    return tuple(path_boundary.sub('[PATH]',URL.sub('[URL]',text)) for text in (system,user))


def validate_request_claims(value):
    """Conservative completion-claim gate for user-request-derived reports.

A successful operation is not established by an imperative prompt. Even an
ambiguous self-report may be downgraded to a discussion rather than asserted.
"""
    claim=re.compile(r'完成|已(?:经|发送|处理|修复|确认)|解决|理清|拉取|启动|发送|处理|核算|审计|确认|\b(?:completed|finished|resolved|sent|pulled|started|handled|sorted out|audited|calculated|confirmed|fixed)\b',re.I)
    qualifier=re.compile(r'请求|要求|询问|讨论|计划|建议|提及|表达|提出|涉及|未知|未(?:确认|核实)|requested|request|asked|asking|discussed|discussion|suggest|planned|planning|mentioned|unknown|unverified',re.I)
    def walk(obj,key=''):
        if isinstance(obj,dict):
            for k,v in obj.items():
                if k in {'next_steps','suggestions','work_pattern','trend','status'}:continue
                walk(v,k)
        elif isinstance(obj,list):
            for item in obj:walk(item,key)
        elif isinstance(obj,str):
            for clause in re.split(r'[。；;\n]|(?<=[.!?])\s+|[，,]',obj):
                if re.match(r'\s*(?:Sent|Completed|Finished|Resolved|Pulled|Started|Handled|Fixed)\b',clause,re.I) or (claim.search(clause) and not qualifier.search(clause)):raise ValueError('unverified completion assertion')
    walk(value)
    return value


def suppress_unverified_claims(value):
    """Suppress unsafe assertions without fabricating a substitute achievement.

Returns corrected JSON and a visible audit count. The local record is not
changed. Suggestions remain suggestions; done statuses are downgraded.
"""
    count=0
    def walk(obj,language='zh',field=''):
        nonlocal count
        if isinstance(obj,dict):
            return {k:walk(v,k if k in {'en','zh'} else language,k) for k,v in obj.items()}
        if isinstance(obj,list):return [walk(x,language,field) for x in obj]
        if field=='status' and obj=='done':count+=1;return 'in_progress'
        if isinstance(obj,str) and field not in {'status','momentum','direction'}:
            try:validate_request_claims({'text':obj})
            except ValueError:
                count+=1
                return 'The records mention this request; its execution outcome is unverified.' if language=='en' else '记录涉及该项请求，执行结果未核实。'
        return obj
    return walk(value),count
