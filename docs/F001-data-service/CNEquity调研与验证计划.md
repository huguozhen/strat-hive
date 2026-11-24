# CNEquity 调研与验证计划

日期：2026-11-18。对应产品基线 [第 3 章](../产品设计基线-261116-v0.2.md#3-数据服务设计)。本文是主线一的候选数据基础设施证据与试用清单，不是接入已经成功的验收报告。

2026-11-19 调整：按[实施前澄清结论](实施前澄清记录.md)，首期直接做盘中按需估值，CNEquity 主要作为历史能力候选独立推进。本文保留固定版本的事实核验；试验矩阵改为按需参考，不设置整套通过、跨 3 个交易日观察等开工门槛。

## 1. 结论与证据边界

CNEquity 值得优先进行**股票与沪深 ETF 的小范围未复权历史日线、收盘价试用**。其代码具有证券目录、交易日历、ETF 行情路径、Parquet 本地存储、增量/缺口恢复及 Python/本地 HTTP 查询能力；尚不能据此判定其数据可达、完整、可授权用于 StratHive，或满足盘中行情服务要求。可用时能承担历史查询或收盘价读取；首期盘中快照仍按实际可用链路选源，不能仅用每日收盘估值代替。

证据级别：

- **D（文档声明）**：上游自述，未替其背书。
- **C（源码核验）**：固定 commit 中可见实现，未等同实际运行通过。
- **L（本次轻量运行）**：仅标准库读取 manifest、执行无第三方依赖的证券符号函数、读取日历 CSV。
- **N（未实测）**：本次未安装 CNEquity 及其重依赖，未运行完整测试，未连接行情源，未采集市场数据，未测服务并发/恢复/时延。下文所有联网命令均为待执行步骤。

固定源码：[05370ab722acaaf1cf4cebc8b4489aaa81d5083b](https://github.com/rootSunc/CNEquity/commit/05370ab722acaaf1cf4cebc8b4489aaa81d5083b)，提交时间 2026-11-13T23:01:59+02:00，提交说明 `chore(release): prepare v0.24.0`。先通过已连接 GitHub 工具核实仓库和 README，再为批量逐文件审阅浅克隆到临时目录。后续引用全部绑定此 commit；官网是滚动文档，不代替固定源码。仓库版本为 **0.24.0**，不据此宣称 PyPI 当日发布包与 main 完全一致。[源码 manifest][manifest]

## 2. 能力核查

| 项目 | D / C 核查结果 | 对 StratHive 的含义与待验证事项 |
|---|---|---|
| Python 与部署 | `requires-python >=3.11`；硬依赖 DuckDB>=2.0、Polars、PyArrow、NumPy、pandas、curl_cffi、Baostock、FastAPI/Uvicorn 等，无轻量 extras；CI 配置列出 3.11–3.14，未核验这些 job 当次成功结果。[manifest][ci] | 建议独立 Python 3.12 环境运行候选，避免直接扩大 Core 的依赖集合。3.14 兼容不能只看下限或 CI 矩阵，须实际解依赖/运行验证。 |
| 运行环境 | 文档支持 macOS/Linux/Windows；TDX 客户端内置，无需桌面通达信。部分同花顺页面路径另需 Deno；发行包已带控制台静态资源，普通运行无需 Node。[安装][installation] | 基础 TDX demo 可先不引入 Deno；海外/本地网络出口可达性待测，安装成功不等于源可用。 |
| A 股 | `SH/SZ/BJ` 标识和采集路由存在；沪深历史与 BJ 补源能力不同，不能将一个 `all_a` 标签理解为各交易所同等完整。[符号][symbols]、[来源][sources] | P0 首批先验沪深；若用户持仓含 BJ，应单列能力门槛，不能静默丢弃。 |
| ETF / LOF | `is_etf_symbol` 支持沪深场内基金前缀；`instruments.asset_type=etf` 也可能含 LOF。`etf_profiles` 是交易所当前目录快照，资格历史不能用今天分类倒填。[符号][symbols]、[字段][schema] | 不能用 `asset_type=etf` 单独证明 ETF 身份、境内股票 ETF 研究资格或历史可交易状态。报价支持与研究资格是不同能力。 |
| ETF 日线 | `fetch_daily_bars(symbols, start, end, ...)` 将显式 symbol 传给 TDX 公共日线分页路径，不对基金另行拒绝；正式默认 `all_a` 股票采集不包含基金，`[universe].ingest_listed_funds=false`，启用后追加已上市基金。显式 demo/backfill 可限定基金 symbol。[TDX入口][tdx-client]、[分页][tdx-bars]、[范围配置][config] | 这是代码支持，不是 ETF 数据已测通过。至少分别核验 `510300.SH` 和 `159915.SZ`，与股票同批验收，缺任何一只即不能宣告覆盖通过。 |
| 日线历史 | 存未复权 OHLC、可空 `pre_close`、volume/amount、source、data_version、fetched_at。分页突破单次 800 根限制；覆盖长度受具体上游历史保留及补源可达性约束。[字段][schema]、[分页][tdx-bars] | 原始 close 可作为收盘估值候选；不能将前复权/后复权价直接乘实际持仓份数用于市值。历史日线不证明实时行情能力。 |
| 分钟线 | 1m/5m 独立数据集，默认关闭；文档称源保留约 95/491 个交易日。15m/30m/60m 由已有分钟数据重采样，默认不派生。[配置][config]、[字段][schema] | 保留窗口是上游文档声明，未做真实覆盖实测；P1 再验，不作为 P0 必选依赖。分钟线与实时推送服务不同。 |
| 实时/快照 | 存在 tip 快照采集和 `cne mcp --live`，后者仅符号查询和未复权日 K，返回 live 警告、无落盘/研究质量保证；不是完整实时服务契约。[live实现][live] | 未确认报价时间戳、延迟、盘中有效性、停牌、陈旧策略、SLA，不能预设满足实时估值。 |
| 交易日历 | `query.calendar` 实现 `list_trading_dates`、`is_trading_day`、`last_trading_day`，优先湖内日历，缺覆盖时用 seed/行情证据，避免直接当成普通工作日。[日历查询][calendar]、[日历构建][calendar-build] | 这些函数位于子模块，不能假设全部已从 `cnequity.query` 顶层导出。日历 seed 覆盖边界、特殊休市、未来日期须实测。 |
| 停牌/完成状态 | 文档约定停牌 bar 有 OHLC、volume=amount=0；独立 `trading_status` 存交易状态。日线采集有当天上海时间 15:05 前拒绝未完结窗口门禁。[字段][schema]、[门禁][scope] | bar 的零量不等同独立停牌证据；不能把缺数据解释为停牌。15:05 是项目缓冲策略，不是交易所发布的 final 标记。 |
| 增量/恢复 | 有水位、按批重试、缺口账与作用域恢复；finish 可以容忍一定缺口发布有效行并报告 partial/unresolved。[日线步骤][daily]、[完成核验][finish]、[运维][runbook] | 水位推进/返回 0/有数据都不等于所有持仓齐全。必须逐 symbol/交易日检查缺口；demo 不足以独自验收正式每日增量。 |
| 存储/读取 | Parquet；修订回执与提交 generation 指针；Python `load`/`scan`、SQL、MCP、本地 HTTP。[读取][reader]、[修订][revisions]、[Python文档][python-api] | 可以独立运行进程，Core 不必导入其依赖。直接读 Parquet 会把版本/去重/复权语义责任交给调用方。 |
| 并发/故障 | 日更互斥锁、init 与日更/事件互斥；源端节流/共享预算/冷却、TDX 单 symbol 失败保留其他返回；文件原子替换与 dataset revision 发布锁。[运行锁][run-lock]、[TDX入口][tdx-client]、[修订][revisions]、[源保护][fetch-policy] | 并非任意并发写入或跨机器协调保证。建议单采集调度器；同出口多个湖须共用限速目录；恢复、并行读取完整性、锁超时仍待故障注入验证。 |

### 2.1 价格、数量、复权和时间的具体陷阱

- `daily_bars` 的 v2 数量契约文档称“股”，基金落到下游语义应明确为“份”；TDX 日线原生手在适配边界乘 100，1m/5m 原生数量不再乘 100。`amount` 为人民币元。指数成交量另有未确证契约，不能借用股票规则。[数量换算][units]、[字段][schema]
- TDX 日线代码遇到 `raw_volume=0` 但 `amount>0` 会跳过该键，原因是基金不足一手成交不能凭整手数推定为零成交；需备源或显式缺口。P0 不应为这种缺行补造停牌 bar。[分页实现][tdx-bars]
- 不应严格断言 `amount == close × volume`：一天成交均价不等于收盘价。可检查均价落在合理 OHLC 区间并排查 100 倍单位误差；BJ 日线包含大宗交易又有例外。[数量换算][units]、[字段][schema]
- 查询默认 `adjust=None`。`hfq/qfq` 另加 `adj_*`、`adj_is_exact`；默认 `strict_adj=False` 时缺因子按 1.0 返回，不能默认为复权完整。研究必须显式要求严格模式并检查结果；严格模式证明因子覆盖，不独立证明其正确。[查询实现][reader]、[查询指南][query-guide]
- 基金的 hfq 是拆分/合并可比的价格收益，不包含现金分配；`adjust="total_return"` 才按代码记录的基金现金分配再投资。现金分配历史完整性与总回报正确性未实测，P2 单独验收。[查询指南][query-guide]
- 交易日期按 Asia/Shanghai；`fetched_at` 表示抓取时间，不能替代行情发生时间。`market_time.is_session_final` 对非当天日期（包含未来日期）返回 True，调用方须先拒绝未来请求，再结合交易日历；日线采集门禁独立存在，也不是逐行 final 列。[时间代码][market-time]、[门禁][scope]
- 证券标识 `{code}.{SH|SZ|BJ}` 应保留六位前导零；`parse_symbol` 只校验分隔/交易所，不是完整证券存在性校验。不能用无交易所的 `000001` 猜测股票或指数身份。[符号][symbols]

### 2.2 查询 API 与独立运行方式

Python 的 `load("daily_bars", symbols=[...], start=..., end=..., adjust=None, data_root=...)` 返回 Polars DataFrame，日期含边界。`load_with_receipt(..., require_replayable=True)` 能固定已保留修订并返回来源、查询窗口和版本凭证，但回执 `coverage.completeness="not_assessed"` 不证明完整性；多依赖没有全湖原子事务。[Python文档][python-api]

本地 HTTP 已有 `GET /api/read/{dataset}?start=...&end=...&symbol=...`，返回 `rows` 与同一次读取回执：窗口最多 366 个日历日、多日必须指定证券、结果最多 1000 行、依赖必须有可重放修订。超范围/缺修订会拒绝。`instruments` 无日期查询轴，此入口会拒绝，须用 Python API 或经单独验证的目录接口，不能假设一个 URL 覆盖全部 P0 查询。[HTTP实现][http]

独立 Python 3.12 环境 + CLI 或本地 HTTP 可使 CNEquity 的 pandas/Arrow/DuckDB 依赖不进入 Core。启动 HTTP 时显式加 `--read-only`；默认控制台也包含运维入口，不能把默认 serve 描述为纯只读服务。仅绑定回环地址。[CLI文档][cli]

## 3. 软件许可与数据授权

代码采用 Apache-2.0，使用/分发仍需遵循 LICENSE 与 NOTICE；这不等同采集结果的授权。仓库不附带生产数据湖，维护者明确不授予行情数据再分发、转售或公开托管的权利。[LICENSE][license]、[许可说明][legal]

固定版本的 `sources/SOURCES.yml` 中，TDX、Sina、Baostock、交易所公开文件等的个人使用、商业使用、缓存和再分发大多是 `unknown`；东财与同花顺记录限制性结论并附官方协议链接。**这些是上游项目的合规登记，不是本次重新核阅全部官方条款后的法律结论**。实际启用的主源、备源、历史回填源均要记录具体端点/权利人、适用条款日期、自动访问/缓存/使用范围；未取得足够依据前，授权状态继续写“待核实”，不能写“免费可商用”或“开源即可再分发”。[来源政策登记][source-policy]

本地技术试用成功不代表授权通过。生产启用门槛应同时覆盖实际使用方式（个人本机、跨设备、对外服务、商用等）和数据许可；不需要把未启用数据集的全部权利核查扩展为 P0 建设内容。

## 4. 本次实际完成的轻量核查

使用宿主 Python 标准库读取 manifest，并通过 `runpy.run_path` 执行无第三方依赖的 `domain/symbols.py`，结果如下；未导入/运行完整 CNEquity。

| symbol | `is_all_a_symbol` | `is_etf_symbol` |
|---|---:|---:|
| 600519.SH | True | False |
| 000001.SZ | True | False |
| 510300.SH | False | True |
| 159915.SZ | False | True |
| 510301.SH（非二级交易码样本） | False | False |
| 519001.SH（场外码样本） | False | False |

标准库读取：项目 `version=0.24.0`、`requires-python=>=3.11`；随包日历 CSV 首日 2016-01-01，末日 2027-12-31。该检查只证明文件范围与符号函数行为，不证明每个日历行正确、证券目录最新或上游能返回这些代码。[manifest][manifest]、[符号函数][symbols]、[日历种子][calendar-seed]

## 5. 待执行的小样本试用

### 5.1 范围与环境

基础试用可选四个样本：`600519.SH`、`000001.SZ`、`510300.SH`、`159915.SZ`，最近约 10 个已完成交易日。停牌样本、跨 3 个实际交易日观察、重复运行和受控中断可随需要补充；未覆盖就如实记录，不作为首期开工条件。ETF 不应因 demo 的缺码 warning 被跳过后仍宣称已取得其数据。

环境安装、行情联网、磁盘占用与请求预算全部属于后续试用，本次未执行。固定 commit，不使用无版本 `pip install -U`。以下命令的选项已对照 `setup_cmds.py`/`demo.py`、`backfill_cmds.py` 和 CLI 文档；**是可执行步骤模板，未宣称已跑通**。[初始化实现][setup]、[demo实现][demo]、[回填实现][backfill]、[CLI][cli]

```bash
# 在独立 Python 3.12 环境；安装固定源码，不修改 StratHive 环境。
python3.12 -m venv /tmp/strathive-cne-venv
/tmp/strathive-cne-venv/bin/python -m pip install \
  'git+https://github.com/rootSunc/CNEquity.git@05370ab722acaaf1cf4cebc8b4489aaa81d5083b'
/tmp/strathive-cne-venv/bin/cne --version
/tmp/strathive-cne-venv/bin/cne doctor

# 真实数据小样本；选择独立、原本不存在的目录，不与 sample 合成数据共湖。
/tmp/strathive-cne-venv/bin/cne init --profile demo \
  --symbols 600519.SH,000001.SZ,510300.SH,159915.SZ --days 10 \
  --data-root /tmp/strathive-cne-live-lake \
  --config-out /tmp/strathive-cne-demo.toml

/tmp/strathive-cne-venv/bin/cne query --config /tmp/strathive-cne-demo.toml \
  --sql 'SELECT symbol,trade_date,close,volume,amount,source,data_version,fetched_at FROM daily_bars ORDER BY symbol,trade_date'

# 先只预览固定历史小窗口；日期须按实际目录/交易日历选择。
/tmp/strathive-cne-venv/bin/cne backfill daily_bars \
  --config /tmp/strathive-cne-demo.toml \
  --symbols 600519.SH,000001.SZ,510300.SH,159915.SZ \
  --start 2026-11-09 --end 2026-11-13 --plan

# 在另一终端运行；仅调用 GET，不操作控制台写入口。
/tmp/strathive-cne-venv/bin/cne serve --config /tmp/strathive-cne-demo.toml \
  --read-only --host 127.0.0.1 --port 8787
```

服务启动后可用：

```bash
curl --fail --silent --show-error \
  'http://127.0.0.1:8787/api/read/daily_bars?start=2026-11-09&end=2026-11-13&symbol=510300.SH'
```

若 demo 湖缺可重放依赖而 HTTP 拒绝，应如实记录，继续用 Python raw 查询核实数据；不可关闭凭证门槛后声称 HTTP 验收通过。Python 查询样例：

```python
from cnequity.query import load
bars = load(
    "daily_bars", symbols=["600519.SH", "000001.SZ", "510300.SH", "159915.SZ"],
    start="2026-11-09", end="2026-11-13", adjust=None,
    data_root="/tmp/strathive-cne-live-lake",
)
print(bars)
```

**副作用与范围：**裸 `cne init` 默认是全市场近三年，`full` 从 2016 起，均不用于网络冒烟。demo 虽只取指定证券的日线，仍会连接/探测 TDX、抓全证券列表后过滤、创建本地配置与湖、构建日历、写日志/Parquet。缺指定代码时只 warning 并跳过。`--research` 会扩大历史窗口并抓因子，`--intraday` 会增加分钟请求，本阶段均不加。demo/sample 配置没有正式调度组；不能直接把其 `run daily` 当正式增量已验收。[初始化][setup]、[demo][demo]

正式增量试验应另外建立可复核的最小配置：固定四证券采集范围、workers=1、仅必要数据集、基金已启用、只使用已核查来源，再通过 `config validate` 和实际帮助检查作用域。这个配置需要结合安装版本生成/核查，不在本报告猜写完整 TOML。`run daily`/backfill 完成可能按默认保留策略删除较老 generation，先设置保留策略或保留需比较的修订；不得对 Core 账本目录执行这些命令。[配置][config]、[安装与保留规则][installation]

### 5.2 按需试用与增强矩阵

以下是排查及后续增强的观察清单，不是强制质量门槛，也不是 CNEquity 的官方 SLA。实现先验证所需的实际调用链路；其余项目按使用需要补充。

| 编号 | 执行与保存证据 | 观察目标 | 问题处理 |
|---|---|---|---|
| T0 安装/隔离 | 记录 Python、commit、依赖解析结果、doctor、安装耗时与磁盘变化 | 独立环境可安装，Core 原环境/锁文件不变；真实源与模拟调用分开记录 | 该后端不可用时降级，不阻塞其他来源及 Core 开发 |
| T1 主数据/标识 | 所选证券目录行；按需核对名称/市场/基金类型，检查缺码警告 | 标识转换正确，缺字段明确为空；ETF 身份不能只靠代码前缀 | 记录缺码和支持范围，不以股票成功掩盖 ETF 缺失 |
| T2 原始日线/单位 | 小窗口保存 raw/来源/抓取时间/data_version；有疑问时抽样对照公开历史行情 | 价格、重复键、v2 数量单位和 OHLC/量额差异可解释 | 记录差异并定位具体数据/转换问题；不扩展成全市场质量前置审查 |
| T3 日历/停牌/final | 按需观察节假日、当天15:05前后及真实停牌样本 | 未收盘不冒充当日收盘，保留实际日期；缺数不推断停牌 | 未知状态明确返回，不阻塞首期其他来源和能力 |
| T4 增量/幂等 | 后续可跨 3 交易日观察更新及同日重复跑，对比键集合、缺口与水位 | 最新覆盖明确，无静默重复；旧价修订可追溯 | 先明确未完成范围，增量增强不作为首期盘中估值的前置 |
| T5 故障/恢复 | 按需做受控断连/中断，保存 run ID 并观察恢复 | 旧可用数据仍可读，失败/陈旧明确，不混模拟数据 | 隔离受影响的后端或数据范围，其他能力继续可用 |
| T6 查询/并发 | 有需要时比较 Python/HTTP raw 或并行读写，记录回执与耗时 | 读取完整已发布版本，限制清楚；观察本地查询性能 | HTTP 不可用先用 Python 桥接；不使用的入口不要求一并验证 |
| T7 授权 | 记录本次实际主/备/回填端点的条款与适用使用场景 | 本地缓存/预定使用有足够授权依据，使用范围与限制可执行 | 授权未明维持技术试验，不作生产/分发通过结论 |
| T8 快照/分钟/复权扩展 | 如选择 CNEquity 承担快照，读取实际时间及延迟；分钟和复权按后续需求另验 | 快照是否可用须实际调用，不能从日线推定；分钟/复权仍按自身语义处理 | CN 快照不可用不阻塞首期其他报价来源，历史能力独立保留 |

不再要求 T0–T7 全部通过才开发或启用首期其他能力。实测取得了哪些证券和数据就记录哪些，未覆盖项不能宣称已经验证；某来源/品种不可用时按能力降级或替换。来源条款仍按实际用途记录，技术可读不等于已获得数据再分发授权。

每次运行保存脱敏环境/版本、symbol和日期窗口、run ID、退出码、行数和缺口、质量报告、查询与修订回执、抽样对照出处、耗时/请求与冷热缓存、授权核查状态。原始湖、完整日志、代理/Cookie/token均留本机；仓库只保存脱敏结论。

## 6. 一手资料索引（固定版本）

下面链接分别支撑正文对应结论；其中官网/官方目录链接仅用于后续逐接口核对，不把页面可访问视为数据授权。

[manifest]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/pyproject.toml
[ci]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/.github/workflows/ci.yml
[installation]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/docs/zh/getting-started/installation.md
[symbols]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/src/cnequity/domain/symbols.py
[sources]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/docs/zh/datasets/sources.md
[schema]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/docs/zh/datasets/schema.md
[tdx-client]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/src/cnequity/adapters/tdx_protocol/client.py#L679
[tdx-bars]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/src/cnequity/adapters/tdx_protocol/bars.py
[config]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/src/cnequity/config/templates/cnequity.example.toml
[live]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/src/cnequity/mcp_server/live.py
[calendar]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/src/cnequity/query/calendar.py
[calendar-build]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/src/cnequity/adapters/calendar/exchange_calendar.py
[calendar-seed]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/src/cnequity/adapters/calendar/seeds/trading_calendar.csv
[scope]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/src/cnequity/steps/bars/bar_scope.py#L31
[daily]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/src/cnequity/steps/bars/daily.py
[finish]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/src/cnequity/steps/bars/finish.py
[runbook]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/docs/zh/operations/runbook.md
[reader]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/src/cnequity/query/reader.py
[revisions]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/src/cnequity/storage/revisions.py
[run-lock]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/src/cnequity/orchestrator/run_lock.py
[fetch-policy]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/docs/zh/operations/fetch-policy.md
[units]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/src/cnequity/domain/units.py
[query-guide]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/docs/zh/datasets/query-guide.md
[market-time]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/src/cnequity/domain/market_time.py
[python-api]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/docs/zh/reference/python-api.md
[http]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/src/cnequity/serve/app.py#L756
[cli]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/docs/zh/reference/cli.md
[license]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/LICENSE
[legal]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/docs/zh/legal-and-data-sources.md
[source-policy]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/sources/SOURCES.yml
[setup]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/src/cnequity/cli/setup_cmds.py#L190
[demo]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/src/cnequity/cli/demo.py
[backfill]: https://github.com/rootSunc/CNEquity/blob/05370ab722acaaf1cf4cebc8b4489aaa81d5083b/src/cnequity/cli/backfill_cmds.py
