from pathlib import Path

from . import settings
from .adapters.catalog import Catalog
from .adapters.sqlite import SQLiteStore
from .core.ledger import Ledger


def build(path: Path | None = None):
    store = SQLiteStore(path if path is not None else settings.ledger_path(), Catalog(settings.catalog_path()))
    return Ledger(store), store
