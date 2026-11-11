"""Refresh the offline catalog from the Shanghai and Shenzhen exchanges."""

import csv
import json
from datetime import date
from pathlib import Path

from strathive.sources.public import fetch_catalog
from strathive.sources.sync import validate_catalog

ROOT = Path(__file__).resolve().parents[1] / "src/strathive/resources"


def main():
    rows, sources = fetch_catalog()
    validate_catalog(rows)
    fields = ("market", "code", "kind", "name", "full_name")
    with (ROOT / "securities.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: row["symbol"]))
    manifest = {"retrieved_on": date.today().isoformat(), "count": len(rows), "sources": sources}
    (ROOT / "securities.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
