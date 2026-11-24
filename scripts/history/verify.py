#!/usr/bin/env python3
"""Offline real-storage check in the isolated CNEquity interpreter; synthetic fixtures only."""
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import tempfile

from strathive.data.history.worker import Backend, verify_version


def main():
    import polars as pl
    from cnequity.adapters.tdx_protocol.bars import _parse_bar_rows
    from strathive.data.history.bars import minute_ends
    version = verify_version()
    with tempfile.TemporaryDirectory(prefix='strathive-history-verify-') as temporary:
        config = {'root': temporary, 'fetch_workers': 2, 'threads': 2, 'memory_limit': '2GB',
                  'warn_gb': 20, 'pause_gb': 10}
        backend = Backend(config, create=True)
        # The official ETF list can contain securities absent from TDX. Exercise
        # that union and the raw directory frame's missing provenance offline.
        from unittest.mock import patch
        observed = date(2026, 11, 24)
        stock = pl.DataFrame([{'symbol': '000001.SZ', 'name': 'fixture stock', 'exchange': 'SZ', 'asset_type': 'stock'}])
        profiles = pl.DataFrame([{'symbol': code, 'as_of_date': observed, 'name': 'fixture fund', 'exchange': market,
                                 'list_date': date(2020, 1, 1), 'source': 'offline_fixture'}
                                for code, market in [('510300.SH', 'SH'), ('159915.SZ', 'SZ')]])
        with patch('cnequity.adapters.tdx_protocol.client.fetch_instruments', return_value=stock), patch(
                'cnequity.adapters.exchange.etf_profiles.fetch_exchange_etf_profiles', return_value=(profiles, [])):
            backend.reference()
        assert set(backend.universe()) == {'SZ.000001', 'SH.510300', 'SZ.159915'}
        assert backend.lake.load('etf_profiles')['fetched_at'].null_count() == 0
        backend.reference(['SH.510300', 'SZ.000001'])
        assert set(backend.lake.load('instruments')['asset_type']) == {'stock', 'etf'}
        now = datetime.now(timezone.utc)
        rows = [{'symbol': '510300.SH', 'trade_date': date(2026, 11, day),
                 'open': '3.123456789123', 'high': '3.2', 'low': '3.1', 'close': '3.15',
                 'volume': 100, 'amount': '313.123456789', 'source': 'offline_fixture',
                 'data_version': 'v2', 'fetched_at': now, 'volume_unit': 'units'} for day in (23, 24)]
        frame = pl.DataFrame(rows)
        original = backend.put('daily_bars', frame)
        assert backend.put('daily_bars', frame) == original, 'identical upsert must not duplicate or rewrite'
        tasks = [{'id': key, 'dataset': 'daily_bars', 'symbol': security, 'start': '2026-11-23', 'end': '2026-11-24'}
                 for key, security in [('fund', 'SH.510300'), ('stock', 'SZ.000001')]]
        stock_frame = frame.with_columns(pl.lit('000001.SZ').alias('symbol'), pl.lit('shares').alias('volume_unit'))
        receipts = backend.publish_many([(tasks[0], frame), (tasks[1], stock_frame)])
        assert receipts['fund']['revision'] == receipts['stock']['revision']
        absent = backend.query({'symbols': ['SZ.000001'], 'start': '2026-11-23', 'end': '2026-11-24', 'adjustment': 'hfq'})
        assert not absent['complete'] and absent['rows'] == []
        assert absent['coverage']['SZ.000001']['missing_factors'] == ['2026-11-23', '2026-11-24']
        factors = pl.DataFrame([{'symbol': '510300.SH', 'trade_date': date(2026, 11, day),
                                'adjust_type': 'hfq', 'factor': factor, 'source': 'offline_fixture',
                                'data_version': 'v1', 'fetched_at': now} for day, factor in ((23, '2'), (24, '4'))])
        backend.put('adj_factors', factors)
        request = {'symbols': ['SH.510300'], 'start': '2026-11-23', 'end': '2026-11-24'}
        raw = backend.query(request)
        assert raw['row_count'] == 2 and raw['complete']
        assert raw['rows'][0]['open'] == '3.123456789123'
        weekly = backend.query({**request, 'period': '1w', 'adjustment': 'qfq'})
        assert Decimal(weekly['rows'][0]['open']) == Decimal('3.123456789123') / 2
        assert not weekly['complete'], 'week is unfinished'
        minute = [{**rows[0], 'bar_time': datetime.fromisoformat(at).replace(tzinfo=None), 'frequency': '1m'}
                  for at in list(minute_ends('2026-11-23'))[:4]]
        backend.put('minute_bars', pl.DataFrame(minute))
        query = backend.query({**request, 'period': '5m'})
        assert not query['rows'][0]['complete'] and query['rows'][0]['base_count'] == 4
        file = str(Path(temporary) / 'bars.jsonl')
        exported = backend.query({**request, 'output': file})
        assert exported['row_count'] == len(Path(file).read_text().splitlines()) == 2
        assert json.loads(Path(file + '.manifest.json').read_text())['snapshot'] == exported['snapshot']
        revision = {**rows[0], 'close': '3.14'}
        backend.put('daily_bars', pl.DataFrame([revision]))
        assert backend.load('daily_bars', 'SH.510300', snapshot=original)['close'][0] == '3.15'
        assert backend.query(request)['rows'][0]['close'] == '3.14'
        with backend.lake.catalog(read_only=False) as catalog:
            catalog.merge_small_files()
        assert backend.load('daily_bars', 'SH.510300', snapshot=original)['close'][0] == '3.15'
        # Exercise the pinned vendor's daily boundary conversion, not a duplicated formula.
        decoded = _parse_bar_rows(pl.DataFrame([{'datetime': '2026-11-23', 'open': 3.1, 'high': 3.2,
                                  'low': 3.0, 'close': 3.15, 'volume': 2, 'amount': 623.5}]),
                                  '510300.SH', date(2026, 11, 23), date(2026, 11, 23))
        assert decoded[0]['volume'] == 200 and decoded[0]['amount'] == 623.5
        print(json.dumps({**version, 'checks': ['current_directory_union', 'batched_publication', 'decimal_storage', 'idempotent_upsert', 'raw_query', 'strict_adjustment',
              'adjust_before_aggregation', 'minute_gaps', 'complete_export', 'retained_revision', 'vendor_daily_units'],
              'fixture': 'synthetic_offline_only', 'snapshots': backend.lake.snapshot()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
