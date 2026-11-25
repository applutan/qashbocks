# Roadmap & Feature Wishlist (TODO)

This document tracks planned architecture enhancements, operational workflows, and feature requests for **Qashbocks**.

---

## 1. Foundational Entity Creation & Management

Currently, Qashbocks automatically creates new payees on the fly when recording transactions via `add`, but relies on database initialisation or SQL scripts to create accounts and categories.

### A. Direct CLI Entity Commands
- [ ] **Account Creation (`qashbocks account add`)**:
  - Syntax: `qashbocks account add "<Name>" --alias <als> --type <checking|savings|credit|cash|loan|investment|pension> [--balance <bal>] [--date <YYYY-MM-DD>] [--limit <lim>]`
  - Creates record in `ACCT` with initial statement anchor (`amtEndRec`, `dtEndRec`), credit limit, and account type flags.
  - Standardised account types:
    - `checking` / `current`: Everyday operational cash; positive balance.
    - `savings`: Reserve deposits, emergency funds, ISAs; positive balance.
    - `cash`: Physical wallet cash; positive balance.
    - `credit`: Revolving credit cards (Amex, Visa); stored as negative balance.
    - `loan`: Mortgages and fixed loans; stored as negative amortised liability.
    - `investment`: Wealth brokerage and portfolios; tracks securities and cash.
    - `pension`: Locked retirement funds (SIPP, workplace pensions).
- [ ] **Account Lifecycle Management**:
  - `qashbocks account edit <alias> [--name <name>] [--limit <lim>] [--alias <new_alias>]`
  - `qashbocks account close <alias>` (sets `fClosed = 1` while preserving ledger history).
- [ ] **Hierarchical Category Management (`qashbocks category add`)**:
  - Syntax: `qashbocks category add "<Parent:Child>" [--type <expense|income>]`
  - Automatically resolves or creates parent category in `CAT`, linking `hcatParent` appropriately.
- [ ] **Explicit Payee Management & 4-Layer Anti-Duplicate Defence**:
  - *Problem*: Blind automatic registration of every unique payee string causes "duplicate payee hell" (`Waitrose` vs `Waitrose Ltd` vs `Waitrose & Partners` vs `WAITROSE 421 LONDON`).
  - **Layer 1: Audit Separation**: Store raw transaction/bank text in `TRN.mMemo` while anchoring `TRN.lHpay` to clean canonical merchant entities.
  - **Layer 2: Payee Aliasing & Match Patterns (`config.toml` / `payees.toml`)**:
    - Declarative alias mappings for known merchants:
      ```toml
      [payee."Waitrose"]
      aliases = ["Waitrose Ltd", "Waitrose & Partners", "WAITROSE*"]
      default_category = "Food:Groceries"
      ```
  - **Layer 3: Typo Detection via `difflib`**:
    - On manual entry or batch ingestion of unknown payees, check standard library `difflib.get_close_matches(payee, existing_payees, cutoff=0.8)`. Prompt to link to existing canonical payee or create new alias rather than duplicating.
  - **Layer 4: Payee Merging Tool (`qashbocks payee merge`)**:
    - Syntax: `qashbocks payee merge "<Source Duplicate>" "<Target Canonical>"`
    - Atomic SQL transaction updating `TRN.lHpay` and `BILL.lHpay`, then removing the duplicate from `PAY`.

### B. Declarative Seed Templates (`--seed`)
- [ ] **Built-in Starter Taxonomy Templates**:
  - Provide ready-to-use TOML category configurations (e.g. `templates/seeds/uk_household.toml`, `templates/seeds/minimal.toml`).
  - Common spending groups: Food & Groceries, Housing & Rent/Mortgage, Utilities, Transport & Travel, Leisure & Entertainment, Health, Income, Transfers.
- [ ] **Initialisation Integration**:
  - Extend `qashbocks init <db_path> --seed <template>` to populate standard accounts and category trees immediately on a fresh database.

---

## 2. Statement Ingestion & Automated Inference Pipeline (`qashbocks import`)

Provide an automated statement import pipeline that ingests raw bank files, infers metadata, and simplifies statement reconciliation.

> [!TIP]
> **Unix Pipeline Principle (Wheel Reinvention Avoidance)**:
> Qashbocks will NOT embed a proprietary, heavyweight PDF layout or OCR engine in core. PDFs are visual layout descriptions, not data interchange formats.
> Instead, Qashbocks relies on existing Unix tooling (`pdftotext -layout` via `poppler-utils`) and lightweight bank-specific regex filters to pre-process PDFs into clean text streams piped directly into `qashbocks add -f -`.
> Qashbocks core remains strictly responsible for the ledger: deduplication, payee canonicalisation, category inference, bill matching, and statement balance anchoring.

### A. Supported Formats & Pipeline Architecture
- [ ] **Structured Plain-Text Ingestion (CSV / QIF / OFX / QFX)**:
  - Zero-dependency parser for standard QIF, OFX, and configurable CSV bank exports.
  - Per-bank CSV column mapping configuration in `config.toml` (Date, Payee/Narrative, Amount/Debit/Credit, Running Balance).
- [ ] **External PDF Pre-Filter Pipeline (`pdftotext -layout` Composition)**:
  - Modular shell / Python pre-filters for major retail banks (e.g. `filters/lloyds_pdf.py`, `filters/amex_pdf.py`).
  - Pipeline invocation:
    ```bash
    pdftotext -layout statement.pdf - | bank_filter.py | qashbocks add -f - -n
    ```

### B. Automated Metadata Inference & Cleaning
- [ ] **Payee Normalisation**:
  - Regex cleaning engine to strip terminal card numbers, reference IDs, location suffixes, and transaction codes:
    ```text
    "CARD 1234 03NOV26 SAINSBURYS S/MKTS NEWCASTLE GBR"  ->  "Sainsbury's"
    "DIRECT DEBIT PAYMENT TO OCTOPUS ENERGY REF: 98765"   ->  "Octopus Energy"
    ```
  - Map cleaned names against existing `PAY` records or auto-create new canonical payees.
- [ ] **Category Inference & Allocation Rules**:
  - **Historical Frequency Matching**: Predict category from prior transactions with the same payee.
  - **Rule-based Pattern Matcher (`rules.toml`)**:
    - Declarative regex rules mapping narrative patterns to categories:
      ```toml
      [[rules]]
      pattern = "(?i)(tfl|translink|lner|national rail)"
      category = "Travel:Transit"

      [[rules]]
      pattern = "(?i)(tesco|sainsbury|waitrose|morrisons|asda|aldi|lidl)"
      category = "Food:Groceries"
      ```
  - Fall back to `Uncategorised` for unmapped transactions with a post-import review workflow.
- [ ] **Duplicate Detection**:
  - Prevent double-posting by matching incoming entries against existing `TRN` rows on `(dt, amt, hacct)`.
- [ ] **Automated Statement Anchoring**:
  - Extract closing statement balance and statement date from the import file.
  - Update `amtEndRec` and `dtEndRec` upon verification, reconciling the account in a single step.

---

## 3. Batch Line-File & Staging Improvements

- [ ] **Enhanced Batch Line Ingestion**:
  - Refine `qashbocks add -f <file>` with interactive dry-run preview and conflict resolution.
- [ ] **Uncategorised Transaction Review Workflow**:
  - `qashbocks review [--account <als>]` interactive CLI helper to step through uncategorised or newly inferred transactions and assign/confirm categories in bulk.

---

## 4. Multi-Device Architecture: Global Master Database & Remote Access

Because the SQLite database is small and portable, having multiple copies across desktop and laptop risks split-brain divergence and merge conflicts. The goal is an authoritative single source of truth accessible globally without compromising local speed or data integrity.

### A. Phase 1: SSH Remote Execution & Batch Staging (Zero-Code Architecture)
- [ ] **Centralised Host Deployment**:
  - Host master database on a primary homelab node/server (e.g. reachable globally via Tailscale/WireGuard).
  - Laptop invokes commands seamlessly over SSH TTY:
    ```bash
    alias qashbocks="ssh -t home-server qashbocks"
    ```
  - Eliminates network filesystem (NFS/SSHFS) SQLite locking issues completely by running queries against local disk on the host.
- [ ] **Offline Staging Workflow**:
  - When disconnected from internet/VPN, draft line-item entries in a local text file (`today.txt` / `receipts.txt`).
  - Batch-ingest to master upon reconnect via `qashbocks add -f receipts.txt`.

### B. Phase 2: Decoupled Client/Server Daemon (`qashbocks serve`)
- [ ] **Pluggable Data Access Layer**:
  - Abstract SQL query dispatch in `queries.py` to support two operational modes in `config.toml`:
    - `mode = "local"`: Direct `sqlite3.Connection` to local file.
    - `mode = "remote"`: Lightweight REST/RPC client communicating with `qashbocks serve` over HTTPS/Tailscale with bearer token authentication.
  - Keeps all SQLite ACID writes on the server while presenting a transparent CLI experience on client devices.

### C. Phase 3: Distributed Embedded Replicas (libSQL / Turso)
- [ ] **Evaluation of Embedded Replicas**:
  - Evaluate replacing `sqlite3` driver with `libsql` to support local read-replicas on portable devices:
    - Instant offline reads for `accounts`, `reg`, `search`, and `forecast`.
    - Transparent write delegation to the remote primary master when connected.

---

## 5. Universal Plain-Text Export & Legacy Roundtrip Verification Suite (`qashbocks export`)

To guarantee long-term data portability and provide an airtight test harness for the MS Money retirement, Qashbocks will support full bidirectional plain-text roundtripping.

### A. Comprehensive Text Exporter (`qashbocks export`)
- [ ] **Command**: `qashbocks export [--dir <path>] [--format <csv|json|md>]`
  - Dumps the entire database into clean, human-editable, version-controllable text files:
    1. `accounts.csv`: Account names, aliases, types, starting balances, and statement anchor dates.
    2. `categories.txt`: Hierarchical category tree.
    3. `bills.csv`: Recurring bill schedules, amounts, frequencies, and next due dates.
    4. `history.csv`: Full canonical 7-column transaction ledger (`Date, Account, Payee, Amount, Category, Memo, Recur`).

### B. Legacy vs Qashbocks Agreement Verification Harness
- [ ] **Automated Agreement Audit Script (`tools/verify_legacy_roundtrip.py`)**:
  - **The Goal**: Export the current access-aware legacy database (`money.db` with 51,000+ historical records across 50 years) into canonical plain text, import the text into a completely fresh Qashbocks database (`clean.db`), and mathematically verify that both databases 100% agree:
    - **Balance Verification**: Every active account balance and total liquid net worth matches to the exact penny.
    - **Reconciliation Statuses**: Uncleared (`cs=0`), cleared (`cs=1`), and reconciled (`cs=2`) counts match identically.
    - **Register Integrity**: Running balances match on every historical date.
    - **Cash-Flow Forecast**: Multi-horizon projections (`bills`, `forecast`) produce identical future trough and peak curves.
  - Serves as the ultimate proof of correctness and peace of mind before permanently decommissioning Microsoft Money.

