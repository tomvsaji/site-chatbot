"""Compare observed sequential agent runs; not a statistical SLA/accuracy score."""
import json,statistics,sys
from pathlib import Path
root=Path(__file__).resolve().parent
variants={'baseline':sys.argv[1] if len(sys.argv)>1 else 'baseline','candidate':sys.argv[2] if len(sys.argv)>2 else 'candidate'}
rows={name:[json.loads(l) for l in (root/(file+'.jsonl')).read_text().splitlines()] for name,file in variants.items()}
if any(len(value)!=6 for value in rows.values()):raise SystemExit('Waiting for both complete six-turn runs')
summary={}
for name,items in rows.items():
 summary[name]={'agent_elapsed_seconds':round(sum(r['seconds'] for r in items),3),'median_agent_seconds':round(statistics.median(r['seconds'] for r in items),3),'completed':sum(r['ok'] for r in items),'requests':len(items),'model_calls':sum((r.get('metrics')or{}).get('model_calls',0) for r in items),'prompt_tokens':sum((r.get('metrics')or{}).get('prompt_tokens',0)or 0 for r in items),'completion_tokens':sum((r.get('metrics')or{}).get('completion_tokens',0)or 0 for r in items)}
pairs=[]
for before,after in zip(rows['baseline'],rows['candidate']):
 assert before['case']==after['case']
 pairs.append({'case':before['case'],'baseline_seconds':before['seconds'],'candidate_seconds':after['seconds'],'baseline_completed':before['ok'],'candidate_completed':after['ok'],'candidate_cache_reuse_ratio':(after.get('metrics')or{}).get('prompt_cache_reuse_ratio'),'candidate_cached_prompt_tokens':(after.get('metrics')or{}).get('cached_prompt_tokens'),'candidate_first_delta_seconds':after['first_delta_seconds']})
report={'date':'2026-11-26','method':'One sequential six-question conversation per variant on same actual host and isolated pinned llama.cpp runtime with 256MiB prompt cache. Disposable conversation memory; read-only existing Qdrant index. Fixed priming request, cache not flushed between variants. Baseline failures change subsequent conversation history. Not an accuracy score or statistical latency distribution. Agent elapsed excludes browser/network admission.','summary':summary,'per_case':pairs,'aggregate_reduction_percent':round(100*(1-summary['candidate']['agent_elapsed_seconds']/summary['baseline']['agent_elapsed_seconds']),1)}
(root/'comparison.json').write_text(json.dumps(report,indent=2))
lines=['# Observed chatbot optimization comparison','',report['method'],'','| Case | Baseline (s) | Candidate (s) | Completion |','|---|---:|---:|---|']
for r in pairs:lines.append(f"| {r['case']} | {r['baseline_seconds']:.2f} | {r['candidate_seconds']:.2f} | {'ok' if r['baseline_completed'] else 'error'} → {'ok' if r['candidate_completed'] else 'error'} |")
lines+=['',f"Total: {summary['baseline']['agent_elapsed_seconds']:.3f} → {summary['candidate']['agent_elapsed_seconds']:.3f}s ({report['aggregate_reduction_percent']}% lower observed wall time).",'', 'Completion means the agent returned a response, not that all claims were factually correct. Manual content review is recorded separately.']
(root/'COMPARISON.md').write_text('\n'.join(lines)+'\n')
print(json.dumps(report,indent=2))
