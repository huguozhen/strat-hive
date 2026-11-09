from sqlalchemy import JSON, Column, ForeignKey, Integer, MetaData, String, Table, UniqueConstraint

metadata = MetaData()

def table(name, *columns, **kwargs):
    return Table(name, metadata, *columns, **kwargs)

def key(name, target=None, **kwargs):
    args = [ForeignKey(target)] if target else []
    return Column(name, Integer, *args, nullable=False, **kwargs)

def value(name, kind=String, **kwargs):
    return Column(name, kind, nullable=False, **kwargs)

instruments = table("instrument", Column("symbol", String, primary_key=True),
                    value("code"), value("market"), value("kind"), value("name"), value("full_name"))

strategies = table("strategy", key("id", primary_key=True), value("name"), value("description"))

accounts = table("account", key("id", primary_key=True), value("name"), value("commission_rate"),
                 value("opened_at"), value("initial_cash", Integer), value("current", JSON))

pools = table("pool", key("id", primary_key=True), key("account_id", "account.id"), value("name"), value("initial_cash", Integer))

runs = table("run", key("id", primary_key=True), key("strategy_id", "strategy.id"),
             key("pool_id", "pool.id", unique=True), value("name"), value("status"))

opening = table("opening_position", key("id", primary_key=True), key("pool_id", "pool.id"),
                value("symbol", ForeignKey("instrument.symbol")), value("quantity", Integer),
                value("unit_cost"), value("position_date"), UniqueConstraint("pool_id", "symbol"))

events = table("ledger_event", key("id", primary_key=True), key("account_id", "account.id", index=True),
               Column("pool_id", ForeignKey("pool.id")), value("effective_at"), value("kind"),
               value("amount", Integer), sqlite_autoincrement=True)

trades = table("trade", key("id", "ledger_event.id", primary_key=True), key("run_id", "run.id"),
               value("symbol", ForeignKey("instrument.symbol")), value("side"),
               value("quantity", Integer), value("price"), value("commission_rate"),
               value("calculated", JSON), value("fees", JSON), value("overrides", JSON),
               value("reason"), value("note"))
