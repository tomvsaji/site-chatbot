"""Security regressions; retrieval/model services remain stubbed by test_app."""
import ipaddress
import unittest
from unittest.mock import patch

from test_app import app


class IdentityQuotaTests(unittest.TestCase):
    def test_untrusted_peer_cannot_forge_forwarding_headers(self):
        with patch.object(app, 'TRUSTED_PROXY_CIDRS', ()):
            self.assertEqual(app.client_ip('198.51.100.5', '192.0.2.1'), '192.0.2.1')

    def test_chain_walk_stops_at_first_untrusted_hop(self):
        with patch.object(app, 'TRUSTED_PROXY_CIDRS', (ipaddress.ip_network('10.0.0.0/24'),)):
            self.assertEqual(app.client_ip('198.51.100.99, 192.0.2.7, 10.0.0.2', '10.0.0.3'), '192.0.2.7')
            self.assertEqual(app.client_ip('2001:0db8::1, 10.0.0.2', '10.0.0.3'), '2001:db8::1')
            for header in ('garbage, 10.0.0.2', '192.0.2.7, ,10.0.0.2', '2001:db8::1%eth0', '[2001:db8::1]:80'):
                self.assertEqual(app.client_ip(header, '10.0.0.3'), '10.0.0.3')
            self.assertEqual(app.client_ip('forged, 192.0.2.7', '10.0.0.3'), '10.0.0.3')

    def test_mapped_ipv4_and_ipv6_subnets_share_quota(self):
        self.assertEqual(app.client_key('::ffff:192.0.2.5'), app.client_key('192.0.2.5'))
        self.assertEqual(app.client_key('2001:db8:1:2::1'), app.client_key('2001:db8:1:2::abcd'))
        self.assertNotEqual(app.client_key('2001:db8:1:3::1'), app.client_key('2001:db8:1:2::1'))

    def test_full_table_rejects_new_identities_without_evicting_active_clients(self):
        limiter = app.SlidingWindowLimiter(2, 3, max_clients=2)
        self.assertTrue(limiter.allow('a', now=0)[0])
        self.assertTrue(limiter.allow('b', now=0)[0])
        for i in range(100):
            self.assertFalse(limiter.allow(str(i), now=1)[0])
        self.assertEqual(len(limiter.attempts), 2)
        self.assertTrue(limiter.allow('a', now=1)[0])
        self.assertFalse(limiter.allow('a', now=2)[0])
        self.assertTrue(limiter.allow('c', now=3602)[0])
        self.assertLessEqual(len(limiter.attempts), 2)

    def test_global_budget_ignores_identity_churn(self):
        with patch.object(app, 'RATE_LIMITER', app.SlidingWindowLimiter(2, 3)), patch.object(app, 'GLOBAL_LIMITER', app.SlidingWindowLimiter(2, 3, max_clients=1)):
            self.assertTrue(app.admit_inference('192.0.2.1')[0])
            self.assertTrue(app.admit_inference('192.0.2.2')[0])
            allowed, retry = app.admit_inference('192.0.2.3')
            self.assertFalse(allowed)
            self.assertGreater(retry, 0)

import contextlib
import http.client
import json
import socket
import os
import threading
import time
import select


class LocalStreamSocket(socket.socket):
    """Sandbox denies sendto/send syscalls; write(2) uses the same real stream."""
    def sendall(self, data, flags=0):
        timeout = self.gettimeout()
        deadline = None if timeout is None else time.monotonic() + timeout
        view = memoryview(data)
        while view:
            try:
                view = view[os.write(self.fileno(), view):]
            except BlockingIOError:
                if timeout == 0:
                    raise
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError('socket write timed out')
                if not select.select([], [self], [], remaining)[1]:
                    raise TimeoutError('socket write timed out')


def stream_pair():
    first, second = socket.socketpair()
    return LocalStreamSocket(socket.AF_UNIX, socket.SOCK_STREAM, 0, fileno=first.detach()), LocalStreamSocket(socket.AF_UNIX, socket.SOCK_STREAM, 0, fileno=second.detach())


class HTTPHarness(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in [('RATE_LIMITER', app.SlidingWindowLimiter(100, 100)),
                            ('GLOBAL_LIMITER', app.SlidingWindowLimiter(100, 100)),
                            ('MODEL_CAPACITY', threading.BoundedSemaphore(1))]:
            self.stack.enter_context(patch.object(app, name, value))
        self.answer = self.stack.enter_context(patch.object(app, 'answer_question', return_value={'answer':'ok', 'sources':[]}))
        self.stack.enter_context(patch.object(app, 'get_index', return_value=object()))
        self.stack.enter_context(patch.object(app.agent, 'run_agent', side_effect=lambda q,s,i,emit: self.answer(q,s)))

    @contextlib.contextmanager
    def bounded_connection(self):
        # Only listening-TCP initialization is omitted; admission, worker,
        # handler, socket buffers, deadlines and semaphore release are real.
        with patch.object(app, 'MAX_HTTP_CONNECTIONS', 1), patch.object(app.ThreadingHTTPServer, '__init__', return_value=None):
            server = app.BoundedHTTPServer(('127.0.0.1', 0), app.Handler)
        timers = []
        class TrackedHandler(app.Handler):
            def finish(self):
                timers.append(self._read_timer)
                super().finish()
        server.RequestHandlerClass = TrackedHandler
        server.daemon_threads = False  # Keep worker handles for bounded joins.
        client, accepted = stream_pair()
        client.settimeout(2)
        with patch.object(server, 'handle_error') as errors:
            server.process_request(accepted, ('127.0.0.1', 12345))
            try:
                yield client, server, timers, errors
            finally:
                client.close()
                for thread in server._threads:
                    thread.join(2)
                    self.assertFalse(thread.is_alive(), 'worker leaked')
                self.assertTrue(server.connection_slots.acquire(blocking=False))
                server.connection_slots.release()
                for timer in timers:
                    timer.join(1)
                    self.assertFalse(timer.is_alive(), 'read timer leaked')

    @contextlib.contextmanager
    def connection(self):
        client, accepted = stream_pair()
        client.settimeout(3)
        def serve():
            try:
                app.Handler(accepted, ('127.0.0.1', 12345), None)
            finally:
                accepted.close()
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield client
        finally:
            client.close()
            thread.join(3)
            self.assertFalse(thread.is_alive(), "handler leaked")

    def request(self, body, headers=None, path='/api/chat'):
        if isinstance(body, str):
            body = body.encode()
        headers = dict(headers or {'Content-Type':'application/json'})
        headers['Content-Length'] = str(len(body))
        return self.raw(''.join(f'{key}: {value}\r\n' for key, value in headers.items()), body, path=path)

    def raw(self, headers, body=b'{}', method='POST', path='/api/chat'):
        with self.connection() as sock:
            sock.sendall(f'{method} {path} HTTP/1.1\r\nHost: localhost\r\n{headers}\r\n'.encode() + body)
            response = http.client.HTTPResponse(sock)
            response.begin()
            return response.status, dict(response.getheaders()), response.read()

class HTTPTests(HTTPHarness):
    def test_json_object_string_question_history_bounds(self):
        for value in ([], None, 1, {'question':42}, {'question':True}, {'question':None},
                      {'question':'q', 'history':[{'role':[], 'content':'x'}]},
                      {'question':'q', 'history':[{'role':'user','content':None}]},
                      {'question':'q', 'history':{}}):
            with self.subTest(value=str(value)[:60]):
                self.assertEqual(self.request(json.dumps(value))[0], 400)
        self.assertFalse(self.answer.called)

    def test_content_type_and_invalid_encoding(self):
        self.assertEqual(self.request('{"question":"q"}', {'Content-Type':'text/plain'})[0], 415)
        for body in (b'\xff', b'{', b'['*1500, b'{"question":NaN}'):
            self.assertEqual(self.request(body)[0], 400)

    def test_invalid_framing_closes_without_consuming_next_request(self):
        for headers in ('Content-Length: 2\r\nContent-Length: 2\r\n',
                        'Content-Length: -1\r\n', 'Content-Length: +2\r\n',
                        'Content-Length: 2, 2\r\n', 'Content-Length: x\r\n',
                        'Content-Length: 2\r\nTransfer-Encoding: chunked\r\n',
                        'Transfer-Encoding: chunked\r\n', 'Content-Length: 10001\r\n'):
            with self.subTest(headers=headers):
                status, response_headers, _ = self.raw(headers)
                self.assertIn(status, (400, 413))
                self.assertEqual(response_headers.get('Connection'), 'close')

    def test_unread_origin_rejection_closes_connection(self):
        status, headers, _ = self.raw('Origin: https://evil.example\r\nContent-Length: 500\r\nContent-Type: application/json\r\n')
        self.assertEqual(status, 403)
        self.assertEqual(headers.get('Connection'), 'close')

    def test_sanitized_errors_retry_headers_and_sse_contract(self):
        self.answer.side_effect = ValueError('internal sensitive detail')
        status, headers, data = self.request('{"question":"q"}')
        self.assertEqual(status, 503)
        self.assertIn('Retry-After', headers)
        self.assertNotIn(b'internal sensitive detail', data)
        with patch.object(app.agent, 'run_agent', side_effect=ValueError('internal sensitive detail')):
            status, headers, data = self.request('{"question":"q"}', path='/api/chat/stream')
        self.assertEqual(status, 200)
        self.assertIn(b'event: error', data)
        self.assertNotIn(b'internal sensitive detail', data)
        with patch.object(app.agent, 'run_agent', return_value={'answer':'ok','sources':[]}):
            status, headers, data = self.request('{"question":"q"}', path='/api/chat/stream')
        for event in (b'event: status', b'event: final', b'event: done'):
            self.assertIn(event, data)
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')

    def test_origin_xff_cannot_reset_limits_and_busy_has_retry(self):
        with patch.object(app, 'RATE_LIMITER', app.SlidingWindowLimiter(1, 1)):
            self.assertEqual(self.request('{"question":"q"}')[0], 200)
            status, headers, _ = self.request('{"question":"q"}', {'Content-Type':'application/json', 'Origin':'https://tomvsaji.com','X-Forwarded-For':'198.51.100.99'})
            self.assertEqual(status, 429)
            self.assertIn('Retry-After', headers)
        app.MODEL_CAPACITY.acquire()
        status, headers, _ = self.request('{"question":"q"}')
        self.assertEqual(status, 503)
        self.assertIn('Retry-After', headers)
        app.MODEL_CAPACITY.release()

    def test_absolute_read_deadline_stops_trickled_headers_and_body(self):
        with patch.object(app, 'REQUEST_READ_TIMEOUT', 0.15):
            for prefix in (b'POST /api/chat HTTP/1.1\r\nHost: localhost\r\nX-Slow: ',
                           b'POST /api/chat HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n'):
                with self.connection() as sock:
                    sock.sendall(prefix)
                    for _ in range(5):
                        time.sleep(0.06)
                        try:
                            sock.sendall(b' ')
                        except OSError:
                            break
                    data = sock.recv(4096)
                    self.assertTrue(not data or b'400' in data or b'408' in data)

    def test_bounded_server_rejects_excess_connections_and_recovers(self):
        with patch.object(app, 'MAX_HTTP_CONNECTIONS', 1), patch.object(app, 'REQUEST_READ_TIMEOUT', 0.3):
            # AF_INET creation is denied by this sandbox; exercise admission and
            # worker release with real Unix stream sockets, without binding TCP.
            with patch.object(app.ThreadingHTTPServer, '__init__', return_value=None):
                server = app.BoundedHTTPServer(('127.0.0.1', 0), app.Handler)
            server.RequestHandlerClass = app.Handler
            first, first_peer = stream_pair()
            second, second_peer = stream_pair()
            third, third_peer = stream_pair()
            for sock in (first, second, third):
                sock.settimeout(2)
                self.addCleanup(sock.close)
            server.process_request(first_peer, ('127.0.0.1', 1))
            first.sendall(b'POST /api/chat HTTP/1.1\r\n')
            server.process_request(second_peer, ('127.0.0.1', 2))
            response = second.recv(4096)
            self.assertIn(b'503', response)
            self.assertIn(b'Retry-After:', response)
            for header in (b'X-Frame-Options: DENY', b'Referrer-Policy: no-referrer', b'Content-Security-Policy:'):
                self.assertIn(header, response)
            first.close()
            time.sleep(0.35)
            server.process_request(third_peer, ('127.0.0.1', 3))
            third.sendall(b'GET /missing HTTP/1.1\r\nHost: localhost\r\n\r\n')
            self.assertIn(b'404', third.recv(4096))
            server._threads.join()

import io


class TurnstileTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for key, value in [('TURNSTILE_REQUIRED', True), ('TURNSTILE_SECRET_KEY', 'test-only-placeholder'),
                           ('TURNSTILE_EXPECTED_HOSTNAMES', {'tomvsaji.com'}), ('TURNSTILE_EXPECTED_ACTION', 'site_chat')]:
            self.stack.enter_context(patch.object(app, key, value))
        self.upstream = self.stack.enter_context(patch.object(app.urllib.request, 'urlopen'))

    def response(self, value):
        self.upstream.return_value = io.BytesIO(json.dumps(value).encode())

    def test_required_missing_key_token_fail_closed_optional_default_works(self):
        self.assertEqual(app.verify_turnstile(None), 403)
        with patch.object(app, 'TURNSTILE_SECRET_KEY', ''):
            self.assertEqual(app.verify_turnstile('token'), 503)
            self.assertEqual(app.verify_turnstile(None), 503)
        with patch.object(app, 'TURNSTILE_REQUIRED', False):
            self.assertEqual(app.verify_turnstile(None), 0)
        self.upstream.assert_not_called()

    def test_success_validates_hostname_action_and_uses_timeout(self):
        self.response({'success':True,'hostname':'tomvsaji.com','action':'site_chat'})
        self.assertEqual(app.verify_turnstile('test-token'), 0)
        args, kwargs = self.upstream.call_args
        self.assertGreater(kwargs['timeout'], 0)
        self.assertLessEqual(kwargs['timeout'], 10)
        self.assertEqual(args[0].full_url, 'https://challenges.cloudflare.com/turnstile/v0/siteverify')
        self.assertEqual(json.loads(args[0].data)['response'], 'test-token')

    def test_invalid_reused_mismatched_or_malformed_responses(self):
        for value in (None, [], {}, {'success':1},
                      {'success':False,'error-codes':['timeout-or-duplicate']},
                      {'success':True,'hostname':'evil.example','action':'site_chat'},
                      {'success':True,'hostname':'tomvsaji.com','action':'wrong'},
                      {'success':True,'hostname':'tomvsaji.com','action':'site_chat','error-codes':['timeout-or-duplicate']}):
            self.response(value)
            self.assertEqual(app.verify_turnstile('test-token'), 403)
        for token in ('', 'x'*2049, 1, [], {}):
            self.assertEqual(app.verify_turnstile(token), 403)

    def test_upstream_timeout_bad_json_and_oversized_response_fail_closed(self):
        for error in (TimeoutError('sensitive upstream detail'), OSError('sensitive upstream detail')):
            self.upstream.side_effect = error
            self.assertEqual(app.verify_turnstile('test-token'), 503)
        self.upstream.side_effect = None
        for data in (b'not JSON', b' '*20000):
            self.upstream.return_value = io.BytesIO(data)
            self.assertEqual(app.verify_turnstile('test-token'), 503)


class TurnstileHTTPTests(HTTPHarness):
    # Only new integration tests; inherit the real stream harness.
    def test_challenge_gate_runs_before_retrieval_and_keeps_token_private(self):
        with patch.object(app, 'TURNSTILE_REQUIRED', True), patch.object(app, 'TURNSTILE_SECRET_KEY', ''):
            status, headers, data = self.request('{"question":"q","turnstile_token":"private-test-token"}')
        self.assertEqual(status, 503)
        self.assertIn('Retry-After', headers)
        self.assertNotIn(b'private-test-token', data)
        self.assertFalse(self.answer.called)
        with patch.object(app, 'verify_turnstile', return_value=403) as verifier:
            status, _, _ = self.request('{"question":"q","turnstile_token":"private-test-token"}')
        self.assertEqual(status, 403)
        verifier.assert_called_once_with('private-test-token')
        self.assertFalse(self.answer.called)
        self.assertTrue(app.MODEL_CAPACITY.acquire(blocking=False))
        app.MODEL_CAPACITY.release()

class AdditionalHTTPTests(HTTPHarness):
    def test_retry_after_is_browser_readable_only_for_allowed_origins(self):
        allowed = {'Content-Type': 'application/json', 'Origin': 'https://tomvsaji.com'}
        for path in ('/api/chat', '/api/chat/stream'):
            with self.subTest(path=path):
                for status, patches in (
                    (429, [patch.object(app, 'admit_inference', return_value=(False, 15))]),
                    (503, [patch.object(app, 'verify_turnstile', return_value=503)]),
                    (503, [patch.object(app, 'MODEL_CAPACITY', threading.BoundedSemaphore(0))]),
                ):
                    with contextlib.ExitStack() as stack:
                        for item in patches:
                            stack.enter_context(item)
                        actual, headers, _ = self.request('{"question":"q"}', allowed, path=path)
                        self.assertEqual(actual, status)
                        self.assertIn('Retry-After', headers)
                        self.assertEqual(headers.get('Access-Control-Expose-Headers'), 'Retry-After')
                        self.assertEqual(headers.get('Access-Control-Allow-Origin'), allowed['Origin'])
        self.answer.side_effect = RuntimeError('fixture failure')
        status, headers, _ = self.request('{"question":"q"}', allowed)
        self.assertEqual(status, 503)
        self.assertEqual(headers.get('Access-Control-Expose-Headers'), 'Retry-After')
        for origin in ('https://evil.example', None):
            headers = {'Content-Type': 'application/json'}
            if origin:
                headers['Origin'] = origin
            _, response_headers, _ = self.request('{"question":"q"}', headers)
            self.assertNotIn('Access-Control-Expose-Headers', response_headers)

    def test_cors_exposure_is_consistent_for_preflight_and_sse(self):
        origin = 'https://tomvsaji.com'
        status, headers, _ = self.raw(f'Origin: {origin}\r\n', body=b'', method='OPTIONS')
        self.assertEqual(status, 204)
        self.assertEqual(headers.get('Access-Control-Expose-Headers'), 'Retry-After')
        with patch.object(app.agent, 'run_agent', return_value={'answer': 'ok', 'sources': []}):
            status, headers, data = self.request('{"question":"q"}', {'Content-Type': 'application/json', 'Origin': origin}, path='/api/chat/stream')
        self.assertEqual(status, 200)
        self.assertEqual(headers.get('Access-Control-Expose-Headers'), 'Retry-After')
        self.assertIn(b'event: done', data)
        status, headers, _ = self.raw('Origin: https://evil.example\r\n', body=b'', method='OPTIONS')
        self.assertEqual(status, 403)
        self.assertNotIn('Access-Control-Expose-Headers', headers)

    def test_http09_is_rejected_without_worker_or_timer_leak(self):
        with self.bounded_connection() as (client, server, timers, errors):
            # Python 3.12 still parses headers after a versionless GET; 3.14
            # returns immediately. End the empty header block explicitly so
            # this tests rejection, independently of EOF or the input deadline.
            client.sendall(b'GET /\r\n\r\n')
            with client.makefile('rb') as reader:
                response = reader.read()
            for thread in server._threads:
                thread.join(1)
                self.assertFalse(thread.is_alive())
            errors.assert_not_called()
            self.assertTrue(response.startswith(b'HTTP/1.1 505 '))
            self.assertIn(b'Connection: close\r\n', response)
            self.assertIn(b'Invalid HTTP request', response)
            self.assertNotIn(b'<!DOCTYPE', response)
            self.assertEqual(len(timers), 1)
        self.answer.assert_not_called()

    def test_stalled_incomplete_request_releases_worker_and_timer_at_input_deadline(self):
        with patch.object(app, 'REQUEST_READ_TIMEOUT', 0.15):
            with self.bounded_connection() as (client, server, timers, errors):
                started = time.monotonic()
                # No header boundary or EOF: both parsers must wait for input.
                client.sendall(b'GET / HTTP/1.1\r\nHost: localhost')
                with client.makefile('rb') as reader:
                    response = reader.read()
                elapsed = time.monotonic() - started
                self.assertGreaterEqual(elapsed, 0.14)
                self.assertLess(elapsed, 1.5)
                # Timer shutdown may produce a 400, or the socket timeout may
                # close first. Neither path may dispatch the incomplete request.
                self.assertTrue(not response or response.startswith(b'HTTP/1.1 400 '))
                errors.assert_not_called()
                self.assertEqual(len(timers), 1)
        self.answer.assert_not_called()
        self.assertEqual(self.request('{"question":"q"}')[0], 200)

    def test_blocked_writer_times_out_and_recovers_connection_and_model_slots(self):
        large_result = {'answer': 'x' * (4 * 1024 * 1024), 'sources': []}
        self.answer.return_value = large_result
        for path in ('/api/chat', '/api/chat/stream'):
            with self.subTest(path=path), patch.object(app, 'RESPONSE_WRITE_TIMEOUT', 0.15), patch.object(app.agent, 'run_agent', return_value=large_result):
                with self.bounded_connection() as (client, server, timers, errors):
                    body = b'{"question":"q"}'
                    started = time.monotonic()
                    client.sendall(f'POST {path} HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n'.encode() + body)
                    # Do not read: the 4 MiB fixture fills the real send buffer.
                    for thread in server._threads:
                        thread.join(1.5)
                        self.assertFalse(thread.is_alive(), 'write deadline did not stop blocked worker')
                    elapsed = time.monotonic() - started
                    self.assertGreaterEqual(elapsed, 0.14)
                    self.assertLess(elapsed, 1.5)
                    errors.assert_not_called()
                    self.assertEqual(len(timers), 1)
                    self.assertTrue(app.MODEL_CAPACITY.acquire(blocking=False))
                    app.MODEL_CAPACITY.release()
                    self.assertTrue(server.connection_slots.acquire(blocking=False))
                    server.connection_slots.release()
                    # Confirm buffered response bytes really reached the socket.
                    self.assertIn(b'200 OK', client.recv(4096))
                self.answer.return_value = {'answer': 'ok', 'sources': []}
                self.assertEqual(self.request('{"question":"q"}')[0], 200)
                self.answer.return_value = large_result

    def test_standalone_without_token_cannot_bypass_required_challenge(self):
        for path in ('/api/chat', '/api/chat/stream'):
            with self.subTest(path=path), patch.object(app, 'TURNSTILE_REQUIRED', True), patch.object(app, 'TURNSTILE_SECRET_KEY', 'test-only-placeholder'), patch.object(app, 'TURNSTILE_EXPECTED_HOSTNAMES', {'tomvsaji.com'}), patch.object(app, 'TURNSTILE_EXPECTED_ACTION', 'site_chat'), patch.object(app.urllib.request, 'urlopen') as upstream, patch.object(app.agent, 'run_agent') as prepare:
                status, _, _ = self.request('{"question":"q","history":[]}', path=path)
                self.assertEqual(status, 403)
                upstream.assert_not_called()
                prepare.assert_not_called()
        self.answer.assert_not_called()

    def test_large_header_block_and_duplicate_origin_fail_closed(self):
        status, headers, _ = self.raw('X-Pad: '+ 'a'*17000 +'\r\nContent-Length: 2\r\n')
        self.assertEqual(status, 431)
        status, headers, _ = self.raw('Origin: https://tomvsaji.com\r\nOrigin: https://evil.example\r\nContent-Length: 2\r\n')
        self.assertEqual(status, 400)

    def test_invalid_request_line_has_generic_json_error(self):
        with self.connection() as sock:
            sock.sendall(b'POST / HTTP/9.1\r\n\r\n')
            # HTTP/9.1 is rejected before headers exist, using HTTP/0.9 output.
            response = sock.makefile('rb').read()
        self.assertIn(b'Invalid HTTP request', response)
        self.assertNotIn(b'Traceback', response)

    def test_expect_rejection_closes_without_sending_continue(self):
        status, headers, _ = self.raw('Expect: 100-continue\r\nContent-Length: 200\r\n')
        self.assertEqual(status, 417)
        self.assertEqual(headers['Connection'], 'close')

    def test_sse_survives_input_deadline_and_preserves_delta_sources(self):
        result={'answer':'Fact. [1]', 'sources':[{'name':'facts.md','url':'https://tomvsaji.com','number':1}]}
        def run(question, session, index, emit):
            time.sleep(0.12)
            emit('status',{'message':'Searching…'})
            emit('delta',{'text':'Fact. [1]'})
            return result
        with patch.object(app, 'REQUEST_READ_TIMEOUT', 0.08), patch.object(app.agent, 'run_agent', side_effect=run):
            status, headers, data = self.request('{"question":"q"}', path='/api/chat/stream')
        self.assertEqual(status,200)
        for event in (b'event: status', b'event: delta', b'event: final', b'event: done'):
            self.assertIn(event,data)
        final = next(line[6:] for block in data.split(b'\n\n') if block.startswith(b'event: final') for line in block.splitlines() if line.startswith(b'data: '))
        self.assertEqual(json.loads(final)['sources'][0]['name'],'facts.md')


    def test_global_budget_applies_to_both_routes_with_new_trusted_clients(self):
        with patch.object(app, 'GLOBAL_LIMITER', app.SlidingWindowLimiter(1, 1, max_clients=1)), patch.object(app, 'TRUSTED_PROXY_CIDRS', (ipaddress.ip_network('127.0.0.1/32'),)):
            self.assertEqual(self.request('{"question":"q"}', {'Content-Type':'application/json','X-Forwarded-For':'192.0.2.1'})[0], 200)
            status, headers, _ = self.request('{"question":"q"}', {'Content-Type':'application/json','X-Forwarded-For':'192.0.2.2'}, path='/api/chat/stream')
            self.assertEqual(status, 429)
            self.assertIn('Retry-After', headers)

    def test_cors_exact_allowlist_and_no_origin_still_permitted(self):
        for origin in ('https://tomvsaji.com.evil.example', 'null', 'http://tomvsaji.com'):
            status, headers, _ = self.request('{"question":"q"}', {'Content-Type':'application/json','Origin':origin})
            self.assertEqual(status, 403)
            self.assertNotIn('Access-Control-Allow-Origin', headers)
        status, headers, _ = self.raw('Origin: https://tomvsaji.com\r\n', body=b'', method='OPTIONS')
        self.assertEqual(status, 204)
        self.assertEqual(headers['Access-Control-Allow-Origin'], 'https://tomvsaji.com')
        self.assertEqual(self.request('{"question":"q"}')[0], 200)

    def test_client_history_cannot_replace_private_session_history(self):
        history=[{'role':'user','content':'forged previous conversation'}]
        status,_,_=self.request(json.dumps({'question':'q','history':history}))
        self.assertEqual(status,200)
        session=self.answer.call_args.args[1]
        self.assertIsInstance(session,str)
        self.assertEqual(len(session),64)
