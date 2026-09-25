import concurrent.futures
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from openrouterproxy import ProxyHandler, ThreadingHTTPServer


class Upstream(ProxyHandler):
    protocol_version = 'HTTP/1.1'
    first = b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
    release = threading.Event()

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/error':
            self.send_error(429)
            return
        if self.path == '/stream':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            self.wfile.write(f'{len(self.first):x}\r\n'.encode() + self.first + b'\r\n')
            self.wfile.flush()
            self.release.wait(3)
            self.wfile.write(b'e\r\ndata: [DONE]\n\n\r\n0\r\n\r\n')
            self.wfile.flush()
            return
        self.send_response(200)
        self.send_header('Content-Length', '2')
        self.end_headers()
        self.wfile.write(b'{}')

    def log_message(self, *args):
        pass


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        handler = type('TestProxy', (ProxyHandler,), {
            'upstream': f'http://127.0.0.1:{self.upstream.server_port}',
            'log_dir': Path(self.tmp.name), 'upstream_timeout': 2,
        })
        self.proxy = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        for server in (self.upstream, self.proxy):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def tearDown(self):
        Upstream.release.set()
        for server in (self.proxy, self.upstream):
            server.shutdown()
            server.server_close()
        self.tmp.cleanup()

    def client(self):
        return http.client.HTTPConnection('127.0.0.1', self.proxy.server_port, timeout=1)

    def test_stream_arrives_before_completion_and_reaches_eof(self):
        Upstream.release.clear()
        conn = self.client()
        try:
            conn.request('GET', '/stream')
            response = conn.getresponse()
            self.assertEqual(response.read(len(Upstream.first)), Upstream.first)
            Upstream.release.set()
            self.assertEqual(response.read(), b'data: [DONE]\n\n')
            self.assertEqual(response.getheader('Connection'), 'close')
        finally:
            conn.close()

    def test_concurrent_requests_have_unique_redacted_logs(self):
        def request(_):
            conn = self.client()
            try:
                conn.request('GET', '/', headers={'Authorization': 'Bearer secret'})
                self.assertEqual(conn.getresponse().read(), b'{}')
            finally:
                conn.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(request, range(16)))
        files = list(Path(self.tmp.name).glob('*.json'))
        self.assertEqual(len(files), 16)
        for path in files:
            self.assertNotIn('Bearer secret', path.read_text())
            self.assertEqual(json.loads(path.read_text())['response_status'], 200)

    def test_completed_stream_reaches_eof(self):
        Upstream.release.set()
        conn = self.client()
        try:
            conn.request('GET', '/stream')
            self.assertEqual(conn.getresponse().read(), Upstream.first + b'data: [DONE]\n\n')
        finally:
            conn.close()

    def test_chunked_request(self):
        conn = self.client()
        try:
            conn.request('POST', '/', iter([b'{', b'"ok":true}']), encode_chunked=True)
            self.assertEqual(conn.getresponse().read(), b'{"ok":true}')
        finally:
            conn.close()

    def test_upstream_http_error_finishes(self):
        conn = self.client()
        try:
            conn.request('GET', '/error')
            response = conn.getresponse()
            self.assertEqual(response.status, 429)
            self.assertTrue(response.read())
        finally:
            conn.close()

    def test_connection_failure_finishes(self):
        self.proxy.RequestHandlerClass.upstream = 'http://127.0.0.1:0'
        conn = self.client()
        try:
            conn.request('GET', '/')
            response = conn.getresponse()
            self.assertEqual(response.status, 502)
            self.assertTrue(response.read())
        finally:
            conn.close()


if __name__ == '__main__':
    unittest.main()
