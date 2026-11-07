import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from strathive.web import make_server

class EntryPointsTest(unittest.TestCase):
    def test_cli_and_web_share_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / 'ledger.db')
            cmd = [sys.executable, '-m', 'strathive', '--db', db, 'account.create', '--json', '{"name":"共享账户","commission_rate":"0.0003","cash":"123.45"}']
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout)['id'], 1)
            server = make_server('127.0.0.1', 0, db)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/api/account.list') as response:
                    accounts = json.load(response)
                self.assertEqual(accounts[0]['name'], '共享账户')
                self.assertEqual(accounts[0]['cash'], '123.45')
            finally:
                server.shutdown(); server.server_close(); thread.join()

    def test_web_operation_is_visible_to_cli_and_reports_errors(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / 'ledger.db')
            server = make_server('127.0.0.1', 0, db)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                with urllib.request.urlopen(base+'/') as response:
                    self.assertIn('策略蜂巢',response.read().decode())
                request=urllib.request.Request(base+'/api/account.create',data=json.dumps({'name':'网页账户','cash':'50','commission_rate':'0.0003'}).encode(),headers={'Content-Type':'application/json'},method='POST')
                with urllib.request.urlopen(request) as response: self.assertEqual(json.load(response)['id'],1)
                result=subprocess.run([sys.executable,'-m','strathive','--db',db,'account.get','--json','{"id":1}'],capture_output=True,text=True,check=True)
                self.assertEqual(json.loads(result.stdout)['cash'],'50.00')
                bad=subprocess.run([sys.executable,'-m','strathive','--db',db,'cash.move','--json','{"account_id":1,"direction":"out","amount":"51"}'],capture_output=True,text=True)
                self.assertNotEqual(bad.returncode,0)
                self.assertIn('未分配现金不足',json.loads(bad.stderr)['error'])
            finally:
                server.shutdown();server.server_close();thread.join()
