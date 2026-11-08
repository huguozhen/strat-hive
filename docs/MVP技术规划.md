# 策略蜂巢 · MVP 技术规划

## 1. 架构与交付

**个人本地应用，Python Core，嵌入式 SQLite，CLI 与配套 Agent Skills。** 账本放在本机应用数据目录。Core 提供统一的应用接口，负责领域规则、查询和事务。

```text
用户 ──────────────────────┐
                           ├── CLI ── 进程内调用 ── Core ── SQLite 账本
用户 ── Agent + Skills ────┘
```

CLI 每次执行命令时直接加载 Core，完成后退出。Agent 在可执行本机 CLI 的环境中，按 Skills 提供的流程调用同一命令。面向用户的发布包集成 Python 运行时和 CLI，SQLite 随运行时提供。配套 Skills 随 MVP 交付，记录命令用法、所需输入、输出解读和失败处理。

Rotki 的本地 Python 应用和 SQLite 存储可作为交付参考。VeighNa 的 SQLite 默认适配器和可插拔数据网关可作为数据接入参考。 [Rotki 构建指南](https://docs.rotki.com/requirement-and-installation/build-from-source) · [Rotki 数据库](https://docs.rotki.com/contribution-guides/rotki-database) · [VeighNa](https://github.com/vnpy/vnpy/blob/master/README_ENG.md)

## 2. 技术选型

| 层 | 选型与理由 |
| --- | --- |
| Core | **Python**。可直接调用国内行情商提供的 Python SDK，也能在后续策略、复盘和回测中复用纯计算规则。供应商 SDK 放在数据源适配层。Java 专用 SDK 有实际接入需求时，采用独立适配进程。 [Tushare 接入方式](https://tushare.pro/document/2?doc_id=40) |
| 本地存储 | **SQLite + SQLAlchemy Core + Alembic**。嵌入式数据库负责关系约束和事务，显式 SQL 与迁移便于检查和升级本地数据。金额和费用以分为单位存储，单价、费率和单位成本保留各自精度，Core 使用 `Decimal` 计算并统一舍入。 [SQLite 事务](https://sqlite.org/lang_transaction.html) · [Python Decimal](https://docs.python.org/3/library/decimal.html) |
| 操作入口 | **Typer CLI + 配套 Agent Skills**。CLI 提供人读输出、`--json` 和稳定退出码。Skills 依赖 CLI 命令及输出契约，业务校验与事务由 Core 统一执行。 |
| 工程 | **Python 单仓**，使用 `uv` 管理依赖，使用 `pytest` 验证 Core、SQLite 和 CLI。Skills 与 CLI 一同维护，端到端测试覆盖人工命令和 Agent 调用。 |

## 3. Core 的顶层模块

| 模块 | MVP 职责 |
| --- | --- |
| 策略与运行 | 管理人工策略、策略运行、状态和逻辑资产池绑定，支持对暂停或结束的运行补记真实成交 |
| 资产账户与资产池 | 维护实际资产账户、佣金费率和逻辑资产池，完成建账、存量持仓承接、账户资金进出及资产池现金划转 |
| 交易日志与账本 | 管理成交明细与归属、计算及覆盖费用，派生账户和资产池的现金、持仓及成本 |
| 证券资料 | 维护沪深主板股票和 ETF 的证券标识，提供有效性校验、搜索与精确解析 |
| 即时对账 | 接收手填或导入的券商状态，核对券商与实际资产账户、实际资产账户与资产池汇总，返回即时差异 |

Core 以 Python 应用用例作为统一接口，持有记账规则和事务边界。建账初始状态、资金操作和成交是记账事实。事实变更与当前资产状态同事务提交，补记、修改或删除成交时按顺序重算，费用采用已确认的入账值。持仓成本在独立规则中计算。 [持仓成本调研](research/position-cost.md)

人工与 Agent 启动的 CLI 进程可能同时访问同一个账本。SQLite 使用 WAL，允许并发读取、串行写入，发布时校验实际链接的 SQLite 版本已包含 WAL 并发修复。记账使用短事务和有界锁等待，对账使用一致的读事务。数据库升级统一管理，并提供一致性备份与恢复入口。 [SQLite WAL](https://sqlite.org/wal.html)

## 4. 项目结构

```text
src/strathive/
  core/          # 领域模块、应用用例、统一接口和存储契约
  adapters/      # SQLite、数据库迁移、文件导入与数据源适配
  cli/           # 命令、参数和输出格式
  bootstrap.py   # 装配 Core 与本地适配器
skills/          # 配套 Agent Skills
tests/           # Core、SQLite、CLI 与 Agent 端到端验证
docs/
```

## 5. 实施与验收

1. **用 CLI 跑通 Core**。完成本地初始化、证券资料、建账、策略运行、资金操作、成交与对账，支持至少一种券商状态导入格式，验证跨进程写入、数据升级和恢复。
2. **交付配套 Agent Skills**。基于同一套 CLI 命令实现账本工作流，验证 Agent 能读取结构化输出并正确反馈成功、失败和对账差异。
3. **通过两种操作方式验证交付**。功能体验、端到端测试和验收通过人工执行 CLI 命令或 Agent 使用 Skills 调用 CLI 进行。两种方式均验证建账、成交录入、查询和即时对账等代表性流程。

## 6. 下一期与后续演进

下一期新增 **React + TypeScript + Vite Web UI**，通过按需启动的本机 FastAPI 调用同一 Core，提供账本操作和分析展示。Web 客户端由 OpenAPI 生成，表格使用 TanStack Table，查询使用 TanStack Query，仪表盘按需引入 Apache ECharts。CLI 与 Agent Skills 随分析、复盘能力扩展。 [FastAPI SDK 生成](https://fastapi.tiangolo.com/advanced/generate-clients/) · [Apache ECharts](https://echarts.apache.org/en/feature.html)

后续行情同步采用限流、批量拉取和可续跑的增量同步。行情历史按需保存到独立的 Parquet 数据集，Polars 用于向量化处理和聚合，SQL 分析需要时再接入嵌入式 DuckDB。重计算用独立本地进程，仪表盘按时间范围请求聚合或降采样结果。策略、复盘和回测可以复用 Core 的纯计算规则，真实账本与模拟状态分别存储。 [Polars 流式执行](https://docs.pola.rs/user-guide/concepts/streaming/) · [DuckDB Parquet 查询](https://duckdb.org/docs/stable/data/parquet/overview.html)
