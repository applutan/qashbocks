#!/usr/bin/env python3
"""
qashbocks/cli.py - Standalone personal finance command-line ledger.

Core daily commands:
  qashbocks accounts              Show active accounts, balances, and reconciliation status
  qashbocks bills [--days 30]     Show upcoming scheduled bills and running cash-flow forecast
  qashbocks register <account>    View recent transactions and register for an account
  qashbocks add                   Record a new transaction (with automated bill matching & confirmation)
  qashbocks find <query>          Universal transaction and split search
  qashbocks forecast              Render terminal balance forecast line chart
  qashbocks reconcile <account>   Run monthly statement reconciliation wizard
  qashbocks init <db_path>        Initialize a new ledger database (or sample database)

Usage:
  qashbocks --help
"""

import argparse
import calendar
import csv
import datetime
import io
import os
import re
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

# Package-relative imports
PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from qashbocks import queries
    from qashbocks.presenter import render_template
    from qashbocks.schema import init_db
except ImportError:
    import queries
    from presenter import render_template
    from schema import init_db


CONFIG_FILE = REPO_ROOT / "config.toml"
USER_CONFIG_FILE = Path.home() / ".config" / "qashbocks" / "config.toml"


def load_config() -> dict:
    candidates = [
        Path.cwd() / "config.toml",
        CONFIG_FILE,
        USER_CONFIG_FILE,
    ]
    for p in candidates:
        if p.exists():
            try:
                import tomllib
                with open(p, "rb") as f:
                    return tomllib.load(f)
            except Exception:
                pass
    return {}


def get_default_db() -> Path:
    """Resolves default database path in order of precedence:
    1. QASHBOCKS_DB or MONEY_DB environment variable
    2. config.toml [defaults] database
    3. Built-in fallback: data/sample.db
    """
    env_db = os.environ.get("QASHBOCKS_DB") or os.environ.get("MONEY_DB")
    if env_db:
        return Path(env_db).expanduser().resolve()

    cfg = load_config()
    cfg_db = cfg.get("defaults", {}).get("database")
    if cfg_db:
        p = Path(cfg_db).expanduser()
        if not p.is_absolute():
            p = (REPO_ROOT / p).resolve()
        if p.exists():
            return p
        sample_p = (REPO_ROOT / "data" / "sample.db").resolve()
        if sample_p.exists():
            return sample_p
        return p

    return (REPO_ROOT / "data" / "sample.db").resolve()


DEFAULT_DB = get_default_db()


def serialize_config(config: dict) -> str:
    """Serializes configuration dict to TOML format with complete documentation comments."""
    lines = [
        "# ==============================================================================\n",
        "# Qashbocks Configuration File\n",
        "# ==============================================================================\n",
        "# Configuration search paths:\n",
        "#   1. ./config.toml (working directory or repository)\n",
        "#   2. ~/.config/qashbocks/config.toml (user-wide configuration)\n",
        "#\n",
        "# Precedence for database path:\n",
        "#   1. CLI argument:             --db <path>\n",
        "#   2. Environment variable:     QASHBOCKS_DB=<path>\n",
        "#   3. Configuration file:       [defaults] database = \"<path>\"\n",
        "#   4. Built-in fallback:        data/sample.db\n",
        "# ==============================================================================\n\n",
        "[defaults]\n",
        "# Path to the primary SQLite database (supports ~/ expansion or relative paths)\n",
    ]
    defaults = config.get("defaults", {})
    if "database" in defaults:
        lines.append(f'database = "{defaults["database"]}"\n')
    lines.append("\n# Default account alias used when none is specified\n")
    if "account" in defaults:
        lines.append(f'account = "{defaults["account"]}"\n')
    lines.append("\n# Default forecast horizon in days for upcoming bills (qashbocks bills)\n")
    if "bill_horizon_days" in defaults:
        lines.append(f'bill_horizon_days = {defaults["bill_horizon_days"]}\n')
    lines.append("\n# Default window in days for register auditing\n")
    if "audit_days" in defaults:
        lines.append(f'audit_days = {defaults["audit_days"]}\n')
    for k, v in sorted(defaults.items()):
        if k not in ("database", "account", "bill_horizon_days", "audit_days"):
            val_str = f'"{v}"' if isinstance(v, str) else str(v)
            lines.append(f"{k} = {val_str}\n")

    lines.append("\n# ==============================================================================\n")
    lines.append("# Account Aliases\n")
    lines.append("# Maps short CLI aliases to canonical account names in the database.\n")
    lines.append("# Used by `qashbocks reg <alias>`, `qashbocks add -a <alias>`, etc.\n")
    lines.append("# Can be viewed or updated via `qashbocks alias [ls|set|rm]`.\n")
    lines.append("# ==============================================================================\n")
    lines.append("[aliases]\n")
    for k, v in sorted(config.get("aliases", {}).items()):
        lines.append(f'{k:<8} = "{v}"\n')
    return "".join(lines)


def get_target_config_file() -> Path:
    if CONFIG_FILE.exists():
        return CONFIG_FILE
    if USER_CONFIG_FILE.exists():
        return USER_CONFIG_FILE
    return CONFIG_FILE


def save_alias_to_config(alias: str, account_name: str) -> bool:
    config = load_config()
    aliases = config.get("aliases", {})
    aliases[alias.lower().strip()] = account_name.strip()
    config["aliases"] = aliases

    content = serialize_config(config)
    target = get_target_config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return True


def remove_alias_from_config(alias: str) -> bool:
    config = load_config()
    aliases = config.get("aliases", {})
    clean_alias = alias.lower().strip()
    if clean_alias not in aliases:
        return False
    del aliases[clean_alias]
    config["aliases"] = aliases

    content = serialize_config(config)
    target = get_target_config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return True


# Default base aliases
ACCOUNT_ALIASES = {
    "checking": "Main Checking",
    "current": "Main Checking",
    "savings": "High-Yield Savings",
    "amex": "Amex Platinum",
    "visa": "Barclays Visa",
    "cash": "Cash Wallet",
    "lucre": "Cash Wallet",
    "euro": "Euro Travel Cash",
}

# Overlay aliases from config.toml
_cfg = load_config()
if "aliases" in _cfg:
    for a_key, a_val in _cfg["aliases"].items():
        ACCOUNT_ALIASES[a_key.lower()] = a_val


def get_db(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        print(f"Error: Database not found at {db_path}", file=sys.stderr)
        print("Run `qashbocks init` to initialize a new database, or specify with --db.", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def parse_date(dt_str: str) -> datetime.date | None:
    if not dt_str:
        return None
    dt_clean = str(dt_str).strip().split()[0]
    try:
        if "-" in dt_clean:
            parts = [int(p) for p in dt_clean.split("-")]
            if len(parts) >= 3 and parts[1] > 0 and parts[2] > 0:
                return datetime.date(parts[0], parts[1], parts[2])
        elif "/" in dt_clean:
            parts = [int(p) for p in dt_clean.split("/")]
            if len(parts) >= 3 and parts[0] > 0 and parts[1] > 0:
                year = parts[2]
                if year < 100:
                    year += 2000 if year < 70 else 1900
                return datetime.date(year, parts[0], parts[1])
    except (ValueError, TypeError):
        return None
    return None


def add_months(sourcedate: datetime.date, months: int) -> datetime.date:
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month // 12
    month = month % 12 + 1
    day = min(sourcedate.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


def format_frequency(frq: int, cfrq: float = 1.0) -> str:
    cfrq = float(cfrq) if cfrq else 1.0
    if frq == 0:
        return "Once"
    elif frq == 1:
        if cfrq == 1.0:
            return "Daily"
        return f"Every {int(cfrq)}d"
    elif frq == 2:
        if cfrq == 1.0:
            return "Weekly"
        elif cfrq == 0.5:
            return "Fortnightly"
        elif cfrq == 0.25:
            return "4-weekly"
        return f"Every {round(1.0/cfrq)}w"
    elif frq == 3:
        if cfrq == 1.0:
            return "Monthly"
        elif cfrq == 0.5:
            return "Bi-monthly"
        elif cfrq == 2.0:
            return "Semi-monthly"
        return f"Every {int(cfrq)}m"
    elif frq == 4:
        if cfrq == 1.0:
            return "Quarterly"
        return f"Every {int(3 * cfrq)}m"
    elif frq == 5:
        if cfrq == 1.0:
            return "Annual"
        elif cfrq == 0.5:
            return "Every 2 yrs"
        elif cfrq == 0.25:
            return "Every 4 yrs"
        elif cfrq == 2.0:
            return "Semi-annual"
        return f"Every {round(12.0/cfrq)}m"
    elif frq == 6:
        return "Semi-monthly"
    return f"frq:{frq}"


def calculate_due_date(start_date: datetime.date, frq: int, cfrq: float, instance_index: int) -> datetime.date:
    cfrq_f = float(cfrq) if cfrq and float(cfrq) > 0 else 1.0
    if frq == 0:
        return start_date
    elif frq == 1:
        days_step = int(round(1.0 / cfrq_f)) if cfrq_f < 1.0 else int(round(cfrq_f))
        return start_date + datetime.timedelta(days=instance_index * days_step)
    elif frq == 2:
        days_step = int(round(7.0 / cfrq_f)) if cfrq_f < 1.0 else int(round(cfrq_f * 7.0))
        return start_date + datetime.timedelta(days=instance_index * days_step)
    elif frq == 3:
        months_step = int(round(1.0 / cfrq_f)) if cfrq_f < 1.0 else int(round(cfrq_f))
        return add_months(start_date, instance_index * months_step)
    elif frq == 4:
        months_step = int(round(3.0 / cfrq_f)) if cfrq_f < 1.0 else int(round(3.0 * cfrq_f))
        return add_months(start_date, instance_index * months_step)
    elif frq == 5:
        months_step = int(round(12.0 / cfrq_f))
        return add_months(start_date, instance_index * months_step)
    elif frq == 6:
        months_added = instance_index // 2
        base = add_months(start_date, months_added)
        if instance_index % 2 == 1:
            return base + datetime.timedelta(days=14)
        return base
    return start_date


def resolve_account(conn: sqlite3.Connection, identifier: str, required: bool = True) -> dict | None:
    cur = conn.cursor()
    ident = identifier.strip().lower()
    clean_id = ident.lstrip("#")
    full_name = ACCOUNT_ALIASES.get(ident, ident)

    if clean_id.isdigit():
        cur.execute("SELECT hacct, szFull, szAls, amtLimit, fClosed FROM ACCT WHERE hacct = ?", (int(clean_id),))
    else:
        cur.execute(
            "SELECT hacct, szFull, szAls, amtLimit, fClosed FROM ACCT WHERE LOWER(szFull) = ? OR LOWER(szAls) = ?",
            (full_name.lower(), ident),
        )
    row = cur.fetchone()
    if not row:
        cur.execute(
            "SELECT hacct, szFull, szAls, amtLimit, fClosed FROM ACCT WHERE LOWER(szFull) LIKE ? AND fClosed = 0",
            (f"%{ident}%",),
        )
        row = cur.fetchone()

    if not row:
        if not required:
            return None
        print(f"Error: Account '{identifier}' not found.", file=sys.stderr)
        sys.exit(1)

    return {
        "id": row["hacct"],
        "name": row["szFull"],
        "alias": row["szAls"],
        "limit": float(row["amtLimit"] or 0.0),
        "closed": bool(row["fClosed"]),
    }


def get_anchored_account_balance(conn: sqlite3.Connection, account_id: int, naive: bool = False) -> float:
    cur = conn.cursor()
    if not naive:
        cur.execute("SELECT current_balance FROM v_active_accounts WHERE account_id = ?", (account_id,))
        row = cur.fetchone()
        if row and row[0] is not None:
            return float(row[0])

    cur.execute("""
        SELECT ROUND(COALESCE(SUM(CAST(amt AS REAL)), 0.0), 2)
        FROM TRN
        WHERE hacct = ?
          AND (CAST(grftt AS INTEGER) & 2097152) = 0
          AND (CAST(grftt AS INTEGER) & 64) = 0
          AND SUBSTR(dt, 1, 10) <= date('now')
    """, (account_id,))
    return float(cur.fetchone()[0] or 0.0)


# =====================================================================
# COMMAND: accounts
# =====================================================================
def get_accounts_context(conn: sqlite3.Connection, naive: bool = False) -> dict:
    cur = conn.cursor()
    cur.execute(queries.GET_ACTIVE_ACCOUNTS)
    rows = cur.fetchall()

    cfg_aliases = load_config().get("aliases", {})
    name_to_alias = {v.lower(): k for k, v in ACCOUNT_ALIASES.items()}
    for k, v in cfg_aliases.items():
        name_to_alias[v.lower()] = k

    liquid_rows = [r for r in rows if not r["is_tracking"] and int(r["account_type"] or 0) != 5]
    inv_rows = [r for r in rows if not r["is_tracking"] and int(r["account_type"] or 0) == 5]
    tracking_rows = [r for r in rows if r["is_tracking"]]

    liquid_accounts = []
    liquid_total = 0.0
    for r in liquid_rows:
        bal = get_anchored_account_balance(conn, r["id"], naive=True) if naive else float(r["balance"] or 0.0)
        liquid_total += bal
        rec_dt = str(r["last_rec_date"] or "")[:10]
        if rec_dt.startswith("1900") or not rec_dt:
            rec_dt = "-"
        liquid_accounts.append({
            "alias": name_to_alias.get(r["name"].lower(), f"#{r['id']}"),
            "name": r["name"][:28],
            "balance": bal,
            "uncl": r["uncleared_count"],
            "rec_dt": rec_dt,
        })

    inv_accounts = []
    for r in inv_rows:
        bal = get_anchored_account_balance(conn, r["id"], naive=True) if naive else float(r["balance"] or 0.0)
        inv_accounts.append({
            "alias": name_to_alias.get(r["name"].lower(), f"#{r['id']}"),
            "name": r["name"][:28],
            "balance": bal,
            "holdings": "0 shares held" if bal == 0.0 else "Active positions",
        })

    tracking_accounts = []
    tracking_total = 0.0
    for r in tracking_rows:
        bal = get_anchored_account_balance(conn, r["id"], naive=True) if naive else float(r["balance"] or 0.0)
        tracking_total += bal
        tracking_accounts.append({
            "alias": name_to_alias.get(r["name"].lower(), f"#{r['id']}"),
            "name": r["name"][:28],
            "balance": bal,
            "role": "Internal Transfer",
        })

    return {
        "sync_badge": "",
        "liquid_accounts": liquid_accounts,
        "liquid_total": liquid_total,
        "inv_accounts": inv_accounts,
        "tracking_accounts": tracking_accounts,
        "tracking_total": tracking_total,
    }


def cmd_accounts(args, conn: sqlite3.Connection):
    ctx = get_accounts_context(conn, naive=getattr(args, "naive", False))
    ctx["cli_style"] = getattr(args, "format", None)
    print(render_template("accounts.tpl", ctx))


# =====================================================================
# COMMAND: alias
# =====================================================================
def cmd_alias(args, conn: sqlite3.Connection):
    action = args.action or "list"
    target_cfg = get_target_config_file()
    if action in ("list", "ls"):
        cfg = load_config()
        aliases = cfg.get("aliases", ACCOUNT_ALIASES)
        print(f"\nAccount Aliases (configured in {target_cfg}):")
        print(f"{'Alias':<12} -> {'Full Account Name'}")
        print("-" * 45)
        for k, v in sorted(aliases.items()):
            print(f"{k:<12} -> {v}")
        print()
    elif action == "set":
        if not args.alias or not args.target:
            print("Usage: qashbocks alias set <alias> <account_name>", file=sys.stderr)
            return
        acct = resolve_account(conn, args.target)
        save_alias_to_config(args.alias, acct["name"])
        print(f"Saved alias: '{args.alias}' -> '{acct['name']}' in {target_cfg}\n")
    elif action in ("rm", "remove", "unset", "del", "delete"):
        if not args.alias:
            print("Usage: qashbocks alias rm <alias>", file=sys.stderr)
            return
        if remove_alias_from_config(args.alias):
            print(f"Removed alias: '{args.alias}' from {target_cfg}\n")
        else:
            print(f"Alias '{args.alias}' not found in {target_cfg}\n", file=sys.stderr)


# =====================================================================
# COMMAND: bills
# =====================================================================
def get_upcoming_bills_list(conn: sqlite3.Connection, horizon_days: int = 45, sleeping: bool = False) -> list[dict]:
    cur = conn.cursor()
    cur.execute(queries.GET_ACTIVE_BILLS)
    rows = cur.fetchall()

    today = datetime.date.today()
    bills = []

    for r in rows:
        head_st = str(r["head_st"])
        latest_st = str(r["latest_st"])
        if head_st == "2" or latest_st == "2":
            continue

        head_unpaid = int(r["head_next_unpaid"]) if str(r["head_next_unpaid"]).isdigit() else 0
        latest_base = int(r["revision_base_iinst"]) if str(r["revision_base_iinst"]).isdigit() else 0

        if r["latest_hbill"] == r["head_id"]:
            rel_idx = head_unpaid
        else:
            rel_idx = head_unpaid - latest_base
            if rel_idx < 0:
                rel_idx = int(r["latest_next_unpaid"]) if str(r["latest_next_unpaid"]).isdigit() else 0

        start_dt = parse_date(r["revision_dt"])
        if not start_dt:
            continue
        frq = int(r["frq"]) if str(r["frq"]).isdigit() else 3
        cfrq = float(r["cFrqInst"]) if r["cFrqInst"] else 1.0

        c_inst_max = int(r["cInstMax"]) if str(r["cInstMax"]).lstrip("-").isdigit() else -1
        if c_inst_max > 0 and rel_idx >= c_inst_max:
            continue

        dt_max = parse_date(r["dtMax"])
        if dt_max and dt_max.year < 2200 and dt_max < today:
            continue

        due_date = calculate_due_date(start_dt, frq, cfrq, rel_idx)
        if not due_date:
            continue
        diff = (due_date - today).days

        if sleeping:
            if diff < -30:
                bills.append({
                    "head_id": r["head_id"],
                    "payee_id": r["payee_id"],
                    "payee": r["payee"] or "Unknown",
                    "account_id": r["account_id"],
                    "account": r["account"] or "Unknown",
                    "category_id": r["category_id"],
                    "category": r["category"] or "Uncategorized",
                    "amount": float(r["amount"] or 0.0),
                    "due_date": due_date,
                    "days_diff": diff,
                    "next_idx": rel_idx,
                    "frq": frq,
                    "cfrq": cfrq,
                    "frequency": format_frequency(frq, cfrq),
                    "last_paid": str(r["last_paid_date"] or "")[:10] if "last_paid_date" in r.keys() and r["last_paid_date"] else "-",
                })
        else:
            if -30 <= diff <= horizon_days:
                bills.append({
                    "head_id": r["head_id"],
                    "payee_id": r["payee_id"],
                    "payee": r["payee"] or "Unknown",
                    "account_id": r["account_id"],
                    "account": r["account"] or "Unknown",
                    "category_id": r["category_id"],
                    "category": r["category"] or "Uncategorized",
                    "amount": float(r["amount"] or 0.0),
                    "due_date": due_date,
                    "days_diff": diff,
                    "next_idx": rel_idx,
                    "frq": frq,
                    "cfrq": cfrq,
                    "frequency": format_frequency(frq, cfrq),
                    "last_paid": str(r["last_paid_date"] or "")[:10] if "last_paid_date" in r.keys() and r["last_paid_date"] else "-",
                })

    if sleeping:
        bills.sort(key=lambda x: x["due_date"], reverse=True)
    else:
        bills.sort(key=lambda x: x["due_date"])
    return bills


def cmd_bills(args, conn: sqlite3.Connection):
    horizon = args.days
    is_sleeping = getattr(args, "sleeping", False)
    bills = get_upcoming_bills_list(conn, horizon_days=horizon, sleeping=is_sleeping)

    for b in bills:
        diff = b["days_diff"]
        if diff < 0:
            b["status_label"] = f"OVERDUE ({abs(diff)}d ago)"
        elif diff == 0:
            b["status_label"] = "DUE TODAY"
        elif diff == 1:
            b["status_label"] = "Tomorrow"
        else:
            b["status_label"] = f"in {diff} days"
        b["payee"] = b["payee"][:24]
        b["account"] = b["account"][:16]

    default_acct_name = load_config().get("defaults", {}).get("account", "checking")
    default_acct = resolve_account(conn, default_acct_name, required=False)
    current_bal = get_anchored_account_balance(conn, default_acct["id"]) if default_acct else 0.0

    ctx = {
        "is_sleeping": is_sleeping,
        "horizon": horizon,
        "bills": bills,
        "current_bal": current_bal,
        "cli_style": getattr(args, "format", None),
    }
    print(render_template("bills.tpl", ctx))


def cmd_skip_bill(args, conn: sqlite3.Connection):
    bill_arg = str(args.bill_id).strip()
    cur = conn.cursor()

    clean_id = bill_arg.lstrip("#")
    if clean_id.isdigit():
        cur.execute(queries.GET_BILL_BY_ID, (int(clean_id),))
    else:
        cur.execute(queries.GET_BILL_BY_PAYEE_SEARCH, (f"%{bill_arg.lower()}%",))

    rows = cur.fetchall()
    if not rows:
        print(f"Error: No recurring bill found matching '{bill_arg}'.", file=sys.stderr)
        return
    if len(rows) > 1 and not clean_id.isdigit():
        print(f"Multiple bills match '{bill_arg}'. Please specify by numeric bill ID:", file=sys.stderr)
        for r in rows:
            print(f"  #{r['head_id']}: {r['payee']} ({r['account']})")
        return

    bill = rows[0]
    head_id = bill["head_id"]
    head_unpaid = int(bill["head_next_unpaid"]) if str(bill["head_next_unpaid"]).isdigit() else 0
    latest_base = int(bill["revision_base_iinst"]) if str(bill["revision_base_iinst"]).isdigit() else 0

    if bill["latest_hbill"] == bill["head_id"]:
        rel_idx = head_unpaid
    else:
        rel_idx = head_unpaid - latest_base
        if rel_idx < 0:
            rel_idx = int(bill["latest_next_unpaid"]) if str(bill["latest_next_unpaid"]).isdigit() else 0

    start_dt = parse_date(bill["revision_dt"])
    frq = int(bill["frq"])
    cfrq = float(bill["cFrqInst"]) if bill["cFrqInst"] else 1.0

    count = getattr(args, "count", 1) or 1
    curr_due = calculate_due_date(start_dt, frq, cfrq, rel_idx)
    next_due = calculate_due_date(start_dt, frq, cfrq, rel_idx + count)
    amt_str = f"£{float(bill['amount']):,.2f}"

    print(f"\nRecurring Bill #{head_id}: {bill['payee']} ({bill['account']})")
    print(f"  Current Occurrence Due: {curr_due} ({amt_str})")
    print(f"  Next Occurrence Due:    {next_due}")

    if getattr(args, "yes", False):
        confirm = "y"
    else:
        confirm = input(f"\nSkip {count} occurrence(s) for {curr_due} and advance schedule to {next_due}? [y/N]: ").strip().lower()

    if confirm in ("y", "yes"):
        cur.execute(queries.ADVANCE_BILL_SCHEDULE, (count, head_unpaid + count - 1, head_id))
        conn.commit()
        print(f"Successfully skipped {count} occurrence(s) for '{bill['payee']}'!")
        print(f"Schedule advanced. Next due date is now {next_due}.\n")
    else:
        print("Skip cancelled. No changes made.\n")


# =====================================================================
# COMMAND: register
# =====================================================================
def cmd_register(args, conn: sqlite3.Connection):
    account_arg = (getattr(args, "account", None) or "all").strip().lower()
    target_date = getattr(args, "target_date", None)
    date_from = getattr(args, "date_from", None)
    date_to = getattr(args, "date_to", None)
    days = getattr(args, "days", None)

    if re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$", account_arg):
        target_date = account_arg.replace("/", "-")
        parts = target_date.split("-")
        target_date = f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
        account_arg = "all"

    is_all = account_arg in ("all", "*", "")

    cur = conn.cursor()
    params = []
    where_clauses = ["is_valid_ledger = 1"]
    acct = None

    if not is_all:
        acct = resolve_account(conn, account_arg)
        where_clauses.append("account_id = ?")
        params.append(acct["id"])

    if target_date:
        t_clean = target_date.strip().lower()
        if t_clean in ("today", "now"):
            target_date = datetime.date.today().isoformat()
        elif t_clean in ("yesterday",):
            target_date = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        else:
            parts = t_clean.replace("/", "-").split("-")
            if len(parts) == 3:
                target_date = f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
        where_clauses.append("tx_date = ?")
        params.append(target_date)
    elif date_from or date_to:
        if date_from:
            where_clauses.append("tx_date >= ?")
            params.append(date_from)
        if date_to:
            where_clauses.append("tx_date <= ?")
            params.append(date_to)
    elif days is not None:
        today_str = datetime.date.today().isoformat()
        cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
        where_clauses.append("tx_date >= ?")
        params.append(cutoff)
        where_clauses.append("tx_date <= ?")
        params.append(today_str)

    search_q = getattr(args, "search", None)
    if search_q:
        q_clean = search_q.strip().replace("£", "").replace(",", "")
        try:
            num_val = abs(float(q_clean))
            where_clauses.append("""
                (ROUND(ABS(amount), 2) = ? OR tx_id IN (
                    SELECT sp.htrnParent FROM TRN_SPLIT sp JOIN TRN ct ON sp.htrn = ct.htrn
                    WHERE ROUND(ABS(CAST(ct.amt AS REAL)), 2) = ?
                ))
            """)
            params.extend([num_val, num_val])
        except ValueError:
            q_like = f"%{search_q.strip().lower()}%"
            where_clauses.append("""
                (
                    LOWER(payee) LIKE ? OR LOWER(category) LIKE ? OR LOWER(memo) LIKE ?
                    OR tx_id IN (
                        SELECT sp.htrnParent FROM TRN_SPLIT sp JOIN TRN ct ON sp.htrn = ct.htrn
                        LEFT JOIN CAT c ON ct.hcat = c.hcat
                        WHERE LOWER(ct.mMemo) LIKE ? OR LOWER(COALESCE(c.szFull, '')) LIKE ?
                    )
                )
            """)
            params.extend([q_like, q_like, q_like, q_like, q_like])

    where_str = f"WHERE {' AND '.join(where_clauses)}"
    if target_date or date_from or date_to or days is not None or search_q:
        limit_val = args.limit if args.limit != 20 else 500
    else:
        limit_val = args.limit

    query = f"""
    SELECT 
        tx_id AS id,
        tx_date AS dt,
        account,
        account_name,
        payee,
        category,
        amount,
        cleared_status AS cs,
        memo
    FROM v_transactions
    {where_str}
    ORDER BY tx_date DESC, tx_id DESC
    LIMIT ?
    """
    params.append(limit_val)
    cur.execute(query, params)
    rows = [dict(r) for r in reversed(cur.fetchall())]

    if target_date:
        date_label = f"Date: {target_date}"
    elif date_from or date_to:
        date_label = f"From {date_from or 'start'} to {date_to or 'today'}"
    elif days:
        date_label = f"Last {days} Days"
    else:
        date_label = None

    if search_q:
        date_label = f"Matching '{search_q}'" + (f", {date_label}" if date_label else "")

    if is_all:
        scope_str = f"All Accounts ({date_label})" if date_label else "All Accounts (Recent Activity)"
    else:
        total_bal = get_anchored_account_balance(conn, acct["id"])
        scope_str = f"{date_label} (Current Balance: £{total_bal:,.2f})" if date_label else f"Current Balance: £{total_bal:,.2f}"

    ctx = {
        "is_all": is_all,
        "acct": acct,
        "scope_str": scope_str,
        "transactions": rows,
        "cli_style": getattr(args, "format", None),
    }
    print(render_template("register.tpl", ctx))


# =====================================================================
# COMMAND: add (With Bill Match Confirmation)
# =====================================================================
def setup_new_recurring_bill(
    conn: sqlite3.Connection,
    acct: dict,
    payee: str,
    amount: float,
    start_date: datetime.date,
    category: str,
) -> tuple[int, int]:
    print("\n--- Setup New Recurring Bill Schedule ---")
    print("Frequency options: 1=Daily, 2=Weekly, 3=Monthly (default), 4=Yearly, 5=Bi-Weekly")
    frq_in = input("Select frequency [3]: ").strip() or "3"
    frq = int(frq_in) if frq_in.isdigit() and int(frq_in) in (1, 2, 3, 4, 5) else 3

    cur = conn.cursor()
    payee_id = get_or_create_payee(conn, payee)
    cat_id = get_category_id(conn, category)

    cur.execute("SELECT COALESCE(MAX(CAST(htrn AS INTEGER)), 0) + 1 FROM TRN")
    template_htrn = cur.fetchone()[0]

    cur.execute(
        """
        INSERT INTO TRN (htrn, hacct, dt, amt, lHpay, hcat, cs, fUpdated, dtSerial)
        VALUES (?, ?, ?, ?, ?, ?, 0, 1, datetime('now'))
        """,
        (template_htrn, acct["id"], f"{start_date.isoformat()} 00:00:00", f"{amount:.4f}", payee_id, cat_id),
    )

    cur.execute("SELECT COALESCE(MAX(CAST(hbill AS INTEGER)), 0) + 1 FROM BILL")
    new_hbill = cur.fetchone()[0]

    cur.execute(
        """
        INSERT INTO BILL (
            hbill, st, frq, cFrqInst, dt, iinstNextUnpaid, dtMax, cInstMax,
            dtSerial, itrnLink, hbillHead, iinst, itrn, lHtrn, iinstLastSkipped,
            cDaysAutoEnter, fUpdated
        ) VALUES (
            ?, 1, ?, 1, ?, 1, '2200-12-31 00:00:00', -1,
            datetime('now'), -1, ?, 0, 0, ?, -1,
            -1, 1
        )
        """,
        (new_hbill, frq, f"{start_date.isoformat()} 00:00:00", new_hbill, template_htrn),
    )
    conn.commit()

    frq_names = {1: "Daily", 2: "Weekly", 3: "Monthly", 4: "Yearly", 5: "Bi-Weekly"}
    print(f"Created new recurring bill template #{new_hbill} for '{payee}' ({frq_names.get(frq, 'Monthly')})!\n")
    return new_hbill, 0


def find_matching_bill(conn: sqlite3.Connection, account_id: int, payee_name: str, tx_date: datetime.date, amount: float = None) -> dict | None:
    candidates = get_upcoming_bills_list(conn, horizon_days=60)
    payee_clean = payee_name.lower().strip()

    matches = []
    for b in candidates:
        if b["account_id"] != account_id:
            continue
        candidate_payee = b["payee"].lower()
        if payee_clean in candidate_payee or candidate_payee in payee_clean:
            diff = abs((b["due_date"] - tx_date).days)
            amt_match = False
            if amount is not None:
                amt_match = abs(abs(b["amount"]) - abs(amount)) < 0.05

            if amt_match and diff <= 35:
                matches.append((0 if amt_match else 1, diff, b))
            elif diff <= 14:
                matches.append((0 if amt_match else 1, diff, b))

    if matches:
        matches.sort(key=lambda m: (m[0], m[1]))
        return matches[0][2]
    return None


def get_or_create_payee(conn: sqlite3.Connection, payee_name: str) -> int:
    clean = payee_name.strip()
    clean_id = clean.lstrip("#")
    cur = conn.cursor()

    if clean_id.isdigit():
        cur.execute("SELECT hpay FROM PAY WHERE hpay = ?", (int(clean_id),))
        row = cur.fetchone()
        if row:
            return row[0]

    cur.execute("SELECT hpay FROM PAY WHERE LOWER(szFull) = ?", (clean.lower(),))
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute("SELECT COALESCE(MAX(CAST(hpay AS INTEGER)), 0) + 1 FROM PAY")
    new_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO PAY (hpay, szFull, fHidden, fVendor, fCust, fNoRecurringBill, fAutofillMemo, fUpdated, fGlobal, fLocal) "
        "VALUES (?, ?, 0, 0, 0, 0, 0, 1, 0, 0)",
        (new_id, clean),
    )
    return new_id


def get_category_id(conn: sqlite3.Connection, category_name: str) -> int:
    if not category_name or not str(category_name).strip():
        return 0
    clean = str(category_name).strip()
    cur = conn.cursor()

    clean_id = clean.lstrip("#")
    if clean_id.isdigit():
        cur.execute("SELECT hcat FROM CAT WHERE hcat = ?", (int(clean_id),))
        row = cur.fetchone()
        if row:
            return row[0]

    if ":" in clean:
        parent_part, child_part = [p.strip().lower() for p in clean.split(":", 1)]
        cur.execute(
            """
            SELECT c.hcat 
            FROM CAT c
            JOIN CAT p ON c.hcatParent = p.hcat
            WHERE LOWER(c.szFull) = ? AND LOWER(p.szFull) = ?
            """,
            (child_part, parent_part),
        )
        row = cur.fetchone()
        if row:
            return row[0]

    cur.execute(
        """
        SELECT c.hcat, COUNT(t.htrn) AS usage_count
        FROM CAT c
        LEFT JOIN TRN t ON c.hcat = t.hcat
        WHERE LOWER(c.szFull) = ?
        GROUP BY c.hcat
        ORDER BY usage_count DESC
        """,
        (clean.lower(),),
    )
    rows = cur.fetchall()
    if rows:
        return rows[0][0]

    cur.execute(
        """
        SELECT c.hcat, COUNT(t.htrn) AS usage_count
        FROM CAT c
        LEFT JOIN TRN t ON c.hcat = t.hcat
        WHERE LOWER(c.szFull) LIKE ?
        GROUP BY c.hcat
        ORDER BY usage_count DESC
        """,
        (f"%{clean.lower()}%",),
    )
    rows = cur.fetchall()
    return rows[0][0] if rows else 0


def create_add_line_parser():
    parser = argparse.ArgumentParser(prog="qashbocks add", add_help=False)
    parser.add_argument("-a", "--account", type=str, default="checking")
    parser.add_argument("-t", "--to-account", "--to", dest="to_account", type=str, default=None)
    parser.add_argument("-p", "--payee", type=str, default="")
    parser.add_argument("-m", "--amount", "--amt", type=float, default=None)
    parser.add_argument("-c", "--category", type=str, default="")
    parser.add_argument("-d", "--date", type=str, default=None)
    parser.add_argument("--memo", type=str, default="")
    parser.add_argument("--income", action="store_true")
    parser.add_argument("--match-bill", dest="match_bill", action="store_true", default=None)
    parser.add_argument("--no-match-bill", dest="match_bill", action="store_false")
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("-y", "--yes", action="store_true")
    return parser


def execute_add_record(
    conn: sqlite3.Connection,
    account: str = "checking",
    payee: str = "",
    amount: float = None,
    date_str: str = None,
    category: str = "",
    memo: str = "",
    income: bool = False,
    to_account: str = None,
    match_bill: bool = None,
    bill_id: int = None,
    quiet: bool = False,
    auto_yes: bool = False,
    commit: bool = True,
) -> tuple[int, str]:
    cur = conn.cursor()

    if not date_str:
        tx_date = datetime.date.today()
    else:
        tx_date = parse_date(date_str)
        if not tx_date:
            raise ValueError(f"Invalid date format: {date_str}")

    acct = resolve_account(conn, account)

    # Transfer Mode
    if not to_account and payee:
        ident_clean = payee.strip()
        is_explicit_xfer = False
        target_name = ident_clean

        if target_name.startswith("[") and target_name.endswith("]"):
            is_explicit_xfer = True
            target_name = target_name[1:-1].strip()
        elif target_name.startswith("->"):
            is_explicit_xfer = True
            target_name = target_name[2:].strip()
        elif target_name.lower().startswith("to:"):
            is_explicit_xfer = True
            target_name = target_name[3:].strip()

        if is_explicit_xfer:
            cand_acct = resolve_account(conn, target_name, required=False)
            if cand_acct and cand_acct["id"] != acct["id"]:
                to_account = target_name
        else:
            clean_cand = ident_clean.lstrip("#")
            if not clean_cand.isdigit():
                ident_lower = ident_clean.lower()
                cfg_aliases = load_config().get("aliases", {})
                known_aliases = {k.lower(): v.lower() for k, v in ACCOUNT_ALIASES.items()}
                for k, v in cfg_aliases.items():
                    known_aliases[k.lower()] = v.lower()

                if ident_lower in known_aliases or ident_lower in known_aliases.values():
                    cur.execute("SELECT hpay FROM PAY WHERE LOWER(szFull) = ?", (ident_lower,))
                    if not cur.fetchone():
                        cand_acct = resolve_account(conn, payee, required=False)
                        if cand_acct and cand_acct["id"] != acct["id"]:
                            to_account = payee

    if to_account:
        dest_acct = resolve_account(conn, to_account)
        if acct["id"] == dest_acct["id"]:
            raise ValueError("Source and destination accounts for transfer cannot be identical.")
        if amount is None or amount == 0:
            raise ValueError("Transfer amount must be non-zero.")

        abs_amt = abs(amount)
        cur.execute("SELECT COALESCE(MAX(CAST(htrn AS INTEGER)), 0) FROM TRN")
        base_id = cur.fetchone()[0]
        htrn_out = base_id + 1
        htrn_in = base_id + 2
        dt_full = f"{tx_date.isoformat()} 00:00:00"

        cur.execute(
            """
            INSERT INTO TRN (htrn, hacct, hacctLink, dt, amt, mMemo, cs, fUpdated, dtSerial)
            VALUES (?, ?, ?, ?, ?, ?, 0, 1, datetime('now'))
            """,
            (htrn_out, acct["id"], dest_acct["id"], dt_full, f"{-abs_amt:.4f}", memo),
        )
        cur.execute(
            """
            INSERT INTO TRN (htrn, hacct, hacctLink, dt, amt, mMemo, cs, fUpdated, dtSerial)
            VALUES (?, ?, ?, ?, ?, ?, 0, 1, datetime('now'))
            """,
            (htrn_in, dest_acct["id"], acct["id"], dt_full, f"{abs_amt:.4f}", memo),
        )
        cur.execute("INSERT INTO TRN_XFER (htrnFrom, htrnLink) VALUES (?, ?)", (htrn_out, htrn_in))
        if commit:
            conn.commit()
        return htrn_out, f"Transfer: {acct['name']} -> {dest_acct['name']} (£{abs_amt:,.2f}) on {tx_date}"

    # Standard Transaction Mode
    if amount is None:
        raise ValueError("Amount is required for transaction.")
    if not payee:
        payee = "Other"

    final_amt = amount
    if not income and final_amt > 0:
        final_amt = -final_amt
    elif income and final_amt < 0:
        final_amt = abs(final_amt)

    matched_head_id = None
    matched_instance = -1

    if bill_id is not None:
        cur.execute(
            """
            SELECT b.hbill, b.iinstNextUnpaid, t.hcat, c.szFull
            FROM BILL b
            LEFT JOIN TRN t ON b.lHtrn = t.htrn
            LEFT JOIN CAT c ON t.hcat = c.hcat
            WHERE b.hbill = b.hbillHead AND b.hbill = ?
            """,
            (int(bill_id),),
        )
        brow = cur.fetchone()
        if brow:
            matched_head_id = brow[0]
            matched_instance = int(brow[1]) if brow[1] is not None else -1
            cur.execute(
                "UPDATE BILL SET iinstNextUnpaid = iinstNextUnpaid + 1, dtSerial = datetime('now') WHERE hbill = ?",
                (matched_head_id,),
            )
            if not category and brow[3]:
                category = brow[3]
        else:
            raise ValueError(f"Bill template #{bill_id} not found in database.")
    else:
        matched_bill = find_matching_bill(conn, acct["id"], payee, tx_date, amount=final_amt)
        if matched_bill:
            if match_bill is True or auto_yes:
                confirm = True
            elif match_bill is False or quiet:
                confirm = False
            else:
                expected_amt_str = f"£{abs(matched_bill['amount']):,.2f}"
                print("\n" + "=" * 65)
                print("  POTENTIAL UPCOMING BILL MATCH DETECTED:")
                print(f"    Payee:        {matched_bill['payee']}")
                print(f"    Account:      {matched_bill['account']}")
                print(f"    Due Date:     {matched_bill['due_date']} (scheduled)")
                print(f"    Expected:     {expected_amt_str}")
                print(f"    This Entry:   £{abs(final_amt):,.2f} on {tx_date}")
                print("=" * 65)
                ans = input("Confirm match with this scheduled bill? [y/N]: ").strip().lower()
                confirm = ans in ("y", "yes")

            if confirm:
                matched_head_id = matched_bill["head_id"]
                matched_instance = int(matched_bill["next_idx"]) if matched_bill["next_idx"] is not None else -1
                cur.execute(
                    "UPDATE BILL SET iinstNextUnpaid = iinstNextUnpaid + 1, dtSerial = datetime('now') WHERE hbill = ?",
                    (matched_head_id,),
                )
                if not category and matched_bill["category_id"]:
                    category = matched_bill["category"]
        else:
            if match_bill is True:
                if quiet:
                    print(f"  [Warning] No upcoming scheduled bill matches '{payee}' in {acct['name']}. Recording as standalone transaction.")
                else:
                    print(f"\nNotice: No upcoming scheduled bill matches '{payee}' in {acct['name']}.")
                    setup = input(f"Would you like to set up a new recurring bill template for '{payee}'? [y/N]: ").strip().lower()
                    if setup in ("y", "yes"):
                        matched_head_id, matched_instance = setup_new_recurring_bill(
                            conn, acct, payee, final_amt, tx_date, category
                        )

    cur.execute("SELECT COALESCE(MAX(CAST(htrn AS INTEGER)), 0) + 1 FROM TRN")
    new_htrn = cur.fetchone()[0]

    payee_id = get_or_create_payee(conn, payee)
    cat_id = get_category_id(conn, category)

    cur.execute(
        """
        INSERT INTO TRN (
            htrn, hacct, dt, amt, lHpay, hcat, mMemo, cs,
            hbillHead, iinst, fUpdated, dtSerial
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, 0,
            ?, ?, 1, datetime('now')
        )
        """,
        (
            new_htrn,
            acct["id"],
            f"{tx_date.isoformat()} 00:00:00",
            f"{final_amt:.4f}",
            payee_id,
            cat_id,
            memo,
            matched_head_id,
            matched_instance if matched_instance >= 0 else -1,
        ),
    )
    if commit:
        conn.commit()

    return new_htrn, f"{tx_date} | {payee} | £{final_amt:,.2f} ({acct['name']})"


def cmd_add_line_file(args, conn: sqlite3.Connection):
    file_path = args.file
    if file_path == "-":
        lines = sys.stdin.readlines()
        source_desc = "standard input"
    else:
        p = Path(file_path)
        if not p.exists() and (REPO_ROOT / file_path).exists():
            p = REPO_ROOT / file_path
        elif not p.exists():
            print(f"Error: Line file not found: {p}", file=sys.stderr)
            sys.exit(1)
        lines = p.read_text(encoding="utf-8").splitlines()
        source_desc = str(p)

    added_count = 0
    errors = 0
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        print("  [DRY RUN MODE ENABLED - Changes will not be committed to database]\n")

    print(f"\nProcessing transaction batch from {source_desc} ({len(lines)} lines)...")
    for idx, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("skip ") or line.startswith("qashbocks skip ") or line.startswith("money skip "):
            for pfx in ("qashbocks skip ", "money skip ", "skip "):
                if line.startswith(pfx):
                    clean_cmd = line[len(pfx):].strip()
                    break
            skip_tokens = shlex.split(clean_cmd)
            skip_id = None
            skip_count = 1
            for i, tok in enumerate(skip_tokens):
                if tok in ("-n", "--count") and i + 1 < len(skip_tokens):
                    try:
                        skip_count = int(skip_tokens[i + 1])
                    except ValueError:
                        pass
                elif not tok.startswith("-") and skip_id is None:
                    skip_id = tok

            if skip_id:
                clean_b_id = skip_id.lstrip("#")
                cur = conn.cursor()
                if clean_b_id.isdigit():
                    cur.execute(
                        """
                        SELECT b.hbill, b.iinstNextUnpaid, p.szFull, a.szFull 
                        FROM BILL b 
                        JOIN TRN t ON b.lHtrn = t.htrn 
                        JOIN ACCT a ON t.hacct = a.hacct 
                        LEFT JOIN PAY p ON t.lHpay = p.hpay 
                        WHERE b.hbill = b.hbillHead AND b.hbill = ?
                        """,
                        (int(clean_b_id),),
                    )
                else:
                    cur.execute(
                        """
                        SELECT b.hbill, b.iinstNextUnpaid, p.szFull, a.szFull 
                        FROM BILL b 
                        JOIN TRN t ON b.lHtrn = t.htrn 
                        JOIN ACCT a ON t.hacct = a.hacct 
                        LEFT JOIN PAY p ON t.lHpay = p.hpay 
                        WHERE b.hbill = b.hbillHead AND LOWER(p.szFull) LIKE ?
                        """,
                        (f"%{clean_b_id.lower()}%",),
                    )
                brow = cur.fetchone()
                if brow:
                    curr_idx = int(brow[1])
                    if not dry_run:
                        cur.execute(
                            """
                            UPDATE BILL 
                            SET iinstNextUnpaid = iinstNextUnpaid + ?, 
                                iinstLastSkipped = ?, 
                                dtSerial = datetime('now') 
                            WHERE hbill = ?
                            """,
                            (skip_count, curr_idx + skip_count - 1, brow[0]),
                        )
                        conn.commit()
                    prefix = "[SIMULATED] " if dry_run else ""
                    print(f"  [{added_count + 1}] Line {idx}: {prefix}Skip Bill #{brow[0]} ({brow[2]}) -> Advanced to instance {curr_idx + skip_count}")
                    added_count += 1
                else:
                    errors += 1
                    print(f"  [Error] Line {idx}: Bill '{skip_id}' not found.", file=sys.stderr)
            continue

        for pfx in ("qashbocks add ", "money add ", "add "):
            if line.startswith(pfx):
                line = line[len(pfx):].strip()
                break

        is_csv = ("," in line) and not line.startswith("-")

        if is_csv:
            try:
                reader = csv.reader(io.StringIO(line))
                fields = next(reader)
                if len(fields) < 4:
                    raise ValueError(f"CSV format requires at least 4 columns: [date],account,payee,amount[,category,remark,recur]")

                date_raw = fields[0].strip() if len(fields) > 0 else ""
                alias_raw = fields[1].strip() if len(fields) > 1 else ""
                payee_raw = fields[2].strip() if len(fields) > 2 else ""
                amt_raw = fields[3].strip() if len(fields) > 3 else ""
                cat_raw = fields[4].strip() if len(fields) > 4 else ""
                remark_raw = fields[5].strip() if len(fields) > 5 else ""
                recur_raw = fields[6].strip() if len(fields) > 6 else ""

                if not alias_raw:
                    raise ValueError("Account alias cannot be empty.")
                if not amt_raw:
                    raise ValueError("Amount cannot be empty.")

                if not date_raw:
                    tx_date = datetime.date.today()
                elif len(date_raw) == 8 and date_raw.isdigit():
                    tx_date = datetime.date(int(date_raw[:4]), int(date_raw[4:6]), int(date_raw[6:8]))
                elif "-" in date_raw:
                    parts = [int(p) for p in date_raw.split("-")]
                    tx_date = datetime.date(parts[0], parts[1], parts[2])
                else:
                    raise ValueError(f"Invalid date format: '{date_raw}'. Expected YYYYMMDD or YYYY-MM-DD.")

                clean_amt = amt_raw.replace("£", "").replace("€", "").replace("$", "").replace(",", "").strip()
                is_income = clean_amt.startswith("+") or recur_raw.upper() in ("INCOME", "+", "DEPOSIT")
                amount_val = float(clean_amt.lstrip("+"))

                match_bill = None
                direct_bill_id = None
                clean_recur = recur_raw.lstrip("#").strip()
                if clean_recur.isdigit():
                    direct_bill_id = int(clean_recur)
                    match_bill = True
                elif clean_recur.upper() in ("RECUR", "MATCH", "BILL", "TRUE", "YES", "Y"):
                    match_bill = True
                elif clean_recur.upper() in ("NO", "NOMATCH", "STANDALONE", "FALSE", "N"):
                    match_bill = False

                htrn, summary = execute_add_record(
                    conn,
                    account=alias_raw,
                    payee=payee_raw,
                    amount=amount_val,
                    date_str=tx_date.isoformat(),
                    category=cat_raw,
                    memo=remark_raw,
                    income=is_income,
                    match_bill=match_bill,
                    bill_id=direct_bill_id,
                    quiet=True,
                    auto_yes=True,
                    commit=not dry_run,
                )
                added_count += 1
                prefix = "[SIMULATED] " if dry_run else ""
                print(f"  [{added_count}] Line {idx}: {prefix}Tx #{htrn} -> {summary}")
            except Exception as e:
                errors += 1
                print(f"  [Error] Line {idx} ('{line}'): {e}", file=sys.stderr)
            continue

        try:
            line_parser = create_add_line_parser()
            line_args = line_parser.parse_args(shlex.split(line))
            auto_yes = getattr(args, "yes", False) or line_args.yes
            quiet = getattr(args, "quiet", False) or line_args.quiet or auto_yes

            htrn, summary = execute_add_record(
                conn,
                account=line_args.account,
                payee=line_args.payee,
                amount=line_args.amount,
                date_str=line_args.date,
                category=line_args.category,
                memo=line_args.memo,
                income=line_args.income,
                to_account=line_args.to_account,
                match_bill=line_args.match_bill,
                quiet=quiet,
                auto_yes=auto_yes,
                commit=not dry_run,
            )
            added_count += 1
            prefix = "[SIMULATED] " if dry_run else ""
            print(f"  [{added_count}] Line {idx}: {prefix}Tx #{htrn} -> {summary}")
        except Exception as e:
            errors += 1
            print(f"  [Error] Line {idx} ('{line}'): {e}", file=sys.stderr)

    if dry_run:
        conn.rollback()
        print(f"\nDry run complete: {added_count} transaction(s) simulated, {errors} error(s). 0 changes committed.\n")
    else:
        conn.commit()
        print(f"\nBatch complete: {added_count} transaction(s) recorded, {errors} error(s).\n")


def cmd_add(args, conn: sqlite3.Connection):
    if getattr(args, "file", None):
        cmd_add_line_file(args, conn)
        return

    if getattr(args, "to_account", None):
        acct_in = args.account or (input("From Account [checking]: ").strip() or "checking")
        to_in = args.to_account
        amt = args.amount
        if amt is None:
            amt_str = input("Transfer Amount: £").strip()
            amt = float(amt_str)
        date_str = args.date or (datetime.date.today().isoformat() if args.quiet else (input(f"Date [{datetime.date.today()}]: ").strip() or datetime.date.today().isoformat()))
        memo = args.memo or ""
        htrn, summary = execute_add_record(
            conn,
            account=acct_in,
            amount=amt,
            date_str=date_str,
            memo=memo,
            to_account=to_in,
            quiet=args.quiet,
            auto_yes=getattr(args, "yes", False),
        )
        print(f"\n{summary}\n")
        return

    acct_in = args.account or (input("Account [checking]: ").strip() or "checking")
    date_str = args.date
    if not date_str:
        today_str = datetime.date.today().isoformat()
        date_str = today_str if args.quiet else (input(f"Date [{today_str}]: ").strip() or today_str)

    payee = args.payee or ("Other" if args.quiet else input("Payee: ").strip())
    if not payee:
        print("Error: Payee is required.", file=sys.stderr)
        return

    amt = args.amount
    if amt is None:
        amt_str = input("Amount (e.g. 45.50 or -45.50): ").strip()
        try:
            amt = float(amt_str)
        except ValueError:
            print("Error: Invalid amount.", file=sys.stderr)
            return

    category = args.category or ("" if args.quiet else input("Category (optional): ").strip())
    memo = args.memo or ""

    htrn, summary = execute_add_record(
        conn,
        account=acct_in,
        payee=payee,
        amount=amt,
        date_str=date_str,
        category=category,
        memo=memo,
        income=args.income,
        match_bill=args.match_bill,
        quiet=args.quiet,
        auto_yes=getattr(args, "yes", False),
    )
    print(f"\nTransaction #{htrn} recorded successfully!")
    print(f"  {summary}\n")


# =====================================================================
# COMMAND: reconcile (Monthly Statement Run)
# =====================================================================
def cmd_reconcile(args, conn: sqlite3.Connection):
    acct = resolve_account(conn, args.account)
    cur = conn.cursor()

    cur.execute("SELECT amtEndRec, dtEndRec FROM ACCT WHERE hacct = ?", (acct["id"],))
    last_rec = cur.fetchone()
    last_bal = float(last_rec["amtEndRec"] or 0.0)
    last_dt = str(last_rec["dtEndRec"] or "None")[:10]

    print(f"\n=======================================================")
    print(f"  Monthly Reconciliation Wizard: {acct['name']}")
    print(f"  Previous Statement: {last_dt} (Ending Balance: £{last_bal:,.2f})")
    print(f"=======================================================\n")

    stmt_date_str = input("New Statement Ending Date (YYYY-MM-DD): ").strip()
    stmt_date = parse_date(stmt_date_str)
    if not stmt_date:
        print("Error: Invalid date.", file=sys.stderr)
        return

    stmt_bal_str = input("New Statement Ending Balance: £").strip()
    try:
        stmt_bal = float(stmt_bal_str)
    except ValueError:
        print("Error: Invalid balance.", file=sys.stderr)
        return

    query = """
    SELECT 
        t.htrn,
        SUBSTR(t.dt, 1, 10) AS dt,
        COALESCE(p.szFull, 'Other') AS payee,
        CAST(t.amt AS REAL) AS amount,
        t.cs
    FROM TRN t
    LEFT JOIN PAY p ON t.lHpay = p.hpay
    WHERE t.hacct = ?
      AND t.cs = 0
      AND t.dt <= ?
    ORDER BY t.dt ASC, t.htrn ASC
    """
    cur.execute(query, (acct["id"], f"{stmt_date.isoformat()} 23:59:59"))
    items = [dict(r) for r in cur.fetchall()]

    if not items:
        print("No uncleared transactions found for this period.")
        return

    cleared_ids = set()

    while True:
        cleared_sum = sum(item["amount"] for item in items if item["htrn"] in cleared_ids)
        calculated_ending = last_bal + cleared_sum
        diff = stmt_bal - calculated_ending

        print(f"\n--- Uncleared Transactions (up to {stmt_date}) ---")
        print(f"{'#':>3} | {'ID':>6} | {'Date':<10} | {'Payee':<24} | {'Amount':>11} | {'Status'}")
        print("-" * 68)
        for idx, itm in enumerate(items, 1):
            is_c = itm["htrn"] in cleared_ids
            status_mark = "[X] Cleared" if is_c else "[ ] Uncleared"
            amt_str = f"£{itm['amount']:,.2f}"
            print(f"{idx:>3} | {itm['htrn']:>6} | {itm['dt']:<10} | {itm['payee'][:24]:<24} | {amt_str:>11} | {status_mark}")

        print("-" * 68)
        print(f"Starting Reconciled Balance:   £{last_bal:,.2f}")
        print(f"Cleared Items Total ({len(cleared_ids)} items): £{cleared_sum:,.2f}")
        print(f"Calculated Ending Balance:     £{calculated_ending:,.2f}")
        print(f"Statement Ending Balance:      £{stmt_bal:,.2f}")
        print(f"Difference:                    £{diff:,.2f}")
        print("-" * 68)

        if abs(diff) < 0.001:
            print(">>> STATEMENT BALANCES PERFECTLY (Difference is £0.00)! <<<")

        cmd = input("\nEnter item numbers to toggle (e.g. '1', '1 2 3', 'all', 'finish', 'quit'): ").strip().lower()
        if cmd in ("q", "quit", "exit"):
            print("Reconciliation cancelled. No changes made.")
            return
        elif cmd == "finish":
            if abs(diff) >= 0.01:
                confirm = input("Warning: Difference is NOT zero. Commit anyway? [y/N]: ").strip().lower()
                if confirm != "y":
                    continue
            for htrn in cleared_ids:
                cur.execute(
                    "UPDATE TRN SET cs = 2, dtCleared = ?, dtSerial = datetime('now') WHERE htrn = ?",
                    (f"{stmt_date.isoformat()} 00:00:00", htrn),
                )
            cur.execute(
                "UPDATE ACCT SET amtOpenRec = amtEndRec, amtEndRec = ?, dtOpenRec = dtEndRec, dtEndRec = ?, dtSerial = datetime('now') WHERE hacct = ?",
                (f"{stmt_bal:.4f}", f"{stmt_date.isoformat()} 00:00:00", acct["id"]),
            )
            conn.commit()
            print(f"\nSuccess! Reconciled {len(cleared_ids)} transactions for {acct['name']}.")
            print(f"Account ending reconciled balance updated to £{stmt_bal:,.2f} on {stmt_date}.\n")
            return
        elif cmd == "all":
            cleared_ids = {itm["htrn"] for itm in items}
        else:
            parts = cmd.split()
            for p in parts:
                if p.isdigit():
                    num = int(p)
                    if 1 <= num <= len(items):
                        target_id = items[num - 1]["htrn"]
                        if target_id in cleared_ids:
                            cleared_ids.remove(target_id)
                        else:
                            cleared_ids.add(target_id)


# =====================================================================
# COMMAND: init (Database Initialization)
# =====================================================================
def cmd_init(args):
    db_target = args.target or (REPO_ROOT / "data" / "qashbocks.db")
    if args.sample:
        db_target = args.target or (REPO_ROOT / "data" / "sample.db")

    print(f"\nInitializing Qashbocks ledger database at: {db_target}")
    init_db(db_target, populate_sample=args.sample)
    print(f"Initialization complete!\n")


# =====================================================================
# CLI ARGUMENT DISPATCHER
# =====================================================================
def main():
    parser = argparse.ArgumentParser(
        prog="qashbocks",
        description="Qashbocks: Fast, standalone personal finance command-line ledger.",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"Path to SQLite database (default: {DEFAULT_DB})")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    fmt_parser = argparse.ArgumentParser(add_help=False)
    fmt_group = fmt_parser.add_argument_group("formatting options")
    fmt_group.add_argument(
        "-f", "--format",
        choices=["plain", "unicode", "md", "markdown", "html"],
        default=None,
        help="Table formatting style (plain, unicode, md, html)",
    )
    fmt_group.add_argument("--plain", action="store_const", const="plain", dest="format", help="Force ASCII table dividers")
    fmt_group.add_argument("--unicode", action="store_const", const="unicode", dest="format", help="Force box-drawing borders")
    fmt_group.add_argument("--md", "--markdown", action="store_const", const="md", dest="format", help="Force GitHub Markdown table")
    fmt_group.add_argument("--html", action="store_const", const="html", dest="format", help="Force HTML table")

    # accounts
    p_accts = subparsers.add_parser(
        "accounts",
        aliases=["status", "ls"],
        parents=[fmt_parser],
        help="List active accounts and balances",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Display active accounts, statement-anchored balances, uncleared transaction counts, and liquid net worth.",
        epilog="""examples:
  qashbocks accounts           # Display active account balances and net worth
  qashbocks accounts --naive   # Display raw cumulative sum without statement anchor
""",
    )
    p_accts.add_argument("--naive", action="store_true", help="Display naive cumulative SUM(TRN) instead of Reconciled Statement Anchor")

    # bills
    p_bills = subparsers.add_parser(
        "bills",
        aliases=["upcoming"],
        parents=[fmt_parser],
        help="List upcoming bills and recurring schedules",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="List upcoming scheduled bills, direct debits, and standing orders within a forecast horizon.",
        epilog="""examples:
  qashbocks bills              # List bills due in the next 30 days
  qashbocks bills -d 60        # Extend forecast horizon to 60 days
  qashbocks bills -s           # List sleeping/dormant schedules (> 30 days overdue)
""",
    )
    p_bills.add_argument("-d", "--days", type=int, default=30, help="Forecast horizon in days (default: 30)")
    p_bills.add_argument("-s", "--sleeping", action="store_true", help="List sleeping/dormant recurring bills (> 30 days overdue)")

    # register
    p_reg = subparsers.add_parser(
        "register",
        aliases=["reg", "activity", "recent"],
        parents=[fmt_parser],
        help="View transaction ledger or recent activity",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="View transaction register ledger with running balance, payees, categories, and reconciliation statuses (R, C, .).\nSupports filtering by specific date, date range, or relative days.",
        epilog="""examples:
  qashbocks reg checking             # View recent activity for Main Checking
  qashbocks reg --date 2026-11-01    # View all transactions on 2026-11-01 across all accounts
  qashbocks reg amex -n 15           # Show last 15 Amex transactions
  qashbocks reg all                  # View recent activity across all open accounts
""",
    )
    p_reg.add_argument("account", nargs="?", default="all", help="Account name or alias, or date (checking, savings, amex, visa, 'all', or YYYY-MM-DD)")
    p_reg.add_argument("-D", "--date", "--on", dest="target_date", type=str, default=None, help="Filter transactions on a specific date (YYYY-MM-DD, 'today', 'yesterday')")
    p_reg.add_argument("--from", "--since", dest="date_from", type=str, default=None, help="Start of date range (YYYY-MM-DD)")
    p_reg.add_argument("--to", "--until", dest="date_to", type=str, default=None, help="End of date range (YYYY-MM-DD)")
    p_reg.add_argument("-d", "--days", type=int, default=None, help="Show activity for the last N days (e.g. --days 3)")
    p_reg.add_argument("-s", "--search", type=str, default=None, help="Filter register transactions by text or amount")
    p_reg.add_argument("-n", "--limit", type=int, default=20, help="Number of transactions to display (default: 20)")

    # add
    p_add = subparsers.add_parser(
        "add",
        help="Record a new transaction with bill match confirmation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Record a new transaction, transfer between accounts, or ingest a batch line file.\nAutomatically checks upcoming scheduled bills for matches by payee and amount.",
        epilog="""examples:
  # Record an expense:
  qashbocks add -a checking -p "Waitrose" -m 24.50 -c "Food:Groceries"

  # Record an account transfer:
  qashbocks add -a checking -t cash -m 50.00 --memo "ATM Cash withdrawal"

  # Ingest a batch file:
  qashbocks add -f batch.csv -y
""",
    )
    p_add.add_argument("-a", "--account", type=str, help="Account alias (checking, savings, amex, visa, cash)")
    p_add.add_argument("-t", "--to-account", "--to", dest="to_account", type=str, default=None, help="Target account for transfer (creates linked pair)")
    p_add.add_argument("-p", "--payee", type=str, help="Payee name")
    p_add.add_argument("-m", "--amount", "--amt", type=float, help="Transaction amount (e.g. 14.50)")
    p_add.add_argument("-c", "--category", type=str, default="", help="Category name")
    p_add.add_argument("-d", "--date", type=str, help="Transaction date (YYYY-MM-DD)")
    p_add.add_argument("--memo", type=str, default="", help="Memo note")
    p_add.add_argument("--income", action="store_true", help="Record as income/deposit rather than expense")
    p_add.add_argument("--match-bill", dest="match_bill", action="store_true", default=None, help="Explicitly attempt match with upcoming scheduled bill")
    p_add.add_argument("--no-match-bill", dest="match_bill", action="store_false", help="Do not match with upcoming bill")
    p_add.add_argument("-f", "--file", type=str, default=None, help="Read transactions from a line file (or '-' for stdin)")
    p_add.add_argument("-y", "--yes", action="store_true", help="Automatically confirm bill matches / prompts")
    p_add.add_argument("-q", "--quiet", action="store_true", help="Non-interactive quiet mode")
    p_add.add_argument("-n", "--dry-run", action="store_true", help="Simulate transaction ingestion without committing to database")

    # alias
    p_alias = subparsers.add_parser(
        "alias",
        help="List or configure account aliases",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="View or manage short human-readable aliases for accounts in config.toml.",
        epilog="""examples:
  qashbocks alias ls           # List all configured account aliases
  qashbocks alias set main 1   # Set alias 'main' for Account #1
  qashbocks alias rm main      # Remove alias 'main'
""",
    )
    p_alias.add_argument("action", nargs="?", choices=["ls", "list", "set", "rm", "remove", "unset", "del", "delete"], default="list")
    p_alias.add_argument("alias", nargs="?", help="Short alias name")
    p_alias.add_argument("target", nargs="?", help="Target account name, ID (#1), or alias")

    # reconcile
    p_rec = subparsers.add_parser(
        "reconcile",
        help="Monthly statement reconciliation wizard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Interactive wizard to reconcile monthly bank or credit card statements against register transactions.",
        epilog="""examples:
  qashbocks reconcile checking  # Reconcile Main Checking statement
  qashbocks reconcile amex      # Reconcile Amex credit card statement
""",
    )
    p_rec.add_argument("account", type=str, help="Account name or alias (checking, amex, visa, savings)")

    # skip
    p_skip = subparsers.add_parser(
        "skip",
        help="Skip the upcoming occurrence of a recurring bill without deleting schedule",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Advances the unpaid instance counter of a recurring bill without creating a transaction ledger entry.",
        epilog="""examples:
  qashbocks skip 1            # Skip next occurrence of Bill #1
  qashbocks skip "Council"    # Skip next occurrence of Council Tax bill
""",
    )
    p_skip.add_argument("bill_id", type=str, help="Bill ID (e.g. 1 or #1) or payee name")
    p_skip.add_argument("-y", "--yes", action="store_true", help="Automatically confirm skip")
    p_skip.add_argument("-n", "--count", type=int, default=1, help="Number of occurrences to skip (default: 1)")

    # forecast / report / chart
    p_fc = subparsers.add_parser(
        "forecast",
        aliases=["report", "chart"],
        help="Balance forecast graph and cash flow projection ('Next Quarter')",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Projects future account balances and cash flows using active recurring bills.\nCan render terminal ANSI charts or generate interactive HTML dashboards.",
        epilog="""examples:
  qashbocks forecast -a checking -d 90        # 90-day cash flow projection for Checking account
  qashbocks forecast --html reports/q4.html   # Export self-contained interactive SVG/HTML report
""",
    )
    p_fc.add_argument("-a", "--account", type=str, default="checking", help="Account alias or name (default: checking)")
    p_fc.add_argument("-d", "--days", type=int, default=90, help="Forecast horizon in days (default: 90 / Next Quarter)")
    p_fc.add_argument("--html", type=Path, nargs="?", const=Path("reports/next_quarter.html"), default=None, help="Export interactive HTML/SVG report (default: reports/next_quarter.html)")
    p_fc.add_argument("--no-income", dest="income", action="store_false", default=True, help="Exclude regular income/salary from projection")
    p_fc.add_argument("--naive-sum", dest="reconciled", action="store_false", default=True, help="Use naive cumulative SUM(TRN) instead of Reconciled Statement Anchor")

    # map / maps
    p_map = subparsers.add_parser(
        "map",
        aliases=["maps"],
        help="Display or generate unambiguous ID-name maps (categories, accounts, bills, payees)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Display or export canonical ID-to-name lookup tables for categories, accounts, bills, or payees.",
        epilog="""examples:
  qashbocks map categories     # Directory of all categories with IDs
  qashbocks map bills -s Gym   # Find bill ID for Gym Membership
  qashbocks map accounts       # Complete accounts directory with IDs and aliases
""",
    )
    p_map.add_argument("domain", nargs="?", choices=["categories", "cats", "accounts", "accts", "bills", "payees", "generate", "all"], default="categories", help="Map domain (default: categories)")
    p_map.add_argument("-a", "--ambiguous", action="store_true", help="Filter ambiguous categories")
    p_map.add_argument("-s", "--search", type=str, default=None, help="Search filter")
    p_map.add_argument("-f", "--format", choices=["table", "csv", "tsv", "json", "markdown"], default="table", help="Output format")
    p_map.add_argument("-o", "--output", type=Path, default=None, help="Save output to file")
    p_map.add_argument("--all", dest="include_all", action="store_true", help="Include closed accounts or generate all maps")

    # categories / cats
    p_cats = subparsers.add_parser(
        "categories",
        aliases=["cats"],
        help="List and search category ID-name maps",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="List and search category ID-name maps, resolving ambiguous leaf names.",
    )
    p_cats.add_argument("-a", "--ambiguous", action="store_true", help="Filter ambiguous categories")
    p_cats.add_argument("-s", "--search", type=str, default=None, help="Search filter")
    p_cats.add_argument("-f", "--format", choices=["table", "csv", "tsv", "json", "markdown"], default="table", help="Output format")
    p_cats.add_argument("-o", "--output", type=Path, default=None, help="Save output to file")

    # payees / pay
    p_pay = subparsers.add_parser(
        "payees",
        aliases=["pay"],
        help="List top payees and their IDs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="List top payees and their IDs, filtered by search query or usage frequency.",
    )
    p_pay.add_argument("-s", "--search", type=str, default=None, help="Search filter")
    p_pay.add_argument("-n", "--limit", type=int, default=50, help="Max number of payees")
    p_pay.add_argument("-f", "--format", choices=["table", "csv", "json", "markdown"], default="table", help="Output format")

    # inspect / show / trn
    p_insp = subparsers.add_parser(
        "inspect",
        aliases=["show", "trn"],
        parents=[fmt_parser],
        help="Inspect transaction register entry and recurring bill linkages",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Deep inspection of a specific register entry by transaction ID (htrn).\nReveals linked accounts, split items, paired transfers, direct foreign keys (hbillHead),\nand traces recurring bill templates and revisions.",
        epilog="""examples:
  qashbocks inspect 101      # Inspect transaction #101 and attached recurring bill
  qashbocks show 105         # Check split transaction #105
""",
    )
    p_insp.add_argument("htrn", type=str, help="Transaction ID (htrn, e.g. 101 or #101)")
    p_insp.add_argument("-a", "--all", "--all-bills", action="store_true", dest="all_bills", help="Show all candidate matching bills including terminated and dormant historical schedules")

    # currencies / crnc / currency
    p_crnc = subparsers.add_parser(
        "currencies",
        aliases=["crnc", "currency"],
        parents=[fmt_parser],
        help="Inspect currency definitions (CRNC), account denominations, and conversions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Inspects active currencies in CRNC, account currency denominations,\nand multi-currency transfers across accounts.",
        epilog="""examples:
  qashbocks currencies            # Show active and utilized currencies
  qashbocks currencies --transfers # Show multi-currency transfers between accounts
""",
    )
    p_crnc.add_argument("-a", "--all", action="store_true", help="Display all defined currencies")
    p_crnc.add_argument("--no-accounts", dest="no_accounts", action="store_true", help="Hide account denomination list")
    p_crnc.add_argument("--transfers", action="store_true", help="Show multi-currency transfers across accounts")
    p_crnc.add_argument("-n", "--limit", type=int, default=15, help="Max transfers to display")

    # recur / repeat
    p_recur = subparsers.add_parser(
        "recur",
        aliases=["repeat"],
        help="Convert an existing register transaction into a recurring bill schedule",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Converts an existing transaction into a recurring bill schedule.\nClones the transaction into a template record (with grftt=0x200000), inserts a master\nschedule into BILL, and retroactively links the original entry as instance 0.",
        epilog="""examples:
  qashbocks recur 101 -f monthly        # Convert transaction #101 into monthly bill
  qashbocks recur 101 -f monthly -n 12  # Set up a 12-instalment series
""",
    )
    p_recur.add_argument("htrn", type=str, help="Transaction ID (htrn, e.g. 101 or #101)")
    p_recur.add_argument("-f", "--frequency", type=str, default="monthly", help="Recurrence frequency (daily, weekly, monthly, yearly)")
    p_recur.add_argument("-n", "--count", type=int, default=-1, help="Total occurrences in series (-1 for indefinite)")
    p_recur.add_argument("-d", "--next-due", dest="next_due", type=str, default=None, help="Next scheduled due date (YYYY-MM-DD)")
    p_recur.add_argument("-y", "--yes", action="store_true", help="Non-interactive automatic confirmation")

    # tax / hmrc
    now = datetime.date.today()
    default_tax_year = now.year if (now.month, now.day) >= (4, 6) else now.year - 1
    p_tax = subparsers.add_parser(
        "tax",
        aliases=["hmrc"],
        parents=[fmt_parser],
        help="Generate HMRC-aligned tax calculation and refund projection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Generate HMRC-aligned tax calculation and refund projection for any tax year.\nAggregates taxable income, personal allowances, and tax bands.",
        epilog=f"""examples:
  qashbocks tax                # Current tax year ({default_tax_year}–{default_tax_year + 1}) projection
  qashbocks tax -y 2025        # Historical 2025–2026 tax calculation
""",
    )
    p_tax.add_argument("-y", "--year", type=int, default=default_tax_year, help=f"Tax year start (default: {default_tax_year} for {default_tax_year}–{default_tax_year + 1})")
    p_tax.add_argument("-o", "--output", type=Path, default=None, help="Save report to file")

    # search / find
    p_search = subparsers.add_parser(
        "search",
        aliases=["find"],
        parents=[fmt_parser],
        help="Search transactions across payees, categories, memos, and amounts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Fast search engine across all transactions, child splits, and transfers.\nMatches text across payee, category, memo, or numeric amounts.\nDefault searches all accounts; use -A / --active to restrict to active accounts.",
        epilog="""examples:
  qashbocks find "Water"              # Search by payee across all accounts
  qashbocks find "groceries"          # Search by memo keyword (including child splits)
  qashbocks find 45.00                # Search by exact transaction amount
  qashbocks find "Amazon" -A          # Search Amazon transactions in active accounts only
  qashbocks find "gym" --splits       # Display individual matching child split line items
""",
    )
    p_search.add_argument("query", nargs="?", default=None, help="Search term (text in payee/category/memo or numeric amount)")
    p_search.add_argument("-q", "--query", dest="named_query", type=str, default=None, help="Explicit search query string")
    p_search.add_argument("-A", "--active", action="store_true", help="Restrict search to active accounts only (default: all accounts)")
    p_search.add_argument("-a", "--account", type=str, default=None, help="Filter by specific account alias or name")
    p_search.add_argument("-p", "--payee", type=str, default=None, help="Filter specifically by payee name")
    p_search.add_argument("-c", "--category", type=str, default=None, help="Filter specifically by category name")
    p_search.add_argument("-m", "--memo", type=str, default=None, help="Filter specifically by memo text")
    p_search.add_argument("--min", type=float, default=None, help="Minimum amount")
    p_search.add_argument("--max", type=float, default=None, help="Maximum amount")
    p_search.add_argument("--from", "--since", dest="date_from", type=str, default=None, help="Start date (YYYY-MM-DD)")
    p_search.add_argument("--to", "--until", dest="date_to", type=str, default=None, help="End date (YYYY-MM-DD)")
    p_search.add_argument("-n", "--limit", type=int, default=50, help="Maximum results to return (default: 50, use 0 for unlimited)")
    p_search.add_argument("--splits", action="store_true", help="Display matching child split line items directly")

    # init
    p_init = subparsers.add_parser(
        "init",
        help="Initialize a new ledger database or demonstration database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Creates the complete SQLite schema, canonical reporting views, and performance indexes.\nOptionally populates with realistic sample data via --sample.",
        epilog="""examples:
  qashbocks init data/ledger.db            # Initialize a new empty database
  qashbocks init data/sample.db --sample   # Generate realistic demonstration database
""",
    )
    p_init.add_argument("target", nargs="?", type=Path, default=None, help="Path for new database (default: data/qashbocks.db or data/sample.db)")
    p_init.add_argument("--sample", action="store_true", help="Populate with realistic demonstration accounts, bills, and transactions")

    # Normalize argv
    normalized_argv = [sys.argv[0]]
    valid_subcommands = {
        "accounts", "status", "ls", "bills", "upcoming", "register", "reg", "activity", "recent",
        "add", "alias", "reconcile", "skip",
        "forecast", "report", "chart", "map", "maps", "categories", "cats", "payees", "pay",
        "inspect", "show", "trn", "recur", "repeat", "currencies", "crnc", "currency",
        "tax", "hmrc", "search", "find", "init"
    }
    for i, arg in enumerate(sys.argv[1:], 1):
        clean = arg.lstrip("-").lower()
        if i == 1 and arg.startswith("-") and clean in valid_subcommands:
            normalized_argv.append(clean)
        elif arg.startswith("-") and not arg.startswith("--") and len(arg) > 2:
            if clean in ("days", "account", "category", "limit", "naive", "force", "apply", "output", "format", "sort", "html", "quiet", "yes", "deep", "stats", "plain", "unicode", "md", "markdown", "date", "on", "from", "since", "to", "until", "sample"):
                normalized_argv.append(f"--{clean}")
            else:
                normalized_argv.append(arg)
        else:
            normalized_argv.append(arg)
    sys.argv = normalized_argv

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    # Subcommand dispatching to external tools
    if args.command == "init":
        cmd_init(args)
        sys.exit(0)

    if args.command in ("map", "maps", "categories", "cats", "payees", "pay"):
        map_script = REPO_ROOT / "tools" / "id_maps.py"
        cmd = [sys.executable, str(map_script), "--db", str(args.db)]
        if args.command in ("categories", "cats"):
            target_domain = "categories"
        elif args.command in ("payees", "pay"):
            target_domain = "payees"
        else:
            target_domain = getattr(args, "domain", "categories")

        cmd.append(target_domain)
        if target_domain in ("generate", "all"):
            if getattr(args, "output", None):
                cmd.extend(["--dir", str(args.output)])
        else:
            if getattr(args, "ambiguous", False):
                cmd.append("--ambiguous")
            if getattr(args, "search", None):
                cmd.extend(["--search", args.search])
            if getattr(args, "format", None):
                cmd.extend(["--format", args.format])
            if getattr(args, "output", None):
                cmd.extend(["--output", str(args.output)])
            if getattr(args, "include_all", False):
                cmd.append("--all")
            if getattr(args, "limit", None):
                cmd.extend(["--limit", str(args.limit)])
        subprocess.run(cmd)
        sys.exit(0)

    if args.command in ("forecast", "report", "chart"):
        fc_script = REPO_ROOT / "tools" / "forecast_report.py"
        cmd = [sys.executable, str(fc_script), "--db", str(args.db), "-a", args.account, "-d", str(args.days)]
        if args.html:
            cmd.extend(["--html", str(args.html)])
        if not args.income:
            cmd.append("--no-income")
        if not args.reconciled:
            cmd.append("--naive-sum")
        subprocess.run(cmd)
        sys.exit(0)

    if args.command in ("inspect", "show", "trn"):
        inspect_script = REPO_ROOT / "tools" / "inspect_entry.py"
        clean_htrn = args.htrn.lstrip("#")
        cmd = [sys.executable, str(inspect_script), clean_htrn, "--db", str(args.db)]
        if getattr(args, "format", None):
            cmd.extend(["--format", args.format])
        if getattr(args, "all_bills", False):
            cmd.append("--all")
        subprocess.run(cmd)
        sys.exit(0)

    if args.command in ("currencies", "crnc", "currency"):
        crnc_script = REPO_ROOT / "tools" / "inspect_currencies.py"
        cmd = [sys.executable, str(crnc_script), "--db", str(args.db)]
        if getattr(args, "all", False):
            cmd.append("--all")
        if getattr(args, "no_accounts", False):
            cmd.append("--no-accounts")
        if getattr(args, "transfers", False):
            cmd.append("--transfers")
        if getattr(args, "limit", None):
            cmd.extend(["--limit-tx", str(args.limit)])
        if getattr(args, "format", None):
            cmd.extend(["--format", args.format])
        subprocess.run(cmd)
        sys.exit(0)

    if args.command in ("tax", "hmrc"):
        tax_script = REPO_ROOT / "tools" / "tax_report.py"
        cmd = [sys.executable, str(tax_script), "-y", str(args.year), "--db", str(args.db)]
        if getattr(args, "output", None):
            cmd.extend(["-o", str(args.output)])
        if getattr(args, "format", None):
            cmd.extend(["--format", args.format])
        subprocess.run(cmd)
        sys.exit(0)

    if args.command in ("recur", "repeat"):
        recur_script = REPO_ROOT / "tools" / "make_recurring.py"
        clean_htrn = args.htrn.lstrip("#")
        cmd = [
            sys.executable,
            str(recur_script),
            clean_htrn,
            "--db",
            str(args.db),
            "-f",
            args.frequency,
            "-n",
            str(args.count),
        ]
        if getattr(args, "next_due", None):
            cmd.extend(["-d", args.next_due])
        if getattr(args, "yes", False):
            cmd.append("-y")
        subprocess.run(cmd)
        sys.exit(0)

    if args.command in ("search", "find"):
        search_script = REPO_ROOT / "tools" / "search_transactions.py"
        cmd = [sys.executable, str(search_script)]
        if getattr(args, "query", None):
            cmd.append(args.query)
        if getattr(args, "named_query", None):
            cmd.extend(["-q", args.named_query])
        if getattr(args, "active", False):
            cmd.append("-A")
        if getattr(args, "account", None):
            cmd.extend(["-a", args.account])
        if getattr(args, "payee", None):
            cmd.extend(["-p", args.payee])
        if getattr(args, "category", None):
            cmd.extend(["-c", args.category])
        if getattr(args, "memo", None):
            cmd.extend(["-m", args.memo])
        if getattr(args, "min", None) is not None:
            cmd.extend(["--min", str(args.min)])
        if getattr(args, "max", None) is not None:
            cmd.extend(["--max", str(args.max)])
        if getattr(args, "date_from", None):
            cmd.extend(["--from", args.date_from])
        if getattr(args, "date_to", None):
            cmd.extend(["--to", args.date_to])
        if getattr(args, "limit", None) is not None:
            cmd.extend(["-n", str(args.limit)])
        if getattr(args, "splits", False):
            cmd.append("--splits")
        if getattr(args, "db", None):
            cmd.extend(["--db", str(args.db)])
        if getattr(args, "format", None):
            cmd.extend(["--format", args.format])
        subprocess.run(cmd)
        sys.exit(0)

    conn = get_db(args.db)

    if args.command in ("accounts", "status", "ls"):
        cmd_accounts(args, conn)
    elif args.command == "alias":
        cmd_alias(args, conn)
    elif args.command in ("bills", "upcoming"):
        cmd_bills(args, conn)
    elif args.command in ("register", "reg", "activity", "recent"):
        cmd_register(args, conn)
    elif args.command == "add":
        cmd_add(args, conn)
    elif args.command == "reconcile":
        cmd_reconcile(args, conn)
    elif args.command == "skip":
        cmd_skip_bill(args, conn)

    conn.close()


if __name__ == "__main__":
    main()
