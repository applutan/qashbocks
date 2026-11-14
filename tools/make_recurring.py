#!/usr/bin/env python3
"""
tools/make_recurring.py

Converts an existing transaction (htrn) into a recurring bill schedule in Qashbocks.
Clones the transaction into a template record (with grftt=0x200000), inserts a new
master schedule into BILL, and retroactively links the original transaction as instance 0.
"""

import argparse
import calendar
import datetime
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "sample.db"

FREQ_NAMES_TO_CODE = {
    "once": 0,
    "daily": 1,
    "weekly": 2,
    "monthly": 3,
    "quarterly": 4,
    "yearly": 4,
    "annual": 4,
    "biweekly": 5,
    "semi-monthly": 6,
}

CODE_TO_FREQ_NAME = {
    0: "Once",
    1: "Daily",
    2: "Weekly",
    3: "Monthly",
    4: "Yearly",
    5: "Bi-Weekly",
    6: "Semi-Monthly",
}


def add_months(sourcedate: datetime.date, months: int) -> datetime.date:
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month // 12
    month = month % 12 + 1
    day = min(sourcedate.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


def calculate_next_due(tx_date: datetime.date, frq: int, interval: int = 1) -> datetime.date:
    if frq == 1:
        return tx_date + datetime.timedelta(days=interval)
    elif frq == 2:
        return tx_date + datetime.timedelta(days=7 * interval)
    elif frq == 3:
        return add_months(tx_date, interval)
    elif frq == 4:
        return add_months(tx_date, 12 * interval)
    elif frq == 5:
        return tx_date + datetime.timedelta(days=14 * interval)
    return add_months(tx_date, 1)


def make_recurring(
    db_path: Path,
    htrn: int,
    frequency: str | int = "monthly",
    count: int = -1,
    next_due_str: str | None = None,
    interactive: bool = True,
    yes: bool = False,
):
    conn = sqlite3.connect(db_path)
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
        print(f"Error: Transaction htrn={htrn} not found.", file=sys.stderr)
        conn.close()
        sys.exit(1)

    print("=" * 75)
    print(f"CONVERT TRANSACTION TO RECURRING SCHEDULE: #{htrn}")
    print("=" * 75)
    tx_date_str = row['dt'][:10]
    tx_date = datetime.date.fromisoformat(tx_date_str)
    amt = float(row['amt'] or 0.0)
    payee_label = row['payee_name'] or (f"Transfer to {row['link_acct_name']}" if row['hacctLink'] else 'Unknown')
    print(f"  Source Transaction:  #{htrn} on {tx_date_str}")
    print(f"  Account:             #{row['hacct']} {row['acct_name']}")
    if row['hacctLink']:
        print(f"  Linked Account:      #{row['hacctLink']} {row['link_acct_name']}")
    print(f"  Payee:               {payee_label}")
    print(f"  Category:            {row['cat_name'] or 'None'}")
    print(f"  Amount:              £{amt:,.2f}")
    if row['mMemo']:
        print(f"  Memo:                {row['mMemo']}")

    if row['hbillHead']:
        print(f"\n[Warning] Transaction #{htrn} is already attached to Bill Head #{row['hbillHead']} (instance {row['iinst']})!")
        if not yes:
            ans = input("Do you wish to detach and create a new recurring schedule? [y/N]: ").strip().lower()
            if ans not in ("y", "yes"):
                print("Aborted.")
                conn.close()
                return

    # Determine frequency code
    frq_code = 3
    if isinstance(frequency, int):
        frq_code = frequency
    elif isinstance(frequency, str):
        if frequency.isdigit():
            frq_code = int(frequency)
        else:
            frq_code = FREQ_NAMES_TO_CODE.get(frequency.lower().strip(), 3)

    if interactive and not yes:
        print("\n--- Recurrence Parameters ---")
        prompt_frq = input(f"Recurrence frequency (1=Daily, 2=Weekly, 3=Monthly, 4=Yearly) [{frq_code}]: ").strip()
        if prompt_frq.isdigit():
            frq_code = int(prompt_frq)

        prompt_count = input(f"Total instalments in series (or Enter for ongoing) [{count if count > 0 else 'unlimited'}]: ").strip()
        if prompt_count.isdigit():
            count = int(prompt_count)
        elif prompt_count.lower() in ("unlimited", "none", ""):
            count = -1

    # Determine next due date
    if next_due_str:
        next_due = datetime.date.fromisoformat(next_due_str)
    else:
        next_due = calculate_next_due(tx_date, frq_code)
        if interactive and not yes:
            prompt_date = input(f"Next scheduled due date [{next_due.isoformat()}]: ").strip()
            if prompt_date:
                try:
                    next_due = datetime.date.fromisoformat(prompt_date)
                except ValueError:
                    print(f"Invalid date format '{prompt_date}', using {next_due.isoformat()}")

    next_unpaid_instance = 1

    print("\n--- Summary of New Schedule ---")
    print(f"  Master Payee:        {payee_label}")
    print(f"  Amount:              £{amt:,.2f}")
    print(f"  Frequency:           {CODE_TO_FREQ_NAME.get(frq_code, 'Monthly')} (code {frq_code})")
    print(f"  Max Occurrences:     {count if count > 0 else 'Indefinite / Ongoing'}")
    print(f"  Original Tx Link:    Instance 0 (Completed on {tx_date_str})")
    print(f"  Next Due Date:       Instance 1 due on {next_due.isoformat()}")

    if not yes:
        confirm = input("\nCreate this recurring schedule in the database? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Aborted.")
            conn.close()
            return

    # 1. Create Template Transaction in TRN (grftt = 2097152 / 0x200000)
    cur.execute("SELECT COALESCE(MAX(CAST(htrn AS INTEGER)), 0) + 1 FROM TRN")
    template_htrn = cur.fetchone()[0]

    cur.execute("""
        INSERT INTO TRN (
            htrn, hacct, hacctLink, dt, amt, amtUser, lHpay, hcat, mMemo, cs,
            grftt, fUpdated, dtSerial
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, 0,
            2097152, 1, datetime('now')
        );
    """, (
        template_htrn,
        row['hacct'],
        row['hacctLink'],
        f"{next_due.isoformat()} 00:00:00",
        row['amt'],
        row['amtUser'],
        row['lHpay'],
        row['hcat'],
        row['mMemo'],
    ))

    # 2. Create Master Bill in BILL
    cur.execute("SELECT COALESCE(MAX(CAST(hbill AS INTEGER)), 0) + 1 FROM BILL")
    new_hbill = cur.fetchone()[0]

    cur.execute("""
        INSERT INTO BILL (
            hbill, st, frq, cFrqInst, dt, iinstNextUnpaid, dtMax, cInstMax,
            dtSerial, itrnLink, hbillHead, iinst, itrn, lHtrn, iinstLastSkipped,
            cDaysAutoEnter, fUpdated
        ) VALUES (
            ?, 1, ?, 1, ?, ?, '2200-12-31 00:00:00', ?,
            datetime('now'), -1, ?, 0, 0, ?, -1,
            -1, 1
        );
    """, (
        new_hbill,
        frq_code,
        f"{next_due.isoformat()} 00:00:00",
        next_unpaid_instance,
        count,
        new_hbill,
        template_htrn,
    ))

    # 3. Retroactively link original transaction as instance 0
    cur.execute("""
        UPDATE TRN 
        SET hbillHead = ?, iinst = 0, fUpdated = 1, dtSerial = datetime('now')
        WHERE htrn = ?;
    """, (new_hbill, htrn))

    conn.commit()
    conn.close()

    print(f"\nSUCCESS: Recurring Schedule Bill #{new_hbill} created successfully!")
    print(f"  Template Transaction: #{template_htrn}")
    print(f"  Original Transaction #{htrn} linked as instance 0.")
    print(f"  Next upcoming occurrence: {next_due.isoformat()} (instance 1).\n")


def main():
    parser = argparse.ArgumentParser(description="Convert an existing transaction into a recurring bill schedule.")
    parser.add_argument("htrn", type=str, help="Transaction ID (htrn, e.g. 56480 or #56480)")
    parser.add_argument("-f", "--frequency", type=str, default="monthly", help="Recurrence frequency (daily, weekly, monthly, yearly; or 1,2,3,4)")
    parser.add_argument("-n", "--count", type=int, default=-1, help="Total number of instalments/occurrences in series (-1 for indefinite)")
    parser.add_argument("-d", "--next-due", dest="next_due", type=str, default=None, help="Next scheduled due date (YYYY-MM-DD)")
    parser.add_argument("-y", "--yes", action="store_true", help="Non-interactive automatic confirmation")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"Path to database (default: {DEFAULT_DB})")
    args = parser.parse_args()

    clean_htrn = int(args.htrn.lstrip("#"))
    make_recurring(
        args.db,
        clean_htrn,
        frequency=args.frequency,
        count=args.count,
        next_due_str=args.next_due,
        interactive=not args.yes,
        yes=args.yes,
    )


if __name__ == "__main__":
    main()
