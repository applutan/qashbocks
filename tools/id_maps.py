#!/usr/bin/env python3
"""
tools/id_maps.py

Generates and displays unambiguous ID-name mapping directories for:
1. Categories (CAT): ID, Parent:Child Path, Leaf Name, Transaction Count, Ambiguity Flag.
2. Accounts (ACCT): ID, Full Name, Configured Aliases, Currency, Status, Balance.
3. Recurring Bills (BILL): Stable Bill ID (#hbillHead), Payee, Amount, Account, Category, Due Date.
4. Payees (PAY): ID, Payee Name, Transaction Count, Last Seen Date.

Supports tabular display, CSV, JSON, Markdown export, and batch directory generation
into data/maps/ for fast reference during batch transaction entry.

Usage:
    python3 tools/id_maps.py categories [--ambiguous] [--search FOOD] [--format table|csv|json|markdown]
    python3 tools/id_maps.py accounts [--all] [--format table|csv|json]
    python3 tools/id_maps.py bills [--search Google] [--format table|csv|json]
    python3 tools/id_maps.py payees [--limit 50] [--search Tesco] [--format table|csv|json]
    python3 tools/id_maps.py generate [--dir data/maps]
"""

import argparse
import csv
import io
import json
import sqlite3
import sys
from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "sample.db"
DEFAULT_MAPS_DIR = PROJECT_ROOT / "data" / "maps"

# Add project root to sys.path
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def get_db(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        print(f"Error: Database not found at {db_path}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# =====================================================================
# 1. CATEGORY MAP
# =====================================================================
def get_categories_data(conn: sqlite3.Connection, search: str = None, ambiguous_only: bool = False) -> list[dict]:
    cur = conn.cursor()
    query = """
    SELECT 
        c.hcat,
        c.szFull AS name,
        c.szAls AS alias,
        c.hcatParent AS parent_id,
        p.szFull AS parent_name,
        COUNT(t.htrn) AS tx_count
    FROM CAT c
    LEFT JOIN CAT p ON c.hcatParent = p.hcat
    LEFT JOIN TRN t ON c.hcat = t.hcat
    GROUP BY c.hcat, c.szFull, c.szAls, c.hcatParent, p.szFull
    ORDER BY c.szFull ASC, tx_count DESC
    """
    cur.execute(query)
    rows = cur.fetchall()

    # Identify duplicate leaf names across different parents
    leaf_counts = {}
    for r in rows:
        leaf = (r["name"] or "").strip().lower()
        leaf_counts[leaf] = leaf_counts.get(leaf, 0) + 1

    results = []
    for r in rows:
        name = r["name"] or ""
        parent = r["parent_name"] or ""
        leaf_lower = name.strip().lower()
        is_ambiguous = leaf_counts.get(leaf_lower, 0) > 1

        if ambiguous_only and not is_ambiguous:
            continue

        full_path = f"{parent}:{name}" if parent else name
        alias_str = r["alias"] or ""

        if search:
            s = search.lower()
            if s not in name.lower() and s not in parent.lower() and s not in str(r["hcat"]) and s not in alias_str.lower():
                continue

        results.append({
            "id": r["hcat"],
            "id_tag": f"#{r['hcat']}",
            "path": full_path,
            "leaf_name": name,
            "parent_name": parent,
            "alias": alias_str,
            "tx_count": r["tx_count"],
            "ambiguous": is_ambiguous,
        })

    # Sort results logically: if ambiguous, keep identical leaves together sorted by usage count descending
    if ambiguous_only:
        results.sort(key=lambda x: (x["leaf_name"].lower(), -x["tx_count"], x["path"].lower()))
    else:
        results.sort(key=lambda x: (x["path"].lower(), -x["tx_count"]))

    return results


# =====================================================================
# 2. ACCOUNT MAP
# =====================================================================
def get_accounts_data(conn: sqlite3.Connection, search: str = None, include_closed: bool = False) -> list[dict]:
    # Import ACCOUNT_ALIASES if possible
    known_aliases = {}
    try:
        from qashbocks.cli import ACCOUNT_ALIASES, load_config
        cfg = load_config()
        known_aliases = {k: v for k, v in ACCOUNT_ALIASES.items()}
        for k, v in cfg.get("aliases", {}).items():
            known_aliases[k] = v
    except Exception:
        pass

    # Invert aliases to map full account name -> list of aliases
    acct_to_aliases = {}
    for als, full in known_aliases.items():
        acct_to_aliases.setdefault(full.lower(), []).append(als)

    cur = conn.cursor()
    query = """
    SELECT 
        a.hacct,
        a.szFull AS name,
        a.szAls AS db_alias,
        a.fClosed AS closed,
        a.amtLimit AS credit_limit,
        cu.szIsoCode AS currency,
        COUNT(t.htrn) AS tx_count
    FROM ACCT a
    LEFT JOIN CRNC cu ON a.hcrnc = cu.hcrnc
    LEFT JOIN TRN t ON a.hacct = t.hacct
    GROUP BY a.hacct, a.szFull, a.szAls, a.fClosed, a.amtLimit, cu.szIsoCode
    ORDER BY a.hacct ASC
    """
    cur.execute(query)
    rows = cur.fetchall()

    results = []
    for r in rows:
        is_closed = str(r["closed"] or "").strip() in ("1", "true", "True")
        if not include_closed and is_closed:
            continue

        name = r["name"] or ""
        aliases = list(acct_to_aliases.get(name.lower(), []))
        if r["db_alias"] and r["db_alias"] not in aliases:
            aliases.append(r["db_alias"])

        alias_str = ", ".join(sorted(aliases)) if aliases else "-"

        if search:
            s = search.lower()
            if s not in name.lower() and s not in str(r["hacct"]) and s not in alias_str.lower():
                continue

        nl = name.lower()
        if "cash" in nl or "lucre" in nl:
            acct_type = "Cash"
        elif "visa" in nl or "amex" in nl or "mastercard" in nl or "card" in nl:
            acct_type = "Credit Card"
        elif "pension" in nl or "shares" in nl or "invest" in nl or "savings" in nl:
            acct_type = "Investment/Savings"
        else:
            acct_type = "Bank/Current"

        results.append({
            "id": r["hacct"],
            "id_tag": f"#{r['hacct']}",
            "name": name,
            "aliases": alias_str,
            "type": acct_type,
            "currency": r["currency"] or "GBP",
            "closed": is_closed,
            "tx_count": r["tx_count"],
        })

    return results


# =====================================================================
# 3. RECURRING BILL MAP
# =====================================================================
def get_bills_data(conn: sqlite3.Connection, search: str = None) -> list[dict]:
    cur = conn.cursor()
    query = """
    SELECT 
        b.hbill,
        b.hbillHead,
        b.st,
        b.frq,
        b.cFrqInst,
        b.dt AS start_dt,
        b.iinstNextUnpaid,
        t.amt,
        p.szFull AS payee,
        a.szFull AS account,
        c.szFull AS category
    FROM BILL b
    JOIN TRN t ON b.lHtrn = t.htrn
    JOIN ACCT a ON t.hacct = a.hacct
    LEFT JOIN PAY p ON t.lHpay = p.hpay
    LEFT JOIN CAT c ON t.hcat = c.hcat
    WHERE b.hbill = b.hbillHead
      AND b.st != 2
    ORDER BY CAST(b.hbillHead AS INTEGER) ASC
    """
    cur.execute(query)
    rows = cur.fetchall()

    frq_map = {0: "Once", 1: "Daily", 2: "Weekly", 3: "Monthly", 4: "Yearly", 5: "Bi-Weekly", 6: "Semi-Monthly"}

    results = []
    for r in rows:
        payee = r["payee"] or "Unknown"
        acct = r["account"] or "Unknown"
        cat = r["category"] or "Uncategorised"
        head_id = r["hbillHead"]

        if search:
            s = search.lower()
            if s not in payee.lower() and s not in acct.lower() and s not in cat.lower() and s not in str(head_id):
                continue

        amt = float(r["amt"] or 0.0)
        frq_name = frq_map.get(int(r["frq"] or 3), "Monthly")

        results.append({
            "id": head_id,
            "id_tag": f"#{head_id}",
            "payee": payee,
            "amount": amt,
            "account": acct,
            "category": cat,
            "frequency": frq_name,
            "next_unpaid_idx": int(r["iinstNextUnpaid"] or 0),
            "start_date": str(r["start_dt"])[:10],
        })

    return results


# =====================================================================
# 4. PAYEE MAP
# =====================================================================
def get_payees_data(conn: sqlite3.Connection, search: str = None, limit: int = 100) -> list[dict]:
    cur = conn.cursor()
    query = """
    SELECT 
        p.hpay,
        p.szFull AS name,
        COUNT(t.htrn) AS tx_count,
        MAX(t.dt) AS last_seen
    FROM PAY p
    LEFT JOIN TRN t ON p.hpay = t.lHpay
    GROUP BY p.hpay, p.szFull
    ORDER BY tx_count DESC, p.szFull ASC
    """
    cur.execute(query)
    rows = cur.fetchall()

    results = []
    for r in rows:
        name = r["name"] or ""
        if search:
            s = search.lower()
            if s not in name.lower() and s not in str(r["hpay"]):
                continue

        results.append({
            "id": r["hpay"],
            "id_tag": f"#{r['hpay']}",
            "name": name,
            "tx_count": r["tx_count"],
            "last_seen": str(r["last_seen"] or "")[:10] or "-",
        })
        if limit and len(results) >= limit:
            break

    return results


# =====================================================================
# FORMATTING & OUTPUT HANDLERS
# =====================================================================
def render_table(headers: list[str], rows: list[list[str]], alignments: list[str] = None) -> str:
    if not rows:
        return "No records found.\n"

    alignments = alignments or ["<"] * len(headers)
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(val)))

    lines = []
    # Header
    hdr_cells = []
    for i, h in enumerate(headers):
        align = alignments[i]
        hdr_cells.append(f"{h:{align}{col_widths[i]}}")
    lines.append(" │ ".join(hdr_cells))

    # Divider
    div_cells = ["─" * col_widths[i] for i in range(len(headers))]
    lines.append("─┼─".join(div_cells))

    # Data
    for row in rows:
        row_cells = []
        for i, val in enumerate(row):
            align = alignments[i]
            row_cells.append(f"{str(val):{align}{col_widths[i]}}")
        lines.append(" │ ".join(row_cells))

    return "\n".join(lines) + "\n"


def render_markdown_table(headers: list[str], rows: list[list[str]], alignments: list[str] = None) -> str:
    if not rows:
        return "_No records found._\n"

    alignments = alignments or ["<"] * len(headers)
    lines = []
    lines.append("| " + " | ".join(headers) + " |")

    div_cells = []
    for a in alignments:
        if a == ">":
            div_cells.append("---:")
        elif a == "^":
            div_cells.append(":---:")
        else:
            div_cells.append(":---")
    lines.append("| " + " | ".join(div_cells) + " |")

    for row in rows:
        safe_row = [str(val).replace("|", "\\|") for val in row]
        lines.append("| " + " | ".join(safe_row) + " |")

    return "\n".join(lines) + "\n"


def render_csv(headers: list[str], rows: list[list[str]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return output.getvalue()


def output_formatted(data_list: list[dict], kind: str, fmt: str, out_file: Path = None):
    text_out = ""
    if fmt == "json":
        text_out = json.dumps(data_list, indent=2) + "\n"
    elif kind == "categories":
        headers = ["ID", "Path (Parent:Child)", "Leaf Name", "Txs", "Ambiguous?"]
        alignments = [">", "<", "<", ">", "^"]
        rows = []
        for d in data_list:
            ambig = "YES (!)" if d["ambiguous"] else "no"
            rows.append([d["id_tag"], d["path"], d["leaf_name"], str(d["tx_count"]), ambig])

        if fmt == "csv":
            text_out = render_csv(["id", "id_tag", "path", "leaf_name", "parent_name", "tx_count", "ambiguous"],
                                  [[d["id"], d["id_tag"], d["path"], d["leaf_name"], d["parent_name"], d["tx_count"], d["ambiguous"]] for d in data_list])
        elif fmt == "tsv":
            text_out = "\n".join(["\t".join(map(str, r)) for r in rows]) + "\n"
        elif fmt == "markdown":
            text_out = render_markdown_table(headers, rows, alignments)
        else:
            text_out = render_table(headers, rows, alignments)

    elif kind == "accounts":
        headers = ["ID", "Account Name", "Aliases", "Type", "Currency", "Txs"]
        alignments = [">", "<", "<", "<", "^", ">"]
        rows = []
        for d in data_list:
            rows.append([d["id_tag"], d["name"], d["aliases"], d["type"], d["currency"], str(d["tx_count"])])

        if fmt == "csv":
            text_out = render_csv(["id", "id_tag", "name", "aliases", "type", "currency", "closed", "tx_count"],
                                  [[d["id"], d["id_tag"], d["name"], d["aliases"], d["type"], d["currency"], d["closed"], d["tx_count"]] for d in data_list])
        elif fmt == "markdown":
            text_out = render_markdown_table(headers, rows, alignments)
        else:
            text_out = render_table(headers, rows, alignments)

    elif kind == "bills":
        headers = ["Bill #", "Payee", "Amount", "Account", "Category", "Freq"]
        alignments = [">", "<", ">", "<", "<", "^"]
        rows = []
        for d in data_list:
            amt_str = f"£{abs(d['amount']):,.2f}" if d['amount'] < 0 else f"+£{d['amount']:,.2f}"
            rows.append([d["id_tag"], d["payee"], amt_str, d["account"], d["category"], d["frequency"]])

        if fmt == "csv":
            text_out = render_csv(["id", "id_tag", "payee", "amount", "account", "category", "frequency", "next_unpaid_idx"],
                                  [[d["id"], d["id_tag"], d["payee"], f"{d['amount']:.2f}", d["account"], d["category"], d["frequency"], d["next_unpaid_idx"]] for d in data_list])
        elif fmt == "markdown":
            text_out = render_markdown_table(headers, rows, alignments)
        else:
            text_out = render_table(headers, rows, alignments)

    elif kind == "payees":
        headers = ["ID", "Payee Name", "Txs", "Last Seen"]
        alignments = [">", "<", ">", "^"]
        rows = []
        for d in data_list:
            rows.append([d["id_tag"], d["name"], str(d["tx_count"]), d["last_seen"]])

        if fmt == "csv":
            text_out = render_csv(["id", "id_tag", "name", "tx_count", "last_seen"],
                                  [[d["id"], d["id_tag"], d["name"], d["tx_count"], d["last_seen"]] for d in data_list])
        elif fmt == "markdown":
            text_out = render_markdown_table(headers, rows, alignments)
        else:
            text_out = render_table(headers, rows, alignments)

    if out_file:
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(text_out, encoding="utf-8")
        print(f"Exported {len(data_list)} {kind} records to: {out_file}")
    else:
        print(text_out, end="")


# =====================================================================
# BATCH GENERATION (data/maps/)
# =====================================================================
def generate_all_maps(conn: sqlite3.Connection, target_dir: Path):
    target_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nGenerating ID-Name Map reference directory in {target_dir}...")

    # 1. Categories
    cats = get_categories_data(conn)
    output_formatted(cats, "categories", "csv", target_dir / "categories.csv")
    output_formatted(cats, "categories", "markdown", target_dir / "categories.md")

    # Ambiguous categories only
    ambig_cats = get_categories_data(conn, ambiguous_only=True)
    output_formatted(ambig_cats, "categories", "markdown", target_dir / "ambiguous_categories.md")

    # 2. Accounts
    accts = get_accounts_data(conn, include_closed=True)
    output_formatted(accts, "accounts", "csv", target_dir / "accounts.csv")
    output_formatted(accts, "accounts", "markdown", target_dir / "accounts.md")

    # 3. Bills
    bills = get_bills_data(conn)
    output_formatted(bills, "bills", "csv", target_dir / "bills.csv")
    output_formatted(bills, "bills", "markdown", target_dir / "bills.md")

    # 4. Payees
    payees = get_payees_data(conn, limit=500)
    output_formatted(payees, "payees", "csv", target_dir / "payees.csv")
    output_formatted(payees, "payees", "markdown", target_dir / "payees.md")

    print("\nAll map files generated successfully!\n")


# =====================================================================
# CLI PARSER
# =====================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Generates and displays unambiguous ID-name maps for Categories, Accounts, Bills, and Payees.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"Path to database (default: {DEFAULT_DB})")
    subparsers = parser.add_subparsers(dest="command", help="Mapping domain")

    # Subcommand: categories / cats
    p_cat = subparsers.add_parser("categories", aliases=["cats", "cat"], help="Category ID-name mapping")
    p_cat.add_argument("-a", "--ambiguous", action="store_true", help="Filter to show only ambiguous leaf names")
    p_cat.add_argument("-s", "--search", type=str, default=None, help="Filter by search query")
    p_cat.add_argument("-f", "--format", choices=["table", "csv", "tsv", "json", "markdown"], default="table", help="Output format (default: table)")
    p_cat.add_argument("-o", "--output", type=Path, default=None, help="Save output to file")

    # Subcommand: accounts / accts
    p_acct = subparsers.add_parser("accounts", aliases=["accts", "acct"], help="Account ID-name mapping")
    p_acct.add_argument("--all", action="store_true", help="Include closed accounts")
    p_acct.add_argument("-s", "--search", type=str, default=None, help="Filter by search query")
    p_acct.add_argument("-f", "--format", choices=["table", "csv", "json", "markdown"], default="table", help="Output format (default: table)")
    p_acct.add_argument("-o", "--output", type=Path, default=None, help="Save output to file")

    # Subcommand: bills
    p_bill = subparsers.add_parser("bills", aliases=["bill"], help="Recurring Bill ID mapping")
    p_bill.add_argument("-s", "--search", type=str, default=None, help="Filter by search query")
    p_bill.add_argument("-f", "--format", choices=["table", "csv", "json", "markdown"], default="table", help="Output format (default: table)")
    p_bill.add_argument("-o", "--output", type=Path, default=None, help="Save output to file")

    # Subcommand: payees
    p_pay = subparsers.add_parser("payees", aliases=["payee", "pay"], help="Payee ID mapping")
    p_pay.add_argument("-n", "--limit", type=int, default=100, help="Max number of payees to show (default: 100)")
    p_pay.add_argument("-s", "--search", type=str, default=None, help="Filter by search query")
    p_pay.add_argument("-f", "--format", choices=["table", "csv", "json", "markdown"], default="table", help="Output format (default: table)")
    p_pay.add_argument("-o", "--output", type=Path, default=None, help="Save output to file")

    # Subcommand: generate / all
    p_gen = subparsers.add_parser("generate", aliases=["all"], help="Generate all CSV & Markdown map files into data/maps/")
    p_gen.add_argument("-d", "--dir", type=Path, default=DEFAULT_MAPS_DIR, help="Target directory (default: data/maps/)")

    args = parser.parse_args()
    if not args.command:
        args.command = "categories"
        args.ambiguous = False
        args.search = None
        args.format = "table"
        args.output = None

    conn = get_db(args.db)

    if args.command in ("categories", "cats", "cat"):
        data = get_categories_data(conn, search=args.search, ambiguous_only=args.ambiguous)
        output_formatted(data, "categories", args.format, args.output)
    elif args.command in ("accounts", "accts", "acct"):
        data = get_accounts_data(conn, search=args.search, include_closed=args.all)
        output_formatted(data, "accounts", args.format, args.output)
    elif args.command in ("bills", "bill"):
        data = get_bills_data(conn, search=args.search)
        output_formatted(data, "bills", args.format, args.output)
    elif args.command in ("payees", "payee", "pay"):
        data = get_payees_data(conn, search=args.search, limit=args.limit)
        output_formatted(data, "payees", args.format, args.output)
    elif args.command in ("generate", "all"):
        generate_all_maps(conn, args.dir)

    conn.close()


if __name__ == "__main__":
    main()
