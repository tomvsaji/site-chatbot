import json,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parent
rows=[json.loads(l) for l in (root/'preflight-context.jsonl').read_text().splitlines()]
messages=[];cases=[]
expected={'clients':('search_site','profile'),'sql_experience':('search_site',['all','writing']),'article_summary':('search_site','writing'),'conversation_summary':('search_memory',None),'first_question':('search_memory',None)}
for row in rows:
 if row['case'] in expected:cases.append({'id':row['case'],'question':row['question'],'snapshot':{'messages':messages[-2:]},'expect':expected[row['case']]})
 if row['ok']:messages.extend([{'role':'user','content':row['question']},{'role':'assistant','content':row['answer']}])
uncached=len(sys.argv)>3 and sys.argv[3]=='uncached'
script='import agent,json,time\ncases='+repr(cases)+'\n'
if uncached:script+='''native=agent._post
def uncached_post(path,payload,timeout=180):
 if path=='chat':payload=dict(payload,cache_prompt=False)
 return native(path,payload,timeout)
agent._post=uncached_post
'''
script+='''for case in cases:
 start=time.monotonic()
 try:
  call=agent.plan_tool(case['question'],case['snapshot']); function=call['function']; args=json.loads(function['arguments'])
  expected=case['expect'][1]
  group_ok=expected is None or (args.get('source_type') in expected if isinstance(expected,list) else args.get('source_type')==expected)
  ok=function['name']==case['expect'][0] and group_ok
  print(json.dumps({'case':case['id'],'ok':ok,'seconds':time.monotonic()-start,'function':function}),flush=True)
 except Exception as error:print(json.dumps({'case':case['id'],'ok':False,'error':str(error)}),flush=True)
'''
with (root/(sys.argv[1]+'.jsonl')).open('w') as out:subprocess.run(['docker','exec','-i',sys.argv[2],'python','-'],input=script.encode(),stdout=out,check=True)
print((root/(sys.argv[1]+'.jsonl')).read_text())
