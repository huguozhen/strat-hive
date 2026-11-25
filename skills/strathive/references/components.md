# 可选组件

历史数据是 StratHive 的可选组件。先运行 `strathive data history status`，查看安装状态、是否启用、数据目录和是否由 StratHive 管理。账本、成交记录和已有估值不依赖它。

## 安装与修复

用户要求安装或启用历史能力时，执行 `strathive data history install`。命令复用当前 StratHive 的 Python，在用户数据目录下载并校验固定版本的 wheel 制品，再建立独立环境、检查扩展和临时离线存储样本、初始化历史库并保存配置。按命令退出码和 JSON 结果判断是否成功；进度在 stderr。

安装不启动全市场同步或定时器。用户还要求获取行情时，再按 [行情范围](market.md) 执行同步。仅安装完成不能说明某证券已有行情或覆盖完整。

安装中断或下载失败时可重新执行同一命令；原环境和配置保持可用。已安装但环境损坏时用 `data history install --repair`。离线安装可使用与发布包清单匹配的 `--bundle FILE`；源码构建生成的制品同时传入 `--manifest FILE`。校验失败时报告错误，不跳过哈希或版本检查。

若返回 `history_artifact_unavailable`，说明当前构建没有发布制品清单。使用包含清单的正式包，或用项目构建流程生成 bundle 和 manifest；不通过安装任意最新版 CNEquity 绕过版本约束。

## 卸载与重新安装

用户要求卸载时执行 `strathive data history uninstall`。命令停止并移除历史定时任务，移除 StratHive 管理的组件环境和下载缓存，解除历史收盘／日历路由；保留历史数据、分钟清单、独立配置及投资账本。共享 DuckDB 扩展缓存和用户自行配置的 Python 环境也保留。

遇到正在运行的历史任务或无法停止 systemd 服务时，报告具体错误；先结束相关任务再重试。卸载命令不会删除行情数据，删除行情属于单独的用户请求，不用通配符清理用户目录。

重新执行 `install` 会连接保留的历史库。用 `status` 和 `doctor` 核对状态；历史数据仍需按查询覆盖结果判断可用性。管理定时更新使用用户明确要求的 `data history schedule`，与组件安装分开。
