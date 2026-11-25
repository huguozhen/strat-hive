"""Command line and machine-readable interface."""

import csv
import json
import sqlite3
import sys
from pathlib import Path
from typing import Annotated

import click
import typer
from alembic.util import CommandError
from sqlalchemy.exc import SQLAlchemyError

from strathive import settings
from strathive.adapters.files import broker_csv, catalog_file, json_file
from strathive.bootstrap import build
from strathive.core.rules import LedgerError, require
from strathive.sources.sync import SOURCE_NAMES, synchronize

app = typer.Typer(help="策略蜂巢本地账本。用 子命令 --help 查看用法。", no_args_is_help=True,
                  add_completion=False)
GROUP_HELP = {
    "db": "初始化、备份和恢复账本。",
    "security": "查询、导入或同步证券目录。",
    "strategy": "管理人工策略。",
    "account": "管理券商账户和资金进出。",
    "run": "管理策略运行。",
    "trade": "录入、查询和更正已成交交易。",
}
Name = Annotated[str, typer.Option("--name", "-n", help="名称。")]
AccountId = Annotated[int, typer.Option(help="券商账户 ID。")]
StrategyId = Annotated[int, typer.Option(help="策略 ID。")]
Version = Annotated[int, typer.Option(help="策略内的版本号。")]
OptionalVersion = Annotated[int | None, typer.Option(help="省略时绑定最新版。")]
RequestId = Annotated[str | None, typer.Option(help="关键写入的重试标识。")]
RunId = Annotated[int, typer.Option(help="策略运行 ID。")]
TradeId = Annotated[int, typer.Option(help="成交记录 ID。")]
At = Annotated[str, typer.Option(help="实际发生时间，ISO 8601，默认北京时间。")]
Amount = Annotated[str, typer.Option(help="资金金额，单位为元。")]
Description = Annotated[str, typer.Option(help="策略描述。")]
File = Annotated[Path, typer.Option("--file", "-f", help="输入文件路径。")]
Security = Annotated[str, typer.Option(help="证券代码或目录中的准确名称。")]
Side = Annotated[str, typer.Option(help="买卖方向，buy 为买入，sell 为卖出。")]
Quantity = Annotated[int, typer.Option("--quantity", "-q", help="成交数量，单位为股或份。")]
Price = Annotated[str, typer.Option(help="成交单价，单位为元。")]
Commission = Annotated[str, typer.Option(help="券商实际佣金，单位为元。")]
StampDuty = Annotated[str, typer.Option(help="实际印花税，单位为元。")]
TransferFee = Annotated[str, typer.Option(help="实际过户费，单位为元。")]
Reason = Annotated[str, typer.Option(help="交易原因。")]
Note = Annotated[str, typer.Option(help="备注。")]

NameOption = Annotated[str | None, typer.Option("--name", "-n", help="名称。")]
RunIdOption = Annotated[int | None, typer.Option(help="策略运行 ID。")]
AtOption = Annotated[str | None, typer.Option(help="实际发生时间，ISO 8601，默认北京时间。")]
RateOption = Annotated[str | None, typer.Option(help="统一设置股票和 ETF 佣金费率，分类参数优先。0.0003 表示 0.03%。")]
StockRateOption = Annotated[str | None, typer.Option(help="股票佣金费率，0.0003 表示 0.03%。")]
EtfRateOption = Annotated[str | None, typer.Option(help="ETF 佣金费率，0.0001 表示 0.01%。")]
DescriptionOption = Annotated[str | None, typer.Option(help="策略描述。")]
FileOption = Annotated[Path | None, typer.Option("--file", "-f", help="输入文件路径。")]
SecurityOption = Annotated[str | None, typer.Option(help="证券代码或目录中的准确名称。")]
SideOption = Annotated[str | None, typer.Option(help="买卖方向，buy 为买入，sell 为卖出。")]
QuantityOption = Annotated[int | None, typer.Option("--quantity", "-q", help="成交数量，单位为股或份。")]
PriceOption = Annotated[str | None, typer.Option(help="成交单价，单位为元。")]
CommissionOption = Annotated[str | None, typer.Option(help="券商实际佣金，单位为元。")]
StampDutyOption = Annotated[str | None, typer.Option(help="实际印花税，单位为元。")]
TransferFeeOption = Annotated[str | None, typer.Option(help="实际过户费，单位为元。")]
ReasonOption = Annotated[str | None, typer.Option(help="交易原因。")]
NoteOption = Annotated[str | None, typer.Option(help="备注。")]

groups = {}
for group_name, help_text in GROUP_HELP.items():
    groups[group_name] = typer.Typer(no_args_is_help=True, help=help_text)
    app.add_typer(groups[group_name], name=group_name)
version_app = typer.Typer(help="创建和查询策略版本。", no_args_is_help=True)
groups["strategy"].add_typer(version_app, name="version")


class Session:
    def __init__(self, db):
        self.db = db
        self._built = None

    @property
    def ledger(self):
        if self._built is None:
            self._built = build(self.db)
        return self._built[0]

    @property
    def store(self):
        if self._built is None:
            self._built = build(self.db)
        return self._built[1]


def output(value):
    typer.echo(json.dumps({"ok": True, "data": value}, ensure_ascii=False))


@app.callback()
def root(ctx: typer.Context, db: Path | None = typer.Option(None, help="指定本地账本文件。"),
         json_output: bool = typer.Option(False, "--json", hidden=True)):
    ctx.obj = Session(db)


@app.command("setup", help="选择账本、配置数据源、可选组件和每日同步。")
def setup(ctx: typer.Context):
    from .setup import run

    run(ctx.obj.store.catalog)


@groups["db"].command("init", help="初始化或升级本地账本。")
def init(ctx: typer.Context):
    output(ctx.obj.store.initialize())


@groups["db"].command("backup", help="备份账本到新文件。")
def backup(ctx: typer.Context, destination: Annotated[Path, typer.Option("--file", "-f", help="备份文件路径。")]):
    output(ctx.obj.store.backup(destination))


@groups["db"].command("restore", help="从备份恢复到新账本。")
def restore(ctx: typer.Context, file: File):
    output(ctx.obj.store.restore(file))


@groups["security"].command("lookup", help="按代码或准确名称查找证券。")
def security(ctx: typer.Context, query: str):
    output(ctx.obj.ledger.instrument(query))


@groups["security"].command("import", help="导入证券目录 CSV。")
def import_security(ctx: typer.Context, file: File):
    catalog = ctx.obj.store.catalog
    with catalog.refresh_lock():
        output(catalog.update(catalog_file(file)))


@groups["security"].command("sync", help="从已配置的来源刷新独立证券目录。")
def sync_security(ctx: typer.Context, if_stale: bool = typer.Option(False, help="当天已成功时跳过。")):
    catalog = ctx.obj.store.catalog
    with catalog.refresh_lock():
        output(synchronize(catalog, settings.settings(), if_stale=if_stale))


@groups["security"].command("sync-status", help="查询当前来源的最近一次尝试和成功记录。")
def security_status(ctx: typer.Context):
    values = settings.settings()
    source = (values.get("selection", {}).get("reference") or [values.get("source")])[0]
    require(source in SOURCE_NAMES, "请先运行 strathive setup 配置数据源。", "source_not_configured")
    output(ctx.obj.store.catalog.status(source))


@groups["strategy"].command("create", help="创建人工策略。")
def strategy_create(ctx: typer.Context, name: Name, description: Description = ""):
    output(ctx.obj.ledger.create_strategy(name, description))


@groups["strategy"].command("list", help="列出策略。")
def strategy_list(ctx: typer.Context):
    output(ctx.obj.ledger.strategies())


@groups["strategy"].command("show", help="查看策略。")
def strategy_show(ctx: typer.Context, strategy_id: int):
    output(ctx.obj.ledger.strategies(strategy_id))


@groups["strategy"].command("edit", help="修改策略。")
def strategy_edit(ctx: typer.Context, strategy_id: Annotated[int, typer.Option(help="策略 ID。")],
                  name: NameOption = None, description: DescriptionOption = None):
    output(ctx.obj.ledger.edit_strategy(strategy_id, name, description))


@version_app.command("create", help="固化策略当前名称和描述。")
def version_create(ctx: typer.Context, strategy_id: StrategyId):
    output(ctx.obj.ledger.create_version(strategy_id))


@version_app.command("list", help="查询策略的版本记录。")
def version_list(ctx: typer.Context, strategy_id: StrategyId):
    output(ctx.obj.ledger.versions(strategy_id))


@version_app.command("show", help="读取指定版本快照。")
def version_show(ctx: typer.Context, strategy_id: StrategyId, version: Version):
    output(ctx.obj.ledger.versions(strategy_id, version))


@groups["account"].command("open", help="建立券商账户。")
def account_open(ctx: typer.Context, name: Name, cash: Annotated[str, typer.Option(help="期初现金，单位为元。")],
                 as_of: Annotated[str, typer.Option(help="建账截止时间，默认北京时间。")],
                 commission_rate: RateOption = None, stock_commission_rate: StockRateOption = None,
                 etf_commission_rate: EtfRateOption = None,
                 opening: Annotated[Path | None, typer.Option(help="期初持仓与运行分配 JSON 文件。")] = None,
                 request_id: RequestId = None):
    details = json_file(opening) if opening else {}
    require(set(details) <= {"positions", "allocations"}, "unexpected opening input field")
    output(ctx.obj.ledger.open_account(name, cash, commission_rate, as_of, stock_rate=stock_commission_rate,
                                      etf_rate=etf_commission_rate, request_id=request_id, **details))


@groups["account"].command("list", help="列出券商账户。")
def account_list(ctx: typer.Context):
    output(ctx.obj.ledger.accounts())


@groups["account"].command("show", help="查看账户现金、持仓和资产池。")
def account_show(ctx: typer.Context, account_id: int):
    output(ctx.obj.ledger.accounts(account_id))


@groups["account"].command("edit", help="修改账户名称或佣金费率。")
def account_edit(ctx: typer.Context, account_id: AccountId, name: NameOption = None,
                 commission_rate: RateOption = None, stock_commission_rate: StockRateOption = None,
                 etf_commission_rate: EtfRateOption = None):
    output(ctx.obj.ledger.edit_account(account_id, name, commission_rate,
                                      stock_rate=stock_commission_rate, etf_rate=etf_commission_rate))


@groups["account"].command("deposit", help="录入外部入金。")
def deposit(ctx: typer.Context, account_id: AccountId, amount: Amount, at: At):
    output(ctx.obj.ledger.move(account_id, "deposit", amount, at))


@groups["account"].command("withdraw", help="录入外部出金。")
def withdraw(ctx: typer.Context, account_id: AccountId, amount: Amount, at: At):
    output(ctx.obj.ledger.move(account_id, "withdraw", amount, at))


@groups["run"].command("create", help="创建运行，绑定账户及具体策略版本。")
def run_create(ctx: typer.Context, strategy_id: StrategyId, account_id: AccountId, name: Name,
               version: OptionalVersion = None):
    output(ctx.obj.ledger.create_run(strategy_id, account_id, name, version))


@groups["run"].command("start", help="启动运行并固定期初分配。")
def run_start(ctx: typer.Context, run_id: RunId,
              cash: Annotated[str, typer.Option(help="期初分配现金，单位为元。")], at: At):
    output(ctx.obj.ledger.start_run(run_id, cash, at))


@groups["run"].command("close", help="结清运行，或将持仓交接给同账户的新运行。")
def run_close(ctx: typer.Context, run_id: RunId, at: At,
              successor_run_id: Annotated[int | None, typer.Option(help="待启动的承接运行 ID。")] = None,
              successor_cash: Annotated[str, typer.Option(help="承接运行期初分配现金。")] = "0",
              handoff_valuation: Annotated[Path | None, typer.Option(help="交接估值 JSON 文件。")] = None,
              request_id: RequestId = None):
    valuations = ()
    if handoff_valuation is not None:
        payload = json_file(handoff_valuation)
        require(set(payload) == {"valuations"}, "expected a valuations array")
        valuations = payload["valuations"]
    output(ctx.obj.ledger.close_run(run_id, at, successor_run_id, successor_cash, valuations,
                                         request_id=request_id))


@groups["run"].command("list", help="列出策略运行。")
def run_list(ctx: typer.Context):
    output(ctx.obj.ledger.runs())


@groups["run"].command("show", help="查看运行、持仓和成交。")
def run_show(ctx: typer.Context, run_id: int):
    output(ctx.obj.ledger.runs(run_id))


@groups["run"].command("status", help="切换运行状态。")
def run_status(ctx: typer.Context, run_id: RunId,
               status: Annotated[str, typer.Option(help="运行状态：active 恢复，paused 暂停。")]):
    output(ctx.obj.ledger.transition(run_id, status))


def fee_overrides(commission, stamp_duty, transfer_fee):
    return {key: value for key, value in
            {"commission": commission, "stamp_duty": stamp_duty, "transfer_fee": transfer_fee}.items()
            if value is not None}


@groups["trade"].command("add", help="录入已成交交易。")
def trade_add(ctx: typer.Context, run_id: RunId, security: Security, side: Side, quantity: Quantity,
              price: Price, at: At, commission: CommissionOption = None, stamp_duty: StampDutyOption = None,
              transfer_fee: TransferFeeOption = None, reason: Reason = "", note: Note = "",
              request_id: RequestId = None):
    overrides = fee_overrides(commission, stamp_duty, transfer_fee)
    output(ctx.obj.ledger.record_trade(run_id, security, side, quantity, price, at,
                                             overrides, reason, note, request_id=request_id))


@groups["trade"].command("show", help="查看成交详情。")
def trade_show(ctx: typer.Context, trade_id: int):
    output(ctx.obj.ledger.trade(trade_id))


@groups["trade"].command("list", help="查询成交记录。")
def trade_list(ctx: typer.Context, run_id: RunIdOption = None, security: SecurityOption = None,
               start: Annotated[str, typer.Option(help="成交开始时间。")] = None,
               end: Annotated[str, typer.Option(help="成交结束时间。")] = None):
    output(ctx.obj.ledger.trades(run_id, security, start, end))


@groups["trade"].command("edit", help="更正已成交交易。")
def trade_edit(ctx: typer.Context, trade_id: TradeId, file: FileOption = None,
               security: SecurityOption = None, side: SideOption = None, quantity: QuantityOption = None,
               price: PriceOption = None, at: AtOption = None, reason: ReasonOption = None,
               note: NoteOption = None, commission: CommissionOption = None,
               stamp_duty: StampDutyOption = None, transfer_fee: TransferFeeOption = None):
    values = {key: value for key, value in dict(security=security, side=side, quantity=quantity,
              price=price, effective_at=at, reason=reason, note=note).items() if value is not None}
    overrides = fee_overrides(commission, stamp_duty, transfer_fee)
    if overrides:
        values["overrides"] = overrides
    if file is not None:
        require(not values, "supply either --file or named changes")
        values = json_file(file)
    require(bool(values) and set(values) <= {"security", "side", "quantity", "price", "effective_at",
                                             "reason", "note", "overrides"}, "invalid trade changes")
    output(ctx.obj.ledger.edit_trade(trade_id, values))


@groups["trade"].command("delete", help="删除成交并重算账本。")
def trade_delete(ctx: typer.Context, trade_id: TradeId,
                 yes: bool = typer.Option(False, "--yes", help="确认删除此成交。")):
    require(yes, "pass --yes to confirm deletion")
    output(ctx.obj.ledger.delete_trade(trade_id))


@app.command(help="核对券商账面现金与持仓。")
def reconcile(ctx: typer.Context, account_id: AccountId,
              cash: str | None = typer.Option(None, help="券商账面现金，单位为元。"),
              position: list[str] | None = typer.Option(None, help="证券,数量[,单位成本]，可重复提供。"),
              file: Path | None = typer.Option(None, "--file", "-f", help="券商快照 UTF-8 CSV 文件。"),
              cost_basis: str | None = typer.Option(None, help="确认成本口径一致时填写 moving_average_v1。")):
    require((file is None) != (cash is None) and (file is None or not position),
            "supply either --file or --cash with optional --position")
    positions = []
    if file is not None:
        cash, positions = broker_csv(file)
    else:
        for item in position or ():
            fields = item.split(",")
            require(2 <= len(fields) <= 3, "expected SECURITY,QUANTITY[,UNIT_COST]")
            positions.append(dict(zip(("security", "quantity", "unit_cost"), fields)))
    output(ctx.obj.ledger.reconcile(account_id, cash, positions, cost_basis))


from .market import install
install(app, output)

def main():
    args = sys.argv[1:]
    # Machine mode applies to the full invocation, including parser errors.
    options_end = args.index("--") if "--" in args else len(args)
    if "--json" in args[:options_end]:
        args = ["--json", *(item for item in args[:options_end] if item != "--json"), *args[options_end:]]
    try:
        return app(args=args, standalone_mode=False) or 0
    except click.Abort:
        code, message, status = "cancelled", "操作已取消。", 1
    except click.ClickException as exc:
        code, message, status = "invalid_input", exc.format_message(), exc.exit_code
    except LedgerError as exc:
        code, message = exc.code, str(exc)
        status = {"invalid_input": 2, "not_found": 3, "busy": 4, "ledger_conflict": 5, "request_conflict": 6}.get(code, 1)
    except (ValueError, TypeError, KeyError, OverflowError, csv.Error, FileNotFoundError) as exc:
        code, message, status = "invalid_input", str(exc), 2
    except (OSError, SQLAlchemyError, sqlite3.DatabaseError, CommandError) as exc:
        code, message, status = "storage_error", str(exc), 1
    typer.echo(json.dumps({"ok": False, "error": {"code": code, "message": message}}, ensure_ascii=False))
    return status
