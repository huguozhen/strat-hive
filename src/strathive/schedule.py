"""安装当前用户的证券目录刷新任务。"""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from platformdirs import user_config_path

TASK = "strathive-security-sync"
RESOURCES = Path(__file__).with_name("resources")
XDG = ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME")


def command():
    if Path(sys.base_prefix) == Path("/opt/strathive/python"):
        return ["/usr/bin/strathive"]
    return [sys.executable, "-I", "-m", "strathive"]


def systemd_quote(value, *, command_arg=False):
    value = value.replace("%", "%%")
    if command_arg:
        value = value.replace("$", "$$")
    return json.dumps(value, ensure_ascii=False)


def enable(task=TASK):
    directory = user_config_path("systemd") / "user"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        for extension in ("service", "timer"):
            filename = f"{task}.{extension}"
            content = (RESOURCES / filename).read_text(encoding="utf-8")
            if extension == "service":
                content = content.replace("@COMMAND@", " ".join(systemd_quote(arg, command_arg=True) for arg in command()))
                for name in XDG:
                    if name in os.environ:
                        content += f"Environment={systemd_quote(f'{name}={os.environ[name]}')}\n"
            (directory / filename).write_text(content, encoding="utf-8")
        for args in (["daemon-reload"], ["enable", "--now", f"{task}.timer"]):
            subprocess.run(["systemctl", "--user", *args], capture_output=True, check=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def cron_command():
    variables = [f"{key}={shlex.quote(os.environ[key])}" for key in XDG if key in os.environ]
    invocation = shlex.join([*command(), "security", "sync", "--if-stale"])
    # Ubuntu cron 在系统时区触发，使用 UTC 时可直接采用此时刻。
    return " ".join(["15 1 * * *", *variables, invocation]).replace("%", "\\%")


def disable_history():
    """Stop only our history units before removing an optional environment."""
    from strathive.core.rules import LedgerError
    directory = user_config_path("systemd") / "user"
    tasks = [f"strathive-history-{name}" for name in ("daily", "minute", "retry")]
    existing = [task for task in tasks if any((directory / f"{task}.{ext}").exists() for ext in ("service", "timer"))]
    if not existing:
        return
    try:
        timers = [f"{task}.timer" for task in existing]
        services = [f"{task}.service" for task in existing]
        for arguments in (["disable", "--now", *timers], ["stop", *services]):
            subprocess.run(["systemctl", "--user", *arguments], capture_output=True, check=True, timeout=30)
        for task in existing:
            for extension in ("service", "timer"):
                (directory / f"{task}.{extension}").unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, check=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        raise LedgerError("history_schedule_stop_failed", "无法停止历史任务，组件环境已保留；请检查 systemd 用户会话后重试。") from exc
