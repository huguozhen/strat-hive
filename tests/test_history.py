"""Historical behavior tests run without CNEquity or outbound market requests."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from strathive import settings
from strathive.cli import app
from strathive.core.rules import LedgerError
from strathive.data.history.bars import (adjusted, aggregate, coverage, minute_ends, normalize,
                                        period_bounds, symbol, vendor_symbol, window)
from strathive.data.history.provider import History, configuration
from strathive.data.history.sync import sync, writer
from strathive.data.history.worker import export
from strathive.data.service import Data

NOW = datetime.fromisoformat('2026-11-24T12:00:00+00:00')


def raw(session='2026-11-23', at=None, **kwargs):
    return {'symbol': '510300.SH', 'trade_date': session, 'open': '3.123456789123',
            'high': '3.2', 'low': '3.1', 'close': '3.15', 'volume': 100, 'amount': '313.123456789',
            'source': 'tdx_protocol', 'data_version': 'v2', 'fetched_at': NOW.isoformat(),
            'volume_unit': 'units', **({'bar_time': at} if at else {}), **kwargs}


def minute_rows(session='2026-11-23'):
    return [normalize(raw(session, at), '1m', 3) for at in minute_ends(session)]


def test_base_fields_precision_and_units():
    row = normalize(raw(), '1d', 3)
    assert row['symbol'] == 'SH.510300'
    assert row['open'] == '3.123456789123'
    assert row['amount'] == '313.123456789'
    assert row['volume'] == 100 and row['volume_unit'] == 'units'
    assert Decimal(row['amount']) != Decimal(row['close']) * row['volume']
    assert row['interval_start'] == row['interval_end'] == '2026-11-23'
    assert row['revision'] == 3
    assert vendor_symbol('SH.000001') == '000001.SH'
    assert symbol('000001.SZ') == 'SZ.000001'


@pytest.mark.parametrize('kwargs', [{'amount': None}, {'volume': '1.2'}, {'low': '4'}, {'open': 'NaN'},
                                     {'close': '0'}, {'volume': 0}, {'amount': '0'}, {'source': 'mock'}, {'fetched_at': '2026-11-24T12:00:00'}])
def test_bad_bars_rejected(kwargs):
    with pytest.raises((LedgerError, ValueError)):
        normalize(raw(**kwargs), '1d', 1)


def test_date_boundaries_and_market_identifiers():
    for value in ('000001', 'BJ.430001', 'SH.1'):
        with pytest.raises(LedgerError):
            symbol(value)
    with pytest.raises(LedgerError):
        window('2026-11-25', '2026-11-25', NOW)
    with pytest.raises(LedgerError):
        window('2026-11-24', '2026-11-23', NOW)
    assert window('2026-11-23', '2026-11-24', NOW)[1].isoformat() == '2026-11-24'


@pytest.mark.parametrize('period,count', [('5m', 48), ('15m', 16), ('30m', 8), ('60m', 4)])
def test_minute_buckets_do_not_cross_lunch(period, count):
    rows = aggregate(minute_rows(), period, ['2026-11-23'])
    assert len(rows) == count
    assert all(r['complete'] for r in rows)
    assert all(not (r['interval_start'][11:16] < '12:00' < r['interval_end'][11:16]) for r in rows)
    assert rows[0]['interval_start'].endswith('09:30:00+08:00')
    assert rows[-1]['interval_end'].endswith('15:00:00+08:00')
    assert sum(r['volume'] for r in rows) == 24000
    assert sum(Decimal(r['amount']) for r in rows) == Decimal('313.123456789') * 240


def test_missing_minutes_and_unfinished_bucket_remain_visible():
    rows = minute_rows()[:3]
    assert coverage(rows, ['2026-11-23'], '1m')[0]['missing_count'] == 237
    bucket = aggregate(rows, '5m', ['2026-11-23'])[0]
    assert not bucket['complete'] and bucket['base_count'] == 3 and bucket['expected_count'] == 5
    with pytest.raises(LedgerError):
        normalize(raw(at='2026-11-23T12:00:00'), '1m', 1)


@pytest.mark.parametrize('period,last', [('1w', '2026-11-29'), ('1mo', '2026-11-30'), ('1q', '2026-12-31')])
def test_calendar_aggregation_marks_partial_periods(period, last):
    rows = [normalize(raw(), '1d', 1), normalize(raw('2026-11-24'), '1d', 1)]
    result = aggregate(rows, period, ['2026-11-23', '2026-11-24', '2026-11-25'])[0]
    assert result['interval_end'] == last
    assert result['base_count'] == 2 and not result['complete']
    assert period_bounds('2024-02-29', '1mo')[1].isoformat() == '2024-02-29'


def test_adjust_before_aggregation_and_reject_factor_holes():
    rows = [normalize(raw('2026-11-23', close='3.2'), '1d', 1), normalize(raw('2026-11-24'), '1d', 1)]
    factors = {'2026-11-23': '2', '2026-11-24': '4'}
    converted, missing = adjusted(rows, factors, 'qfq', '4')
    assert not missing
    weekly = aggregate(converted, '1w', ['2026-11-23', '2026-11-24'])[0]
    assert Decimal(weekly['open']) == Decimal(rows[0]['open']) / 2
    assert weekly['high'] == '3.2' and weekly['close'] == '3.15'
    assert rows[0]['open'] == '3.123456789123'
    assert adjusted(rows, {}, 'hfq') == ([], ['2026-11-23', '2026-11-24'])
    assert adjusted(rows, factors, 'qfq')[0] == []
    assert adjusted(rows, factors, 'hfq')[0][1]['close'] == '12.60'


class Backend:
    def __init__(self, sessions=None):
        self.dates = sessions or ['2026-11-23', '2026-11-24']
        self.calls, self.rows, self.fail, self.interrupt = [], set(), False, False
    def reference(self, explicit=None):
        pass
    def universe(self):
        return ['SH.510300']
    def sessions(self, start, end):
        return [d for d in self.dates if start <= d <= end]
    def fetch(self, task):
        self.calls.append(task.copy())
        if self.fail:
            raise RuntimeError('source unavailable')
        return self.sessions(task['start'], task['end'])
    def publish(self, task, data):
        if self.interrupt:
            raise KeyboardInterrupt()
        for session in data:
            self.rows.add((task['dataset'], task['symbol'], session))
        return {'rows_received': len(data), 'revision': 1, 'gaps': []}


def config(tmp_path):
    return {**configuration({'history': {}}), 'root': str(tmp_path / 'lake')}


def test_idempotence_failed_batches_and_exact_retry(tmp_path):
    backend, cfg = Backend(), config(tmp_path)
    request = {'symbols': ['SH.510300'], 'datasets': ['daily_bars'], 'start': '2026-11-23', 'end': '2026-11-24'}
    assert sync(backend, cfg, request, NOW)['complete']
    assert sync(backend, cfg, request, NOW)['complete']
    assert len(backend.rows) == 2
    backend.fail = True
    assert not sync(backend, cfg, request, NOW)['complete']
    backend.fail = False
    assert sync(backend, cfg, {'retry_only': True}, NOW)['complete']
    assert backend.calls[-1]['start'] == '2026-11-23'
    count = len(backend.calls)
    assert sync(backend, cfg, {'retry_only': True}, NOW)['batches'] == []
    assert len(backend.calls) == count


def test_interruption_resumes_and_no_implicit_minute_universe(tmp_path):
    backend, cfg = Backend(), config(tmp_path)
    request = {'datasets': ['minute_bars', 'adj_factors']}
    assert sync(backend, cfg, request, NOW)['batches'] == []
    assert backend.calls == []
    request = {'symbols': ['SH.510300'], 'datasets': ['daily_bars'], 'start': '2026-11-23', 'end': '2026-11-24'}
    backend.interrupt = True
    with pytest.raises(KeyboardInterrupt):
        sync(backend, cfg, request, NOW)
    backend.interrupt = False
    assert sync(backend, cfg, {'retry_only': True}, NOW)['complete']


def test_trading_day_window_and_revision_lookback(tmp_path):
    dates = [(NOW.date() - timedelta(days=i)).isoformat() for i in range(200, -1, -1)]
    backend, cfg = Backend(dates), config(tmp_path)
    cfg['minute_symbols'] = ['SH.510300']
    result = sync(backend, cfg, {'datasets': ['minute_bars', 'adj_factors']}, NOW)
    assert result['complete']
    assert backend.calls[0]['start'] == dates[-60]
    assert backend.calls[1]['start'] == dates[-60]
    backend.calls.clear()
    sync(backend, cfg, {'datasets': ['minute_bars', 'adj_factors']}, NOW)
    assert backend.calls[0]['start'] == dates[-2]
    assert backend.calls[1]['start'] == dates[-5]


def test_single_writer_and_low_space_do_not_touch_ledger(tmp_path):
    cfg = config(tmp_path)
    with writer(cfg['root']):
        with pytest.raises(LedgerError) as exc:
            sync(Backend(), cfg, {'retry_only': True}, NOW)
        assert exc.value.code == 'history_busy'
    cfg['pause_gb'] = 10**9
    with pytest.raises(LedgerError) as exc:
        sync(Backend(), cfg, {}, NOW)
    assert exc.value.code == 'history_low_space'
    assert not settings.ledger_path().exists()


@pytest.mark.parametrize('suffix', ['jsonl', 'csv'])
def test_atomic_complete_export_and_failure_cleanup(tmp_path, suffix):
    target = tmp_path / ('bars.' + suffix)
    rows = minute_rows()
    with export(str(target)) as emit:
        for row in rows:
            emit(row)
        emit.manifest = {'row_count': len(rows), 'snapshot': 3}
    assert len(target.read_text().splitlines()) == len(rows) + (suffix == 'csv')
    assert json.loads(Path(str(target) + '.manifest.json').read_text())['row_count'] == 240
    with pytest.raises(LedgerError):
        with export(str(target)):
            pass
    broken = tmp_path / ('broken.' + suffix)
    with pytest.raises(RuntimeError):
        with export(str(broken)) as emit:
            emit(rows[0])
            raise RuntimeError('interrupted')
    assert not broken.exists() and not Path(str(broken) + '.manifest.json').exists()
    assert not list(tmp_path.glob('.history-*'))


def test_history_cli_missing_backend_and_independent_ledger(tmp_path):
    runner = CliRunner()
    ledger = tmp_path / 'ledger.db'
    status = runner.invoke(app, ['--db', str(ledger), 'data', 'status'])
    assert status.exit_code == 0
    assert not json.loads(status.output)['data']['history']['installed']
    query = runner.invoke(app, ['--db', str(ledger), 'data', 'bars', '--security', 'SH.510300', '--start', '2026-11-23', '--end', '2026-11-24'])
    assert query.exit_code == 1
    assert query.exception.code == 'history_backend_unavailable'
    assert not ledger.exists()
    result = runner.invoke(app, ['data', 'history', 'configure', '--minute-security', '510300.SH'])
    assert result.exit_code == 0, result.output
    assert settings.settings()['history']['minute_symbols'] == ['SH.510300']
    assert settings.settings()['selection']['quote'] == []
    assert 'cnequity' in settings.settings()['selection']['close']
    assert runner.invoke(app, ['data', 'history', 'clear-minute-list']).exit_code == 0
    assert settings.settings()['history']['minute_symbols'] == []


def test_historical_closes_do_not_copy_to_quote_cache(tmp_path):
    class HistoryProvider:
        def close_prices(self, symbols, session_date):
            return [{'symbol': s, 'price': '3.15', 'kind': 'close', 'provider': 'cnequity', 'as_of': None,
                     'session_date': session_date, 'retrieved_at': NOW.isoformat()} for s in symbols]
    data = Data(config={'providers': {}, 'selection': {'close': ['cnequity']}},
                providers={'cnequity': HistoryProvider()}, clock=lambda: NOW)
    assert data.close_prices(['SH.510300'], '2026-11-23')['prices']['SH.510300']['price'] == '3.15'
    assert data.cache.find('SH.510300', 'close') == []


def test_reference_failure_is_reported_and_retry_replans(tmp_path):
    backend, cfg = Backend(), config(tmp_path)
    def unavailable(explicit=None):
        raise RuntimeError('directory offline')
    backend.reference = unavailable
    request = {'symbols': ['SH.510300'], 'datasets': ['daily_bars'], 'start': '2026-11-23', 'end': '2026-11-24'}
    with pytest.raises(RuntimeError):
        sync(backend, cfg, request, NOW)
    assert History(cfg).status()['pending_plans'] == 1
    backend.reference = lambda explicit=None: None
    result = sync(backend, cfg, {'retry_only': True}, NOW)
    assert result['complete'] and result['recovered_plans'][0]['complete']
    assert len(backend.rows) == 2 and History(cfg).status()['pending_plans'] == 0


def test_partial_coverage_is_not_success_and_older_gaps_survive(tmp_path):
    backend, cfg = Backend(), config(tmp_path)
    original = backend.publish
    def partial(task, data):
        return {**original(task, data), 'gaps': [{'session_date': '2026-11-23', 'missing_count': 1}]}
    backend.publish = partial
    request = {'symbols': ['SH.510300'], 'datasets': ['daily_bars'], 'start': '2026-11-23', 'end': '2026-11-24'}
    assert not sync(backend, cfg, request, NOW)['complete']
    backend.publish = original
    backend.dates.append('2026-11-25')
    later = NOW + timedelta(days=1)
    request.update(start='2026-11-25', end='2026-11-25')
    assert sync(backend, cfg, request, later)['complete']
    assert backend.calls[-1]['start'] == '2026-11-23'
    assert backend.calls[-1]['end'] == '2026-11-25'


def test_factors_wait_for_all_base_publications_with_odd_batch(tmp_path):
    backend, cfg = Backend(), config(tmp_path)
    original = backend.fetch
    def fetch(task):
        if task['dataset'] == 'adj_factors':
            assert ('daily_bars', 'SH.510300', '2026-11-24') in backend.rows
        return original(task)
    backend.fetch = fetch
    assert sync(backend, cfg, {'symbols': ['SH.510300'], 'datasets': ['daily_bars', 'adj_factors'],
                              'start': '2026-11-23', 'end': '2026-11-24'}, NOW)['complete']


def test_configure_preserves_venv_interpreter_symlink(tmp_path):
    target = tmp_path / 'base-python'
    target.write_text('')
    interpreter = tmp_path / 'venv' / 'bin' / 'python'
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(target)
    result = CliRunner().invoke(app, ['data', 'history', 'configure', '--python', str(interpreter)])
    assert result.exit_code == 0
    assert settings.settings()['history']['python'] == str(interpreter)


def test_daily_and_minute_retries_stay_in_selected_scope(tmp_path):
    backend, cfg = Backend(), config(tmp_path)
    cfg['minute_symbols'] = ['SH.510300']
    backend.fail = True
    request = {'symbols': ['SH.510300'], 'datasets': ['minute_bars'], 'start': '2026-11-23', 'end': '2026-11-24'}
    sync(backend, cfg, request, NOW)
    backend.fail = False
    backend.calls.clear()
    sync(backend, cfg, {**request, 'datasets': ['daily_bars']}, NOW)
    assert {t['dataset'] for t in backend.calls} == {'daily_bars'}
    assert History(cfg).status()['batches']['failed'] == 1


def test_schedule_uses_current_python_xdg_and_beijing_time(tmp_path, monkeypatch):
    from strathive import schedule
    calls = []
    monkeypatch.setattr(schedule.subprocess, 'run', lambda args, **kw: calls.append(args))
    monkeypatch.setattr(schedule, 'command', lambda: ['/test/python', '-m', 'strathive'])
    assert schedule.enable('strathive-history-minute')
    root = Path(settings.config_dir()).parent / 'systemd' / 'user'
    service = (root / 'strathive-history-minute.service').read_text()
    timer = (root / 'strathive-history-minute.timer').read_text()
    assert '"/test/python" "-m" "strathive" data sync --period 1m --scheduled' in service
    assert 'XDG_DATA_HOME=' in service and 'TimeoutStartSec=6h' in service
    assert '18:15:00 Asia/Shanghai' in timer and 'Persistent=true' in timer
    assert calls[-1][-1] == 'strathive-history-minute.timer'


def test_zero_volume_and_amount_do_not_invent_a_missing_bar():
    row = normalize(raw(volume=0, amount='0'), '1d', 1)
    assert row['volume'] == 0 and row['amount'] == '0'
    assert coverage([], ['2026-11-23'], '1d')[0]['missing_count'] == 1
    assert coverage([row], ['2026-11-23'], '1d') == []
