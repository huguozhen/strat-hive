"""验证旧账本的迁移与回滚备份。"""

from pathlib import Path

from alembic import command
from sqlalchemy import MetaData, Table

from strathive.bootstrap import build


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

    result = store.initialize([{"symbol": "SH.600000", "market": "SH", "code": "600000", "kind": "stock",
                                "name": "浦发银行", "full_name": "上海浦东发展银行股份有限公司"}])
    assert Path(result["migration_backup"]).is_file()
    run = ledger.runs(7)
    assert run["status"] == "paused" and run["legacy_status"] == "ended"
    assert run["version"]["description"] == "旧描述" and run["version"]["source"] == "migration"
    assert run["current"]["cash"] == "500.00"
    assert ledger.runs(8)["current"]["cash"] == "100.00"
    assert ledger.accounts(1)["current"]["unallocated_cash"] == "400.00"
    ledger.close_run(7, "2026-11-03T09:00")
    assert ledger.accounts(1)["current"]["unallocated_cash"] == "900.00"
    with store.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
