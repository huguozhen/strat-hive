import tempfile
import unittest
from pathlib import Path
from strathive.ledger import Ledger, LedgerError


class ReconciliationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = Ledger(Path(self.tmp.name) / 'ledger.db')
        self.ledger.execute('catalog.import', {'source':'test', 'securities':[
            {'code':'600000','name':'浦发银行','type':'stock'},
            {'code':'510300','name':'沪深300ETF','type':'etf'}]})
        self.account = self.ledger.execute('account.create', {'name':'账户','cash':'1000','commission_rate':'0','opened_at':'2025-01-01T00:00'})['id']
        strategy = self.ledger.execute('strategy.create', {'name':'策略'})['id']
        self.pool = self.ledger.execute('pool.create', {'account_id':self.account,'name':'池'})['id']
        self.run = self.ledger.execute('run.create', {'strategy_id':strategy,'pool_id':self.pool,'name':'运行'})['id']
        self.ledger.execute('initial.record', {'account_id':self.account,'holdings':[{'security':'600000','quantity':100,'cost':'10','date':'2025-01-01','allocations':[{'pool_id':self.pool,'quantity':100}]}]})

    def test_matching_broker_and_pool_state_does_not_change_ledger(self):
        before = self.ledger.execute('account.get', {'id':self.account})
        result = self.ledger.execute('reconcile', {'account_id':self.account,'broker':{'cash':'1000','holdings':[{'code':'600000','quantity':100,'cost':'10'}]}})
        self.assertTrue(result['matches'])
        self.assertTrue(result['broker_comparison']['matches'])
        self.assertTrue(result['allocation_comparison']['matches'])
        self.assertEqual(self.ledger.execute('account.get', {'id':self.account}),before)

    def test_broker_differences_and_securities_missing_or_extra(self):
        broker={'cash':'999.50','holdings':[{'code':'600000','quantity':90,'cost':'10.25'},{'code':'510300','quantity':10,'cost':'2'}]}
        result=self.ledger.execute('reconcile',{'account_id':self.account,'broker':broker})
        self.assertFalse(result['matches'])
        self.assertEqual(result['broker_comparison']['cash']['difference'],'-0.50')
        rows={h['code']:h for h in result['broker_comparison']['holdings']}
        self.assertEqual(rows['510300']['status'],'extra')
        self.assertEqual(rows['600000']['status'],'different')
        self.assertEqual(rows['600000']['quantity_difference'],-10)
        self.assertEqual(rows['600000']['cost_difference'],'0.2500')
        missing=self.ledger.execute('reconcile',{'account_id':self.account,'broker':{'cash':'1000','holdings':[]}})
        self.assertEqual(missing['broker_comparison']['holdings'][0]['status'],'missing')

    def test_zero_values_and_invalid_broker_state(self):
        zero=self.ledger.execute('account.create',{'name':'空账户','cash':'0','commission_rate':'0'})['id']
        result=self.ledger.execute('reconcile',{'account_id':zero,'broker':{'cash':'0','holdings':[{'code':'600000','quantity':0,'cost':0}]}})
        self.assertTrue(result['matches'])
        before=self.ledger.execute('account.get',{'id':self.account})
        invalid=[{'cash':'-1','holdings':[]},{'cash':'0','holdings':[{'code':'600000','quantity':-1,'cost':0}]},
                 {'cash':'0','holdings':[{'code':'600000','quantity':1,'cost':'-1'}]},
                 {'cash':'0','holdings':[{'code':'999999','quantity':1,'cost':0}]},
                 {'cash':'0','holdings':[{'code':'600000','quantity':0,'cost':0},{'code':'600000','quantity':1,'cost':0}]},
                 {'cash':'0','holdings':[None]},{'cash':'0','holdings':None}, {'holdings':[]}]
        for broker in invalid:
            with self.subTest(broker=broker), self.assertRaises(LedgerError): self.ledger.execute('reconcile',{'account_id':self.account,'broker':broker})
        self.assertEqual(self.ledger.execute('account.get',{'id':self.account}),before)

    def test_allocation_summary_shows_holdings_and_cash(self):
        second=self.ledger.execute('pool.create',{'account_id':self.account,'name':'另一个池'})['id']
        self.ledger.execute('cash.move',{'account_id':self.account,'pool_id':self.pool,'direction':'in','amount':'300','time':'2025-01-02T09:00'})
        result=self.ledger.execute('reconcile',{'account_id':self.account,'broker':{'cash':'1000','holdings':[{'code':'600000','quantity':100,'cost':'10'}]}})
        allocation=result['allocation_comparison']
        self.assertEqual((allocation['account_cash'],allocation['pool_cash'],allocation['unallocated_cash']),('1000.00','300.00','700.00'))
        self.assertEqual(allocation['holdings'][0]['account_quantity'],100)
        self.assertEqual(allocation['holdings'][0]['pool_quantity'],100)
        self.assertEqual(allocation['holdings'][0]['account_cost'],'10.0000')
        self.assertEqual(allocation['holdings'][0]['pool_cost'],'10.0000')
        self.assertEqual(len(allocation['pools']),2)

    def test_complete_mvp_flow_reconciles_after_funding_and_trades(self):
        l=self.ledger
        cash_only=l.execute('account.create',{'name':'纯现金','cash':'0','commission_rate':'0'})['id']
        self.assertTrue(l.execute('reconcile',{'account_id':cash_only,'broker':{'cash':0,'holdings':[]}})['matches'])
        second=l.execute('pool.create',{'account_id':self.account,'name':'另一个池'})['id']
        # Existing account already has 100 shares in the first pool; trade both directions.
        l.execute('cash.move',{'account_id':self.account,'direction':'in','amount':'500','time':'2025-01-02T09:00'})
        l.execute('cash.move',{'account_id':self.account,'pool_id':self.pool,'direction':'in','amount':'800','time':'2025-01-02T09:01'})
        l.execute('cash.move',{'account_id':self.account,'direction':'out','amount':'100','time':'2025-01-02T09:02'})
        l.execute('trade.create',{'run_id':self.run,'security':'510300','direction':'buy','time':'2025-01-02T10:00','price':'5','quantity':100,'commission':'2','stamp_tax':'0','transfer_fee':'0'})
        l.execute('trade.create',{'run_id':self.run,'security':'510300','direction':'sell','time':'2025-01-03T10:00','price':'6','quantity':20,'commission':'1','stamp_tax':'0','transfer_fee':'0'})
        before=l.execute('account.get',{'id':self.account}); trades=l.execute('trade.list',{})
        broker={'cash':'1017','holdings':[{'code':'600000','quantity':100,'cost':'10'},{'code':'510300','quantity':80,'cost':'4.7875'}]}
        result=l.execute('reconcile',{'account_id':self.account,'broker':broker})
        self.assertTrue(result['matches'])
        self.assertEqual((result['allocation_comparison']['pool_cash'],result['allocation_comparison']['unallocated_cash']),('417.00','600.00'))
        self.assertEqual(l.execute('reconcile',{'account_id':self.account,'broker':broker}),result)
        self.assertEqual(l.execute('account.get',{'id':self.account}),before)
        self.assertEqual(l.execute('trade.list',{}),trades)

    def test_split_position_uses_weighted_cost_and_identifies_cost_difference(self):
        l=self.ledger
        account=l.execute('account.create',{'name':'跨池账户','cash':'1000','commission_rate':'0','opened_at':'2025-01-01T00:00'})['id']
        strategy=l.execute('strategy.create',{'name':'跨池策略'})['id']
        pools=[l.execute('pool.create',{'account_id':account,'name':name})['id'] for name in ('一池','二池')]
        runs=[l.execute('run.create',{'strategy_id':strategy,'pool_id':p,'name':str(p)})['id'] for p in pools]
        l.execute('initial.record',{'account_id':account,'holdings':[{'security':'600000','quantity':100,'cost':'10','date':'2025-01-01','allocations':[{'pool_id':pools[0],'quantity':40},{'pool_id':pools[1],'quantity':60}]}]})
        l.execute('cash.move',{'account_id':account,'pool_id':pools[0],'direction':'in','amount':'1000','time':'2025-01-02T09:00'})
        l.execute('trade.create',{'run_id':runs[0],'security':'600000','direction':'buy','time':'2025-01-02T10:00','price':'5','quantity':100,'commission':'0','stamp_tax':'0','transfer_fee':'0'})
        result=l.execute('reconcile',{'account_id':account,'broker':{'cash':'500','holdings':[{'code':'600000','quantity':200,'cost':'7.6'}]}})
        row=result['broker_comparison']['holdings'][0]
        self.assertEqual((row['status'],row['quantity_difference'],row['cost_difference']),('different',0,'0.1000'))
        allocation=result['allocation_comparison']
        self.assertEqual((allocation['holdings'][0]['pool_cost'],allocation['holdings'][0]['pool_quantity']),('7.5000',200))
        self.assertEqual([p['holdings'][0]['quantity'] for p in allocation['pools']],[140,60])
        self.assertIn('同花顺',result['cost_note'])

    def test_account_and_pool_cost_cycles_can_differ_without_breaking_allocation(self):
        l=self.ledger
        account=l.execute('account.create',{'name':'分池','cash':'0','commission_rate':'0','opened_at':'2025-01-01T00:00'})['id']
        strategy=l.execute('strategy.create',{'name':'策略二'})['id']
        pools=[l.execute('pool.create',{'account_id':account,'name':name})['id'] for name in ('池一','池二')]
        runs=[l.execute('run.create',{'strategy_id':strategy,'pool_id':p,'name':str(p)})['id'] for p in pools]
        l.execute('initial.record',{'account_id':account,'holdings':[{'security':'600000','quantity':200,'cost':'10','date':'2025-01-01','allocations':[{'pool_id':pools[0],'quantity':100},{'pool_id':pools[1],'quantity':100}]}]})
        l.execute('trade.create',{'run_id':runs[0],'security':'600000','direction':'sell','time':'2025-01-02T10:00','price':'12','quantity':100,'commission':'0','stamp_tax':'0','transfer_fee':'0'})
        broker={'cash':'1200','holdings':[{'code':'600000','quantity':100,'cost':'8'}]}
        result=l.execute('reconcile',{'account_id':account,'broker':broker})
        self.assertTrue(result['matches'])
        row=result['allocation_comparison']['holdings'][0]
        self.assertEqual((row['account_cost'],row['pool_cost']),('8.0000','10.0000'))
        self.assertTrue(row['matches'])
        self.assertFalse(row['cost_matches'])
        self.assertEqual(row['cost_difference'],'-2.0000')
