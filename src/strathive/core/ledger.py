"""Application use cases. Persistence is supplied through the store adapter."""

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, localcontext

from .rules import (
    COST_BASIS, SHANGHAI, Balance, LedgerError, decimal, fees_for, money,
    money_text, quantity, require, timestamp, to_cents,
)

TRANSITIONS = {
    "draft": {"active"},
    "active": {"paused", "ended"},
    "paused": {"active", "ended"},
    "ended": set(),
}


def name(value, label="name"):
    require(isinstance(value, str) and bool(value.strip()), f"{label} is required")
    return value.strip()


def commission(value):
    rate = decimal(value, "commission rate")
    require(rate <= Decimal("0.003"), "commission rate must be at most 0.003")
    return str(rate)


def resolve(tx, value):
    value = name(value, "security")
    candidates = tx.find("instrument", symbol=value.upper())
    if not candidates:
        field = "code" if value.isascii() and value.isdigit() else "name"
        candidates = tx.find("instrument", **{field: value})
        if field == "name":
            by_symbol = {row["symbol"]: row for row in (*candidates, *tx.find("instrument", full_name=value))}
            candidates = list(by_symbol.values())
    require(bool(candidates), f"unknown security: {value}", "not_found")
    require(len(candidates) == 1, f"ambiguous security: {value}; use SH.###### or SZ.######")
    return candidates[0]


class Ledger:
    def __init__(self, store):
        self.store = store

    def instrument(self, security):
        with self.store.transaction() as tx:
            return resolve(tx, security)

    def create_strategy(self, title, description=""):
        with self.store.transaction(write=True) as tx:
            return tx.add("strategy", name=name(title), description=description)

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

    def open_account(self, title, cash, rate, as_of, positions=(), allocations=()):
        opened_at = timestamp(as_of)
        opened_date = datetime.fromisoformat(opened_at).astimezone(SHANGHAI).date()
        initial_cash = money(cash, "cash")
        require(isinstance(positions, (list, tuple)) and isinstance(allocations, (list, tuple)),
                "positions and allocations must be arrays")
        with self.store.transaction(write=True) as tx:
            holdings = {}
            for row in positions:
                symbol = resolve(tx, row["security"])["symbol"]
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
                pool = tx.add("pool", account_id=account["id"], name=name(allocation["pool"]),
                              initial_cash=money(allocation.get("cash", "0"), "pool cash"))
                strategy = tx.add("strategy", name=name(allocation["strategy"]), description="")
                tx.add("run", pool_id=pool["id"], strategy_id=strategy["id"],
                       name=name(allocation["run"]), status="draft")
                seen = set()
                for row in assigned:
                    symbol = resolve(tx, row["security"])["symbol"]
                    require(symbol not in seen, "duplicate opening security in the same pool")
                    seen.add(symbol)
                    count = quantity(row["quantity"])
                    require(remaining.get(symbol, 0) >= count, "opening allocation exceeds account position")
                    remaining[symbol] -= count
                    _, unit_cost, position_date = holdings[symbol]
                    tx.add("opening_position", pool_id=pool["id"], symbol=symbol,
                           quantity=count, position_date=position_date, unit_cost=unit_cost)
            require(not any(remaining.values()), "all opening positions must be assigned to asset pools")
            return self._replay(tx, account["id"])

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

    def create_pool(self, account_id, title):
        with self.store.transaction(write=True) as tx:
            tx.get("account", account_id)
            pool = tx.add("pool", account_id=account_id, name=name(title), initial_cash=0)
            self._replay(tx, account_id)
            return pool

    def pools(self, pool_id=None, account_id=None):
        with self.store.transaction() as tx:
            if pool_id is not None:
                pool = tx.get("pool", pool_id)
                account = tx.get("account", pool["account_id"])
                return {**pool, "current": account["current"]["pools"][str(pool_id)]}
            return tx.find("pool", **({"account_id": account_id} if account_id is not None else {}))

    def edit_pool(self, pool_id, title):
        with self.store.transaction(write=True) as tx:
            return tx.update("pool", pool_id, name=name(title))

    def create_run(self, strategy_id, pool_id, title):
        with self.store.transaction(write=True) as tx:
            tx.get("strategy", strategy_id)
            tx.get("pool", pool_id)
            require(not tx.find("run", pool_id=pool_id), "asset pool is already bound", "ledger_conflict")
            return tx.add("run", strategy_id=strategy_id, pool_id=pool_id, name=name(title), status="draft")

    def runs(self, run_id=None):
        with self.store.transaction() as tx:
            if run_id is None:
                return tx.find("run")
            run = tx.get("run", run_id)
            pool = tx.get("pool", run["pool_id"])
            account = tx.get("account", pool["account_id"])
            return {**run, "account_id": account["id"], "current": account["current"]["pools"][str(pool["id"])],
                    "trades": self._trade_list(tx, run_id=run_id)}

    def transition(self, run_id, status):
        with self.store.transaction(write=True) as tx:
            run = tx.get("run", run_id)
            require(status in TRANSITIONS[run["status"]], "invalid run status transition", "ledger_conflict")
            return tx.update("run", run_id, status=status)

    def move(self, account_id, kind, amount, effective_at, pool_id=None):
        require(kind in {"deposit", "withdraw", "allocate", "release"}, "unknown cash operation")
        require((pool_id is not None) == (kind in {"allocate", "release"}), "invalid pool for cash operation")
        amount = money(amount, "cash amount", positive=True)
        with self.store.transaction(write=True) as tx:
            account = tx.get("account", account_id)
            if pool_id is not None:
                require(tx.get("pool", pool_id)["account_id"] == account_id, "pool belongs to another account")
            event = tx.add("ledger_event", account_id=account_id, pool_id=pool_id,
                           kind=kind, amount=amount, effective_at=self._after_open(account, effective_at))
            self._replay(tx, account_id)
            return {**event, "amount": money_text(amount)}

    def record_trade(self, run_id, security, side, count, price, effective_at, overrides=None, reason="", note=""):
        with self.store.transaction(write=True) as tx:
            run = tx.get("run", run_id)
            pool = tx.get("pool", run["pool_id"])
            account = tx.get("account", pool["account_id"])
            effective_at = self._after_open(account, effective_at)
            instrument = resolve(tx, security)
            details = self._trade_details(instrument, side, count, price, effective_at,
                                          account["commission_rate"], overrides or {})
            event = tx.add("ledger_event", account_id=account["id"], pool_id=pool["id"],
                           kind="trade", amount=0, effective_at=effective_at)
            trade = tx.add("trade", id=event["id"], run_id=run_id, reason=reason, note=note, **details)
            self._replay(tx, account["id"])
            return self._present_trade(trade, event, instrument)

    def edit_trade(self, trade_id, changes):
        with self.store.transaction(write=True) as tx:
            trade = tx.get("trade", trade_id)
            event = tx.get("ledger_event", trade_id)
            account = tx.get("account", event["account_id"])
            effective_at = self._after_open(account, changes.get("effective_at", event["effective_at"]))
            instrument = resolve(tx, changes.get("security", trade["symbol"]))
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
            self._replay(tx, account["id"])
            return self._present_trade(trade, event, instrument)

    def delete_trade(self, trade_id):
        with self.store.transaction(write=True) as tx:
            tx.get("trade", trade_id)
            event = tx.get("ledger_event", trade_id)
            tx.remove("trade", trade_id)
            tx.remove("ledger_event", trade_id)
            self._replay(tx, event["account_id"])
            return {"id": trade_id, "deleted": True}

    def trade(self, trade_id):
        with self.store.transaction() as tx:
            trade = tx.get("trade", trade_id)
            return self._present_trade(trade, tx.get("ledger_event", trade_id),
                                       resolve(tx, trade["symbol"]))

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
                symbol = resolve(tx, row["security"])["symbol"]
                require(symbol not in external, f"duplicate broker position: {symbol}")
                count = quantity(row["quantity"], positive=False)
                unit_cost = row.get("unit_cost")
                external[symbol] = (count, None if unit_cost in (None, "") else decimal(unit_cost, "unit cost"))
            differences = []
            incomparable = []
            if cash != money(current["cash"], "account cash"):
                differences.append({"field": "cash", "ledger": current["cash"], "broker": money_text(cash)})
            pool_totals = defaultdict(int)
            for pool in current["pools"].values():
                for row in pool["positions"]:
                    pool_totals[row["symbol"]] += row["quantity"]
            for symbol in sorted(expected.keys() | external.keys() | pool_totals.keys()):
                ledger = expected.get(symbol, {})
                ledger_qty = ledger.get("quantity", 0)
                broker_qty, broker_cost = external.get(symbol, (0, None))
                if ledger_qty != broker_qty:
                    differences.append({"field": "quantity", "symbol": symbol, "ledger": ledger_qty,
                                        "broker": broker_qty})
                if ledger_qty != pool_totals[symbol]:
                    differences.append({"field": "pool_quantity", "symbol": symbol, "ledger": ledger_qty,
                                        "pools": pool_totals[symbol]})
                if ledger_qty and broker_qty:
                    if cost_basis != COST_BASIS or broker_cost is None:
                        incomparable.append(symbol)
                    elif Decimal(ledger["unit_cost"]) != broker_cost:
                        differences.append({"field": "unit_cost", "symbol": symbol, "ledger": ledger["unit_cost"],
                                            "broker": str(broker_cost)})
            pool_cash = money(current["unallocated_cash"], "unallocated cash") + sum(
                money(pool["cash"], "pool cash") for pool in current["pools"].values()
            )
            if pool_cash != money(current["cash"], "account cash"):
                differences.append({"field": "pool_cash", "ledger": current["cash"],
                                    "pools_and_unallocated": money_text(pool_cash)})
            return {"account_id": account_id,
                    "status": "different" if differences else "partial" if incomparable else "matched",
                    "differences": differences, "cost_incomparable": incomparable}

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
            where["symbol"] = resolve(tx, security)["symbol"]
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

    def _replay(self, tx, account_id):
        with localcontext() as ctx:
            ctx.prec = 50
            return self._rebuild(tx, account_id)

    @staticmethod
    def _rebuild(tx, account_id):
        account = tx.get("account", account_id)
        account_balance = Balance(cash=account["initial_cash"])
        unallocated = Balance(cash=account["initial_cash"])
        pools = {pool["id"]: Balance(cash=pool["initial_cash"])
                 for pool in tx.find("pool", account_id=account_id)}
        allocated_cash = {key: pool.cash for key, pool in pools.items()}
        for pool in pools.values():
            unallocated.change_cash(-pool.cash)
        for row in tx.find("opening_position", pool_id=list(pools)):
            basis = Decimal(row["unit_cost"]) * row["quantity"]
            for balance in (account_balance, pools[row["pool_id"]]):
                balance.buy(row["symbol"], row["quantity"], basis)
        events = tx.find("ledger_event", account_id=account_id)
        trades = {row["id"]: row for row in tx.find("trade", id=[e["id"] for e in events if e["kind"] == "trade"])}
        for event in sorted(events, key=lambda row: (row["effective_at"], row["id"])):
            kind, amount = event["kind"], event["amount"]
            if kind in {"deposit", "withdraw"}:
                delta = amount if kind == "deposit" else -amount
                account_balance.change_cash(delta)
                unallocated.change_cash(delta)
            elif kind in {"allocate", "release"}:
                delta = amount if kind == "allocate" else -amount
                unallocated.change_cash(-delta)
                pool_id = event["pool_id"]
                pools[pool_id].change_cash(delta)
                allocated_cash[pool_id] += delta
            else:
                trade = trades[event["id"]]
                count, symbol = trade["quantity"], trade["symbol"]
                gross = to_cents(Decimal(trade["price"]) * count)
                fees = sum(trade["fees"].values())
                for balance in (account_balance, pools[event["pool_id"]]):
                    if trade["side"] == "buy":
                        balance.change_cash(-gross - fees)
                        balance.buy(symbol, count, Decimal(gross + fees).scaleb(-2))
                    else:
                        balance.sell(symbol, count)
                        balance.change_cash(gross - fees)
        current = {
            **account_balance.snapshot(), "unallocated_cash": money_text(unallocated.cash),
            "pools": {
                str(key): {**pool.snapshot(), "allocated_cash": money_text(allocated_cash[key]),
                           "available_cash": money_text(pool.cash)}
                for key, pool in pools.items()
            },
        }
        return tx.update("account", account_id, current=current)
