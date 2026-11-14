{% if sync_badge %}
{{ sync_badge }}
{% endif %}
Liquid Accounts:
{{ liquid_accounts | table(
    headers=["Alias", "Account", "Balance", "Uncl", "Last Rec Date"],
    keys=["alias", "name", "balance", "uncl", "rec_dt"],
    style="unicode",
    footer=["Total", "Liquid Net", liquid_total, "", ""]
) }}
{% if inv_accounts %}

Investment Portfolios:
{{ inv_accounts | table(
    headers=["Alias", "Account", "Value", "Holdings"],
    keys=["alias", "name", "balance", "holdings"],
    style="unicode"
) }}
{% endif %}
{% if tracking_accounts %}

Internal Tracking Registers:
{{ tracking_accounts | table(
    headers=["Alias", "Register", "Cumulative", "Role"],
    keys=["alias", "name", "balance", "role"],
    style="unicode",
    footer=["Total", "Total Tracking Flow", tracking_total, ""]
) }}
{% endif %}
