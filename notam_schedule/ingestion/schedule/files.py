"""Reading CSV (; or ,) and XLSX files into rows of text."""
from __future__ import annotations

import csv
from datetime import date, datetime, time, timedelta
from pathlib import Path


def cell_to_str(v) -> str:
    """Convert an XLSX cell value to the same text a CSV would contain."""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d") if v.time() == time(0) else v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, time):
        return v.strftime("%H:%M")
    if isinstance(v, timedelta):  # durations such as block time
        mins = int(v.total_seconds() // 60)
        return f"{mins // 60:02d}:{mins % 60:02d}"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()

def read_rows(path: Path, sheet: str | None = None) -> list[list[str]]:
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb[sheet] if sheet else wb.worksheets[0]
        rows = [[cell_to_str(c) for c in r] for r in ws.iter_rows(values_only=True)]
        wb.close()
        return rows
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    lines = text.splitlines()
    if not lines:
        return []
    delim = ";" if lines[0].count(";") >= lines[0].count(",") else ","
    if "\t" in lines[0] and lines[0].count("\t") > lines[0].count(delim):
        delim = "\t"
    return [list(r) for r in csv.reader(lines, delimiter=delim)]
