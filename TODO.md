# Roadmap & Feature Wishlist (TODO)

This document tracks planned architecture enhancements, operational workflows, and feature requests for **Qashbocks**.

---

## 1. Foundational Entity Creation & Management

Currently, Qashbocks automatically creates new payees on the fly when recording transactions via `add`, but relies on database initialisation or SQL scripts to create accounts and categories.

### A. Direct CLI Entity Commands
- [ ] **Account Creation (`qashbocks account add`)**:
  - Syntax: `qashbocks account add "<Name>" --alias <als> --type <checking|savings|credit|cash> [--balance <bal>] [--date <YYYY-MM-DD>] [--limit <lim>]`
  - Creates record in `ACCT` with initial statement anchor (`amtEndRec`, `dtEndRec`), credit limit, and account type flags.
- [ ] **Account Lifecycle Management**:
  - `qashbocks account edit <alias> [--name <name>] [--limit <lim>] [--alias <new_alias>]`
  - `qashbocks account close <alias>` (sets `fClosed = 1` while preserving ledger history).
- [ ] **Hierarchical Category Management (`qashbocks category add`)**:
  - Syntax: `qashbocks category add "<Parent:Child>" [--type <expense|income>]`
  - Automatically resolves or creates parent category in `CAT`, linking `hcatParent` appropriately.
- [ ] **Explicit Payee Management (`qashbocks payee add`)**:
  - Syntax: `qashbocks payee add "<Name>" [--alias <als>]`

### B. Declarative Seed Templates (`--seed`)
- [ ] **Built-in Starter Taxonomy Templates**:
  - Provide ready-to-use TOML category configurations (e.g. `templates/seeds/uk_household.toml`, `templates/seeds/minimal.toml`).
  - Common spending groups: Food & Groceries, Housing & Rent/Mortgage, Utilities, Transport & Travel, Leisure & Entertainment, Health, Income, Transfers.
- [ ] **Initialisation Integration**:
  - Extend `qashbocks init <db_path> --seed <template>` to populate standard accounts and category trees immediately on a fresh database.

---

## 2. Statement Ingestion & Automated Inference Pipeline (`qashbocks import`)

Provide an automated statement import pipeline that ingests raw bank files, infers metadata, and simplifies statement reconciliation.

### A. Supported Formats
- [ ] **Structured Ingestion (CSV / QIF / OFX / QFX)**:
  - Zero-dependency parser for standard QIF, OFX, and configurable CSV formats.
  - Per-bank CSV column mapping configuration in `config.toml` (Date, Payee/Narrative, Amount/Debit/Credit, Running Balance).
- [ ] **Bank PDF Statement Scraper**:
  - Extraction module using `pypdf` or `pdfplumber` for visual PDF statements.
  - Pluggable bank-specific layout adaptors for major UK / retail banks (e.g. TSB, Lloyds, Barclays, NatWest, Monzo).

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

