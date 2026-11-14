"""
Unit and integration tests for Qashbocks CLI commands.
Verifies accounts, register, bills, forecasting, and search against data/sample.db.
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
DB_FILE = REPO_DIR / "data" / "sample.db"
CLI_BIN = REPO_DIR / "bin" / "qashbocks"


def test_sample_db_integrity():
    assert DB_FILE.exists(), f"Sample database missing at {DB_FILE}"
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM ACCT WHERE fClosed = 0")
    active_accts = cur.fetchone()[0]
    assert active_accts >= 4, f"Expected at least 4 active accounts, found {active_accts}"

    cur.execute("SELECT COUNT(*) FROM TRN WHERE (grftt & 2097152) = 0")
    tx_count = cur.fetchone()[0]
    assert tx_count > 10, f"Expected transaction records, found {tx_count}"
    conn.close()


def test_cli_accounts():
    res = subprocess.run(
        [sys.executable, str(CLI_BIN), "--db", str(DB_FILE), "accounts", "--plain"],
        capture_output=True,
        text=True,
        check=True,
    )
    out = res.stdout
    assert "Checking Account" in out or "Checking" in out
    assert "Savings" in out


def test_cli_register():
    res = subprocess.run(
        [sys.executable, str(CLI_BIN), "--db", str(DB_FILE), "reg", "checking", "-n", "5", "--plain"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.returncode == 0
    assert len(res.stdout.splitlines()) > 3


def test_cli_bills():
    res = subprocess.run(
        [sys.executable, str(CLI_BIN), "--db", str(DB_FILE), "bills", "-d", "45", "--plain"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.returncode == 0


def test_cli_search():
    res = subprocess.run(
        [sys.executable, str(CLI_BIN), "--db", str(DB_FILE), "find", "Water", "--plain"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.returncode == 0
    assert "Water" in res.stdout


def test_cli_inspect():
    res = subprocess.run(
        [sys.executable, str(CLI_BIN), "--db", str(DB_FILE), "inspect", "33", "--plain"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.returncode == 0
    assert "SPLIT LINE ITEMS" in res.stdout
    assert "Kokodi Kokoda" in res.stdout


def test_cli_forecast():
    res = subprocess.run(
        [sys.executable, str(CLI_BIN), "--db", str(DB_FILE), "forecast", "-a", "checking", "-d", "30"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.returncode == 0
    assert "Balance Forecast" in res.stdout


if __name__ == "__main__":
    test_sample_db_integrity()
    test_cli_accounts()
    test_cli_register()
    test_cli_bills()
    test_cli_search()
    test_cli_inspect()
    test_cli_forecast()
    print("All Qashbocks integration tests passed successfully.")
