import sys
import types
import unittest

stub = types.ModuleType('vector_store')
stub.EMBEDDING_MODEL = 'test-embedding'
stub.MIN_LEXICAL_SCORE = 2.0
stub.QDRANT_COLLECTION = 'test'
stub.HybridIndex = object
sys.modules.setdefault('vector_store', stub)
import app


class AppTests(unittest.TestCase):
    def test_minute_rate_limit_recovers(self):
        limiter = app.SlidingWindowLimiter(2, 10)
        self.assertTrue(limiter.allow('client', now=0)[0])
        self.assertTrue(limiter.allow('client', now=1)[0])
        self.assertFalse(limiter.allow('client', now=2)[0])
        self.assertTrue(limiter.allow('client', now=62)[0])

    def test_hour_rate_limit_recovers(self):
        limiter = app.SlidingWindowLimiter(10, 2)
        self.assertTrue(limiter.allow('client', now=0)[0])
        self.assertTrue(limiter.allow('client', now=61)[0])
        self.assertFalse(limiter.allow('client', now=122)[0])
        self.assertTrue(limiter.allow('client', now=3601)[0])

    def test_invalid_forwarded_ip_uses_peer(self):
        self.assertEqual(app.client_ip('invalid', '192.0.2.1'), '192.0.2.1')


class SessionHTTPTests(unittest.TestCase):
    def setUp(self):
        import threading
        from http.server import ThreadingHTTPServer
        from unittest.mock import patch
        self.sessions = []
        def fake_run(question, session, index, emit=lambda *_:None):
            self.sessions.append(session)
            return {'answer':'test answer','sources':[]}
        self.patches = [patch('app.get_index',return_value=object()),
                        patch('app.agent.run_agent',side_effect=fake_run),
                        patch('app.RATE_LIMITER', app.SlidingWindowLimiter(100,100)),
                        patch('app.Handler.log_message')]
        for p in self.patches:
            p.start(); self.addCleanup(p.stop)
        self.server = ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, path='/api/chat', cookie='', body=None, origin=None):
        import http.client,json
        connection=http.client.HTTPConnection(*self.server.server_address,timeout=5)
        headers={'Content-Type':'application/json','Cookie':cookie}
        if origin:headers['Origin']=origin
        connection.request('POST',path,json.dumps(body if body is not None else {'question':'hello'}),headers)
        response=connection.getresponse()
        status=response.status;set_cookie=response.getheader('Set-Cookie');data=response.read()
        connection.close()
        return status,set_cookie,data

    def test_cookie_scopes_memory_and_rejects_invalid_tokens(self):
        status,cookie,_=self.post()
        self.assertEqual(status,200)
        self.assertIn('HttpOnly',cookie)
        self.assertIn('Secure',cookie)
        self.assertIn('SameSite=Strict',cookie)
        first=self.sessions[-1]
        self.post(cookie=cookie.split(';')[0])
        self.assertEqual(self.sessions[-1],first)
        self.post(cookie='site_chat_session=attacker-controlled')
        self.assertNotEqual(self.sessions[-1],first)
        self.post()
        self.assertNotEqual(self.sessions[-1],first)

    def test_untrusted_origin_and_invalid_body_never_reach_agent(self):
        self.assertEqual(self.post(origin='https://untrusted.invalid')[0],403)
        self.assertEqual(self.post(body={'question':None})[0],400)
        self.assertEqual(self.sessions,[])

    def test_clear_deletes_only_the_cookie_session_and_rotates_token(self):
        from unittest.mock import patch,Mock
        _,cookie,_=self.post()
        old_key=self.sessions[-1]
        memory=Mock()
        with patch('app.agent.store',return_value=memory):
            status,new_cookie,_=self.post('/api/conversation/reset',cookie.split(';')[0],{})
        self.assertEqual(status,200)
        memory.delete.assert_called_once_with(old_key)
        self.assertNotEqual(new_cookie.split(';')[0],cookie.split(';')[0])
