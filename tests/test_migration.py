"""验证旧账本的迁移与回滚备份。"""

from pathlib import Path
import sqlite3

from alembic import command
from sqlalchemy import MetaData, Table

from strathive.bootstrap import build

from test_ledger import CATALOG, CUTOFF, FIRST


def test_legacy_funding_and_open_ended_run_migrate(tmp_path):
    ledger, store = build(tmp_path / "ledger.db")
    opened = "2026-11-01T01:00:00.000000+00:00"
    with store.engine.begin() as connection:
        store.migrations.attributes["connection"] = connection
        command.upgrade(store.migrations, "0001")
        store.migrations.attributes.clear()
        meta = MetaData()

        def insert(table, **values):
            connection.execute(Table(table, meta, autoload_with=connection).insert(), values)

        insert("strategy", id=1, name="旧策略", description="旧描述")
        insert("account", id=1, name="A", commission_rate="0.0003", opened_at=opened,
               initial_cash=100_000, current={"cash": "1000.00", "unallocated_cash": "400.00",
                                            "positions": [], "pools": {"2": {"cash": "500.00", "positions": []},
                                                                       "3": {"cash": "100.00", "positions": []}}})
        insert("pool", id=2, account_id=1, name="已绑定", initial_cash=0)
        insert("pool", id=3, account_id=1, name="未绑定", initial_cash=0)
        insert("run", id=7, strategy_id=1, pool_id=2, name="旧运行", status="ended")
        insert("ledger_event", id=8, account_id=1, pool_id=2, effective_at="2026-11-02T01:00:00.000000+00:00",
               kind="allocate", amount=50_000)
        insert("ledger_event", id=9, account_id=1, pool_id=3, effective_at="2026-11-02T01:00:00.000000+00:00",
               kind="allocate", amount=10_000)

    result = store.initialize()
    assert Path(result["migration_backup"]).is_file()
    run = ledger.runs(7)
    assert run["status"] == "paused" and run["legacy_status"] == "ended"
    assert run["version"]["description"] == "旧描述" and run["version"]["source"] == "migration"
    assert run["current"]["cash"] == "500.00"
    assert ledger.runs(8)["current"]["cash"] == "100.00"
    assert ledger.accounts(1)["current"]["unallocated_cash"] == "400.00"
    assert ledger.accounts(1)["commission_rate"] == ledger.accounts(1)["etf_commission_rate"] == "0.0003"
    ledger.close_run(7, "2026-11-03T09:00")
    assert ledger.accounts(1)["current"]["unallocated_cash"] == "900.00"
    with store.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []


def test_existing_uniform_rates_and_requests_survive_upgrade(tmp_path):
    ledger, store = build(tmp_path / "uniform.db")
    store.initialize()
    store.catalog.prepare(CATALOG)
    account = ledger.open_account("原账户", "1000000", "0.0003", CUTOFF, request_id="opening")
    strategy = ledger.create_strategy("策略")
    run = ledger.create_run(strategy["id"], account["id"], "运行")
    ledger.start_run(run["id"], "1000000", FIRST)
    trade = ledger.record_trade(run["id"], "159915", "buy", 10000, "10", FIRST, request_id="trade")
    before = ledger.accounts(account["id"])["current"]
    original_receipt = {key: value for key, value in account.items() if key != "etf_commission_rate"}
    # 恢复为 0003 的账户结构及建账回执，验证真实升级和重试路径。
    with store.transaction(write=True) as tx:
        tx.update("write_request", "opening", result=original_receipt)
        tx.connection.exec_driver_sql("ALTER TABLE account DROP COLUMN etf_commission_rate")
        tx.connection.exec_driver_sql("UPDATE alembic_version SET version_num = '0003'")

    result = store.initialize()
    with sqlite3.connect(result["migration_backup"]) as backup:
        assert backup.execute("SELECT version_num FROM alembic_version").fetchone() == ("0003",)
        assert "etf_commission_rate" not in {row[1] for row in backup.execute("PRAGMA table_info(account)")}
    migrated = ledger.accounts(account["id"])
    assert migrated["commission_rate"] == migrated["etf_commission_rate"] == "0.0003"
    assert migrated["current"] == before
    assert ledger.open_account("原账户", "1000000", "0.0003", CUTOFF, request_id="opening") == original_receipt
    assert len(ledger.accounts()) == 1
    assert ledger.record_trade(run["id"], "159915", "buy", 10000, "10", FIRST, request_id="trade") == trade
    ledger.edit_account(account["id"], etf_rate="0.0001")
    assert ledger.trade(trade["id"]) == trade
    added = ledger.record_trade(run["id"], "159915", "buy", 10000, "10", FIRST)
    assert added["fees"]["commission"] == "10.00"
