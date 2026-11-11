"""安装固定版本的银河 SDK，并在启用前验证原生库。"""

import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path
from time import monotonic
from urllib.request import urlopen

from strathive.core.rules import LedgerError, require
from strathive.settings import data_dir

RELEASE = "https://gitee.com/cgs2026/xysz/releases"
DOWNLOAD = f"{RELEASE}/download/v1.0.0"
WHEELS = {
    "AmazingData-1.1.18-cp314-none-any.whl": "657fef139291697db879d583f95f4f169d4e020b9c349e1af291de43cfb9346a",
    "tgw-1.0.9.2-py3-none-any.whl": "cbc30194e2d3923c87e5d40ce469b79575758001f9d5f7481d46c29c9667e21d",
}
# 供应商元数据未列出 requests，实测 AmazingData 导入时需要。
EXTRA_DEPENDENCIES = ("requests==2.34.2",)


def sdk_dir():
    identity = repr((sys.version_info[:3], sys.base_prefix, WHEELS, EXTRA_DEPENDENCIES))
    version = hashlib.sha256(identity.encode()).hexdigest()[:16]
    return data_dir() / "sdk" / version


def sdk_python():
    python = sdk_dir() / "bin" / "python"
    require(python.is_file(), "银河 SDK 尚未安装，请运行 strathive setup。", "sdk_unavailable")
    return python


def download(filename, destination):
    deadline = monotonic() + 110
    with urlopen(f"{DOWNLOAD}/{filename}", timeout=15) as response, destination.open("wb") as stream:
        total = 0
        while chunk := response.read(256 * 1024):
            total += len(chunk)
            require(total <= 100 * 1024 * 1024 and monotonic() < deadline,
                    f"下载 {filename} 超时或文件大小异常。", "sdk_download_failed")
            stream.write(chunk)


def wheel_files(directory, manual):
    paths = []
    for filename, expected in WHEELS.items():
        path = (manual or directory) / filename
        try:
            if manual is None:
                download(filename, path)
            with path.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError as exc:
            message = "下载失败" if manual is None else "无法读取手动下载文件"
            raise LedgerError("sdk_download_failed", f"{filename} {message}。") from exc
        require(actual == expected, f"{filename} 的 SHA-256 校验失败。", "sdk_checksum_failed")
        paths.append(path.resolve())
    return paths


def install(*, manual=None):
    from .sync import request

    destination = sdk_dir()
    if destination.exists() and manual is None:
        try:
            request("galaxy", "load", python=sdk_python(), timeout=30)
            return
        except LedgerError:
            pass
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".sdk-", dir=destination.parent) as work:
        directory = Path(work)
        wheels = wheel_files(directory, manual)
        environment = directory / "environment"
        python = environment / "bin" / "python"
        try:
            subprocess.run([sys.executable, "-E", "-m", "venv", str(environment)],
                           capture_output=True, check=True, timeout=60)
            subprocess.run([str(python), "-E", "-m", "pip", "--isolated", "--disable-pip-version-check",
                            "install", "--progress-bar", "off", "--only-binary=:all:", "--timeout", "20", "--retries", "2",
                            *(str(wheel) for wheel in wheels), *EXTRA_DEPENDENCIES],
                           capture_output=True, check=True, timeout=420)
        except (OSError, subprocess.SubprocessError) as exc:
            raise LedgerError("sdk_install_failed", "银河 SDK 依赖安装失败，请检查网络或运行环境。") from exc
        request("galaxy", "load", python=python, timeout=30)
        previous = directory / "previous"
        if destination.exists():
            destination.rename(previous)
        try:
            environment.rename(destination)
        except OSError:
            if previous.exists():
                previous.rename(destination)
            raise
