#!/usr/bin/env python3
"""
tools/search_transactions.py

General-purpose search engine across all transactions, child splits, and accounts.
Supports:
1. Free-text search matching across Payee, Category, Memo, and Child Split line items.
2. Numeric amount search matching exact amounts on parent transactions and child splits.
3. Switch for active accounts only (-A / --active; default is off, searching all accounts).
4. Specific field filters (--account, --payee, --category, --memo, --min, --max, --from, --to).
5. Switch to expand into atomic child split line items (--splits).
6. Multi-format output (table, plain, unicode, md, csv, json).

Usage:
    qashbocks find "HMV"
    qashbocks find "bluray" -A
    qashbocks find 17.98
    qashbocks find "Amazon" --from 2026-01-01 --to 2026-11-14
    qashbocks find "bluray" --splits
"""

import argparse
import csv
import datetime
import io
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "qashbocks") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "qashbocks"))

try:
    from qashbocks.presenter import render_template
except ImportError:
    from presenter import render_template


def get_default_db() -> Path:
    env_db = os.environ.get("QASHBOCKS_DB") or os.environ.get("MONEY_DB")
    if env_db:
        return Path(env_db).expanduser().resolve()

    cfg_file = PROJECT_ROOT / "config.toml"
    if cfg_file.exists():
        try:
            import tomllib
            with open(cfg_file, "rb") as f:
                data = tomllib.load(f)
            cfg_db = data.get("defaults", {}).get("database")
            if cfg_db:
                p = Path(cfg_db).expanduser()
                if not p.is_absolute():
                    p = (PROJECT_ROOT / p).resolve()
                if p.exists():
                    return p
        except Exception:
            pass

    sample_db = PROJECT_ROOT / "data" / "sample.db"
    if sample_db.exists():
        return sample_db

    local_db = PROJECT_ROOT / "data" / "qashbocks.db"
    if local_db.exists():
        return local_db
    return sample_db


def parse_numeric_query(q: str) -> Optional[float]:
    """Tries to parse query as an amount (e.g. '17.98', '-17.98', '£17.98', '17.98p')."""
    clean = q.strip().replace("£", "").replace("€", "").replace("$", "").replace(",", "")
    if clean.endswith("p") or clean.endswith("P"):
        clean = clean[:-1]
    try:
        val = float(clean)
        return abs(val)
    except ValueError:
        return None


def search_transactions(
    conn: sqlite3.Connection,
    query: Optional[str] = None,
    active_only: bool = False,
    account: Optional[str] = None,
    payee: Optional[str] = None,
    category: Optional[str] = None,
    memo: Optional[str] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
    splits_mode: bool = False,
) -> Tuple[List[Dict[str, Any]], str]:
    cur = conn.cursor()
    params: List[Any] = []
    where_clauses: List[str] = []

    # 1. Active accounts filter
    if active_only:
        where_clauses.append("a.fClosed = 0")

    # 2. Account filter
    if account:
        acct_clean = account.strip().lower()
        where_clauses.append("(LOWER(a.szFull) LIKE ? OR LOWER(a.szAls) = ? OR vt.account = ?)")
        params.extend([f"%{acct_clean}%", acct_clean, acct_clean])

    # 3. Date range filters
    if date_from:
        where_clauses.append("vt.tx_date >= ?")
        params.append(date_from)
    if date_to:
        where_clauses.append("vt.tx_date <= ?")
        params.append(date_to)

    # 4. Explicit amount filters
    if min_amount is not None:
        where_clauses.append("ABS(vt.amount) >= ?")
        params.append(abs(min_amount))
    if max_amount is not None:
        where_clauses.append("ABS(vt.amount) <= ?")
        params.append(abs(max_amount))

    # 5. Field-specific text filters
    if payee:
        where_clauses.append("LOWER(vt.payee) LIKE ?")
        params.append(f"%{payee.lower()}%")

    if category:
        c_term = f"%{category.lower()}%"
        where_clauses.append("""
            (LOWER(vt.category) LIKE ? OR vt.tx_id IN (
                SELECT sp.htrnParent 
                FROM TRN_SPLIT sp 
                JOIN TRN ct ON sp.htrn = ct.htrn 
                LEFT JOIN CAT c ON ct.hcat = c.hcat 
                WHERE LOWER(c.szFull) LIKE ?
            ))
        """)
        params.extend([c_term, c_term])

    if memo:
        m_term = f"%{memo.lower()}%"
        where_clauses.append("""
            (LOWER(vt.memo) LIKE ? OR vt.tx_id IN (
                SELECT sp.htrnParent 
                FROM TRN_SPLIT sp 
                JOIN TRN ct ON sp.htrn = ct.htrn 
                WHERE LOWER(ct.mMemo) LIKE ?
            ))
        """)
        params.extend([m_term, m_term])

    # 6. General query string
    query_desc_parts = []
    if query:
        q_str = query.strip()
        num_val = parse_numeric_query(q_str)
        if num_val is not None:
            query_desc_parts.append(f"amount = £{num_val:,.2f}")
            where_clauses.append("""
                (ROUND(ABS(vt.amount), 2) = ? OR vt.tx_id IN (
                    SELECT sp.htrnParent 
                    FROM TRN_SPLIT sp 
                    JOIN TRN ct ON sp.htrn = ct.htrn 
                    WHERE ROUND(ABS(CAST(ct.amt AS REAL)), 2) = ?
                ))
            """)
            params.extend([num_val, num_val])
        else:
            q_like = f"%{q_str.lower()}%"
            query_desc_parts.append(f"'{q_str}'")
            where_clauses.append("""
                (
                    LOWER(vt.payee) LIKE ?
                    OR LOWER(vt.category) LIKE ?
                    OR LOWER(vt.memo) LIKE ?
                    OR LOWER(COALESCE(vt.check_ref, '')) LIKE ?
                    OR vt.tx_id IN (
                        SELECT sp.htrnParent 
                        FROM TRN_SPLIT sp 
                        JOIN TRN ct ON sp.htrn = ct.htrn 
                        LEFT JOIN CAT c ON ct.hcat = c.hcat 
                        WHERE LOWER(ct.mMemo) LIKE ? OR LOWER(COALESCE(c.szFull, '')) LIKE ?
                    )
                )
            """)
            params.extend([q_like, q_like, q_like, q_like, q_like, q_like])

    if payee:
        query_desc_parts.append(f"payee contains '{payee}'")
    if category:
        query_desc_parts.append(f"category contains '{category}'")
    if memo:
        query_desc_parts.append(f"memo contains '{memo}'")
    if min_amount or max_amount:
        query_desc_parts.append(f"amount between £{min_amount or 0:.2f} and £{max_amount or 999999:.2f}")
    if date_from or date_to:
        query_desc_parts.append(f"date {date_from or 'start'} to {date_to or 'today'}")
    if account:
        query_desc_parts.append(f"account '{account}'")

    query_desc = ", ".join(query_desc_parts) if query_desc_parts else "All transactions"

    # --- Mode A: Atomic Child Splits View ---
    if splits_mode:
        sql = f"""
        SELECT 
            vcs.line_id AS id,
            vcs.tx_date AS dt,
            vcs.account,
            vcs.account_name,
            vcs.payee,
            vcs.category,
            vcs.amount,
            COALESCE(vcs.memo, '') AS memo,
            vcs.is_split_child,
            vcs.parent_tx_id
        FROM v_category_spending vcs
        JOIN ACCT a ON vcs.account_id = a.hacct
        """
        vcs_where: List[str] = []
        vcs_params: List[Any] = []
        if active_only:
            vcs_where.append("a.fClosed = 0")
        if account:
            acct_clean = account.strip().lower()
            vcs_where.append("(LOWER(a.szFull) LIKE ? OR LOWER(a.szAls) = ? OR vcs.account = ?)")
            vcs_params.extend([f"%{acct_clean}%", acct_clean, acct_clean])
        if date_from:
            vcs_where.append("vcs.tx_date >= ?")
            vcs_params.append(date_from)
        if date_to:
            vcs_where.append("vcs.tx_date <= ?")
            vcs_params.append(date_to)
        if min_amount is not None:
            vcs_where.append("ABS(vcs.amount) >= ?")
            vcs_params.append(abs(min_amount))
        if max_amount is not None:
            vcs_where.append("ABS(vcs.amount) <= ?")
            vcs_params.append(abs(max_amount))
        if payee:
            vcs_where.append("LOWER(vcs.payee) LIKE ?")
            vcs_params.append(f"%{payee.lower()}%")
        if category:
            vcs_where.append("LOWER(vcs.category) LIKE ?")
            vcs_params.append(f"%{category.lower()}%")
        if memo:
            vcs_where.append("LOWER(vcs.memo) LIKE ?")
            vcs_params.append(f"%{memo.lower()}%")
        if query:
            num_val = parse_numeric_query(query)
            if num_val is not None:
                vcs_where.append("ROUND(ABS(vcs.amount), 2) = ?")
                vcs_params.append(num_val)
            else:
                q_like = f"%{query.strip().lower()}%"
                vcs_where.append("(LOWER(vcs.payee) LIKE ? OR LOWER(vcs.category) LIKE ? OR LOWER(vcs.memo) LIKE ?)")
                vcs_params.extend([q_like, q_like, q_like])

        if vcs_where:
            sql += f" WHERE {' AND '.join(vcs_where)}"
        sql += " ORDER BY vcs.tx_date DESC, vcs.line_id DESC"
        if limit and limit > 0:
            sql += f" LIMIT {limit}"

        cur.execute(sql, vcs_params)
        raw_rows = cur.fetchall()
        results = []
        for r in raw_rows:
            row_dict = dict(r)
            row_dict["id"] = row_dict["id"]
            if row_dict.get("is_split_child"):
                row_dict["memo"] = f"[Split child of #{row_dict['parent_tx_id']}] {row_dict['memo']}".strip()
            results.append(row_dict)
        return results, query_desc

    # --- Mode B: Ledger Register Master View ---
    where_clauses.insert(0, "vt.is_valid_ledger = 1")
    where_str = f"WHERE {' AND '.join(where_clauses)}"

    split_match_sql = ""
    split_subparams: List[Any] = []
    if query:
        num_val = parse_numeric_query(query)
        if num_val is not None:
            split_match_sql = """
            (
                SELECT GROUP_CONCAT(COALESCE(c.szFull, '') || ' (' || printf('%.2f', CAST(ct.amt AS REAL)) || ')', '; ')
                FROM TRN_SPLIT sp
                JOIN TRN ct ON sp.htrn = ct.htrn
                LEFT JOIN CAT c ON ct.hcat = c.hcat
                WHERE sp.htrnParent = vt.tx_id
                  AND ROUND(ABS(CAST(ct.amt AS REAL)), 2) = ?
            )
            """
            split_subparams.append(num_val)
        else:
            q_like = f"%{query.strip().lower()}%"
            split_match_sql = """
            (
                SELECT GROUP_CONCAT(COALESCE(ct.mMemo, c.szFull) || ' (' || printf('%.2f', CAST(ct.amt AS REAL)) || ')', '; ')
                FROM TRN_SPLIT sp
                JOIN TRN ct ON sp.htrn = ct.htrn
                LEFT JOIN CAT c ON ct.hcat = c.hcat
                WHERE sp.htrnParent = vt.tx_id
                  AND (LOWER(ct.mMemo) LIKE ? OR LOWER(COALESCE(c.szFull, '')) LIKE ?)
            )
            """
            split_subparams.extend([q_like, q_like])
    else:
        split_match_sql = "NULL"

    sql = f"""
    SELECT 
        vt.tx_id AS id,
        vt.tx_date AS dt,
        vt.account,
        vt.account_name,
        vt.payee,
        vt.category,
        vt.amount,
        COALESCE(vt.memo, '') AS memo,
        {split_match_sql} AS child_match_notes
    FROM v_transactions vt
    JOIN ACCT a ON vt.account_id = a.hacct
    {where_str}
    ORDER BY vt.tx_date DESC, vt.tx_id DESC
    """
    if limit and limit > 0:
        sql += f" LIMIT {limit}"

    all_params = split_subparams + params
    cur.execute(sql, all_params)
    raw_rows = cur.fetchall()

    results = []
    for r in raw_rows:
        row_dict = dict(r)
        child_notes = row_dict.get("child_match_notes")
        if child_notes:
            if row_dict["memo"]:
                row_dict["memo"] = f"{row_dict['memo']} [Split: {child_notes}]"
            else:
                row_dict["memo"] = f"[Split: {child_notes}]"
        results.append(row_dict)

    return results, query_desc


def main():
    parser = argparse.ArgumentParser(
        description="Search transactions, child splits, payees, and amounts across the ledger.",
        epilog="""examples:
  qashbocks find "HMV"                 # Search across all accounts
  qashbocks find "bluray" -A           # Search active accounts only
  qashbocks find 17.98                 # Search by transaction amount
  qashbocks find "Amazon" -a current   # Search restricted to a specific account
  qashbocks find "Waitrose" --since 2026-01-01  # Search with date bounds
  qashbocks find "bluray" --splits     # Display individual matching child split items
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("query", nargs="?", default=None, help="Search term (text in payee/category/memo or numeric amount)")
    parser.add_argument("-q", "--query", dest="named_query", type=str, default=None, help="Explicit search query string")
    parser.add_argument("-A", "--active", action="store_true", help="Restrict search to active accounts only (default: off, searches all accounts)")
    parser.add_argument("-a", "--account", type=str, default=None, help="Filter by specific account alias or name")
    parser.add_argument("-p", "--payee", type=str, default=None, help="Filter specifically by payee name")
    parser.add_argument("-c", "--category", type=str, default=None, help="Filter specifically by category name")
    parser.add_argument("-m", "--memo", type=str, default=None, help="Filter specifically by memo text")
    parser.add_argument("--min", type=float, default=None, help="Minimum amount")
    parser.add_argument("--max", type=float, default=None, help="Maximum amount")
    parser.add_argument("--from", "--since", dest="date_from", type=str, default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", "--until", dest="date_to", type=str, default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("-n", "--limit", type=int, default=50, help="Maximum results to return (default: 50, use 0 for unlimited)")
    parser.add_argument("--splits", action="store_true", help="Display matching child split line items directly")
    parser.add_argument("--db", type=Path, default=None, help="Path to SQLite database")
    parser.add_argument("--format", choices=["table", "plain", "unicode", "md", "csv", "json"], default="table", help="Output format")

    args = parser.parse_args()
    q = args.named_query or args.query

    if not q and not (args.payee or args.category or args.memo or args.min or args.max or args.account or args.date_from or args.date_to):
        parser.print_help()
        sys.exit(1)

    db_path = args.db or get_default_db()
    if not db_path.exists():
        print(f"Error: Database not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    results, query_desc = search_transactions(
        conn=conn,
        query=q,
        active_only=args.active,
        account=args.account,
        payee=args.payee,
        category=args.category,
        memo=args.memo,
        min_amount=args.min,
        max_amount=args.max,
        date_from=args.date_from,
        date_to=args.date_to,
        limit=args.limit,
        splits_mode=args.splits,
    )
    conn.close()

    if args.format == "json":
        print(json.dumps({"query": query_desc, "active_only": args.active, "count": len(results), "transactions": results}, indent=2))
        return

    if args.format == "csv":
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["ID", "Date", "Account", "Payee", "Category", "Amount", "Memo"])
        for r in results:
            writer.writerow([r.get("id"), r.get("dt"), r.get("account"), r.get("payee"), r.get("category"), r.get("amount"), r.get("memo")])
        print(out.getvalue(), end="")
        return

    ctx = {
        "query_desc": query_desc,
        "count": len(results),
        "limit": args.limit,
        "active_only": args.active,
        "transactions": results,
        "is_plain": args.format == "plain",
        "cli_style": args.format,
    }
    rendered = render_template("search.tpl", ctx)
    print(rendered)


if __name__ == "__main__":
    main()
