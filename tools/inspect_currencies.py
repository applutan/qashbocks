#!/usr/bin/env python3
"""
tools/inspect_currencies.py

Audits currencies defined in CRNC table, account denominations in ACCT,
and transaction usage in TRN. Identifies multi-currency accounts.
Uses presenter.py and templates/currencies.tpl for presentation.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from qashbocks.presenter import render_template
except ImportError:
    from presenter import render_template

DEFAULT_DB = PROJECT_ROOT / "data" / "sample.db"


def get_currencies_context(
    conn: sqlite3.Connection,
    show_all: bool = False,
    show_accounts: bool = True,
    show_transfers: bool = True,
    limit_tx: int = 15,
) -> dict:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 1. Query all currencies in CRNC and count linked accounts & transactions
    cur.execute("""
        SELECT 
            c.hcrnc AS id, 
            c.szName AS name, 
            c.szSymbol AS symbol, 
            c.szIsoCode AS iso,
            COUNT(DISTINCT a.hacct) AS total_accounts,
            COUNT(DISTINCT CASE WHEN CAST(a.fClosed AS INTEGER) = 0 THEN a.hacct END) AS active_accounts,
            COUNT(DISTINCT t.htrn) AS total_transactions
        FROM CRNC c
        LEFT JOIN ACCT a ON c.hcrnc = a.hcrnc
        LEFT JOIN TRN t ON a.hacct = t.hacct
        GROUP BY c.hcrnc
        ORDER BY active_accounts DESC, total_accounts DESC, c.hcrnc ASC;
    """)
    all_currencies = [dict(r) for r in cur.fetchall()]

    for c in all_currencies:
        c["iso"] = c["iso"] or "-"
        c["symbol"] = c["symbol"] or "-"
        c["name"] = c["name"] or "Unknown"
        c["active_accounts"] = c["active_accounts"] or 0
        c["total_accounts"] = c["total_accounts"] or 0
        c["total_transactions"] = c["total_transactions"] or 0

    if show_all:
        filtered_currencies = all_currencies
    else:
        filtered_currencies = [c for c in all_currencies if c["total_accounts"] > 0]

    total_currencies = len(all_currencies)
    used_count = sum(1 for c in all_currencies if c["total_accounts"] > 0)
    active_count = sum(1 for c in all_currencies if c["active_accounts"] > 0)

    # 2. Detailed Account Denominations
    accounts = []
    if show_accounts:
        cur.execute("""
            SELECT 
                c.szSymbol AS symbol,
                c.szIsoCode AS iso,
                c.szName AS currency_name,
                a.hacct AS account_id,
                a.szFull AS account_name,
                a.szAls AS alias,
                a.fClosed AS is_closed,
                a.at AS account_type,
                COUNT(t.htrn) AS tx_count
            FROM ACCT a
            JOIN CRNC c ON a.hcrnc = c.hcrnc
            LEFT JOIN TRN t ON a.hacct = t.hacct
            GROUP BY a.hacct
            ORDER BY c.hcrnc ASC, a.fClosed ASC, a.hacct ASC;
        """)
        type_names = {0: "Current", 1: "Credit", 2: "Savings", 3: "Loan", 4: "Mortgage", 5: "Investment", 6: "Cash"}
        for a in cur.fetchall():
            is_closed = int(a["is_closed"]) != 0 if a["is_closed"] is not None else False
            at_code = int(a["account_type"]) if a["account_type"] is not None else -1
            accounts.append({
                "iso": a["iso"] or "-",
                "symbol": a["symbol"] or "-",
                "currency_name": a["currency_name"] or "Unknown",
                "account_id": a["account_id"],
                "account_name": a["account_name"][:28],
                "alias": a["alias"] or "-",
                "status": "Closed" if is_closed else "Active",
                "type": type_names.get(at_code, f"Type {at_code}"),
                "tx_count": a["tx_count"],
            })

    # 3. Inter-currency transfers
    transfers = []
    if show_transfers:
        cur.execute("""
            SELECT 
                x.htrnFrom, x.htrnLink,
                tFrom.dt AS tx_date,
                aFrom.szFull AS from_account, cFrom.szIsoCode AS from_iso, tFrom.amt AS from_amt,
                aTo.szFull AS to_account, cTo.szIsoCode AS to_iso, tTo.amt AS to_amt
            FROM TRN_XFER x
            JOIN TRN tFrom ON x.htrnFrom = tFrom.htrn
            JOIN TRN tTo ON x.htrnLink = tTo.htrn
            JOIN ACCT aFrom ON tFrom.hacct = aFrom.hacct
            JOIN ACCT aTo ON tTo.hacct = aTo.hacct
            JOIN CRNC cFrom ON aFrom.hcrnc = cFrom.hcrnc
            JOIN CRNC cTo ON aTo.hcrnc = cTo.hcrnc
            WHERE aFrom.hcrnc != aTo.hcrnc
            ORDER BY tFrom.dt DESC
            LIMIT ?;
        """, (limit_tx,))
        sym_map = {"GBP": "£", "EUR": "€", "USD": "$"}
        for x in cur.fetchall():
            f_amt = float(x["from_amt"] or 0.0)
            t_amt = float(x["to_amt"] or 0.0)
            f_sym = sym_map.get(x["from_iso"], f"{x['from_iso']} ")
            t_sym = sym_map.get(x["to_iso"], f"{x['to_iso']} ")
            f_str = f"{f_sym}-{abs(f_amt):,.2f}" if f_amt < 0 else f"{f_sym}{f_amt:,.2f}"
            t_str = f"{t_sym}-{abs(t_amt):,.2f}" if t_amt < 0 else f"{t_sym}{t_amt:,.2f}"
            transfers.append({
                "tx_date": str(x["tx_date"])[:10],
                "from_account": x["from_account"][:20],
                "from_amt_str": f_str,
                "to_account": x["to_account"][:20],
                "to_amt_str": t_str,
            })

    return {
        "currencies": filtered_currencies,
        "total_currencies": total_currencies,
        "used_count": used_count,
        "active_count": active_count,
        "accounts": accounts,
        "transfers": transfers,
        "show_transfers": show_transfers,
    }


def audit_currencies(
    db_path: Path,
    show_all: bool = False,
    show_accounts: bool = True,
    show_transfers: bool = True,
    limit_tx: int = 15,
    format_style: str | None = None,
):
    conn = sqlite3.connect(db_path)
    ctx = get_currencies_context(
        conn,
        show_all=show_all,
        show_accounts=show_accounts,
        show_transfers=show_transfers,
        limit_tx=limit_tx,
    )
    conn.close()

    ctx["cli_style"] = format_style
    output = render_template("currencies.tpl", ctx)
    print(output)


def main():
    parser = argparse.ArgumentParser(description="Audit currencies and account denominations.")
    parser.add_argument("--all", action="store_true", help="Display all defined currencies, including unused ones")
    parser.add_argument("--no-accounts", dest="show_accounts", action="store_false", help="Hide account list")
    parser.add_argument("--transfers", action="store_true", help="Show multi-currency transfers across accounts")
    parser.add_argument("--limit-tx", type=int, default=15, help="Limit number of multi-currency transfers shown")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"Path to database (default: {DEFAULT_DB})")
    fmt_group = parser.add_argument_group("formatting options")
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
    args = parser.parse_args()

    audit_currencies(
        args.db,
        show_all=args.all,
        show_accounts=args.show_accounts,
        show_transfers=args.transfers,
        limit_tx=args.limit_tx,
        format_style=args.format,
    )


if __name__ == "__main__":
    main()
