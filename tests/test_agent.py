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
        with patch('agent.store', return_value=self.memory), patch('agent.post', return_value={'choices':[{'message':{'content':'We discussed SQL agents.'}}]}):
            summary, recent = agent.compact_session(self.memory.snapshot('a'), 'a', lambda *_: None)
        self.assertEqual(len(recent), agent.RECENT_MESSAGES)
        self.assertEqual(summary, 'We discussed SQL agents.')
        self.assertEqual(len(self.memory.snapshot('a')['messages']), 12)
        self.assertTrue(self.memory.search('a', 'turn 0', Embedder()))


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
        self.patches = [patch('agent.store',return_value=self.memory), patch('agent.fit_context')]
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
        self.assertEqual([t['function']['name'] for t in requests[0]['tools']], ['search_site'])
        self.assertEqual(requests[1]['messages'][-1]['role'], 'tool')
        self.assertIn('Tom built an SQL agent', requests[1]['messages'][-1]['content'])

    def test_summary_can_answer_from_history_without_site_search(self):
        self.memory.touch('a')
        self.memory.append('a', [{'role':'user','content':'Tell me about SQL agents.'},{'role':'assistant','content':'We discussed SQL.'}], Embedder())
        with patch('agent.stream_completion', return_value=iter(completion('We discussed SQL agents.'))) as model:
            result = agent.run_agent('Summarize our conversation.', 'a', self.index)
        self.assertEqual(result['sources'], [])
        self.assertIn('Tell me about SQL agents.', str(model.call_args.args[0]['messages']))
        self.assertIn('first user question',model.call_args.args[0]['messages'][0]['content'])

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

    def test_disconnect_is_an_error(self):
        response=io.BytesIO(b'data: {"choices":[{"delta":{"content":"partial"}}]}\n')
        with patch('agent.urllib.request.urlopen',return_value=response):
            with self.assertRaisesRegex(RuntimeError,'ended unexpectedly'):
                list(agent.stream_completion({}))


class ContextTests(unittest.TestCase):
    def test_citation_words_are_normalized_and_removed_from_memory_context(self):
        self.assertEqual(agent.normalize_citations("Supported (citation 1) and citation 2."), "Supported [1] and [2].")
        self.assertEqual(agent.strip_citations("Earlier [1] and (citation 2)."), "Earlier  and .")

    def test_exact_budget_drops_old_pair_preserving_current_tool_exchange(self):
        messages=[{'role':'system','content':'rules'},{'role':'user','content':'old'},{'role':'assistant','content':'old answer'},
                  {'role':'user','content':'new','_current':True},{'role':'assistant','content':None,'tool_calls':[call()]},
                  {'role':'tool','tool_call_id':'call1','content':'result'}]
        responses=[{'prompt':'large'},{'tokens':[0]*8000},{'prompt':'small'},{'tokens':[0]*2000}]
        with patch('agent.post',side_effect=responses):
            agent.fit_context(messages)
        self.assertEqual([m['role'] for m in messages],['system','user','assistant','tool'])
        self.assertEqual(messages[1]['content'],'new')
