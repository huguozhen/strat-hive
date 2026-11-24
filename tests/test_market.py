"""Market integration tests exercise business outcomes without a vendor account."""
from datetime import datetime, timedelta, timezone
import json

import pytest
from typer.testing import CliRunner

from strathive.application import Application
from strathive.bootstrap import build
from strathive.cli import app
from strathive.core.rules import LedgerError
from strathive.data.cache import PriceCache
from strathive.data.models import Price
from strathive.data.service import Data, configuration
from test_ledger import CATALOG, CUTOFF, FIRST, SECOND, allocation, holdings

NOW = datetime.fromisoformat('2026-11-19T02:00:00+00:00')


class Provider:
    def __init__(self, name='fake', price='12', fail=False, missing=()):
        self.name, self.price, self.fail, self.missing = name, price, fail, missing
        self.calls = 0

    def quotes(self, symbols):
        self.calls += 1
        if self.fail:
            raise LedgerError('source_timeout', 'timeout')
        return [Price(s, self.price, 'last', self.name, NOW.isoformat(), '2026-11-19', NOW.isoformat()).to_dict()
                for s in symbols if s not in self.missing]

    def close_prices(self, symbols, day):
        return [Price(s, self.price, 'close', self.name, None, day, NOW.isoformat()).to_dict() for s in symbols]

    def calendar(self, market):
        return ['2026-10-30', '2026-11-02', '2026-11-03', '2026-11-19', '2026-11-20']


def service(tmp_path, providers):
    names = list(providers)
    return Data(config={'providers': {}, 'selection': dict(quote=names, close=names, calendar=names)},
                providers=providers, cache=PriceCache(tmp_path / 'prices.db'), clock=lambda: NOW)


def test_cache_refresh_offline_and_backup_source(tmp_path):
    first, second = Provider('first', fail=True), Provider('second')
    data = service(tmp_path, {'first': first, 'second': second})
    result = data.quotes(['SH.600000'])
    assert result['prices']['SH.600000']['provider'] == 'second'
    assert result['errors'] == {'first': 'source_timeout'}
    data.quotes(['SH.600000'])
    assert second.calls == 1
    data.quotes(['SH.600000'], refresh=True)
    assert second.calls == 2
    first.fail = second.fail = True
    offline = data.quotes(['SH.600000'], offline=True)
    assert offline['prices']['SH.600000']['price'] == '12'
    assert second.calls == 2
    with pytest.raises(LedgerError):
        data.quotes([], refresh=True, offline=True)


def test_missing_and_close_fallback_do_not_invent_prices(tmp_path):
    data = service(tmp_path, {})
    assert data.quotes(['SH.600000'])['missing'] == ['SH.600000']
    data.cache.put(Price('SH.600000', '10', 'close', 'old', None, '2026-11-18', NOW.isoformat()))
    row = data.quotes(['SH.600000'], offline=True)['prices']['SH.600000']
    assert row['kind'] == 'close' and row['degraded']
    assert row['as_of'] is None


def test_boundary_dates_and_market_hours(tmp_path):
    data = service(tmp_path, {'fake': Provider()})
    assert data.boundary_date('2026-11-19T08:00:00+08:00') == '2026-11-03'
    assert data.boundary_date('2026-11-19T16:00:00+08:00') == '2026-11-19'
    assert data.boundary_date('2026-11-01T08:00:00+08:00') == '2026-10-30'
    with pytest.raises(LedgerError, match='盘前或盘后'):
        data.boundary_date('2026-11-19T12:00:00+08:00')


def make_ledger(tmp_path):
    ledger, store = build(tmp_path / 'ledger.db')
    store.initialize()
    store.catalog.prepare(CATALOG)
    ledger.data = service(tmp_path, {'fake': Provider()})
    ledger.data.catalog = store.catalog
    return ledger, store


def test_valuation_and_facts_unchanged(tmp_path):
    ledger, store = make_ledger(tmp_path)
    strategy = ledger.create_strategy('s')
    account = ledger.open_account('a', '1000', '0.0003', CUTOFF, holdings(),
                                  [allocation(strategy, 'a', 40), allocation(strategy, 'b', 60)])
    before = ledger.accounts()
    valued = ledger.valuation(account_id=account['id'])
    assert valued['total_assets'] == '2200.00'
    assert valued['runs']['1']['priced_market_value'] == '480.00'
    assert ledger.accounts() == before
    ledger.data.providers['fake'].missing = ('SH.600000',)
    ledger.data.cache = PriceCache(tmp_path / 'empty.db')
    # No configured close provider: this must be partial, not a zero-price valuation.
    ledger.data.config['selection']['close'] = []
    valued = ledger.valuation(account_id=account['id'], refresh=True)
    assert valued['total_assets'] is None
    assert valued['unpriced_positions'] == ['SH.600000']


def test_boundary_prices_retries_and_empty_completion(tmp_path):
    ledger, store = make_ledger(tmp_path)
    strategy = ledger.create_strategy('s')
    inputs = ('a', '1000', '0.0003', CUTOFF, holdings(), [allocation(strategy, 'a', 100)])
    opened = ledger.open_account(*inputs, request_id='opening')
    assert opened['current']['runs']['1']['initial_capital'] == '1200.00'
    ledger.data.providers['fake'].price = '99'
    assert ledger.open_account(*inputs, request_id='opening') == opened
    assert ledger.accounts()[0]['current']['positions'][0]['unit_cost'] == '9.00000000'
    target = ledger.create_run(strategy['id'], opened['id'], 'next')
    ledger.data.config['selection']['close'] = []
    ledger.data.cache = PriceCache(tmp_path / 'new.db')
    closed = ledger.close_run(1, '2026-11-03T16:00:00+08:00', target['id'], request_id='close')
    assert closed['current']['closing']['ending_value'] is None
    ledger.data.config['selection']['close'] = ['fake']
    completed = ledger.complete_valuation(target['id'])
    assert completed['current']['initial_capital'] == '9900.00'
    ledger.data.providers['fake'].price = '1'
    ledger.complete_valuation(target['id'])
    assert ledger.runs(target['id'])['current']['initial_capital'] == '9900.00'
    assert ledger.close_run(1, '2026-11-03T16:00:00+08:00', target['id'], request_id='close') == closed


def test_data_cli_without_ledger_and_configuration_keeps_secrets_private(tmp_path):
    result = CliRunner().invoke(app, ['--db', str(tmp_path / 'absent.db'), 'data', 'status'])
    assert result.exit_code == 0
    assert not (tmp_path / 'absent.db').exists()
    values = configuration({'source': 'galaxy', 'connection': {'password': 'private'}})
    data = Data(config=values)
    assert 'private' not in json.dumps(data.status())
    assert values['selection']['quote'] == []


@pytest.mark.parametrize('value', ['0', '-1', 'NaN', 'Infinity', 'bad'])
def test_invalid_market_values_rejected(value):
    with pytest.raises(ValueError):
        Price('SH.600000', value, 'last', 'fake', NOW.isoformat(), '2026-11-19', NOW.isoformat())


def test_future_facts_and_valuation_during_concurrent_change(tmp_path, monkeypatch):
    ledger, store = make_ledger(tmp_path)
    account = ledger.open_account('cash', '1000', '0.0003', '2026-11-01T08:00:00')
    original = ledger.data.quotes
    def changing(*args, **kwargs):
        ledger.edit_account(account['id'], title='changed')
        return original(*args, **kwargs)
    monkeypatch.setattr(ledger.data, 'quotes', changing)
    # One concurrent edit is retried, without a network-held ledger transaction.
    assert ledger.valuation(account_id=account['id'])['total_assets'] == '1000.00'
    ledger.move(account['id'], 'deposit', '1', '2027-01-01T08:00:00')
    with pytest.raises(LedgerError) as exc:
        ledger.valuation(account_id=account['id'])
    assert exc.value.code == 'state_not_current'


def test_cli_valuation_options(tmp_path, monkeypatch):
    from strathive import settings
    ledger, store = make_ledger(tmp_path)
    account = ledger.open_account('cash', '1000', '0.0003', CUTOFF)
    result = CliRunner().invoke(app, ['--db', str(store.path), 'valuation', 'account', '--account-id', str(account['id']), '--offline'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['data']['total_assets'] == '1000.00'


def test_0004_migration_preserves_prices_and_receipts(tmp_path):
    ledger, store = make_ledger(tmp_path)
    strategy = ledger.create_strategy('s')
    opened = ledger.open_account('a', '1000', '0.0003', CUTOFF, holdings(), [allocation(strategy, 'a', 100, '10')], request_id='old')
    with store.transaction(write=True) as tx:
        tx.connection.exec_driver_sql('ALTER TABLE opening_position DROP COLUMN valuation_meta')
        tx.connection.exec_driver_sql("UPDATE alembic_version SET version_num='0004'")
    result = store.initialize()
    assert result['revision'] == '0005'
    assert result['migration_backup']
    assert ledger.open_account('a', '1000', '0.0003', CUTOFF, holdings(), [allocation(strategy, 'a', 100, '10')], request_id='old') == opened


def test_galaxy_bounded_snapshot_worker_uses_exchange_timestamp(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import os
    from strathive.sources import galaxy
    callback = None
    class Subscription:
        def register(self, **kwargs):
            assert kwargs['code_list'] == ['600000.SH']
            def bind(function):
                nonlocal callback
                callback = function
                return function
            return bind
        def run(self):
            callback(SimpleNamespace(code='600000.SH', last=12.5, trade_time=NOW), 1)
    sdk = SimpleNamespace(BaseData=lambda: object(), SubscribeData=Subscription,
                          constant=SimpleNamespace(Period=SimpleNamespace(snapshot=SimpleNamespace(value=1))),
                          logout=lambda _: None)
    monkeypatch.setattr(galaxy, 'connect', lambda _: sdk)
    def exited(code):
        raise SystemExit(code)
    monkeypatch.setattr(os, '_exit', exited)
    result_path = tmp_path / 'quote.json'
    with pytest.raises(SystemExit):
        galaxy.market({'username': 'fake'}, 'quotes', ['SH.600000'], None, result_path)
    row = json.loads(result_path.read_text())['data'][0]
    assert row['price'] == '12.5'
    assert row['as_of'] == NOW.isoformat()
    assert row['symbol'] == 'SH.600000'
