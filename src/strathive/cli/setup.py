"""安装后的交互向导，选择账本、数据源与每日同步。"""

from importlib.metadata import version
from pathlib import Path

import click
import typer
from sqlalchemy.exc import SQLAlchemyError

from strathive import schedule, settings
from strathive.adapters.sqlite import SQLiteStore
from strathive.core.rules import LedgerError
from strathive.sources import sdk
from strathive.sources.sync import SOURCE_NAMES, request, synchronize

DEFAULT_HOST = "140.206.44.234"
DEFAULT_PORT = 8600
SKILL_NAME = "strathive-ledger"
SKILL_REPO = "https://github.com/huguozhen/strat-hive"


def choose_ledger(catalog):
    default = settings.ledger_path()
    while True:
        choice = typer.prompt("账本  1 打开已有文件 / 2 新建", type=click.IntRange(1, 2),
                              default=1 if default.is_file() else 2)
        if choice == 1:
            path = Path(typer.prompt("已有账本文件", default=str(default))).expanduser()
        else:
            directory = Path(typer.prompt("新建账本的存储目录", default=str(settings.data_dir()))).expanduser()
            path = directory / "ledger.sqlite3"
            if path.is_file():
                if not typer.confirm(f"{path} 已存在，打开这个账本？", default=True):
                    continue
                choice = 1
        try:
            result = SQLiteStore(path, catalog).initialize(existing=choice == 1)
        except (LedgerError, OSError, SQLAlchemyError) as exc:
            typer.echo(f"无法打开或创建账本。{exc}", err=True)
            continue
        typer.echo(f"账本文件  {result['path']}")
        if result.get("migration_backup"):
            typer.echo(f"升级前的备份  {result['migration_backup']}")
        typer.echo("账本保存账户、策略运行和成交等业务记录。请定期将账本备份到另一块磁盘或其他存储介质。")
        typer.echo("使用 strathive db backup --file <备份文件路径> 生成备份。")
        return result["path"]


def install_sdk():
    manual = None
    while True:
        try:
            typer.echo("正在准备银河 SDK，首次下载安装可能需要几分钟。")
            sdk.install(manual=manual)
            return True
        except LedgerError as exc:
            typer.echo(str(exc), err=True)
            typer.echo(f"可在 {sdk.RELEASE} 的 v1.0.0 下载两个指定的 wheel 到同一目录。")
            choice = typer.prompt("1 重试自动下载 / 2 使用已下载的文件 / 3 返回",
                                  type=click.IntRange(1, 3), default=2)
            if choice == 3:
                return False
            manual = Path(typer.prompt("wheel 所在目录")).expanduser() if choice == 2 else None


def galaxy_config(previous):
    typer.echo("请联系中国银河证券客户经理申请星耀数智账号和密码。")
    if not install_sdk():
        return None
    old = previous.get("connection") or {}
    host, port = old.get("host", DEFAULT_HOST), old.get("port", DEFAULT_PORT)
    while True:
        if old and typer.confirm("沿用已保存的银河账号和密码？", default=True):
            username, password = old["username"], old["password"]
        else:
            username = typer.prompt("银河账号")
            password = typer.prompt("银河密码", hide_input=True)
        old = None
        while True:
            connection = {"host": host, "port": port, "username": username, "password": password}
            try:
                typer.echo("正在验证银河连接和证券数据权限。")
                request("galaxy", "check", connection, python=sdk.sdk_python(), timeout=80)
                return {"source": "galaxy", "connection": connection}
            except LedgerError as exc:
                typer.echo(str(exc), err=True)
                if exc.code == "endpoint_unreachable" and typer.confirm("指定其他服务器地址？", default=True):
                    host = typer.prompt("服务器 IP 或主机名", default=host)
                    port = typer.prompt("服务器端口", default=port, type=click.IntRange(1, 65535))
                    continue
                if exc.code == "login_failed" and typer.confirm("重新输入银河账号和密码？", default=True):
                    break
                return None


def show_agent_setup():
    """用已安装的发行版版本生成与 CLI 匹配的 Skill 链接。"""
    tag = f"v{version('strathive')}"
    url = f"{SKILL_REPO}/tree/{tag}/skills/{SKILL_NAME}"
    local_path = f"/usr/share/strathive/skills/{SKILL_NAME}"
    typer.echo("\n将下面这段提示词复制给本机常用的 Agent（如 Hermes、OpenClaw、Codex 或 WorkBuddy）。\n")
    typer.echo(f"我已经安装并配置了 StratHive {tag}。请按你自己的 Skill 安装方式，将 {SKILL_NAME} 安装到本机。\n"
               f"请使用与当前版本匹配的目录 {url}\n"
               f"如果 GitHub 目录暂时无法访问，请使用本机安装包附带的目录 {local_path}\n"
               "请保留完整目录结构，包括 SKILL.md 和 references。安装后确认你能发现这个 Skill，"
               "运行 `strathive --help` 验证命令可用，并告诉我如何调用它。")


def run(catalog):
    """切换数据源前先完成整次同步，失败时保留原先的来源。"""
    with catalog.refresh_lock():
        values = settings.settings()
        values["ledger"] = choose_ledger(catalog)
        settings.save(values)
        while True:
            choice = typer.prompt("数据源  1 银河星耀数智（推荐，需账号） / 2 公开证券目录 / 3 退出",
                                  default=1 if values.get("source") != "public" else 2, type=click.IntRange(1, 3))
            if choice == 3:
                if not values.get("source"):
                    typer.echo("账本已选定。可稍后运行 strathive setup 配置数据源。")
                return
            config = galaxy_config(values) if choice == 1 else {"source": "public"}
            if config is None:
                continue
            try:
                typer.echo(f"正在同步{SOURCE_NAMES[config['source']]}。")
                result = synchronize(catalog, config)
            except LedgerError as exc:
                typer.echo(str(exc), err=True)
                continue
            settings.save({"ledger": values["ledger"], **config})
            typer.echo(f"同步完成，共更新 {result['count']} 条证券记录。")
            if result["conflicts"]:
                typer.echo(f"保留了 {len(result['conflicts'])} 条品种冲突记录的原值。")
            break
    if schedule.enable():
        typer.echo("已启用每日同步，北京时间 09:15 运行。")
    else:
        typer.echo("当前无法启动 systemd 用户任务。重新登录后可运行 "
                   "systemctl --user enable --now strathive-security-sync.timer。", err=True)
        typer.echo("也可配置 cron。以下示例适用于系统时区为 UTC 的机器：", err=True)
        typer.echo(schedule.cron_command(), err=True)
    show_agent_setup()
