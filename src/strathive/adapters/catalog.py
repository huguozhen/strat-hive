"""独立的证券目录库，可由离线快照或联网同步重建。"""

import fcntl
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Boolean, Column, Integer, MetaData, String, Table, create_engine, func, inspect, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from strathive.core.rules import LedgerError

from .files import catalog_file

metadata = MetaData()
FIELDS = ("symbol", "code", "market", "kind", "name", "full_name")
instruments = Table("security", metadata, Column("symbol", String, primary_key=True),
                    *(Column(field, String, nullable=False) for field in FIELDS[1:]),
                    Column("active", Boolean, nullable=False))
sync = Table("sync_status", metadata, Column("source", String, primary_key=True),
             Column("attempted_at", String), Column("succeeded_at", String),
             Column("count", Integer), Column("error", String))


class Catalog:
    def __init__(self, path):
        self.path = Path(path)
        self.engine = create_engine(URL.create("sqlite", database=str(self.path)),
                                    connect_args={"timeout": 5}, poolclass=NullPool)

    @contextmanager
    def refresh_lock(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a+b") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise LedgerError("busy", "security refresh in progress; retry") from exc
            yield

    def prepare(self, rows=None):
        if self.path.is_file() and rows is None and inspect(self.engine).has_table(sync.name):
            return
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            metadata.create_all(connection)
            if connection.execute(select(instruments.c.symbol).limit(1)).first() is None:
                self._put(connection, rows if rows else catalog_file())

    def find(self, **filters):
        self.prepare()
        with self.engine.connect() as connection:
            rows = connection.execute(select(instruments).filter_by(**filters)).mappings()
            return [dict(row) for row in rows]

    @staticmethod
    def _put(connection, rows):
        statement = insert(instruments)
        statement = statement.on_conflict_do_update(
            index_elements=[instruments.c.symbol],
            set_={
                "name": statement.excluded.name,
                "full_name": func.coalesce(func.nullif(statement.excluded.full_name, ""), instruments.c.full_name),
                "active": statement.excluded.active,
            },
            where=instruments.c.kind == statement.excluded.kind,
        )
        symbols = [row["symbol"] for row in rows]
        current = connection.execute(select(instruments.c.symbol, instruments.c.kind)
                                     .where(instruments.c.symbol.in_(symbols))).all()
        kinds = dict(current)
        conflicts = [row["symbol"] for row in rows if kinds.get(row["symbol"], row["kind"]) != row["kind"]]
        connection.execute(statement, [{**row, "active": row.get("active", True)} for row in rows])
        return {"count": len(rows) - len(conflicts), "conflicts": conflicts}

    def update(self, rows):
        self.prepare()
        with self.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            return self._put(connection, rows)

    def status(self, source):
        self.prepare()
        with self.engine.connect() as connection:
            row = connection.execute(select(sync).filter_by(source=source)).mappings().first()
            return dict(row) if row is not None else {"source": source, "attempted_at": None,
                                                      "succeeded_at": None, "count": None, "error": None}

    @staticmethod
    def _status(connection, source, **changes):
        statement = insert(sync).values(source=source, **changes)
        connection.execute(statement.on_conflict_do_update(index_elements=[sync.c.source], set_=changes))

    def refresh(self, source, rows, attempted_at):
        with self.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            result = self._put(connection, rows)
            self._status(connection, source, attempted_at=attempted_at,
                         succeeded_at=datetime.now(timezone.utc).isoformat(), count=result["count"], error=None)
            return result

    def failure(self, source, attempted_at, error):
        with self.engine.begin() as connection:
            self._status(connection, source, attempted_at=attempted_at, error=error)
