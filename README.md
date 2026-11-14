# Qashbocks

Qashbocks is a fast, standalone personal finance ledger and cash-flow command-line application built on SQLite and Python.

It provides a responsive terminal interface for managing everyday personal accounts, tracking running balances, searching split transactions, and projecting cash flows with recurring bill schedules.

---

## Features

- **Multi-Account Register**: View transaction activity, uncleared entries, running balances, and statement reconciliation status across bank accounts, savings, credit cards, and cash wallets.
- **Statement Reconciliation**: Interactive reconciliation wizard to balance monthly bank and card statements against register transactions.
- **Universal Search**: Fast substring and numeric search across all transactions, memos, categories, and child line-item splits.
- **Recurring Schedules**: Schedule recurring bills, standing orders, and direct debits, with automatic match detection upon entry.
- **Cash-Flow Forecasting**: Multi-horizon cash-flow projections and terminal ANSI graph rendering to forecast upcoming troughs and peaks.
- **Multi-Currency**: Track foreign currency accounts and inter-currency transfers.
- **Demonstration Database**: Out of the box sample database showcasing real-world account structures and recurring schedules.

---

## Installation

### Prerequisites
- Python 3.10 or later
- Jinja2 (`pip install jinja2`)

### Quick Setup

Clone the repository and run the installer:

```bash
git clone https://github.com/applutan/qashbocks.git
cd qashbocks
./install.sh
```

The installer configures `~/.config/qashbocks/config.toml` and links the executable to `~/.local/bin/qashbocks`.

Ensure `~/.local/bin` is in your environment PATH:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

---

## Quickstart Tutorial

Explore the pre-populated demonstration database:

```bash
# 1. Review account balances and liquid net worth
qashbocks accounts

# 2. Inspect upcoming scheduled bills for the next 30 days
qashbocks bills

# 3. View the recent transaction register for the checking account
qashbocks reg checking

# 4. Search transactions and split line items
qashbocks find "groceries"
qashbocks find "water"

# 5. Render a 90-day cash flow projection graph in the terminal
qashbocks forecast -a checking -d 90

# 6. Deep inspection of an entry and attached recurring bill
qashbocks inspect 101
```

---

## Command Reference

| Command | Shorthand | Description |
| :--- | :--- | :--- |
| `qashbocks accounts` | `ls`, `status` | Display active accounts, balances, and reconciliation status |
| `qashbocks bills` | `upcoming` | List upcoming bills and standing orders (`-s` for sleeping) |
| `qashbocks reg <acct>` | `activity` | Transaction register ledger with running balances |
| `qashbocks add` | | Record a new transaction or inter-account transfer |
| `qashbocks find <query>` | `search` | Search transactions across payees, memos, and split items |
| `qashbocks forecast` | `report` | Terminal ANSI cash-flow forecast line chart (`--html` for report) |
| `qashbocks reconcile <acct>` | | Interactive monthly statement reconciliation wizard |
| `qashbocks skip <bill_id>` | | Advance recurring bill schedule without posting a transaction |
| `qashbocks inspect <trn_id>` | `trn` | Detailed breakdown of a transaction, splits, and bill linkages |
| `qashbocks recur <trn_id>` | `repeat` | Convert an existing transaction into a recurring schedule |
| `qashbocks currencies` | `crnc` | Audit account currencies and multi-currency transfers |
| `qashbocks alias` | | View or update short account aliases |
| `qashbocks init <path>` | | Initialise a new ledger database (`--sample` for demo data) |

---

## Configuration

Qashbocks reads configuration from `./config.toml` or `~/.config/qashbocks/config.toml`:

```toml
[defaults]
database = "data/sample.db"
account = "checking"
bill_horizon_days = 30

[aliases]
checking = "Main Checking"
savings  = "High-Yield Savings"
amex     = "Amex Platinum"
visa     = "Barclays Visa"
cash     = "Cash Wallet"
```

---

## Running Automated Tests

Run the test suite against the sample database:

```bash
python3 tests/test_cli.py
```

---

## License

Released under the MIT License. Copyright (c) 2026 Paul Sampson.
