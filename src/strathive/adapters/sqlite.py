"""SQLite persistence and maintenance for the local ledger."""

import fcntl
import os
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, delete, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError

from strathive.core.rules import LedgerError, require

from .schema import instruments, metadata


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


class SQLiteStore:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self.engine = create_engine(URL.create("sqlite", database=str(self.path)),
                                    connect_args={"timeout": 5})
        self.migrations = Config()
        self.migrations.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        self.head = ScriptDirectory.from_config(self.migrations).get_current_head()

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

    def initialize(self, catalog):
        self.check_sqlite()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.lock(exclusive=True):
            if not self.path.exists():
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            self._initialize_locked(catalog)
        return {"path": str(self.path), "revision": self.head, "sqlite": sqlite3.sqlite_version}

    def _initialize_locked(self, catalog):
        with self.engine.connect() as connection:
            self.configure(connection)
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            connection.commit()
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                self.migrations.attributes["connection"] = connection
                command.upgrade(self.migrations, "head")
                self._put_catalog(connection, catalog, replace=False)
                connection.commit()
            finally:
                self.migrations.attributes.pop("connection", None)

    @staticmethod
    def _put_catalog(connection, rows, *, replace=True):
        statement = sqlite_insert(instruments)
        if replace:
            statement = statement.on_conflict_do_update(
                index_elements=["symbol"],
                set_={key: statement.excluded[key] for key in ("name", "full_name", "kind")},
            )
        else:
            statement = statement.on_conflict_do_nothing(index_elements=["symbol"])
        connection.execute(statement, rows)

    def update_catalog(self, rows):
        with self.transaction(write=True) as tx:
            self._put_catalog(tx.connection, rows)
        return {"count": len(rows)}

    def backup(self, destination):
        require(self.path.is_file(), "initialize the ledger with db init", "not_initialized")
        self.check_sqlite()
        with self.lock():
            self._copy_database(self.path, Path(destination), self.head)
        return {"path": str(Path(destination).resolve())}

    def restore(self, source):
        self.check_sqlite()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.lock(exclusive=True):
            self._copy_database(Path(source), self.path, self.head)
        return {"path": str(self.path)}

    @staticmethod
    def _copy_database(source, destination, revision):
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
                require(versions == [(revision,)], "database schema is incompatible", "invalid_backup")
            os.link(temporary, destination)
        finally:
            os.unlink(temporary)
