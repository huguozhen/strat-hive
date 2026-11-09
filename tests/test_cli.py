import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor


def command(db, *args, machine=True):
    return subprocess.run(
        [sys.executable, "-m", "strathive", "--db", str(db), *(["--json"] if machine else []), *args],
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
    assert invoke(db, "db", "init")["revision"] == "0001"
    assert invoke(db, "db", "init")["revision"] == "0001"
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

    snapshot = tmp_path / "broker.csv"
    snapshot.write_text("type,cash,security,quantity,unit_cost\ncash,102.50,,,\n")
    assert invoke(db, "reconcile", "--account-id", account_id, "--file", str(snapshot))["status"] == "matched"
    bad = command(db, "security", "lookup", "不存在")
    assert bad.returncode == 3
    assert json.loads(bad.stdout)["error"]["code"] == "not_found"
    invalid = command(db, "account", "open")
    assert invalid.returncode == 2
    assert json.loads(invalid.stdout)["error"]["code"] == "invalid_input"

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
    opening = tmp_path / "opening.json"
    opening.write_text(json.dumps({
        "positions": [{"security": "SH.600000", "quantity": 100, "unit_cost": "9",
                       "position_date": "2026-10-01"}],
        "allocations": [
            {"strategy": name, "pool": name, "run": name,
             "positions": [{"security": "SH.600000", "quantity": count}]}
            for name, count in (("A", 40), ("B", 60))
        ],
    }))
    changes = tmp_path / "changes.json"
    changes.write_text('{"price":"11"}')
    broker = tmp_path / "broker.csv"
    broker.write_text("type,cash,security,quantity,unit_cost\ncash,1109.74,,,\nposition,,SH.600000,90,9\n")

    invoke(db, "db", "init")
    invoke(db, "account", "open", "-n", "示例", "--cash", "1000", "--commission-rate", "0.0003",
           "--as-of", "2026-11-01T09:00:00",
           "--opening", str(opening))
    invoke(db, "run", "status", "--run-id", "1", "--status", "active")
    invoke(db, "pool", "allocate", "--account-id", "1", "--pool-id", "1", "--amount", "200",
           "--at", "2026-11-02T09:00:00")
    trade = invoke(db, "trade", "add", "--run-id", "1", "--security", "SH.600000", "--side", "sell", "-q", "10",
                   "--price", "10", "--at", "2026-11-02T09:30:00",
                   "--commission", "0.20")
    corrected = invoke(db, "trade", "edit", "--trade-id", str(trade["id"]), "--price", "11")
    assert corrected["total_fees"] == "0.26"
    assert invoke(db, "run", "show", "1")["current"]["available_cash"] == "309.74"
    conflict = command(db, "trade", "edit", "--trade-id", str(trade["id"]), "--price", "12", "-f", str(changes))
    assert conflict.returncode == 2
    assert json.loads(conflict.stdout)["error"]["code"] == "invalid_input"
    assert invoke(db, "trade", "edit", "--trade-id", str(trade["id"]), "-f", str(changes)) == corrected
    result = invoke(db, "reconcile", "--account-id", "1", "--file", str(broker), "--cost-basis", "moving_average_v1")
    assert result == {"account_id": 1, "status": "matched", "differences": [], "cost_incomparable": []}

    summary = command(db, "run", "show", "1", machine=False)
    assert summary.returncode == 0
    assert all(label in summary.stdout for label in ("可用现金：309.74", "持仓", "成交", "卖出"))

    detail = command(db, "trade", "show", str(trade["id"]), machine=False)
    assert detail.returncode == 0
    assert "2026-11-02 09:30:00+08:00" in detail.stdout

    difference = command(db, "reconcile", "--account-id", "1", "--cash", "1000",
                         "--position", "SH.600000,90,9", machine=False)
    assert difference.returncode == 0
    assert all(label in difference.stdout for label in ("发现对账差异", "系统账本", "成本不可比"))

    missing = command(db, "trade", "add", machine=False)
    assert missing.returncode == 2
    assert "缺少必填选项" in missing.stderr
