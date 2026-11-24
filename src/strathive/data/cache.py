"""Rebuildable latest-value cache; never stores accounting facts."""
from contextlib import contextmanager
import json
import sqlite3
from strathive.core.rules import LedgerError
from .models import Price


class PriceCache:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.execute('CREATE TABLE IF NOT EXISTS price (symbol TEXT, provider TEXT, kind TEXT, payload TEXT, PRIMARY KEY(symbol,provider,kind))')
            with connection:
                yield connection
        except sqlite3.DatabaseError as exc:
            raise LedgerError("market_cache_unavailable", "行情缓存不可用，可移走缓存后重新获取。") from exc
        finally:
            connection.close()

    def put(self, price):
        with self.connection() as connection:
            old = connection.execute('SELECT payload FROM price WHERE symbol=? AND provider=? AND kind=?',
                                     (price.symbol, price.provider, price.kind)).fetchone()
            if old:
                old = json.loads(old[0])
                if (old.get('as_of') or old['session_date']) > (price.as_of or price.session_date):
                    return
            connection.execute('INSERT OR REPLACE INTO price VALUES (?,?,?,?)',
                               (price.symbol, price.provider, price.kind, json.dumps(price.to_dict())))

    def find(self, symbol, kind):
        with self.connection() as connection:
            rows = connection.execute('SELECT payload FROM price WHERE symbol=? AND kind=?', (symbol, kind)).fetchall()
        return [Price(**json.loads(row[0])) for row in rows]
