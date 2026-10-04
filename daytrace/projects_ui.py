"""Small dashboard surfaces backed by the local repository index."""
from datetime import date,timedelta
from html import escape
from urllib.parse import urlencode
from .projects import list_projects
from .db import query_events


def projects_panel(con,days,boundary_hour,lang):
    zh=lang=='zh'
    title='本地仓库项目' if zh else 'Local repositories'
    hint='同一仓库的多个 checkout 合并统计；只展示采集到的工作痕迹。' if zh else 'Multiple checkouts of the same repository share one project. Counts reflect collected traces.'
    projects=list_projects(con)
    if not projects:
        return f'<section class="card"><h2>{title}</h2><p>尚未建立本地仓库索引</p></section>'
    start=min(days)+f'T{boundary_hour:02d}:00:00'
    end=(date.fromisoformat(max(days))+timedelta(days=1)).isoformat()+f'T{boundary_hour:02d}:00:00'
    rows=[]
    for p in projects:
        stats=con.execute('SELECT count(*) AS n,max(start) AS latest FROM events WHERE repo_project_id=? AND start>=? AND start<?',(p['project_id'],start,end)).fetchone()
        paths=[r['path'] for r in con.execute('SELECT path FROM repo_checkouts WHERE project_id=? ORDER BY path',(p['project_id'],))]
        link='/events?'+urlencode({'project':p['name'],'lang':lang})
        details='<details><summary>'+str(len(paths))+' checkout</summary>'+'<br>'.join(escape(x) for x in paths)+'</details>'
        rows.append((stats['n'],p['name'],f'<tr><td><a href="{escape(link)}">{escape(p["name"])}</a></td><td>{stats["n"]}</td><td>{escape(stats["latest"] or "—")}</td><td>{details}</td></tr>'))
    rows.sort(key=lambda r:(-r[0],r[1]))
    heads=['仓库','本期事件','最近痕迹','本地副本'] if zh else ['Repository','Events','Latest trace','Local checkouts']
    return f'<section class="card"><h2>{title}</h2><p class="muted">{hint}</p><div style="overflow-x:auto"><table><thead><tr>'+''.join('<th>'+h+'</th>' for h in heads)+'</tr></thead><tbody>'+''.join(r[2] for r in rows)+'</tbody></table></div></section>'


def audit_panel(con,days,lang):
    if not days:return ''
    projects=list_projects(con)
    if not projects:return ''
    start=min(min(days),(date.fromisoformat(max(days))-timedelta(days=6)).isoformat())
    rows=con.execute("SELECT coalesce(original_project_guess,project_guess) AS original,count(*) AS n FROM events WHERE repo_project_id IS NULL AND date BETWEEN ? AND ? AND coalesce(original_project_guess,project_guess,'')!='' GROUP BY 1 ORDER BY n DESC LIMIT 20",(start,max(days))).fetchall()
    if not rows:return ''
    options='<option value="">—</option>'+''.join(f'<option value="{escape(p["project_id"])}">{escape(p["name"])}</option>' for p in projects)
    body=''.join(f'<tr><td>{escape(r["original"])}</td><td>{r["n"]}</td><td><input type="hidden" name="project[]" value="{escape(r["original"])}"><select name="record[]">{options}</select></td></tr>' for r in rows)
    title='项目归属校对' if lang=='zh' else 'Repository attribution'
    button='保存本地映射' if lang=='zh' else 'Save local mapping'
    return f'<section class="card" id="alignment-audit"><h2>{title}</h2><form method="post" action="/api/projects/alias"><table>{body}</table><button type="submit">{button}</button></form></section>'
