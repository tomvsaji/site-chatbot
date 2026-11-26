import json,subprocess,sys,time,threading
from pathlib import Path
root=Path(__file__).resolve().parent;label=sys.argv[1]; target=sys.argv[2] if len(sys.argv)>2 else 'site-chatbot-app-1'
llm=sys.argv[3] if len(sys.argv)>3 else 'site-chatbot-llm-1'
pid=subprocess.check_output(['docker','inspect','--format','{{.State.Pid}}',llm],text=True).strip()
cgroup=Path('/proc',pid,'cgroup').read_text().strip().split('::')[-1]
memfile=Path('/sys/fs/cgroup'+cgroup)/'memory.current'
measurements=[];stop=threading.Event()
def sample():
 while not stop.is_set():
  try:measurements.append({'time':time.time(),'bytes':int(memfile.read_text())})
  except OSError:pass
  stop.wait(.5)
t=threading.Thread(target=sample);t.start()
try:
 with (root/(label+'.jsonl')).open('w') as out:
  process=subprocess.Popen(['docker','exec','-i',target,'python','-'],stdin=subprocess.PIPE,stdout=out,stderr=(root/(label+'.stderr')).open('w'))
  process.communicate((root/'runner.py').read_bytes());code=process.returncode
finally:
 stop.set();t.join()
(root/(label+'-memory.json')).write_text(json.dumps({'observed_llm_peak_bytes':max((x['bytes'] for x in measurements),default=None),'samples':len(measurements),'exit_code':code,'method':'llm container cgroup memory.current sampled every 0.5s; includes runtime cache and model'},indent=2))
print(json.dumps({'variant':label,'exit_code':code,'rows':sum(1 for _ in (root/(label+'.jsonl')).open()),'observed_llm_peak_bytes':max((x['bytes'] for x in measurements),default=None)}))
