# CLI 结构化输入

现金、价格和费率可使用十进制字符串，数量使用整数。运行及成交时间使用 ISO 格式，未标明时区时按北京时间处理。业务命令默认返回 JSON。带持仓建账、成交录入和结束运行使用 `--request-id` 标识一次写入，恢复结果时使用相同 ID 和输入。

## 佣金费率

`account open` 和 `account edit` 支持 `--stock-commission-rate` 和 `--etf-commission-rate`，例如股票 `0.0003`、ETF `0.0001`。只修改其中一类时，传入该类参数即可。

`--commission-rate` 继续统一设置两类费率，分类参数优先。建账时须提供股票费率或统一费率，ETF 费率省略时沿用统一费率，若仅提供股票费率则与股票相同。旧账本执行 `db init` 升级后，两类费率均继承原值。

账户查询中的 `commission_rate` 表示股票费率，`etf_commission_rate` 表示 ETF 费率。成交自动按证券品种选择费率并保存，后续修改账户费率后，更正原成交价量仍使用原成交费率。跨品种更正证券时使用账户当前对应品种的费率。

## 期初持仓

`account open --name NAME --cash CASH --stock-commission-rate STOCK_RATE --etf-commission-rate ETF_RATE --as-of TIME --opening FILE` 在建账截止时点创建并启动承接运行。每只证券分配数量之和须等于账户持仓数量，所有运行分配现金之和须在账户期初现金范围内。先创建策略，并在各分配项填写 `strategy_id`。可用正整数 `version` 指定已有版本，省略时在建账事务内绑定当时的最新版。可为承接持仓提供期初估值，缺少估值时相关绩效待估值。

```json
{
  "positions": [
    {"security": "SH.600000", "quantity": 100, "unit_cost": "10.25", "position_date": "2026-10-12"}
  ],
  "allocations": [
    {
      "strategy_id": 1,
      "version": 1,
      "run": "首期",
      "cash": "1000.00",
      "positions": [{"security": "SH.600000", "quantity": 100, "valuation_price": "11.00"}]
    }
  ]
}
```

对于纯现金账户，省略 `--opening`，之后可用 `run start --run-id ID --cash CASH --at TIME` 启动新运行。

## 结束与持仓交接

`run close --run-id ID --at TIME` 用于持仓已归零的运行，并将剩余现金归还账户。还持有证券时，用 `--successor-run-id ID` 指定同账户的待启动运行，剩余持仓作为其期初资产。用 `--successor-cash CASH` 指定新运行的期初现金，默认为零。持仓交接和新运行启动均以结束时间生效。补记旧运行成交时，成交时间须早于其结束时间；已经按原顺序入账的同一时刻成交可以更正。

可以用 `--handoff-valuation FILE` 提供交接时的估值。每项估值注明证券、单价和价格来源。未提供估值的证券可以交接，涉及该证券的绩效待估值。

```json
{
  "valuations": [
    {"security": "SH.600000", "price": "12.00", "source": "用户提供的交接时点收盘价"}
  ]
}
```

## 成交更正

常见更正直接用具名选项，例如 `trade edit --trade-id 2 --price 10.10`。结构化输入使用 `trade edit --trade-id 2 --file changes.json`。文件填写发生变化的字段，可选字段为 `security`、`side`、`quantity`、`price`、`effective_at`、`reason`、`note`、`overrides`。

`overrides` 支持 `commission`、`stamp_duty` 和 `transfer_fee`。`{"overrides":{"commission":null}}` 将佣金恢复为自动计算。

## 对账 CSV

用 `reconcile --account-id ID --file FILE` 导入 UTF-8 CSV。现金取券商账面现金，数量取账户持仓总量。缺少成本时，`unit_cost` 可留空。

```csv
type,cash,security,quantity,unit_cost
cash,1000.00,,,
position,,SH.600000,100,10.25
```

## 自动取价与估值

新建账/交接优先只提供真实数量、成本及生效时间，系统自动取价并随结果回显，用户无须填写估值文件。旧格式仍可用于恢复历史请求。盘前使用上一已完成交易日收盘价，盘后使用当日收盘价；对应日历或价格不可用时显示待估值，不用其他日期价格代替。

查询参考市值使用 `valuation account --account-id ID` 或 `valuation run --run-id ID`；`--refresh` 强制联网刷新，`--offline` 仅读缓存，两者不能同时使用。向用户说明返回的价格日期、来源及降级情况。缺价结果中总资产为空不表示资产为零，记账成功但待估值也不应重复提交记账。

用 `valuation complete --run-id ID` 按原生效日期补齐空缺市值，再回显结果。已采用价格不因补齐或普通行情刷新改变；同一请求 ID 重试恢复首次提交结果，因此后续补齐不会改变原请求回执。
