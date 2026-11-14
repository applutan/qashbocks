================================================================================
TRANSACTION REGISTER ENTRY INSPECTION: htrn={{ trn.htrn }}
================================================================================
  Date:                 {{ trn.dt | date }}
  Account:              #{{ trn.hacct }} {{ trn.acct_name or 'Unknown' }}
{% if trn.hacctLink %}
  Linked Account:       #{{ trn.hacctLink }} {{ trn.link_acct_name or 'Unknown' }} (Transfer)
{% endif %}
  Amount:               {{ trn.amt | gbp }}{% if trn.amtUser is defined and trn.amtUser %} (User amt: {{ trn.amtUser | gbp }}){% endif %}
  Payee:                {{ ('#' ~ trn.lHpay ~ ' ' ~ trn.payee_name) if trn.lHpay else 'None' }}
  Category:             {{ ('#' ~ trn.hcat ~ ' ' ~ trn.cat_name) if trn.hcat else 'None' }}
  Memo:                 {{ trn.mMemo or 'None' }}
  Status (cs):          {{ trn.cs }} ({{ trn.cs | cs }})
  Flags (grftt):        {{ trn.grftt }} (Bitmask)
  Direct hbillHead:     {{ trn.hbillHead or 'None' }}
  Direct iinst:         {{ trn.iinst if trn.iinst is not none else 'None' }}
{% if trn.frq is defined and trn.frq %}
  Direct frq:           {{ trn.frq }} ({{ trn.frq | freq }})
{% endif %}
{% if trn.dtCleared is defined and trn.dtCleared %}
  dtCleared:            {{ trn.dtCleared }}
{% endif %}

--------------------------------------------------------------------------------
RECURRING BILL LINKAGE ANALYSIS:
--------------------------------------------------------------------------------
{% if bills_direct %}
[A] Direct Foreign Key Link: TRN.hbillHead = {{ trn.hbillHead }}
{% for b in bills_direct %}
  -> Bill #{{ b.hbill }} (Head #{{ b.hbillHead }}, {{ 'MASTER HEAD' if b.hbill == b.hbillHead else 'REVISION' }})
     Status (st):           {{ b.st }} ({{ 'Active' if (b.st | int) == 1 else 'Terminated' }})
     Frequency (frq):      {{ b.frq }} ({{ b.frq | freq }}), cFrqInst={{ b.cFrqInst }}
     Due Date (dt):        {{ b.dt | date }}
     Max Occurrences:      {{ b.cInstMax if (b.cInstMax | int) > 0 else 'Indefinite' }}
     Next Unpaid (iinst):  {{ b.iinstNextUnpaid }} (Head) / Rev start iinst={{ b.iinst }}
     Template TRN (lHtrn): #{{ b.lHtrn }}
     Payee:                {{ b.payee_name or 'None' }}
     Account:              {{ b.acct_name or 'None' }}
     Category:             {{ b.cat_name or 'None' }}
     Amount:               {{ b.bill_amt | gbp }}
{% endfor %}
{% endif %}
{% if bills_template %}

[B] Template Transaction Link: BILL.lHtrn = {{ trn.htrn }}
{% for b in bills_template %}
  -> Bill #{{ b.hbill }} uses this transaction as its recurring template!
     Head: #{{ b.hbillHead }}, Status={{ b.st }} ({{ 'Active' if (b.st | int) == 1 else 'Terminated' }}), Frequency={{ b.frq }} ({{ b.frq | freq }})
     Next Due: {{ b.dt | date }}, Amount: {{ b.bill_amt | gbp }}
{% endfor %}
{% endif %}
{% if matching_bills %}

[C] Candidate Matching Recurring Bills by Payee / Account:
{% for b in matching_bills %}
  -> Bill Head #{{ b.hbillHead }}: {{ b.payee_name }} | Account: {{ b.acct_name }}
     Status: {{ 'Active' if (b.st | int) == 1 else 'Terminated/Closed' }}
     Amount: {{ b.bill_amt | gbp }} | Trn Amount: {{ trn.amt | gbp }}
     Frequency: {{ b.frq | freq }} (cFrqInst={{ b.cFrqInst }}) | Next Due: {{ b.dt | date }}
     Next Unpaid Instance: {{ b.iinstNextUnpaid }}
{% endfor %}
{% endif %}
{% if not bills_direct and not bills_template and not matching_bills %}

  No recurring bill linkages or candidate matches found for this transaction.
{% endif %}
{% if linked_txs %}

All Posted / Linked Register Transactions for Bill #{{ trn.hbillHead }}:
{{ linked_txs | table(
    headers=["htrn", "Date", "Amount", "iinst", "Status", "Memo"],
    keys=["htrn", "tx_dt", "amt", "iinst", "cs", "mMemo"]
) }}
{% endif %}
{% if splits %}

--------------------------------------------------------------------------------
SPLIT LINE ITEMS (Breakdown across categories):
--------------------------------------------------------------------------------
{{ splits | table(
    headers=["#", "Child htrn", "Amount", "Category", "Memo"],
    keys=["iSplit", "child_htrn", "amt", "cat_name", "memo"]
) }}
{% endif %}
{% if split_parent %}

--------------------------------------------------------------------------------
SPLIT PARENT ENTRY:
--------------------------------------------------------------------------------
  This transaction is child line item #{{ split_parent.iSplit }} of parent transaction #{{ split_parent.htrnParent }}
  Parent Payee:  {{ split_parent.parent_payee }}
  Parent Date:   {{ split_parent.parent_date }}
  Parent Total:  {{ split_parent.parent_amt | gbp }}
{% endif %}
{% if xfers %}

--------------------------------------------------------------------------------
TRANSFER DETAILS:
--------------------------------------------------------------------------------
{% for x in xfers %}
  Transfer from htrn={{ x.htrnFrom }} to htrnLink={{ x.htrnLink }}
{% endfor %}
{% endif %}
