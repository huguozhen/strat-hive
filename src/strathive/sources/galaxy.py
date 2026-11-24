"""银河星耀数智证券目录适配器，仅在独立 SDK 进程中加载。"""

import re
import socket


class SourceError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def sdk():
    try:
        import AmazingData
        import tgw
    except (ImportError, OSError) as exc:
        raise SourceError("sdk_unavailable", "银河 SDK 或原生库加载失败，请重新配置数据源。") from exc
    return AmazingData


def connect(config):
    """先验证网络，再登录 SDK，以便安装向导给出对应的处理选项。"""
    ad = sdk()
    for attempt in range(2):
        try:
            with socket.create_connection((config["host"], config["port"]), timeout=4):
                break
        except OSError as exc:
            if attempt == 1:
                raise SourceError("endpoint_unreachable", "无法连接银河服务器，请检查地址或网络。") from exc
    try:
        signed_in = ad.login(**config)
    except Exception as exc:
        raise SourceError("login_failed", "银河登录失败，请检查账号、密码及账户权限。") from exc
    if not signed_in:
        raise SourceError("login_failed", "银河登录失败，请检查账号、密码及账户权限。")
    return ad


def read(config, *, check_only=False, previous_stocks=()):
    ad = connect(config)
    try:
        base = ad.BaseData()
        stocks = base.get_code_info(security_type="EXTRA_STOCK_A_SH_SZ")
        if stocks is None or stocks.empty:
            raise SourceError("catalog_unavailable", "银河没有返回股票目录，请检查证券数据权限。")
        if check_only:
            return True
        funds = base.get_code_info(security_type="EXTRA_ETF")
        info = ad.InfoData()
        return stock_rows(info, stocks) + fund_rows(funds) + delisted_rows(info, stocks, previous_stocks)
    finally:
        try:
            ad.logout(config["username"])
        except Exception:
            pass  # 子进程退出会释放连接，已取得的数据仍可使用。


def normalized(code):
    match = re.fullmatch(r"([0-9]{6})\.(SH|SZ)", str(code).strip().upper())
    if match:
        return match[2], match[1]
    return None


def entry(market, code, kind, name, full_name="", *, active=True):
    return dict(symbol=f"{market}.{code}", market=market, code=code, kind=kind,
                name=str(name).strip(), full_name=str(full_name).strip(), active=active)


def stock_rows(info, stocks):
    if not stocks.index.is_unique:
        raise SourceError("invalid_catalog", "银河股票目录包含重复代码。")
    codes = list(stocks.index)
    rows = []
    for start in range(0, len(codes), 800):
        batch = codes[start:start + 800]
        data = info.get_stock_basic(batch)
        details = data.set_index("MARKET_CODE")
        if not details.index.is_unique or not set(batch) <= set(details.index):
            raise SourceError("invalid_catalog", "银河股票基础信息缺失或包含重复代码。")
        for key, row in details.loc[batch].fillna("").iterrows():
            match = normalized(key)
            if match is None:
                raise SourceError("invalid_catalog", "银河返回了未知格式的股票代码。")
            if "主板" in str(row["LISTPLATE_NAME"]):
                rows.append(entry(*match, "stock", row["SECURITY_NAME"], row["COMP_NAME"],
                                  active=row["IS_LISTED"] not in (3, "3")))
    return rows


def fund_rows(funds):
    if funds is None or funds.empty or not funds.index.is_unique:
        raise SourceError("invalid_catalog", "银河 ETF 目录缺失或包含重复代码。")
    rows = []
    for key, name in funds["symbol"].fillna("").items():
        match = normalized(key)
        if match is not None:
            rows.append(entry(*match, "etf", name))
    return rows


def delisted_rows(info, stocks, previous_stocks):
    """仅在银河明确返回终止上市状态时标记已从每日目录消失的股票。"""
    missing = {f"{row['code']}.{row['market']}": row for row in previous_stocks}
    for key in stocks.index:
        missing.pop(key, None)
    if not missing:
        return []
    try:
        details = info.get_stock_basic(list(missing))
        terminated = details.loc[details["IS_LISTED"].isin((3, "3")), "MARKET_CODE"]
    except Exception:
        # 停用确认可在下次同步重试，未确认时保留原状态。
        return []
    return [{**missing[key], "active": False} for key in set(terminated) & missing.keys()]


def market(config, action, symbols, session_date, result_path):
    """Read only requested symbols. Subscription lifetime is bounded by the parent."""
    import json
    import os
    import threading
    from datetime import datetime, timezone
    from pathlib import Path

    ad = connect(config)
    base = ad.BaseData()
    if action == "calendar":
        days = base.get_calendar(market=symbols[0])
        ad.logout(config["username"])
        return [datetime.strptime(''.join(c for c in str(day) if c.isdigit())[:8], "%Y%m%d").date().isoformat() for day in days]
    codes = [f"{symbol.split('.')[1]}.{symbol.split('.')[0]}" for symbol in symbols]

    def row(symbol, value, kind, at, day):
        if isinstance(at, datetime):
            if at.tzinfo is None:
                from datetime import timedelta
                at = at.replace(tzinfo=timezone(timedelta(hours=8)))
            at = at.isoformat()
        else:
            at = None
        return dict(symbol=symbol, price=str(value), kind=kind, provider="galaxy", as_of=at,
                    session_date=day, retrieved_at=datetime.now(timezone.utc).isoformat())

    if action == "close":
        try:
            calendar = base.get_calendar()
            data = ad.MarketData(calendar).query_kline(codes, begin_date=int(session_date.replace('-', '')),
                        end_date=int(session_date.replace('-', '')), period=ad.constant.Period.day.value)
            rows = []
            for symbol, code in zip(symbols, codes):
                frame = data.get(code)
                if frame is not None and not frame.empty:
                    value = frame.iloc[-1]
                    rows.append(row(symbol, value['close'], 'close', None, session_date))
            return rows
        finally:
            ad.logout(config["username"])
    found = {}
    lock = threading.Lock()

    def finish():
        with lock:
            Path(result_path).write_text(json.dumps({"data": list(found.values())}), encoding="utf-8")
        os._exit(0)  # Terminates vendor's blocking subscription and native threads together.

    timer = threading.Timer(12, finish)
    timer.daemon = True
    timer.start()
    subscription = ad.SubscribeData()

    @subscription.register(code_list=codes, period=ad.constant.Period.snapshot.value)
    def snapshot(data, period):
        code = data.code
        if code not in codes:
            return
        symbol = symbols[codes.index(code)]
        at = data.trade_time
        with lock:
            found[symbol] = row(symbol, data.last, 'last', at, at.date().isoformat())
            complete = len(found) == len(symbols)
        if complete:
            finish()

    try:
        subscription.run()
        return list(found.values())
    finally:
        timer.cancel()
        ad.logout(config["username"])
