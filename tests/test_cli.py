import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor


def command(db, *args):
    return subprocess.run(
        [sys.executable, "-m", "strathive", "--db", str(db), *args],
        capture_output=True, text=True, timeout=15,
    )


def invoke(db, *args):
    process = command(db, *args)
    assert process.returncode == 0, process.stdout + process.stderr
    result = json.loads(process.stdout)
    assert result["ok"]
    return result["data"]


def test_cli_packaging_inputs_and_concurrency(tmp_path):
    db = tmp_path / "ledger.db"
    assert invoke(db, "db", "init")["revision"] == "0002"
    assert invoke(db, "db", "init")["revision"] == "0002"
    assert invoke(db, "security", "lookup", "000001")["market"] == "SZ"
    assert invoke(db, "security", "lookup", "SH.510300")["kind"] == "etf"
    account = invoke(db, "account", "open", "--name", "test", "--cash", "100", "--commission-rate", "0.0003",
                     "--as-of", "2026-11-01T09:00:00")
    account_id = str(account["id"])

    def deposit(_):
        return invoke(db, "account", "deposit", "--account-id", account_id, "--amount", "1.25", "--at", "2026-11-02T09:30:00")

    with ThreadPoolExecutor(max_workers=2) as executor:
        rows = list(executor.map(deposit, range(2)))
    assert rows[0]["id"] != rows[1]["id"]
    assert invoke(db, "account", "show", account_id)["current"]["cash"] == "102.50"
    strategy = invoke(db, "strategy", "create", "--name", "Concurrent strategy")

    def create_version(_):
        return invoke(db, "strategy", "version", "create", "--strategy-id", str(strategy["id"]))

    with ThreadPoolExecutor(max_workers=2) as executor:
        versions = list(executor.map(create_version, range(2)))
    assert sorted(version["number"] for version in versions) == [2, 3]

    snapshot = tmp_path / "broker.csv"
    snapshot.write_text("type,cash,security,quantity,unit_cost\ncash,102.50,,,\n")
    assert invoke(db, "reconcile", "--account-id", account_id, "--file", str(snapshot))["status"] == "matched"
    bad = command(db, "security", "lookup", "不存在")
    assert bad.returncode == 3
    assert json.loads(bad.stdout)["error"]["code"] == "not_found"
    invalid = command(db, "account", "open")
    assert invalid.returncode == 2
    assert json.loads(invalid.stdout)["error"]["code"] == "invalid_input"

    invalid_time = command(db, "account", "open", "--name", "Bad time", "--cash", "1",
                           "--commission-rate", "0.0003", "--as-of", "0001-01-01T00:00:00")
    assert invalid_time.returncode == 2
    assert json.loads(invalid_time.stdout)["error"]["code"] == "invalid_input"

    backup = tmp_path / "backup.db"
    restored = tmp_path / "restored.db"
    invoke(db, "db", "backup", "--file", str(backup))
    invoke(restored, "db", "restore", "--file", str(backup))
    assert invoke(restored, "account", "show", account_id) == invoke(db, "account", "show", account_id)
    duplicate = command(restored, "db", "restore", "--file", str(backup))
    assert duplicate.returncode
    assert json.loads(duplicate.stdout)["error"]["code"] == "already_exists"


def test_opening_to_broker_reconciliation(tmp_path):
    db = tmp_path / "ledger.db"
    invoke(db, "db", "init")
    invoke(db, "strategy", "create", "--name", "示例策略")
    opening = tmp_path / "opening.json"
    opening.write_text(json.dumps({
        "positions": [{"security": "SH.600000", "quantity": 100, "unit_cost": "9",
                       "position_date": "2026-10-01"}],
        "allocations": [{"strategy_id": 1, "version": 1, "run": "A",
                         "positions": [{"security": "SH.600000", "quantity": 100}]}],
    }))
    invoke(db, "account", "open", "--name", "示例", "--cash", "1000", "--commission-rate", "0.0003",
           "--as-of", "2026-11-01T09:00:00", "--opening", str(opening), "--request-id", "opening")
    invoke(db, "strategy", "edit", "--strategy-id", "1", "--description", "next")
    invoke(db, "strategy", "version", "create", "--strategy-id", "1")
    successor = invoke(db, "run", "create", "--strategy-id", "1", "--account-id", "1", "--name", "B")
    assert successor["version"]["number"] == 2
    assert invoke(db, "run", "show", "1")["version"]["number"] == 1
    args = ("trade", "add", "--run-id", "1", "--security", "SH.600000", "--side", "sell", "-q", "10",
            "--price", "10", "--at", "2026-11-02T09:30:00", "--commission", "0.20", "--request-id", "sale")
    sale = invoke(db, *args)
    assert invoke(db, *args) == sale
    invoke(db, "trade", "edit", "--trade-id", str(sale["id"]), "--price", "11")
    closed = invoke(db, "run", "close", "--run-id", "1", "--at", "2026-11-03T09:30:00",
                    "--successor-run-id", "2", "--request-id", "close")
    assert closed["successor"]["opening_unpriced"] == ["SH.600000"]
    assert closed["current"]["closing"]["returned_cash"] == "109.74"
    broker = tmp_path / "broker.csv"
    broker.write_text("type,cash,security,quantity,unit_cost\ncash,1109.74,,,\nposition,,SH.600000,90,9\n")
    result = invoke(db, "reconcile", "--account-id", "1", "--file", str(broker), "--cost-basis", "moving_average_v1")
    assert result == {"account_id": 1, "status": "matched", "differences": [], "cost_incomparable": []}
    rejected = command(db, "run", "start", "--run-id", "2", "--cash", "10", "--at", "2026-11-04T09:00:00")
    assert rejected.returncode == 5
    assert json.loads(rejected.stdout)["error"]["code"] == "ledger_conflict"
