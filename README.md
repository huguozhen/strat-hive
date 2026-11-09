# 策略蜂巢（StratHive）

在本机维护策略运行、资产池和成交记录，并与券商账面数据对账。

## 安装

下载并解压 `strathive-linux-x86_64.tar.gz`。发布包内置 Python 和 SQLite，适用于 Linux x86-64，已在 Ubuntu 24.04 验证。

```sh
tar -xzf strathive-linux-x86_64.tar.gz
export PATH="$PWD/strathive:$PATH"
strathive --help
```

可将第二行添加到 shell 配置中。移动应用时，保留整个解压后的目录。

## 从建账到对账

以下示例使用独立的 `demo.sqlite3`，避免操作默认账本。多参数命令使用具名选项，顺序可调整。单条详情可直接跟 ID，例如 `strathive run show 1`。

```sh
strathive --db demo.sqlite3 db init

strathive --db demo.sqlite3 account open --name 券商账户 --cash 10000.00 \
  --commission-rate 0.0003 --as-of 2026-11-01T09:00:00

strathive --db demo.sqlite3 strategy create --name 人工策略
strathive --db demo.sqlite3 pool create --account-id 1 --name 主池
strathive --db demo.sqlite3 run create --strategy-id 1 --pool-id 1 --name 首期

strathive --db demo.sqlite3 pool allocate --account-id 1 --pool-id 1 \
  --amount 2000.00 --at 2026-11-02T09:00:00
strathive --db demo.sqlite3 run status --run-id 1 --status active

strathive --db demo.sqlite3 trade add --run-id 1 --security SH.600000 --side buy \
  --quantity 100 --price 10.00 --at 2026-11-02T09:30:00
strathive --db demo.sqlite3 run show 1

strathive --db demo.sqlite3 reconcile --account-id 1 --cash 8994.99 --position SH.600000,100
```

已有账本请使用实际返回的 ID。金额以元为单位，佣金费率用小数填写，`0.0003` 表示 0.03%。未指定时区的时间按北京时间处理。按券商成交回单覆盖费用时，可填写 `--commission`、`--stamp-duty` 或 `--transfer-fee`。

- **更正成交**用 `strathive --db demo.sqlite3 trade edit --trade-id 2 --price 10.10`。需要撤销费用覆盖时，使用更正 JSON。
- **带持仓建账**在 `account open` 增加 `--opening FILE`。
- **导入对账**用 `strathive --db demo.sqlite3 reconcile --account-id 1 --file broker.csv`。使用券商账面现金和持仓总量。仅在确认成本口径一致时填写 `--cost-basis moving_average_v1`。

[期初持仓、更正 JSON 和对账 CSV 示例](skills/strathive-ledger/references/inputs.md)

## 帮助、备份与 Agent

- 用 `strathive trade add --help` 查看各字段的含义。短选项 `-n`、`-q`、`-f` 分别对应 `--name`、`--quantity`、`--file`。
- 用 `strathive db backup --file backup.sqlite3` 备份。用 `strathive --db recovered.sqlite3 db restore --file backup.sqlite3` 恢复到新文件。升级后运行 `strathive db init`。
- 将发布包中的 `skills/strathive-ledger` 复制到项目的 `.agents/skills/strathive-ledger`，并让 Codex 会话可在 `PATH` 中找到 CLI。启动新会话后可使用 `$strathive-ledger`。Skill 通过全局 `--json` 读取机器可读结果。

## 本地开发

运行 `uv sync --locked` 安装依赖，使用 `uv run strathive` 运行 CLI，使用 `uv run pytest` 验证。通过 `scripts/build-linux.sh` 构建发布包，构建使用的 Python 需链接带 WAL 并发修复的 SQLite。

离线证券目录包含来源和获取日期。深交所接口不可用时，深圳条目采用东方财富目录。构建时可运行 `scripts/update-securities.py` 更新目录，也可用 `strathive security import --file securities.csv` 导入已有账本。
