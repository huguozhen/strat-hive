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

维护者可运行 `./scripts/build-deb.sh` 构建 `.deb` 和 `SHA256SUMS`。推送与 `pyproject.toml` 版本一致的 `vX.Y.Z` 标签后，GitHub Actions 验证 Ubuntu 安装和包内运行时，再创建 Release 草稿供维护者核对发布。 [实施计划](docs/mvp/银河数据源与Linux发布实施计划.md)
