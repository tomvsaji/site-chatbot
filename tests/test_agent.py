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


def json_completion(answer, finish='stop'):
    return completion(json.dumps({'answer':answer}),finish=finish)


def call(name='search_site', query='Tom SQL projects'):
    return {'id':'call1', 'type':'function', 'function':{'name':name, 'arguments':json.dumps({'query':query})}}


def planning_response(name='search_memory', query='this conversation', usage=None, source_type='all'):
    action = {'profile':'search_profile','writing':'search_writing','all':'search_site'}.get(source_type,source_type) if name=='search_site' else name
    response={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'action':action,'query':query,'order':'relevance','role':'any'})}}]}
    if usage is not None:
        response['usage']=usage
    return response


def after_memory(response):
    """Valid required retrieval before focused tests of final-answer handling."""
    def stream(payload, **kwargs):
        if payload.get('tool_choice') == 'required':
            return iter(completion(calls=[call('search_memory', 'this conversation')], finish='tool_calls'))
        return response(payload, **kwargs) if callable(response) else iter(response)
    return stream


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = ConversationMemory(Path(self.temp.name) / 'memory.sqlite3')
        self.index = SimpleNamespace(embedder=Embedder(), search=lambda query,limit,**kwargs: [SimpleNamespace(
            chunk=Chunk('sql.md','Tom built an SQL agent.',('sql',),title='SQL',url='https://tomvsaji.com/sql'), score=0.03)])
        self.patches = [patch('agent.store',return_value=self.memory), patch('agent.fit_context'), patch('agent.context_tokens', return_value=0), patch('agent.plan_tool',return_value=call('search_memory'))]
        for p in self.patches:
            p.start(); self.addCleanup(p.stop)

    def test_llm_query_executes_tool_and_reaches_model_as_tool_result(self):
        requests = []
        def stream(payload, **kwargs):
            requests.append(payload)
            return iter(completion('Tom built an SQL agent. [1]'))
        with patch('agent.plan_tool',return_value=call()), patch('agent.stream_completion', side_effect=stream):
            result = agent.run_agent('What has he built?', 'a', self.index)
        self.assertEqual(result['sources'][0]['name'], 'sql.md')
        self.assertEqual([t['function']['name'] for t in requests[0]['tools']], ['search_site', 'search_memory'])
        self.assertEqual(requests[0]['tool_choice'],'auto')
        self.assertEqual(requests[0]['max_tokens'],640)
        self.assertEqual(requests[0]['messages'][-2]['role'], 'tool')
        self.assertIn('Tom built an SQL agent', requests[0]['messages'][-2]['content'])
        self.assertIn('Current published-evidence citation IDs: [1]',requests[0]['messages'][-1]['content'])
        self.assertEqual(requests[0]['messages'][-1]['role'],'user')
        self.assertIn('What has he built?',requests[0]['messages'][-1]['content'])

    def test_summary_can_answer_from_history_without_site_search(self):
        self.memory.touch('a')
        self.memory.append('a', [{'role':'user','content':'Tell me about SQL agents.'},{'role':'assistant','content':'We discussed SQL.'}], Embedder())
        with patch('agent.stream_completion', side_effect=after_memory(completion('We discussed SQL agents.'))) as model:
            result = agent.run_agent('Summarize our conversation.', 'a', self.index)
        self.assertEqual(result['sources'], [])
        self.assertIn('Tell me about SQL agents.', str(model.call_args.args[0]['messages']))
        self.assertIn('first_user_question',model.call_args.args[0]['messages'][1]['content'])
        self.assertEqual(model.call_args.args[0]['messages'][-1]['role'],'user')
        self.assertIn('Summarize our conversation.',model.call_args.args[0]['messages'][-1]['content'])
        self.assertIn('under 100 words',model.call_args.args[0]['messages'][-1]['content'])
        self.assertEqual(self.memory.snapshot('a')['messages'][-1]['content'],'We discussed SQL agents.')

    def test_site_answer_excludes_old_assistant_claims_but_preserves_archive(self):
        self.memory.touch('a')
        self.memory.append('a',[{'role':'user','content':'Previous user question'},{'role':'assistant','content':'Tom built Cursor. FALSE_OLD_CLAIM'}],Embedder())
        self.memory.summarize('a','FALSE_SUMMARY_CLAIM',0)
        with patch('agent.plan_tool',return_value=call()),patch('agent.stream_completion',return_value=iter(completion('Supported. [1]'))) as model:
            agent.run_agent('Current factual question','a',self.index)
        wire=model.call_args.args[0]['messages']
        self.assertNotIn('FALSE_OLD_CLAIM',str(wire))
        self.assertNotIn('FALSE_SUMMARY_CLAIM',str(wire))
        self.assertEqual(sum(m['role']=='system' for m in wire),1)
        self.assertNotIn('Previous user question',str(wire))
        self.assertEqual(wire[1]['content'],'Current factual question')
        self.assertEqual(wire[2]['role'],'assistant')
        self.assertIn('tool_calls',wire[2])
        self.assertEqual(wire[3]['role'],'tool')
        self.assertIn('FALSE_OLD_CLAIM',str(self.memory.snapshot('a')['messages']))

    def test_rules_and_tool_schema_stay_stable_as_history_grows(self):
        requests=[]
        def stream(payload, **kwargs):
            requests.append(payload)
            return iter(completion('Hello.'))
        with patch('agent.stream_completion', side_effect=after_memory(stream)):
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

    def test_visible_history_memory_lookup_never_embeds_or_duplicates_messages(self):
        self.memory.touch('a')
        self.memory.append('a',[{'role':'user','content':'private first question'}],Embedder())
        with patch.object(self.memory,'search',side_effect=AssertionError('Visible history must not be searched')):
            result=agent.execute_tool(call('search_memory'),'a',self.index,{}, {},lambda *_:None)
        self.assertEqual(result['first_user_question']['text'],'private first question')
        self.assertEqual(result['total_messages'],1)
        self.assertTrue(result['all_messages_visible'])
        self.assertNotIn('passages',result)

    def test_archived_memory_uses_original_private_hybrid_lookup(self):
        for session in ['a','b']:
            self.memory.touch(session)
            self.memory.append(session,[{'role':'user','content':'private first '+session},{'role':'assistant','content':'answer [9]'}],Embedder())
        snapshot=self.memory.snapshot('a')
        self.memory.summarize('a','Earlier topic.',snapshot['messages'][0]['id'])
        with patch.object(self.memory,'search',wraps=self.memory.search) as search:
            result=agent.execute_tool(call('search_memory'),'a',self.index,{}, {},lambda *_:None)
        search.assert_called_once()
        self.assertNotIn('all_messages_visible',result)
        self.assertIn('private first a',str(result['passages']))
        self.assertNotIn('private first b',str(result))
        self.assertNotIn('[9]',str(result['passages']))

    def test_unknown_tool_and_malformed_arguments_do_not_execute(self):
        result = agent.execute_tool(call('delete_everything'), 'a', self.index, {}, {}, lambda *_:None)
        self.assertIn('Unknown tool', result['error'])
        bad = call(); bad['function']['arguments']='not json'
        self.assertIn('Invalid arguments', agent.execute_tool(bad,'a',self.index,{}, {},lambda *_:None)['error'])

    def test_query_schema_and_execution_share_bounded_free_form_queries(self):
        for tool in agent.TOOLS:
            query_schema=tool['function']['parameters']['properties']['query']
            self.assertEqual((query_schema['minLength'],query_schema['maxLength']),(1,240))
        for query in ['', '   ', 'q'*241]:
            result=agent.execute_tool(call(query=query),'a',self.index,{}, {},lambda *_:None)
            self.assertIn('Invalid arguments',result['error'])
        result=agent.execute_tool(call(query='q'*240),'a',self.index,{}, {},lambda *_:None)
        self.assertIn('passages',result)

    def test_search_budget_is_enforced(self):
        with patch('agent.MAX_SEARCHES',1), patch('agent.stream_completion',side_effect=lambda _, **kwargs:iter(completion(calls=[call()],finish='tool_calls'))):
            with self.assertRaisesRegex(RuntimeError,'search budget'):
                agent.run_agent('Search forever', 'a', self.index)

    def test_invalid_initial_plan_fails_before_answer_generation(self):
        with patch('agent.plan_tool',side_effect=agent.AgentError('The assistant could not plan a valid search.')), patch('agent.stream_completion') as stream:
            with self.assertRaisesRegex(agent.AgentError,'could not plan a valid search'):
                agent.run_agent('question','a',self.index)
        stream.assert_not_called()
        self.assertEqual(self.memory.snapshot('a')['messages'],[])

    def test_invalid_initial_call_is_not_successful_grounding(self):
        invalid=call();invalid['function']['arguments']='not json'
        replies=iter([completion('Unsupported factual prose.')])
        with patch('agent.plan_tool',return_value=invalid), patch('agent.stream_completion',side_effect=lambda *a,**k:iter(next(replies))):
            with self.assertRaisesRegex(agent.AgentError,'could not retrieve evidence'):
                agent.run_agent('question','a',self.index)
        self.assertEqual(self.memory.snapshot('a')['messages'],[])

    def test_default_two_searches_allow_refinement_then_disable_tools(self):
        self.assertEqual(agent.MAX_SEARCHES,2)
        requests=[]
        def stream(payload,**kwargs):
            requests.append(payload)
            return iter(completion(calls=[call()],finish='tool_calls') if len(requests)<2 else json_completion('Supported. [1]'))
        with patch('agent.plan_tool',return_value=call()), patch('agent.stream_completion',side_effect=stream):
            agent.run_agent('question','a',self.index)
        self.assertEqual([r.get('tool_choice') for r in requests],['auto',None])
        self.assertNotIn('tools',requests[-1])
        self.assertEqual(requests[-1]['messages'][-1]['role'],'user')
        self.assertIn('No tools are available.',requests[-1]['messages'][-1]['content'])

    def test_site_answer_without_citations_is_repaired_and_only_corrected_reply_is_saved(self):
        requests=[]
        replies=iter([completion('Tom built an SQL agent.'),completion('Tom built an SQL agent. [1]')])
        def stream(payload,**kwargs):
            requests.append(payload);return iter(next(replies))
        with patch('agent.plan_tool',return_value=call()), patch('agent.stream_completion',side_effect=stream):
            result=agent.run_agent('question','a',self.index)
        self.assertEqual(result['answer'],'Tom built an SQL agent. [1]')
        self.assertEqual(len(requests),2)
        self.assertIn(agent.SITE_ABSTENTION,requests[-1]['messages'][-1]['content'])
        self.assertEqual(self.memory.snapshot('a')['messages'][-1]['content'],result['answer'])

    def test_repeat_uncited_site_prose_fails_but_exact_abstention_is_allowed(self):
        for answer in ['Unsupported factual prose.',agent.SITE_ABSTENTION]:
            with self.subTest(answer=answer):
                replies=iter([completion(answer),completion(answer)])
                with patch('agent.plan_tool',return_value=call()), patch('agent.stream_completion',side_effect=lambda *a,**k:iter(next(replies))):
                    if answer==agent.SITE_ABSTENTION:
                        result=agent.run_agent('question',answer,self.index)
                        self.assertEqual(result['sources'],[])
                    else:
                        with self.assertRaisesRegex(agent.AgentError,'could not produce a supported answer'):
                            agent.run_agent('question',answer,self.index)
                        self.assertEqual(self.memory.snapshot(answer)['messages'],[])

    def test_forced_final_citation_repair_repeats_no_tools_and_brevity(self):
        requests=[]
        replies=iter([json_completion('Tom built an SQL agent.'),json_completion('Tom built an SQL agent. [1]')])
        def stream(payload,**kwargs):
            requests.append(payload);return iter(next(replies))
        with patch('agent.MAX_SEARCHES',1),patch('agent.plan_tool',return_value=call()),patch('agent.stream_completion',side_effect=stream):
            agent.run_agent('Original question','a',self.index)
        self.assertTrue(all('tools' not in r for r in requests))
        correction=requests[-1]['messages'][-1]['content']
        for text in ['No tools are available.','under 100 words','Original question']:
            self.assertIn(text,correction)
        self.assertNotIn('You may refine',correction)

    def test_forced_json_answer_never_streams_provisional_json_or_reasoning(self):
        events=[]
        def stream(request,**kwargs):
            self.assertNotIn('tools',request)
            self.assertEqual(request['max_tokens'],640)
            schema=request['response_format']['json_schema']['schema']
            self.assertEqual(schema['properties']['answer']['maxLength'],1200)
            yield from completion('{"answer":"Private</think>Visible. ',finish=None)
            self.assertFalse(any(e=='delta' for e,d in events))
            yield from completion('Final."}',finish='stop')
        with patch('agent.MAX_SEARCHES',1),patch('agent.stream_completion',side_effect=stream):
            result=agent.run_agent('question','a',self.index,lambda e,d:events.append((e,d)))
        self.assertEqual(result['answer'],'Visible. Final.')
        self.assertNotIn('Private',str(events))
        self.assertEqual([d['text'] for e,d in events if e=='delta'],['Visible. Final.'])

    def test_forced_json_malformed_or_truncated_answer_is_not_saved(self):
        for response,error in [(completion('{"answer":',finish='stop'),'answer format'),(json_completion('Partial.',finish='length'),'response limit')]:
            with self.subTest(error=error),patch('agent.MAX_SEARCHES',1),patch('agent.stream_completion',return_value=iter(response)):
                with self.assertRaisesRegex(agent.AgentError,error):
                    agent.run_agent('question',error,self.index)
                self.assertEqual(self.memory.snapshot(error)['messages'],[])

    def test_bound_hit_shortens_only_complete_current_cited_prefix_and_labels_it(self):
        prefix='Supported claim. [1] '
        answer=prefix+'x'*(1200-len(prefix))
        with patch('agent.MAX_SEARCHES',1),patch('agent.plan_tool',return_value=call()),patch('agent.stream_completion',return_value=iter(json_completion(answer))):
            result=agent.run_agent('question','a',self.index)
        self.assertEqual(result['answer'],'Supported claim. [1]\n\n(Shortened to complete sentences.)')
        self.assertEqual(result['sources'][0]['number'],1)

    def test_bound_hit_without_safe_prefix_uses_existing_single_repair(self):
        for corrected in ['Supported. [1]','x'*1200]:
            replies=iter([json_completion('x'*1200),json_completion(corrected)])
            with self.subTest(corrected=corrected[:20]),patch('agent.MAX_SEARCHES',1),patch('agent.plan_tool',return_value=call()),patch('agent.stream_completion',side_effect=lambda *a,**k:iter(next(replies))):
                if corrected.startswith('Supported'):
                    result=agent.run_agent('question',corrected,self.index)
                    self.assertEqual(result['answer'],corrected)
                else:
                    with self.assertRaisesRegex(agent.AgentError,'could not complete a supported answer'):
                        agent.run_agent('question',corrected,self.index)
                    self.assertEqual(self.memory.snapshot(corrected)['messages'],[])

    def test_bounded_answer_parser_rejects_extra_fields_oversize_and_non_json_prefix(self):
        for value in ['{"answer":"ok","extra":1}',json.dumps({'answer':'x'*1201}),'{"answer":false}','Private</think>{"answer":"ok"}']:
            with self.subTest(value=value[:30]),self.assertRaisesRegex(agent.AgentError,'answer format'):
                agent.parse_bounded_answer(value)

    def test_memory_after_site_does_not_bypass_citation_gate(self):
        replies=iter([completion(calls=[call('search_memory')],finish='tool_calls'),json_completion('Unsupported factual prose.'),json_completion('Unsupported factual prose.')])
        with patch('agent.plan_tool',return_value=call()), patch('agent.stream_completion',side_effect=lambda *a,**k:iter(next(replies))):
            with self.assertRaisesRegex(agent.AgentError,'could not produce a supported answer'):
                agent.run_agent('question','a',self.index)

    def test_session_turn_limit(self):
        self.memory.touch('a')
        self.memory.append('a', [{'role':'user','content':'hello'}], Embedder())
        with patch('agent.MAX_TURNS',1):
            with self.assertRaisesRegex(RuntimeError,'message limit'):
                agent.run_agent('again','a',self.index)

    def test_unissued_citations_are_not_saved(self):
        with patch('agent.stream_completion',side_effect=after_memory(lambda *a,**k:iter(completion('Invented fact. [99]')))):
            with self.assertRaisesRegex(RuntimeError,'unsupported citation'):
                agent.run_agent('question','a',self.index)
        self.assertEqual(self.memory.snapshot('a')['messages'], [])

    def test_truncated_generation_is_an_error(self):
        with patch('agent.stream_completion',return_value=iter(completion('unfinished',finish='length'))):
            with self.assertRaisesRegex(RuntimeError,'response limit'):
                agent.run_agent('question','a',self.index)

    def test_reasoning_prefix_never_reaches_client_events_or_memory(self):
        events=[]
        with patch('agent.stream_completion',side_effect=after_memory(completion('private reasoning</think>Visible answer.'))):
            result=agent.run_agent('question','a',self.index,lambda e,d:events.append((e,d)))
        self.assertEqual(result['answer'],'Visible answer.')
        self.assertNotIn('private reasoning',str(events))
        self.assertNotIn('private reasoning',str(self.memory.snapshot('a')['messages']))

    def test_unclosed_reasoning_is_not_streamed_or_saved(self):
        events=[]
        with patch('agent.stream_completion',side_effect=after_memory(completion('<think>private reasoning'))):
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
        with patch('agent.stream_completion',side_effect=after_memory(stream)):
            result=agent.run_agent('question','a',self.index,lambda e,d:events.append((e,d)))
        self.assertEqual(''.join(d['text'] for e,d in events if e=='delta'),result['answer'])
        self.assertNotIn('private reasoning',str(events))

    def test_plain_content_stays_buffered_until_complete(self):
        events=[]
        def stream(*args, **kwargs):
            yield from completion('Potential unmarked thoughts. ',finish=None)
            self.assertFalse(any(e=='delta' for e,d in events))
            yield from completion('Final.',finish='stop')
        with patch('agent.stream_completion',side_effect=after_memory(stream)):
            agent.run_agent('question','a',self.index,lambda e,d:events.append((e,d)))

    def test_late_bad_citation_resets_early_text_before_repair(self):
        events=[]
        def first():
            yield from completion('hidden</think>Safe sentence. Next ',finish=None)
            yield from completion('claim [99].',finish='stop')
        replies=iter([first(),iter(completion('Corrected.'))])
        with patch('agent.stream_completion',side_effect=after_memory(lambda *a,**k:next(replies))):
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
                replies=iter([first(),iter(json_completion('Supported [1].'))])
                with patch('agent.stream_completion',side_effect=after_memory(lambda *a,**k:next(replies))):
                    if finish=='length':
                        with self.assertRaisesRegex(agent.AgentError,'response limit'):
                            agent.run_agent('question',finish,self.index,lambda e,d:events.append((e,d)))
                    else:
                        agent.run_agent('question',finish,self.index,lambda e,d:events.append((e,d)))
                self.assertIn(('reset',{}),events)

    def test_unissued_and_incomplete_citations_are_never_streamable(self):
        for text in ['hidden</think>Claim [99]. Next', 'hidden</think>Claim [1. Next', 'hidden</thi']:
            self.assertEqual(agent.streamable_prefix(text,{}),'')

    def test_site_prefix_waits_for_current_citations_but_memory_prefix_does_not(self):
        text='hidden</think>Claim. Next sentence.'
        self.assertEqual(agent.streamable_prefix(text,{1:{}},require_citation=True),'')
        self.assertEqual(agent.streamable_prefix(text,{},require_citation=False),'Claim. ')
        self.assertEqual(agent.streamable_prefix('hidden</think>Claim. [1] Next sentence.',{1:{}},require_citation=True),'Claim. [1] ')

    def test_retrieval_is_broad_but_tool_payload_is_small_and_sources_preserved(self):
        matches=[SimpleNamespace(chunk=Chunk(str(i),'Detail '+str(i)+' '+('context '*200),('context',),title='Title',heading='Section',url='https://tomvsaji.com/article'),score=0.03) for i in range(8)]
        with patch.object(self.index,'search',return_value=matches) as search:
            sources={}
            result=agent.execute_tool(call(query='Detail 7'),'a',self.index,sources,{},lambda *_:None)
        search.assert_called_once_with('Detail 7',limit=8,source_type='all')
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


class PlanningTests(unittest.TestCase):
    def test_model_selects_scope_with_current_question_last_and_bounded_untrusted_history(self):
        snapshot={'messages':[{'role':'user','content':'OLD_QUESTION'},{'role':'assistant','content':'x'*2000}]}
        response=planning_response('search_memory','What did we discuss?')
        with patch('agent.post',return_value=response) as post:
            planned=agent.plan_tool('CURRENT_QUESTION',snapshot)
        request=post.call_args.args[1]
        self.assertEqual(request['messages'][-1],{'role':'user','content':'CURRENT_QUESTION'})
        self.assertIn('untrusted reference data',request['messages'][0]['content'])
        self.assertIn('OLD_QUESTION',request['messages'][0]['content'])
        self.assertNotIn('x',request['messages'][0]['content'].split('Previous user question')[-1])
        self.assertNotIn('x'*401,request['messages'][0]['content'])
        self.assertEqual(set(request['response_format']['json_schema']['schema']['required']),{'action','query','order','role'})
        self.assertEqual(request['max_tokens'],192)
        self.assertNotIn('tools',request)
        self.assertTrue(request['response_format']['json_schema']['strict'])
        self.assertEqual(planned['function']['name'],'search_memory')
        self.assertEqual(json.loads(planned['function']['arguments'])['query'],'What did we discuss?')

    def test_site_plan_marshals_only_allowed_site_arguments(self):
        with patch('agent.post',return_value=planning_response('search_site','Tom AI projects')):
            planned=agent.plan_tool('question',{'messages':[]})
        self.assertEqual(json.loads(planned['function']['arguments']),{'query':'Tom AI projects','source_type':'all'})
        self.assertEqual(planned['id'],'plan_search_0')

    def test_model_selected_source_type_is_forwarded_only_to_site_search(self):
        for name in ['search_site','search_memory']:
            with patch('agent.post',return_value=planning_response(name,source_type='profile')):
                planned=agent.plan_tool('Any freely phrased question',{'messages':[]})
            arguments=json.loads(planned['function']['arguments'])
            if name=='search_site':
                self.assertEqual(arguments['source_type'],'profile')
            else:
                self.assertNotIn('source_type',arguments)

    def test_unknown_action_plan_is_rejected(self):
        with patch('agent.post',return_value=planning_response('search_site',source_type='secret')):
            with self.assertRaisesRegex(agent.AgentError,'could not plan a valid search'):
                agent.plan_tool('question',{'messages':[]})

    def test_all_model_selected_actions_map_to_allowlisted_tools_without_query_rules(self):
        for action,(name,source_type) in agent.PLAN_ACTIONS.items():
            response={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'action':action,'query':'Any arbitrary wording','order':'newest','role':'user'})}}]}
            with patch('agent.post',return_value=response):
                planned=agent.plan_tool('Another arbitrary question',{'messages':[]})
            self.assertEqual(planned['function']['name'],name)
            arguments=json.loads(planned['function']['arguments'])
            self.assertEqual(arguments['query'],'Any arbitrary wording')
            if source_type is None:
                self.assertNotIn('source_type',arguments)
            else:
                self.assertEqual(arguments['source_type'],source_type)

    def test_planning_context_contains_only_last_user_question_bounded_to_240(self):
        snapshot={'messages':[{'role':'user','content':'EARLIER'},{'role':'user','content':'q'*500},{'role':'assistant','content':'ASSISTANT_PROSE_MUST_NOT_ENTER_PLAN'}]}
        with patch('agent.post',return_value=planning_response()) as post:
            agent.plan_tool('CURRENT',snapshot)
        context=post.call_args.args[1]['messages'][0]['content']
        self.assertIn('q'*240,context)
        self.assertNotIn('q'*241,context)
        self.assertNotIn('EARLIER',context)
        self.assertNotIn('ASSISTANT_PROSE_MUST_NOT_ENTER_PLAN',context)

    def test_malformed_truncated_or_unbounded_plan_fails_closed(self):
        base=planning_response()
        invalid=[]
        for content in ['not JSON','private reasoning</think>'+base['choices'][0]['message']['content'],json.dumps({'name':'delete_all','query':'q','order':'relevance','role':'any'}),json.dumps({'name':'search_site','query':'q'*241,'order':'relevance','role':'any'}),json.dumps({'name':'search_site','query':'q','order':'relevance','role':'any','extra':'x'})]:
            invalid.append({'choices':[{'finish_reason':'stop','message':{'content':content}}]})
        invalid.append({'choices':[{'finish_reason':'length','message':{'content':base['choices'][0]['message']['content']}}]})
        for response in invalid:
            with self.subTest(response=response),patch('agent.post',return_value=response):
                with self.assertRaisesRegex(agent.AgentError,'could not plan a valid search'):
                    agent.plan_tool('question',{'messages':[]})


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
    def setUp(self):
        AgentTests.setUp(self)
        self.patches[-1].stop()  # Exercise actual planner HTTP usage/cache accounting.

    @staticmethod
    def plan_wire(name='search_memory',usage=None):
        return io.BytesIO(json.dumps(planning_response(name,usage=usage)).encode())
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
        responses = [self.plan_wire('search_site',usage),
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
            data=json.loads(args.args[0].data)
            if data['stream']:
                self.assertTrue(data['stream_options']['include_usage'])
            else:
                self.assertEqual(data['response_format']['type'],'json_schema')
        self.assertIsNone(agent._METRICS.get())

    def test_missing_usage_keeps_timings_and_model_but_never_estimates_tokens(self):
        with patch('agent.urllib.request.urlopen', side_effect=[self.plan_wire(),self.wire(completion('Hello.'))]):
            result = agent.run_agent('Hello', 'missing', self.index)
        stats = result['metrics']
        self.assertEqual(stats['model_calls'],2)
        self.assertEqual(stats['search_calls'],1)
        self.assertFalse(stats['usage_complete'])
        self.assertIsNone(stats['total_tokens'])
        self.assertIsNone(stats['tokens_per_second'])

    def test_summary_usage_is_included_and_requests_do_not_share_counters(self):
        usage = {'prompt_tokens':5,'completion_tokens':3,'total_tokens':8}
        self.memory.touch('summary')
        self.memory.append('summary',[{'role':'user' if i%2==0 else 'assistant','content':f'turn {i}'} for i in range(8)],Embedder())
        summary = io.BytesIO(json.dumps({'choices':[{'message':{'content':'Earlier topics.'}}],'usage':usage}).encode())
        memory_wire=lambda:self.plan_wire(usage=usage)
        with patch('agent.context_tokens',return_value=4000), patch('agent.urllib.request.urlopen',side_effect=[summary,memory_wire(),self.wire(completion('We discussed earlier topics.'),usage),memory_wire(),self.wire(completion('Hello.'),usage)]):
            first = agent.run_agent('Summarize', 'summary', self.index)['metrics']
            second = agent.run_agent('Hello', 'separate', self.index)['metrics']
        self.assertEqual(first['model_calls'],3)
        self.assertEqual(first['total_tokens'],24)
        self.assertEqual(second['model_calls'],2)
        self.assertEqual(second['total_tokens'],16)

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
