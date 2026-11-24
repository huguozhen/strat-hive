#!/usr/bin/env python3
"""Explicit four-security live sample and resource measurements; never a PR test."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import resource
import time

from strathive.data.history.provider import History


def size(root):
    return sum(p.stat().st_size for p in root.rglob('*') if p.is_file() and not p.is_symlink())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True, help='独立的新样本库')
    parser.add_argument('--report', type=Path, required=True)
    options = parser.parse_args()
    backend = History({'python': str(options.python.absolute()), 'root': str(options.root.resolve()),
                       'timeout_seconds': 600})
    started = time.monotonic()
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'results': {}}
    try:
        for action in ('doctor', 'init'):
            report['results'][action] = backend.call(action)
        end = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
        start = (datetime.now(timezone.utc).date() - timedelta(days=16)).isoformat()
        symbols = ['SH.600519', 'SZ.000001', 'SH.510300', 'SZ.159915']
        report['results']['sync'] = backend.call('sync', request={'symbols': symbols,
            'datasets': ['daily_bars', 'minute_bars', 'adj_factors'], 'start': start, 'end': end})
        for period in ('1d', '1m'):
            report['results'][period] = backend.bars(symbols, start, end, period, output=options.report.with_suffix('.' + period + '.jsonl'))
        report['results']['inspect'] = backend.call('inspect')
    except Exception as exc:
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)}
    report.update(elapsed_seconds=time.monotonic() - started, lake_bytes=size(options.root),
                  environment_bytes=size(options.python.absolute().parents[1]),
                  child_max_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(options.report)
    if 'error' in report or not report['results'].get('sync', {}).get('complete'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
