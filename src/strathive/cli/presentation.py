"""Chinese terminal output for human CLI sessions."""

from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.text import Text

from strathive.core.rules import SHANGHAI

LABELS = {
    "id": "ID", "name": "名称", "description": "描述", "account_id": "账户 ID",
    "strategy_id": "策略 ID", "run_id": "运行 ID", "pool_id": "资产池 ID",
    "commission_rate": "佣金费率", "opened_at": "建账时间", "status": "状态",
    "symbol": "证券代码", "full_name": "全称", "kind": "品种",
    "effective_at": "发生时间", "side": "方向", "quantity": "数量",
    "price": "单价", "unit_cost": "单位成本", "gross": "成交金额", "total_fees": "合计费用",
    "reason": "交易原因", "note": "备注", "cash": "现金", "available_cash": "可用现金",
    "allocated_cash": "净划入现金", "unallocated_cash": "未分配现金", "cash_occupied": "持仓成本合计",
    "commission": "佣金", "stamp_duty": "印花税", "transfer_fee": "过户费",
    "pool_quantity": "资产池汇总数量", "pool_cash": "资产池汇总现金",
}
RUN_STATES = {"draft": "待启动", "active": "运行中", "paused": "已暂停", "ended": "已结束"}
TRADE_COLUMNS = ("id", "run_id", "symbol", "side", "quantity", "price", "total_fees", "effective_at")
LIST_COLUMNS = {
    "strategy": ("id", "name", "description"),
    "account": ("id", "name", "commission_rate", "cash", "unallocated_cash"),
    "pool": ("id", "account_id", "name"),
    "run": ("id", "strategy_id", "pool_id", "name", "status"),
    "trade": TRADE_COLUMNS,
}
RECEIPTS = {
    ("db", "init"): "账本已就绪",
    ("db", "backup"): "账本已备份",
    ("db", "restore"): "账本已恢复",
    ("account", "open"): "券商账户已建账",
    ("account", "deposit"): "入金已记录",
    ("account", "withdraw"): "出金已记录",
    ("pool", "allocate"): "现金已划入资产池",
    ("pool", "release"): "现金已划回账户",
    ("run", "status"): "运行状态已更新",
    ("trade", "add"): "成交已记录",
    ("trade", "edit"): "成交已更正",
    ("trade", "delete"): "成交已删除",
    ("security", "import"): "证券目录已导入",
}


def display(key, value):
    if value is None or value == "":
        return "—"
    if key in {"effective_at", "opened_at"}:
        local_time = datetime.fromisoformat(value).astimezone(SHANGHAI)
        precision = "microseconds" if local_time.microsecond else "seconds"
        return local_time.isoformat(sep=" ", timespec=precision)
    if key == "status":
        return RUN_STATES.get(value, value)
    if key == "side":
        return {"buy": "买入", "sell": "卖出"}.get(value, value)
    if key == "kind":
        return {"stock": "股票", "etf": "ETF"}.get(value, value)
    return str(value)


def table(console, rows, columns):
    if not rows:
        console.print("暂无记录。")
        return
    result = Table()
    for key in columns:
        result.add_column(LABELS[key], overflow="fold")
    for row in rows:
        result.add_row(*(Text(display(key, row.get(key, row.get("current", {}).get(key)))) for key in columns))
    console.print(result)


def fields(console, record, keys):
    for key in keys:
        if key in record and record[key] not in (None, ""):
            console.print(Text(f"{LABELS[key]}：{display(key, record[key])}"))


def assets(console, current):
    fields(console, current, ("cash", "available_cash", "allocated_cash", "unallocated_cash", "cash_occupied"))
    console.print("持仓")
    table(console, current["positions"], ("symbol", "quantity", "unit_cost"))


def reconciliation(console, result):
    summaries = {"matched": "对账一致。", "partial": "现金与持仓数量一致，部分成本无法比较。",
                 "different": "发现对账差异。"}
    console.print(summaries[result["status"]])
    if result["differences"]:
        difference_table = Table("项目", "证券", "系统账本", "券商或资产池汇总")
        for row in result["differences"]:
            difference_table.add_row(
                Text(LABELS.get(row["field"], row["field"])), Text(row.get("symbol", "—")),
                Text(str(row["ledger"])),
                Text(str(row.get("broker", row.get("pools", row.get("pools_and_unallocated", "—"))))),
            )
        console.print(difference_table)
    if result["cost_incomparable"]:
        console.print(Text("成本不可比：" + "、".join(result["cost_incomparable"])))


def render(group, action, result):
    console = Console()
    if action == "reconcile":
        reconciliation(console, result)
        return
    if action == "list":
        table(console, result, LIST_COLUMNS[group])
        return
    if action != "show" and action != "lookup":
        console.print(RECEIPTS.get((group, action), "已创建" if action == "create" else "已更新"))

    if group == "db":
        console.print(Text(f"文件：{result['path']}"))
    elif group == "security" and action == "import":
        console.print(f"导入数量：{result['count']}")
    elif group == "security":
        fields(console, result, ("symbol", "name", "full_name", "kind"))
    elif group == "trade" and action != "delete":
        fields(console, result, (*TRADE_COLUMNS, "gross", "reason", "note"))
        fields(console, result["fees"], ("commission", "stamp_duty", "transfer_fee"))
    else:
        fields(console, result, ("id", "name", "description", "account_id", "strategy_id", "pool_id",
                                 "commission_rate", "opened_at", "status"))
        if "amount" in result:
            console.print(Text(f"金额：{result['amount']} 元"))
        if "current" in result and action == "show":
            assets(console, result["current"])
            if pools := result["current"].get("pools"):
                console.print("资产池")
                rows = [{"id": pool_id, **pool} for pool_id, pool in pools.items()]
                table(console, rows, ("id", "allocated_cash", "available_cash", "cash_occupied"))
            if "trades" in result:
                console.print("成交")
                table(console, result["trades"], TRADE_COLUMNS)


ERROR_TITLES = {
    "invalid_input": "输入有误", "not_found": "未找到记录", "busy": "账本正忙，请稍后重试",
    "ledger_conflict": "账本状态不允许该操作", "not_initialized": "请先运行 strathive db init 初始化账本",
    "migration_required": "请运行 strathive db init 升级账本", "already_exists": "目标已存在",
    "unsupported_sqlite": "SQLite 版本不受支持", "invalid_backup": "备份无法使用", "storage_error": "存储操作失败",
}
ERROR_DETAILS = {
    "pass --yes to confirm deletion": "请核对记录后使用 --yes 确认删除。",
    "supply either --file or named changes": "请在 --file 和具名修改选项之间选择一种。",
    "invalid trade changes": "请提供至少一个有效修改字段。",
    "supply either --file or --cash with optional --position": "请提供 --file，或使用 --cash 和可选的 --position。",
    "expected SECURITY,QUANTITY[,UNIT_COST]": "持仓格式为 证券,数量[,单位成本]。",
    "insufficient or out-of-range cash": "可用现金不足或金额超出范围。",
    "invalid run status transition": "当前运行状态不支持此次切换。",
}


def render_error(code, message):
    detail = ERROR_DETAILS.get(message, message)
    if detail.startswith("Missing option "):
        detail = "缺少必填选项 " + detail.removeprefix("Missing option ")
    elif detail.startswith("unknown security: "):
        detail = "未知证券：" + detail.removeprefix("unknown security: ")
    Console(stderr=True).print(Text(f"{ERROR_TITLES.get(code, '操作失败')}：{detail}"))
