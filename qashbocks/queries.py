"""
queries.py

Centralised repository of SQL statements and query templates used across
Money CLI (moneymaker.py) and reporting tools.

Encapsulates database access behind clean, named SQL constants and templates.
Works alongside canonical SQLite views (v_active_accounts, v_active_bills, v_transactions).
"""

# =============================================================================
# ACCOUNTS
# =============================================================================

GET_ACTIVE_ACCOUNTS = """
SELECT 
    account_id AS id,
    account_name AS name,
    alias,
    short_alias,
    account_type,
    is_tracking,
    current_balance AS balance,
    uncleared_count,
    last_reconciled_balance AS last_rec_bal,
    last_reconciled_date AS last_rec_date,
    transaction_count
FROM v_active_accounts
ORDER BY is_tracking ASC, (account_type = 5) ASC, transaction_count DESC;
"""

GET_ACCOUNT_BY_ID = """
SELECT hacct AS id, szFull AS name, szAls AS alias, amtEndRec, dtEndRec, fClosed
FROM ACCT
WHERE hacct = ?;
"""

GET_ACCOUNT_BY_NAME_OR_ALIAS = """
SELECT hacct AS id, szFull AS name, szAls AS alias, amtEndRec, dtEndRec, fClosed
FROM ACCT
WHERE fClosed = 0 AND (LOWER(szFull) LIKE ? OR LOWER(szAls) = ?);
"""

# =============================================================================
# RECURRING BILLS & SCHEDULES
# =============================================================================

GET_ACTIVE_BILLS = """
SELECT 
    head_id,
    head_st,
    head_next_unpaid,
    latest_hbill,
    latest_st,
    frq,
    cFrqInst,
    revision_dt,
    dtMax,
    cInstMax,
    revision_base_iinst,
    latest_next_unpaid,
    amount,
    payee_id,
    payee,
    category_id,
    category,
    account_id,
    account,
    last_paid_date
FROM v_active_bills;
"""

GET_BILL_BY_ID = """
SELECT 
    head_id,
    head_st,
    head_next_unpaid,
    latest_hbill,
    latest_st,
    frq,
    cFrqInst,
    revision_dt,
    dtMax,
    cInstMax,
    revision_base_iinst,
    latest_next_unpaid,
    amount,
    payee_id,
    payee,
    category_id,
    category,
    account_id,
    account,
    last_paid_date
FROM v_active_bills
WHERE head_id = ?;
"""

GET_BILL_BY_PAYEE_SEARCH = """
SELECT 
    head_id,
    head_st,
    head_next_unpaid,
    latest_hbill,
    latest_st,
    frq,
    cFrqInst,
    revision_dt,
    dtMax,
    cInstMax,
    revision_base_iinst,
    latest_next_unpaid,
    amount,
    payee_id,
    payee,
    category_id,
    category,
    account_id,
    account,
    last_paid_date
FROM v_active_bills
WHERE LOWER(payee) LIKE ?;
"""

ADVANCE_BILL_SCHEDULE = """
UPDATE BILL
SET iinstNextUnpaid = iinstNextUnpaid + ?,
    iinstLastSkipped = ?,
    dtSerial = datetime('now')
WHERE hbill = ?;
"""

# Used by cash flow forecast to track all revisions across time
GET_ACCOUNT_BILL_REVISIONS = """
SELECT 
    b.hbill,
    b.hbillHead,
    b.st,
    b.frq,
    b.cFrqInst,
    b.dt,
    b.dtMax,
    b.cInstMax,
    b.iinst,
    b.iinstNextUnpaid,
    t.amt,
    p.szFull AS payee,
    c.szFull AS category,
    head.st AS head_st,
    head.iinstNextUnpaid AS head_next_unpaid
FROM BILL b
JOIN BILL head ON b.hbillHead = head.hbill AND head.hbill = head.hbillHead
JOIN TRN t ON b.lHtrn = t.htrn
LEFT JOIN PAY p ON t.lHpay = p.hpay
LEFT JOIN CAT c ON t.hcat = c.hcat
WHERE t.hacct = ?
ORDER BY b.hbillHead, CAST(b.iinst AS INTEGER) ASC, CAST(b.hbill AS INTEGER) ASC;
"""

# =============================================================================
# TRANSACTIONS & LEDGER REGISTER
# =============================================================================

GET_REGISTER_BASE = """
SELECT 
    tx_id AS id,
    tx_date AS dt,
    account,
    account_name,
    payee,
    amount AS amt,
    cleared_desc AS cleared,
    category,
    memo,
    check_ref AS chq
FROM v_transactions
WHERE is_valid_ledger = 1
"""

GET_UNRECONCILED_TRANSACTIONS = """
SELECT 
    tx_id,
    tx_date,
    payee,
    amount,
    category,
    memo,
    cleared_status
FROM v_transactions
WHERE account_id = ?
  AND cleared_status != 2
  AND is_valid_ledger = 1
  AND tx_date <= ?
ORDER BY tx_date ASC, tx_id ASC;
"""

UPDATE_TRANSACTION_CLEARED_STATUS = """
UPDATE TRN
SET cs = ?,
    dtSerial = datetime('now')
WHERE htrn = ?;
"""

# =============================================================================
# PAYEES & CATEGORIES
# =============================================================================

GET_ALL_PAYEES = """
SELECT hpay, szFull
FROM PAY
ORDER BY szFull ASC;
"""

GET_ALL_CATEGORIES = """
SELECT hcat, szFull, hcatParent
FROM CAT
ORDER BY szFull ASC;
"""
