"""Read local indexed Git metadata for any reporting period; never fetch or read bodies."""
from __future__ import annotations
from datetime import datetime, timedelta
from pathlib import Path
import os
import subprocess
from .briefings import build_pack, digest, period_dates, project_identity
from .ai_privacy import clean_text, unsafe_reason
from .timezone import LOCAL_TZ
from .stats import DAY_BOUNDARY_HOUR

MAX_COMMITS = 2000


def git(path, *args):
    return subprocess.run(
        ['git', '--no-optional-locks', '-C', str(path), *args],
        capture_output=True, text=True, check=True, timeout=8,
        env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0'},
    ).stdout


def build_source_pack(con, kind, period):
    """The same dynamic source path is used for every daily/weekly plan.

    Checkout paths come only from the local index, plus initialized submodules
    listed in each checkout's index. No arbitrary recursive directory scanning.
    SSH entries and unavailable roots are reported, never contacted.
    """
    days = period_dates(kind, period)
    start = datetime.fromisoformat(days[0]).replace(hour=DAY_BOUNDARY_HOUR, tzinfo=LOCAL_TZ)
    stop = (datetime.fromisoformat(days[-1]) + timedelta(days=1)).replace(hour=DAY_BOUNDARY_HOUR, tzinfo=LOCAL_TZ)
    facts, audit, candidates = [], [], {}
    names = {}
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if {'repo_checkouts', 'repo_projects'} <= tables:
        for row in con.execute('SELECT c.path,c.project_id,p.name FROM repo_checkouts c JOIN repo_projects p USING(project_id) ORDER BY c.path'):
            path, rid, name = row
            pid, label = project_identity(name, rid)
            if unsafe_reason(label):
                audit.append({'status': 'unsafe_project_label_withheld'})
                continue
            names[rid] = (pid, label)
            if not Path(path).is_absolute():
                audit.append({'project_id': pid, 'status': 'nonlocal_checkout_not_contacted'})
                continue
            candidates[str(Path(path).resolve())] = (pid, label)
    # One level of initialized submodules, attributed to the containing project.
    for root, identity in list(candidates.items()):
        try:
            for line in git(root, 'ls-files', '--stage').splitlines():
                if line.startswith('160000 '):
                    child = (Path(root) / line.split('\t', 1)[1]).resolve()
                    if Path(root) in child.parents and (child / '.git').exists():
                        candidates.setdefault(str(child), identity)
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    seen = set()
    for root, (pid, label) in sorted(candidates.items()):
        try:
            output = git(root, 'log', '--all', '--since-as-filter=' + start.isoformat(),
                         '--until=' + stop.isoformat(), '--max-count=' + str(MAX_COMMITS + 1),
                         '--format=%H%x09%cI%x09%s')
        except (OSError, subprocess.SubprocessError):
            audit.append({'project_id': pid, 'checkout': root, 'status': 'unavailable_or_timeout'})
            continue
        lines = output.splitlines()
        audit.append({'project_id': pid, 'checkout': root, 'status': 'truncated' if len(lines) > MAX_COMMITS else 'read', 'observed_commits': min(len(lines), MAX_COMMITS)})
        for line in lines[:MAX_COMMITS]:
            try:
                sha, timestamp, subject = line.split('\t', 2)
                at = datetime.fromisoformat(timestamp).astimezone(LOCAL_TZ)
            except (ValueError, TypeError):
                continue
            if not start <= at < stop or (pid, sha) in seen:
                continue
            seen.add((pid, sha))
            if unsafe_reason(subject):
                audit.append({'project_id': pid, 'status': 'subject_withheld'})
                continue
            shifted_day = (at - timedelta(hours=DAY_BOUNDARY_HOUR)).date().isoformat()
            facts.append({'id': 'git:' + digest([pid, sha])[:24], 'project_id': pid,
                'project_name': label, 'date': shifted_day, 'at': at.isoformat(),
                'kind': 'git_commit', 'supports': ['implemented'], 'scope': 'repository_change',
                'text': clean_text(subject)[:800], 'commit_sha': sha,
                'source_label': 'Git ' + sha[:8], 'source_locator': root,
                'visibility': 'local_only'})
    pack = build_pack(con, kind, period, local_facts=facts, project_names=names)
    pack['source_audit'] = audit
    pack['coverage']['git_checkouts_read'] = sum(a['status'] in {'read','truncated'} for a in audit)
    pack['coverage']['unavailable_checkouts'] = sum(a['status']=='unavailable_or_timeout' for a in audit)
    pack['coverage']['truncated_checkouts'] = sum(a['status']=='truncated' for a in audit)
    pack['coverage']['remote_checkouts_skipped'] = sum(a['status']=='nonlocal_checkout_not_contacted' for a in audit)
    pack['source_capabilities'] = {
        'available': ['filtered_user_requests', 'local_git_commit_subjects'],
        'not_collected': ['document_bodies', 'assistant_or_tool_transcripts', 'test_results', 'deployment_acceptance', 'remote_hosts'],
        'meaning': 'Git commits establish repository changes, not successful execution, deployment, acceptance or scientific conclusions.',
        'timezone': str(LOCAL_TZ), 'day_boundary_hour': DAY_BOUNDARY_HOUR,
    }
    return pack
