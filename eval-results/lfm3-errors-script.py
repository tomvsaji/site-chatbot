import urllib.request,urllib.error,json,subprocess
base='http://127.0.0.1:8082'
def req(path,body,headers={}):
 try:
  r=urllib.request.urlopen(urllib.request.Request(base+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json',**headers}),timeout=30);status=r.status;out=r.read().decode()
 except urllib.error.HTTPError as e: status=e.code;out=e.read().decode()
 print(json.dumps({'path':path,'status':status,'body':out}),flush=True);return status,out
assert req('/api/chat',{'question':'hi','history':[None]})[0]==400
assert req('/api/chat',{'question':'hi'}, {'Origin':'https://untrusted.invalid'})[0]==403
statuses=[req('/api/chat',{'question':'hi','history':[]})[0] for _ in range(4)]
assert 429 in statuses
subprocess.run(['docker','stop','site-chatbot-llm-1'],check=True,stdout=subprocess.DEVNULL)
try:
 status,out=req('/api/chat',{'question':'What does Tom specialize in?'},{'X-Forwarded-For':'192.0.2.200'});assert status==503 and 'unavailable' in out
 status,out=req('/api/chat/stream',{'question':'What does Tom specialize in?'},{'X-Forwarded-For':'192.0.2.201'});assert status==200 and 'event: error' in out and 'unavailable' in out
finally: subprocess.run(['docker','start','site-chatbot-llm-1'],check=True,stdout=subprocess.DEVNULL)
