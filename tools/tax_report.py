#!/usr/bin/env python3
"""
tools/tax_report.py

HMRC-style Tax Calculation and Reconciliation Tool for any tax year in Qashbocks.
Automatically calculates:
1. Personal allowance (£12,570 default).
2. Taxable income streams (employment, pension, interest).
3. Personal Savings Allowance and tax bands.
4. Net tax position (tax due or refund).
Uses presenter.py and templates/tax.tpl for presentation.
"""

import argparse
import datetime
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

PERSONAL_ALLOWANCES = {
    2020: 12500.0,
    2021: 12570.0,
    2022: 12570.0,
    2023: 12570.0,
    2024: 12570.0,
    2025: 12570.0,
    2026: 12570.0,
    2027: 12570.0,
}


def get_current_tax_year() -> int:
    now = datetime.date.today()
    return now.year if (now.month, now.day) >= (4, 6) else now.year - 1


def get_tax_year_dates(year: int) -> tuple[str, str]:
    return f"{year}-04-06", f"{year + 1}-04-05"


def get_tax_context(conn: sqlite3.Connection, year: int, format_style: str | None = None) -> dict:
    start_dt, end_dt = get_tax_year_dates(year)
    pa = PERSONAL_ALLOWANCES.get(year, 12570.0)
    cur = conn.cursor()

    # Query taxable income from category tree
    cur.execute("""
        SELECT 
            COALESCE(p.szFull, c.szFull, 'Income') AS source_name,
            COUNT(t.htrn) AS count,
            ROUND(SUM(CAST(t.amt AS REAL)), 2) AS total_amt
        FROM TRN t
        JOIN CAT c ON t.hcat = c.hcat
        LEFT JOIN PAY p ON t.lHpay = p.hpay
        WHERE (CAST(t.grftt AS INTEGER) & 2097152) = 0
          AND (CAST(t.grftt AS INTEGER) & 64) = 0
          AND CAST(t.amt AS REAL) > 0
          AND (c.szFull LIKE '%Salary%' OR c.szFull LIKE '%Wage%' OR c.szFull LIKE '%Pension%' OR c.szFull LIKE '%Interest%' OR c.szFull LIKE '%Income%')
          AND SUBSTR(t.dt, 1, 10) >= ? AND SUBSTR(t.dt, 1, 10) <= ?
        GROUP BY source_name
        ORDER BY total_amt DESC;
    """, (start_dt, end_dt))
    rows = cur.fetchall()

    income_sources = []
    total_gross = 0.0
    for r in rows:
        amt = float(r[2] or 0.0)
        total_gross += amt
        income_sources.append({
            "name": r[0],
            "frequency": f"{r[1]} payments",
            "gross": amt,
            "tax_paid": 0.0,
            "net": amt,
        })

    if not income_sources:
        income_sources.append({
            "name": "No taxable income recorded for this period",
            "frequency": "-",
            "gross": 0.0,
            "tax_paid": 0.0,
            "net": 0.0,
        })

    taxable_income = max(0.0, total_gross - pa)
    tax_due = taxable_income * 0.20  # Basic rate

    allowances = [
        {"desc": "Standard Personal Allowance", "amt": pa},
        {"desc": "Total Income Received", "amt": total_gross},
        {"desc": "Net Taxable Income", "amt": taxable_income},
    ]

    bands = [
        {
            "band": "Personal Allowance (0%)",
            "rate": "0%",
            "slice": min(total_gross, pa),
            "tax_due": 0.0,
        },
        {
            "band": "Basic Rate (20%)",
            "rate": "20%",
            "slice": taxable_income,
            "tax_due": tax_due,
        },
    ]

    final_rows = [
        {"item": "Total Tax Liability", "amt": tax_due},
        {"item": "Tax Deducted at Source", "amt": 0.0},
        {"item": "Net Tax Due / (Refund)", "amt": tax_due},
    ]

    return {
        "taxpayer_name": "Account Holder",
        "year": year,
        "year_next": year + 1,
        "start_dt": start_dt,
        "end_dt": end_dt,
        "pa": pa,
        "income_sources": income_sources,
        "total_gross": total_gross,
        "total_tax_at_source": 0.0,
        "total_net": total_gross,
        "isa_interest": 0.0,
        "sp_exempt_total": 0.0,
        "allowances": allowances,
        "bands": bands,
        "total_taxable_slice": taxable_income,
        "total_tax_due": tax_due,
        "final_rows": final_rows,
        "cli_style": format_style,
    }


def generate_tax_report(db_path: Path, year: int, output_file: Path | None = None, format_style: str | None = None):
    conn = sqlite3.connect(db_path)
    ctx = get_tax_context(conn, year, format_style=format_style)
    conn.close()

    output = render_template("tax.tpl", ctx)
    if output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(output, encoding="utf-8")
        print(f"Tax calculation exported to {output_file}")
    else:
        print(output)


def main():
    parser = argparse.ArgumentParser(description="HMRC-style Tax Calculation and Reconciliation Tool.")
    parser.add_argument("-y", "--year", type=int, default=get_current_tax_year(), help="Tax year start (e.g. 2026 for 2026-27)")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Save report to file")
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

    generate_tax_report(args.db, args.year, output_file=args.output, format_style=args.format)


if __name__ == "__main__":
    main()
