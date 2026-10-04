"""Local Git repository index. No network, Feishu, or credential operations.

Remote identity merges clones; Git common-dir merges worktrees without a
remote. Original event attribution and legacy Feishu snapshots are retained.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
from urllib.parse import urlsplit
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'config/projects.yaml'
SKIP = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', 'data', '.Trash'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS repo_projects (
 project_id TEXT PRIMARY KEY, canonical_key TEXT NOT NULL UNIQUE,
 name TEXT NOT NULL, remote_url TEXT, last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS repo_checkouts (
 path TEXT PRIMARY KEY, project_id TEXT NOT NULL, common_dir TEXT,
 last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_repo_checkouts_project ON repo_checkouts(project_id);
'''


def ensure_schema(con):
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute('PRAGMA table_info(events)')}
    for name in ('repo_project_id', 'original_project_guess'):
        if name not in cols:
            con.execute(f'ALTER TABLE events ADD COLUMN {name} TEXT')
    con.execute('CREATE INDEX IF NOT EXISTS idx_events_repo_project ON events(repo_project_id)')


def load_config(path=CONFIG):
    p = Path(path)
    cfg = yaml.safe_load(p.read_text()) if p.exists() else {}
    cfg = cfg or {}
    cfg.setdefault('roots', [str(ROOT.parent)])
    cfg.setdefault('aliases', {})
    cfg.setdefault('paths', {})
    return cfg


def canonical_remote(value):
    """Return sanitized host/path identity; never retain URL credentials."""
    value = (value or '').strip()
    if not value:
        return None
    scp = re.match(r'^(?:[^/@:]+@)?([^/:]+):(.+)$', value)
    if '://' not in value and scp and not value.startswith(('/', '.')):
        host, path = scp.groups()
    else:
        u = urlsplit(value)
        if not u.hostname or u.scheme not in {'https', 'http', 'ssh', 'git'}:
            return None
        host, path = u.hostname, u.path
    host = host.lower()
    path = path.strip('/').removesuffix('.git')
    if host == 'github.com':
        path = path.lower()
    return host + '/' + path if path else None


def git_output(path, *args):
    try:
        r = subprocess.run(['git','-C',str(path),*args], capture_output=True, text=True, timeout=3)
        return r.stdout.strip() if r.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''  # Unavailable metadata must not block local collection.


def checkout_info(path):
    path = Path(path).expanduser()
    if not path.exists():
        return None
    if path.is_file():
        path = path.parent
    top = git_output(path, 'rev-parse', '--show-toplevel')
    if not top:
        return None
    top = Path(top).resolve()
    common = git_output(top, 'rev-parse', '--path-format=absolute', '--git-common-dir')
    common = str(Path(common).resolve()) if common else str(top / '.git')
    remote = canonical_remote(git_output(top, 'config', '--get', 'remote.origin.url'))
    key = 'remote:' + remote if remote else 'local:' + common
    return {'path':str(top),'common_dir':common,'canonical_key':key,
            'project_id':'repo:' + hashlib.sha256(key.encode()).hexdigest()[:24],
            'name':remote.rsplit('/',1)[-1] if remote else top.name,
            'remote_url':'https://' + remote if remote else None}


def discover(roots, max_depth=4):
    def walk(p, depth):
        if not p.exists() or not p.is_dir():
            return
        if (p/'.git').exists():
            info = checkout_info(p)
            if info:
                yield info
        if depth >= max_depth:
            return
        try:
            children = sorted(p.iterdir())
        except OSError:
            return
        for c in children:
            if c.name not in SKIP and not c.name.startswith('.') and c.is_dir() and not c.is_symlink():
                yield from walk(c,depth+1)
    seen = set()
    for root in roots:
        for info in walk(Path(root).expanduser(),0):
            if info['path'] not in seen:
                seen.add(info['path'])
                yield info


def _path(value):
    return str(Path(value).expanduser().resolve()) if value else ''


def sync_projects(con, config_path=CONFIG):
    ensure_schema(con)
    cfg = load_config(config_path)
    events = con.execute('SELECT id,project_guess,original_project_guess,evidence_json FROM events').fetchall()
    evidence = {}
    for row in events:
        try: evidence[row['id']] = json.loads(row['evidence_json'] or '{}')
        except (ValueError,TypeError): evidence[row['id']] = {}
    discovered = {x['path']:x for x in discover(cfg['roots'])}
    # Include existing local checkouts referenced by traces (e.g. Codex worktrees).
    candidates = {str(e[k]) for e in evidence.values() for k in ('repo','cwd') if isinstance(e.get(k),str)}
    for path in sorted(candidates):
        if os.environ.get('DAYTRACE_PROJECT_METADATA_ROOTS_ONLY') == '1':
            absolute = os.path.abspath(os.path.expanduser(path))
            roots = [os.path.abspath(os.path.expanduser(str(p))) for p in cfg['roots']]
            if not any(absolute == root or absolute.startswith(root + os.sep) for root in roots):
                continue  # Preserve cached mappings without probing protected folders.
        if any(_path(path) == p or _path(path).startswith(p+'/') for p in discovered):
            continue
        info = checkout_info(path)
        if info: discovered[info['path']] = info
    for info in discovered.values():
        con.execute('INSERT INTO repo_projects(project_id,canonical_key,name,remote_url) VALUES(?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET last_seen_at=CURRENT_TIMESTAMP', (info['project_id'],info['canonical_key'],info['name'],info['remote_url']))
        con.execute('INSERT INTO repo_checkouts(path,project_id,common_dir) VALUES(?,?,?) ON CONFLICT(path) DO UPDATE SET project_id=excluded.project_id,common_dir=excluded.common_dir,last_seen_at=CURRENT_TIMESTAMP', (info['path'],info['project_id'],info['common_dir']))
    projects = {r['project_id']:dict(r) for r in con.execute('SELECT * FROM repo_projects')}
    names = {}
    for pid,p in projects.items():
        short = p['canonical_key'].rsplit('/',1)[-1] if p['canonical_key'].startswith('remote:') else Path(p['canonical_key'].removeprefix('local:')).parent.name
        names.setdefault(short.casefold(),set()).add(pid)
    # Resolve display-name collisions without merging unrelated same-name repos.
    for ids in names.values():
        for pid in ids:
            p = projects[pid]
            label = p['canonical_key'].removeprefix('remote:') if len(ids)>1 else p['name']
            if label != p['name']:
                con.execute('UPDATE repo_projects SET name=? WHERE project_id=?',(label,pid));p['name']=label
    checkouts = {r['path']:r['project_id'] for r in con.execute('SELECT * FROM repo_checkouts')}
    for p,pid in cfg['paths'].items():
        if pid not in projects: raise ValueError('Unknown project ID in path mapping')
        checkouts[_path(p)] = pid
    aliases = cfg['aliases']
    if any(pid not in projects for pid in aliases.values()):
        raise ValueError('Unknown project ID in aliases')
    paths = sorted(checkouts, key=len, reverse=True)
    linked = 0
    for row in events:
        ev = evidence[row['id']]
        original = row['original_project_guess'] if row['original_project_guess'] is not None else (row['project_guess'] or '')
        pid = aliases.get(original)
        remote_repo = ev.get('daytrace_remote_repository') or {}
        if not pid and remote_repo.get('canonical_key'):
            pid = next((ident for ident,p in projects.items() if p['canonical_key'] == remote_repo['canonical_key']), None)
        for key in ('repo','cwd','path'):
            if pid: break
            if not isinstance(ev.get(key),str): continue
            val = _path(ev[key])
            for p in paths:
                if val == p or val.startswith(p+'/'):
                    pid = checkouts[p];break
        if not pid:
            candidates = names.get(original.casefold(),set())
            if len(candidates)==1: pid = next(iter(candidates))
        label = projects[pid]['name'] if pid else (original or None)
        con.execute('UPDATE events SET original_project_guess=?,repo_project_id=?,project_guess=? WHERE id=?',(original,pid,label,row['id']))
        linked += bool(pid)
    con.commit()
    return {'projects':len(projects),'checkouts':len(checkouts),'events':len(events),'linked_events':linked,'unlinked_events':len(events)-linked}


def list_projects(con):
    return [dict(r) for r in con.execute('SELECT p.*, (SELECT count(*) FROM repo_checkouts c WHERE c.project_id=p.project_id) AS checkout_count FROM repo_projects p ORDER BY name')]


def save_aliases(con, picks, config_path=CONFIG):
    cfg = load_config(config_path)
    ids = {p['project_id'] for p in list_projects(con)}
    added = removed = 0
    for name,pid in picks:
        if pid and pid not in ids: raise ValueError('Unknown local repository ID')
        if pid:
            added += cfg['aliases'].get(name) != pid
            cfg['aliases'][name] = pid
        elif name in cfg['aliases']:
            del cfg['aliases'][name];removed += 1
    p = Path(config_path);p.parent.mkdir(parents=True,exist_ok=True)
    # Write atomically so readers cannot observe a partial YAML file.
    tmp = p.with_suffix('.yaml.tmp');tmp.write_text(yaml.safe_dump(cfg,allow_unicode=True,sort_keys=False));tmp.replace(p)
    result = sync_projects(con,config_path)
    return {'added':added,'removed':removed,'links_inserted':result['linked_events']}
