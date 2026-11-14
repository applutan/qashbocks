{% if is_all %}
Register: {{ scope_str }}
{{ transactions | table(
    headers=["ID", "Date", "Account", "Payee", "Category", "Amount", "Status"],
    keys=["id", "dt", "account", "payee", "category", "amount", "cs"]
) }}
{% else %}
Register: {{ acct.name }} ({{ scope_str }})
{{ transactions | table(
    headers=["ID", "Date", "Payee", "Category", "Amount", "Status"],
    keys=["id", "dt", "payee", "category", "amount", "cs"]
) }}
{% endif %}
