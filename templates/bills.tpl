{% if is_sleeping %}
Sleeping Recurring Bills (> 30 days overdue): {{ bills | length }} found
{% else %}
Upcoming Bills & Recurring Transactions (Next {{ horizon }} Days):
{% endif %}
{{ bills | table(
    headers=["Due Date", "Bill #", "Payee", "Amount", "Account", "Frequency", "Last Paid", "Status"],
    keys=["due_date", "head_id", "payee", "amount", "account", "frequency", "last_paid", "status_label"]
) }}
{% if is_sleeping %}
Note: These bills are active schedules (st=1) whose schedules stopped advancing.
Tip: Match with `qashbocks add --match-bill` or advance with `qashbocks skip`.
{% else %}
Current checking balance: {{ current_bal | gbp }}
Tip: Run `qashbocks skip <bill#>` to skip an occurrence without deleting the schedule.
{% endif %}
