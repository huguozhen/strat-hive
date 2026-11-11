"""策略运行账本的应用用例，持久化由存储适配器提供。"""

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext

from .requests import recoverable_write
from .replay import rebuild
from .rules import (
    COST_BASIS, SHANGHAI, LedgerError, decimal, fees_for, money,
    money_text, quantity, require, timestamp, to_cents,
)

TRANSITIONS = {"active": {"paused"}, "paused": {"active"}}


def name(value, label="name"):
    require(isinstance(value, str) and bool(value.strip()), f"{label} is required")
    return value.strip()


def commission(value):
    rate = decimal(value, "commission rate")
    require(rate <= Decimal("0.003"), "commission rate must be at most 0.003")
    return str(rate)


def resolve(find, value):
    value = name(value, "security")
    candidates = find(symbol=value.upper())
    if not candidates:
        field = "code" if value.isascii() and value.isdigit() else "name"
        candidates = find(**{field: value})
        if field == "name":
            by_symbol = {row["symbol"]: row for row in (*candidates, *find(full_name=value))}
            candidates = list(by_symbol.values())
    require(bool(candidates), f"unknown security: {value}", "not_found")
    require(len(candidates) == 1, f"ambiguous security: {value}; use SH.###### or SZ.######")
    return candidates[0]


class Ledger:
    """统一的账本业务入口，CLI 和其他客户端共用这里的校验与事务。"""

    def __init__(self, store):
        self.store = store

    def instrument(self, security):
        return resolve(self.store.catalog.find, security)

    def _instrument(self, tx, security, *, capture=False):
        """账本优先使用已入账的证券身份，首次入账时保存独立快照。"""
        def find(**filters):
            local = tx.find("instrument", **filters)
            if local and "symbol" in filters:
                return local
            by_symbol = {row["symbol"]: row for row in self.store.catalog.find(**filters)}
            by_symbol.update((row["symbol"], row) for row in local)
            return list(by_symbol.values())

        instrument = resolve(find, security)
        if not capture:
            return instrument
        existing = tx.find("instrument", symbol=instrument["symbol"])
        if existing:
            return existing[0]
        return tx.add("instrument", **{key: instrument[key] for key in
                      ("symbol", "code", "market", "kind", "name", "full_name")})

    def create_strategy(self, title, description=""):
        with self.store.transaction(write=True) as tx:
            strategy = tx.add("strategy", name=name(title), description=description)
            version = self._add_version(tx, strategy, 1)
            return {**strategy, "version": version}

    def strategies(self, strategy_id=None):
        with self.store.transaction() as tx:
            return tx.get("strategy", strategy_id) if strategy_id is not None else tx.find("strategy")

    def edit_strategy(self, strategy_id, title=None, description=None):
        changes = {}
        if title is not None:
            changes["name"] = name(title)
        if description is not None:
            changes["description"] = description
        with self.store.transaction(write=True) as tx:
            return tx.update("strategy", strategy_id, **changes)

    @staticmethod
    def _add_version(tx, strategy, number):
        return tx.add("strategy_version", strategy_id=strategy["id"], number=number,
                      created_at=datetime.now(timezone.utc).isoformat(), name=strategy["name"],
                      description=strategy["description"], source="created")

    @staticmethod
    def _resolve_version(tx, strategy_id, version):
        strategy_id = quantity(strategy_id)
        tx.get("strategy", strategy_id)
        versions = tx.find("strategy_version", strategy_id=strategy_id)
        if version is None:
            return max(versions, key=lambda row: row["number"])
        number = quantity(version)
        match = next((row for row in versions if row["number"] == number), None)
        require(match is not None, "strategy version does not exist", "not_found")
        return match

    def create_version(self, strategy_id):
        """固化策略当前资料，运行始终引用已保存的版本。"""
        with self.store.transaction(write=True) as tx:
            strategy = tx.get("strategy", strategy_id)
            latest = self._resolve_version(tx, strategy_id, None)
            return self._add_version(tx, strategy, latest["number"] + 1)

    def versions(self, strategy_id, version=None):
        with self.store.transaction() as tx:
            tx.get("strategy", strategy_id)
            if version is not None:
                return self._resolve_version(tx, strategy_id, version)
            return sorted(tx.find("strategy_version", strategy_id=strategy_id), key=lambda row: row["number"])

    def open_account(self, title, cash, rate, as_of, positions=(), allocations=(), *, request_id=None):
        """在同一事务创建账户和承接存量持仓的运行。"""
        return recoverable_write(self.store, "account.open", self._open_account, request_id, title=title, cash=cash, rate=rate,
                                 as_of=as_of, positions=positions, allocations=allocations)

    def _open_account(self, tx, title, cash, rate, as_of, positions, allocations):
        opened_at = timestamp(as_of)
        opened_date = datetime.fromisoformat(opened_at).astimezone(SHANGHAI).date()
        initial_cash = money(cash, "cash")
        require(isinstance(positions, (list, tuple)) and isinstance(allocations, (list, tuple)),
                "positions and allocations must be arrays")
        holdings = {}
        for row in positions:
            symbol = self._instrument(tx, row["security"], capture=True)["symbol"]
            require(symbol not in holdings, "duplicate opening security")
            try:
                position_date = date.fromisoformat(row["position_date"])
            except (TypeError, ValueError) as exc:
                raise LedgerError("invalid_input", "position_date must be an ISO date") from exc
            require(position_date <= opened_date, "position date is after account opening")
            holdings[symbol] = (quantity(row["quantity"]), str(decimal(row["unit_cost"], "unit cost")),
                                position_date.isoformat())
        remaining = {symbol: row[0] for symbol, row in holdings.items()}
        account = tx.add("account", name=name(title), commission_rate=commission(rate),
                         opened_at=opened_at, initial_cash=initial_cash, current={})
        for allocation in allocations:
            assigned = allocation["positions"]
            require(isinstance(assigned, list) and bool(assigned),
                    "opening allocations require at least one position")
            version = self._resolve_version(tx, allocation["strategy_id"], allocation.get("version"))
            run = tx.add("run", account_id=account["id"], strategy_version_id=version["id"],
                         name=name(allocation["run"]), status="active", started_at=opened_at,
                         opening_cash=money(allocation.get("cash", "0"), "opening run cash"))
            seen = set()
            for row in assigned:
                symbol = self._instrument(tx, row["security"])["symbol"]
                require(symbol not in seen, "duplicate opening security in the same run")
                seen.add(symbol)
                count = quantity(row["quantity"])
                require(remaining.get(symbol, 0) >= count, "opening allocation exceeds account position")
                remaining[symbol] -= count
                _, unit_cost, position_date = holdings[symbol]
                valuation = row.get("valuation_price")
                tx.add("opening_position", run_id=run["id"], symbol=symbol, quantity=count,
                       position_date=position_date, unit_cost=unit_cost,
                       valuation_price=None if valuation is None else str(decimal(valuation, "valuation price",
                                                                                 positive=True)))
        require(not any(remaining.values()), "all opening positions must be assigned to runs")
        return rebuild(tx, account["id"])

    def accounts(self, account_id=None):
        with self.store.transaction() as tx:
            return tx.get("account", account_id) if account_id is not None else tx.find("account")

    def edit_account(self, account_id, title=None, rate=None):
        changes = {}
        if title is not None:
            changes["name"] = name(title)
        if rate is not None:
            changes["commission_rate"] = commission(rate)
        with self.store.transaction(write=True) as tx:
            return tx.update("account", account_id, **changes)

    def create_run(self, strategy_id, account_id, title, version=None):
        """创建时解析版本；启动或恢复时仍使用同一条版本记录。"""
        with self.store.transaction(write=True) as tx:
            tx.get("account", account_id)
            selected = self._resolve_version(tx, strategy_id, version)
            run = tx.add("run", account_id=account_id, strategy_version_id=selected["id"],
                         name=name(title), status="draft", opening_cash=0)
            rebuild(tx, account_id)
            return {**run, "version": selected}

    def runs(self, run_id=None):
        with self.store.transaction() as tx:
            if run_id is None:
                versions = {row["id"]: row for row in tx.find("strategy_version")}
                return [{**run, "version": versions[run["strategy_version_id"]]} for run in tx.find("run")]
            run = tx.get("run", run_id)
            account = tx.get("account", run["account_id"])
            current = account["current"]["runs"].get(str(run_id))
            return {**run, "version": tx.get("strategy_version", run["strategy_version_id"]),
                    "current": current, "trades": self._trade_list(tx, run_id=run_id)}

    def transition(self, run_id, status):
        with self.store.transaction(write=True) as tx:
            run = tx.get("run", run_id)
            require(status in TRANSITIONS.get(run["status"], ()), "invalid run status transition", "ledger_conflict")
            return tx.update("run", run_id, status=status)

    def start_run(self, run_id, cash, effective_at):
        """固定本次运行的期初现金，后续资产变化由账本事实驱动。"""
        with self.store.transaction(write=True) as tx:
            run = tx.get("run", run_id)
            require(run["status"] == "draft", "only draft runs can start", "ledger_conflict")
            account = tx.get("account", run["account_id"])
            at = self._after_open(account, effective_at)
            self._event(tx, account["id"], "start", at, money(cash, "starting cash"), run_id)
            run = tx.update("run", run_id, status="active", started_at=at)
            current = rebuild(tx, account["id"])["current"]["runs"][str(run_id)]
            return {**run, "current": current}

    def close_run(self, run_id, effective_at, successor_run_id=None, successor_cash="0", valuations=(),
                  *, request_id=None):
        """原运行归零、现金归还和新运行承接在同一个事务内完成。"""
        return recoverable_write(self.store, "run.close", self._close_run, request_id, run_id=run_id, effective_at=effective_at,
                                 successor_run_id=successor_run_id, successor_cash=successor_cash,
                                 valuations=valuations)

    def _close_run(self, tx, run_id, effective_at, successor_run_id, successor_cash, valuations):
        run = tx.get("run", run_id)
        require(run["status"] in {"active", "paused"}, "only started runs can close", "ledger_conflict")
        account = tx.get("account", run["account_id"])
        at = self._after_open(account, effective_at)
        require(at >= run["started_at"], "close is before run start")
        existing = tx.find("ledger_event", run_id=run_id)
        require(all(event["effective_at"] <= at for event in existing),
                "close is before a recorded run event", "ledger_conflict")
        cash = money(successor_cash, "successor cash")
        require(isinstance(valuations, (list, tuple)), "valuations must be an array")
        marks = {}
        for row in valuations:
            symbol = self._instrument(tx, row["security"])["symbol"]
            require(symbol not in marks, f"duplicate valuation: {symbol}")
            marks[symbol] = {"price": str(decimal(row["price"], "valuation price", positive=True)),
                             "source": name(row["source"], "valuation source")}
        if successor_run_id is not None:
            successor = tx.get("run", successor_run_id)
            require(successor["status"] == "draft" and successor["account_id"] == account["id"],
                    "successor must be a draft run in the same account", "ledger_conflict")
            tx.update("run", successor_run_id, status="active", started_at=at)
        else:
            require(not cash and not marks, "successor cash and valuations require a successor")
        event = self._event(tx, account["id"], "close", at, cash, run_id,
                            {"successor_run_id": successor_run_id, "valuations": marks})
        run = tx.update("run", run_id, status="ended", closed_at=at)
        projections = rebuild(tx, account["id"])["current"]["runs"]
        current = projections[str(run_id)]
        transferred = {row["symbol"] for row in current["closing"]["positions"]}
        require(marks.keys() <= transferred, "valuations must refer to transferred holdings")
        return {**run, "event_id": event["id"], "current": current,
                "successor_run_id": successor_run_id,
                "successor": projections.get(str(successor_run_id))}

    @staticmethod
    def _event(tx, account_id, kind, at, amount=0, run_id=None, details=None):
        return tx.add("ledger_event", account_id=account_id, run_id=run_id,
                      kind=kind, amount=amount, effective_at=at, details=details or {})

    def move(self, account_id, kind, amount, effective_at):
        require(kind in {"deposit", "withdraw"}, "unknown cash operation")
        amount = money(amount, "cash amount", positive=True)
        with self.store.transaction(write=True) as tx:
            account = tx.get("account", account_id)
            event = self._event(tx, account_id, kind, self._after_open(account, effective_at), amount)
            rebuild(tx, account_id)
            return {**event, "amount": money_text(amount)}

    def record_trade(self, run_id, security, side, count, price, effective_at, overrides=None,
                     reason="", note="", *, request_id=None):
        """记录真实成交并在同一事务重算受影响的运行。"""
        return recoverable_write(self.store, "trade.add", self._record_trade, request_id, run_id=run_id, security=security,
                                 side=side, count=count, price=price, effective_at=effective_at,
                                 overrides=overrides, reason=reason, note=note)

    def _record_trade(self, tx, run_id, security, side, count, price, effective_at, overrides, reason, note):
        run = tx.get("run", run_id)
        account = tx.get("account", run["account_id"])
        effective_at = self._trade_time(account, run, effective_at)
        instrument = self._instrument(tx, security, capture=True)
        details = self._trade_details(instrument, side, count, price, effective_at,
                                      account["commission_rate"], overrides or {})
        event = self._event(tx, account["id"], "trade", effective_at, run_id=run_id)
        trade = tx.add("trade", id=event["id"], run_id=run_id, reason=reason, note=note, **details)
        rebuild(tx, account["id"])
        return self._present_trade(trade, event, instrument)

    def edit_trade(self, trade_id, changes):
        with self.store.transaction(write=True) as tx:
            trade = tx.get("trade", trade_id)
            previous_symbol = trade["symbol"]
            event = tx.get("ledger_event", trade_id)
            account = tx.get("account", event["account_id"])
            run = tx.get("run", trade["run_id"])
            effective_at = self._trade_time(account, run, changes.get("effective_at", event["effective_at"]),
                                            existing_at=event["effective_at"])
            instrument = self._instrument(tx, changes.get("security", trade["symbol"]), capture=True)
            details = {}
            if changes.keys() & {"effective_at", "security", "side", "quantity", "price", "overrides"}:
                overrides = {**trade["overrides"], **changes.get("overrides", {})}
                overrides = {key: value for key, value in overrides.items() if value is not None}
                details = self._trade_details(instrument, changes.get("side", trade["side"]),
                                              changes.get("quantity", trade["quantity"]),
                                              changes.get("price", trade["price"]), effective_at,
                                              trade["commission_rate"], overrides)
            trade = tx.update("trade", trade_id, **details, reason=changes.get("reason", trade["reason"]),
                              note=changes.get("note", trade["note"]))
            event = tx.update("ledger_event", trade_id, effective_at=effective_at)
            if trade["symbol"] != previous_symbol:
                tx.remove_unused_instrument(previous_symbol)
            rebuild(tx, account["id"])
            return self._present_trade(trade, event, instrument)

    def delete_trade(self, trade_id):
        with self.store.transaction(write=True) as tx:
            trade = tx.get("trade", trade_id)
            event = tx.get("ledger_event", trade_id)
            tx.remove("trade", trade_id)
            tx.remove("ledger_event", trade_id)
            tx.remove_unused_instrument(trade["symbol"])
            rebuild(tx, event["account_id"])
            return {"id": trade_id, "deleted": True}

    def trade(self, trade_id):
        with self.store.transaction() as tx:
            trade = tx.get("trade", trade_id)
            return self._present_trade(trade, tx.get("ledger_event", trade_id),
                                       tx.get("instrument", trade["symbol"]))

    def trades(self, run_id=None, security=None, start=None, end=None):
        with self.store.transaction() as tx:
            return self._trade_list(tx, run_id=run_id, security=security, start=start, end=end)

    def reconcile(self, account_id, cash, positions, cost_basis=None):
        cash = money(cash, "broker cash")
        require(isinstance(positions, (list, tuple)), "positions must be an array")
        with self.store.transaction() as tx:
            current = tx.get("account", account_id)["current"]
            expected = {p["symbol"]: p for p in current["positions"]}
            external = {}
            for row in positions:
                symbol = self._instrument(tx, row["security"])["symbol"]
                require(symbol not in external, f"duplicate broker position: {symbol}")
                count = quantity(row["quantity"], positive=False)
                unit_cost = row.get("unit_cost")
                external[symbol] = (count, None if unit_cost in (None, "") else decimal(unit_cost, "unit cost"))
            differences = []
            incomparable = []
            if cash != money(current["cash"], "account cash"):
                differences.append({"field": "cash", "ledger": current["cash"], "broker": money_text(cash)})
            run_totals = defaultdict(int)
            for run in current["runs"].values():
                for row in run["positions"]:
                    run_totals[row["symbol"]] += row["quantity"]
            for symbol in sorted(expected.keys() | external.keys() | run_totals.keys()):
                ledger = expected.get(symbol, {})
                ledger_qty = ledger.get("quantity", 0)
                broker_qty, broker_cost = external.get(symbol, (0, None))
                if ledger_qty != broker_qty:
                    differences.append({"field": "quantity", "symbol": symbol, "ledger": ledger_qty,
                                        "broker": broker_qty})
                if ledger_qty != run_totals[symbol]:
                    differences.append({"field": "run_quantity", "symbol": symbol, "ledger": ledger_qty,
                                        "runs": run_totals[symbol]})
                if ledger_qty and broker_qty:
                    if cost_basis != COST_BASIS or broker_cost is None:
                        incomparable.append(symbol)
                    elif Decimal(ledger["unit_cost"]) != broker_cost:
                        differences.append({"field": "unit_cost", "symbol": symbol, "ledger": ledger["unit_cost"],
                                            "broker": str(broker_cost)})
            run_cash = money(current["unallocated_cash"], "unallocated cash") + sum(
                money(run["cash"], "run cash") for run in current["runs"].values()
            )
            if run_cash != money(current["cash"], "account cash"):
                differences.append({"field": "run_cash", "ledger": current["cash"],
                                    "runs_and_unallocated": money_text(run_cash)})
            return {"account_id": account_id,
                    "status": "different" if differences else "partial" if incomparable else "matched",
                    "differences": differences, "cost_incomparable": incomparable}

    @classmethod
    def _trade_time(cls, account, run, value, *, existing_at=None):
        at = cls._after_open(account, value)
        require(run["started_at"] is not None and at >= run["started_at"],
                "trade is before run start", "ledger_conflict")
        require(run["closed_at"] is None or at < run["closed_at"] or at == existing_at,
                "trade is at or after run close", "ledger_conflict")
        return at

    @staticmethod
    def _after_open(account, effective_at):
        effective_at = timestamp(effective_at)
        require(effective_at > account["opened_at"], "operation must be later than the opening cutoff")
        return effective_at

    @staticmethod
    def _trade_details(instrument, side, count, price, effective_at, rate, overrides):
        require(side in {"buy", "sell"}, "side must be buy or sell")
        count = quantity(count)
        price = decimal(price, "price", positive=True)
        with localcontext() as ctx:
            ctx.prec = 50
            gross = price * count
            to_cents(gross)
            calculated, fees = fees_for(instrument["kind"], side, effective_at, gross, Decimal(rate), overrides)
        return {"symbol": instrument["symbol"], "side": side, "quantity": count, "price": str(price),
                "commission_rate": rate, "calculated": calculated, "fees": fees,
                "overrides": {key: str(value) for key, value in overrides.items()}}

    @staticmethod
    def _present_trade(trade, event, instrument):
        with localcontext() as ctx:
            ctx.prec = 50
            gross = to_cents(Decimal(trade["price"]) * trade["quantity"])
        return {**trade, "effective_at": event["effective_at"], "security_name": instrument["name"],
                "gross": money_text(gross), "total_fees": money_text(sum(trade["fees"].values())),
                **{key: {name: money_text(value) if value is not None else None for name, value in trade[key].items()}
                   for key in ("fees", "calculated")}}

    def _trade_list(self, tx, run_id=None, security=None, start=None, end=None):
        where = {}
        if run_id is not None:
            tx.get("run", run_id)
            where["run_id"] = run_id
        if security is not None:
            try:
                instrument = resolve(lambda **filters: tx.find("instrument", **filters), security)
            except LedgerError as exc:
                if exc.code != "not_found":
                    raise
                instrument = self.instrument(security)
            where["symbol"] = instrument["symbol"]
        start = timestamp(start) if start is not None else ""
        end = timestamp(end) if end is not None else "~"
        require(start <= end, "start must be at or before end")
        trades = tx.find("trade", **where)
        events = {row["id"]: row for row in tx.find("ledger_event", id=[trade["id"] for trade in trades])}
        instruments = {row["symbol"]: row for row in tx.find("instrument",
                       symbol=list({trade["symbol"] for trade in trades}))}
        results = [self._present_trade(trade, events[trade["id"]], instruments[trade["symbol"]]) for trade in trades
                   if start <= events[trade["id"]]["effective_at"] <= end]
        return sorted(results, key=lambda trade: (trade["effective_at"], trade["id"]))
