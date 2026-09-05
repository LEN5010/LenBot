"""Summarize execution evidence; never substitute model grades for human review."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def distribution(values):
    values = sorted(v for v in values if v is not None)
    return {"samples":len(values), "p50_ms":values[math.ceil(len(values)*.5)-1] if values else None,
            "p95_ms":values[math.ceil(len(values)*.95)-1] if values else None}


def summarize(path):
    data=json.loads(path.read_text()); records=data['results']
    attempts=[]; tools=[]; calls=tokens_in=tokens_out=0
    for record in records:
        for trace in record['traces']:
            payload=trace['payload']; cognition=payload.get('cognition', payload)
            if trace['kind'] in {'social_cognition','social_cognition_error','agent_job','agent_job_error'}:
                attempts.extend(cognition.get('attempts',[]))
        tools.extend(o['result'].get('duration_ms') for o in record['observations'])
        for route in record['metrics'].get('routes',[]):
            calls+=route['calls'];tokens_in+=route['prompt_tokens'];tokens_out+=route['completion_tokens']
    failures=[{'case':r['case'],'repeat':r['repeat'],'error':r['error'], 'run':r['run']} for r in records if r['error']]
    return dict(file=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(), model=data['model'],
        source_tree_sha256=data['source_tree_sha256'], fixture_sha256=data.get('fixture_sha256'),
        model_mode=data['model_mode'],tool_mode=data['tool_mode'],samples=len(records),
        cases={name:sum(r['case']==name for r in records) for name in sorted({r['case'] for r in records})},
        completed=len(records)-len(failures),failures=failures,
        unsupported=sorted({c for r in records for c in r['unsupported_capabilities']}),
        model_calls=calls,prompt_tokens=tokens_in,completion_tokens=tokens_out,
        model_attempt_latency=distribution([a.get('latency_ms') for a in attempts]), tool_latency=distribution(tools),
        first_visible_reply_latency=None,complete_live_work_latency=None,live_queue_latency=None,live_send_latency=None,
        human_review=None,grounding_acceptance=None,constraint_acceptance=None,naturalness_acceptance=None,
        notice='模拟送达没有真实可见延迟；链路完成不代表事实或自然度通过。原始失败和人工评分分开保存。')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files',nargs='+',type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    args.output.write_text(json.dumps({'runs':[summarize(p) for p in args.files]},ensure_ascii=False,indent=2)+'\n')
