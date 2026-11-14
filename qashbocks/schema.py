"""
qashbocks.schema

SQLite schema definitions, performance indexing, canonical reporting views,
and rich sample database generation.
"""

import datetime
import random
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

TABLE_DEFINITIONS = [
    """
    CREATE TABLE IF NOT EXISTS ACCT (
        hacct INTEGER PRIMARY KEY AUTOINCREMENT,
        szFull TEXT NOT NULL,
        szAls TEXT DEFAULT '',
        at INTEGER DEFAULT 1,          -- Account Type (1: Checking, 3: Savings, 4: Credit Card, 5: Investment/Asset)
        fClosed INTEGER DEFAULT 0,     -- 0: Open/Active, 1: Closed
        amtLimit REAL DEFAULT 0.0,     -- Credit Limit / Overdraft Limit
        amtOpenRec REAL DEFAULT 0.0,   -- Statement reconciliation opening balance
        dtOpenRec TEXT,                -- Statement reconciliation opening date
        amtEndRec REAL DEFAULT 0.0,    -- Last statement reconciled ending balance
        dtEndRec TEXT                  -- Last statement reconciliation date
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS PAY (
        hpay INTEGER PRIMARY KEY AUTOINCREMENT,
        szFull TEXT NOT NULL UNIQUE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS CAT (
        hcat INTEGER PRIMARY KEY AUTOINCREMENT,
        szFull TEXT NOT NULL,
        hcatParent INTEGER DEFAULT NULL,
        FOREIGN KEY (hcatParent) REFERENCES CAT(hcat)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS TRN (
        htrn INTEGER PRIMARY KEY AUTOINCREMENT,
        hacct INTEGER NOT NULL,
        hacctLink INTEGER DEFAULT NULL,
        lHpay INTEGER DEFAULT NULL,
        hcat INTEGER DEFAULT NULL,
        amt REAL NOT NULL,
        dt TEXT NOT NULL,
        mMemo TEXT DEFAULT '',
        cs INTEGER DEFAULT 0,          -- Cleared Status (0: Uncleared, 1: Cleared, 2: Reconciled)
        grftt INTEGER DEFAULT 0,       -- Bitmask (0x20: Split Parent, 0x40: Split Child, 0x200000: Recurring Bill Template)
        szId TEXT DEFAULT '',          -- Check / Reference ID
        hbillHead INTEGER DEFAULT NULL,
        iinst INTEGER DEFAULT -1,
        FOREIGN KEY (hacct) REFERENCES ACCT(hacct),
        FOREIGN KEY (lHpay) REFERENCES PAY(hpay),
        FOREIGN KEY (hcat) REFERENCES CAT(hcat)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS TRN_SPLIT (
        htrn INTEGER NOT NULL,
        htrnParent INTEGER NOT NULL,
        iSplit INTEGER NOT NULL,
        PRIMARY KEY (htrnParent, iSplit)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS TRN_XFER (
        htrnFrom INTEGER NOT NULL,
        htrnLink INTEGER NOT NULL,
        PRIMARY KEY (htrnFrom, htrnLink)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS BILL (
        hbill INTEGER PRIMARY KEY AUTOINCREMENT,
        hbillHead INTEGER NOT NULL,
        lHtrn INTEGER NOT NULL,
        st INTEGER DEFAULT 1,          -- 1: Active, 0: Terminated
        frq INTEGER NOT NULL,          -- 1: Daily, 2: Weekly, 3: Monthly, 4: Quarterly, 5: Yearly
        cFrqInst REAL DEFAULT 1.0,     -- Frequency multiplier (e.g. 0.25 for 4-weekly, 0.5 for bi-monthly)
        dt TEXT NOT NULL,              -- Next due date
        dtMax TEXT DEFAULT '',         -- Expiration / series cutoff
        cInstMax INTEGER DEFAULT -1,   -- Max instances (-1 for indefinite series)
        iinst INTEGER DEFAULT 0,
        iinstNextUnpaid INTEGER DEFAULT 0
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS _sync_meta (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """
]

INDEX_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS idx_trn_acct_dt ON TRN(hacct, dt);",
    "CREATE INDEX IF NOT EXISTS idx_trn_dt ON TRN(dt);",
    "CREATE INDEX IF NOT EXISTS idx_trn_payee ON TRN(lHpay);",
    "CREATE INDEX IF NOT EXISTS idx_trn_cat ON TRN(hcat);",
    "CREATE INDEX IF NOT EXISTS idx_trn_billhead ON TRN(hbillHead);",
    "CREATE INDEX IF NOT EXISTS idx_bill_head ON BILL(hbillHead, st);",
    "CREATE INDEX IF NOT EXISTS idx_split_parent ON TRN_SPLIT(htrnParent);",
    "CREATE INDEX IF NOT EXISTS idx_split_child ON TRN_SPLIT(htrn);",
    "CREATE INDEX IF NOT EXISTS idx_xfer_from ON TRN_XFER(htrnFrom);",
    "CREATE INDEX IF NOT EXISTS idx_xfer_link ON TRN_XFER(htrnLink);",
]


def alias_sql(prefix: str = "a") -> str:
    return f"""
    CASE
        WHEN COALESCE({prefix}.szAls, '') != '' THEN {prefix}.szAls
        WHEN LOWER({prefix}.szFull) LIKE '%current%' OR LOWER({prefix}.szFull) LIKE '%check%' THEN 'current'
        WHEN LOWER({prefix}.szFull) LIKE '%saving%' OR LOWER({prefix}.szFull) LIKE '%s2%' THEN 'savings'
        WHEN LOWER({prefix}.szFull) LIKE '%amex%' THEN 'amex'
        WHEN LOWER({prefix}.szFull) LIKE '%visa%' THEN 'visa'
        WHEN LOWER({prefix}.szFull) LIKE '%cash%' OR LOWER({prefix}.szFull) LIKE '%lucre%' THEN 'cash'
        WHEN LOWER({prefix}.szFull) LIKE '%eur%' THEN 'euro'
        ELSE LOWER(REPLACE({prefix}.szFull, ' ', '_'))
    END
    """


VIEW_STATEMENTS = [
    # 1. Active Accounts View
    f"""
    CREATE VIEW IF NOT EXISTS v_active_accounts AS
    SELECT 
        a.hacct AS account_id,
        a.szFull AS account_name,
        a.szAls AS alias,
        {alias_sql('a')} AS short_alias,
        CAST(a.at AS INTEGER) AS account_type,
        0 AS is_tracking,
        CASE 
            WHEN a.at = 5 THEN 0.0
            WHEN a.amtEndRec IS NOT NULL AND a.amtEndRec != 0.0 THEN
                ROUND(CAST(a.amtEndRec AS REAL) + COALESCE((
                    SELECT SUM(CAST(t.amt AS REAL))
                    FROM TRN t
                    WHERE t.hacct = a.hacct
                      AND t.cs != 2
                      AND (CAST(t.grftt AS INTEGER) & 2097152) = 0
                      AND (CAST(t.grftt AS INTEGER) & 64) = 0
                      AND SUBSTR(t.dt, 1, 10) <= date('now')
                ), 0.0), 2)
            ELSE
                ROUND(COALESCE((
                    SELECT SUM(CAST(t.amt AS REAL))
                    FROM TRN t
                    WHERE t.hacct = a.hacct
                      AND (CAST(t.grftt AS INTEGER) & 2097152) = 0
                      AND (CAST(t.grftt AS INTEGER) & 64) = 0
                      AND SUBSTR(t.dt, 1, 10) <= date('now')
                ), 0.0), 2)
        END AS current_balance,
        (
            SELECT COUNT(*)
            FROM TRN t
            WHERE t.hacct = a.hacct AND t.cs = 0
              AND (CAST(t.grftt AS INTEGER) & 2097152) = 0
              AND (CAST(t.grftt AS INTEGER) & 64) = 0
        ) AS uncleared_count,
        ROUND(CAST(a.amtEndRec AS REAL), 2) AS last_reconciled_balance,
        a.dtEndRec AS last_reconciled_date,
        (SELECT COUNT(*) FROM TRN t WHERE t.hacct = a.hacct) AS transaction_count,
        (SELECT MAX(t.dt) FROM TRN t WHERE t.hacct = a.hacct) AS latest_transaction_date,
        a.fClosed AS is_closed
    FROM ACCT a;
    """,

    # 2. Active Recurring Bills View
    f"""
    CREATE VIEW IF NOT EXISTS v_active_bills AS
    WITH ranked_revisions AS (
        SELECT 
            b.hbill AS rev_hbill,
            b.hbillHead,
            b.st AS rev_st,
            b.frq,
            b.cFrqInst,
            b.dt AS revision_dt,
            b.dtMax,
            b.cInstMax,
            b.iinst AS revision_base_iinst,
            b.iinstNextUnpaid AS latest_next_unpaid,
            b.lHtrn,
            ROW_NUMBER() OVER (
                PARTITION BY b.hbillHead 
                ORDER BY CAST(b.iinst AS INTEGER) DESC, CAST(b.hbill AS INTEGER) DESC
            ) AS rn
        FROM BILL b
        JOIN BILL head ON b.hbillHead = head.hbill AND head.hbill = head.hbillHead
        WHERE CAST(b.iinst AS INTEGER) <= CAST(head.iinstNextUnpaid AS INTEGER)
    )
    SELECT 
        head.hbillHead AS head_id,
        head.st AS head_st,
        head.iinstNextUnpaid AS head_next_unpaid,
        COALESCE(r.rev_hbill, head.hbill) AS latest_hbill,
        COALESCE(r.rev_st, head.st) AS latest_st,
        COALESCE(r.frq, head.frq) AS frq,
        COALESCE(r.cFrqInst, head.cFrqInst) AS cFrqInst,
        COALESCE(r.revision_dt, head.dt) AS revision_dt,
        COALESCE(r.dtMax, head.dtMax) AS dtMax,
        COALESCE(r.cInstMax, head.cInstMax) AS cInstMax,
        COALESCE(r.revision_base_iinst, head.iinst) AS revision_base_iinst,
        COALESCE(r.latest_next_unpaid, head.iinstNextUnpaid) AS latest_next_unpaid,
        t.amt AS amount,
        p.hpay AS payee_id,
        COALESCE(p.szFull, 'Unknown Payee') AS payee,
        c.hcat AS category_id,
        CASE 
            WHEN x_from.htrnLink IS NOT NULL THEN '-> ' || {alias_sql('a_to')}
            WHEN x_link.htrnFrom IS NOT NULL THEN '-> ' || {alias_sql('a_from')}
            WHEN sp.htrnParent IS NOT NULL THEN '[Split]'
            ELSE COALESCE(c.szFull, 'Uncategorized')
        END AS category,
        a.hacct AS account_id,
        {alias_sql('a')} AS account,
        a.szFull AS account_name,
        (
            SELECT MAX(SUBSTR(trn_hist.dt, 1, 10))
            FROM TRN trn_hist
            WHERE trn_hist.hbillHead = head.hbillHead
              AND (CAST(trn_hist.grftt AS INTEGER) & 2097152) = 0
              AND (CAST(trn_hist.grftt AS INTEGER) & 64) = 0
              AND SUBSTR(trn_hist.dt, 1, 10) <= date('now')
        ) AS last_paid_date
    FROM BILL head
    LEFT JOIN ranked_revisions r ON head.hbill = r.hbillHead AND r.rn = 1
    JOIN TRN t ON COALESCE(r.lHtrn, head.lHtrn) = t.htrn
    JOIN ACCT a ON t.hacct = a.hacct
    LEFT JOIN PAY p ON t.lHpay = p.hpay
    LEFT JOIN CAT c ON t.hcat = c.hcat
    LEFT JOIN TRN_XFER x_from ON t.htrn = x_from.htrnFrom
    LEFT JOIN TRN t_to ON x_from.htrnLink = t_to.htrn
    LEFT JOIN ACCT a_to ON t_to.hacct = a_to.hacct
    LEFT JOIN TRN_XFER x_link ON t.htrn = x_link.htrnLink
    LEFT JOIN TRN t_from ON x_link.htrnFrom = t_from.htrn
    LEFT JOIN ACCT a_from ON t_from.hacct = a_from.hacct
    LEFT JOIN (SELECT DISTINCT htrnParent FROM TRN_SPLIT) sp ON t.htrn = sp.htrnParent
    WHERE head.hbill = head.hbillHead;
    """,

    # 3. Clean Transaction Ledger View
    f"""
    CREATE VIEW IF NOT EXISTS v_transactions AS
    SELECT 
        t.htrn AS tx_id,
        t.hacct AS account_id,
        {alias_sql('a')} AS account,
        a.szFull AS account_name,
        SUBSTR(t.dt, 1, 10) AS tx_date,
        CAST(t.amt AS REAL) AS amount,
        COALESCE(p.szFull, 'Transfer / Other') AS payee,
        CASE 
            WHEN x_from.htrnLink IS NOT NULL THEN '-> ' || {alias_sql('a_to')}
            WHEN x_link.htrnFrom IS NOT NULL THEN '-> ' || {alias_sql('a_from')}
            WHEN sp.htrnParent IS NOT NULL THEN '[Split]'
            ELSE COALESCE(c.szFull, 'Uncategorized')
        END AS category,
        COALESCE(t.mMemo, '') AS memo,
        t.cs AS cleared_status,
        CASE t.cs
            WHEN 0 THEN 'Uncleared'
            WHEN 1 THEN 'Cleared'
            WHEN 2 THEN 'Reconciled'
            ELSE 'Unknown'
        END AS cleared_desc,
        t.szId AS check_ref,
        t.hbillHead AS bill_head_id,
        t.iinst AS bill_instance,
        (CAST(t.grftt AS INTEGER) & 2097152) = 0 AND (CAST(t.grftt AS INTEGER) & 64) = 0 AS is_valid_ledger
    FROM TRN t
    JOIN ACCT a ON t.hacct = a.hacct
    LEFT JOIN PAY p ON t.lHpay = p.hpay
    LEFT JOIN CAT c ON t.hcat = c.hcat
    LEFT JOIN TRN_XFER x_from ON t.htrn = x_from.htrnFrom
    LEFT JOIN TRN t_to ON x_from.htrnLink = t_to.htrn
    LEFT JOIN ACCT a_to ON t_to.hacct = a_to.hacct
    LEFT JOIN TRN_XFER x_link ON t.htrn = x_link.htrnLink
    LEFT JOIN TRN t_from ON x_link.htrnFrom = t_from.htrn
    LEFT JOIN ACCT a_from ON t_from.hacct = a_from.hacct
    LEFT JOIN (SELECT DISTINCT htrnParent FROM TRN_SPLIT) sp ON t.htrn = sp.htrnParent;
    """,

    # 4. Canonical Line Items / Category Spending View
    f"""
    CREATE VIEW IF NOT EXISTS v_category_spending AS
    SELECT 
        t.htrn AS line_id,
        t.htrn AS parent_tx_id,
        0 AS is_split_child,
        t.hacct AS account_id,
        {alias_sql('a')} AS account,
        a.szFull AS account_name,
        SUBSTR(t.dt, 1, 10) AS tx_date,
        CAST(t.amt AS REAL) AS amount,
        COALESCE(p.szFull, 'Transfer / Other') AS payee,
        c.hcat AS category_id,
        CASE 
            WHEN x_from.htrnLink IS NOT NULL THEN '-> ' || {alias_sql('a_to')}
            WHEN x_link.htrnFrom IS NOT NULL THEN '-> ' || {alias_sql('a_from')}
            ELSE COALESCE(c.szFull, 'Uncategorized')
        END AS category,
        COALESCE(t.mMemo, '') AS memo
    FROM TRN t
    JOIN ACCT a ON t.hacct = a.hacct
    LEFT JOIN PAY p ON t.lHpay = p.hpay
    LEFT JOIN CAT c ON t.hcat = c.hcat
    LEFT JOIN TRN_XFER x_from ON t.htrn = x_from.htrnFrom
    LEFT JOIN TRN t_to ON x_from.htrnLink = t_to.htrn
    LEFT JOIN ACCT a_to ON t_to.hacct = a_to.hacct
    LEFT JOIN TRN_XFER x_link ON t.htrn = x_link.htrnLink
    LEFT JOIN TRN t_from ON x_link.htrnFrom = t_from.htrn
    LEFT JOIN ACCT a_from ON t_from.hacct = a_from.hacct
    LEFT JOIN (SELECT DISTINCT htrnParent FROM TRN_SPLIT) sp ON t.htrn = sp.htrnParent
    WHERE sp.htrnParent IS NULL
      AND (CAST(t.grftt AS INTEGER) & 2097152) = 0
      AND (CAST(t.grftt AS INTEGER) & 64) = 0

    UNION ALL

    SELECT 
        c_trn.htrn AS line_id,
        sp.htrnParent AS parent_tx_id,
        1 AS is_split_child,
        p_trn.hacct AS account_id,
        {alias_sql('a')} AS account,
        a.szFull AS account_name,
        SUBSTR(p_trn.dt, 1, 10) AS tx_date,
        CAST(c_trn.amt AS REAL) AS amount,
        COALESCE(p.szFull, 'Unknown Payee') AS payee,
        c_cat.hcat AS category_id,
        COALESCE(c_cat.szFull, 'Uncategorized') AS category,
        COALESCE(c_trn.mMemo, '') AS memo
    FROM TRN_SPLIT sp
    JOIN TRN c_trn ON sp.htrn = c_trn.htrn
    JOIN TRN p_trn ON sp.htrnParent = p_trn.htrn
    JOIN ACCT a ON p_trn.hacct = a.hacct
    LEFT JOIN PAY p ON p_trn.lHpay = p.hpay
    LEFT JOIN CAT c_cat ON c_trn.hcat = c_cat.hcat
    WHERE (CAST(p_trn.grftt AS INTEGER) & 2097152) = 0;
    """
]


def init_schema(conn: sqlite3.Connection):
    cur = conn.cursor()
    for stmt in TABLE_DEFINITIONS:
        cur.execute(stmt)
    for stmt in INDEX_STATEMENTS:
        cur.execute(stmt)
    for stmt in VIEW_STATEMENTS:
        cur.execute(stmt)
    conn.commit()


def init_db(db_path: Path, populate_sample: bool = False):
    """Initializes a new database schema or sample database."""
    if populate_sample:
        generate_sample_database(db_path)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        init_schema(conn)
        conn.close()


def generate_sample_database(db_path: Path):
    """Generates a rich, realistic demonstration database for Qashbocks."""
    if db_path.exists():
        db_path.unlink()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    init_schema(conn)
    cur = conn.cursor()

    # 1. Accounts
    accounts = [
        ("Checking Account", "current", 1, 0, 1854.20, "2026-10-31"),
        ("Rainy Day Savings", "savings", 3, 0, 12500.00, "2026-10-31"),
        ("Rewards Credit Card", "amex", 4, 0, -214.50, "2026-10-25"),
        ("Everyday Visa", "visa", 4, 0, -42.80, "2026-10-20"),
        ("Cash Wallet", "cash", 1, 0, 65.00, "2026-10-31"),
        ("Euro Travel Wallet", "euro", 1, 0, 180.00, "2026-10-31"),
        ("Old Student Bank", "student", 1, 1, 0.00, "2018-05-15"),  # Closed
    ]
    cur.executemany("""
        INSERT INTO ACCT (szFull, szAls, at, fClosed, amtEndRec, dtEndRec)
        VALUES (?, ?, ?, ?, ?, ?);
    """, accounts)

    # 2. Categories
    categories = [
        ("Food:Groceries", None),
        ("Food:Dining Out", None),
        ("Food:Coffee", None),
        ("Housing:Rent", None),
        ("Housing:Home Insurance", None),
        ("Utilities:Electricity", None),
        ("Utilities:Broadband", None),
        ("Utilities:Water", None),
        ("Utilities:Mobile", None),
        ("Entertainment:Film", None),
        ("Entertainment:Music", None),
        ("Entertainment:Streaming", None),
        ("Books:Fiction", None),
        ("Books:Non-Fiction", None),
        ("Books:Technical", None),
        ("Transport:Fuel", None),
        ("Transport:Transit", None),
        ("Income:Salary", None),
        ("Income:Interest", None),
        ("Income:Consulting", None),
        ("Healthcare:Pharmacy", None),
        ("Household:Supplies", None),
    ]
    cur.executemany("INSERT INTO CAT (szFull, hcatParent) VALUES (?, ?);", categories)

    # 3. Payees
    payees = [
        "Waitrose", "Sainsbury's", "Tesco", "Local Artisan Bakery", "The Coffee House",
        "Pho Cafe", "St Sushi", "Apex Property Management", "Octopus Energy", "Zen Internet",
        "Northumbrian Water", "Criterion Collection", "HMV", "Bandcamp", "Rough Trade",
        "Waterstones", "Blackwell's", "Amazon", "Shell Fuel", "Transport for London",
        "Acme Corp (Employer)", "Yorkshire Building Society", "Boots Chemist", "IKEA"
    ]
    cur.executemany("INSERT INTO PAY (szFull) VALUES (?);", [(p,) for p in payees])

    # Helper maps
    acct_map = {row[0]: row[1] for row in cur.execute("SELECT szAls, hacct FROM ACCT;").fetchall()}
    cat_map = {row[0]: row[1] for row in cur.execute("SELECT szFull, hcat FROM CAT;").fetchall()}
    pay_map = {row[0]: row[1] for row in cur.execute("SELECT szFull, hpay FROM PAY;").fetchall()}

    # 4. Recurring Bills
    bills = [
        ("Apex Property Management", "current", "Housing:Rent", -850.00, 3, 1.0, "2026-12-01"),
        ("Octopus Energy", "current", "Utilities:Electricity", -125.00, 3, 1.0, "2026-11-28"),
        ("Zen Internet", "current", "Utilities:Broadband", -38.50, 3, 1.0, "2026-11-20"),
        ("Northumbrian Water", "current", "Utilities:Water", -24.00, 3, 1.0, "2026-11-17"),
        ("Acme Corp (Employer)", "current", "Income:Salary", 2850.00, 3, 1.0, "2026-11-28"),
        ("Rewards Credit Card", "current", None, -214.50, 3, 1.0, "2026-11-22"),  # Transfer payoff
    ]

    for payee_name, acct_alias, cat_name, amt, frq, c_frq, next_due in bills:
        # Create template transaction
        hacct = acct_map[acct_alias]
        hpay = pay_map.get(payee_name)
        hcat = cat_map.get(cat_name)
        hacct_link = acct_map["amex"] if payee_name == "Rewards Credit Card" else None

        cur.execute("""
            INSERT INTO TRN (hacct, hacctLink, lHpay, hcat, amt, dt, mMemo, cs, grftt)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 2097152);
        """, (hacct, hacct_link, hpay, hcat, amt, next_due, f"Recurring {payee_name}"))
        l_htrn = cur.lastrowid

        cur.execute("""
            INSERT INTO BILL (hbillHead, lHtrn, st, frq, cFrqInst, dt, iinstNextUnpaid)
            VALUES (0, ?, 1, ?, ?, ?, 0);
        """, (l_htrn, frq, c_frq, next_due))
        hbill = cur.lastrowid
        cur.execute("UPDATE BILL SET hbillHead = ? WHERE hbill = ?;", (hbill, hbill))
        cur.execute("UPDATE TRN SET hbillHead = ? WHERE htrn = ?;", (hbill, l_htrn))

    # 5. Realistic Historical & Recent Transactions (last 60 days)
    today = datetime.date(2026, 11, 14)
    base_date = today - datetime.timedelta(days=60)

    # Regular expenses
    expenses = [
        (3, "current", "Waitrose", "Food:Groceries", -64.20, "Weekly groceries", 2),
        (5, "current", "The Coffee House", "Food:Coffee", -3.60, "Espresso & croissant", 2),
        (8, "amex", "Waterstones", "Books:Technical", -28.00, "Designing Data-Intensive Applications", 2),
        (10, "current", "Shell Fuel", "Transport:Fuel", -45.00, "Unleaded fuel", 2),
        (14, "current", "Zen Internet", "Utilities:Broadband", -38.50, "Direct debit", 2),
        (15, "amex", "St Sushi", "Food:Dining Out", -42.50, "Dinner with colleagues", 2),
        (18, "current", "Waitrose", "Food:Groceries", -58.10, "Groceries", 2),
        (22, "current", "Octopus Energy", "Utilities:Electricity", -125.00, "Direct debit", 2),
        (25, "current", "The Coffee House", "Food:Coffee", -3.70, "Flat white", 2),
        (28, "current", "Acme Corp (Employer)", "Income:Salary", 2850.00, "Monthly payroll", 2),
        (30, "current", "Apex Property Management", "Housing:Rent", -850.00, "Standing order", 2),
        (32, "visa", "Boots Chemist", "Healthcare:Pharmacy", -14.25, "Vitamins and supplies", 2),
        (35, "current", "Waitrose", "Food:Groceries", -72.40, "Weekly shop", 1),
        (38, "amex", "Amazon", "Entertainment:Streaming", -8.99, "Prime subscription", 1),
        (40, "current", "The Coffee House", "Food:Coffee", -3.70, "Morning coffee", 1),
        (43, "visa", "Local Artisan Bakery", "Food:Groceries", -8.50, "Sourdough loaf", 1),
        (45, "current", "Pho Cafe", "Food:Dining Out", -26.00, "Lunch", 1),
        (48, "current", "Waitrose", "Food:Groceries", -61.30, "Groceries", 1),
        (52, "amex", "Rough Trade", "Entertainment:Music", -24.99, "Vinyl record [LP]", 1),
        (55, "current", "Octopus Energy", "Utilities:Electricity", -125.00, "Direct debit", 0),
        (57, "current", "The Coffee House", "Food:Coffee", -3.70, "Coffee on the hoof", 0),
        (58, "visa", "Sainsbury's", "Food:Groceries", -18.40, "Mid-week essentials", 0),
    ]

    for day_offset, acct_alias, payee, cat, amt, memo, cs in expenses:
        dt_str = (base_date + datetime.timedelta(days=day_offset)).isoformat()
        hacct = acct_map[acct_alias]
        hpay = pay_map[payee]
        hcat = cat_map[cat]
        cur.execute("""
            INSERT INTO TRN (hacct, lHpay, hcat, amt, dt, mMemo, cs, grftt)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0);
        """, (hacct, hpay, hcat, amt, dt_str, memo, cs))

    # 6. Inter-Account Transfers
    # Cash withdrawal
    dt_cash = (today - datetime.timedelta(days=20)).isoformat()
    cur.execute("""
        INSERT INTO TRN (hacct, hacctLink, amt, dt, mMemo, cs)
        VALUES (?, ?, -50.00, ?, 'ATM cash withdrawal', 2);
    """, (acct_map["current"], acct_map["cash"], dt_cash))
    trn_from = cur.lastrowid

    cur.execute("""
        INSERT INTO TRN (hacct, hacctLink, amt, dt, mMemo, cs)
        VALUES (?, ?, 50.00, ?, 'ATM cash withdrawal', 2);
    """, (acct_map["cash"], acct_map["current"], dt_cash))
    trn_to = cur.lastrowid

    cur.execute("INSERT INTO TRN_XFER (htrnFrom, htrnLink) VALUES (?, ?);", (trn_from, trn_to))

    # Credit card payoff
    dt_payoff = (today - datetime.timedelta(days=22)).isoformat()
    cur.execute("""
        INSERT INTO TRN (hacct, hacctLink, amt, dt, mMemo, cs)
        VALUES (?, ?, -180.00, ?, 'Amex monthly statement payment', 2);
    """, (acct_map["current"], acct_map["amex"], dt_payoff))
    trn_pay_from = cur.lastrowid

    cur.execute("""
        INSERT INTO TRN (hacct, hacctLink, amt, dt, mMemo, cs)
        VALUES (?, ?, 180.00, ?, 'Payment received - Thank you', 2);
    """, (acct_map["amex"], acct_map["current"], dt_payoff))
    trn_pay_to = cur.lastrowid

    cur.execute("INSERT INTO TRN_XFER (htrnFrom, htrnLink) VALUES (?, ?);", (trn_pay_from, trn_pay_to))

    # 7. Split Transactions (Multi-Item Receipts)
    # Split 1: HMV Film Purchases (today)
    dt_hmv = today.isoformat()
    cur.execute("""
        INSERT INTO TRN (hacct, lHpay, amt, dt, mMemo, cs, grftt)
        VALUES (?, ?, -17.98, ?, '', 0, 32);
    """, (acct_map["current"], pay_map["HMV"], dt_hmv))
    hmv_parent = cur.lastrowid

    cur.execute("""
        INSERT INTO TRN (hacct, lHpay, hcat, amt, dt, mMemo, cs, grftt)
        VALUES (?, ?, ?, -9.99, ?, 'Sisu [bluray]', 0, 64);
    """, (acct_map["current"], pay_map["HMV"], cat_map["Entertainment:Film"], dt_hmv))
    hmv_c1 = cur.lastrowid

    cur.execute("""
        INSERT INTO TRN (hacct, lHpay, hcat, amt, dt, mMemo, cs, grftt)
        VALUES (?, ?, ?, -7.99, ?, 'Kokodi Kokoda [bluray]', 0, 64);
    """, (acct_map["current"], pay_map["HMV"], cat_map["Entertainment:Film"], dt_hmv))
    hmv_c2 = cur.lastrowid

    cur.execute("INSERT INTO TRN_SPLIT (htrn, htrnParent, iSplit) VALUES (?, ?, 0);", (hmv_c1, hmv_parent))
    cur.execute("INSERT INTO TRN_SPLIT (htrn, htrnParent, iSplit) VALUES (?, ?, 1);", (hmv_c2, hmv_parent))

    # Split 2: IKEA Home & Plants (7 days ago)
    dt_ikea = (today - datetime.timedelta(days=7)).isoformat()
    cur.execute("""
        INSERT INTO TRN (hacct, lHpay, amt, dt, mMemo, cs, grftt)
        VALUES (?, ?, -42.50, ?, 'Weekend home improvements', 1, 32);
    """, (acct_map["current"], pay_map["IKEA"], dt_ikea))
    ikea_parent = cur.lastrowid

    cur.execute("""
        INSERT INTO TRN (hacct, lHpay, hcat, amt, dt, mMemo, cs, grftt)
        VALUES (?, ?, ?, -28.50, ?, 'FÖRHÖJA Kitchen cart shelf', 1, 64);
    """, (acct_map["current"], pay_map["IKEA"], cat_map["Household:Supplies"], dt_ikea))
    ikea_c1 = cur.lastrowid

    cur.execute("""
        INSERT INTO TRN (hacct, lHpay, hcat, amt, dt, mMemo, cs, grftt)
        VALUES (?, ?, ?, -14.00, ?, 'Swedish meatballs & cinnamon buns', 1, 64);
    """, (acct_map["current"], pay_map["IKEA"], cat_map["Food:Groceries"], dt_ikea))
    ikea_c2 = cur.lastrowid

    cur.execute("INSERT INTO TRN_SPLIT (htrn, htrnParent, iSplit) VALUES (?, ?, 0);", (ikea_c1, ikea_parent))
    cur.execute("INSERT INTO TRN_SPLIT (htrn, htrnParent, iSplit) VALUES (?, ?, 1);", (ikea_c2, ikea_parent))

    conn.commit()
    conn.close()


if __name__ == "__main__":
    import sys
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/sample.db")
    generate_sample_database(out)
    print(f"Generated sample database at: {out} ({out.stat().st_size:,} bytes)")
