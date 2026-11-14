# Tax Calculation Summary ({{ year }}–{{ year_next }})

**Taxpayer:** {{ taxpayer_name or "Account Holder" }}
**Tax Year:** {{ year }} to {{ year_next }} ({{ start_dt }} to {{ end_dt }})
**Personal Allowance:** {{ pa | gbp }}

---

## 1. Income & Tax Deducted at Source

{{ income_sources | table(
    headers=["Income Source", "Frequency / Status", "Gross Amount", "Tax Paid at Source", "Net Received"],
    keys=["name", "frequency", "gross", "tax_paid", "net"],
    style=style or "markdown",
    footer=["Total Income", "", total_gross, total_tax_at_source, total_net]
) }}
{% if isa_interest > 0 %}

> [!NOTE]
> Cash ISA interest ({{ isa_interest | gbp }}) is tax-exempt and excluded from taxable income.
{% endif %}
{% if sp_exempt_total > 0 %}

> [!NOTE]
> Statutorily tax-free DWP state payments ({{ sp_exempt_total | gbp }}, e.g. Christmas Bonus / Winter Fuel Payment) are excluded from taxable State Pension.
{% endif %}

---

## 2. Allowances & Taxable Income

{{ allowances | table(
    headers=["Description", "Amount"],
    keys=["desc", "amt"],
    style=style or "markdown"
) }}

---

## 3. Tax Calculation by Band

{{ bands | table(
    headers=["Band / Allowance", "Rate", "Taxable Slice", "Tax Due"],
    keys=["band", "rate", "slice", "tax_due"],
    style=style or "markdown",
    footer=["Total Tax Payable", "", total_taxable_slice, total_tax_due]
) }}

---

## 4. Final Position

{{ final_rows | table(
    headers=["Item", "Amount"],
    keys=["item", "amt"],
    style=style or "markdown"
) }}
