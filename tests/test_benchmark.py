import io
import unittest
from unittest.mock import Mock

import benchmark


class Response(io.BytesIO):
    status = 200


class BenchmarkTests(unittest.TestCase):
    def test_status_is_not_first_answer_and_missing_done_fails(self):
        client = Mock()
        client.open.return_value = Response(b'event: status\ndata: {"message":"Thinking"}\n\n')
        row = benchmark.ask(client, 'http://localhost', 'test')
        self.assertFalse(row['success'])
        self.assertIsNone(row['first_answer_seconds'])
        client.open.return_value = Response(b'event: delta\ndata: {"text":"answer"}\n\nevent: final\ndata: {"answer":"answer","sources":[],"response_mode":"curated"}\n\nevent: done\ndata: {}\n\n')
        row = benchmark.ask(client, 'http://localhost', 'test')
        self.assertTrue(row['success'])
        self.assertIsNotNone(row['first_answer_seconds'])

    def test_fast_faq_does_not_hide_generated_latency_or_rejections(self):
        summary = benchmark.summarize([
            {'seconds': .01, 'response_mode': 'curated', 'success': True},
            {'seconds': 20, 'response_mode': 'generated', 'success': True},
            {'seconds': .02, 'success': False},
            {'seconds': 30, 'success': True},
        ])
        self.assertEqual(summary['generated']['p50_seconds'], 20)
        self.assertEqual(summary['curated']['count'], 1)
        self.assertEqual(summary['rejected']['successful'], 0)
        self.assertEqual(summary['legacy']['p50_seconds'], 30)
