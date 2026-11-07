# 策略蜂巢

单人本地策略运行账本：记录实际资产账户、策略、资产池和初始资产归属。当前交付建账及资金管理，不提供行情、自动下单或交易录入。需要 Python 3.10 或更新版本，无第三方运行依赖。

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

账户与池显示的是账本现金，未计算冻结资金或券商可用资金。账户持仓成本以初始持仓的数量加权，资产池展示初始成本及日期；不显示需要行情的市值和收益。

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
python -m strathive account.create --json '{"name":"券商账户","cash":"10000.00","commission_rate":"0.0003"}'
python -m strathive account.list
python -m strathive account.get --json '{"id":1}'
python -m strathive account.update --json '{"id":1,"name":"新名称","commission_rate":"0.0003"}'
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
python -m strathive cash.move --json '{"account_id":1,"pool_id":1,"direction":"in","amount":"100.00"}'
```

`initial.record` 的 `holdings` 数组必须包含该账户全部初始持仓，每条 `allocations` 之和等于对应数量；跨池承接时列出多个池。无持仓账户可以显式运行 `initial.close --json '{"account_id":1}'`，或由首次资金操作结束建账。`cash.move` 不传 `pool_id` 表示外部资金，传入时 `in` 为未分配现金划入池、`out` 为池划回未分配现金。运行状态为 `running`、`paused`、`ended`；结束不可重启，可以在池空闲时创建新的运行。

## 测试

```sh
python -m unittest discover -s tests -v
```
