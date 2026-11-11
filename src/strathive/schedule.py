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


def enable():
    directory = user_config_path("systemd") / "user"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        for extension in ("service", "timer"):
            filename = f"{TASK}.{extension}"
            content = (RESOURCES / filename).read_text(encoding="utf-8")
            if extension == "service":
                content = content.replace("@COMMAND@", " ".join(systemd_quote(arg, command_arg=True) for arg in command()))
                for name in XDG:
                    if name in os.environ:
                        content += f"Environment={systemd_quote(f'{name}={os.environ[name]}')}\n"
            (directory / filename).write_text(content, encoding="utf-8")
        for args in (["daemon-reload"], ["enable", "--now", f"{TASK}.timer"]):
            subprocess.run(["systemctl", "--user", *args], capture_output=True, check=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def cron_command():
    variables = [f"{key}={shlex.quote(os.environ[key])}" for key in XDG if key in os.environ]
    invocation = shlex.join([*command(), "security", "sync", "--if-stale"])
    # Ubuntu cron 在系统时区触发，使用 UTC 时可直接采用此时刻。
    return " ".join(["15 1 * * *", *variables, invocation]).replace("%", "\\%")
