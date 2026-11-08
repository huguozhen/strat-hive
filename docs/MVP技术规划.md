# 策略蜂巢 · MVP 技术规划

## 1. 架构与交付

**个人本地应用，Python Core，嵌入式 SQLite，浏览器 Web UI。** 应用在用户自己的电脑上运行，账本放在本机应用数据目录。Core 是统一的应用接口，负责领域规则、查询和事务。CLI 与 Web UI 是两种操作入口。

```text
CLI ──────── Python 进程内调用 ─────────┐
                                      ├── Core ── SQLite 账本
浏览器 Web UI ── 本机 HTTP ── FastAPI ──┘
```

CLI 每次执行命令时直接加载 Core，独立于 Web 服务运行。`strathive ui` 按需启动仅监听回环地址的 HTTP 服务，同时提供静态页面和 API，由同一进程调用 Core。两种入口使用同一应用装配逻辑和本地账本。面向用户的发布包集成 Python 运行时、CLI、HTTP 入口和预构建 Web 资源，SQLite 随运行时提供。Node.js 用于开发和前端构建。本机 HTTP 使用同源页面与会话校验保护写操作。

Rotki 的本地 Python 后端、回环 HTTP 和 SQLite 存储可作为交付参考。VeighNa 的 SQLite 默认适配器和可插拔数据网关可作为数据接入参考。 [Rotki 构建指南](https://docs.rotki.com/requirement-and-installation/build-from-source) · [Rotki 数据库](https://docs.rotki.com/contribution-guides/rotki-database) · [VeighNa](https://github.com/vnpy/vnpy/blob/master/README_ENG.md)

## 2. 技术选型

| 层 | 选型与理由 |
| --- | --- |
| Core | **Python**。可直接调用国内行情商提供的 Python SDK，也能在后续策略、复盘和回测中复用纯计算规则。供应商 SDK 放在数据源适配层。Java 专用 SDK 有实际接入需求时，采用独立适配进程。 [Tushare 接入方式](https://tushare.pro/document/2?doc_id=40) |
| 本地存储 | **SQLite + SQLAlchemy Core + Alembic**。嵌入式数据库负责关系约束和事务，显式 SQL 与迁移便于检查和升级本地数据。金额和费用以分为单位存储，单价、费率和单位成本保留各自精度，Core 使用 `Decimal` 计算并统一舍入。 [SQLite 事务](https://sqlite.org/lang_transaction.html) · [Python Decimal](https://docs.python.org/3/library/decimal.html) |
| 操作入口 | **Typer CLI + FastAPI 本机 HTTP**。CLI 提供人读输出、`--json` 和退出码。HTTP 负责请求转换、会话和响应，Web 的 TypeScript 客户端从 OpenAPI 生成。业务校验与事务由 Core 统一执行。 [FastAPI SDK 生成](https://fastapi.tiangolo.com/advanced/generate-clients/) |
| Web UI | **React + TypeScript + Vite**。静态前端配合 TanStack Query 处理查询、提交与刷新，TanStack Table 支撑交易和对账表格。分析阶段按需引入 **Apache ECharts**，支持 K 线、多图联动、缩放和热力图。 [Apache ECharts](https://echarts.apache.org/en/feature.html) |
| 工程 | **单仓、两套工具链**。Python 用 `uv`、`pytest`，Web 用 `pnpm`。统一提交和验证 Core、接口契约、CLI 与 Web 构建产物。 |

## 3. Core 的顶层模块

| 模块 | MVP 职责 |
| --- | --- |
| 策略与运行 | 管理人工策略、策略运行、状态和逻辑资产池绑定，支持对暂停或结束的运行补记真实成交 |
| 资产账户与资产池 | 维护实际资产账户、佣金费率和逻辑资产池，完成建账、存量持仓承接、账户资金进出及资产池现金划转 |
| 交易日志与账本 | 管理成交明细与归属、计算及覆盖费用，派生账户和资产池的现金、持仓及成本 |
| 证券资料 | 维护沪深主板股票和 ETF 的证券标识，提供有效性校验、搜索与精确解析 |
| 即时对账 | 接收手填或导入的券商状态，核对券商与实际资产账户、实际资产账户与资产池汇总，返回即时差异 |

Core 以 Python 应用用例作为统一接口，持有记账规则和事务边界。建账初始状态、资金操作和成交是记账事实。事实变更与当前资产状态同事务提交，补记、修改或删除成交时按顺序重算，费用采用已确认的入账值。持仓成本在独立规则中计算。 [持仓成本调研](research/position-cost.md)

CLI 和 Web 服务可能从不同进程访问同一个账本。SQLite 使用 WAL，允许并发读取、串行写入，发布时校验实际链接的 SQLite 版本已包含 WAL 并发修复。记账使用短事务和有界锁等待，对账使用一致的读事务。数据库升级统一管理，并提供一致性备份与恢复入口。 [SQLite WAL](https://sqlite.org/wal.html)

## 4. 项目结构

```text
src/strathive/
  core/          # 领域模块、应用用例、统一接口和存储契约
  adapters/      # SQLite、数据库迁移、文件导入与数据源适配
  cli/           # 命令、参数和输出格式
  http/          # 本机 API、会话和静态页面
  bootstrap.py   # CLI 与 HTTP 共用的 Core 装配
web/             # React 页面、仪表盘、生成的 API 客户端
tests/           # Core、数据库与入口集成测试
docs/
```

## 5. 实施与性能演进

1. **先用 CLI 跑通 Core**。完成本地初始化、证券资料、建账、策略运行、成交与对账，验证跨进程写入、数据升级和恢复。
2. **接入 Web UI 并验证交付**。复用同一套用例，完成录入、查询和对账工作流，支持至少一种券商状态导入格式，验证从安装、打开浏览器到 CLI 与 Web 同时操作本地账本。
3. **按需扩展行情和分析**。数据源适配器做限流、批量拉取与可续跑的增量同步。行情历史按需落到独立的 Parquet 数据集，Polars 用于向量化处理和聚合，SQL 分析需要时再接入嵌入式 DuckDB。重计算用独立本地进程，仪表盘按时间范围请求聚合或降采样结果。 [Polars 流式执行](https://docs.pola.rs/user-guide/concepts/streaming/) · [DuckDB Parquet 查询](https://duckdb.org/docs/stable/data/parquet/overview.html)

后续策略、复盘和回测可以复用 Core 的纯计算规则，真实账本与模拟状态分别存储。Agent 可先从 CLI 的机器可读输出接入，再按场景增加调用适配器。
