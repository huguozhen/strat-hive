# 策略蜂巢（StratHive）

在本机记录券商账户、策略运行、成交和资产交接，并与券商账面数据对账。一次策略运行直接绑定所选策略的一个具体版本及一个资产账户，启动时分配期初资产，结束时结清名下现金与持仓。

## Ubuntu 24.04 x86-64 安装

从 [GitHub Releases](https://github.com/huguozhen/strat-hive/releases) 下载 `.deb` 和校验和。在下载目录运行以下命令，将 `<版本>` 替换为下载的版本号。

```sh
sha256sum -c SHA256SUMS
sudo apt install ./strathive_<版本>_amd64.deb
strathive setup
```

向导可打开已有 StratHive 账本，或在默认目录 `~/.local/share/strathive/` 及自选目录新建账本。账本文件保存账户、成交和策略运行等业务记录，建议定期备份到另一块磁盘或其他存储介质。证券目录另存到 `~/.cache/strathive/reference/`，可以重新同步。

推荐使用银河星耀数智。请先联系中国银河证券客户经理申请账号和密码，向导会安装 SDK 并测试连接。暂时没有账号时可选择公开证券目录。首次同步后，两种来源都按北京时间 09:15 每日刷新。没有 systemd 用户会话时，向导会给出手动启用和 cron 配置提示。

向导完成后，把终端显示的提示词复制给你常用的本机 Agent，例如 Hermes、OpenClaw、Codex 或 WorkBuddy。提示词包含与当前安装版本匹配的 GitHub Skill 目录，也提供安装包内的备用路径 `/usr/share/strathive/skills/strathive-ledger`。

## 从源码运行与接入 Agent

在仓库根目录使用 `uv` 安装锁定依赖。项目固定使用 CPython 3.14。

```sh
uv sync --locked
export PATH="$PWD/.venv/bin:$PATH"
strathive --help
```

将仓库中的 `skills/strathive-ledger` 复制到项目的 `.agents/skills/strathive-ledger`，并让 Codex 会话继承上述 `PATH`。启动新会话后，使用 `$strathive-ledger` 操作账本。

## 用 Agent 操作账本

可以先用独立账本体验，告诉 Agent：

> 使用 $strathive-ledger，在 demo.sqlite3 建一个示例账本。建立一个只有 10,000 元现金的券商账户，佣金费率 0.03%，建账截止时间为 2026-11-01 09:00。创建一个人工策略，并在 2026-11-02 09:00 启动一次运行，分配 2,000 元期初现金。

随后可以让 Agent 记录实际成交、查询运行持仓或导入券商快照对账。结束运行时，先告知 Agent 剩余证券是已经卖出，还是要交给同一账户的新运行。关键写入由 Agent 确认所需输入并依据 CLI 结果反馈。

建账或修改账户时可分别提供股票、ETF 佣金费率，例如股票 0.03%、ETF 0.01%。CLI 使用 `--stock-commission-rate 0.0003 --etf-commission-rate 0.0001`，也可在 `account edit` 中单独修改其中一类。

## CLI 契约与排障

CLI 面向 Agent 和自动化调用。业务命令默认返回 JSON，命令参数用 `--help` 查看。CLI 默认使用向导选定的账本，全局 `--db PATH` 可覆盖单次操作。下列接口和 [结构化输入示例](skills/strathive-ledger/references/inputs.md) 描述本版设计，排障时以安装版本的帮助为准。

- `strategy create` 生成 `v1`。更新策略资料后可用 `strategy version create --strategy-id ID` 固化新版本。`run create` 可通过 `--version` 指定版本，省略时绑定当时的最新版。
- `run show ID` 返回绑定版本和运行资产。记成交、带持仓建账及结束运行等写入可通过请求 ID 恢复已提交结果。
- `strathive db backup --file backup.sqlite3` 创建一致性备份。`strathive --db recovered.sqlite3 db restore --file backup.sqlite3` 恢复到新账本。恢复后可通过 `strathive setup` 选用这个文件。打开旧版账本时，向导会在迁移前生成备份。
- `strathive security sync` 手动刷新目录，`strathive security sync-status` 查看最后一次尝试和成功时间。`strathive security import --file securities.csv` 把 CSV 导入独立的证券目录库。

## 升级与回滚

升级前执行 `systemctl --user stop strathive-security-sync.timer strathive-security-sync.service` 并备份账本。安装新版后运行 `strathive setup`，打开现有账本并重新启用每日同步。回滚时用 `sudo apt install --allow-downgrades ./strathive_<旧版本>_amd64.deb`，如需恢复迁移前状态，使用相应备份恢复到新文件。

## 本地开发

使用 `uv run strathive` 调用 CLI，使用 `uv run pytest` 验证。

离线证券目录记录来源和获取日期。深交所接口不可用时，深圳条目采用东方财富目录。可运行 `uv run python scripts/update-securities.py` 更新源码中的离线目录。

普通 PR 的 CI 运行 `uv run --locked pytest -q`，不构建安装包或启动 Docker。维护者可运行 `./scripts/build-deb.sh` 在本机构建 `.deb` 和 `SHA256SUMS`。

推送与 `pyproject.toml` 版本一致的 `vX.Y.Z` 标签后，Linux release 工作流运行测试、构建安装包、验证 Ubuntu 安装和干净容器中的包内运行时，再创建 Release 草稿供维护者核对发布。也可在 GitHub Actions 中手动运行该工作流，提前验证并下载构建产物；手动运行不创建或更新 Release 草稿。 [实施计划](docs/mvp/银河数据源与Linux发布实施计划.md)

## 盘中参考估值

目录和行情可分别选择来源。已有银河连接可用以下命令选用银河行情；该命令保存选择，不代表已实连通过。`doctor --online` 验证日历，盘中 `quote --refresh` 验证实际报价。没有 SDK、账号或权限时，相关功能返回不可用或旧价降级，账本操作仍可使用。

```sh
strathive data configure --provider galaxy
strathive data status
strathive data doctor --online
strathive data quote --security SH.600000 --security SZ.159915 --refresh
strathive valuation account --account-id 1
strathive valuation run --run-id 1 --offline
```

默认报价缓存约 60 秒，`--refresh` 跳过短缓存，`--offline` 禁止联网。结果保留价格时间、来源和降级说明；完整缺价的持仓不会按零值或成本计入总资产。行情 SDK 只在有界子进程中运行，不需要行情守护进程。

带持仓建账、持仓交接会按生效日期自动获取收盘价并回显；行情不可用时事实仍可入账，市值标为待估值。配置恢复后可用 `strathive valuation complete --run-id 1` 补齐空缺计量，已保存价格保持原值。期初及交接价格随账本备份，不依赖行情缓存恢复。
