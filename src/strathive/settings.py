"""用户配置与账本、可重建数据的存放位置。"""

import json
import os
import tempfile
from pathlib import Path

from platformdirs import user_cache_path, user_config_path, user_data_path

APP = "strathive"


def data_dir():
    return user_data_path(APP)


def config_dir():
    return user_config_path(APP)


def catalog_path():
    return user_cache_path(APP) / "reference" / "securities.sqlite3"


def settings():
    try:
        return json.loads((config_dir() / "settings.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def ledger_path():
    return Path(settings().get("ledger", data_dir() / "ledger.sqlite3"))


def save(values):
    """原子替换用户配置，凭据从创建文件时起只允许当前用户读取。"""
    directory = config_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".settings-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(values, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / "settings.json")
    finally:
        Path(temporary).unlink(missing_ok=True)


def market_path():
    return user_cache_path(APP) / "market" / "prices.sqlite3"
