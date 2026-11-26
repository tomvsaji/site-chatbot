import importlib.util
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rag import BM25Index, Chunk, tokenize
# HTTP tests intentionally install a vector_store stub. Load the implementation
# under an isolated name so these tests exercise it without changing that stub.
spec = importlib.util.spec_from_file_location('source_filter_vector_store', Path(__file__).resolve().parents[1] / 'vector_store.py')
vector_store = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = vector_store
spec.loader.exec_module(vector_store)
HybridIndex, PROFILE_SOURCES = vector_store.HybridIndex, vector_store.PROFILE_SOURCES


class SourceTypeTests(unittest.TestCase):
    def setUp(self):
        # Never construct/rebuild a real index in a read-only filter test.
        self.index = HybridIndex.__new__(HybridIndex)
        names = ['tom-facts.md','site-home.md','site-about.md','site-writing.md','posts/composer.md','posts/sql.md']
        self.index.chunks = [Chunk(name, 'Shared SQL Cursor words ' + name, tokenize('Shared SQL Cursor words')) for name in names]
        self.index.bm25 = BM25Index(self.index.chunks)
        vector = SimpleNamespace(tolist=lambda:[1.0,0.0])
        self.index.embedder = SimpleNamespace(query_embed=lambda query:iter([vector]))
        def query_points(**kwargs):
            points=[]
            for number,chunk in enumerate(self.index.chunks):
                allowed=True
                for condition in kwargs['query_filter'].must if kwargs['query_filter'] else []:
                    match=condition.match
                    allowed &= chunk.source in match.any if hasattr(match,'any') else chunk.source==match.value
                if allowed:
                    points.append(SimpleNamespace(payload={'source':chunk.source,'text':chunk.text},score=0.9-number/100))
            return SimpleNamespace(points=points)
        self.index.client = SimpleNamespace(query_points=Mock(side_effect=query_points))

    def test_profile_filters_both_retrievers_before_fusion_independently_of_query_words(self):
        for query in ['SQL Cursor writing', 'clients and skills', 'unrelated semantic phrase']:
            with self.subTest(query=query), patch.object(vector_store,'BM25Index',wraps=BM25Index) as lexical:
                matches=self.index.search(query,limit=8,source_type='profile')
            self.assertEqual({m.chunk.source for m in matches},set(PROFILE_SOURCES))
            self.assertTrue(all(c.source in PROFILE_SOURCES for c in lexical.call_args.args[0]))
            conditions=self.index.client.query_points.call_args.kwargs['query_filter'].must
            self.assertEqual(set(conditions[0].match.any),set(PROFILE_SOURCES))

    def test_writing_includes_catalog_and_articles_and_excludes_profile(self):
        with patch.object(vector_store,'BM25Index',wraps=BM25Index) as lexical:
            matches=self.index.search('skills and clients',limit=8,source_type='writing')
        expected={'site-writing.md','posts/composer.md','posts/sql.md'}
        self.assertEqual({m.chunk.source for m in matches},expected)
        self.assertEqual({c.source for c in lexical.call_args.args[0]},expected)
        conditions=self.index.client.query_points.call_args.kwargs['query_filter'].must
        self.assertEqual(set(conditions[0].match.any),expected)

    def test_all_default_retains_unfiltered_search_and_exact_source_intersects_group(self):
        matches=self.index.search('SQL',limit=8)
        self.assertEqual(len(matches),len(self.index.chunks))
        self.assertIsNone(self.index.client.query_points.call_args.kwargs['query_filter'])
        self.assertEqual(self.index.search('SQL',source='posts/sql.md',source_type='profile'),[])
        self.assertEqual([m.chunk.source for m in self.index.search('SQL',source='posts/sql.md',source_type='writing')],['posts/sql.md'])

    def test_unknown_type_is_rejected_without_querying_vectors(self):
        with self.assertRaises(ValueError):
            self.index.search('query',source_type='secret')
        self.index.client.query_points.assert_not_called()
