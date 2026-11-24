"""Decimal OHLCV contracts, strict adjustment and session-aware aggregation."""
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, localcontext
import re

from strathive.core.rules import LedgerError, SHANGHAI, require

PERIODS = ('1d', '1w', '1mo', '1q', '1m', '5m', '15m', '30m', '60m')
NUMBERS = ('open', 'high', 'low', 'close', 'amount')


def symbol(value):
    value = value.upper().strip()
    if re.fullmatch(r'\d{6}\.(SH|SZ)', value):
        value = '.'.join(reversed(value.split('.')))
    require(bool(re.fullmatch(r'(SH|SZ)\.\d{6}', value)), '历史证券标识须带沪深市场，如 SH.600519。')
    return value


def vendor_symbol(value):
    return '.'.join(reversed(symbol(value).split('.')))


def day(value):
    try:
        result = date.fromisoformat(value)
        require(result.isoformat() == value, '日期须为 YYYY-MM-DD。')
        return result
    except (ValueError, TypeError) as exc:
        raise LedgerError('invalid_input', '日期须为 YYYY-MM-DD。') from exc


def window(start, end, now):
    first, last = day(start), day(end)
    require(first <= last <= now.astimezone(SHANGHAI).date(), '日期范围倒置或包含未来日期。')
    return first, last


def number(value):
    try:
        result = Decimal(str(value))
        require(result.is_finite() and result >= 0, '历史价量必须为有限非负十进制数。', 'invalid_history_data')
        return result
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise LedgerError('invalid_history_data', '无效的历史价量字段。') from exc


def minute_ends(session):
    for hour, minute in ((9, 30), (13, 0)):
        opening = datetime.combine(day(session), time(hour, minute), SHANGHAI)
        for offset in range(1, 121):
            yield (opening + timedelta(minutes=offset)).isoformat()


def normalize(row, period, revision):
    """TDX daily volume is already converted by CNEquity; never multiply again."""
    session = str(row['trade_date'])
    day(session)
    result = {'symbol': symbol(row['symbol']), 'session_date': session, 'period': period,
              'adjustment': 'none', 'currency': 'CNY', 'amount_unit': 'CNY',
              'volume_unit': row.get('volume_unit', 'shares'),
              'source': row['source'], 'retrieved_at': str(row['fetched_at']),
              'data_version': row['data_version'], 'revision': revision}
    require(row['source'] != 'mock', '历史库拒绝模拟行情。', 'invalid_history_data')
    require(datetime.fromisoformat(result['retrieved_at']).tzinfo is not None,
            '历史获取时间须带时区。', 'invalid_history_data')
    for key in NUMBERS:
        result[key] = str(number(row[key]))
    prices = [number(result[k]) for k in ('open', 'high', 'low', 'close')]
    require(min(prices) > 0 and prices[2] <= min(prices[0], prices[3]) <= max(prices[0], prices[3]) <= prices[1],
            '历史 OHLC 区间无效。', 'invalid_history_data')
    volume = number(row['volume'])
    require(volume == int(volume), '成交量须为股／份整数。', 'invalid_history_data')
    require((volume == 0) == (number(result['amount']) == 0), '成交量和成交额的零值不一致。', 'invalid_history_data')
    result['volume'] = int(volume)
    if period == '1m':
        at = row['bar_time']
        at = datetime.fromisoformat(at) if isinstance(at, str) else at
        at = at.replace(tzinfo=SHANGHAI) if at.tzinfo is None else at.astimezone(SHANGHAI)
        closing_minute = at.hour * 60 + at.minute
        require(at.date().isoformat() == session and at.second == 0 and at.microsecond == 0
                and (571 <= closing_minute <= 690 or 781 <= closing_minute <= 900),
                '分钟线不在交易时段内。', 'invalid_history_data')
        result['interval_start'] = (at - timedelta(minutes=1)).isoformat()
        result['interval_end'] = at.isoformat()
    else:
        result['interval_start'] = session
        result['interval_end'] = session
    result['complete'] = True
    return result


def adjusted(rows, factors, adjustment, anchor=None):
    require(adjustment in ('none', 'qfq', 'hfq'), '复权参数为 none、qfq 或 hfq。')
    if adjustment == 'none':
        return rows, []
    missing, result = [], []
    with localcontext() as context:
        context.prec = 50
        for row in rows:
            factor = factors.get(row['session_date'])
            if factor is None or (adjustment == 'qfq' and anchor is None):
                missing.append(row['session_date'])
                continue
            multiple = number(factor)
            require(multiple > 0, '复权因子必须为正。', 'invalid_history_data')
            if adjustment == 'qfq':
                require(number(anchor) > 0, '前复权锚点必须为正。', 'invalid_history_data')
                multiple /= number(anchor)
            result.append({**row, **{k: str(number(row[k]) * multiple) for k in ('open', 'high', 'low', 'close')},
                           'adjustment': adjustment, 'factor': str(factor), 'factor_anchor': anchor})
    # An adjusted request never passes off a subset or raw prices as a complete read.
    return ([], sorted(set(missing))) if missing else (result, [])


def period_bounds(session, period):
    at = day(session)
    if period == '1w':
        first = at - timedelta(days=at.weekday())
        return first, first + timedelta(days=6)
    month = at.month if period == '1mo' else (at.month - 1) // 3 * 3 + 1
    last_month = month if period == '1mo' else month + 2
    return date(at.year, month, 1), date(at.year, last_month, monthrange(at.year, last_month)[1])


def aggregate(rows, period, sessions):
    require(period in PERIODS, '不支持的 K 线周期。')
    if period in ('1m', '1d'):
        return rows
    groups = defaultdict(list)
    for row in rows:
        if period.endswith('m'):
            width = int(period[:-1])
            end = datetime.fromisoformat(row['interval_end'])
            opening = end.replace(hour=9 if end.hour < 12 else 13, minute=30 if end.hour < 12 else 0)
            index = int((end - opening).total_seconds() / 60) - 1
            first = opening + timedelta(minutes=index // width * width)
            last = first + timedelta(minutes=width)
            key = row['symbol'], first.isoformat(), last.isoformat()
        else:
            first, last = period_bounds(row['session_date'], period)
            key = row['symbol'], first.isoformat(), last.isoformat()
        groups[key].append(row)
    result = []
    with localcontext() as context:
        context.prec = 50
        for (_, first, last), values in sorted(groups.items()):
            values.sort(key=lambda r: r['interval_end'])
            row = {**values[0], 'interval_start': first, 'interval_end': last, 'period': period,
                   'open': values[0]['open'], 'close': values[-1]['close'],
                   'high': str(max(number(r['high']) for r in values)),
                   'low': str(min(number(r['low']) for r in values)),
                   'volume': sum(r['volume'] for r in values),
                   'amount': str(sum((number(r['amount']) for r in values), Decimal(0))),
                   'source': sorted({r['source'] for r in values}),
                   'retrieved_at': max(r['retrieved_at'] for r in values),
                   'base_count': len(values)}
            row.pop('factor', None)
            if period.endswith('m'):
                expected = int(period[:-1])
                complete = len({r['interval_end'] for r in values}) == expected
            else:
                expected_days = {d for d in sessions if first <= d <= last}
                expected = len(expected_days)
                complete = bool(expected) and {r['session_date'] for r in values} == expected_days
            row.update(expected_count=expected, complete=complete)
            result.append(row)
    return result


def coverage(rows, sessions, period):
    by_date = defaultdict(set)
    for row in rows:
        by_date[row['session_date']].add(row['interval_end'])
    gaps = []
    for session in sessions:
        expected = set(minute_ends(session)) if period == '1m' else {session}
        missing = expected - by_date[session]
        if missing:
            gaps.append({'session_date': session, 'missing_count': len(missing), 'expected_count': len(expected)})
    return gaps
