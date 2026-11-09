# 结构化输入

金额和费率建议用字符串表示，持仓数量为整数。建账截止和成交使用 ISO 时间，未注明时区时按北京时间处理。

## 期初持仓

带持仓建账时，在 `account open --name NAME --cash CASH --commission-rate RATE --as-of TIME` 后加 `--opening FILE`。佣金费率 `0.0003` 表示 0.03%。资产池分配的数量之和应与账户持仓一致。

```json
{
  "positions": [
    {"security": "SH.600000", "quantity": 100, "unit_cost": "10.25", "position_date": "2026-10-12"}
  ],
  "allocations": [
    {
      "strategy": "人工策略",
      "run": "首期",
      "pool": "主池",
      "cash": "1000.00",
      "positions": [{"security": "SH.600000", "quantity": 100}]
    }
  ]
}
```

## 成交更正

常见更正可直接用具名选项，例如 `trade edit --trade-id 2 --price 10.10`。也可用 `trade edit --trade-id 2 --file changes.json`。文件只填有变化的字段，可选字段为 `security`、`side`、`quantity`、`price`、`effective_at`、`reason`、`note`、`overrides`。

`overrides` 支持 `commission`、`stamp_duty`、`transfer_fee`。例如 `{"overrides":{"commission":null}}` 将佣金恢复为自动计算。

## 对账 CSV

使用 `reconcile --account-id ID --file FILE` 导入 UTF-8 CSV。现金取券商账面现金，数量取持仓总量。缺少成本时，`unit_cost` 可留空。

```csv
type,cash,security,quantity,unit_cost
cash,1000.00,,,
position,,SH.600000,100,10.25
```
