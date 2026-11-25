"""Runs only in the isolated, pinned CNEquity environment."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import csv
import json
import os
import ssl
from pathlib import Path
import sys
import tempfile

from strathive.core.rules import LedgerError, SHANGHAI, require
from .bars import (NUMBERS, adjusted, aggregate, coverage, day, normalize, number,
                   period_bounds, symbol, vendor_symbol, window)
from . import COMMIT
from .bundle import RECEIPT
from .sync import space, sync, writable, writer


def verify_version():
    from importlib.metadata import distribution
    package = distribution('cnequity')
    require(package.version == '0.25.0', '历史后端须为 CNEquity 0.25.0。', 'history_version_mismatch')
    direct = json.loads(package.read_text('direct_url.json') or '{}')
    actual = direct.get('vcs_info', {}).get('commit_id')
    if actual != COMMIT and direct.get('url', '').startswith('file://'):
        # Development sample installs can use a verified checkout of the same commit.
        import subprocess
        checkout = Path(direct['url'].removeprefix('file://'))
        result = subprocess.run(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], capture_output=True, text=True)
        dirty = subprocess.run(['git', '-C', str(checkout), 'status', '--porcelain'], capture_output=True, text=True)
        actual = result.stdout.strip() if dirty.returncode == 0 and not dirty.stdout.strip() else None
    if actual != COMMIT:
        try:
            receipt = json.loads((Path(sys.prefix) / RECEIPT).read_text())
        except (OSError, ValueError):
            receipt = {}
        if (receipt.get('managed_by') == 'strathive' and receipt.get('component') == 'history'
                and receipt.get('cnequity_version') == '0.25.0' and receipt.get('commit') == COMMIT
                and len(receipt.get('artifact_sha256', '')) == 64):
            actual = COMMIT
    require(actual == COMMIT, '历史后端源码提交与锁定版本不符，请重新安装。', 'history_version_mismatch')
    return {'version': package.version, 'commit': actual}


class Backend:
    def __init__(self, config, create=False):
        import polars as pl
        from cnequity.config import Config
        from cnequity.domain.core_registry import core_spec
        from equitylake_core.catalog import Catalog
        from equitylake_core.lake import Lake
        from equitylake_core.spec import Registry
        self.pl, self.config = pl, config
        self.root = Path(config['root'])
        self.native = Config(data_root=self.root, workers=config['fetch_workers'],
                             duckdb_memory_limit=config['memory_limit'], duckdb_threads=config['threads'],
                             daily_waves=[], schedule_groups={}, events_groups={}, init_phases=[],
                             minute_bars_enabled=False, tdx_allow_mock=False,
                             sources={'sina': True, 'exchange': True})
        self.marker = self.root / 'strathive-history.json'
        if create:
            writable(self.root, config)
            require(not self.marker.exists() and not (self.root / 'lake').exists(),
                    '初始化要求新历史库，不迁移或覆盖已有历史库。')
        else:
            require(self.marker.is_file(), '请先运行 data history init 创建新历史库。', 'history_not_initialized')
            require(json.loads(self.marker.read_text()) == {'format': 1, 'commit': COMMIT},
                    '历史库格式或版本不匹配。', 'history_version_mismatch')
        specs = []
        for name in ('daily_bars', 'minute_bars', 'adj_factors', 'instruments', 'trading_calendar', 'etf_profiles'):
            columns = dict(core_spec(name).columns)
            # Decimal strings retain the provider's usable decimal representation without
            # imposing a fixed DECIMAL scale or another float roundtrip in our storage.
            for column in (*NUMBERS, 'pre_close', 'factor'):
                if column in columns:
                    columns[column] = 'VARCHAR'
            if name in ('daily_bars', 'minute_bars'):
                columns['volume_unit'] = 'VARCHAR'
            specs.append(core_spec(name, columns=columns))
        registry = Registry(specs)
        budgets = {'memory_limit': config['memory_limit'], 'threads': config['threads']}
        class LocalLake(Lake):
            def catalog(this, *, read_only=True):
                return Catalog(this.root, read_only=read_only, **budgets)
        home = self.root / 'lake'
        if create:
            # Initialize with the same resource budget as later reads and writes.
            from equitylake_core.lake import _prepare_records
            with Catalog(home, create=True, **budgets) as catalog:
                _prepare_records(catalog)
            self.marker.write_text(json.dumps({'format': 1, 'commit': COMMIT}))
        self.lake = LocalLake(home, registry)

    def sessions(self, start, end):
        from cnequity.query.calendar import list_trading_dates
        return [d.isoformat() for d in list_trading_dates(self.native, day(start), day(end))]

    def reference(self, explicit=None):
        from cnequity.adapters.tdx_protocol.client import fetch_instruments, fetch_trading_calendar, normalize_with_source
        from cnequity.adapters.exchange.etf_profiles import fetch_exchange_etf_profiles
        now = datetime.now(SHANGHAI)
        calendar = fetch_trading_calendar(date(2016, 1, 1), date(now.year, 12, 31))
        self.put('trading_calendar', normalize_with_source(calendar, 'exchange_calendar', dataset='trading_calendar'))
        if explicit:
            # Explicit scope can be sampled without downloading the entire current directory.
            from cnequity.domain.symbols import is_etf_symbol
            existing = set(self.lake.load('instruments')['symbol'].to_list())
            rows = [{'symbol': vendor_symbol(s), 'name': '', 'exchange': symbol(s).split('.')[0],
                     'asset_type': 'etf' if is_etf_symbol(*vendor_symbol(s).split('.')) else 'stock'} for s in explicit if vendor_symbol(s) not in existing]
            if rows:
                frame = self.pl.DataFrame(rows)
                self.put('instruments', normalize_with_source(frame, 'explicit_scope', dataset='instruments'))
            return
        instruments = normalize_with_source(fetch_instruments(config=self.native), 'tdx_protocol', dataset='instruments')
        import httpx
        with httpx.Client(verify=ssl.create_default_context(), timeout=30, headers={'User-Agent': 'Mozilla/5.0'}) as client:
            profiles, _ = fetch_exchange_etf_profiles(self.native, now.date(), client=client)
        profiles = normalize_with_source(profiles, 'exchange', dataset='etf_profiles')
        require(profiles.height > 0, '交易所 ETF 目录为空。', 'history_reference_failed')
        self.put('etf_profiles', profiles)
        # Only positively identified exchange ETFs join stocks. LOF classification is not inferred.
        funds = profiles.select('symbol', 'name', 'exchange', 'list_date', 'source', 'data_version', 'fetched_at').with_columns(self.pl.lit('etf').alias('asset_type'))
        instruments = self.pl.concat([instruments.filter(self.pl.col('asset_type') == 'stock'), funds], how='diagonal_relaxed')
        self.put('instruments', instruments)
        scope = self.root / 'current-symbols.json'
        temporary = scope.with_suffix('.tmp')
        temporary.write_text(json.dumps([symbol(s) for s in instruments['symbol'].to_list()]))
        os.replace(temporary, scope)

    def universe(self):
        scope = self.root / 'current-symbols.json'
        require(scope.is_file(), '当前证券清单尚未初始化。', 'history_reference_failed')
        return json.loads(scope.read_text())

    def listing(self, security, snapshot=None):
        rows = self.load('instruments', security, snapshot=snapshot).to_dicts()
        return str(rows[0]['list_date']) if rows and rows[0].get('list_date') else None

    def expected_sessions(self, security, start, end, snapshot=None):
        listed = self.listing(security, snapshot)
        return [d for d in self.sessions(start, end) if listed is None or d >= listed]

    def load(self, dataset, security, start=None, end=None, snapshot=None):
        return self.lake.load(dataset, start=start, end=end, snapshot=snapshot,
                              where=self.pl.col('symbol') == vendor_symbol(security))

    def put(self, dataset, frame):
        if frame.is_empty():
            return self.lake.snapshot()
        for name in (*NUMBERS, 'pre_close', 'factor'):
            if name in frame.columns:
                frame = frame.with_columns(self.pl.Series(name, [None if v is None else str(number(v)) for v in frame[name]], dtype=self.pl.String))
        writable(self.root, self.config)
        with self.lake.write() as write:
            count = write.put(dataset, frame)
            require(count == frame.height, '历史批次含重复键或无效字段，拒绝发布。', 'invalid_history_data')
            commit = write.commit()
        return commit.snapshot if commit else self.lake.snapshot()

    def fetch(self, task):
        from cnequity.adapters.tdx_protocol.client import fetch_daily_bars, fetch_minute_bars, normalize_with_source
        security, start, end = vendor_symbol(task['symbol']), day(task['start']), day(task['end'])
        dataset = task['dataset']
        if dataset == 'daily_bars':
            frame = fetch_daily_bars([security], start, end, backfill=True, config=self.native)
        elif dataset == 'minute_bars':
            # Do not clamp an older request and then claim the requested window is covered.
            frame, failed = fetch_minute_bars([security], start, end, frequency='1m', backfill=True,
                                            config=self.native, require_complete=False, workers=1)
            require(not failed or frame.height > 0, '分钟源返回失败。', 'history_source_failed')
        else:
            from cnequity.adapters.sina.adj_factors import fetch_adj_factor_series
            import httpx
            with httpx.Client(verify=ssl.create_default_context(), timeout=20) as client:
                series = fetch_adj_factor_series(security, 'hfq', config=self.native, client=client)
            # Align a cumulative vendor series only after its first observation. Missing
            # leading factors stay missing; no unit-factor fallback is permitted.
            all_dates = self.load('daily_bars', task['symbol'])['trade_date'].to_list()
            all_dates += self.load('minute_bars', task['symbol'])['trade_date'].to_list()
            values = sorted(series.iter_rows())
            rows, index, current = [], 0, None
            for session in sorted(set(all_dates)):
                while index < len(values) and values[index][0] <= session:
                    current = values[index][1]
                    index += 1
                if current is not None:
                    rows.append({'symbol': security, 'trade_date': session, 'adjust_type': 'hfq', 'factor': str(number(current))})
            if not rows:
                return self.pl.DataFrame()
            return normalize_with_source(self.pl.DataFrame(rows), 'sina', dataset=dataset)
        frame = normalize_with_source(frame, 'tdx_protocol', dataset=dataset)
        from cnequity.domain.symbols import is_etf_symbol
        return frame.with_columns(self.pl.lit('units' if is_etf_symbol(*security.split('.')) else 'shares').alias('volume_unit'))

    def validate(self, task, frame):
        dataset, security = task['dataset'], task['symbol']
        sessions = self.expected_sessions(security, task['start'], task['end'])
        rows = frame.to_dicts()
        require(all(symbol(r['symbol']) == security for r in rows), '源返回了请求外证券。', 'invalid_history_data')
        if dataset == 'adj_factors':
            require(all(number(r['factor']) > 0 for r in rows), '复权因子无效。', 'invalid_history_data')
        else:
            for row in rows:
                require(task['start'] <= str(row['trade_date']) <= task['end'] and str(row['trade_date']) in sessions,
                        '源返回了请求外日期。', 'invalid_history_data')
                normalize(row, '1m' if dataset == 'minute_bars' else '1d', None)

    def publish(self, task, frame, revision=None):
        self.validate(task, frame)
        dataset, security = task['dataset'], task['symbol']
        sessions = self.expected_sessions(security, task['start'], task['end'])
        rows = frame.to_dicts()
        revision = self.put(dataset, frame) if revision is None else revision
        local = [r for r in rows if task['start'] <= str(r['trade_date']) <= task['end']]
        present = {str(r['trade_date']) for r in local}
        expected = len(sessions) * (240 if dataset == 'minute_bars' else 1)
        if len(local) != expected or present != set(sessions):
            local = self.load(dataset, security, task['start'], task['end'], revision).to_dicts()
        if dataset == 'adj_factors':
            present = {str(r['trade_date']) for r in local}
            gaps = [{'session_date': d, 'missing_count': 1, 'expected_count': 1} for d in sessions if d not in present]
        else:
            period = '1m' if dataset == 'minute_bars' else '1d'
            gaps = coverage([normalize(r, period, revision) for r in local], sessions, period)
        return {'rows_received': len(rows), 'revision': revision, 'gaps': gaps,
                'source_window_trading_days': 95 if dataset == 'minute_bars' else None}

    def publish_many(self, pairs):
        """One snapshot per dataset/batch, with failed symbols tracked independently."""
        results, valid = {}, []
        for task, frame in pairs:
            try:
                self.validate(task, frame)
                valid.append((task, frame))
            except Exception as exc:
                results[task['id']] = exc
        if not valid:
            return results
        try:
            frames = [frame for _, frame in valid if not frame.is_empty()]
            revision = self.put(valid[0][0]['dataset'], self.pl.concat(frames, how='diagonal_relaxed')) if frames else self.lake.snapshot()
            for task, frame in valid:
                try:
                    results[task['id']] = self.publish(task, frame, revision)
                except Exception as exc:
                    results[task['id']] = exc
        except Exception as exc:
            for task, _ in valid:
                results[task['id']] = exc
        return results

    def query(self, request):
        start, end = request['start'], request['end']
        window(start, end, datetime.now(timezone.utc))
        period, adjustment = request.get('period', '1d'), request.get('adjustment', 'none')
        from .bars import PERIODS
        require(period in PERIODS and adjustment in ('none', 'hfq', 'qfq'), '周期或复权参数无效。')
        require(bool(request.get('symbols')), '历史查询须指定证券。')
        base = '1m' if period.endswith('m') else '1d'
        dataset = 'minute_bars' if base == '1m' else 'daily_bars'
        snapshot = self.lake.snapshot()
        sessions = self.sessions(start, end)
        bounds = (period_bounds(start, period)[0].isoformat(), period_bounds(end, period)[1].isoformat()) if period in ('1w', '1mo', '1q') else (start, end)
        aggregate_sessions = self.sessions(*bounds)
        summaries, all_rows, count = {}, [], 0
        target = request.get('output')
        with export(target) as emit:
            for security in request['symbols']:
                rows = [normalize(r, base, snapshot) for r in self.load(dataset, security, start, end, snapshot).to_dicts()]
                expected_sessions = self.expected_sessions(security, start, end, snapshot)
                gaps = coverage(rows, expected_sessions, base)
                factor_missing, factor_metadata = [], None
                if adjustment != 'none':
                    factors = self.load('adj_factors', security, snapshot=snapshot).sort('trade_date').to_dicts()
                    anchor = factors[-1]['factor'] if factors else None
                    factor_metadata = {'snapshot': snapshot, 'anchor_date': str(factors[-1]['trade_date']) if factors else None,
                                       'anchor': anchor if adjustment == 'qfq' else None,
                                       'sources': sorted({r['source'] for r in factors})}
                    rows, factor_missing = adjusted(rows, {str(r['trade_date']): r['factor'] for r in factors}, adjustment, anchor)
                listed = self.listing(security, snapshot)
                applicable = [d for d in aggregate_sessions if listed is None or d >= listed]
                bars = aggregate(rows, period, applicable)
                summaries[security] = {'gaps': gaps, 'missing_factors': factor_missing,
                                       'factors': factor_metadata, 'listed_on': listed, 'not_expected_sessions': len(sessions) - len(expected_sessions), 'complete': not gaps and not factor_missing and all(r['complete'] for r in bars)}
                for row in bars:
                    count += 1
                    if emit:
                        emit(row)
                    else:
                        require(count <= 10000, '结果超过 10000 根，请指定 --output 导出完整文件。', 'history_export_required')
                        all_rows.append(row)
            result = {'rows': all_rows if not target else None, 'row_count': count, 'snapshot': snapshot,
                      'adjustment': adjustment, 'period': period, 'start': start, 'end': end,
                      'coverage': summaries, 'complete': all(v['complete'] for v in summaries.values())}
            if target:
                result['output'] = target
                result['manifest'] = target + '.manifest.json'
                emit.manifest = result
        return result


@contextmanager
def export(target):
    """Stream complete results to a new file. Failure never leaves a partial export."""
    if not target:
        yield None
        return
    path = Path(target)
    require(path.suffix in ('.jsonl', '.csv'), '导出文件扩展名须为 .jsonl 或 .csv。')
    manifest = Path(str(path) + '.manifest.json')
    require(not path.exists() and not manifest.exists(), '导出路径已存在，请使用新文件名。')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.history-export-')
    metadata_temp = None
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
            csv_writer = None
            def emit(row):
                nonlocal csv_writer
                if path.suffix == '.jsonl':
                    stream.write(json.dumps(row, ensure_ascii=False) + '\n')
                else:
                    if csv_writer is None:
                        csv_writer = csv.DictWriter(stream, fieldnames=list(row))
                        csv_writer.writeheader()
                    csv_writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in row.items()})
            yield emit
            stream.flush()
            os.fsync(stream.fileno())
        meta_fd, metadata_temp = tempfile.mkstemp(dir=path.parent, prefix='.history-manifest-')
        with os.fdopen(meta_fd, 'w', encoding='utf-8') as stream:
            json.dump(emit.manifest, stream, ensure_ascii=False)
        os.link(metadata_temp, manifest)
        try:
            os.link(temporary, path)
        except BaseException:
            manifest.unlink()
            raise
    finally:
        Path(temporary).unlink(missing_ok=True)
        if metadata_temp:
            Path(metadata_temp).unlink(missing_ok=True)


def selftest(config):
    """An installation check in a disposable lake; no live collection or user data."""
    import polars as pl
    with tempfile.TemporaryDirectory(prefix='strathive-history-check-') as temporary:
        backend = Backend({**config, 'root': temporary, 'pause_gb': 0, 'warn_gb': 0}, create=True)
        row = {'symbol': '510300.SH', 'trade_date': date(2026, 11, 23), 'open': '3.123456789123',
               'high': '3.2', 'low': '3.1', 'close': '3.15', 'volume': 100, 'amount': '313.125',
               'volume_unit': 'units', 'source': 'offline_fixture', 'data_version': 'v2',
               'fetched_at': datetime.now(timezone.utc)}
        backend.put('daily_bars', pl.DataFrame([row]))
        query = {'symbols': ['SH.510300'], 'start': '2026-11-23', 'end': '2026-11-23'}
        result = backend.query(query)
        require(result['row_count'] == 1 and result['rows'][0]['open'] == row['open'],
                '历史组件存储样本校验失败。', 'history_selftest_failed')
        missing = backend.query({**query, 'adjustment': 'hfq'})
        require(not missing['complete'] and not missing['rows'], '复权缺口校验失败。', 'history_selftest_failed')
    return {'passed': True, 'live_collection': False, 'checks': ['storage', 'decimal', 'strict_adjustment']}


def execute(request):
    version = verify_version()
    action, config = request['action'], request['config']
    if action == 'doctor':
        import duckdb
        from equitylake_core.extensions import ensure_extensions
        with duckdb.connect() as connection:
            extensions = ensure_extensions(connection)
        from importlib.metadata import version as package_version
        dependencies = {name: package_version(name) for name in ('duckdb', 'polars', 'pyarrow', 'pandas', 'numpy', 'click', 'rich')}
        return {**version, 'extensions': extensions, 'dependencies': dependencies, 'python': sys.version, **space(config['root'], config)}
    if action == 'selftest':
        return selftest(config)
    if action == 'init':
        with writer(config['root']):
            backend = Backend(config, create=True)
        return {**version, 'root': config['root'], 'snapshot': backend.lake.snapshot()}
    backend = Backend(config)
    if action == 'validate':
        return {'initialized': True, 'snapshot': backend.lake.snapshot()}
    if action == 'sync':
        return sync(backend, config, request['request'], datetime.now(timezone.utc))
    if action == 'bars':
        return backend.query(request)
    if action == 'calendar':
        return backend.sessions('2016-01-01', f'{datetime.now(SHANGHAI).year}-12-31')
    if action == 'inspect':
        from dataclasses import asdict
        return {'datasets': [asdict(s) for s in backend.lake.summary()], 'revisions': backend.lake.revisions(), **space(config['root'], config)}
    if action == 'maintain':
        # Compaction is explicit. Snapshot expiration and old-file deletion are deliberately
        # absent: retention requires a separately reviewed backup/retention policy.
        with writer(config['root']):
            writable(config['root'], config)
            with backend.lake.catalog(read_only=False) as catalog:
                catalog.merge_small_files()
        return {'compacted': True, 'history_deleted': False}
    raise LedgerError('invalid_input', '未知历史操作。')


def main():
    request, response = map(Path, sys.argv[1:])
    try:
        result = {'ok': True, 'data': execute(json.loads(request.read_text(encoding='utf-8')))}
    except Exception as exc:
        result = {'ok': False, 'error': exc.code if isinstance(exc, LedgerError) else 'history_backend_failed', 'message': str(exc)}
    response.write_text(json.dumps(result, default=lambda value: value.isoformat() if hasattr(value, 'isoformat') else str(value), ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
