from pathlib import Path

from platformdirs import user_data_path

from .adapters.sqlite import SQLiteStore
from .core.ledger import Ledger


def build(path: Path | None = None):
    store = SQLiteStore(path if path is not None else user_data_path("strathive") / "ledger.sqlite3")
    return Ledger(store), store
