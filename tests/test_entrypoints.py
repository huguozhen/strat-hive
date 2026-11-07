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

    def test_web_rejects_untrusted_host(self):
        import urllib.error
        with tempfile.TemporaryDirectory() as d:
            server=make_server('127.0.0.1',0,str(Path(d)/'ledger.db'))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                for method in ('GET','POST'):
                    req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/account.list',data=b'{}' if method=='POST' else None,headers={'Host':f'evil.example:{server.server_port}'},method=method)
                    with self.assertRaises(urllib.error.HTTPError) as err: urllib.request.urlopen(req)
                    self.assertEqual(err.exception.code,403)
                    err.exception.close()
            finally:
                server.shutdown();server.server_close();thread.join()

    def test_web_trade_visible_to_cli_and_rejected_edits_keep_state(self):
        import urllib.error
        from strathive.ledger import Ledger
        with tempfile.TemporaryDirectory() as d:
            db=str(Path(d)/'ledger.db'); l=Ledger(db)
            l.execute('catalog.import',{'source':'test','securities':[{'code':'510300','name':'基金','type':'etf'}]})
            a=l.execute('account.create',{'name':'账户','cash':'1000','commission_rate':'0','opened_at':'2025-01-01T00:00'})['id']
            s=l.execute('strategy.create',{'name':'策略'})['id']
            p=l.execute('pool.create',{'name':'池','account_id':a})['id']
            r=l.execute('run.create',{'name':'运行','strategy_id':s,'pool_id':p})['id']
            l.execute('cash.move',{'account_id':a,'pool_id':p,'direction':'in','amount':'1000','time':'2026-01-01T09:00'})
            server=make_server('127.0.0.1',0,db); thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            def post(action,data):
                req=urllib.request.Request(base+'/api/'+action,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'},method='POST')
                return urllib.request.urlopen(req)
            try:
                with post('trade.create',{'run_id':r,'security':'510300','direction':'buy','time':'2026-01-02T10:00','price':'2','quantity':100,'commission':'0','stamp_tax':'0','transfer_fee':'0'}) as response:
                    self.assertIn('暂停',json.load(response)['warning'])
                result=subprocess.run([sys.executable,'-m','strathive','--db',db,'trade.list'],capture_output=True,text=True,check=True)
                self.assertEqual(json.loads(result.stdout)[0]['amount'],'200')
                with self.assertRaises(urllib.error.HTTPError) as err:
                    post('trade.update',{'id':1,'quantity':1000})
                self.assertIn('现金不足',json.load(err.exception)['error'])
                err.exception.close()
                self.assertEqual(l.execute('account.get',{'id':a})['cash'],'800.00')
            finally:
                server.shutdown();server.server_close();thread.join()

    def test_reconciliation_through_web_and_cli_import(self):
        from strathive.ledger import Ledger
        import urllib.error
        with tempfile.TemporaryDirectory() as d:
            db=str(Path(d)/'ledger.db'); ledger=Ledger(db)
            account=ledger.execute('account.create',{'name':'券商','commission_rate':'0','cash':'50'})['id']
            payload={'account_id':account,'broker':{'cash':'50','holdings':[{'code':'000001','quantity':10,'cost':'-1.2345'}]}}
            path=Path(d)/'broker.json'; path.write_text(json.dumps(payload),encoding='utf-8')
            before=ledger.execute('account.get',{'id':account})
            result=subprocess.run([sys.executable,'-m','strathive','--db',db,'reconcile','--json','@'+str(path)],capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(result.stdout)['broker_comparison']['unverified_codes'],['000001'])
            self.assertEqual(json.loads(result.stdout)['broker_comparison']['holdings'][0]['status'],'extra')
            self.assertEqual(json.loads(result.stdout)['broker_comparison']['holdings'][0]['broker']['cost'],'-1.2345')
            server=make_server('127.0.0.1',0,db); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            try:
                with urllib.request.urlopen(base+'/') as response:
                    self.assertIn('即时对账',response.read().decode())
                request=urllib.request.Request(base+'/api/reconcile',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'},method='POST')
                with urllib.request.urlopen(request) as response:
                    comparison=json.load(response)['broker_comparison']
                    self.assertEqual(comparison['unverified_codes'],['000001'])
                    self.assertEqual(comparison['holdings'][0]['status'],'extra')
                    self.assertEqual(comparison['holdings'][0]['broker']['cost'],'-1.2345')
                bad={'account_id':account,'broker':{'cash':'-1','holdings':[]}}
                request=urllib.request.Request(base+'/api/reconcile',data=json.dumps(bad).encode(),headers={'Content-Type':'application/json'},method='POST')
                with self.assertRaises(urllib.error.HTTPError) as err: urllib.request.urlopen(request)
                self.assertIn('现金',json.load(err.exception)['error']); err.exception.close()
                self.assertEqual(ledger.execute('account.get',{'id':account}),before)
            finally:
                server.shutdown(); server.server_close(); thread.join()
