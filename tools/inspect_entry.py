#!/usr/bin/env python3
"""
tools/inspect_entry.py

Inspects a specific transaction / register entry (by htrn ID) in Qashbocks.
Extracts transaction details, linked accounts, payee, category, splits,
transfers, and recurring bill linkages, then delegates presentation to presenter.py.
"""

import argparse
import datetime
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "sample.db"

# Import presentation engine
sys.path.insert(0, str(PROJECT_ROOT))
try:
    from qashbocks.presenter import render_template
except ImportError:
    from presenter import render_template


def get_inspection_context(conn: sqlite3.Connection, htrn: int, show_all_bills: bool = False) -> dict | None:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT t.*, 
               a.szFull AS acct_name, 
               alink.szFull AS link_acct_name,
               p.szFull AS payee_name, 
               c.szFull AS cat_name
        FROM TRN t
        LEFT JOIN ACCT a ON t.hacct = a.hacct
        LEFT JOIN ACCT alink ON t.hacctLink = alink.hacct
        LEFT JOIN PAY p ON t.lHpay = p.hpay
        LEFT JOIN CAT c ON t.hcat = c.hcat
        WHERE t.htrn = ?;
    """, (htrn,))
    row = cur.fetchone()

    if not row:
        return None

    ctx = {
        "trn": dict(row),
        "bills_direct": [],
        "bills_template": [],
        "matching_bills": [],
        "matching_bills_omitted": 0,
        "historical_bills_count": 0,
        "linked_txs": [],
        "splits": [],
        "xfers": [],
    }

    # 1. Direct Foreign Key Link: TRN.hbillHead
    if row['hbillHead'] and int(row['hbillHead']) > 0:
        hbill_head = int(row['hbillHead'])
        cur.execute("""
            SELECT b.*, t.amt as bill_amt, p.szFull as payee_name, a.szFull as acct_name, c.szFull as cat_name
            FROM BILL b
            LEFT JOIN TRN t ON b.lHtrn = t.htrn
            LEFT JOIN PAY p ON t.lHpay = p.hpay
            LEFT JOIN ACCT a ON t.hacct = a.hacct
            LEFT JOIN CAT c ON t.hcat = c.hcat
            WHERE b.hbill = ? OR b.hbillHead = ?
            ORDER BY b.hbill ASC;
        """, (hbill_head, hbill_head))
        ctx["bills_direct"] = [dict(b) for b in cur.fetchall()]

        # All posted / linked register transactions for this bill
        cur.execute("""
            SELECT t.htrn, SUBSTR(t.dt, 1, 10) as tx_dt, t.amt, t.mMemo, t.iinst, t.cs
            FROM TRN t
            WHERE t.hbillHead = ?
            ORDER BY t.dt ASC, t.htrn ASC;
        """, (hbill_head,))
        ctx["linked_txs"] = [dict(tx) for tx in cur.fetchall()]

    # 2. Check if this htrn is the template transaction in BILL (lHtrn)
    cur.execute("""
        SELECT b.*, t.amt as bill_amt, p.szFull as payee_name, a.szFull as acct_name
        FROM BILL b
        LEFT JOIN TRN t ON b.lHtrn = t.htrn
        LEFT JOIN PAY p ON t.lHpay = p.hpay
        LEFT JOIN ACCT a ON t.hacct = a.hacct
        WHERE b.lHtrn = ?;
    """, (htrn,))
    ctx["bills_template"] = [dict(b) for b in cur.fetchall()]

    # 3. Check candidate matching bills by payee and account if no direct link
    if not ctx["bills_direct"] and not ctx["bills_template"]:
        is_transfer = bool(row['hacctLink'] and str(row['hacctLink']).strip())
        trn_amt = float(row['amt'] or 0.0)
        trn_dt_str = str(row['dt'] or "")[:10]

        if is_transfer:
            # For inter-account transfers, candidate bills MUST also be transfers between the same two accounts
            cur.execute("""
                SELECT b.hbill, b.hbillHead, b.st, b.frq, b.cFrqInst, b.dt, t.amt as bill_amt, b.iinstNextUnpaid,
                       p.szFull as payee_name, a.szFull as acct_name, alink.szFull as link_acct_name,
                       t.hacct, t.hacctLink
                FROM BILL b
                JOIN TRN t ON b.lHtrn = t.htrn
                LEFT JOIN PAY p ON t.lHpay = p.hpay
                LEFT JOIN ACCT a ON t.hacct = a.hacct
                LEFT JOIN ACCT alink ON t.hacctLink = alink.hacct
                WHERE b.hbill = b.hbillHead
                  AND ((t.hacct = ? AND t.hacctLink = ?) OR (t.hacct = ? AND t.hacctLink = ?))
                ORDER BY b.st ASC, b.dt DESC;
            """, (row['hacct'], row['hacctLink'], row['hacctLink'], row['hacct']))
        else:
            # For standard transactions, payee must match and candidate must not be an inter-account transfer
            cur.execute("""
                SELECT b.hbill, b.hbillHead, b.st, b.frq, b.cFrqInst, b.dt, t.amt as bill_amt, b.iinstNextUnpaid,
                       p.szFull as payee_name, a.szFull as acct_name, NULL as link_acct_name,
                       t.hacct, t.hacctLink
                FROM BILL b
                JOIN TRN t ON b.lHtrn = t.htrn
                LEFT JOIN PAY p ON t.lHpay = p.hpay
                LEFT JOIN ACCT a ON t.hacct = a.hacct
                WHERE b.hbill = b.hbillHead
                  AND t.lHpay = ?
                  AND (t.hacctLink IS NULL OR t.hacctLink = '' OR t.hacctLink = 0)
                ORDER BY b.st ASC, b.dt DESC;
            """, (row['lHpay'],))

        all_candidates = [dict(c) for c in cur.fetchall()]

        live_candidates = []
        dormant_count = 0

        for c in all_candidates:
            bill_amt = float(c.get('bill_amt') or 0.0)
            c['amt_diff'] = abs(abs(trn_amt) - abs(bill_amt))
            c['same_acct'] = (str(c.get('hacct')) == str(row['hacct']))

            # Calculate dormancy relative to transaction date
            b_dt_str = str(c.get('dt') or "")[:10]
            days_gap = 0
            if trn_dt_str and b_dt_str and len(trn_dt_str) == 10 and len(b_dt_str) == 10:
                try:
                    d_trn = datetime.date.fromisoformat(trn_dt_str)
                    d_bill = datetime.date.fromisoformat(b_dt_str)
                    days_gap = (d_trn - d_bill).days
                except ValueError:
                    pass

            is_terminated = (str(c.get('st')) != '1')
            is_dormant = (days_gap > 180 and not is_terminated and c['amt_diff'] > 0.01)

            c['is_terminated'] = is_terminated
            c['is_dormant'] = is_dormant
            c['days_gap'] = days_gap

            if show_all_bills:
                live_candidates.append(c)
            else:
                if not is_terminated and not is_dormant:
                    live_candidates.append(c)
                else:
                    dormant_count += 1

        ctx["historical_bills_count"] = dormant_count

        # Rank candidates: active first, same source account, closest amount, newest due date
        live_candidates.sort(
            key=lambda c: (
                1 if c.get('is_terminated') else (1 if c.get('is_dormant') else 0),
                0 if c.get('same_acct') else 1,
                c.get('amt_diff', 0),
                -(c.get('days_gap', 0))
            )
        )

        if not show_all_bills and len(live_candidates) > 5:
            ctx["matching_bills"] = live_candidates[:5]
            ctx["matching_bills_omitted"] = len(live_candidates) - 5
        else:
            ctx["matching_bills"] = live_candidates
            ctx["matching_bills_omitted"] = 0

    # 4. Check Splits
    # Check if this transaction is a split parent
    cur.execute("""
        SELECT s.iSplit, s.htrn AS child_htrn, s.htrnParent, CAST(t.amt AS REAL) AS amt,
               COALESCE(c.szFull, 'Uncategorized') AS cat_name,
               COALESCE(t.mMemo, '') AS memo
        FROM TRN_SPLIT s
        JOIN TRN t ON s.htrn = t.htrn
        LEFT JOIN CAT c ON t.hcat = c.hcat
        WHERE s.htrnParent = ?
        ORDER BY CAST(s.iSplit AS INTEGER) ASC;
    """, (htrn,))
    ctx["splits"] = [dict(s) for s in cur.fetchall()]

    # Check if this transaction is a split child
    ctx["split_parent"] = None
    if not ctx["splits"]:
        cur.execute("""
            SELECT s.iSplit, s.htrnParent, CAST(t.amt AS REAL) AS parent_amt,
                   COALESCE(p.szFull, 'Unknown Payee') AS parent_payee,
                   SUBSTR(t.dt, 1, 10) AS parent_date
            FROM TRN_SPLIT s
            JOIN TRN t ON s.htrnParent = t.htrn
            LEFT JOIN PAY p ON t.lHpay = p.hpay
            WHERE s.htrn = ?;
        """, (htrn,))
        parent_row = cur.fetchone()
        if parent_row:
            ctx["split_parent"] = dict(parent_row)

    # 5. Check Transfers
    cur.execute("SELECT * FROM TRN_XFER WHERE htrnFrom = ? OR htrnLink = ?;", (htrn, htrn))
    ctx["xfers"] = [dict(x) for x in cur.fetchall()]

    return ctx


def inspect_transaction(db_path: Path, htrn: int, format_style: str | None = None, show_all_bills: bool = False):
    conn = sqlite3.connect(db_path)
    ctx = get_inspection_context(conn, htrn, show_all_bills=show_all_bills)
    conn.close()

    if not ctx:
        print(f"Error: Transaction htrn={htrn} not found in {db_path}.", file=sys.stderr)
        return

    ctx["cli_style"] = format_style
    output = render_template("inspect.tpl", ctx)
    print(output)


def main():
    parser = argparse.ArgumentParser(description="Inspect a specific transaction and its linked recurring bill.")
    parser.add_argument("htrn", type=int, help="Transaction ID (htrn) to inspect")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"Path to database (default: {DEFAULT_DB})")
    parser.add_argument(
        "-a", "--all", "--all-bills",
        action="store_true",
        dest="all_bills",
        help="Show all candidate matching bills including terminated and dormant historical schedules",
    )
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

    inspect_transaction(args.db, args.htrn, format_style=args.format, show_all_bills=args.all_bills)


if __name__ == "__main__":
    main()
