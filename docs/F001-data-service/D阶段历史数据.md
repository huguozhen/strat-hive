# F001 阶段 D：历史数据使用与实施记录

实现分支：`feature/f001-data-service`。本轮以用户确认的 D 阶段计划为准，固定 **CNEquity 0.25.0 / dd6881ad7a3e8fce4ed4314711c99f6251ec06f8**；此前 0.24.0 的候选调研保留作历史记录，不代表本轮安装版本。

## 已交付范围

- 独立 Python 3.14 环境及带哈希的依赖锁；CNEquity、DuckDB、Polars、Arrow、pandas、NumPy 不进入 Core 的 `pyproject.toml` 或 `uv.lock`。
- CNEquity 的 TDX 日线／1 分钟适配、Sina 后复权因子及交易所 ETF 目录，使用该版本随包的 `equitylake_core` 写入嵌入式 DuckLake。目录库为 SQLite，行情为 Parquet；不启动 HTTP 服务或数据库服务器。
- 默认日线覆盖当前沪深股票及交易所目录中的所有 ETF，首次窗口为向前推三个自然年；不把目录不在 TDX 中的 ETF 静默排除。分钟清单默认空，默认回填 60 个实际交易日。支持显式证券与日期。
- 周／月／季度由日线聚合，5／15／30／60 分钟由 1 分钟聚合；仅基础日线和 1 分钟线进行源端同步。
- 单写者锁、按证券／数据集的持久批次、部分缺口、规划失败和中断恢复；20:00 重跑仅处理未完成任务。两个抓取线程，每批最多 100 个证券发布一次快照，以控制初始回填反复重写分区和保留旧修订的成本。
- 未复权历史收盘可进入既有期初／交接取价路由；历史行不复制到报价缓存或账本。银河、历史后端缺失时均可继续使用原有账本命令。
- 不采集财报、新闻、信号、逐笔、盘口或完整历史证券池。未实现收益率曲线、逐日账本回放、总回报或完整回测。

## 安装与使用

历史能力是可选组件。正式发布包用户无需另外安装 Python、uv 或 Git，可在 `strathive setup` 中选择安装，或随时执行：

```bash
strathive data history install
strathive data history status
strathive data history doctor
strathive data history install --repair
strathive data history uninstall
```

安装使用主包自带的 Python 3.14 创建独立环境，按主包内发布清单下载经过 SHA-256 校验的 wheel 制品；CNEquity、依赖和仅有源码发行的依赖均在发布构建时准备好。客户端只安装 wheel，不依赖编译工具、源码构建或 Git。安装过程验证依赖闭包、DuckDB 两个扩展、临时离线存储与严格复权缺口，随后初始化或连接保留的历史库并保存配置；不采真实行情、不启动定时器。

失败时清理本次候选环境、保留当前配置与原环境，可重复执行安装。升级／修复使用新的版本目录，检查通过后切换；原环境保留供恢复。卸载停止并移除历史任务、删除组件管理的环境和下载缓存、解除收盘与日历路由，保留行情数据、配置、分钟清单和账本；外部 Python 环境、主包 Python 及共享扩展缓存不删除。重新安装会复用历史库。

主包只携带组件清单，独立的 `strathive-history-*.tar.gz` 随同版本 Release 分发，压缩制品的体积不等于环境安装后的占用。离线安装使用 `--bundle FILE`，但首次准备 DuckDB 扩展仍需网络或已有缓存。未发布构建的默认清单没有可下载制品，会返回 `history_artifact_unavailable`；源码开发者可构建并显式提供清单：

```bash
uv run --locked python scripts/history/build.py
strathive data history install --manifest dist/history-component.json --bundle dist/strathive-history-实际文件名.tar.gz
```

制品构建需要 uv／Git 与网络，发布 CI 运行该步骤，并把制品 SHA-256／下载地址嵌入 `.deb`；这些是构建环境依赖。`--source` 可指定固定提交的干净 CNEquity checkout，`--wheel-source` 可使用预下载依赖进行构建。Core 的 `pyproject.toml` 和 `uv.lock` 保持轻量。主包升级导致 Python 补丁版本变化时，执行安装／修复会准备并检查匹配的新环境，避免在运行中直接升级旧环境。

显式小样本与资源记录（独立新库，不混入离线合成夹具）：

```bash
uv run --locked python scripts/history/sample.py \
  --python ~/.local/share/strathive/history-env/bin/python \
  --root /tmp/strathive-history-live \
  --report /tmp/strathive-history-live-report.json
```

日常查询与同步不需要 Docker、Deno 或 Node.js：

```bash
# 三年日线及因子：省略证券表示当前全市场；首次执行前先用样本校准资源。
strathive data sync --period 1d

# 分钟清单只能显式设置；不设置就不采全市场分钟。
strathive data history configure --minute-security SH.510300 --minute-security SZ.159915 --minute-days 60
strathive data sync --period 1m
strathive data history clear-minute-list

# 显式手动范围，独立于默认清单。
strathive data sync --security SH.600519 --security SZ.159915 \
  --period all --start 2026-11-09 --end 2026-11-23
strathive data sync --retry-only

strathive data bars --security SH.510300 --start 2026-11-09 --end 2026-11-23 --period 1d
strathive data bars --security SH.510300 --start 2026-11-09 --end 2026-11-23 \
  --period 60m --adjustment qfq --output /tmp/510300-60m.jsonl
strathive data history status
strathive data history inspect
strathive data history maintain
```

`maintain` 仅显式合并小文件，不自动过期快照、删除旧修订或按日期淘汰行情。清空分钟清单也保留已采集的数据。

托管环境位于 XDG 数据目录的 `strathive/components/history/environments/`，下载缓存单独保存。原来手工配置的 Python 仍可通过高级 configure 命令使用。配置存放于既有 XDG `settings.json` 的独立 `history` 项：`enabled`、`root`、`python`、`minute_symbols`、`minute_days`、`memory_limit`（默认 2GB）、`threads`（2）、`fetch_workers`（2）、`write_batch_size`（100）、`warn_gb`（20）、`pause_gb`（10）、`timeout_seconds`（21600）。路径与证券标识在入口规范化，解释器保留 venv 符号链接路径。少于 10GB 剩余空间时暂停新增历史写入，状态和查询仍可使用。HTTP 来源使用系统受信任 CA 验证 TLS，可使用标准 `SSL_CERT_FILE`，没有关闭证书校验。

## 数据与查询约定

每根基础 K 线输出 `symbol`（如 `SH.510300`）、`session_date`、`interval_start`／`interval_end`、OHLC、`volume`、`amount`，并携带 `source`、`retrieved_at`、`data_version`、`revision`、`adjustment`、币种及单位。分钟区间使用带 `+08:00` 的北京时间，原始分钟时间戳是区间结束时间：09:31–11:30、13:01–15:00，共 240 根；日线区间是交易日。

- 价和额按十进制文本保存和输出，十进制运算不引入新的二进制浮点运算，也不固定舍入到两位。CNEquity 源适配已经使用 Float64，保存的是其返回值可用的十进制表示；无法恢复上游未保留的原始文本精度。
- CNEquity 已将 TDX 日线“手”转换为股，本层不会重复乘 100。分钟量沿用源端股数；ETF 量语义为份（`volume_unit=units`），股票为股（`shares`）。`amount` 是源端人民币成交额，不用收盘价乘成交量替代。ETF 价格是市场成交价，不引入净值或 IOPV。
- 原始价格始终未复权。研究查询支持 `none`／`qfq`／`hfq`；复权先作用到每根基础 K 线，再进行聚合。量和成交额不复权。
- 仅保存累计后复权因子；前复权归一到同一个快照中该证券本地最新因子日。结果包含因子锚点日期、因子来源及快照。缺任何请求行的因子时，该证券的调整后行为空，`missing_factors` 列出缺口，`complete=false`，不会回退到原价或假定因子为 1。
- 查询固定整次读取的 DuckLake 快照，原始和因子不会读到不同发布时刻。旧快照／数据文件默认累计保留。
- 分钟桶不跨午休，缺分钟不造行，缺行或未结束的周期标记 `complete=false`。周／月／季度以自然周期边界和实际交易日判断完整性，只请求半周也会标记未完整。
- 覆盖按请求中的证券、交易日及分钟键核对。缺行不自动解释为停牌；已经确认的上市日前日期标为无需覆盖，未知上市日、停牌或源端不支持造成的缺行仍明确显示。基金目录只说明当前证券身份，不构建历史证券池。
- 源端分钟窗口约 95 个交易日是能力说明；更长请求会采可达数据并报告未覆盖日期，已有本地旧数据继续参与覆盖与查询。三年／60 日均为补齐窗口，不是查询或保留上限。
- 返回不超过 10000 根可直接打印；超过时返回 `history_export_required`，须用 `--output`。JSONL／CSV 逐证券流式导出全部行，另附 `.manifest.json` 记录行数、请求、快照和缺口。导出不覆盖已有路径，中途失败不留下部分结果文件。

## 调度与恢复

```bash
# 在完成样本、配置实际范围并初始化历史库之后启用。
strathive data history schedule
systemctl --user list-timers 'strathive-history-*'
```

三个用户 timer 分别在北京时间 18:00（日线＋因子）、18:15（分钟清单＋因子）、20:00（仅失败补跑）调用同一 `data sync` 命令。服务使用当前安装的解释器／CLI 和 XDG 路径；`Persistent=true`，补唤醒按最后一个已完成交易日补齐，因此机器错过日期后仍可恢复。这些时刻不代表源端保证出数；显式请求当天盘中批量历史会被拒绝。没有安装或启动通用调度服务。

`status` 在没有安装后端时仍可读取配置、空间、各状态批次数、待重试范围和未完成规划数。每批先记录 running，成功发布后核对覆盖再改为 complete／partial；异常记录 failed。若进程在参考资料准备或提交前后中断，`--retry-only` 可恢复规划或重新核对已写键，不靠命令退出码冒充覆盖完成。20:00 不会重新抓已经完成的常规回看窗口。

## 验证与资源记录

普通 PR 运行 `uv run --locked pytest -q`，不安装历史重依赖，不采集真实全市场。独立存储验证使用：

```bash
PYTHONPATH=src /path/to/history-env/bin/python scripts/history/verify.py
```

该脚本使用临时、明确标记为 `offline_fixture` 的合成数据，实际走 CNEquity 0.25.0 的目录／Parquet 写读、幂等、保留修订、严格复权、聚合、导出及 TDX 量单位换算。其结果与真实采集记录分开。

实际安装、在线四证券样本、目录范围和资源测量见 [D 阶段验证记录](D阶段验证记录.md)。全市场三年生产回填没有在普通测试中启动；清洁环境、Docker 安装和正式 release 仍在发布前执行。
