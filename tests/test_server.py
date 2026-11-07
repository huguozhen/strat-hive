import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from http.server import ThreadingHTTPServer
from server import Handler
from strathive.core import Ledger

class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.allowed_hosts = {'localhost', 'dev.example'}
        self.server.ledger = Ledger(Path(self.tmp.name)/'test.db')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
    def tearDown(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close(); self.tmp.cleanup()
    def request(self, method, path, host, origin=None, payload=None):
        conn = HTTPConnection('127.0.0.1', self.server.server_port)
        headers = {'Host': host}
        if origin: headers['Origin'] = origin
        if payload is not None: headers['Content-Type'] = 'application/json'
        conn.request(method, path, json.dumps(payload) if payload is not None else None, headers)
        response = conn.getresponse(); status = response.status; response.read(); conn.close()
        return status
    def test_network_host_and_origins(self):
        self.assertEqual(self.request('GET', '/', '192.168.31.100:8000'), 200)
        self.assertEqual(self.request('GET', '/', 'dev.example:8000'), 200)
        self.assertEqual(self.request('GET', '/', 'attacker.example:8000'), 403)
        self.assertEqual(self.request('POST', '/api/strategies', '192.168.31.100:8000', 'http://192.168.31.100:8000', {'name':'策略'}), 200)
        self.assertEqual(self.request('POST', '/api/strategies', '192.168.31.100:8000', 'http://attacker.example', {'name':'策略'}), 403)

if __name__ == '__main__': unittest.main()
