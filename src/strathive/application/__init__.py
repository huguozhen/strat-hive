"""Ledger/data orchestration; all network I/O happens outside ledger transactions."""
from decimal import Decimal, ROUND_FLOOR
from hashlib import sha256
import json

from strathive.core.ledger import Ledger, resolve
from strathive.core.replay import rebuild
from strathive.core.rules import LedgerError, require, timestamp, to_cents, money_text
from strathive.core.requests import recoverable_write
from strathive.data.service import Data


class Application(Ledger):
    def __init__(self, store, data=None):
        super().__init__(store)
        self.data = data or Data(catalog=store.catalog)

    def instrument(self, security):
        return self.data.instruments(security)

    def prepare_references(self):
        self.references = self.data.catalog.find()

    def _recover(self, operation, inputs, request_id):
        if request_id is None:
            return None
        fingerprint = sha256(json.dumps([operation, inputs], sort_keys=True, default=str).encode()).hexdigest()
        with self.store.transaction() as tx:
            previous = tx.find("write_request", id=request_id)
            if previous:
                require(previous[0]['fingerprint'] == fingerprint, 'request ID was already used for different input', 'request_conflict')
                return previous[0]['result']

    def boundary_prices(self, at, symbols):
        if not symbols:
            return {}
        try:
            day = self.data.boundary_date(timestamp(at))
            return self.data.close_prices(symbols, day)['prices']
        except LedgerError as exc:
            if exc.code == 'invalid_input':
                raise
            return {}

    def open_account(self, title, cash, rate=None, as_of=None, positions=(), allocations=(), *,
                     stock_rate=None, etf_rate=None, request_id=None):
        inputs = dict(title=title, cash=cash, rate=rate, as_of=as_of, positions=positions, allocations=allocations)
        if stock_rate is not None:
            inputs['stock_rate'] = stock_rate
        if etf_rate is not None:
            inputs['etf_rate'] = etf_rate
        previous = self._recover('account.open', inputs, request_id)
        if previous is not None:
            return previous
        self.prepare_references()
        symbols = [resolve(lambda **f: [r for r in self.references if all(r.get(k) == v for k,v in f.items())], p['security'])['symbol'] for p in positions]
        prices = self.boundary_prices(as_of, symbols)
        return recoverable_write(self.store, 'account.open', self._open_account_presented, request_id,
                                 context={'market_prices': prices}, **inputs)

    def _open_account_presented(self, tx, **inputs):
        result = self._open_account(tx, **inputs)
        rows = tx.find('opening_position', run_id=[r['id'] for r in tx.find('run', account_id=result['id'])])
        if not rows:
            return result
        return {**result, 'opening_valuations': [
            {'run_id': row['run_id'], 'symbol': row['symbol'], 'quantity': row['quantity'],
             'unit_cost': row['unit_cost'], 'price': row['valuation_price'], 'source': row['valuation_meta']}
            for row in rows]}

    def close_run(self, run_id, effective_at, successor_run_id=None, successor_cash='0', valuations=(), *, request_id=None):
        inputs = dict(run_id=run_id, effective_at=effective_at, successor_run_id=successor_run_id,
                      successor_cash=successor_cash, valuations=valuations)
        previous = self._recover('run.close', inputs, request_id)
        if previous is not None:
            return previous
        self.prepare_references()
        with self.store.transaction() as tx:
            run = tx.get('run', run_id)
            account = tx.get('account', run['account_id'])
            holdings = account['current']['runs'][str(run_id)]['positions']
        prices = self.boundary_prices(effective_at, [p['symbol'] for p in holdings]) if successor_run_id else {}
        return recoverable_write(self.store, 'run.close', self._close_run, request_id,
                                 context={'market_prices': prices}, **inputs)

    def record_trade(self, *args, **kwargs):
        self.prepare_references()
        return super().record_trade(*args, **kwargs)

    def edit_trade(self, *args, **kwargs):
        self.prepare_references()
        return super().edit_trade(*args, **kwargs)

    def reconcile(self, *args, **kwargs):
        self.prepare_references()
        return super().reconcile(*args, **kwargs)

    def _snapshot(self, account_id):
        with self.store.transaction() as tx:
            account = tx.get('account', account_id)
            times = [account['opened_at']] + [e['effective_at'] for e in tx.find('ledger_event', account_id=account_id)]
            require(max(times) <= timestamp(self.data.clock().isoformat()),
                    '账本包含未来生效事实，不能作为当前估值。', 'state_not_current')
            return account

    def valuation(self, account_id=None, run_id=None, refresh=False, offline=False):
        if run_id is not None:
            with self.store.transaction() as tx:
                account_id = tx.get('run', run_id)['account_id']
        for attempt in range(2):
            account = self._snapshot(account_id)
            current = account['current']
            quoted = self.data.quotes([p['symbol'] for p in current['positions']], refresh, offline)
            if self._snapshot(account_id) == account:
                break
        else:
            raise LedgerError('busy', '估值期间账本持续变化，请重试。')
        prices = quoted['prices']
        missing = quoted['missing']
        run_values = {key: Decimal(0) for key in current['runs']}
        market = 0
        for position in current['positions']:
            symbol = position['symbol']
            if symbol not in prices:
                continue
            price = Decimal(prices[symbol]['price'])
            cents = to_cents(price * position['quantity'])
            market += cents
            allocations = []
            for key, run in current['runs'].items():
                count = sum(p['quantity'] for p in run['positions'] if p['symbol'] == symbol)
                raw = price * count * 100
                floor = int(raw.to_integral_value(rounding=ROUND_FLOOR))
                allocations.append((key, floor, raw - floor))
            remainder = cents - sum(value for _, value, _ in allocations)
            ranked = sorted(allocations, key=lambda row: (-row[2], int(row[0])))
            for index, (key, floor, _) in enumerate(ranked):
                run_values[key] += floor + (1 if index < remainder else 0)
        runs = {}
        for key, run in current['runs'].items():
            unpriced = [p['symbol'] for p in run['positions'] if p['symbol'] not in prices]
            runs[key] = {**run, 'priced_market_value': money_text(int(run_values[key])),
                         'total_assets': None if unpriced else format(Decimal(run['cash']) + run_values[key]/100, '.2f'),
                         'unpriced_positions': unpriced}
        result = {'account_id': account_id, 'cash': current['cash'], 'unallocated_cash': current['unallocated_cash'],
                  'priced_market_value': money_text(market), 'total_assets': None if missing else format(Decimal(current['cash']) + Decimal(market)/100, '.2f'),
                  'availability': 'partial' if missing and prices else 'unavailable' if missing else 'complete',
                  'freshness': 'unknown' if missing or any(p['freshness']=='unknown' for p in prices.values()) else 'stale' if any(p['freshness']=='stale' for p in prices.values()) else 'current',
                  'prices': prices, 'unpriced_positions': missing, 'runs': runs}
        return {**result, 'run_id': run_id, 'current': runs[str(run_id)]} if run_id is not None else result

    def runs(self, run_id=None):
        result = super().runs(run_id)
        if run_id is None:
            return result
        with self.store.transaction() as tx:
            opening = tx.find('opening_position', run_id=run_id)
            incoming = [e for e in tx.find('ledger_event', account_id=result['account_id'])
                        if e['kind'] == 'close' and e['details'].get('successor_run_id') == run_id]
        return {**result, 'opening_valuations': [
            {'symbol': p['symbol'], 'price': p['valuation_price'], 'source': p['valuation_meta']} for p in opening],
            'incoming_valuations': [e['details']['valuations'] for e in incoming]}

    def complete_valuation(self, run_id):
        with self.store.transaction() as tx:
            run = tx.get('run', run_id)
            opening = tx.find('opening_position', run_id=run_id)
            events = tx.find('ledger_event', account_id=run['account_id'])
            boundaries = [e for e in events if e['kind']=='close' and
                          (e['run_id']==run_id or e['details'].get('successor_run_id')==run_id)]
        requests = [(run['started_at'], [p['symbol'] for p in opening if p['valuation_price'] is None])]
        for event in boundaries:
            with self.store.transaction() as tx:
                account = tx.get('account', run['account_id'])
            positions = account['current']['runs'][str(event['run_id'])]['closing']['positions']
            requests.append((event['effective_at'], [p['symbol'] for p in positions if p['valuation'] is None]))
        grouped = {}
        for at, symbols in requests:
            if at and symbols:
                grouped.setdefault(at, set()).update(symbols)
        prices = {at: self.boundary_prices(at, symbols) for at, symbols in grouped.items()}
        with self.store.transaction(write=True) as tx:
            for row in tx.find('opening_position', run_id=run_id):
                p = prices.get(run['started_at'], {}).get(row['symbol'])
                if row['valuation_price'] is None and p:
                    tx.update('opening_position', row['id'], valuation_price=p['price'], valuation_meta=p)
            for event in boundaries:
                current = tx.get('ledger_event', event['id'])
                details = current['details']
                marks = dict(details['valuations'])
                for symbol, p in prices.get(event['effective_at'], {}).items():
                    marks.setdefault(symbol, {**p, 'source': p['provider']})
                tx.update('ledger_event', event['id'], details={**details, 'valuations': marks})
            rebuild(tx, run['account_id'])
        return self.runs(run_id)
