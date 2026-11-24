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
                 value("etf_commission_rate"), value("opened_at"), value("initial_cash", Integer),
                 value("current", JSON))

versions = table("strategy_version", key("id", primary_key=True), key("strategy_id", "strategy.id"),
                 value("number", Integer), value("created_at"), value("name"), value("description"),
                 value("source"), UniqueConstraint("strategy_id", "number"))

runs = table("run", key("id", primary_key=True), key("strategy_version_id", "strategy_version.id"),
             key("account_id", "account.id", index=True), value("name"), value("status"),
             Column("started_at", String), Column("closed_at", String), Column("legacy_status", String),
             value("opening_cash", Integer))

opening = table("opening_position", key("id", primary_key=True), key("run_id", "run.id"),
                value("symbol", ForeignKey("instrument.symbol")), value("quantity", Integer),
                value("unit_cost"), value("position_date"), Column("valuation_price", String), Column("valuation_meta", JSON),
                UniqueConstraint("run_id", "symbol"))

events = table("ledger_event", key("id", primary_key=True), key("account_id", "account.id", index=True),
               Column("run_id", ForeignKey("run.id")), value("effective_at"), value("kind"),
               value("amount", Integer), value("details", JSON), sqlite_autoincrement=True)

requests = table("write_request", Column("id", String, primary_key=True), value("fingerprint"),
                 value("result", JSON))

trades = table("trade", key("id", "ledger_event.id", primary_key=True), key("run_id", "run.id"),
               value("symbol", ForeignKey("instrument.symbol")), value("side"),
               value("quantity", Integer), value("price"), value("commission_rate"),
               value("calculated", JSON), value("fees", JSON), value("overrides", JSON),
               value("reason"), value("note"))
