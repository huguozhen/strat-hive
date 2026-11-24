"""Resumable historical batches; SQLite contains work receipts, never prices."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
import fcntl
import json
from pathlib import Path
import shutil
import sqlite3
import uuid

from strathive.core.rules import LedgerError, SHANGHAI, require
from .bars import day, window


@contextmanager
def writer(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'strathive-writer.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LedgerError('history_busy', '历史库已有同步／维护任务。') from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def space(root, config):
    path = Path(root)
    while not path.exists():
        path = path.parent
    free = shutil.disk_usage(path).free
    return {'free_bytes': free, 'warning': free < config.get('warn_gb', 20) * 10**9,
            'writes_paused': free < config.get('pause_gb', 10) * 10**9}


def writable(root, config):
    require(not space(root, config)['writes_paused'], '剩余空间低于历史写入阈值。', 'history_low_space')


class Journal:
    def __init__(self, root):
        self.path = Path(root) / 'strathive-sync.sqlite3'
        self.db = sqlite3.connect(self.path)
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS batch (
            id TEXT PRIMARY KEY, dataset TEXT, symbol TEXT, start TEXT, end TEXT,
            status TEXT, detail TEXT, updated_at TEXT);
          CREATE TABLE IF NOT EXISTS planning (id TEXT PRIMARY KEY, request TEXT);
          CREATE TABLE IF NOT EXISTS coverage (
            dataset TEXT, symbol TEXT, session_date TEXT, complete INTEGER,
            PRIMARY KEY(dataset,symbol,session_date));
        ''')

    def close(self):
        self.db.close()

    def pending(self):
        return [dict(zip(('id', 'dataset', 'symbol', 'start', 'end'), r)) for r in self.db.execute(
            "SELECT id,dataset,symbol,start,end FROM batch WHERE status != 'complete' ORDER BY rowid")]

    def add(self, dataset, symbol, first, last, now):
        task = dict(id=uuid.uuid4().hex, dataset=dataset, symbol=symbol, start=first, end=last)
        self.db.execute('INSERT INTO batch VALUES (?,?,?,?,?,?,?,?)',
                        (*task.values(), 'pending', '{}', now))
        self.db.commit()
        return task

    def status(self, task, status, detail, now):
        self.db.execute('UPDATE batch SET status=?,detail=?,updated_at=? WHERE id=?',
                        (status, json.dumps(detail, ensure_ascii=False), now, task['id']))
        self.db.commit()

    def covered(self, dataset, symbol):
        return {r[0] for r in self.db.execute('SELECT session_date FROM coverage WHERE dataset=? AND symbol=? AND complete=1',
                                             (dataset, symbol))}

    def record(self, task, sessions, missing):
        self.db.executemany('INSERT OR REPLACE INTO coverage VALUES (?,?,?,?)',
                            [(task['dataset'], task['symbol'], d, int(d not in missing)) for d in sessions])
        self.db.commit()


def _sync(backend, config, request, now):
    """Vendor calls run in at most two lanes; validated writes stay serial."""
    root = config['root']
    with writer(root):
        writable(root, config)
        if request.get('scheduled'):
            # Persistent timers can wake up on a holiday or before today's close.
            today = now.astimezone(SHANGHAI).date()
            final = today if now.astimezone(SHANGHAI).strftime('%H:%M') >= '15:05' else today - timedelta(days=1)
            dates = backend.sessions((final - timedelta(days=30)).isoformat(), final.isoformat())
            require(bool(dates), '日历无法确定已完成交易日。', 'history_calendar_gap')
            request = {**request, 'end': dates[-1]}
        journal = Journal(root)
        try:
            if request.get('retry_only'):
                tasks = journal.pending()
            else:
                # Refresh only required reference datasets; never invoke bare cne init/run.
                plan_id = request.get('_plan_id') or uuid.uuid4().hex
                journal.db.execute('INSERT OR REPLACE INTO planning VALUES (?,?)', (plan_id, json.dumps(request)))
                journal.db.commit()
                requested_datasets = request.get('datasets', ['daily_bars', 'adj_factors', 'minute_bars'])
                if set(requested_datasets) <= {'minute_bars', 'adj_factors'} and 'minute_bars' in requested_datasets and not request.get('symbols') and not config.get('minute_symbols'):
                    journal.db.execute('DELETE FROM planning WHERE id=?', (plan_id,))
                    journal.db.commit()
                    return {'complete': True, 'batches': [], 'pending_batches': len(journal.pending()), 'skipped': 'empty_minute_watchlist', **space(root, config)}
                scope = request.get('symbols') or (config.get('minute_symbols', []) if 'daily_bars' not in requested_datasets and 'minute_bars' in requested_datasets else None)
                backend.reference(scope)
                end = request.get('end') or now.astimezone(SHANGHAI).date().isoformat()
                today = now.astimezone(SHANGHAI)
                if request.get('end') and end == today.date().isoformat() and today.strftime('%H:%M') < '15:05':
                    raise LedgerError('history_session_unfinished', '盘后批量同步须等待北京时间 15:05；可查询已有历史。')
                if end == today.date().isoformat() and today.strftime('%H:%M') < '15:05':
                    end = (day(end) - timedelta(days=1)).isoformat()
                daily_start = request.get('start') or day(end).replace(year=day(end).year - 3,
                                            day=min(day(end).day, 28) if day(end).month == 2 else day(end).day).isoformat()
                window(daily_start, end, now)
                datasets = request.get('datasets', ['daily_bars', 'adj_factors', 'minute_bars'])
                require(set(datasets) <= {'daily_bars', 'adj_factors', 'minute_bars'}, '仅同步日线、因子与 1 分钟线。')
                tasks = []
                daily_symbols = request.get('symbols') or (backend.universe() if 'daily_bars' in datasets or datasets == ['adj_factors'] else [])
                minute_symbols = request.get('symbols') or config.get('minute_symbols', [])
                factor_symbols = sorted(set(daily_symbols) | set(minute_symbols if 'minute_bars' in datasets else []))
                allowed = {dataset: set(minute_symbols if dataset == 'minute_bars' else factor_symbols if dataset == 'adj_factors' else daily_symbols) for dataset in datasets}
                tasks = [t for t in journal.pending() if t['dataset'] in allowed and t['symbol'] in allowed[t['dataset']]]
                minute_start = daily_start
                for dataset in datasets:
                    symbols = minute_symbols if dataset == 'minute_bars' else factor_symbols if dataset == 'adj_factors' else daily_symbols
                    if not symbols:
                        continue
                    if dataset == 'minute_bars' and not request.get('start'):
                        dates = backend.sessions((day(end) - timedelta(days=730)).isoformat(), end)
                        count = config.get('minute_days', 60)
                        require(len(dates) >= count, '日历不足以换算分钟回填交易日。', 'history_calendar_gap')
                        start = dates[-count]
                        minute_start = start
                    else:
                        start = minute_start if dataset == 'adj_factors' and 'daily_bars' not in datasets and 'minute_bars' in datasets else daily_start
                    sessions = backend.sessions(start, end)
                    refresh = sessions[-(2 if dataset == 'minute_bars' else 5):]
                    for security in sorted(set(symbols)):
                        owed = set(sessions) - journal.covered(dataset, security)
                        owed.update(refresh)
                        if owed:
                            existing = [t for t in tasks if t['dataset'] == dataset and t['symbol'] == security]
                            if existing:
                                task = existing[0]
                                task['start'], task['end'] = min(task['start'], min(owed)), max(task['end'], max(owed))
                                journal.db.execute('UPDATE batch SET start=?,end=? WHERE id=?', (task['start'], task['end'], task['id']))
                                journal.db.commit()
                            else:
                                tasks.append(journal.add(dataset, security, min(owed), max(owed), now.isoformat()))
                journal.db.execute('DELETE FROM planning WHERE id=?', (plan_id,))
                journal.db.commit()
            results = []
            # Each source call returns either data or an exception; one symbol must not cancel others.
            def fetch(task):
                try:
                    return backend.fetch(task), None
                except Exception as exc:
                    return None, exc
            # Keep factors after base bars, including retries interrupted between the two commits.
            tasks.sort(key=lambda t: (t['dataset'] == 'adj_factors', t['dataset'], t['symbol']))
            workers = config.get('fetch_workers', 2)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                # Submit in bounded batches rather than buffering a whole-market minute sweep.
                stages = [[t for t in tasks if t['dataset'] != 'adj_factors'],
                          [t for t in tasks if t['dataset'] == 'adj_factors']]
                size = config.get('write_batch_size', 100)
                batches = [stage[offset:offset + size] for stage in stages for offset in range(0, len(stage), size)]
                # Separate datasets so each publication rewrites a partition only once for
                # many securities. One-symbol commits amplify retained backfill revisions.
                batches = [part for batch in batches for dataset in ('daily_bars', 'minute_bars', 'adj_factors')
                           if (part := [t for t in batch if t['dataset'] == dataset])]
                for batch in batches:
                    for task in batch:
                        journal.status(task, 'running', {}, now.isoformat())
                    fetched = list(pool.map(fetch, batch))
                    valid = [(task, data) for task, (data, error) in zip(batch, fetched) if error is None]
                    published = {}
                    if valid and hasattr(backend, 'publish_many'):
                        writable(root, config)
                        published = backend.publish_many(valid)
                    for task, (data, error) in zip(batch, fetched):
                        try:
                            if error:
                                raise error
                            writable(root, config)
                            receipt = published.get(task['id']) if published else backend.publish(task, data)
                            if isinstance(receipt, Exception):
                                raise receipt
                            sessions = backend.sessions(task['start'], task['end'])
                            missing = receipt['gaps']
                            journal.record(task, sessions, {r['session_date'] for r in missing})
                            state = 'partial' if missing else 'complete'
                            journal.status(task, state, receipt, now.isoformat())
                            results.append({**task, 'status': state, **receipt})
                        except Exception as exc:
                            code = exc.code if isinstance(exc, LedgerError) else 'history_source_failed'
                            detail = {'error': code, 'message': str(exc)}
                            journal.status(task, 'failed', detail, now.isoformat())
                            results.append({**task, 'status': 'failed', **detail})
                            if code == 'history_low_space':
                                raise
            return {'complete': all(r['status'] == 'complete' for r in results),
                    'batches': results, 'pending_batches': len(journal.pending()), **space(root, config)}
        finally:
            journal.close()


def sync(backend, config, request, now):
    """Also recover requests interrupted while preparing their reference data/scope."""
    planning = []
    if request.get('retry_only'):
        with writer(config['root']):
            journal = Journal(config['root'])
            try:
                planning = list(journal.db.execute('SELECT id,request FROM planning ORDER BY rowid'))
            finally:
                journal.close()
    recovered, failures = [], []
    for plan_id, payload in planning:
        try:
            recovered.append(_sync(backend, config, {**json.loads(payload), '_plan_id': plan_id}, now))
        except Exception as exc:
            failures.append({'plan_id': plan_id, 'message': str(exc)})
    result = _sync(backend, config, request, now)
    if planning:
        result['recovered_plans'] = recovered
        result['planning_failures'] = failures
        result['complete'] = result['complete'] and not failures and all(r['complete'] for r in recovered)
    return result
