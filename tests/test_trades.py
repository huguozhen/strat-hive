import tempfile
import unittest
from pathlib import Path
from strathive.ledger import Ledger, LedgerError

class TradeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'ledger.db'
        self.l = Ledger(self.path)
        self.l.execute('catalog.import', {'source':'test','securities':[{'code':'600000','name':'浦发银行','type':'stock'},{'code':'510300','name':'沪深300ETF','type':'etf'}]})
        self.a = self.l.execute('account.create', {'name':'账户','cash':'20000','commission_rate':'0.0003'})['id']
        s = self.l.execute('strategy.create', {'name':'策略'})['id']
        self.p = self.l.execute('pool.create', {'account_id':self.a,'name':'池'})['id']
        self.r = self.l.execute('run.create', {'strategy_id':s,'pool_id':self.p,'name':'运行'})['id']
        self.l.execute('cash.move', {'account_id':self.a,'pool_id':self.p,'direction':'in','amount':'15000','time':'2026-01-01T09:00:00'})

    def test_buy_records_fees_and_persists_assets(self):
        result = self.l.execute('trade.create', {'run_id':self.r,'security':'浦发银行','direction':'buy','time':'2026-01-02T10:00:00','price':'10','quantity':1000,'reason':'计划','note':'记录'})
        self.assertIn('暂停', result['warning'])
        trade = Ledger(self.path).execute('trade.list', {'run_id':self.r})[0]
        self.assertEqual((trade['amount'],trade['commission'],trade['stamp_tax'],trade['transfer_fee'],trade['fees']),('10000','5.00','0.00','0.10','5.10'))
        view = self.l.execute('account.get', {'id':self.a})
        self.assertEqual(view['cash'],'9994.90')
        self.assertEqual(view['pools'][0]['cash'],'4994.90')
        self.assertEqual(view['holdings'][0]['quantity'],1000)

    def test_history_changes_are_atomic_and_replay_funding(self):
        buy=self.l.execute('trade.create', {'run_id':self.r,'security':'600000','direction':'buy','time':'2026-01-02T10:00:00','price':'10','quantity':1000,'commission':'0','transfer_fee':'0','stamp_tax':'0'})['id']
        sell=self.l.execute('trade.create', {'run_id':self.r,'security':'600000','direction':'sell','time':'2026-01-03T10:00:00','price':'12','quantity':600,'commission':'0','transfer_fee':'0','stamp_tax':'0'})['id']
        before=self.l.execute('account.get', {'id':self.a})
        with self.assertRaisesRegex(LedgerError, '持仓'):
            self.l.execute('trade.update', {'id':buy,'quantity':500})
        with self.assertRaisesRegex(LedgerError, '持仓'):
            self.l.execute('trade.delete', {'id':buy})
        self.assertEqual(self.l.execute('account.get', {'id':self.a}),before)
        self.assertEqual(self.l.execute('trade.get', {'id':buy})['quantity'],1000)
        self.l.execute('trade.update', {'id':sell,'quantity':500})
        self.assertEqual(self.l.execute('account.get', {'id':self.a})['holdings'][0]['quantity'],500)
        with self.assertRaisesRegex(LedgerError, '现金不足'):
            self.l.execute('trade.create', {'run_id':self.r,'security':'600000','direction':'buy','time':'2025-12-31T10:00:00','price':'1','quantity':1,'commission':'0','transfer_fee':'0','stamp_tax':'0'})

    def test_sell_overrides_and_cost(self):
        self.l.execute('trade.create', {'run_id':self.r,'security':'600000','direction':'buy','time':'2026-01-02T10:00:00','price':'10','quantity':1000,'commission':'1','transfer_fee':'0','stamp_tax':'0'})
        result=self.l.execute('trade.create', {'run_id':self.r,'security':'600000','direction':'sell','time':'2026-01-03T10:00:00','price':'12','quantity':500,'commission':'2','transfer_fee':'0','stamp_tax':'3'})
        t=self.l.execute('trade.get', {'id':result['id']})
        self.assertEqual(t['fees'],'5.00')
        self.assertEqual(self.l.execute('account.get', {'id':self.a})['holdings'][0]['cost'],'8.0120')
        self.assertEqual(self.l.execute('account.get', {'id':self.a})['cash'],'15994.00')

    def test_fractional_cent_is_exact_and_invalid_security_rejected(self):
        self.l.execute('trade.create', {'run_id':self.r,'security':'510300','direction':'buy','time':'2026-01-02T10:00:00','price':'1.2345','quantity':1,'commission':'0','transfer_fee':'0','stamp_tax':'0'})
        self.assertEqual(self.l.execute('account.get', {'id':self.a})['cash'],'19998.7655')
        with self.assertRaisesRegex(LedgerError,'目录'):
            self.l.execute('trade.create', {'run_id':self.r,'security':'浦发','direction':'buy','time':'2026-01-02T10:00:00','price':'1','quantity':1})

    def test_ended_run_does_not_claim_rebound_pool_assets(self):
        self.l.execute('run.status', {'id':self.r,'status':'ended'})
        strategy=self.l.execute('strategy.create', {'name':'新策略'})['id']
        current=self.l.execute('run.create', {'strategy_id':strategy,'pool_id':self.p,'name':'新运行'})['id']
        old=self.l.execute('run.get', {'id':self.r})
        new=self.l.execute('run.get', {'id':current})
        self.assertIsNone(old['pool']['cash'])
        self.assertEqual(new['pool']['cash'],'15000.00')

    def test_invalid_catalog_data_cannot_replace_snapshot(self):
        before=self.l.execute('catalog.search', {'query':'600000'})
        for source, entries in [(None,[{'code':'600000','name':'证券','type':'stock'}]),('source',[None]),('source',[{'code':'600000','name':None,'type':'stock'}])]:
            with self.assertRaises(LedgerError):
                self.l.execute('catalog.import', {'source':source,'securities':entries})
            self.assertEqual(self.l.execute('catalog.search', {'query':'600000'}),before)

    def test_trade_can_move_to_another_run_only_if_both_histories_remain_valid(self):
        other=self.l.execute('pool.create',{'account_id':self.a,'name':'另一池'})['id']
        strategy=self.l.execute('strategy.create',{'name':'另一策略'})['id']
        run=self.l.execute('run.create',{'pool_id':other,'strategy_id':strategy,'name':'另一个运行'})['id']
        self.l.execute('cash.move',{'account_id':self.a,'pool_id':other,'direction':'in','amount':'3000','time':'2026-01-01T09:01'})
        buy=self.l.execute('trade.create',{'run_id':self.r,'security':'510300','direction':'buy','time':'2026-01-02T10:00','price':'10','quantity':100,'commission':'0','stamp_tax':'0','transfer_fee':'0'})['id']
        self.l.execute('trade.update',{'id':buy,'run_id':run})
        view=self.l.execute('account.get',{'id':self.a})
        self.assertEqual([p['cash'] for p in view['pools']],['15000.00','2000.00'])
        self.assertEqual(self.l.execute('trade.get',{'id':buy})['run_id'],run)

    def test_estimates_overrides_and_ended_run(self):
        self.l.execute('run.status',{'id':self.r,'status':'ended'})
        buy=self.l.execute('trade.create',{'run_id':self.r,'security':'510300','direction':'buy','time':'2026-01-02T10:00','price':'10','quantity':1000})
        self.assertIn('结束',buy['warning'])
        t=self.l.execute('trade.get',{'id':buy['id']})
        self.assertEqual((t['commission'],t['stamp_tax'],t['transfer_fee'],t['fees']),('5.00','0.00','0.00','5.00'))
        sell=self.l.execute('trade.create',{'run_id':self.r,'security':'510300','direction':'sell','time':'2026-01-03T10:00','price':'10','quantity':500,'commission':'1.20'})
        t=self.l.execute('trade.get',{'id':sell['id']})
        self.assertEqual((t['commission'],t['stamp_tax'],t['transfer_fee'],t['fees']),('1.20','0.00','0.00','1.20'))
        self.assertEqual(len(self.l.execute('trade.list',{'security':'510300','from':'2026-01-03T00:00','to':'2026-01-03T23:59'})),1)
        with self.assertRaisesRegex(LedgerError,'持仓'):
            self.l.execute('trade.create',{'run_id':self.r,'security':'510300','direction':'sell','time':'2026-01-04T10:00','price':'10','quantity':501})
