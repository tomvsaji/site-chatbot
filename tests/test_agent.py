import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import agent
from memory import ConversationMemory
from rag import Chunk


class Embedder:
    def embed(self, texts):
        return ([1.0, 0.0] for text in texts)
    def query_embed(self, text):
        return iter([[1.0, 0.0]])


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = ConversationMemory(Path(self.temp.name) / 'memory.sqlite3')

    def test_private_archive_and_delete(self):
        for session in ['a', 'b']:
            self.memory.touch(session)
            self.memory.append(session, [{'role':'user', 'content':'private ' + session}], Embedder())
        found = self.memory.search('a', 'private', Embedder())
        self.assertEqual([p['text'] for p in found], ['user: private a'])
        self.memory.delete('a')
        self.assertEqual(self.memory.search('a', 'private', Embedder()), [])
        self.assertEqual(len(self.memory.snapshot('b')['messages']), 1)

    def test_chronological_recall_is_session_scoped_and_not_similarity_ranked(self):
        self.memory.touch('a')
        self.memory.append('a', [{'role':'user','content':'first exact question'}, {'role':'assistant','content':'reply'}, {'role':'user','content':'later SQL question'}], Embedder())
        results=self.memory.search('a', 'SQL', Embedder(), order='oldest', role='user')
        self.assertEqual(results[0]['text'], 'user: first exact question')
        self.assertEqual(results[0]['message_number'], 1)
        newest=self.memory.search('a','first',Embedder(),order='newest',role='user')
        self.assertEqual(newest[0]['text'], 'user: later SQL question')

    def test_expiry_removes_messages_and_vectors(self):
        self.memory.touch('a')
        self.memory.append('a', [{'role':'user', 'content':'private a'}], Embedder())
        with self.memory.connect() as db:
            db.execute('UPDATE sessions SET updated=0')
        self.memory.touch('b')
        with self.memory.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM passages').fetchone()[0], 0)

    def test_compaction_keeps_originals_and_recent_turns(self):
        self.memory.touch('a')
        messages = [{'role':'user' if i%2==0 else 'assistant', 'content':f'turn {i}'} for i in range(12)]
        self.memory.append('a', messages, Embedder())
        with patch('agent.store', return_value=self.memory), patch('agent.context_tokens', return_value=4000), patch('agent.post', return_value={'choices':[{'message':{'content':'We discussed SQL agents.'}}]}):
            summary, recent = agent.compact_session(self.memory.snapshot('a'), 'a', lambda *_: None)
        self.assertEqual(len(recent), agent.RECENT_MESSAGES)
        self.assertEqual(summary, 'We discussed SQL agents.')
        self.assertEqual(len(self.memory.snapshot('a')['messages']), 12)
        self.assertTrue(self.memory.search('a', 'turn 0', Embedder()))

    def test_small_history_is_not_compressed_or_discarded_on_a_turn_schedule(self):
        self.memory.touch('a')
        self.memory.append('a', [{'role':'user' if i%2==0 else 'assistant','content':f'turn {i}'} for i in range(12)], Embedder())
        with patch('agent.context_tokens', return_value=2000), patch('agent.post') as model:
            summary, recent = agent.compact_session(self.memory.snapshot('a'), 'a', lambda *_:None)
        self.assertEqual(summary, '')
        self.assertEqual(len(recent), 12)
        model.assert_not_called()


def completion(content=None, calls=None, finish='stop'):
    delta = {}
    if content is not None:
        delta['content'] = content
    if calls:
        delta['tool_calls'] = [dict(call, index=i) for i,call in enumerate(calls)]
    return [{'delta':delta, 'finish_reason':finish}]


def call(name='search_site', query='Tom SQL projects'):
    return {'id':'call1', 'type':'function', 'function':{'name':name, 'arguments':json.dumps({'query':query})}}


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = ConversationMemory(Path(self.temp.name) / 'memory.sqlite3')
        self.index = SimpleNamespace(embedder=Embedder(), search=lambda query,limit: [SimpleNamespace(
            chunk=Chunk('sql.md','Tom built an SQL agent.',('sql',),title='SQL',url='https://tomvsaji.com/sql'), score=0.03)])
        self.patches = [patch('agent.store',return_value=self.memory), patch('agent.fit_context'), patch('agent.context_tokens', return_value=0)]
        for p in self.patches:
            p.start(); self.addCleanup(p.stop)

    def test_llm_query_executes_tool_and_reaches_model_as_tool_result(self):
        requests = []
        def stream(payload, **kwargs):
            requests.append(payload)
            return iter(completion(calls=[call()], finish='tool_calls') if len(requests)==1 else completion('Tom built an SQL agent. [1]'))
        with patch('agent.stream_completion', side_effect=stream):
            result = agent.run_agent('What has he built?', 'a', self.index)
        self.assertEqual(result['sources'][0]['name'], 'sql.md')
        self.assertEqual([t['function']['name'] for t in requests[0]['tools']], ['search_site', 'search_memory'])
        self.assertEqual(requests[1]['messages'][-1]['role'], 'tool')
        self.assertIn('Tom built an SQL agent', requests[1]['messages'][-1]['content'])

    def test_summary_can_answer_from_history_without_site_search(self):
        self.memory.touch('a')
        self.memory.append('a', [{'role':'user','content':'Tell me about SQL agents.'},{'role':'assistant','content':'We discussed SQL.'}], Embedder())
        with patch('agent.stream_completion', return_value=iter(completion('We discussed SQL agents.'))) as model:
            result = agent.run_agent('Summarize our conversation.', 'a', self.index)
        self.assertEqual(result['sources'], [])
        self.assertIn('Tell me about SQL agents.', str(model.call_args.args[0]['messages']))
        self.assertIn('first_user_question',model.call_args.args[0]['messages'][1]['content'])

    def test_rules_and_tool_schema_stay_stable_as_history_grows(self):
        requests=[]
        def stream(payload, **kwargs):
            requests.append(payload)
            return iter(completion('Hello.'))
        with patch('agent.stream_completion', side_effect=stream):
            for _ in range(4):
                agent.run_agent('Hello', 'a', self.index)
        self.assertTrue(all(r['messages'][0] == requests[0]['messages'][0] for r in requests))
        self.assertTrue(all(r['tools'] == requests[0]['tools'] for r in requests))

    def test_memory_tool_exposes_exact_first_question_with_chronology(self):
        self.memory.touch('a')
        self.memory.append('a',[{'role':'user','content':'first exact question'},{'role':'assistant','content':'answer'},{'role':'user','content':'later SQL question'}],Embedder())
        tool=call('search_memory','SQL')
        tool['function']['arguments']=json.dumps({'query':'SQL','order':'relevance','role':'user'})
        result=agent.execute_tool(tool,'a',self.index,{}, {},lambda *_:None)
        self.assertEqual(result['first_user_question']['text'],'first exact question')
        self.assertEqual(result['total_messages'],3)

    def test_unknown_tool_and_malformed_arguments_do_not_execute(self):
        result = agent.execute_tool(call('delete_everything'), 'a', self.index, {}, {}, lambda *_:None)
        self.assertIn('Unknown tool', result['error'])
        bad = call(); bad['function']['arguments']='not json'
        self.assertIn('Invalid arguments', agent.execute_tool(bad,'a',self.index,{}, {},lambda *_:None)['error'])

    def test_search_budget_is_enforced(self):
        with patch('agent.MAX_SEARCHES',1), patch('agent.stream_completion',side_effect=lambda _, **kwargs:iter(completion(calls=[call()],finish='tool_calls'))):
            with self.assertRaisesRegex(RuntimeError,'search budget'):
                agent.run_agent('Search forever', 'a', self.index)

    def test_session_turn_limit(self):
        self.memory.touch('a')
        self.memory.append('a', [{'role':'user','content':'hello'}], Embedder())
        with patch('agent.MAX_TURNS',1):
            with self.assertRaisesRegex(RuntimeError,'message limit'):
                agent.run_agent('again','a',self.index)

    def test_unissued_citations_are_not_saved(self):
        with patch('agent.stream_completion',side_effect=lambda *a,**k:iter(completion('Invented fact. [99]'))):
            with self.assertRaisesRegex(RuntimeError,'unsupported citation'):
                agent.run_agent('question','a',self.index)
        self.assertEqual(self.memory.snapshot('a')['messages'], [])

    def test_truncated_generation_is_an_error(self):
        with patch('agent.stream_completion',return_value=iter(completion('unfinished',finish='length'))):
            with self.assertRaisesRegex(RuntimeError,'response limit'):
                agent.run_agent('question','a',self.index)

    def test_reasoning_prefix_never_reaches_client_events_or_memory(self):
        events=[]
        with patch('agent.stream_completion',return_value=iter(completion('private reasoning</think>Visible answer.'))):
            result=agent.run_agent('question','a',self.index,lambda e,d:events.append((e,d)))
        self.assertEqual(result['answer'],'Visible answer.')
        self.assertNotIn('private reasoning',str(events))
        self.assertNotIn('private reasoning',str(self.memory.snapshot('a')['messages']))

    def test_unclosed_reasoning_is_not_streamed_or_saved(self):
        events=[]
        with patch('agent.stream_completion',return_value=iter(completion('<think>private reasoning'))):
            with self.assertRaisesRegex(RuntimeError,'invalid response'):
                agent.run_agent('question','a',self.index,lambda e,d:events.append((e,d)))
        self.assertFalse(any(e=='delta' for e,d in events))
        self.assertEqual(self.memory.snapshot('a')['messages'],[])

    def test_streams_only_after_split_reasoning_boundary_and_complete_sentence(self):
        events=[]
        def stream(*args, **kwargs):
            for text in ['private reasoning</thi', 'nk>First sentence. ', 'Second sentence.']:
                yield from completion(text, finish=None)
            self.assertIn(('delta', {'text':'First sentence. '}), events)
            yield from completion('', finish='stop')
        with patch('agent.stream_completion',side_effect=stream):
            result=agent.run_agent('question','a',self.index,lambda e,d:events.append((e,d)))
        self.assertEqual(''.join(d['text'] for e,d in events if e=='delta'),result['answer'])
        self.assertNotIn('private reasoning',str(events))

    def test_plain_content_stays_buffered_until_complete(self):
        events=[]
        def stream(*args, **kwargs):
            yield from completion('Potential unmarked thoughts. ',finish=None)
            self.assertFalse(any(e=='delta' for e,d in events))
            yield from completion('Final.',finish='stop')
        with patch('agent.stream_completion',side_effect=stream):
            agent.run_agent('question','a',self.index,lambda e,d:events.append((e,d)))

    def test_late_bad_citation_resets_early_text_before_repair(self):
        events=[]
        def first():
            yield from completion('hidden</think>Safe sentence. Next ',finish=None)
            yield from completion('claim [99].',finish='stop')
        with patch('agent.stream_completion',side_effect=[first(),iter(completion('Corrected.'))]):
            result=agent.run_agent('question','a',self.index,lambda e,d:events.append((e,d)))
        self.assertEqual(result['answer'],'Corrected.')
        self.assertIn(('reset',{}),events)
        self.assertNotIn('[99]',str(events))

    def test_early_text_resets_on_truncation_and_tool_round(self):
        for finish in ['length','tool_calls']:
            with self.subTest(finish=finish):
                events=[]
                def first():
                    yield from completion('hidden</think>Safe sentence. Next ',finish=None)
                    yield from completion(calls=[call()] if finish=='tool_calls' else None,finish=finish)
                with patch('agent.stream_completion',side_effect=[first(),iter(completion('Supported [1].'))]):
                    if finish=='length':
                        with self.assertRaisesRegex(agent.AgentError,'response limit'):
                            agent.run_agent('question',finish,self.index,lambda e,d:events.append((e,d)))
                    else:
                        agent.run_agent('question',finish,self.index,lambda e,d:events.append((e,d)))
                self.assertIn(('reset',{}),events)

    def test_unissued_and_incomplete_citations_are_never_streamable(self):
        for text in ['hidden</think>Claim [99]. Next', 'hidden</think>Claim [1. Next', 'hidden</thi']:
            self.assertEqual(agent.streamable_prefix(text,{}),'')

    def test_retrieval_is_broad_but_tool_payload_is_small_and_sources_preserved(self):
        matches=[SimpleNamespace(chunk=Chunk(str(i),'Detail '+str(i)+' '+('context '*200),('context',),title='Title',heading='Section',url='https://tomvsaji.com/article'),score=0.03) for i in range(8)]
        with patch.object(self.index,'search',return_value=matches) as search:
            sources={}
            result=agent.execute_tool(call(query='Detail 7'),'a',self.index,sources,{},lambda *_:None)
        search.assert_called_once_with('Detail 7',limit=8)
        self.assertEqual(len(result['passages']),4)
        self.assertEqual(len(sources),4)
        self.assertTrue(all(len(p['text'].split())<=112 for p in result['passages']))
        self.assertNotIn('url',result['passages'][0])
        self.assertTrue(sources[1]['url'])

    def test_excerpt_keeps_semantic_only_matches_and_can_find_late_query_context(self):
        self.assertTrue(agent.relevant_excerpt('semantic passage '*100,'unrelated').startswith('semantic passage'))
        excerpt=agent.relevant_excerpt('irrelevant '*150+'important answer appears here','important answer')
        self.assertIn('important answer',excerpt)

    def test_disconnect_is_an_error(self):
        response=io.BytesIO(b'data: {"choices":[{"delta":{"content":"partial"}}]}\n')
        with patch('agent.urllib.request.urlopen',return_value=response):
            with self.assertRaisesRegex(RuntimeError,'ended unexpectedly'):
                list(agent.stream_completion({}))


class ContextTests(unittest.TestCase):
    def test_citation_words_are_normalized_and_removed_from_memory_context(self):
        self.assertEqual(agent.normalize_citations("Supported (citation 1) and citation 2."), "Supported [1] and [2].")
        self.assertEqual(agent.strip_citations("Earlier [1] and (citation 2)."), "Earlier  and .")

    def test_exact_budget_never_discards_unsummarized_history(self):
        messages=[{'role':'system','content':'rules'},{'role':'user','content':'old'},{'role':'assistant','content':'old answer'},
                  {'role':'user','content':'new','_current':True},{'role':'assistant','content':None,'tool_calls':[call()]},
                  {'role':'tool','tool_call_id':'call1','content':'result'}]
        responses=[{'prompt':'large'},{'tokens':[0]*8000}]
        with patch('agent.post',side_effect=responses):
            with self.assertRaisesRegex(agent.AgentError, 'context budget'):
                agent.fit_context(messages)
        self.assertEqual([m['role'] for m in messages],['system','user','assistant','user','assistant','tool'])


class MetricsTests(unittest.TestCase):
    setUp = AgentTests.setUp
    @staticmethod
    def wire(choices, usage=None, usage_only=True):
        chunks = [{'choices': [choice]} for choice in choices]
        if usage is not None:
            if usage_only:
                chunks.append({'choices': [], 'usage': usage})
            else:
                chunks[-1]['usage'] = usage
        return io.BytesIO((''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n').encode())

    def test_real_stream_usage_only_chunk_is_counted_across_tool_rounds(self):
        usage = {'prompt_tokens':5, 'completion_tokens':3, 'total_tokens':8, 'prompt_tokens_details':{'cached_tokens':2}}
        responses = [self.wire(completion(calls=[call()],finish='tool_calls'), usage),
                     self.wire(completion('Tom built an SQL agent. [1]'), usage, usage_only=False)]
        with patch('agent.urllib.request.urlopen', side_effect=responses) as request:
            result = agent.run_agent('What did Tom build?', 'metrics', self.index)
        stats = result['metrics']
        self.assertEqual(stats['model'], 'LiquidAI/LFM3-1.7B')
        self.assertEqual((stats['model_calls'],stats['search_calls']), (2,1))
        self.assertEqual((stats['prompt_tokens'],stats['completion_tokens'],stats['total_tokens']), (10,6,16))
        self.assertTrue(stats['usage_complete'])
        self.assertTrue(stats['prompt_cache_complete'])
        self.assertEqual(stats['cached_prompt_tokens'],4)
        self.assertEqual(stats['prompt_cache_reuse_ratio'],0.4)
        self.assertGreater(stats['tokens_per_second'],0)
        self.assertGreaterEqual(stats['elapsed_ms'], stats['model_time_ms'] + stats['search_time_ms'])
        for args in request.call_args_list:
            self.assertTrue(json.loads(args.args[0].data)['stream_options']['include_usage'])
        self.assertIsNone(agent._METRICS.get())

    def test_missing_usage_keeps_timings_and_model_but_never_estimates_tokens(self):
        with patch('agent.urllib.request.urlopen', return_value=self.wire(completion('Hello.'))):
            result = agent.run_agent('Hello', 'missing', self.index)
        stats = result['metrics']
        self.assertEqual(stats['model_calls'],1)
        self.assertEqual(stats['search_calls'],0)
        self.assertFalse(stats['usage_complete'])
        self.assertIsNone(stats['total_tokens'])
        self.assertIsNone(stats['tokens_per_second'])

    def test_summary_usage_is_included_and_requests_do_not_share_counters(self):
        usage = {'prompt_tokens':5,'completion_tokens':3,'total_tokens':8}
        self.memory.touch('summary')
        self.memory.append('summary',[{'role':'user' if i%2==0 else 'assistant','content':f'turn {i}'} for i in range(8)],Embedder())
        summary = io.BytesIO(json.dumps({'choices':[{'message':{'content':'Earlier topics.'}}],'usage':usage}).encode())
        with patch('agent.context_tokens',return_value=4000), patch('agent.urllib.request.urlopen',side_effect=[summary,self.wire(completion('We discussed earlier topics.'),usage),self.wire(completion('Hello.'),usage)]):
            first = agent.run_agent('Summarize', 'summary', self.index)['metrics']
            second = agent.run_agent('Hello', 'separate', self.index)['metrics']
        self.assertEqual(first['model_calls'],2)
        self.assertEqual(first['total_tokens'],16)
        self.assertEqual(second['model_calls'],1)
        self.assertEqual(second['total_tokens'],8)

    def test_invalid_and_partial_usage_remain_unavailable(self):
        metrics = agent.RequestMetrics(); metrics.model_calls = 2
        metrics.record_usage({'prompt_tokens':5,'completion_tokens':3,'total_tokens':8})
        for bad in [None,{}, {'prompt_tokens':True,'completion_tokens':3,'total_tokens':4}, {'prompt_tokens':5,'completion_tokens':3,'total_tokens':9}]:
            metrics.record_usage(bad)
        self.assertFalse(metrics.result()['usage_complete'])
        self.assertIsNone(metrics.result()['prompt_tokens'])

    def test_prompt_cache_requires_runtime_metadata_for_every_call(self):
        metrics=agent.RequestMetrics(); metrics.model_calls=2
        metrics.record_cache({'prompt_tokens':100,'prompt_tokens_details':{'cached_tokens':60}})
        self.assertIsNone(metrics.result()['cached_prompt_tokens'])
        metrics.record_cache({'prompt_tokens':50}, {'cache_n':10,'prompt_n':40})
        result=metrics.result()
        self.assertTrue(result['prompt_cache_complete'])
        self.assertEqual(result['cached_prompt_tokens'],70)
        self.assertEqual(result['cache_prompt_tokens'],150)
        self.assertAlmostEqual(result['prompt_cache_reuse_ratio'],70/150)
        self.assertFalse(result['usage_complete'])

    def test_cache_missing_invalid_and_explicit_zero_are_distinct(self):
        for bad in [None,{}, {'prompt_tokens':10,'prompt_tokens_details':{'cached_tokens':11}}, {'prompt_tokens':10,'prompt_tokens_details':{'cached_tokens':True}}]:
            metrics=agent.RequestMetrics();metrics.model_calls=1;metrics.record_cache(bad)
            self.assertFalse(metrics.result()['prompt_cache_complete'])
        metrics=agent.RequestMetrics();metrics.model_calls=1
        metrics.record_cache({'prompt_tokens':10},{'cache_n':2,'prompt_n':10})
        self.assertIsNone(metrics.result()['cached_prompt_tokens'])
        metrics.record_cache({'prompt_tokens':10,'prompt_tokens_details':{'cached_tokens':0}})
        self.assertEqual(metrics.result()['cached_prompt_tokens'],0)
        self.assertEqual(metrics.result()['prompt_cache_reuse_ratio'],0)
