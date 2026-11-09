from decimal import Decimal

import pytest

from strathive.bootstrap import build
from strathive.core.rules import LedgerError, fees_for

CUTOFF = "2026-11-01T09:00:00"
FIRST = "2026-11-02T09:30:00"
SECOND = "2026-11-03T09:30:00"


@pytest.fixture
def ledger(tmp_path):
    app, store = build(tmp_path / "ledger.sqlite3")
    store.initialize([
        {"symbol": "SH.600000", "market": "SH", "code": "600000", "kind": "stock",
         "name": "浦发银行", "full_name": "上海浦东发展银行股份有限公司"},
        {"symbol": "SZ.159915", "market": "SZ", "code": "159915", "kind": "etf",
         "name": "创业板ETF", "full_name": "创业板交易型开放式指数证券投资基金"},
    ])
    return app


def holdings():
    return [{"security": "600000", "quantity": 100, "unit_cost": "9", "position_date": "2026-10-01"}]


def allocation(title, quantity):
    return {"strategy": title, "pool": title, "run": title,
            "positions": [{"security": "600000", "quantity": quantity}]}


def test_opening_allocation_must_balance_and_is_atomic(ledger):
    with pytest.raises(LedgerError, match="must be assigned"):
        ledger.open_account("Account", "1000", "0.0003", CUTOFF, holdings(), [allocation("A", 40)])
    assert ledger.accounts() == []
    assert ledger.pools() == []
    assert ledger.strategies() == []
    assert ledger.runs() == []


def test_split_holdings_trade_edit_delete_and_reconciliation(ledger):
    account = ledger.open_account("Account", "1000", "0.0003", CUTOFF, holdings(),
                                  [allocation("A", 40), allocation("B", 60)])
    assert account["current"]["positions"][0]["quantity"] == 100
    assert ledger.instrument("上海浦东发展银行股份有限公司")["symbol"] == "SH.600000"
    run, other = ledger.runs()
    ledger.transition(run["id"], "active")
    ledger.transition(run["id"], "paused")
    ledger.transition(run["id"], "ended")
    ledger.move(account["id"], "allocate", "200", FIRST, run["pool_id"])
    trade = ledger.record_trade(run["id"], "浦发银行", "sell", 10, "10", FIRST, {"commission": "0.20"})
    assert trade["fees"] == {"commission": "0.20", "stamp_duty": "0.05", "transfer_fee": "0.00"}
    result = ledger.edit_trade(trade["id"], {"price": "11", "note": "corrected"})
    assert result["note"] == "corrected"
    assert result["total_fees"] == "0.26"
    assert ledger.trades(run_id=run["id"], security="SH.600000", start=FIRST, end=FIRST) == [result]
    assert ledger.runs(other["id"])["current"]["positions"][0]["quantity"] == 60
    assert ledger.runs(run["id"])["current"]["allocated_cash"] == "200.00"
    assert ledger.runs(run["id"])["current"]["available_cash"] == "309.74"
    assert ledger.accounts(account["id"])["current"]["unallocated_cash"] == "800.00"

    broker = [{"security": "600000", "quantity": 90, "unit_cost": "9"}]
    assert ledger.reconcile(account["id"], "1109.74", broker)["status"] == "partial"
    assert ledger.reconcile(account["id"], "1109.74", broker, "moving_average_v1")["status"] == "matched"
    assert ledger.reconcile(account["id"], "1109.75", broker)["differences"][0]["field"] == "cash"

    ledger.delete_trade(trade["id"])
    assert ledger.accounts(account["id"])["current"]["cash"] == "1000.00"
    assert ledger.accounts(account["id"])["current"]["positions"][0]["quantity"] == 100
    new_trade = ledger.record_trade(run["id"], "600000", "sell", 1, "10", SECOND)
    assert new_trade["id"] > trade["id"]


def test_replay_failure_and_cost_reset(ledger):
    account = ledger.open_account("Cash only", "1000", "0.0003", CUTOFF)
    strategy = ledger.create_strategy("Strategy")
    pool = ledger.create_pool(account["id"], "Pool")
    run = ledger.create_run(strategy["id"], pool["id"], "Run")
    with pytest.raises(LedgerError, match="already bound"):
        ledger.create_run(strategy["id"], pool["id"], "Other")
    ledger.move(account["id"], "deposit", "10", FIRST)
    ledger.move(account["id"], "withdraw", "10", FIRST)
    ledger.move(account["id"], "allocate", "1000", FIRST, pool["id"])
    purchase = ledger.record_trade(run["id"], "159915", "buy", 10, "4", FIRST)
    ledger.record_trade(run["id"], "159915", "sell", 10, "4", SECOND)
    with pytest.raises(LedgerError, match="insufficient position"):
        ledger.delete_trade(purchase["id"])
    assert len(ledger.trades()) == 2
    latest = ledger.record_trade(run["id"], "159915", "buy", 10, "5", SECOND)
    assert latest["fees"] == {"commission": "5.00", "stamp_duty": "0.00", "transfer_fee": "0.00"}
    assert ledger.pools(pool["id"])["current"]["positions"][0]["unit_cost"] == "5.50000000"
    with pytest.raises(LedgerError, match="opening cutoff"):
        ledger.record_trade(run["id"], "159915", "buy", 10, "5", CUTOFF)


def test_fee_schedule_and_override():
    computed, actual = fees_for("stock", "sell", "2026-11-01T01:00:00+00:00",
                                Decimal("10000"), Decimal("0.0003"), {"commission": "0.25"})
    assert computed == {"commission": 500, "stamp_duty": 500, "transfer_fee": 10}
    assert actual == {**computed, "commission": 25}
    with pytest.raises(LedgerError, match="outside the built-in schedule"):
        fees_for("stock", "sell", "2022-01-01T01:00:00+00:00", Decimal("10000"), Decimal(0), {})
