"""Add trades from a CSV file into the 'OPTIONS DEMO.xlsx' journal.

The script looks for a CSV file named ``recent trades.csv``. If it does not
exist it falls back to ``all_trades.csv``. Each trade is added as a new row in
the workbook using the same formulas and formatting as the existing rows.

Run the script with ``python journal_trades.py``.
"""
from __future__ import annotations

import csv
from copy import copy
from datetime import datetime
from pathlib import Path
from typing import Optional

from openpyxl import load_workbook


def _csv_path() -> Path:
    """Return the path to the CSV file containing the trade data."""
    for name in ("recent trades.csv", "all_trades.csv"):
        path = Path(name)
        if path.exists():
            return path
    raise FileNotFoundError("No trade CSV file found.")


def _float(value: str) -> Optional[float]:
    """Convert ``value`` to float if possible."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_symbol(symbol: str) -> tuple[str, datetime, float, str]:
    """Return (pair, expiry, strike, option_type) extracted from ``symbol``."""
    base, expiry_str, strike, opt, *_ = symbol.split("-")
    expiry = datetime.strptime(expiry_str, "%d%b%y")
    option_type = "CALL" if opt.upper().startswith("C") else "PUT"
    return base, expiry, float(strike), option_type


def _money(option: str, strike: float, underlying: Optional[float]) -> Optional[str]:
    """Return 'ITM' or 'OTM' if ``underlying`` price is available."""
    if underlying is None:
        return None
    if option == "CALL":
        return "ITM" if underlying >= strike else "OTM"
    return "ITM" if underlying <= strike else "OTM"


def main() -> None:
    csv_file = _csv_path()
    wb = load_workbook("OPTIONS DEMO.xlsx")
    ws = wb.active

    # Use row 3 as a template for formatting.
    template_row = 3
    max_col = ws.max_column

    # Find the last row with data in column B (Option column).
    last_row = ws.max_row
    while last_row > 1 and ws.cell(row=last_row, column=2).value is None:
        last_row -= 1

    with csv_file.open(newline="") as f:
        reader = csv.DictReader(f)
        for trade in reader:
            last_row += 1
            pair, expiry, strike, option_type = _parse_symbol(trade["symbol"])
            underlying = _float(trade.get("underlyingPrice"))
            entry_time = datetime.strptime(trade["localTime"], "%d/%m/%Y %H:%M")

            # Basic values
            ws.cell(row=last_row, column=1, value=_money(option_type, strike, underlying))
            ws.cell(row=last_row, column=2, value=option_type)
            ws.cell(row=last_row, column=3, value=strike)
            ws.cell(row=last_row, column=4, value=pair)
            ws.cell(row=last_row, column=5, value=trade.get("orderType"))
            ws.cell(row=last_row, column=6, value=trade.get("side"))
            ws.cell(row=last_row, column=8, value=_float(trade.get("execPrice")))
            ws.cell(row=last_row, column=9, value=_float(trade.get("markPrice")))
            ws.cell(row=last_row, column=10, value=_float(trade.get("execQty")))
            ws.cell(row=last_row, column=15, value=entry_time)
            ws.cell(row=last_row, column=16, value=entry_time)
            ws.cell(row=last_row, column=18, value=expiry)
            ws.cell(row=last_row, column=20, value=_float(trade.get("netFee") or trade.get("execFee")))
            ws.cell(row=last_row, column=22, value=_float(trade.get("tradeIv") or trade.get("markIv")))
            ws.cell(row=last_row, column=23, value=_float(trade.get("indexPrice")))
            ws.cell(row=last_row, column=24, value=_float(trade.get("markPrice")))
            ws.cell(row=last_row, column=25, value=_float(trade.get("underlyingPrice")))

            # Formulas
            ws.cell(row=last_row, column=7, value=f'=IF(F{last_row}="BUY", (H{last_row}*J{last_row})/-1,(H{last_row}*J{last_row}))')
            ws.cell(row=last_row, column=17, value=f'=TEXT((P{last_row}-O{last_row}), "hh:mm")')
            ws.cell(row=last_row, column=19, value=f'=R{last_row}-O{last_row}')
            ws.cell(row=last_row, column=27, value=f'=U{last_row}+T{last_row}+G{last_row}+(I{last_row}/100)')
            ws.cell(row=last_row, column=28, value=f'=AA{last_row}/AC{last_row-1}')
            ws.cell(row=last_row, column=29, value=f'=AC{last_row-1}+AA{last_row}')

            # Copy formatting from the template row.
            for col in range(1, max_col + 1):
                tmpl = ws.cell(row=template_row, column=col)
                cell = ws.cell(row=last_row, column=col)
                cell.font = copy(tmpl.font)
                cell.fill = copy(tmpl.fill)
                cell.border = copy(tmpl.border)
                cell.alignment = copy(tmpl.alignment)
                cell.number_format = tmpl.number_format

    wb.save("OPTIONS DEMO.xlsx")


if __name__ == "__main__":
    main()
