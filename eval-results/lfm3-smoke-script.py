import urllib.request,json,time
base='http://127.0.0.1:8082'
def request(path,body=None):
 start=time.monotonic();r=urllib.request.urlopen(urllib.request.Request(base+path,data=json.dumps(body).encode() if body else None,headers={'Content-Type':'application/json'}),timeout=180)
 out=r.read().decode(); print(json.dumps({'path':path,'seconds':round(time.monotonic()-start,3),'body':out}),flush=True);return json.loads(out)
request('/health')
a=request('/api/chat',{'question':'What does Tom specialize in?'})
request('/api/chat',{'question':'Tell me more about that.', 'history':[{'role':'user','content':'What does Tom specialize in?'},{'role':'assistant','content':a['answer']}]})
t=time.monotonic();r=urllib.request.urlopen(urllib.request.Request(base+'/api/chat/stream',data=json.dumps({'question':'What is the Unified Data Platform?'}).encode(),headers={'Content-Type':'application/json'}),timeout=180)
first=None;lines=[]
for raw in r:
 line=raw.decode().strip();lines.append(line)
 if line=='event: claim' and first is None:first=round(time.monotonic()-t,3)
print(json.dumps({'stream_seconds':round(time.monotonic()-t,3),'first_claim_seconds':first,'events':lines}),flush=True)
