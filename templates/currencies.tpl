================================================================================
CURRENCY DEFINITIONS & USAGE AUDIT (CRNC, ACCT, TRN)
================================================================================

1. DEFINED CURRENCIES IN DATABASE:
{{ currencies | table(
    headers=["ID", "ISO", "Symbol", "Currency Name", "Active", "Total Accts", "Total Tx"],
    keys=["id", "iso", "symbol", "name", "active_accounts", "total_accounts", "total_transactions"]
) }}

Total currencies in CRNC: {{ total_currencies }} | In use by accounts: {{ used_count }} | Currencies with active open accounts: {{ active_count }}
{% if accounts %}

2. ACCOUNTS BY CURRENCY DENOMINATION:
{{ accounts | table(
    headers=["ISO", "Account #", "Alias", "Account Name", "Status", "Type", "Transactions"],
    keys=["iso", "account_id", "alias", "account_name", "status", "type", "tx_count"]
) }}
{% endif %}
{% if transfers %}

3. MULTI-CURRENCY TRANSFERS & CONVERSIONS:
{{ transfers | table(
    headers=["Date", "From Account", "From Amt", "To Account", "To Amt"],
    keys=["tx_date", "from_account", "from_amt_str", "to_account", "to_amt_str"]
) }}
{% elif show_transfers %}

3. MULTI-CURRENCY TRANSFERS & CONVERSIONS:
  No inter-currency transfers detected in TRN_XFER.
{% endif %}
