"""
presenter.py

Jinja2-based presentation and template rendering engine for moneymaker.
Decouples terminal, Markdown, and report layouts from core database querying logic.
Includes automatic table width calculation, multi-currency handling, and custom Jinja filters.
"""

import html
from decimal import Decimal
from pathlib import Path
from typing import Any
import jinja2

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent
USER_TEMPLATES = Path.home() / ".config" / "qashbocks" / "templates"

TEMPLATE_SEARCH_PATHS = [
    USER_TEMPLATES,
    REPO_ROOT / "templates",
    PACKAGE_ROOT / "templates",
]
TEMPLATES_DIR = REPO_ROOT / "templates"

# ---------------------------------------------------------------------
# Custom Template Filters & Helpers
# ---------------------------------------------------------------------

def filter_amount(value: Any, currency: str | None = None, show_pence: bool = True) -> str:
    """
    Format a financial amount.
    If currency is provided (e.g. 'EUR' or '€'), appends or prefixes it.
    If currency is None or 'GBP' (and not multi-currency), formats plain numbers.
    """
    if value is None or value == "":
        return "-"
    try:
        val = float(value)
    except (ValueError, TypeError):
        return str(value)

    fmt = f"{val:,.2f}" if show_pence else f"{val:,.0f}"
    if currency and currency not in ("GBP", "£"):
        # Explicit non-base currency
        sym = "€" if currency in ("EUR", "€") else f"{currency} "
        if val < 0:
            return f"{sym}-{abs(val):,.2f}"
        return f"{sym}{val:,.2f}"
    return fmt


def filter_gbp(value: Any, sign_first: bool = False, show_symbol: bool = True) -> str:
    """
    Format a currency value in GBP (£).
    If show_symbol is False, returns plain formatted float ('-133.00', '1,072.47').
    """
    if value is None or value == "":
        return "0.00" if not show_symbol else "£0.00"
    try:
        val = float(value)
    except (ValueError, TypeError):
        return str(value)

    if not show_symbol:
        return f"{val:,.2f}"

    if val < 0:
        if sign_first:
            return f"-£{abs(val):,.2f}"
        return f"£-{abs(val):,.2f}"
    return f"£{val:,.2f}"


def filter_pad(value: Any, width: int) -> str:
    """Right-align value in a field of given width."""
    return str(value if value is not None else "").rjust(width)


def filter_lpad(value: Any, width: int) -> str:
    """Left-align value in a field of given width."""
    return str(value if value is not None else "").ljust(width)


def filter_date(value: Any) -> str:
    """Extract YYYY-MM-DD from date object or ISO timestamp string."""
    if not value:
        return ""
    val_str = str(value).strip()
    return val_str.split()[0]


def filter_cs(value: Any, short: bool = False) -> str:
    """Reconciliation cleared status enum translation."""
    try:
        val_int = int(value)
    except (ValueError, TypeError):
        return str(value)

    if short:
        return {0: ".", 1: "C", 2: "R"}.get(val_int, "?")
    return {0: "Uncleared", 1: "Cleared", 2: "Reconciled"}.get(val_int, str(value))


def filter_freq(value: Any) -> str:
    """MS Money frequency enum code to human-readable string."""
    try:
        val_int = int(value)
    except (ValueError, TypeError):
        return str(value)
    freq_map = {
        -1: "None",
        0: "Once",
        1: "Daily",
        2: "Weekly",
        3: "Monthly",
        4: "Yearly",
        5: "Bi-Weekly",
        6: "Semi-Monthly",
    }
    return freq_map.get(val_int, f"Code {val_int}")


# ---------------------------------------------------------------------
# Dynamic Auto-Table Formatter
# ---------------------------------------------------------------------

@jinja2.pass_context
def filter_table(
    context: Any,
    rows: list[dict[str, Any]] | list[list[Any]],
    headers: list[str],
    keys: list[str] | None = None,
    alignments: list[str] | None = None,
    style: str | None = None,
    max_widths: dict[str, int] | None = None,
    show_currency: bool = False,
    footer: list[Any] | None = None,
) -> str:
    """
    Auto-computes column widths from data and formats clean monospace or HTML tables.
    Styles supported:
      - 'plain':    'Col 1 | Col 2', '-----+-----'
      - 'unicode':  '┌─────┬─────┐', '│ ... │ ... │', '├─────┼─────┤', '└─────┴─────┘'
      - 'markdown': '| Col 1 | Col 2 |', '| :--- | ---: |' (alias: 'md')
      - 'html':     '<table class="money-table">...'
    """
    if not rows and not footer:
        return "  (No records)"

    # Determine active style (context cli_style overrides template style)
    cli_style = getattr(context, "get", lambda k, d=None: None)("cli_style") if context else None
    active_style = (cli_style or style or "plain").lower()
    if active_style in ("md", "pipe"):
        active_style = "markdown"

    num_cols = len(headers)
    if keys is None:
        if rows and isinstance(rows[0], dict):
            keys = [h.lower().replace(" ", "_").replace("#", "id") for h in headers]
        else:
            keys = [str(i) for i in range(num_cols)]

    max_w_map = max_widths or {}

    # Extract strings and detect numeric columns
    str_matrix = []
    is_numeric = [True] * num_cols

    for r in rows:
        row_strs = []
        for j in range(num_cols):
            key = keys[j]
            val = r.get(key) if isinstance(r, dict) else (r[j] if j < len(r) else "")
            
            # Formatting specials
            if key in ("amt", "amount", "balance", "value", "bill_amt", "cumulative", "total", "gross", "tax_paid", "net", "slice", "tax_due") or "amt" in key:
                cell_str = filter_gbp(val, show_symbol=show_currency)
            elif key in ("dt", "due_date", "tx_date", "date"):
                cell_str = filter_date(val)
            elif key == "cs":
                cell_str = filter_cs(val, short=True)
            elif key in ("head_id", "id", "bill_id", "account_id"):
                cell_str = f"#{val}" if val not in (None, "") else ""
            else:
                cell_str = str(val) if val is not None else ""

            # Check if column values are numeric for right-alignment
            if cell_str not in ("", "-", "£0.00", "0.00"):
                clean_num = cell_str.lstrip("£€$#-").replace(",", "")
                if not (clean_num.replace(".", "", 1).isdigit()):
                    is_numeric[j] = False

            # Max width constraint
            if key in max_w_map and len(cell_str) > max_w_map[key]:
                cell_str = cell_str[:max_w_map[key] - 1] + "…"

            row_strs.append(cell_str)
        str_matrix.append(row_strs)

    # Process optional footer row
    footer_strs = None
    if footer:
        footer_strs = []
        for j in range(num_cols):
            f_val = footer[j] if j < len(footer) else ""
            if isinstance(f_val, (int, float, Decimal)):
                f_str = filter_gbp(f_val, show_symbol=show_currency)
            else:
                f_str = str(f_val) if f_val is not None else ""
            footer_strs.append(f_str)

    # Determine default alignments if not provided
    if alignments is None:
        alignments = [">" if is_numeric[j] else "<" for j in range(num_cols)]

    footer_alignments = alignments
    if footer_strs:
        footer_alignments = []
        for j, c in enumerate(footer_strs):
            if alignments and j < len(alignments):
                footer_alignments.append(alignments[j])
            else:
                footer_alignments.append(">" if is_numeric[j] else "<")

    # Compute column widths (max of header, cell contents, and footer)
    col_widths = []
    for j in range(num_cols):
        h_len = len(headers[j])
        cell_max = max(len(str_matrix[i][j]) for i in range(len(str_matrix))) if str_matrix else 0
        f_len = len(footer_strs[j]) if footer_strs else 0
        w = max(h_len, cell_max, f_len)
        col_widths.append(w)

    def format_row(cells: list[str], align_list: list[str]) -> list[str]:
        padded = []
        for j, c in enumerate(cells):
            w = col_widths[j]
            if align_list[j] == ">":
                padded.append(c.rjust(w))
            else:
                padded.append(c.ljust(w))
        return padded

    lines = []

    if active_style == "unicode":
        top = "┌─" + "─┬─".join("─" * w for w in col_widths) + "─┐"
        mid = "├─" + "─┼─".join("─" * w for w in col_widths) + "─┤"
        bot = "└─" + "─┴─".join("─" * w for w in col_widths) + "─┘"
        header_row = "│ " + " │ ".join(format_row(headers, ["<"] * num_cols)) + " │"
        lines.append(top)
        lines.append(header_row)
        lines.append(mid)
        for r_cells in str_matrix:
            lines.append("│ " + " │ ".join(format_row(r_cells, alignments)) + " │")
        if footer_strs:
            lines.append(mid)
            lines.append("│ " + " │ ".join(format_row(footer_strs, footer_alignments)) + " │")
        lines.append(bot)

    elif active_style == "markdown":
        header_row = "| " + " | ".join(format_row(headers, ["<"] * num_cols)) + " |"
        sep_row = "| " + " | ".join(("---:" if a == ">" else ":---") for a in alignments) + " |"
        lines.append(header_row)
        lines.append(sep_row)
        for r_cells in str_matrix:
            lines.append("| " + " | ".join(format_row(r_cells, alignments)) + " |")
        if footer_strs:
            lines.append(sep_row)
            lines.append("| " + " | ".join(format_row(footer_strs, footer_alignments)) + " |")

    elif active_style == "html":
        lines.append('<table class="money-table">')
        lines.append('  <thead>')
        lines.append('    <tr>')
        for j, h in enumerate(headers):
            align = "right" if alignments[j] == ">" else "left"
            lines.append(f'      <th style="text-align: {align};">{html.escape(h)}</th>')
        lines.append('    </tr>')
        lines.append('  </thead>')
        lines.append('  <tbody>')
        for r_cells in str_matrix:
            lines.append('    <tr>')
            for j, c in enumerate(r_cells):
                align = "right" if alignments[j] == ">" else "left"
                lines.append(f'      <td style="text-align: {align};">{html.escape(c)}</td>')
            lines.append('    </tr>')
        lines.append('  </tbody>')
        if footer_strs:
            lines.append('  <tfoot>')
            lines.append('    <tr>')
            for j, c in enumerate(footer_strs):
                align = "right" if footer_alignments[j] == ">" else "left"
                lines.append(f'      <td style="text-align: {align}; font-weight: bold;">{html.escape(c)}</td>')
            lines.append('    </tr>')
            lines.append('  </tfoot>')
        lines.append('</table>')

    else:  # plain
        header_row = " | ".join(format_row(headers, ["<"] * num_cols))
        sep_row = "-+-".join("-" * w for w in col_widths)
        lines.append(header_row)
        lines.append(sep_row)
        for r_cells in str_matrix:
            lines.append(" | ".join(format_row(r_cells, alignments)))
        if footer_strs:
            lines.append(sep_row)
            lines.append(" | ".join(format_row(footer_strs, footer_alignments)))

    return "\n".join(lines)


# ---------------------------------------------------------------------
# Template Environment Setup
# ---------------------------------------------------------------------

def get_template_env(template_dir: Path | None = None) -> jinja2.Environment:
    """Initialise and configure Jinja2 Environment."""
    if template_dir:
        search_dirs = [str(template_dir)]
    else:
        search_dirs = [str(p) for p in TEMPLATE_SEARCH_PATHS if p.exists()]
        if not search_dirs:
            search_dirs = [str(TEMPLATES_DIR)]

    loader = jinja2.FileSystemLoader(search_dirs)
    env = jinja2.Environment(
        loader=loader,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=False,
    )

    # Register custom filters
    env.filters["gbp"] = filter_gbp
    env.filters["amount"] = filter_amount
    env.filters["pad"] = filter_pad
    env.filters["lpad"] = filter_lpad
    env.filters["date"] = filter_date
    env.filters["cs"] = filter_cs
    env.filters["freq"] = filter_freq
    env.filters["table"] = filter_table

    return env


_DEFAULT_ENV = None


def render_template(template_name: str, context: dict[str, Any], template_dir: Path | None = None) -> str:
    """
    Renders an external template file (.tpl or .txt) with the given context dictionary.
    """
    global _DEFAULT_ENV
    if template_dir is not None:
        env = get_template_env(template_dir)
    else:
        if _DEFAULT_ENV is None:
            _DEFAULT_ENV = get_template_env()
        env = _DEFAULT_ENV

    # Support .tpl extension preference
    base_stem = Path(template_name).stem
    candidates = [
        f"{base_stem}.tpl",
        template_name,
        f"{base_stem}.txt",
    ]

    selected_template = None
    for cand in candidates:
        try:
            selected_template = env.get_template(cand)
            break
        except jinja2.TemplateNotFound:
            continue

    if selected_template is None:
        # Fallback to direct load or raise
        selected_template = env.get_template(template_name)

    return selected_template.render(context)
