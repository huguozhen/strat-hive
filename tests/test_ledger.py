import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from strathive.core import Ledger, Error

class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.ledger=Ledger(Path(self.tmp.name)/'ledger.db')
        self.pos={'code':'600000','name':'浦发银行','quantity':100,'cost':'10.1234','acquired_date':'2025-01-02'}
        self.opening={'strategy_name':'存量策略','pool_name':'存量池','run_name':'存量运行','cash':'10.00'}
    def create(self, positions=None, cash='100.00', opening=None):
        return self.ledger.account_create({'name':'账户','commission_rate':'0.0003','cash':cash,'positions':positions or [],'opening':opening})
    def test_cash_only_and_allocation(self):
        aid=self.create(); s=self.ledger.snapshot(); self.assertEqual((len(s['strategies']),len(s['runs']),len(s['pools'])),(0,0,0))
        pid=self.ledger.pool_create({'name':'池','account_id':aid,'cash':'70.00'})
        with self.assertRaises(Error): self.ledger.pool_create({'name':'池2','account_id':aid,'cash':'30.01'})
        self.ledger.transfer(pid,{'direction':'in','amount':'30.00'})
        with self.assertRaises(Error): self.ledger.transfer(pid,{'direction':'in','amount':'0.01'})
        self.ledger.transfer(pid,{'direction':'out','amount':'50.01'})
        s=self.ledger.snapshot();self.assertEqual(s['accounts'][0]['unallocated_cash'],'50.01');self.assertEqual(s['pools'][0]['cash'],'49.99')
        with self.assertRaises(Error): self.ledger.account_update(aid,{'name':'a','commission_rate':'0.1','cash':'49.98','positions':[]})
    def test_opening_is_atomic_and_correct(self):
        with self.assertRaises(Error): self.create([self.pos])
        with self.assertRaises(Error): self.create([self.pos],opening={**self.opening,'cash':'101'})
        self.assertFalse(self.ledger.snapshot()['accounts'])
        aid=self.create([self.pos],opening=self.opening);s=self.ledger.snapshot()
        self.assertEqual((len(s['strategies']),len(s['pools']),len(s['runs'])),(1,1,1)); self.assertEqual(s['runs'][0]['status'],'created')
        self.assertEqual(s['accounts'][0]['position_summary'][0]['allocated_quantity'],100)
        self.assertEqual(s['pools'][0]['positions'][0]['cost'],'10.1234')
        self.assertEqual(s['accounts'][0]['unallocated_cash'],'90.00')
    def test_update_holdings_and_new_opening(self):
        aid=self.create([self.pos],opening=self.opening)
        updated={**self.pos,'quantity':120,'cost':'11.4321'}
        new={'code':'510300','name':'沪深300ETF','quantity':20,'cost':'4.1','acquired_date':'2025-02-01'}
        data={'name':'更名','commission_rate':'0.001','cash':'150','positions':[updated,new]}
        with self.assertRaises(Error): self.ledger.account_update(aid,data)
        self.assertEqual(self.ledger.snapshot()['accounts'][0]['name'],'账户')
        self.ledger.account_update(aid,{**data,'opening':self.opening})
        s=self.ledger.snapshot();self.assertEqual(len(s['runs']),2)
        self.assertEqual(next(x for x in s['accounts'][0]['position_summary'] if x['code']=='600000')['allocated_quantity'],120)
        self.assertTrue(all(p['cost_match'] and p['unallocated_quantity']==0 for p in s['accounts'][0]['position_summary']))
    def test_run_binding_and_state_machine(self):
        a=self.create();p=self.ledger.pool_create({'name':'池','account_id':a,'cash':'0'});s=self.ledger.strategy_create({'name':'策略'})
        r=self.ledger.run_create({'name':'运行','strategy_id':s,'pool_id':p})
        with self.assertRaises(Error): self.ledger.run_create({'name':'重复','strategy_id':s,'pool_id':p})
        with self.assertRaises(Error): self.ledger.transition(r,{'status':'paused'})
        for status in ['running','paused','running','ended']: self.ledger.transition(r,{'status':status})
        with self.assertRaises(Error): self.ledger.transition(r,{'status':'running'})
        self.ledger.strategy_update(s,{'name':'新名','description':'描述'})
        self.assertEqual(self.ledger.snapshot()['runs'][0]['strategy_name'],'新名')
    def test_input_validation(self):
        for pos in [{**self.pos,'code':'300001'},{**self.pos,'quantity':0},{**self.pos,'cost':'NaN'},{**self.pos,'acquired_date':'2025-02-30'}]:
            with self.subTest(pos=pos),self.assertRaises(Error):self.create([pos],opening=self.opening)
        with self.assertRaises(Error):self.create([self.pos,self.pos],opening=self.opening)
        with self.assertRaises(Error): self.create(cash='0.001')
        with self.assertRaises(Error):self.ledger.account_create({'name':'x','commission_rate':'1.1','cash':'1'})
    def test_concurrent_pool_allocation_cannot_overallocate(self):
        aid=self.create()
        def allocate(i):
            try: self.ledger.pool_create({'name':str(i),'account_id':aid,'cash':'60'}); return True
            except Error: return False
        with ThreadPoolExecutor(max_workers=2) as ex: results=list(ex.map(allocate,range(2)))
        self.assertEqual(sorted(results),[False,True]);self.assertEqual(self.ledger.snapshot()['accounts'][0]['unallocated_cash'],'40.00')

if __name__=='__main__': unittest.main()
