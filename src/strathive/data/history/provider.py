"""File-based subprocess protocol keeps CNEquity dependencies outside Core."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

from strathive import settings
from strathive.core.rules import LedgerError, require
from .bars import PERIODS, symbol, window
from .sync import space
from strathive.data.models import utcnow

from . import COMMIT


def defaults():
    return {'python': str(settings.data_dir() / 'history-env' / 'bin' / 'python'),
            'root': str(settings.data_dir() / 'history'), 'enabled': True, 'minute_symbols': [], 'minute_days': 60,
            'memory_limit': '2GB', 'threads': 2, 'fetch_workers': 2, 'write_batch_size': 100, 'warn_gb': 20, 'pause_gb': 10,
            'timeout_seconds': 21600}


def configuration(values=None):
    values = settings.settings() if values is None else values
    result = {**defaults(), **values.get('history', {})}
    require(isinstance(result['minute_symbols'], list), '分钟清单须为数组。')
    result['root'] = str(Path(result['root']).expanduser().resolve())
    result['python'] = str(Path(result['python']).expanduser().absolute())
    result['minute_symbols'] = sorted({symbol(s) for s in result['minute_symbols']})
    for key in ('minute_days', 'threads', 'fetch_workers', 'write_batch_size', 'timeout_seconds'):
        require(type(result[key]) is int and result[key] > 0, f'{key} 须为正整数。')
    require(result['write_batch_size'] <= 100, '历史写入批次上限为 100 个证券。')
    require(result['fetch_workers'] <= 2, '首版历史抓取并发上限为 2。')
    require(0 <= result['pause_gb'] <= result['warn_gb'], '空间阈值无效。')
    return result


class History:
    name = 'cnequity'

    def __init__(self, config=None):
        self.config = configuration() if config is None else configuration({'history': config})

    def status(self):
        root = Path(self.config['root'])
        from .component import receipt
        from .bundle import MANIFEST
        try:
            release = json.loads(MANIFEST.read_text())
        except (OSError, ValueError):
            release = {}
        result = {'enabled': self.config.get('enabled', True),
                  'managed': (receipt(self.config['python']) or {}).get('managed_by') == 'strathive',
                  'download_available': bool(release.get('artifact')), 'configured': 'history' in settings.settings(), 'installed': self.config.get('enabled', True) and Path(self.config['python']).is_file(),
                  'initialized': (root / 'lake' / 'catalog.sqlite').is_file(), 'config': self.config,
                  'version': '0.25.0', 'commit': COMMIT, **space(root, self.config)}
        # Status stays available with a missing interpreter or a damaged lake.
        path = root / 'strathive-sync.sqlite3'
        if path.is_file():
            import sqlite3
            try:
                with sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True) as db:
                    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    result['pending_plans'] = db.execute('SELECT count(*) FROM planning').fetchone()[0] if 'planning' in tables else 0
                    result['batches'] = dict(db.execute('SELECT status,count(*) FROM batch GROUP BY status'))
                    result['pending'] = [dict(zip(('dataset', 'symbol', 'start', 'end', 'status', 'detail'), r))
                                         for r in db.execute("SELECT dataset,symbol,start,end,status,detail FROM batch WHERE status!='complete'")]
            except sqlite3.Error:
                result['journal_error'] = 'history_journal_unavailable'
        return result

    def call(self, action, *, _locked=False, **arguments):
        require(self.config.get('enabled', True), '历史组件已卸载，请运行 strathive data history install。', 'history_backend_unavailable')
        if not _locked:
            from .component import access
            with access():
                return self.call(action, _locked=True, **arguments)
        python = Path(self.config['python'])
        require(python.is_file(), '历史组件未安装；请运行 strathive data history install。', 'history_backend_unavailable')
        with tempfile.TemporaryDirectory(prefix='strathive-history-') as temporary:
            request, response = Path(temporary) / 'request.json', Path(temporary) / 'response.json'
            request.write_text(json.dumps({'action': action, 'config': self.config, **arguments}), encoding='utf-8')
            env = {**os.environ,
                   'POLARS_MAX_THREADS': str(self.config['threads']), 'OMP_NUM_THREADS': str(self.config['threads'])}
            try:
                process = subprocess.run([str(python), '-I', str(Path(__file__).with_name('entry.py')), str(request), str(response)],
                                         env=env, capture_output=True, text=True, timeout=self.config['timeout_seconds'])
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise LedgerError('history_backend_unavailable', '历史进程无法启动或超时；已提交批次可续跑。') from exc
            require(response.is_file(), '历史进程退出，未返回结果；运行 data history doctor 检查环境。', 'history_backend_failed')
            result = json.loads(response.read_text(encoding='utf-8'))
            if not result['ok']:
                raise LedgerError(result['error'], result['message'])
            require(process.returncode == 0, '历史进程异常退出。', 'history_backend_failed')
            return result['data']

    def bars(self, symbols, start, end, period='1d', adjustment='none', output=None):
        window(start, end, utcnow())
        require(period in PERIODS, '不支持的 K 线周期。')
        require(adjustment in ('none', 'qfq', 'hfq'), '复权参数为 none、qfq 或 hfq。')
        require(bool(symbols), '历史查询须指定证券。')
        return self.call('bars', symbols=sorted({symbol(s) for s in symbols}), start=start, end=end,
                         period=period, adjustment=adjustment, output=str(Path(output).resolve()) if output else None)

    def calendar(self, market):
        require(market in ('SH', 'SZ'), '历史日历仅支持沪深。')
        return self.call('calendar')

    def close_prices(self, symbols, session_date):
        result = self.bars(list(symbols), session_date, session_date)
        return [{'symbol': row['symbol'], 'price': row['close'], 'kind': 'close', 'provider': self.name,
                 'as_of': None, 'session_date': session_date, 'retrieved_at': row['retrieved_at']}
                for row in result['rows']]
