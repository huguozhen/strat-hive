"""从不可变的资金边界与成交事实重算账户及运行状态。"""

from dataclasses import dataclass, field
from decimal import Decimal, localcontext

from .rules import Balance, money_text, require, to_cents


@dataclass
class RunAssets:
    """运行当前资产与单独计量的期初投入、期末归还。"""

    balance: Balance
    initial_cash: int
    opening_value: Decimal = Decimal(0)
    opening_unpriced: list = field(default_factory=list)
    closing: dict | None = None
    legacy_funding: bool = False

    def add_opening(self, symbol, quantity, basis, mark):
        self.balance.buy(symbol, quantity, basis)
        if mark is None:
            self.opening_unpriced.append(symbol)
        else:
            self.opening_value += Decimal(mark) * quantity

    def snapshot(self):
        snapshot = self.balance.snapshot()
        return {**snapshot, "available_cash": snapshot["cash"], "initial_cash": money_text(self.initial_cash),
                "initial_capital": None if self.opening_unpriced or self.legacy_funding else
                money_text(to_cents(self.opening_value) + self.initial_cash),
                "opening_unpriced": sorted(self.opening_unpriced), "legacy_funding": self.legacy_funding,
                "closing": self.closing}


def close(event, runs, unallocated):
    """交接只改变运行归属，账户级证券数量和成本不参与此次更新。"""
    source = runs[event["run_id"]]
    cash = source.balance.cash
    unallocated.change_cash(cash)
    source.balance.cash = 0
    details = event["details"]
    successor_id = details["successor_run_id"]
    positions = []
    missing = []
    outgoing_value = Decimal(0)
    if successor_id is None:
        require(not source.balance.positions, "sell or hand off all holdings before closing", "ledger_conflict")
    else:
        target = runs[successor_id]
        starting_cash = event["amount"]
        unallocated.change_cash(-starting_cash)
        target.balance.change_cash(starting_cash)
        target.initial_cash = starting_cash
        for symbol, position in sorted(source.balance.positions.items()):
            mark = details["valuations"].get(symbol)
            price = mark["price"] if mark else None
            target.add_opening(symbol, position.quantity, position.basis, price)
            if price is None:
                missing.append(symbol)
            else:
                outgoing_value += Decimal(price) * position.quantity
            positions.append({"symbol": symbol, "quantity": position.quantity,
                              "book_cost": format(position.basis, "f"), "valuation": mark})
        source.balance.positions.clear()
    source.closing = {"returned_cash": money_text(cash), "successor_run_id": successor_id,
                      "positions": positions, "unpriced": missing,
                      "ending_value": None if missing else money_text(cash + to_cents(outgoing_value))}


def rebuild(tx, account_id):
    """按固定精度重放并写入当前投影，由调用方管理事务。"""
    with localcontext() as ctx:
        ctx.prec = 50
        return _rebuild(tx, account_id)


def _rebuild(tx, account_id):
    account = tx.get("account", account_id)
    account_balance = Balance(cash=account["initial_cash"])
    unallocated = Balance(cash=account["initial_cash"])
    runs = {row["id"]: RunAssets(Balance(cash=row["opening_cash"]), row["opening_cash"])
            for row in tx.find("run", account_id=account_id)}
    for run in runs.values():
        unallocated.change_cash(-run.initial_cash)
    for row in tx.find("opening_position", run_id=list(runs)):
        symbol, count = row["symbol"], row["quantity"]
        basis = Decimal(row["unit_cost"]) * count
        account_balance.buy(symbol, count, basis)
        runs[row["run_id"]].add_opening(symbol, count, basis, row["valuation_price"])
    events = tx.find("ledger_event", account_id=account_id)
    trades = {row["id"]: row for row in tx.find("trade", id=[e["id"] for e in events if e["kind"] == "trade"])}
    # 同一时刻沿用原始记账顺序；先启动、后成交、再交接的顺序可重放。
    for event in sorted(events, key=lambda row: (row["effective_at"], row["id"])):
        kind, amount = event["kind"], event["amount"]
        if kind in {"deposit", "withdraw"}:
            delta = amount if kind == "deposit" else -amount
            account_balance.change_cash(delta)
            unallocated.change_cash(delta)
        elif kind in {"start", "legacy_allocate", "legacy_release"}:
            run = runs[event["run_id"]]
            require(run.closing is None, "funding cannot follow the close event", "ledger_conflict")
            delta = -amount if kind == "legacy_release" else amount
            unallocated.change_cash(-delta)
            run.balance.change_cash(delta)
            if kind == "start":
                run.initial_cash = delta
            else:
                run.legacy_funding = True
        elif kind == "close":
            close(event, runs, unallocated)
        elif kind == "trade":
            trade = trades[event["id"]]
            run = runs[event["run_id"]]
            require(run.closing is None, "trade cannot follow the close event", "ledger_conflict")
            count, symbol = trade["quantity"], trade["symbol"]
            gross = to_cents(Decimal(trade["price"]) * count)
            fees = sum(trade["fees"].values())
            for balance in (account_balance, run.balance):
                if trade["side"] == "buy":
                    balance.change_cash(-gross - fees)
                    balance.buy(symbol, count, Decimal(gross + fees).scaleb(-2))
                else:
                    balance.sell(symbol, count)
                    balance.change_cash(gross - fees)
    current = {**account_balance.snapshot(), "unallocated_cash": money_text(unallocated.cash),
               "runs": {str(key): run.snapshot() for key, run in runs.items()}}
    return tx.update("account", account_id, current=current)
