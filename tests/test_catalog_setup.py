"""验证可重建证券数据、账本恢复和安装向导的边界。"""

from importlib import import_module
from pathlib import Path

import pytest
from sqlalchemy import table, column
from typer.testing import CliRunner

from strathive import schedule, settings
from strathive.adapters.files import catalog_file
from strathive.adapters.schema import instruments
from strathive.bootstrap import build
from strathive.cli import app
from strathive.core.rules import LedgerError
from strathive.sources import sdk, sync

from test_ledger import CATALOG, CUTOFF, FIRST, allocation, holdings


def test_split_legacy_catalog_and_restore_without_cache(tmp_path, monkeypatch):
    ledger, store = build(tmp_path / "old.db")
    store.initialize()
    assert not store.catalog.path.exists()
    store.catalog.prepare(CATALOG)
    strategy = ledger.create_strategy("版本")
    ledger.open_account("账户", "1000", "0.0003", CUTOFF, holdings(), [allocation(strategy, "原运行", 100, "10")])
    ledger.record_trade(1, "SH.600000", "sell", 10, "10", FIRST, request_id="legacy-sell")

    # 模拟 0002 结构及未被业务事实引用的旧目录条目。
    revision = table("alembic_version", column("version_num"))
    with store.engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE opening_position DROP COLUMN valuation_meta")
        connection.exec_driver_sql("ALTER TABLE account DROP COLUMN etf_commission_rate")
        connection.execute(revision.update().values(version_num="0002"))
        connection.execute(instruments.insert(), [CATALOG[1]])
    catalog_file_path = ledger.store.catalog.path
    catalog_file_path.unlink()

    result = store.initialize(existing=True)
    assert Path(result["migration_backup"]).is_file()
    with store.transaction() as tx:
        assert [row["symbol"] for row in tx.find("instrument")] == ["SH.600000"]
    assert ledger.instrument("SZ.159915")["kind"] == "etf"
    assert store.catalog.update([{**CATALOG[0], "active": False, "name": "目录新名"}])["count"] == 1
    assert ledger.instrument("SH.600000")["active"] is False
    assert ledger.trade(1)["security_name"] == CATALOG[0]["name"]

    backup = tmp_path / "backup.sqlite3"
    store.backup(backup)
    restored, restored_store = build(tmp_path / "restored.sqlite3")
    restored_store.restore(backup)
    catalog_file_path.unlink()

    def fail_if_opened(**_):
        raise AssertionError("账本历史应可脱离目录读取")

    monkeypatch.setattr(restored_store.catalog, "find", fail_if_opened)
    assert restored.accounts() == ledger.accounts()
    assert restored.trades(security="SH.600000") == ledger.trades()
    assert restored.trades(security="600000") == ledger.trades()
    assert restored.trades(security=CATALOG[0]["full_name"]) == ledger.trades()
    assert restored.trade(1)["security_name"] == CATALOG[0]["name"]
    assert restored.trades()[0]["quantity"] == 10
    assert not catalog_file_path.exists()
    assert ledger.instrument("SZ.159915")["kind"] == "etf"
    assert restored.accounts() == ledger.accounts()


def test_refresh_validates_full_result_and_keeps_last_success(monkeypatch, tmp_path):
    ledger, store = build(tmp_path / "not-created.sqlite3")
    catalog = store.catalog
    config = {"source": "public"}
    rows = catalog_file()
    monkeypatch.setattr(sync, "request", lambda *_a, **_kw: rows)
    with catalog.refresh_lock():
        first = sync.synchronize(catalog, config)
        with pytest.raises(LedgerError, match="refresh in progress"):
            with catalog.refresh_lock():
                pass
    assert first["count"] == len(rows)
    assert not store.path.exists()
    assert ledger.instrument("SH.510300")["kind"] == "etf"

    monkeypatch.setattr(sync, "request", lambda *_a, **_kw: rows[:100])
    with catalog.refresh_lock():
        assert sync.synchronize(catalog, config, if_stale=True)["skipped"]
        with pytest.raises(LedgerError, match="规模异常"):
            sync.synchronize(catalog, config)
    status = catalog.status("public")
    assert status["succeeded_at"] == first["succeeded_at"]
    assert status["count"] == first["count"]
    assert "不完整" in status["error"]
    assert catalog.find(symbol="SH.510300")[0]["kind"] == "etf"


def test_setup_creates_custom_ledger_and_reopens_it(monkeypatch, tmp_path):
    wizard = import_module("strathive.cli.setup")
    monkeypatch.setattr(wizard, "version", lambda _: "9.8.7")
    monkeypatch.setattr(sync, "request", lambda *_a, **_kw: catalog_file())
    monkeypatch.setattr(schedule, "enable", lambda: True)
    runner = CliRunner()
    directory = tmp_path / "my ledger"
    result = runner.invoke(app, ["setup"], input=f"2\n{directory}\n2\n")
    assert result.exit_code == 0, result.output
    assert "其他存储介质" in result.output and "09:15" in result.output
    assert "https://github.com/huguozhen/strat-hive/tree/v9.8.7/skills/strathive-ledger" in result.output
    assert "/usr/share/strathive/skills/strathive-ledger" in result.output
    path = directory / "ledger.sqlite3"
    assert settings.ledger_path() == path
    assert settings.settings()["source"] == "public"
    assert (settings.config_dir() / "settings.json").stat().st_mode & 0o777 == 0o600
    before = path.stat().st_ino
    result = runner.invoke(app, ["setup"], input="\n\n3\n")
    assert result.exit_code == 0, result.output
    assert "strathive-ledger" not in result.output
    assert path.stat().st_ino == before


def test_galaxy_setup_recovers_from_download_and_endpoint_errors(monkeypatch, tmp_path):
    wizard = import_module("strathive.cli.setup")
    attempts = []

    def install(*, manual):
        attempts.append(manual)
        if manual is None:
            raise LedgerError("sdk_download_failed", "下载失败")

    def check(_source, _action, connection, **_kwargs):
        if connection["host"] == wizard.DEFAULT_HOST:
            raise LedgerError("endpoint_unreachable", "无法连接")
        assert connection["host"] == "192.0.2.8"
        assert connection["port"] == 8601

    monkeypatch.setattr(sdk, "install", install)
    monkeypatch.setattr(sdk, "sdk_python", lambda: Path("/test/sdk/python"))
    monkeypatch.setattr(wizard, "request", check)
    monkeypatch.setattr(sync, "request", lambda *_a, **_kw: catalog_file())
    monkeypatch.setattr(schedule, "enable", lambda: True)
    result = CliRunner().invoke(app, ["setup"], input=(
        "\n\n\n2\n" + str(tmp_path) + "\ndemo\nsecret-credential\ny\n192.0.2.8\n8601\n"
    ))
    assert result.exit_code == 0, result.output
    assert attempts == [None, tmp_path]
    assert "secret-credential" not in result.output
    assert settings.settings()["connection"]["host"] == "192.0.2.8"
    assert settings.ledger_path() == settings.data_dir() / "ledger.sqlite3"
