"""Run inside the app image; isolated disposable memory; READ ONLY existing vector index.
Never instantiate HybridIndex normally: its constructor rebuilds the shared collection.
"""
import json,os,time,tempfile,resource
from pathlib import Path
import agent
from memory import ConversationMemory
from rag import load_chunks,BM25Index
from vector_store import HybridIndex,TextEmbedding,QdrantClient,EMBEDDING_MODEL,EMBEDDING_CACHE_DIR,QDRANT_URL
index=HybridIndex.__new__(HybridIndex)
index.chunks=load_chunks(Path('/app/data'));index.bm25=BM25Index(index.chunks)
index.embedder=TextEmbedding(model_name=EMBEDDING_MODEL,cache_dir=EMBEDDING_CACHE_DIR)
index.client=QdrantClient(url=QDRANT_URL,timeout=30)
generation_trace=[]
native_stream=agent.stream_completion
def trace_stream(request,*args,**kwargs):
 started=time.monotonic(); content=''; names=[]; finishes=[]
 try:
  for choice in native_stream(request,*args,**kwargs):
   delta=choice.get('delta',{})
   if isinstance(delta.get('content'),str):content+=delta['content']
   names.extend(f.get('function',{}).get('name','') for f in delta.get('tool_calls',[]))
   if choice.get('finish_reason'):finishes.append(choice['finish_reason'])
   yield choice
 finally:
  # No reasoning text or raw content is persisted in diagnostic traces.
  generation_trace.append({'seconds':round(time.monotonic()-started,3),'tool_choice':request.get('tool_choice'), 'has_tools':bool(request.get('tools')),'finish_reasons':finishes,'content_chars':len(content),'has_think_open':'<think>' in content,'has_think_close':'</think>' in content,'has_raw_tool_tag':'<tool_call>' in content,'tool_name_fragments':names})
agent.stream_completion=trace_stream
captured_metrics=[]
NativeMetrics=agent.RequestMetrics
class CapturedMetrics(NativeMetrics):
 def __init__(self):
  super().__init__();captured_metrics.append(self)
agent.RequestMetrics=CapturedMetrics
tool_calls=[]
native_tool=agent.execute_tool
def trace_tool(call,*args,**kwargs):
 result=native_tool(call,*args,**kwargs)
 tool_calls.append({'function':call.get('function'), 'result':result})
 return result
agent.execute_tool=trace_tool
questions=[
 ('specialization','What does Tom specialize in?'),
 ('clients','Tell me some projects or clients he has worked with.'),
 ('sql_experience',"What is his experience with SQL agents?"),
 ('article_summary','Summarize his Building Reliable SQL Agents in Complex On-Prem SQL Environments article.'),
 ('conversation_summary','Summarize our conversation so far.'),
 ('first_question','What was my first question in this conversation?')]
# A fixed tiny unrelated prompt makes starting cache conditions reproducible; not a cold model restart.
agent.post('chat',agent.payload([{'role':'user','content':'Reply with OK.'}],tools=False,max_tokens=8))
with tempfile.TemporaryDirectory(prefix='latency-bench-') as d:
 agent.MEMORY=ConversationMemory(Path(d)/'memory.sqlite3')
 for name,question in questions:
  tool_calls.clear();generation_trace.clear();captured_metrics.clear()
  started=time.monotonic();events=[];first_delta=[]
  def emit(event,body):
   elapsed=time.monotonic()-started
   events.append({'event':event,'seconds':round(elapsed,3),'chars':len(body.get('text',''))})
   if event=='delta' and not first_delta:first_delta.append(elapsed)
  row={'case':name,'question':question}
  try:
   result=agent.run_agent(question,'benchmark',index,emit)
   row.update(result,ok=True)
  except Exception as error:
   row.update(ok=False,error=type(error).__name__+': '+str(error),metrics=captured_metrics[-1].result() if captured_metrics else None)
  row.update(tool_calls=tool_calls,generation_trace=generation_trace,seconds=round(time.monotonic()-started,3),first_delta_seconds=round(first_delta[0],3) if first_delta else None,events=events,process_peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
  print(json.dumps(row,ensure_ascii=False),flush=True)
  if not row['ok'] and os.getenv('BENCH_STOP_ON_FAILURE') == 'true':break
