"""Normalized files accepted by the local CLI."""

import csv
import json
import re
from decimal import Decimal
from pathlib import Path

from strathive.core.rules import LedgerError, require

CATALOG = Path(__file__).parents[1] / "resources" / "securities.csv"


def json_file(path):
    with path.open(encoding="utf-8-sig") as stream:
        value = json.load(stream, parse_float=Decimal)
    require(isinstance(value, dict), "JSON input must be an object")
    return value


def catalog_file(path=CATALOG):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = {"market", "code", "kind", "name", "full_name"}
        require(set(reader.fieldnames or ()) == fields, "invalid security catalog header")
        records = []
        seen = set()
        for row in reader:
            require(all(row.get(field) is not None for field in fields) and None not in row,
                    "invalid security catalog row")
            market, code = row["market"], row["code"]
            require(market in {"SH", "SZ"} and re.fullmatch(r"[0-9]{6}", code)
                    and row["kind"] in {"stock", "etf"} and bool(row["name"].strip()),
                    "invalid security catalog entry")
            symbol = f"{market}.{code}"
            require(symbol not in seen, f"duplicate catalog security: {symbol}")
            seen.add(symbol)
            records.append({"symbol": symbol, **row})
        require(bool(records), "security catalog is empty")
        return records


def broker_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = {"type", "cash", "security", "quantity", "unit_cost"}
        require(set(reader.fieldnames or ()) == fields, "invalid broker CSV header")
        cash = None
        positions = []
        for row in reader:
            require(all(row.get(field) is not None for field in fields) and None not in row,
                    "invalid broker CSV row")
            if row["type"] == "cash":
                require(cash is None and bool(row["cash"]) and not any(
                    row[field] for field in ("security", "quantity", "unit_cost")), "invalid broker cash row")
                cash = row["cash"]
            elif row["type"] == "position":
                require(not row["cash"] and bool(row["security"]) and bool(row["quantity"]),
                        "invalid broker position row")
                positions.append({field: row[field] for field in ("security", "quantity", "unit_cost")})
            else:
                raise LedgerError("invalid_input", "broker row type must be cash or position")
        require(cash is not None, "broker CSV requires one cash row")
        return cash, positions
