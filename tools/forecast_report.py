#!/usr/bin/env python3
"""
tools/forecast_report.py

Generates the "Next Quarter" (or custom horizon) Balance Forecast
graph and cash-flow projection for any account.

Features:
- Multi-occurrence bill recurrence engine (monthly, weekly, bi-weekly, annual)
- In-terminal ANSI truecolor line graph (using block/braille curves)
- Self-contained interactive SVG/HTML dashboard report
- Cash-flow trough/peak detection and milestone breakdown

Usage:
    python3 tools/forecast_report.py [--account checking] [--days 90] [--html reports/next_quarter.html]
"""

import argparse
import calendar
import datetime
import math
import os
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
try:
    from qashbocks import queries
except ImportError:
    import queries

CONFIG_FILE = PROJECT_ROOT / "config.toml"


def get_configured_db() -> Path:
    env_db = os.environ.get("QASHBOCKS_DB") or os.environ.get("MONEY_DB")
    if env_db:
        return Path(env_db).expanduser().resolve()
    candidates = [
        CONFIG_FILE,
        Path.home() / ".config" / "qashbocks" / "config.toml",
    ]
    for cfg_path in candidates:
        if cfg_path.exists():
            try:
                import tomllib
                with open(cfg_path, "rb") as f:
                    data = tomllib.load(f)
                cfg_db = data.get("defaults", {}).get("database")
                if cfg_db:
                    p = Path(cfg_db).expanduser()
                    if not p.is_absolute():
                        p = (PROJECT_ROOT / p).resolve()
                    return p
            except Exception:
                pass
    return PROJECT_ROOT / "data" / "sample.db"


DEFAULT_DB = get_configured_db()


def load_account_aliases() -> dict[str, str]:
    aliases = {
        "checking": "Main Checking",
        "current": "Main Checking",
        "savings": "High-Yield Savings",
        "amex": "Amex Platinum",
        "visa": "Barclays Visa",
        "cash": "Cash Wallet",
        "lucre": "Cash Wallet",
        "euro": "Euro Travel Cash",
    }
    candidates = [
        CONFIG_FILE,
        Path.home() / ".config" / "qashbocks" / "config.toml",
    ]
    for cfg_path in candidates:
        if cfg_path.exists():
            try:
                import tomllib
                data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
                for k, v in data.get("aliases", {}).items():
                    aliases[k.lower()] = v
            except Exception:
                pass
    return aliases


def resolve_account(conn: sqlite3.Connection, identifier: str) -> dict:
    aliases = load_account_aliases()
    ident = identifier.strip().lower()
    clean_id = ident.lstrip("#")
    full_name = aliases.get(ident, ident)

    cur = conn.cursor()
    if clean_id.isdigit():
        cur.execute("SELECT hacct, szFull FROM ACCT WHERE hacct = ?", (int(clean_id),))
    else:
        cur.execute(
            "SELECT hacct, szFull FROM ACCT WHERE LOWER(szFull) = ? OR LOWER(szAls) = ?",
            (full_name.lower(), ident),
        )
    row = cur.fetchone()
    if not row:
        cur.execute("SELECT hacct, szFull FROM ACCT WHERE LOWER(szFull) LIKE ?", (f"%{ident}%",))
        row = cur.fetchone()
    if not row:
        raise ValueError(f"Account '{identifier}' not found.")
    return {"id": row[0], "name": row[1]}


def parse_date(dt_str: str) -> datetime.date | None:
    if not dt_str:
        return None
    clean = str(dt_str).strip().split()[0]
    try:
        if "-" in clean:
            parts = [int(p) for p in clean.split("-")]
            if len(parts) >= 3 and parts[1] > 0 and parts[2] > 0:
                return datetime.date(parts[0], parts[1], parts[2])
    except (ValueError, TypeError):
        return None
    return None


def add_months(sourcedate: datetime.date, months: int) -> datetime.date:
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month // 12
    month = month % 12 + 1
    day = min(sourcedate.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


def calculate_due_date(start_date: datetime.date, frq: int, cfrq: float, instance_index: int) -> datetime.date:
    cfrq_f = float(cfrq) if cfrq and float(cfrq) > 0 else 1.0
    if frq == 0:
        return start_date
    elif frq == 1:
        days_step = int(round(1.0 / cfrq_f)) if cfrq_f < 1.0 else int(round(cfrq_f))
        return start_date + datetime.timedelta(days=instance_index * days_step)
    elif frq == 2:
        days_step = int(round(7.0 / cfrq_f)) if cfrq_f < 1.0 else int(round(cfrq_f * 7.0))
        return start_date + datetime.timedelta(days=instance_index * days_step)
    elif frq == 3:
        months_step = int(round(1.0 / cfrq_f)) if cfrq_f < 1.0 else int(round(cfrq_f))
        return add_months(start_date, instance_index * months_step)
    elif frq == 4:
        months_step = int(round(3.0 / cfrq_f)) if cfrq_f < 1.0 else int(round(3.0 * cfrq_f))
        return add_months(start_date, instance_index * months_step)
    elif frq == 5:
        months_step = int(round(12.0 / cfrq_f))
        return add_months(start_date, instance_index * months_step)
    elif frq == 6:
        months_added = instance_index // 2
        base = add_months(start_date, months_added)
        if instance_index % 2 == 1:
            return base + datetime.timedelta(days=14)
        return base
    return None


def get_anchored_account_balance(conn: sqlite3.Connection, account_id: int, use_reconciled: bool = True) -> tuple[float, str]:
    cur = conn.cursor()
    if use_reconciled:
        cur.execute("SELECT amtEndRec, dtEndRec FROM ACCT WHERE hacct = ?", (account_id,))
        row = cur.fetchone()
        if row and row["amtEndRec"] is not None and str(row["dtEndRec"] or "").startswith("20"):
            base_bal = float(row["amtEndRec"])
            cur.execute("""
                SELECT ROUND(COALESCE(SUM(CAST(amt AS REAL)), 0.0), 2)
                FROM TRN
                WHERE hacct = ?
                  AND cs != 2
                  AND (CAST(grftt AS INTEGER) & 2097152) = 0
                  AND (CAST(grftt AS INTEGER) & 64) = 0
                  AND SUBSTR(dt, 1, 10) <= date('now')
            """, (account_id,))
            subsequent = cur.fetchone()[0] or 0.0
            return base_bal + subsequent, f"Reconciled Anchor (stmt bal: £{base_bal:,.2f}) + unreconciled: £{subsequent:,.2f}"

    cur.execute("""
        SELECT ROUND(COALESCE(SUM(CAST(amt AS REAL)), 0.0), 2)
        FROM TRN
        WHERE hacct = ?
          AND (CAST(grftt AS INTEGER) & 2097152) = 0
          AND (CAST(grftt AS INTEGER) & 64) = 0
          AND SUBSTR(dt, 1, 10) <= date('now')
    """, (account_id,))
    total = float(cur.fetchone()[0] or 0.0)
    return total, "Non-template SUM(TRN) <= today"


def get_projected_cash_flow(
    conn: sqlite3.Connection,
    account_id: int,
    start_date: datetime.date,
    days: int = 90,
    include_income: bool = True,
    use_reconciled: bool = True,
) -> tuple[float, list[dict], list[dict], str]:
    cur = conn.cursor()

    # 1. Get current balance using Reconciled Anchor
    start_bal, bal_desc = get_anchored_account_balance(conn, account_id, use_reconciled=use_reconciled)
    end_date = start_date + datetime.timedelta(days=days)

    # 2. Query all recurring bill revisions linked to this account
    cur.execute(queries.GET_ACCOUNT_BILL_REVISIONS, (account_id,))
    rows = cur.fetchall()

    heads = {}
    for r in rows:
        head_id = r[1]
        if head_id not in heads:
            heads[head_id] = {
                "head_st": str(r[13]),
                "head_next_unpaid": int(r[14]) if str(r[14]).isdigit() else 0,
                "revisions": []
            }
        heads[head_id]["revisions"].append({
            "hbill": r[0],
            "st": str(r[2]),
            "frq": int(r[3]) if str(r[3]).isdigit() else 3,
            "cfrq": float(r[4]) if r[4] else 1.0,
            "dt": parse_date(r[5]),
            "dtMax": parse_date(r[6]),
            "cInstMax": int(r[7]) if str(r[7]).lstrip("-").isdigit() else -1,
            "iinst": int(r[8]) if str(r[8]).isdigit() else 0,
            "amt": float(r[10]) if r[10] else 0.0,
            "payee": r[11] or "Unknown",
            "category": r[12] or "",
        })

    events = []

    for head_id, data in heads.items():
        if data["head_st"] == "2":
            continue
        head_unpaid = data["head_next_unpaid"]
        revs = data["revisions"]
        if not revs:
            continue

        # Check current unpaid due date: ignore abandoned schedules (> 90 days overdue)
        applicable_revs = [r for r in revs if r["iinst"] <= head_unpaid]
        first_rev = applicable_revs[-1] if applicable_revs else revs[0]
        if first_rev["st"] == "2":
            continue
        first_due = calculate_due_date(first_rev["dt"], first_rev["frq"], first_rev["cfrq"], head_unpaid - first_rev["iinst"])
        if not first_due or first_due < start_date - datetime.timedelta(days=90):
            continue

        # Project occurrences across the forecast horizon
        curr_inst = head_unpaid
        prev_due = None
        guard = 0
        while guard < 500:
            guard += 1
            avail = [r for r in revs if r["iinst"] <= curr_inst]
            if not avail:
                avail = [revs[0]]
            active_rev = avail[-1]

            if active_rev["st"] == "2":
                break

            rel_idx = curr_inst - active_rev["iinst"]
            if active_rev["cInstMax"] > 0 and rel_idx >= active_rev["cInstMax"]:
                break

            due_date = calculate_due_date(active_rev["dt"], active_rev["frq"], active_rev["cfrq"], rel_idx)
            if not due_date:
                break
            if prev_due and due_date <= prev_due:
                break
            prev_due = due_date

            if active_rev["dtMax"] and active_rev["dtMax"].year < 2200 and due_date > active_rev["dtMax"]:
                break
            if due_date > end_date:
                break

            amt = active_rev["amt"]
            # Exclude positive occurrences if --no-income passed
            if not include_income and amt > 0:
                pass
            elif due_date >= start_date:
                events.append({
                    "date": due_date,
                    "original_due": due_date,
                    "amount": amt,
                    "payee": active_rev["payee"],
                    "category": active_rev["category"],
                    "bill_id": head_id,
                    "instance": curr_inst,
                    "is_overdue": False,
                })

            if active_rev["frq"] == 0:
                break
            curr_inst += 1

    events.sort(key=lambda x: (x["date"], int(x["bill_id"]) if str(x["bill_id"]).isdigit() else 0))

    # 3. Build daily timeline
    daily_timeline = []
    curr_bal = start_bal
    event_idx = 0

    for day_offset in range(days + 1):
        day_date = start_date + datetime.timedelta(days=day_offset)
        day_inflow = 0.0
        day_outflow = 0.0
        day_events = []

        while event_idx < len(events) and events[event_idx]["date"] == day_date:
            ev = events[event_idx]
            amt = ev["amount"]
            if amt > 0:
                day_inflow += amt
            else:
                day_outflow += abs(amt)
            curr_bal += amt
            day_events.append(ev)
            event_idx += 1

        daily_timeline.append({
            "date": day_date,
            "day_index": day_offset,
            "balance": curr_bal,
            "inflow": day_inflow,
            "outflow": day_outflow,
            "events": day_events,
        })

    return start_bal, events, daily_timeline, bal_desc


def render_terminal_graph(
    account_name: str,
    start_bal: float,
    timeline: list[dict],
    width: int = 76,
    height: int = 15,
):
    bals = [d["balance"] for d in timeline]
    min_b = min(bals)
    max_b = max(bals)
    end_bal = bals[-1]
    net_change = end_bal - start_bal

    min_day = min(timeline, key=lambda x: x["balance"])
    max_day = max(timeline, key=lambda x: x["balance"])

    span = max_b - min_b
    if span < 1.0:
        span = 100.0
    y_min = min_b - span * 0.05
    y_max = max_b + span * 0.05
    y_range = y_max - y_min

    num_days = len(timeline)
    slots = []
    for col in range(width):
        idx = int((col / (width - 1)) * (num_days - 1))
        slots.append(timeline[idx])

    canvas = [[" " for _ in range(width)] for _ in range(height)]

    for col in range(width):
        val = slots[col]["balance"]
        row = int(((val - y_min) / y_range) * (height - 1))
        row = max(0, min(height - 1, row))
        inverted_row = (height - 1) - row
        canvas[inverted_row][col] = "■"

        for r in range(inverted_row + 1, height):
            canvas[r][col] = "·"

    start_d = timeline[0]["date"]
    end_d = timeline[-1]["date"]
    days_total = (end_d - start_d).days
    days_count = max(1, days_total)
    daily_rate = net_change / days_count

    change_sym = "+" if net_change >= 0 else "-"
    change_color = "\033[32m" if net_change >= 0 else "\033[31m"
    daily_sym = "+" if daily_rate >= 0 else "-"
    daily_color = "\033[32m" if daily_rate >= 0 else "\033[31m"
    daily_desc = "surplus / accumulation" if daily_rate >= 0 else "burn / deficit"
    reset = "\033[0m"
    bold = "\033[1m"
    dim = "\033[2m"

    print(f"\n{bold}=== Balance Forecast: {account_name} ({start_d} to {end_d}, {days_total} days) ==={reset}")
    print(f"  Starting Balance: £{start_bal:,.2f}")
    print(f"  Ending Balance:   £{end_bal:,.2f} ({change_color}{change_sym}£{abs(net_change):,.2f}{reset} total)")
    print(f"  Daily Cash Flow:  {daily_color}{daily_sym}£{abs(daily_rate):.2f} / day{reset} ({daily_desc})")
    print(f"  Trough (Lowest):  £{min_b:,.2f} on {min_day['date']}")
    print(f"  Peak (Highest):   £{max_b:,.2f} on {max_day['date']}\n")

    for r in range(height):
        frac = ((height - 1) - r) / (height - 1)
        axis_val = y_min + frac * y_range
        axis_label = f"£{axis_val:>10,.0f} |"

        row_chars = []
        for c in range(width):
            ch = canvas[r][c]
            if ch == "■":
                row_chars.append(f"\033[38;2;40;180;255m{ch}{reset}")
            elif ch == "·":
                row_chars.append(f"\033[38;2;60;80;100m{ch}{reset}")
            else:
                row_chars.append(" ")
        print(f"{dim}{axis_label}{reset}" + "".join(row_chars))

    print(f"{' ':>12}+" + "-" * width)

    marker_indices = [0, width // 4, width // 2, (3 * width) // 4, width - 1]
    date_line = [" " for _ in range(width + 15)]
    for mi in marker_indices:
        d_val = slots[mi]["date"].strftime("%b %d")
        pos = 13 + mi
        for i, ch in enumerate(d_val):
            if pos + i < len(date_line):
                date_line[pos + i] = ch
    print("".join(date_line))


def export_svg_dashboard(
    output_path: Path,
    account_name: str,
    start_bal: float,
    events: list[dict],
    timeline: list[dict],
):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    bals = [d["balance"] for d in timeline]
    min_b = min(bals)
    max_b = max(bals)
    end_bal = bals[-1]
    net_change = end_bal - start_bal

    start_d = timeline[0]["date"]
    end_d = timeline[-1]["date"]

    svg_w = 960
    svg_h = 360
    pad_left = 90
    pad_right = 40
    pad_top = 40
    pad_bottom = 60

    plot_w = svg_w - pad_left - pad_right
    plot_h = svg_h - pad_top - pad_bottom

    span = max_b - min_b if (max_b - min_b) > 1.0 else 100.0
    y_min = min_b - span * 0.08
    y_max = max_b + span * 0.08
    y_range = y_max - y_min

    points = []
    num_pts = len(timeline)
    for i, d in enumerate(timeline):
        x = pad_left + (i / (num_pts - 1)) * plot_w
        y = pad_top + (1.0 - (d["balance"] - y_min) / y_range) * plot_h
        points.append(f"{x:.1f},{y:.1f}")

    poly_str = " ".join(points)
    fill_poly = f"{pad_left},{pad_top + plot_h} " + poly_str + f" {pad_left + plot_w},{pad_top + plot_h}"

    grid_lines = []
    y_steps = 5
    for s in range(y_steps + 1):
        frac = s / y_steps
        y_val = y_min + frac * y_range
        py = pad_top + (1.0 - frac) * plot_h
        grid_lines.append(
            f'<line x1="{pad_left}" y1="{py:.1f}" x2="{pad_left + plot_w}" y2="{py:.1f}" stroke="#2a324b" stroke-dasharray="4,4"/>'
            f'<text x="{pad_left - 10}" y="{py + 4:.1f}" text-anchor="end" fill="#8892b0" font-size="12" font-family="monospace">£{y_val:,.0f}</text>'
        )

    x_dates = []
    x_steps = 6
    for s in range(x_steps + 1):
        frac = s / x_steps
        idx = int(frac * (num_pts - 1))
        px = pad_left + frac * plot_w
        d_str = timeline[idx]["date"].strftime("%d %b")
        x_dates.append(
            f'<line x1="{px:.1f}" y1="{pad_top}" x2="{px:.1f}" y2="{pad_top + plot_h}" stroke="#1e2638" stroke-dasharray="2,2"/>'
            f'<text x="{px:.1f}" y="{pad_top + plot_h + 24}" text-anchor="middle" fill="#8892b0" font-size="12" font-family="monospace">{d_str}</text>'
        )

    event_rows = []
    for ev in events[:25]:
        amt_color = "#4ade80" if ev["amount"] > 0 else "#f87171"
        overdue_badge = ' <span style="color:#fbbf24;font-size:0.8em;">(OVERDUE)</span>' if ev.get("is_overdue") else ""
        event_rows.append(
            f"<tr><td>{ev['date']}</td><td>#{ev['bill_id']}</td><td>{ev['payee']}{overdue_badge}</td>"
            f"<td>{ev['category']}</td><td style='text-align:right;color:{amt_color};font-weight:bold;'>£{ev['amount']:,.2f}</td></tr>"
        )

    days_total = max(1, (end_d - start_d).days)
    daily_rate = net_change / days_total

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Balance Forecast: {account_name} (Next Quarter)</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #0b0f19; color: #e2e8f0; margin: 0; padding: 24px; }}
  .container {{ max-width: 1040px; margin: 0 auto; }}
  .header {{ display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 20px; border-bottom: 1px solid #1e293b; padding-bottom: 16px; }}
  .title {{ font-size: 24px; font-weight: bold; color: #f8fafc; }}
  .subtitle {{ color: #94a3b8; font-size: 14px; margin-top: 4px; }}
  .metrics {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 16px; margin-bottom: 24px; }}
  .card {{ background: #131b2e; border: 1px solid #1e293b; border-radius: 8px; padding: 16px; }}
  .card-label {{ font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; }}
  .card-value {{ font-size: 20px; font-weight: bold; margin-top: 6px; font-family: monospace; }}
  .chart-box {{ background: #131b2e; border: 1px solid #1e293b; border-radius: 8px; padding: 16px; margin-bottom: 28px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 14px; }}
  th, td {{ padding: 10px 14px; text-align: left; border-bottom: 1px solid #1e293b; }}
  th {{ background: #182238; color: #94a3b8; font-size: 12px; text-transform: uppercase; }}
  tr:hover {{ background: #1a253d; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div>
      <div class="title">Next Quarter Balance Forecast: {account_name}</div>
      <div class="subtitle">Forecast Horizon: {start_d} through {end_d} ({days_total} days)</div>
    </div>
    <div style="text-align: right; font-size: 13px; color: #64748b;">Generated from Qashbocks</div>
  </div>

  <div class="metrics">
    <div class="card">
      <div class="card-label">Starting Balance</div>
      <div class="card-value">£{start_bal:,.2f}</div>
    </div>
    <div class="card">
      <div class="card-label">Ending Balance</div>
      <div class="card-value" style="color: {'#4ade80' if net_change >= 0 else '#f87171'};">£{end_bal:,.2f}</div>
    </div>
    <div class="card">
      <div class="card-label">Daily Cash Flow</div>
      <div class="card-value" style="color: {'#4ade80' if daily_rate >= 0 else '#f87171'};">{'+' if daily_rate >= 0 else ''}£{daily_rate:.2f}/d</div>
    </div>
    <div class="card">
      <div class="card-label">Quarterly Trough</div>
      <div class="card-value">£{min_b:,.2f}</div>
    </div>
    <div class="card">
      <div class="card-label">Quarterly Peak</div>
      <div class="card-value">£{max_b:,.2f}</div>
    </div>
  </div>

  <div class="chart-box">
    <svg viewBox="0 0 {svg_w} {svg_h}" width="100%" height="auto" style="display: block;">
      <defs>
        <linearGradient id="curveGradient" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#38bdf8" stop-opacity="0.35"/>
          <stop offset="100%" stop-color="#38bdf8" stop-opacity="0.0"/>
        </linearGradient>
      </defs>
      {"".join(grid_lines)}
      {"".join(x_dates)}
      <polygon points="{fill_poly}" fill="url(#curveGradient)"/>
      <polyline points="{poly_str}" fill="none" stroke="#38bdf8" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
  </div>

  <div class="card" style="margin-bottom: 24px;">
    <div style="font-weight: bold; font-size: 16px; margin-bottom: 12px;">Scheduled Bills & Milestones in this Quarter ({len(events)} events)</div>
    <table>
      <thead>
        <tr><th>Due Date</th><th>Bill #</th><th>Payee</th><th>Category</th><th style="text-align:right;">Amount</th></tr>
      </thead>
      <tbody>
        {"".join(event_rows)}
      </tbody>
    </table>
  </div>
</div>
</body>
</html>
"""
    output_path.write_text(html_content, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Balance Forecast graph and projection ('Next Quarter').")
    parser.add_argument("-a", "--account", type=str, default="checking", help="Account alias or name (default: checking)")
    parser.add_argument("-d", "--days", type=int, default=90, help="Forecast horizon in days (default: 90 / Next Quarter)")
    parser.add_argument("--html", type=Path, default=None, help="Export interactive HTML/SVG report to file")
    parser.add_argument("--no-income", dest="income", action="store_false", default=True, help="Exclude regular income/salary from projection")
    parser.add_argument("--naive-sum", dest="reconciled", action="store_false", default=True, help="Use naive cumulative SUM(TRN) instead of Reconciled Statement Anchor")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"Path to database (default: {DEFAULT_DB})")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"Error: Database not found at {args.db}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    acct = resolve_account(conn, args.account)
    today = datetime.date.today()

    start_bal, events, timeline, bal_desc = get_projected_cash_flow(
        conn,
        acct["id"],
        today,
        days=args.days,
        include_income=args.income,
        use_reconciled=args.reconciled,
    )

    # 1. Terminal plot
    print(f"\n[Balance Basis: {bal_desc}]")
    if args.income:
        print("[Included Inflow: Scheduled recurring salary / deposits]")
    else:
        print("[Income: Excluded (burn-down mode)]")

    render_terminal_graph(acct["name"], start_bal, timeline, width=72, height=14)

    # 2. Upcoming events summary
    print(f"\nTop Upcoming Milestones ({len(events)} total scheduled in next {args.days} days):")
    print(f"{'Due Date':<14} | {'Bill #':>6} | {'Payee':<22} | {'Amount':>11} | {'Category'}")
    print("-" * 74)
    for ev in events[:15]:
        amt_str = f"£{ev['amount']:,.2f}"
        d_str = str(ev["date"])
        print(f"{d_str:<14} | #{ev['bill_id']:>5} | {ev['payee'][:22]:<22} | {amt_str:>11} | {ev['category'][:14]}")
    if len(events) > 15:
        print(f"  ... and {len(events) - 15} more upcoming scheduled occurrences.")
    print("-" * 74 + "\n")

    if args.html:
        export_svg_dashboard(args.html, acct["name"], start_bal, events, timeline)
        print(f"HTML dashboard exported to {args.html}")

    conn.close()


if __name__ == "__main__":
    main()
