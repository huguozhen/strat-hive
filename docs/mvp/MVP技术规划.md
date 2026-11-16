# 策略蜂巢 · MVP 技术规划

## 1. 架构与交付

策略蜂巢是个人本地应用。用户通过 Agent Skills 操作账本。CLI 是供 Agent 调用的本机机器接口，在进程内调用 Python Core 并于命令完成后退出。SQLite 账本默认位于本机应用数据目录，也可选择已有文件或指定新建目录。开发阶段从源码运行，正式版本通过 GitHub Releases 交付。

```text
用户 ── Agent + Skills ── CLI ── Core ── SQLite
```

Rotki 的本地 Python 应用和 SQLite 存储可作为交付参考。VeighNa 的数据库适配器与数据网关可作为后续数据接入参考。 [Rotki 构建指南](https://docs.rotki.com/requirement-and-installation/build-from-source) · [Rotki 数据库](https://docs.rotki.com/contribution-guides/rotki-database) · [VeighNa](https://github.com/vnpy/vnpy/blob/master/README_ENG.md)

## 2. 技术选型

| 层 | 选型 |
| --- | --- |
| Core | Python，按业务用例组织，纯计算规则可用于后续策略分析和回测。行情 SDK 通过数据源适配器接入。 [Tushare 接入方式](https://tushare.pro/document/2?doc_id=40) |
| 存储 | SQLite、SQLAlchemy Core、Alembic。使用构造器或绑定参数访问数据库。现金与费用按分存储，单价、费率和单位成本使用 Decimal。 [SQLite 事务](https://sqlite.org/lang_transaction.html) · [Python Decimal](https://docs.python.org/3/library/decimal.html) |
| 操作入口 | Typer CLI 与 Agent Skills。CLI 提供稳定的参数、JSON 输出和退出码，保留 `--help` 供发现和排障。业务校验与事务集中在 Core |
| 工程 | Python 单仓，使用 `uv` 管理依赖，使用 `pytest` 验证 Core、SQLite 和 CLI |

## 3. Core 模块与记账模型

| 模块 | 职责 |
| --- | --- |
| 策略与运行 | 管理人工策略、不可变版本、运行状态和账户绑定，提供启动、暂停、恢复、结束和交接用例 |
| 资产账户与账本 | 管理建账、账户资金进出、运行期初现金与持仓、结束资金归还和持仓交接，派生账户及运行当前状态 |
| 交易日志 | 记录真实成交及运行归属，计算费用并接受覆盖，支持查询、更正与删除 |
| 证券资料 | 提供沪深主板股票及 ETF 的离线目录、有效性校验与精确解析 |
| 即时对账 | 读取券商输入和同一事务中的账本状态，核对券商与账户、账户与运行汇总 |

策略实体保留名称和描述。每条版本记录归属于一条策略，保存单调递增的版本号、创建时间及名称和描述快照，内部 ID 用于运行引用。数据库约束同一策略内的版本号唯一。创建策略与生成 `v1` 在同一事务内完成。创建后续版本和创建运行时的版本解析均在写事务中完成，防止并发编号重复或运行引用漂移。

运行保存具体版本 ID 与资产账户 ID。选择最新版只发生在创建运行时，查询历史直接读取绑定版本。Core 提供版本创建和查询用例，已保存版本无覆盖或删除操作。策略规则正文的数据结构、文件格式与执行方式留待后续设计。

期初分配、结束归还及持仓交接均由账本事实驱动，运行状态和资产变动同事务提交。现金型建账可以独立完成；持仓型建账同步记录承接归属及运行起点。交易按业务时间与稳定序号重算，使用已确认的入账费用。已结束运行的补记或更正须落在其运行期间，并能同步重算归还现金和交接后续状态。无法保持结束归零或后续归属一致时，整笔更正回滚。

运行结束时需验证持仓通过真实卖出或交接归零，并释放全部剩余现金。同账户交接同时关闭旧运行和启动新运行，成对记录转出与转入；账户总现金、证券数量和历史账本成本保持连续。交接估值与账本成本分别保存，来源和时间随估值记录。双方绩效使用同一个值，缺少估值时返回待估值状态。 [持仓成本调研](持仓成本计算调研.md)

核心业务事实保存在可独立备份和恢复的 SQLite 账本，包含解释历史成交、持仓和交接所需的证券引用及已确认估值。可重建的第三方证券目录使用独立 SQLite 文件，后续行情使用独立目录，配置及凭据单独存放。新增数据按可重建性和对历史业务结果的影响决定归属。 [存储布局与安装流程](银河数据源与Linux发布实施计划.md#2-账本选择与数据存储)

SQLite 使用 WAL 和短写事务，启动时校验运行时包含 WAL 并发修复。数据库升级由 Alembic 管理，迁移前保留一致性备份。迁移验证账户和运行的现金、持仓、成交归属及历史资金事实，既有运行期间的资金变动按原事实保留。既有策略按迁移时可见的名称和描述生成带来源标记的初始版本，既有运行绑定该版本。迁移来源表示当时可取得的快照。旧账本标记为结束但仍有资产的运行迁移为暂停并保留原状态，等待用户结清；有余额或历史流水但尚无运行归属的旧记录迁入专门的暂停运行，以便继续归还资金。 [SQLite WAL](https://sqlite.org/wal.html)

## 4. 项目结构

```text
src/strathive/
  core/          # 领域规则、运行生命周期和账本用例
  adapters/      # SQLite、迁移、文件导入与数据源适配
  cli/           # 机器命令、结构化输入与输出
  bootstrap.py   # 装配 Core 与本地适配器
skills/          # CLI 操作流程
tests/           # Core、数据库、CLI 与 Agent 验收
docs/
```

## 5. CLI 契约与验收

业务命令默认在标准输出返回一个 JSON 对象。成功返回 `ok: true` 和 `data`，失败返回 `ok: false` 以及包含稳定 `code` 和可读 `message` 的 `error`。成功退出码为零，失败使用稳定的非零退出码。对账差异属于成功返回的业务结果。诊断信息走标准错误，`--help` 用于发现命令和参数。

简单查询和单步操作使用具名参数。带持仓建账、成交更正和交接估值等复合输入可使用 JSON，券商快照可使用 CSV。命令按业务动作设计，由 Core 在一次事务内完成复合写入。

记成交、带持仓建账、结束与交接等关键写命令支持请求 ID。相同请求 ID 和输入返回已提交结果，相同 ID 携带不同输入时返回冲突。写入结果不明时，Skill 用同一请求 ID 确认结果，或先查询账本再决定下一步。

`strategy create` 创建策略及其初始版本。`strategy version create` 固化当前策略资料，`strategy version list` 和 `strategy version show` 用于查询。`run create` 接收策略、可选版本号和资产账户，`run show` 返回绑定版本。`run start` 接收期初现金和启动时间。`run close` 接收结束时间，可指定待启动的新运行、该运行的期初分配现金和可选交接估值。暂停和恢复通过运行状态操作。启动、结束和交接通过专用用例完成。带持仓建账、结构化估值和对账格式见 [CLI 输入说明](../../skills/strathive-ledger/references/inputs.md)。

1. **Core 与数据库**。验证版本编号、快照不可变、历史绑定、并发创建和迁移，覆盖纯现金及带持仓建账、资金隔离、成交重算、运行中普通调资拒绝、持仓未清时结束失败、卖出退出与承接退出。覆盖交接回滚、账户汇总连续性、缺少估值、补记以及备份恢复。
2. **CLI 契约与产品验收**。自动化测试校验参数、默认 JSON、错误码、退出码和写入重试。本机 Codex 通过 Skill 在独立账本完成版本选择、建账、启动、成交、对账、结束和交接，核对版本、现金、数量及归属。
3. **版本发布**。版本准备就绪后，在干净环境验证初始化及完整工作流，再通过 GitHub Releases 发布同版本的 CLI、说明和 Skill。

## 6. 下一期与后续演进

下一期通过按需启动的本机 FastAPI 复用同一 Core，使用 React、TypeScript 和 Vite 实现 Web UI。Web 客户端由 OpenAPI 生成，表格和查询使用 TanStack，仪表盘按需引入 Apache ECharts。Agent Skills 随分析和复盘扩展，CLI 提供对应机器接口。TUI 如有需求，也复用同一 Core。 [FastAPI SDK 生成](https://fastapi.tiangolo.com/advanced/generate-clients/) · [Apache ECharts](https://echarts.apache.org/en/feature.html)

后续行情适配器负责增量同步。行情历史在独立目录保存为 SQLite 或 Parquet，Polars 用于批量计算，按需使用 DuckDB 提供本地分析查询。行情估值用于市值仓位和运行绩效，真实账本与模拟运行分别存储。 [Polars 流式执行](https://docs.pola.rs/user-guide/concepts/streaming/) · [DuckDB Parquet 查询](https://duckdb.org/docs/stable/data/parquet/overview.html)
