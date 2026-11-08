import tempfile
import unittest
import io
from unittest.mock import patch
from pathlib import Path
from strathive.ledger import Ledger, LedgerError

class LedgerTest(unittest.TestCase):
    def test_exchange_catalog_requests_use_the_matching_exchange_origin(self):
        from strathive.catalog import fetch
        requests=[]
        def respond(request, timeout):
            requests.append(request)
            return io.BytesIO(b'data')
        with patch('urllib.request.urlopen', side_effect=respond):
            fetch('https://www.szse.cn/api/report/ShowReport')
            fetch('https://query.sse.com.cn/commonSoaQuery.do')
        self.assertEqual(requests[0].get_header('Referer'), 'https://www.szse.cn/')
        self.assertEqual(requests[1].get_header('Referer'), 'https://www.sse.com.cn/')

    def test_public_catalog_keeps_provided_legal_names(self):
        from strathive.catalog import sync
        with tempfile.TemporaryDirectory() as d:
            ledger = Ledger(Path(d) / 'ledger.db')
            def stock_or_fund(path, params):
                if params.get('STOCK_TYPE'):
                    return [{'A_STOCK_CODE':'600000','SEC_NAME_CN':'浦发银行','FULL_NAME':'上海浦东发展银行股份有限公司'}]
                return [{'fundCode':'510300','fundAbbr':'沪深300ETF','secNameFull':'沪深300ETF华泰柏瑞'}]
            def shenzhen(url):
                if '1110' in url:
                    return [{'板块':'主板','A股代码':'000001','A股简称':'平安银行','公司全称':'平安银行股份有限公司'}]
                return [{'基金类别':'ETF','基金代码':'159001','基金简称':'ETF测试'}]
            with patch('strathive.catalog.sse', side_effect=stock_or_fund), patch('strathive.catalog.xlsx', side_effect=shenzhen):
                sync(ledger)
            stock=ledger.execute('catalog.search', {'query':'上海浦东发展银行股份有限公司'})['securities'][0]
            fund=ledger.execute('catalog.search', {'query':'510300'})['securities'][0]
            self.assertEqual(stock['code'], '600000')
            self.assertEqual(fund['full_name'], '')  # 扩位简称不是基金法律全称

    def test_future_account_opening_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = Ledger(Path(d) / 'ledger.db')
            with self.assertRaisesRegex(LedgerError, '未来'):
                ledger.execute('account.create', {'name':'账户', 'commission_rate':'0', 'cash':'100', 'opened_at':'2099-01-01T00:00'})
            self.assertEqual(ledger.execute('account.list'), [])

    def test_invalid_initial_holdings_are_rejected_atomically(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = Ledger(Path(d) / 'ledger.db')
            account = ledger.execute('account.create', {'name':'账户', 'commission_rate':'0', 'cash':'100'})['id']
            ledger.execute('catalog.import', {'source':'test','securities':[{'code':'600000','name':'浦发银行','type':'stock'}]})
            before = ledger.execute('account.get', {'id':account})
            for entries in ([None], [{'security':'600000','quantity':1,'cost':'10','date':'2025-01-01','allocations':[None]}]):
                with self.assertRaises(LedgerError):
                    ledger.execute('initial.record', {'account_id':account, 'holdings':entries})
                self.assertEqual(ledger.execute('account.get', {'id':account}), before)

    def test_future_cash_operation_does_not_change_current_balance(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = Ledger(Path(d) / 'ledger.db')
            account = ledger.execute('account.create', {'name':'账户', 'commission_rate':'0', 'cash':'100'})['id']
            with self.assertRaisesRegex(LedgerError, '未来'):
                ledger.execute('cash.move', {'account_id':account, 'direction':'in', 'amount':'1000', 'time':'2099-01-01T10:00:00'})
            self.assertEqual(ledger.execute('account.get', {'id':account})['cash'], '100.00')

    def test_cash_account_persists(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'ledger.db'
            ledger = Ledger(path)
            account = ledger.execute('account.create', {'name':'券商', 'commission_rate':'0.0003', 'cash':'1000.01'})
            view = Ledger(path).execute('account.get', {'id':account['id']})
            self.assertEqual(view['cash'], '1000.01')
            self.assertEqual(view['unallocated_cash'], '1000.01')
            self.assertEqual(view['pools'], [])

    def test_holdings_split_across_runs_and_cash_conserved(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = Ledger(Path(d) / 'ledger.db')
            ledger.execute('catalog.import', {'source':'test', 'securities':[{'code':'600000','name':'浦发银行','type':'stock'}]})
            a = ledger.execute('account.create', {'name':'券商','commission_rate':'0.0003','cash':'1000'})['id']
            s = ledger.execute('strategy.create', {'name':'趋势','description':'人工'})['id']
            p1 = ledger.execute('pool.create', {'account_id':a,'name':'一号'})['id']
            p2 = ledger.execute('pool.create', {'account_id':a,'name':'二号'})['id']
            ledger.execute('run.create', {'strategy_id':s,'pool_id':p1,'name':'运行一'})
            ledger.execute('run.create', {'strategy_id':s,'pool_id':p2,'name':'运行二'})
            ledger.execute('initial.record', {'account_id':a,'holdings':[{'security':'600000','quantity':100,'cost':'10.25','date':'2025-01-02','allocations':[{'pool_id':p1,'quantity':40},{'pool_id':p2,'quantity':60}]}]})
            ledger.execute('cash.move', {'account_id':a,'pool_id':p1,'direction':'in','amount':'200.15'})
            view = ledger.execute('account.get', {'id':a})
            self.assertEqual(view['cash'], '1000.00')
            self.assertEqual(view['unallocated_cash'], '799.85')
            self.assertEqual(view['holdings'][0]['quantity'],100)
            self.assertEqual([p['holdings'][0]['quantity'] for p in view['pools']], [40,60])
            with self.assertRaises(LedgerError):
                ledger.execute('cash.move', {'account_id':a,'pool_id':p1,'direction':'out','amount':'200.16'})
            with self.assertRaises(LedgerError):
                ledger.execute('initial.record', {'account_id':a,'holdings':[{'security':'600000','quantity':10,'cost':'1','date':'2025-01-02','allocations':[{'pool_id':p1,'quantity':9}]}]})
            self.assertEqual(ledger.execute('account.get', {'id':a}), view)

    def test_invalid_catalog_replacement_preserves_existing_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = Ledger(Path(d)/'ledger.db')
            ledger.execute('catalog.import', {'source':'本地', 'securities':[{'code':'000001','name':'平安银行','type':'stock'}]})
            before = ledger.execute('catalog.search', {'query':'000001'})
            with self.assertRaises(LedgerError):
                ledger.execute('catalog.import', {'source':'坏文件', 'securities':[{'code':'bad','name':'错误','type':'stock'}]})
            self.assertEqual(ledger.execute('catalog.search', {'query':'000001'}), before)

    def test_initial_setup_is_atomic_and_cannot_be_repeated(self):
        with tempfile.TemporaryDirectory() as d:
            l = Ledger(Path(d)/'ledger.db')
            l.execute('catalog.import', {'source':'本地','securities':[{'code':'600000','name':'浦发银行','type':'stock'},{'code':'510300','name':'沪深300ETF','type':'etf'}]})
            a = l.execute('account.create', {'name':'账户','commission_rate':'0','cash':'20'})['id']
            s = l.execute('strategy.create', {'name':'策略'})['id']
            p = l.execute('pool.create', {'account_id':a,'name':'池'})['id']
            l.execute('run.create', {'strategy_id':s,'pool_id':p,'name':'运行'})
            first = {'security':'600000','quantity':10,'cost':'8.00','date':'2025-01-01','allocations':[{'pool_id':p,'quantity':10}]}
            second = {'security':'510300','quantity':20,'cost':'4.00','date':'2025-01-01','allocations':[{'pool_id':p,'quantity':19}]}
            with self.assertRaises(LedgerError): l.execute('initial.record', {'account_id':a,'holdings':[first,second]})
            self.assertEqual(l.execute('account.get', {'id':a})['holdings'], [])
            second['allocations'][0]['quantity']=20
            l.execute('initial.record', {'account_id':a,'holdings':[first,second]})
            with self.assertRaises(LedgerError): l.execute('initial.record', {'account_id':a,'holdings':[first]})
            view=l.execute('account.get', {'id':a})
            self.assertEqual(len(view['holdings']),2)
            self.assertEqual(view['holdings'][1]['cost'],'8.0000')

    def test_cash_boundaries_and_pool_binding(self):
        with tempfile.TemporaryDirectory() as d:
            l=Ledger(Path(d)/'ledger.db')
            a=l.execute('account.create', {'name':'账户','commission_rate':'0.0003','cash':'10.00'})['id']
            s=l.execute('strategy.create', {'name':'策略'})['id']
            p=l.execute('pool.create', {'name':'池','account_id':a})['id']
            r=l.execute('run.create', {'name':'运行','strategy_id':s,'pool_id':p})['id']
            with self.assertRaises(LedgerError): l.execute('run.create', {'name':'冲突','strategy_id':s,'pool_id':p})
            l.execute('cash.move', {'account_id':a,'pool_id':p,'direction':'in','amount':'6.00'})
            with self.assertRaises(LedgerError): l.execute('cash.move', {'account_id':a,'direction':'out','amount':'4.01'})
            with self.assertRaises(LedgerError): l.execute('cash.move', {'account_id':a,'pool_id':p,'direction':'in','amount':'4.01'})
            l.execute('cash.move', {'account_id':a,'pool_id':p,'direction':'out','amount':'2.00'})
            l.execute('cash.move', {'account_id':a,'direction':'out','amount':'3.00'})
            view=l.execute('account.get', {'id':a})
            self.assertEqual((view['cash'],view['unallocated_cash'],view['pools'][0]['cash']),('7.00','3.00','4.00'))
            l.execute('run.status', {'id':r,'status':'ended'})
            l.execute('run.create', {'name':'后续运行','strategy_id':s,'pool_id':p})
            with self.assertRaises(LedgerError): l.execute('run.status', {'id':r,'status':'running'})
            with self.assertRaises(LedgerError): l.execute('initial.record', {'account_id':a,'holdings':[{}]})

    def test_run_detail_exposes_bound_pool_assets(self):
        with tempfile.TemporaryDirectory() as d:
            l=Ledger(Path(d)/'ledger.db')
            a=l.execute('account.create', {'name':'账户','commission_rate':'0','cash':'100'})['id']
            s=l.execute('strategy.create', {'name':'策略'})['id']
            p=l.execute('pool.create', {'name':'池','account_id':a})['id']
            r=l.execute('run.create', {'name':'运行','strategy_id':s,'pool_id':p})['id']
            l.execute('cash.move', {'account_id':a,'pool_id':p,'direction':'in','amount':'42.30'})
            detail=l.execute('run.get', {'id':r})
            self.assertEqual(detail['pool']['cash'],'42.30')
            self.assertEqual(detail['status'],'paused')
