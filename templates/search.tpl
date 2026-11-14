{% if is_plain %}
Search: {{ query_desc }} ({{ count }} matches{{ ', active accounts only' if active_only else '' }})
{% if transactions %}
ID	Date	Account	Payee	Category	Amount	Memo
{% for t in transactions %}
{{ t.id }}	{{ t.dt }}	{{ t.account }}	{{ t.payee }}	{{ t.category }}	{{ t.amount }}	{{ t.memo }}
{% endfor %}
{% else %}
No transactions found matching criteria.
{% endif %}
{% else %}
Search: {{ query_desc }} ({{ count }} matches{{ ', active accounts only' if active_only else '' }})
{% if transactions %}
{{ transactions | table(
    headers=["ID", "Date", "Account", "Payee", "Category", "Amount", "Memo"],
    keys=["id", "dt", "account", "payee", "category", "amount", "memo"]
) }}
{% else %}
No transactions found matching criteria.
{% endif %}
{% endif %}
