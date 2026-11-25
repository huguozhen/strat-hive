# 行情与历史数据

用 `strathive data status` 查看来源和历史组件状态。证券标识带市场，例如 `SH.510300` 或 `SZ.159915`；仅有简称或代码时先用 `security lookup` 解析。查询行情、组件和同步命令不需要初始化账本。

## 盘中报价和估值

`data quote --security SYMBOL` 支持重复证券、`--refresh` 或 `--offline`，两者互斥。报价与参考市值须说明价格时间、来源、缺价及降级情况。账户／运行估值参照 [账本流程](ledger.md)。

## 历史查询

使用 `data bars --security SYMBOL --start YYYY-MM-DD --end YYYY-MM-DD`，证券可重复。日期为北京时间交易日期，范围含首尾；用户未给定必需的证券和日期时先补齐这些信息。

- `--period`：`1d`、`1w`、`1mo`、`1q`、`1m`、`5m`、`15m`、`30m`、`60m`。
- `--adjustment`：`none`、`qfq`、`hfq`。实际持仓估值使用未复权价格；研究查询按用户指定口径，缺因子须报告不完整。
- `--output FILE.jsonl` 或 `FILE.csv`：完整导出并生成 manifest。超过 10000 根而未指定文件时，按 `history_export_required` 改用导出，使用新文件名，不静默缩小查询范围。

回显证券、日期、周期、价格口径、行数、快照及覆盖。`complete=false`、缺因子、缺分钟和未完成自然周期均保留其含义；缺数据不解释为停牌，不以原价冒充已复权，也不把 ETF 净值替代市场 K 线。

## 同步与维护

历史组件未安装时按 [组件流程](components.md) 处理。同步前采用用户已指定的范围；缺少会扩大采集规模的范围时先说明需要证券清单、周期或日期。明确要求全市场日线才省略证券执行 `data sync --period 1d`。

`data sync` 支持重复 `--security`、`--period 1d|1m|factors|all` 和日期。默认日线窗口三年，分钟窗口 60 个交易日；分钟默认清单为空。显式证券配合 `all` 会同时采该证券的分钟线，因此只要求日线时使用 `1d`。周／月／季度及其他分钟周期由查询聚合，无须同步多份。

`data sync --retry-only` 仅补未完成范围；失败或 partial 不作为覆盖完成。用 `data history status` 查任务，`inspect` 查库中数据和修订。启用定时器、变更分钟清单和显式 `maintain` 均按用户要求执行；清空分钟清单和卸载组件保留已取得的数据。
