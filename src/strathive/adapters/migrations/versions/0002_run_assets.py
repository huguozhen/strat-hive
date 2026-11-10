"""将历史资产归属迁入运行，并固化迁移时可取得的策略资料。"""

from datetime import datetime, timezone
from decimal import Decimal

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def create_tables():
    op.create_table('write_request',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('fingerprint', sa.String(), nullable=False),
    sa.Column('result', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('strategy_version',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('strategy_id', sa.Integer(), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('description', sa.String(), nullable=False),
    sa.Column('source', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['strategy_id'], ['strategy.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('strategy_id', 'number')
    )
    op.create_table('run',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('strategy_version_id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('started_at', sa.String(), nullable=True),
    sa.Column('closed_at', sa.String(), nullable=True),
    sa.Column('legacy_status', sa.String(), nullable=True),
    sa.Column('opening_cash', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account.id'], ),
    sa.ForeignKeyConstraint(['strategy_version_id'], ['strategy_version.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_run_account_id'), 'run', ['account_id'], unique=False)
    op.create_table('ledger_event',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.Column('effective_at', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('amount', sa.Integer(), nullable=False),
    sa.Column('details', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['run.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sqlite_autoincrement=True
    )
    op.create_index(op.f('ix_ledger_event_account_id'), 'ledger_event', ['account_id'], unique=False)
    op.create_table('opening_position',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('unit_cost', sa.String(), nullable=False),
    sa.Column('position_date', sa.String(), nullable=False),
    sa.Column('valuation_price', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['run_id'], ['run.id'], ),
    sa.ForeignKeyConstraint(['symbol'], ['instrument.symbol'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'symbol')
    )
    op.create_table('trade',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(), nullable=False),
    sa.Column('side', sa.String(), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('price', sa.String(), nullable=False),
    sa.Column('commission_rate', sa.String(), nullable=False),
    sa.Column('calculated', sa.JSON(), nullable=False),
    sa.Column('fees', sa.JSON(), nullable=False),
    sa.Column('overrides', sa.JSON(), nullable=False),
    sa.Column('reason', sa.String(), nullable=False),
    sa.Column('note', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['id'], ['ledger_event.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['run.id'], ),
    sa.ForeignKeyConstraint(['symbol'], ['instrument.symbol'], ),
    sa.PrimaryKeyConstraint('id')
    )


def upgrade():
    conn = op.get_bind()
    op.drop_index("ix_ledger_event_account_id", table_name="ledger_event")
    for table in ("trade", "opening_position", "ledger_event", "run"):
        op.rename_table(table, "legacy_" + table)
    create_tables()
    meta = sa.MetaData()

    def table(name):
        return sa.Table(name, meta, autoload_with=conn)

    def rows(name):
        return conn.execute(sa.select(table(name))).mappings()

    def insert(name, values):
        conn.execute(table(name).insert(), values)

    migrated_at = datetime.now(timezone.utc).isoformat()
    for strategy in rows("strategy"):
        insert("strategy_version", {"id": strategy["id"], "strategy_id": strategy["id"], "number": 1,
                                   "created_at": migrated_at, "name": strategy["name"],
                                   "description": strategy["description"], "source": "migration"})

    accounts = {row["id"]: row for row in rows("account")}
    old_runs = {row["pool_id"]: row for row in rows("legacy_run")}
    old_events = list(rows("legacy_ledger_event"))
    old_positions = list(rows("legacy_opening_position"))
    activity = {}
    for event in old_events:
        if event["pool_id"] is not None:
            activity[event["pool_id"]] = max(activity.get(event["pool_id"], ""), event["effective_at"])
    opening_ids = {row["pool_id"] for row in old_positions}
    run_ids = {}
    next_id = max((row["id"] for row in old_runs.values()), default=0) + 1
    closures = []
    for pool in rows("pool"):
        old = old_runs.get(pool["id"])
        legacy_status = old["status"] if old is not None else None
        account = accounts[pool["account_id"]]
        started = bool(pool["initial_cash"] or pool["id"] in activity or pool["id"] in opening_ids)
        if old is None:
            if not started:
                continue
            # 未绑定的历史资金同样保留来源，用户可关闭这条迁移运行并归还现金。
            strategy = conn.execute(table("strategy").insert().values(
                name=f"历史资金分配 · {pool['name']}", description="迁移时保留的历史资金记录")
                .returning(table("strategy"))).mappings().one()
            insert("strategy_version", {"id": strategy["id"], "strategy_id": strategy["id"], "number": 1,
                                       "created_at": migrated_at, "name": strategy["name"],
                                       "description": strategy["description"], "source": "migration"})
            old = {"id": next_id, "strategy_id": strategy["id"], "name": pool["name"], "status": "paused"}
            next_id += 1
        status = old["status"]
        started = started or status != "draft"
        closed_at = None
        if status == "ended":
            balance = account["current"].get("pools", {}).get(str(pool["id"]), {})
            if not balance.get("positions") and not Decimal(balance.get("cash", "0")):
                closed_at = activity.get(pool["id"], account["opened_at"])
                closures.append((pool["account_id"], old["id"], closed_at))
            else:
                status = "paused"
        if status == "draft" and started:
            status = "paused"
        run_ids[pool["id"]] = old["id"]
        insert("run", {"id": old["id"], "strategy_version_id": old["strategy_id"],
                       "account_id": pool["account_id"], "name": old["name"], "status": status,
                       "started_at": account["opened_at"] if started else None, "closed_at": closed_at,
                       "legacy_status": legacy_status, "opening_cash": pool["initial_cash"]})

    for row in old_positions:
        values = dict(row)
        values["run_id"] = run_ids[values.pop("pool_id")]
        insert("opening_position", values)
    migrated_events = []
    for row in old_events:
        values = dict(row)
        values["run_id"] = run_ids.get(values.pop("pool_id"))
        if values["kind"] in {"allocate", "release"}:
            values["kind"] = "legacy_" + values["kind"]
        values["details"] = {}
        migrated_events.append(values)
        insert("ledger_event", values)
    sequence = conn.exec_driver_sql("SELECT seq FROM sqlite_sequence WHERE name = 'legacy_ledger_event'").scalar()
    if sequence is not None:
        conn.exec_driver_sql("DELETE FROM sqlite_sequence WHERE name = 'ledger_event'")
        conn.exec_driver_sql("INSERT INTO sqlite_sequence(name, seq) VALUES ('ledger_event', ?)", (sequence,))
    for account_id, run_id, at in closures:
        insert("ledger_event", {"account_id": account_id, "run_id": run_id, "kind": "close", "amount": 0,
                                "effective_at": at, "details": {"successor_run_id": None, "valuations": {}}})
    old_trades = {row["id"]: dict(row) for row in rows("legacy_trade")}
    for values in old_trades.values():
        insert("trade", values)

    copied_events = {row["id"]: dict(row) for row in rows("ledger_event")}
    if any(copied_events.get(event["id"]) != event for event in migrated_events):
        raise RuntimeError("migration changed historical ledger events")
    if {row["id"]: dict(row) for row in rows("trade")} != old_trades:
        raise RuntimeError("migration changed historical trades")
    op.get_context().config.attributes["legacy_run_ids"] = run_ids
    for name in ("legacy_trade", "legacy_opening_position", "legacy_ledger_event", "legacy_run", "pool"):
        op.drop_table(name)


def downgrade():
    raise RuntimeError("Restore the pre-upgrade backup to return to revision 0001.")
