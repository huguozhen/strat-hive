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
        self.a = self.l.execute('account.create', {'name':'账户','cash':'20000','commission_rate':'0.0003','opened_at':'2025-01-01T00:00'})['id']
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

    def test_trade_before_account_opening_is_rejected(self):
        account=self.l.execute('account.create',{'name':'时间边界','cash':'100','commission_rate':'0','opened_at':'2026-02-01T09:00'})['id']
        strategy=self.l.execute('strategy.create',{'name':'时间策略'})['id']
        pool=self.l.execute('pool.create',{'account_id':account,'name':'时间池'})['id']
        run=self.l.execute('run.create',{'strategy_id':strategy,'pool_id':pool,'name':'时间运行'})['id']
        self.l.execute('cash.move',{'account_id':account,'pool_id':pool,'direction':'in','amount':'100','time':'2026-02-01T09:00'})
        with self.assertRaisesRegex(LedgerError,'建账'):
            self.l.execute('trade.create',{'run_id':run,'security':'510300','direction':'buy','time':'2026-01-31T10:00','price':'1','quantity':1,'commission':'0'})
        self.assertEqual(self.l.execute('trade.list',{'run_id':run}),[])
        valid=self.l.execute('trade.create',{'run_id':run,'security':'510300','direction':'buy','time':'2026-02-01T09:01','price':'1','quantity':1,'commission':'0'})['id']
        with self.assertRaisesRegex(LedgerError,'建账'):
            self.l.execute('trade.update',{'id':valid,'time':'2026-01-31T10:00'})
        self.assertEqual(self.l.execute('trade.get',{'id':valid})['time'],'2026-02-01T09:01:00.000000')

    def test_legacy_account_requires_confirmation_of_unknown_opening_time(self):
        import sqlite3
        with sqlite3.connect(self.path) as db:
            db.execute('ALTER TABLE accounts DROP COLUMN opened_at')
        migrated=Ledger(self.path)
        self.assertIsNone(migrated.execute('account.get',{'id':self.a})['opened_at'])
        trade={'run_id':self.r,'security':'510300','direction':'buy','time':'2026-01-02T10:00','price':'1','quantity':1,'commission':'0'}
        with self.assertRaisesRegex(LedgerError,'时点未知'):
            migrated.execute('cash.move',{'account_id':self.a,'direction':'in','amount':'1','time':'2026-01-02T10:00'})
        with self.assertRaisesRegex(LedgerError,'时点未知'):
            migrated.execute('trade.create',trade)
        migrated.execute('account.update',{'id':self.a,'opened_at':'2025-01-01T00:00'})
        ident=migrated.execute('trade.create',trade)['id']
        with self.assertRaisesRegex(LedgerError,'晚于已有成交'):
            migrated.execute('account.update',{'id':self.a,'opened_at':'2026-01-03T00:00'})
        self.assertEqual(migrated.execute('trade.get',{'id':ident})['quantity'],1)

    def test_account_cost_replays_separately_from_pool_cost(self):
        strategy=self.l.execute('strategy.create',{'name':'第二策略'})['id']
        p2=self.l.execute('pool.create',{'account_id':self.a,'name':'第二池'})['id']
        r2=self.l.execute('run.create',{'strategy_id':strategy,'pool_id':p2,'name':'第二运行'})['id']
        self.l.execute('cash.move',{'account_id':self.a,'pool_id':p2,'direction':'in','amount':'2000','time':'2026-01-01T10:00'})
        for run in (self.r,r2):
            self.l.execute('trade.create',{'run_id':run,'security':'510300','direction':'buy','time':'2026-01-02T10:00','price':'10','quantity':100,'commission':'0','stamp_tax':'0','transfer_fee':'0'})
        self.l.execute('trade.create',{'run_id':self.r,'security':'510300','direction':'sell','time':'2026-01-03T10:00','price':'20','quantity':100,'commission':'0','stamp_tax':'0','transfer_fee':'0'})
        view=self.l.execute('account.get',{'id':self.a})
        self.assertEqual((view['holdings'][0]['quantity'],view['holdings'][0]['cost']),(100,'0.0000'))
        self.assertEqual(view['pools'][1]['holdings'][0]['cost'],'10.0000')

    def test_recorded_estimates_survive_metadata_edits_and_reprice_on_amount_change(self):
        self.l.execute('account.update',{'id':self.a,'commission_rate':'0.001'})
        trade=self.l.execute('trade.create',{'run_id':self.r,'security':'510300','direction':'buy','time':'2026-01-02T10:00','price':'10','quantity':10})['id']
        self.l.execute('account.update',{'id':self.a,'commission_rate':'0.1'})
        self.l.execute('trade.update',{'id':trade,'note':'补记说明'})
        self.assertEqual(self.l.execute('trade.get',{'id':trade})['commission'],'5.00')
        self.l.execute('trade.update',{'id':trade,'price':'11'})
        self.assertEqual(self.l.execute('trade.get',{'id':trade})['commission'],'11.00')
        self.l.execute('trade.update',{'id':trade,'commission':'2'})
        self.l.execute('trade.update',{'id':trade,'price':'12'})
        self.assertEqual(self.l.execute('trade.get',{'id':trade})['commission'],'2.00')
        self.l.execute('trade.update',{'id':trade,'commission':''})
        self.assertEqual(self.l.execute('trade.get',{'id':trade})['commission'],'12.00')

    def test_cash_cannot_precede_opening_and_opening_cannot_cross_existing_cash(self):
        before=self.l.execute('account.get',{'id':self.a})
        with self.assertRaisesRegex(LedgerError,'建账'):
            self.l.execute('cash.move',{'account_id':self.a,'direction':'in','amount':'10','time':'2024-12-31T10:00'})
        self.assertEqual(self.l.execute('account.get',{'id':self.a}),before)
        with self.assertRaisesRegex(LedgerError,'资金'):
            self.l.execute('account.update',{'id':self.a,'opened_at':'2026-01-02T00:00'})
        self.assertEqual(self.l.execute('account.get',{'id':self.a})['opened_at'],'2025-01-01T00:00:00.000000')

    def test_historical_stock_fees_follow_verified_dates_or_require_override(self):
        a=self.l.execute('account.create',{'name':'历史账户','cash':'0','commission_rate':'0.0003','opened_at':'2022-07-01T00:00'})['id']
        s=self.l.execute('strategy.create',{'name':'历史策略'})['id']
        p=self.l.execute('pool.create',{'account_id':a,'name':'历史池'})['id']
        r=self.l.execute('run.create',{'strategy_id':s,'pool_id':p,'name':'历史运行'})['id']
        self.l.execute('initial.record',{'account_id':a,'holdings':[{'security':'600000','quantity':2000,'cost':'5','date':'2020-01-01','allocations':[{'pool_id':p,'quantity':2000}]}]})
        sale={'run_id':r,'security':'600000','direction':'sell','time':'2023-02-01T10:00','price':'10','quantity':1000}
        with self.assertRaisesRegex(LedgerError,'过户费'):
            self.l.execute('trade.create',sale)
        first=self.l.execute('trade.create',{**sale,'transfer_fee':'0.20'})['id']
        self.assertEqual(self.l.execute('trade.get',{'id':first})['stamp_tax'],'10.00')
        second=self.l.execute('trade.create',{**sale,'time':'2023-08-28T10:00','transfer_fee':'0.10'})['id']
        self.assertEqual(self.l.execute('trade.get',{'id':second})['stamp_tax'],'5.00')

    def test_initial_holding_date_cannot_be_after_opening_date(self):
        account=self.l.execute('account.create',{'name':'初始状态','cash':'0','commission_rate':'0','opened_at':'2026-01-01T09:30'})['id']
        strategy=self.l.execute('strategy.create',{'name':'初始策略'})['id']
        pool=self.l.execute('pool.create',{'account_id':account,'name':'初始池'})['id']
        self.l.execute('run.create',{'strategy_id':strategy,'pool_id':pool,'name':'初始运行'})
        initial={'account_id':account,'holdings':[{'security':'600000','quantity':100,'cost':'8','date':'2026-01-02','allocations':[{'pool_id':pool,'quantity':100}]}]}
        with self.assertRaisesRegex(LedgerError,'持仓日期.*建账'):
            self.l.execute('initial.record',initial)
        self.assertFalse(self.l.execute('account.get',{'id':account})['setup_complete'])
        self.assertEqual(self.l.execute('account.get',{'id':account})['holdings'],[])

    def test_legacy_initial_holding_is_available_at_opening_even_if_recorded_date_is_later(self):
        import sqlite3
        a=self.l.execute('account.create',{'name':'旧快照','cash':'0','commission_rate':'0','opened_at':'2026-01-01T09:30'})['id']
        s=self.l.execute('strategy.create',{'name':'快照策略'})['id']
        p=self.l.execute('pool.create',{'account_id':a,'name':'快照池'})['id']
        r=self.l.execute('run.create',{'strategy_id':s,'pool_id':p,'name':'快照运行'})['id']
        self.l.execute('initial.record',{'account_id':a,'holdings':[{'security':'510300','quantity':100,'cost':'10','date':'2025-01-01','allocations':[{'pool_id':p,'quantity':100}]}]})
        # Previous versions accepted a future holding date; model such an existing database.
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE holdings SET date=? WHERE account_id=?',('2026-01-02',a))
        sale={'run_id':r,'security':'510300','direction':'sell','time':'2026-01-01T09:30','price':'12','quantity':50,'commission':'0','stamp_tax':'0','transfer_fee':'0'}
        self.l.execute('trade.create',sale)
        view=self.l.execute('account.get',{'id':a})
        self.assertEqual((view['holdings'][0]['quantity'],view['holdings'][0]['cost']),(50,'8.0000'))
        self.assertEqual(view['pools'][0]['holdings'][0]['date'],'2026-01-02')

    def test_opening_snapshot_handles_distinct_dates_securities_and_pool_costs(self):
        a=self.l.execute('account.create',{'name':'快照账户','cash':'0','commission_rate':'0','opened_at':'2026-01-01T09:30'})['id']
        s=self.l.execute('strategy.create',{'name':'快照策略'})['id']
        pools=[self.l.execute('pool.create',{'account_id':a,'name':name})['id'] for name in ('甲池','乙池')]
        runs=[self.l.execute('run.create',{'strategy_id':s,'pool_id':p,'name':'运行'})['id'] for p in pools]
        self.l.execute('initial.record',{'account_id':a,'holdings':[
            {'security':'600000','quantity':100,'cost':'10','date':'2020-05-01','allocations':[{'pool_id':pools[0],'quantity':60},{'pool_id':pools[1],'quantity':40}]},
            {'security':'510300','quantity':100,'cost':'5','date':'2026-01-01','allocations':[{'pool_id':pools[1],'quantity':100}]}]})
        sale={'run_id':runs[0],'security':'600000','direction':'sell','time':'2026-01-01T09:30','price':'20','quantity':60,'commission':'0','stamp_tax':'0','transfer_fee':'0'}
        with self.assertRaisesRegex(LedgerError,'建账'):
            self.l.execute('trade.create',{**sale,'time':'2026-01-01T09:29'})
        self.l.execute('trade.create',sale)
        view=self.l.execute('account.get',{'id':a})
        self.assertEqual([(h['code'],h['quantity'],h['cost']) for h in view['holdings']],[('510300',100,'5.0000'),('600000',40,'-5.0000')])
        self.assertEqual(view['cash'],'1200.00')
        self.assertEqual(view['pools'][0]['holdings'],[])
        self.assertEqual([(h['code'],h['quantity'],h['cost'],h['date']) for h in view['pools'][1]['holdings']],[('600000',40,'10.0000','2020-05-01'),('510300',100,'5.0000','2026-01-01')])
        with self.assertRaisesRegex(LedgerError,'持仓日期.*建账'):
            self.l.execute('account.update',{'id':a,'opened_at':'2025-12-31T09:30'})
        self.assertEqual(self.l.execute('account.get',{'id':a}),view)
