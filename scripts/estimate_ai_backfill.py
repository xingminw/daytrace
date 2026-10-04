#!/usr/bin/env python3
"""Offline prompt-size estimate. Never loads secrets, calls an API or saves prompts."""
import argparse
from collections import defaultdict
from datetime import datetime,timedelta
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['DAYTRACE_DISABLE_AI']='1'
from daytrace import ai_client,ai_report
from daytrace.channels import ChannelContext
from daytrace.db import events_for_shifted_day
from daytrace.timezone import LOCAL_TZ

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',type=Path,default=ROOT/'data/daytrace.sqlite')
    p.add_argument('--end',default=(datetime.now(LOCAL_TZ).date()-timedelta(days=1)).isoformat())
    p.add_argument('--out',type=Path,default=ROOT/'data/ai-backfill-estimate.json')
    args=p.parse_args()
    disk=sqlite3.connect(f'file:{args.db.resolve()}?mode=ro',uri=True)
    con=sqlite3.connect(':memory:');con.row_factory=sqlite3.Row;disk.backup(con);disk.close()
    all_dates=[r[0] for r in con.execute('SELECT date FROM day_report WHERE total_events>0 ORDER BY date')]
    dates=[d for d in all_dates if d<=args.end]
    counts=defaultdict(lambda:{'requests':0,'ascii_characters':0,'non_ascii_characters':0,'output_cap_tokens':0})
    active=['']
    def deny(*a,**kw):raise RuntimeError('Offline estimate: credentials and network are forbidden')
    ai_client._load_secrets_into_environ=deny
    # Replace transport and availability before evaluating any prompt builder.
    ai_client.is_available=lambda:True
    def record(*,system,user,max_tokens=2048,**kwargs):
        stat=counts[active[0]]
        ascii_count=sum(ord(ch)<128 for ch in system+user)
        stat['requests']+=1;stat['ascii_characters']+=ascii_count
        stat['non_ascii_characters']+=len(system)+len(user)-ascii_count
        stat['output_cap_tokens']+=max_tokens
        return ai_client.LLMResponse(json={'labels':{}},tokens_in=0,tokens_out=0,cost_usd=0,model='offline-placeholder')
    ai_client.call_json=record;ai_client.call_json_validated=record
    import socket,urllib.request
    socket.create_connection=deny;urllib.request.urlopen=deny
    event_counts=[]
    for d in dates:
        events=events_for_shifted_day(con,d,order='asc',limit=None)
        event_counts.append(len(events))
        ctx=ChannelContext(date=d,con=con,events_hash='offline')
        for name,fn in [('project_summary',ai_report.compute_ai_project_summary_batch),('daily_overview',ai_report.compute_ai_overview),('activity_labels',ai_report.compute_ai_activity_labels)]:
            active[0]=name;fn(events,ctx)
    for stat in counts.values():
        stat['rough_input_tokens_low']=math.ceil(stat['ascii_characters']/4+stat['non_ascii_characters']*0.7)
        stat['rough_input_tokens_high']=math.ceil(stat['ascii_characters']/3+stat['non_ascii_characters']*1.5)
    extra_days=max(0,len(dates)-1)
    result={
        'offline':True,'credentials_read':False,'actual_api_calls':0,
        'code_default_provider_url':ai_client.DEFAULT_BASE_URL,'code_default_model':ai_client.DEFAULT_MODEL,
        'model_availability_verified':False,'all_data_workdays':len(all_dates),
        'candidate_complete_workdays':len(dates),'first':dates[0] if dates else None,'last':dates[-1] if dates else None,
        'excluded_later_workdays':[d for d in all_dates if d>args.end],
        'candidate_events':sum(event_counts),'max_daily_events':max(event_counts,default=0),
        'base_channels':dict(counts),
        'base_requests':sum(s['requests'] for s in counts.values()),
        'rough_base_input_tokens_low':sum(s['rough_input_tokens_low'] for s in counts.values()),
        'rough_base_input_tokens_high':sum(s['rough_input_tokens_high'] for s in counts.values()),
        'additional_continuity_requests_upper':2*extra_days,
        'configured_output_token_caps_no_retries':sum(s['output_cap_tokens'] for s in counts.values())+2100*extra_days,
        'limitations':['Character ratios are rough estimates, not a model tokenizer.','Base input omits not-yet-generated project summaries injected into overviews and continuity prompts.','Continuity can add up to two requests per day after the first; its input size depends on generated summaries.','Output caps are maxima, not expected usage; retries and weekly summaries are excluded.','The code passes sensitivity flags through without redaction. Titles and truncated summaries can contain private prompt text.','No currency estimate: verify current official model availability/prices and agree a budget first.'],
        'fields_sent_if_approved':{'daily_overview':'date, times, source, repository label, title, summary first 120 characters, deterministic stats, recent baseline, all indexed repo names, generated per-project summaries','project_summary':'repo name, event count, active minutes; first 30 events per project with time/source/title/summary first 80 characters','activity_labels':'event ID, time, source, repository label, title first 80 characters; chunks of 80 events','continuity':'today and previous generated daily/project summaries'},
        'source_content':'Current restored sources are Codex user messages/thread-start summaries and Git commit/status metadata. No assistant responses, tool outputs, attachments or full rollout/evidence JSON are deliberately included in the prompt builders; copied or quoted content may still occur within a user prompt.'
    }
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))
    con.close()

if __name__=='__main__':main()
