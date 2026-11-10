# 策略蜂巢（StratHive）

在本机记录券商账户、策略运行、成交和资产交接，并与券商账面数据对账。一次策略运行直接绑定所选策略的一个具体版本及一个资产账户，启动时分配期初资产，结束时结清名下现金与持仓。

## 从源码运行与接入 Agent

在仓库根目录安装依赖。使用链接了 WAL 并发修复版 SQLite 的 Python。

```sh
uv sync --locked
export PATH="$PWD/.venv/bin:$PATH"
strathive --help
```

将仓库中的 `skills/strathive-ledger` 复制到项目的 `.agents/skills/strathive-ledger`，并让 Codex 会话继承上述 `PATH`。启动新会话后，使用 `$strathive-ledger` 操作账本。正式版本准备好后通过 GitHub Releases 发布。

## 用 Agent 操作账本

可以先用独立账本体验，告诉 Agent：

> 使用 $strathive-ledger，在 demo.sqlite3 建一个示例账本。建立一个只有 10,000 元现金的券商账户，佣金费率 0.03%，建账截止时间为 2026-11-01 09:00。创建一个人工策略，并在 2026-11-02 09:00 启动一次运行，分配 2,000 元期初现金。

随后可以让 Agent 记录实际成交、查询运行持仓或导入券商快照对账。结束运行时，先告知 Agent 剩余证券是已经卖出，还是要交给同一账户的新运行。关键写入由 Agent 确认所需输入并依据 CLI 结果反馈。

## CLI 契约与排障

CLI 面向 Agent 和自动化调用。业务命令默认返回 JSON，命令参数用 `--help` 查看。使用全局 `--db PATH` 选择账本。下列接口和 [结构化输入示例](skills/strathive-ledger/references/inputs.md) 描述本版设计，排障时以安装版本的帮助为准。

- `strategy create` 生成 `v1`。更新策略资料后可用 `strategy version create --strategy-id ID` 固化新版本。`run create` 可通过 `--version` 指定版本，省略时绑定当时的最新版。
- `run show ID` 返回绑定版本和运行资产。记成交、带持仓建账及结束运行等写入可通过请求 ID 恢复已提交结果。
- `strathive db backup --file backup.sqlite3` 创建备份。`strathive --db recovered.sqlite3 db restore --file backup.sqlite3` 恢复到新账本。升级后运行 `strathive db init`，旧账本升级前自动备份。旧账本中标记结束但仍有资产的运行会转为暂停，供用户结清。

## 本地开发

使用 `uv run strathive` 调用 CLI，使用 `uv run pytest` 验证。

离线证券目录记录来源和获取日期。深交所接口不可用时，深圳条目采用东方财富目录。可运行 `scripts/update-securities.py` 更新源码中的目录，也可用 `strathive security import --file securities.csv` 导入已有账本。
