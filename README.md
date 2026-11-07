# 策略蜂巢

单人本地策略运行账本：记录实际资产账户、策略、资产池和初始资产归属。支持建账、资金管理、真实成交记录和即时对账，不提供行情或自动下单。需要 Python 3.10 或更新版本，无第三方运行依赖。

## 启动

```sh
python -m strathive serve
```

浏览器访问 <http://127.0.0.1:8765>。服务仅监听本机。默认数据库为 `~/.strathive/ledger.db`；Web 和 CLI 使用同一文件。可通过 `STRATHIVE_DB` 或 `--db /path/ledger.db` 指定路径，例如 `python -m strathive --db ./ledger.db serve --port 8766`。备份时先停止服务，再复制数据库文件。

### 建账顺序

1. 创建账户并录入当时的现金和小数形式的佣金费率（万分之三为 `0.0003`）。只有现金时无需策略或池。
2. 有存量持仓时，先初始化证券资料，再创建策略、资产池和绑定该池的策略运行。运行初始为暂停，可在策略页面启动。
3. 在资产分配页面逐条加入**全部**存量持仓，每只证券的数量须完整分配给本账户一个或多个已有运行的池，然后一次保存。保存是原子操作，不生成历史买入。全部保存后不能再次添加初始持仓。
4. 资金进出及池内外划转会结束初始建账；如有存量持仓请先保存。外部转出和池划入受未分配现金限制，池划出受池现金限制。

账户与池显示的是账本现金，未计算冻结资金或券商可用资金；不显示需要行情的市值和收益。资金操作可填写发生时间（不填为当前时间），不带时区的输入按运行账本的本机时间解释；带时区的输入换算为本机时间。补录成交前请确保对应资金已按时间划入。新账户记录建账时点（默认当前时间，也可填写真实时点），禁止录入更早的成交。升级前的旧账户时点未知，先在修改账户信息中填写实际建账时点，才能录入成交；不要猜测该时点。存量持仓日期可以早于建账。修改和删除成交会按时间重放初始持仓、所有资金操作及成交，若任一后续状态为负则整体撤销。相同时间先处理初始持仓，再处理资金操作，最后处理成交；同类记录按记录 ID 排序。

## 证券资料

证券资料页可从上交所及深交所公开网站同步沪深主板 **A 股**和沪深 ETF；网络同步会完整读取四类资料，全部校验通过后替换本地目录。任一来源不可用、格式变化或校验失败时保留旧目录。数据为当前目录快照，不代表完整历史、退市或停牌资料；未收录时请检查代码或更新资料。网页内部接口没有稳定性承诺，也可能因网络或证书问题不可用。保持 TLS 校验，**不要关闭证书检查**。来源、访问细节、实测覆盖及使用条件见 [证券资料调研](docs/research/securities-directory.md)。使用和再分发可能需要权利人许可，请根据用途确认。

网络不可用时，在证券资料页导入 UTF-8 JSON 文件（完整替换）或通过 CLI 导入。六位代码须保留前导零，类型为 `stock` 或 `etf`：

```json
{"source":"自备目录，2026-11-07", "securities":[
  {"code":"600000", "name":"浦发银行", "type":"stock"},
  {"code":"510300", "name":"沪深300ETF", "type":"etf"}
]}
```

这只是格式示例，不是完整目录。CLI 的证券参数接受代码或准确名称，Web 支持代码或名称搜索选择。名称不唯一时请使用代码。

## CLI

命令格式 `python -m strathive [--db 数据库路径] 操作 --json JSON对象`；成功时标准输出为 JSON，错误输出到标准错误并以非零状态退出。`--json @文件名` 读取 UTF-8 JSON，`--json @-` 从标准输入读取；方便脚本和后续工具调用。

```sh
python -m strathive account.create --json '{"name":"券商账户","cash":"10000.00","commission_rate":"0.0003","opened_at":"2026-01-01T09:00:00"}'
python -m strathive account.list
python -m strathive account.get --json '{"id":1}'
python -m strathive account.update --json '{"id":1,"name":"新名称","commission_rate":"0.0003","opened_at":"2026-01-01T09:00:00"}'
python -m strathive catalog.sync
python -m strathive catalog.import --json @securities.json
python -m strathive catalog.search --json '{"query":"600000"}'
python -m strathive strategy.create --json '{"name":"趋势","description":"人工策略"}'
python -m strathive strategy.list
python -m strathive strategy.update --json '{"id":1,"name":"趋势策略","description":"记录方法"}'
python -m strathive pool.create --json '{"account_id":1,"name":"长期池"}'
python -m strathive run.create --json '{"strategy_id":1,"pool_id":1,"name":"长期运行"}'
python -m strathive run.list
python -m strathive run.get --json '{"id":1}'
python -m strathive run.status --json '{"id":1,"status":"running"}'
python -m strathive initial.record --json '{"account_id":1,"holdings":[{"security":"600000","quantity":100,"cost":"10.25","date":"2025-01-02","allocations":[{"pool_id":1,"quantity":100}]}]}'
python -m strathive cash.list --json '{"account_id":1}'
python -m strathive cash.move --json '{"account_id":1,"direction":"in","amount":"100.00"}'
python -m strathive cash.move --json '{"account_id":1,"pool_id":1,"direction":"in","amount":"100.00","time":"2026-01-02T09:00:00"}'
python -m strathive trade.create --json '{"run_id":1,"security":"600000","direction":"buy","time":"2026-01-02T10:00:00","price":"10.00","quantity":10,"commission":"5.00","stamp_tax":"0.00","transfer_fee":"0.00","reason":"计划买入","note":"交割单已核对"}'
python -m strathive trade.list --json '{"run_id":1,"security":"600000","from":"2026-01-02T00:00:00","to":"2026-01-02T23:59:59"}'
python -m strathive trade.get --json '{"id":1}'
python -m strathive trade.update --json '{"id":1,"price":"10.10"}'
python -m strathive trade.delete --json '{"id":1}'
python -m strathive reconcile --json @broker.json
```

`initial.record` 的 `holdings` 数组必须包含该账户全部初始持仓，每条 `allocations` 之和等于对应数量；跨池承接时列出多个池。无持仓账户可以显式运行 `initial.close --json '{"account_id":1}'`，或由首次资金操作结束建账。`cash.move` 不传 `pool_id` 表示外部资金，传入时 `in` 为未分配现金划入池、`out` 为池划回未分配现金。运行状态为 `running`、`paused`、`ended`；结束不可重启，可以在池空闲时创建新的运行。

## 即时对账

在“即时对账”页面选择账户，填写券商现金余额，逐条添加证券代码、数量和每股或每份成本价；无持仓时无需添加。也可导入 UTF-8 JSON 文件。CLI 支持手动传入同一 JSON 或通过 `--json @broker.json` 导入；完整文件示例（账户 ID 请按实际修改）：

```json
{
  "account_id": 1,
  "broker": {
    "cash": "1000.00",
    "holdings": [
      {"code": "600000", "quantity": 100, "cost": "10.0000"}
    ]
  }
}
```

Web 也可导入单独的 `broker` 对象。代码为本地证券资料中的六位字符串（保留前导零），数量为非负整数，成本价为非负数、最多四位小数；零数量视为无持仓，重复代码和未知证券会被拒绝。现金为非负数、最多四位小数，填写与账本现金余额对应的券商字段，而不是可用资金；不计算未成交委托冻结资金。结果 `matches` 表示整体是否一致，`broker_comparison` 展示券商减账本的差额及 `missing`（券商缺少）、`extra`（券商多出）、`different` 或 `match`，`allocation_comparison` 展示账户与池、未分配现金关系。没有另一侧的证券时差额为 `null`，请查看状态。账户成本和池汇总成本以各池未四舍五入的剩余成本金额加权，展示和比较至四位小数。对账使用现有账本快照，不需要行情，不保存本次输入或结果，也不会改变账本事实。

## 测试

```sh
python -m unittest discover -s tests -v
```

## 费用与成本口径

成交额精确计算为价格乘整数数量。费用输入留空时为**估算**：按账户佣金费率、每条成交最低 5 元；股票卖出印花税 0.05%，股票双向过户费 0.001%，ETF 二级交易两项为零。分别按四舍五入至分是产品假设，不代表已经核实所有券商的舍入方法；拆单最低佣金、较早历史费率及非普通竞价未核实。估算使用当前费率，不按历史日期推断。佣金可能已包括过户费，实际费用请据交割单分别覆盖，避免重复计入；某项实际为零时填 `0`。成交保存后费用值不会因修改账户费率而改变；修改成交且未手动覆盖的费用会重新估算。详见[一手来源和限制](docs/research/trading-fees.md)。

成本为参考同花顺模拟盘的近似摊薄成本：初始数量乘输入成本为起点，买入加成交额及费用，卖出减去扣费后的回收，除以剩余数量。全部卖出时归零，之后买入重新开始；当日清仓再买的规则尚未证实，不能视为同花顺实盘口径。账户成本由各池剩余成本金额合并后除以账户持仓数量，不能相加单位成本；与券商口径可能不同。详见[成本调研](docs/research/position-cost.md)。运行结束后池若重新绑定新运行，历史运行不展示该池现在的资产；历史成交仍归属于原运行。
