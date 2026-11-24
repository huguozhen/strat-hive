"""证券同步编排，联网期间保持上次成功的目录可用。"""

import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from strathive.core.rules import SHANGHAI, LedgerError, require

SOURCE_NAMES = {"galaxy": "银河星耀数智", "public": "公开证券目录"}
WORKER = Path(__file__).with_name("worker.py")


def request(source, action, connection=None, *, python=None, timeout=115, previous_stocks=(), symbols=(), session_date=None):
    task = {"source": source, "action": action, "connection": connection, "previous_stocks": previous_stocks, "symbols": symbols, "session_date": session_date}
    with tempfile.TemporaryDirectory(prefix="strathive-source-") as directory:
        result = Path(directory) / "result.json"
        try:
            subprocess.run([str(python or sys.executable), "-E", "-s", str(WORKER), str(result)],
                           input=json.dumps(task), text=True, timeout=timeout, cwd=directory,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            response = json.loads(result.read_text(encoding="utf-8"))
        except subprocess.TimeoutExpired as exc:
            raise LedgerError("source_timeout", "数据源连接或读取超时，请稍后重试。") from exc
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            raise LedgerError("source_failed", "数据源进程执行失败，请检查 SDK 和运行环境。") from exc
    if "error" in response:
        raise LedgerError(response["code"], response["error"])
    return response["data"]


def validate_catalog(rows):
    """拒绝重复、格式错误或明显缺少任一交易所和品种的响应。"""
    groups = Counter()
    symbols = set()
    for row in rows:
        market, code, kind = row["market"], row["code"], row["kind"]
        symbol = f"{market}.{code}"
        require(row["symbol"] == symbol and market in {"SH", "SZ"} and re.fullmatch(r"[0-9]{6}", code)
                and kind in {"stock", "etf"} and bool(row["name"].strip())
                and isinstance(row.get("active", True), bool), "证券目录包含无效条目。", "invalid_catalog")
        require(symbol not in symbols, f"证券目录包含重复代码 {symbol}。", "invalid_catalog")
        symbols.add(symbol)
        if row.get("active", True):
            groups[market, kind] += 1
    require(all(groups[market, kind] >= minimum for market in ("SH", "SZ")
                for kind, minimum in (("stock", 1200), ("etf", 350))),
            "证券目录规模异常，至少一个交易所或品种的结果不完整。", "invalid_catalog")


def synchronize(catalog, config, *, if_stale=False):
    """调用方持有刷新锁，成功状态与新目录在同一事务提交。"""
    source = (config.get("selection", {}).get("reference") or [config.get("source")])[0]
    config = {**config, "connection": config.get("providers", {}).get(source, {}).get("connection", config.get("connection"))}
    require(source in SOURCE_NAMES, "请先运行 strathive setup 配置数据源。", "source_not_configured")
    now = datetime.now(timezone.utc)
    status = catalog.status(source)
    last_success = status["succeeded_at"]
    if if_stale and last_success:
        last_day = datetime.fromisoformat(last_success).astimezone(SHANGHAI).date()
        if last_day == now.astimezone(SHANGHAI).date():
            return {"skipped": True, **status}
    try:
        if source == "galaxy":
            from .sdk import sdk_python

            rows = request(source, "fetch", config["connection"], python=sdk_python(),
                           previous_stocks=catalog.find(kind="stock", active=True))
        else:
            rows = request(source, "fetch")
        validate_catalog(rows)
    except LedgerError as exc:
        catalog.failure(source, now.isoformat(), str(exc))
        raise
    result = catalog.refresh(source, rows, now.isoformat())
    return {**catalog.status(source), **result, "skipped": False}
