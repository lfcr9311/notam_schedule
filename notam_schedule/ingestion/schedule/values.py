"""Parsing of schedule cell values: dates, times and SSIM weekday frequency."""
from __future__ import annotations

import re
from datetime import date, datetime, time

from .errors import ScheduleError

MONTHS_EN_PT = {
    "JAN": 1, "FEB": 2, "FEV": 2, "MAR": 3, "APR": 4, "ABR": 4, "MAY": 5, "MAI": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "AGO": 8, "SEP": 9, "SET": 9, "OCT": 10, "OUT": 10, "NOV": 11, "DEC": 12, "DEZ": 12,
}

def parse_date(value: str) -> date:
    v = value.strip().upper()
    v = re.sub(r"[ T]00:00(:00)?$", "", v)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            pass
    m = re.fullmatch(r"(\d{1,2})[-\s]?([A-Z]{3})[-\s]?(\d{2}|\d{4})", v)  # 12NOV26 / 12-NOV-2026
    if m and m.group(2) in MONTHS_EN_PT:
        y = int(m.group(3))
        return date(y + 2000 if y < 100 else y, MONTHS_EN_PT[m.group(2)], int(m.group(1)))
    raise ScheduleError(f"Data inválida: '{value}'")

def parse_time(value: str) -> tuple[time, int] | None:
    """Return (time, extra_days). Accepts HH:MM, HHMM, HH:MM:SS and a +1 suffix."""
    v = value.strip()
    if not v:
        return None
    extra = 0
    m = re.search(r"\+\s*(\d)$", v)
    if m:
        extra = int(m.group(1))
        v = v[: m.start()].strip()
    m = re.fullmatch(r"(\d{1,2}):?(\d{2})(?::\d{2})?", v)
    if not m:
        raise ScheduleError(f"Horário inválido: '{value}'")
    h, mi = int(m.group(1)), int(m.group(2))
    if h == 24 and mi == 0:
        return time(0, 0), extra + 1
    return time(h, mi), extra

def parse_frequency(value: str) -> set[int]:
    """'1234567', '.2.4567', '1 3 5', 'X2X4X6X' -> set of weekday() values (0=Monday)."""
    v = value.strip()
    if not v:
        return set(range(7))
    if len(v) == 7 and not v.isdigit():  # positional layout: '.2.4567' / '1-3-5--'
        return {i for i, ch in enumerate(v) if ch.isdigit() and ch != "0"}
    days = {int(ch) - 1 for ch in v if ch in "1234567"}
    if not days:
        raise ScheduleError(f"Frequência inválida: '{value}'")
    return days
