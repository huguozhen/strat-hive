from decimal import Decimal

import pytest

from strathive.bootstrap import build
from strathive.core.rules import LedgerError, fees_for

CUTOFF = "2026-11-01T09:00:00"
FIRST = "2026-11-02T09:30:00"
SECOND = "2026-11-03T09:30:00"

CATALOG = [
    {"symbol": "SH.600000", "market": "SH", "code": "600000", "kind": "stock",
     "name": "浦发银行", "full_name": "上海浦东发展银行股份有限公司"},
    {"symbol": "SZ.159915", "market": "SZ", "code": "159915", "kind": "etf",
     "name": "创业板ETF", "full_name": "创业板交易型开放式指数证券投资基金"},
]


@pytest.fixture
def ledger(tmp_path):
    app, store = build(tmp_path / "ledger.sqlite3")
    store.initialize()
    store.catalog.prepare(CATALOG)
    return app


def holdings():
    return [{"security": "600000", "quantity": 100, "unit_cost": "9", "position_date": "2026-10-01"}]


def allocation(strategy, title, count, valuation=None):
    return {"strategy_id": strategy["id"], "run": title, "positions": [
        {"security": "600000", "quantity": count, "valuation_price": valuation}
    ]}


def test_opening_allocation_is_atomic_and_retryable(ledger):
    strategy = ledger.create_strategy("A")
    with pytest.raises(LedgerError, match="must be assigned"):
        ledger.open_account("Account", "1000", "0.0003", CUTOFF, holdings(), [allocation(strategy, "A", 40)],
                            request_id="opening")
    assert ledger.accounts() == []
    assert ledger.runs() == []
    inputs = ("Account", "1000", "0.0003", CUTOFF, holdings(),
              [allocation(strategy, "A", 40, "10"), allocation(strategy, "B", 60)])
    account = ledger.open_account(*inputs, request_id="opening")
    assert ledger.open_account(*inputs, request_id="opening") == account
    assert account["current"]["positions"][0]["quantity"] == 100
    assert account["current"]["runs"]["1"]["initial_capital"] == "400.00"
    assert account["current"]["runs"]["2"]["initial_capital"] is None


def test_versions_handoff_and_late_trade_correction(ledger):
    strategy = ledger.create_strategy("A", "before")
    account = ledger.open_account("Account", "1000", "0.0003", CUTOFF, holdings(),
                                  [allocation(strategy, "A", 100, "10")])
    source, = ledger.runs()
    ledger.edit_strategy(strategy["id"], "B", "after")
    assert ledger.create_version(strategy["id"])["number"] == 2
    target = ledger.create_run(strategy["id"], account["id"], "B")
    assert target["version"]["description"] == "after"
    assert str(target["id"]) in ledger.accounts(account["id"])["current"]["runs"]
    assert ledger.runs(target["id"])["current"]["cash"] == "0.00"
    assert ledger.runs(source["id"])["version"]["description"] == "before"
    assert ledger.create_run(strategy["id"], account["id"], "old", version=1)["version"]["number"] == 1

    ledger.transition(source["id"], "paused")
    trade = ledger.record_trade(source["id"], "浦发银行", "sell", 10, "10", FIRST, {"commission": "0.20"},
                                request_id="sale")
    assert trade["total_fees"] == "0.25"
    assert ledger.record_trade(source["id"], "浦发银行", "sell", 10, "10", FIRST, {"commission": "0.20"},
                               request_id="sale") == trade
    with pytest.raises(LedgerError) as conflict:
        ledger.record_trade(source["id"], "浦发银行", "sell", 10, "11", FIRST, request_id="sale")
    assert conflict.value.code == "request_conflict"
    with pytest.raises(LedgerError, match="sell or hand off"):
        ledger.close_run(source["id"], SECOND)

    valuation = [{"security": "600000", "price": "11", "source": "sample"}]
    inputs = (source["id"], SECOND, target["id"], "200", valuation)
    closed = ledger.close_run(*inputs, request_id="close")
    assert ledger.close_run(*inputs, request_id="close") == closed
    assert closed["current"]["cash"] == "0.00"
    assert closed["current"]["positions"] == []
    assert closed["current"]["closing"]["ending_value"] == "1089.75"
    assert closed["successor"]["initial_capital"] == "1190.00"
    assert closed["successor"]["positions"][0]["unit_cost"] == "9.00000000"

    ledger.edit_trade(trade["id"], {"quantity": 20})
    assert ledger.runs(target["id"])["current"]["positions"][0]["quantity"] == 80
    assert ledger.runs(source["id"])["current"]["closing"]["returned_cash"] == "199.70"
    with pytest.raises(LedgerError, match="after run close"):
        ledger.record_trade(source["id"], "600000", "sell", 1, "10", "2026-11-04T09:30:00")
    assert ledger.reconcile(account["id"], "1199.70", holdings()[:0])["status"] == "different"
    broker = [{"security": "600000", "quantity": 80, "unit_cost": "9"}]
    assert ledger.reconcile(account["id"], "1199.70", broker, "moving_average_v1")["status"] == "matched"


def test_cash_only_close_rejects_historical_edit_that_leaves_holdings(ledger):
    account = ledger.open_account("Cash only", "1000", "0.0003", CUTOFF)
    strategy = ledger.create_strategy("Strategy")
    run = ledger.create_run(strategy["id"], account["id"], "Run")
    with pytest.raises(LedgerError, match="before run start"):
        ledger.record_trade(run["id"], "159915", "buy", 10, "4", FIRST)
    ledger.start_run(run["id"], "100", FIRST)
    with pytest.raises(LedgerError, match="only draft"):
        ledger.start_run(run["id"], "100", FIRST)
    purchase = ledger.record_trade(run["id"], "159915", "buy", 10, "4", FIRST)
    assert purchase["fees"] == {"commission": "5.00", "stamp_duty": "0.00", "transfer_fee": "0.00"}
    sale = ledger.record_trade(run["id"], "159915", "sell", 10, "4", SECOND)
    ledger.close_run(run["id"], SECOND)
    with pytest.raises(LedgerError, match="sell or hand off"):
        ledger.delete_trade(sale["id"])
    assert ledger.runs(run["id"])["current"]["closing"]["returned_cash"] == "90.00"
    assert ledger.accounts(account["id"])["current"]["unallocated_cash"] == "990.00"


def test_fee_schedule_and_override():
    computed, actual = fees_for("stock", "sell", "2026-11-01T01:00:00+00:00",
                                Decimal("10000"), Decimal("0.0003"), {"commission": "0.25"})
    assert computed == {"commission": 500, "stamp_duty": 500, "transfer_fee": 10}
    assert actual == {**computed, "commission": 25}
    with pytest.raises(LedgerError, match="outside the built-in schedule"):
        fees_for("stock", "sell", "2022-01-01T01:00:00+00:00", Decimal("10000"), Decimal(0), {})
