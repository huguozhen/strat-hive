"""SQLite persistence and maintenance for the local ledger."""

import fcntl
import os
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path
from time import time_ns

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, delete, insert, inspect, select, update
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError

from strathive.core.replay import rebuild
from strathive.core.rules import LedgerError, require

from .schema import instruments, metadata, opening, trades


class Transaction:
    def __init__(self, connection):
        self.connection = connection

    def find(self, entity, **filters):
        table = metadata.tables[entity]
        query = select(table)
        for key, value in filters.items():
            column = table.c[key]
            query = query.where(column.in_(value) if isinstance(value, list) else column == value)
        return [dict(row) for row in self.connection.execute(query.order_by(*table.primary_key)).mappings()]

    def get(self, entity, identifier):
        column, = metadata.tables[entity].primary_key.columns
        rows = self.find(entity, **{column.name: identifier})
        require(bool(rows), f"{entity} {identifier} does not exist", "not_found")
        return rows[0]

    def add(self, entity, **values):
        table = metadata.tables[entity]
        return dict(self.connection.execute(insert(table).values(**values).returning(table)).mappings().one())

    def update(self, entity, identifier, **values):
        if not values:
            return self.get(entity, identifier)
        table = metadata.tables[entity]
        row = self.connection.execute(update(table).where(table.c.id == identifier)
                                      .values(**values).returning(table)).mappings().first()
        require(row is not None, f"{entity} {identifier} does not exist", "not_found")
        return dict(row)

    def remove(self, entity, identifier):
        table = metadata.tables[entity]
        self.connection.execute(delete(table).where(table.c.id == identifier))

    def remove_unused_instrument(self, symbol):
        self.connection.execute(delete(instruments).where(
            instruments.c.symbol == symbol,
            ~select(trades.c.symbol).where(trades.c.symbol == symbol).exists(),
            ~select(opening.c.symbol).where(opening.c.symbol == symbol).exists(),
        ))


class SQLiteStore:
    def __init__(self, path, catalog):
        self.path = Path(path).expanduser().resolve()
        self.catalog = catalog
        self.engine = create_engine(URL.create("sqlite", database=str(self.path)),
                                    connect_args={"timeout": 5})
        self.migrations = Config()
        self.migrations.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        scripts = ScriptDirectory.from_config(self.migrations)
        self.head = scripts.get_current_head()
        self.revisions = {item.revision for item in scripts.walk_revisions()}

    @contextmanager
    def lock(self, *, exclusive=False):
        with self.path.with_name(self.path.name + ".lock").open("a+b") as lock:
            try:
                mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
                fcntl.flock(lock, mode | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise LedgerError("busy", "ledger maintenance in progress; retry") from exc
            yield

    @staticmethod
    def check_sqlite():
        version = sqlite3.sqlite_version_info
        require(version >= (3, 51, 3) or version in {(3, 44, 6), (3, 50, 7)},
                f"SQLite {sqlite3.sqlite_version} lacks the required WAL fix", "unsupported_sqlite")

    @staticmethod
    def configure(connection):
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql("PRAGMA synchronous=FULL")
        connection.commit()

    @contextmanager
    def transaction(self, *, write=False):
        require(self.path.is_file(), "initialize the ledger with db init", "not_initialized")
        self.check_sqlite()
        try:
            with self.lock(), self.engine.connect() as connection:
                self.configure(connection)
                connection.exec_driver_sql("BEGIN IMMEDIATE" if write else "BEGIN")
                require(MigrationContext.configure(connection).get_current_revision() == self.head,
                        "upgrade the ledger with db init", "migration_required")
                yield Transaction(connection)
                connection.commit()
        except OperationalError as exc:
            if getattr(exc.orig, "sqlite_errorcode", 0) & 255 in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise LedgerError("busy", "ledger is busy; retry") from exc
            raise

    def initialize(self, *, existing=None):
        """创建或升级账本，旧版目录先移入可独立重建的目录库。"""
        self.check_sqlite()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.lock(exclusive=True):
            exists = self.path.exists()
            if existing is True:
                require(self.path.is_file(), "ledger file does not exist", "not_found")
            if existing is False:
                require(not exists, "ledger already exists; open the existing file", "already_exists")
            if not exists:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            else:
                require(os.access(self.path, os.W_OK), "ledger file is not writable", "invalid_ledger")
            try:
                backup = self._initialize_locked(exists)
            except Exception:
                if not exists:
                    self.engine.dispose()
                    self.path.unlink(missing_ok=True)
                raise
        result = {"path": str(self.path), "revision": self.head, "sqlite": sqlite3.sqlite_version}
        if backup is not None:
            result["migration_backup"] = str(backup)
        return result

    def _initialize_locked(self, exists):
        with self.engine.connect() as connection:
            self.configure(connection)
            previous = MigrationContext.configure(connection).get_current_revision()
            if exists:
                required = {"account", "strategy", "trade", "instrument", "pool"} if previous == "0001" else set(metadata.tables)
                require(previous in self.revisions and required <= set(inspect(connection).get_table_names()),
                        "file is not a compatible StratHive ledger", "invalid_ledger")
            connection.commit()
            backup = None
            if previous is not None and previous != self.head:
                backup = self.path.with_name(f"{self.path.name}.pre-{self.head}-{time_ns()}.bak")
                self._copy_database(self.path, backup, {previous})
                rows = connection.execute(select(instruments)).mappings().all()
                if rows:
                    self.catalog.prepare(rows)
                connection.commit()
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            connection.commit()
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                self.migrations.attributes["connection"] = connection
                command.upgrade(self.migrations, "head")
                if previous is not None and previous != self.head:
                    # 重放迁移后的事实；校验失败时 SQLite 会回滚整个升级。
                    tx = Transaction(connection)
                    for account in tx.find("account"):
                        before = account["current"]
                        after = rebuild(tx, account["id"])["current"]
                        require(all(before[field] == after[field] for field in ("cash", "unallocated_cash", "positions")),
                                "migration changed account assets", "migration_conflict")
                        if previous in {"0002", "0003"}:
                            require(before["runs"] == after["runs"], "migration changed run assets", "migration_conflict")
                        run_ids = self.migrations.attributes.get("legacy_run_ids", {})
                        for old_id, old_assets in before.get("pools", {}).items():
                            run_id = run_ids.get(int(old_id))
                            new_assets = after["runs"].get(str(run_id), {"cash": "0.00", "positions": []})
                            require(all(old_assets[field] == new_assets[field] for field in ("cash", "positions")),
                                    f"migration changed run assets: {run_id or old_id}", "migration_conflict")
                require(not connection.exec_driver_sql("PRAGMA foreign_key_check").first(),
                        "migration left invalid references", "migration_conflict")
                connection.commit()
            finally:
                self.migrations.attributes.pop("connection", None)
                self.migrations.attributes.pop("legacy_run_ids", None)
            return backup

    def backup(self, destination):
        require(self.path.is_file(), "initialize the ledger with db init", "not_initialized")
        self.check_sqlite()
        with self.lock():
            self._copy_database(self.path, Path(destination), self.revisions)
        return {"path": str(Path(destination).resolve())}

    def restore(self, source):
        self.check_sqlite()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.lock(exclusive=True):
            self._copy_database(Path(source), self.path, self.revisions)
        return {"path": str(self.path)}

    @staticmethod
    def _copy_database(source, destination, revisions):
        source = source.resolve()
        destination = destination.resolve()
        require(source.is_file(), f"database does not exist: {source}", "not_found")
        require(not destination.exists(), f"destination already exists: {destination}", "already_exists")
        require(not any(Path(str(destination) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")),
                "destination has SQLite sidecar files", "already_exists")
        fd, temporary = tempfile.mkstemp(prefix=".strathive-", dir=destination.parent)
        os.close(fd)
        try:
            with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5)) as origin, \
                    closing(sqlite3.connect(temporary)) as target:
                origin.backup(target, pages=128)
                require(target.execute("PRAGMA quick_check").fetchall() == [("ok",)],
                        "database failed integrity check", "invalid_backup")
                try:
                    versions = target.execute("SELECT version_num FROM alembic_version").fetchall()
                except sqlite3.DatabaseError as exc:
                    raise LedgerError("invalid_backup", "backup is not a StratHive ledger") from exc
                require(len(versions) == 1 and versions[0][0] in revisions, "database schema is incompatible", "invalid_backup")
            os.link(temporary, destination)
        finally:
            os.unlink(temporary)
