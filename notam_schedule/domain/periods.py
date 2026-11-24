"""Expansion of NOTAM activity periods (items B, C and D) into UTC intervals."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from .notam import Notam
from .text import normalize

UTC = timezone.utc


# --------------------------------------------------------------------------------------
# Item D expansion (activity periods)
# Tokens below are NOTAM vocabulary (English and Portuguese) and must stay as-is.
# --------------------------------------------------------------------------------------
MONTHS = {
    "JAN": 1, "FEB": 2, "FEV": 2, "MAR": 3, "APR": 4, "ABR": 4, "MAY": 5, "MAI": 5,
    "JUN": 6, "JUL": 7, "AUG": 8, "AGO": 8, "SEP": 9, "SET": 9, "OCT": 10, "OUT": 10,
    "NOV": 11, "DEC": 12, "DEZ": 12,
}
WEEKDAYS = {
    "MON": 0, "SEG": 0, "TUE": 1, "TER": 1, "WED": 2, "QUA": 2, "THU": 3, "QUI": 3,
    "FRI": 4, "SEX": 4, "SAT": 5, "SAB": 5, "SUN": 6, "DOM": 6,
}
DAILY = {"DLY", "DAILY", "DIARIO", "DIARIAMENTE", "DIARIA", "DIAR", "TODOS"}
RANGE_WORDS = {"TIL", "ATE", "A", "TO", "THRU"}
EXCEPT_WORDS = {"EXC", "EXCETO", "EXCEPT"}
IGNORE = {"AND", "E", "&", "UTC", "HR", "HRS", "DURANTE", "OS", "DIAS", "DIA", "DE", "ON", "EVERY"}
# Conservative approximation (UTC) of sunrise/sunset in Brazil
SR_MIN, SS_MIN = 8 * 60, 22 * 60
SPECIAL_TIME = {"SR": SR_MIN, "SS": SS_MIN, "NASCER": SR_MIN, "POR": SS_MIN}

TIME_RANGE = re.compile(r"^(\d{4}|SR|SS)-(\d{4}|SR|SS)$")
WD_RANGE = re.compile(r"^([A-Z]{3})-([A-Z]{3})$")
DAY_RANGE = re.compile(r"^(\d{1,2})-(\d{1,2})$")
DAY_TO_MONTH = re.compile(r"^(\d{1,2})-([A-Z]{3})$")  # "16-DEC" in "NOV 16-DEC 06" (cross-month range)


def _hhmm(tok: str) -> int:
    if tok in SPECIAL_TIME:
        return SPECIAL_TIME[tok]
    h, m = int(tok[:2]), int(tok[2:])
    if h > 24 or m > 59:
        raise ValueError(tok)
    total = h * 60 + m
    return 1440 if total >= 1439 else total


@dataclass
class _Group:
    dates: list[date] = field(default_factory=list)
    weekdays: set[int] = field(default_factory=set)
    excl_dates: set[date] = field(default_factory=set)
    excl_weekdays: set[int] = field(default_factory=set)
    times: list[tuple[int, int]] = field(default_factory=list)

    inherited: bool = False  # dates copied from the previous group ("SEP 18-30 MON-FRI ..., SAT ...")

    def has_days(self) -> bool:
        return bool(self.dates or self.weekdays)


def _merge(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def expand_periods(
    notam: Notam, win_start: datetime, win_end: datetime
) -> tuple[list[tuple[datetime, datetime]], bool, str]:
    """Compute the UTC intervals in which the NOTAM is active within the analysis window.

    Returns (intervals, parsed_ok, note). When item D cannot be parsed, the NOTAM is
    considered active for the whole B–C period (conservative).
    """
    start = notam.start or win_start
    end = notam.end or win_end
    eff_start, eff_end = max(start, win_start), min(end, win_end)
    if eff_start >= eff_end:
        return [], True, ""
    d = normalize(notam.d)
    if not d:
        return [(eff_start, eff_end)], True, ""

    try:
        groups, unknown, approx = _parse_d(d, start)
    except ValueError as exc:
        groups, unknown, approx = [], [str(exc)], False
    if unknown or not groups:
        note = f"Item D não interpretado ({notam.d!r}); considerado ativo em todo o período B–C."
        return [(eff_start, eff_end)], False, note

    intervals: list[tuple[datetime, datetime]] = []
    first_day = eff_start.date() - timedelta(days=1)
    n_days = (eff_end.date() - first_day).days + 1
    all_days = [first_day + timedelta(days=i) for i in range(n_days)]
    for g in groups:
        if g.dates:
            days = list(g.dates)
            if g.weekdays:  # "NOV 18-JAN 02 WED THU FRI SAT": dates filtered by weekday
                days = [x for x in days if x.weekday() in g.weekdays]
        elif g.weekdays:
            days = [x for x in all_days if x.weekday() in g.weekdays]
        else:
            days = list(all_days)
        days = [x for x in days if x not in g.excl_dates and x.weekday() not in g.excl_weekdays]
        for day in days:
            base = datetime(day.year, day.month, day.day, tzinfo=UTC)
            for t1, t2 in g.times or [(0, 1440)]:
                s = base + timedelta(minutes=t1)
                e = base + timedelta(minutes=t2 if t2 > t1 else t2 + 1440)
                s, e = max(s, eff_start), min(e, eff_end)
                if s < e:
                    intervals.append((s, e))
    note = "Horário SR/SS aproximado (0800–2200 UTC)." if approx else ""
    return _merge(intervals), True, note


def _parse_d(d: str, ref: datetime) -> tuple[list[_Group], list[str], bool]:
    tokens = re.sub(r"\s*-\s*", "-", re.sub(r"[,;.]", " ", d)).split()
    groups: list[_Group] = []
    g = _Group()
    month = ref.month
    in_except = False
    pending_range = False
    last_date: date | None = None
    last_wd: int | None = None
    last_was_time = False
    unknown: list[str] = []
    approx = False

    def resolve_date(day: int) -> date:
        year = ref.year
        cand = date(year, month, day)
        if cand < ref.date() - timedelta(days=1):
            cand = date(year + 1, month, day)
        return cand

    def new_day_token(weekday: bool = False) -> None:
        nonlocal g, in_except, last_was_time
        if last_was_time:
            groups.append(g)
            prev = g
            g = _Group()
            in_except = False
            last_was_time = False
            # A weekday-only group right after a "date range + weekdays" group keeps that date range:
            # "SEP 18-30 MON-FRI 0240-0845, SAT 0240-0845" -> SAT within SEP 18-30.
            if weekday and prev.dates and (prev.weekdays or prev.inherited):
                g.dates, g.excl_dates, g.inherited = list(prev.dates), set(prev.excl_dates), True

    def add_dates(ds: list[date]) -> None:
        (g.excl_dates.update(ds) if in_except else g.dates.extend(ds))

    def add_wds(ws: list[int]) -> None:
        (g.excl_weekdays.update(ws) if in_except else g.weekdays.update(ws))

    def wd_span(a: int, b: int) -> list[int]:
        return [(a + i) % 7 for i in range(((b - a) % 7) + 1)]

    for tok in tokens:
        if tok in IGNORE:
            continue
        if tok == "H24":
            g.times.append((0, 1440))
            last_was_time = True
            continue
        m = TIME_RANGE.match(tok)
        if m:
            if "SR" in tok or "SS" in tok:
                approx = True
            g.times.append((_hhmm(m.group(1)), _hhmm(m.group(2))))
            last_was_time = True
            continue
        if tok in RANGE_WORDS:
            pending_range = True
            continue
        if tok in EXCEPT_WORDS:
            in_except = True
            continue
        if tok in DAILY:
            new_day_token()
            continue
        if tok in MONTHS:
            new_day_token()
            month = MONTHS[tok]
            continue
        if tok in WEEKDAYS:
            new_day_token(weekday=True)
            wd = WEEKDAYS[tok]
            if pending_range and last_wd is not None:
                add_wds(wd_span(last_wd, wd))
            else:
                add_wds([wd])
            last_wd, pending_range = wd, False
            continue
        m = WD_RANGE.match(tok)
        if m and m.group(1) in WEEKDAYS and m.group(2) in WEEKDAYS:
            new_day_token(weekday=True)
            a, b = WEEKDAYS[m.group(1)], WEEKDAYS[m.group(2)]
            add_wds(wd_span(a, b))
            last_wd = b
            continue
        m = DAY_TO_MONTH.match(tok)
        if m and m.group(2) in MONTHS:
            new_day_token()
            last_date = resolve_date(int(m.group(1)))
            add_dates([last_date])
            month = MONTHS[m.group(2)]
            pending_range = True  # the next day number closes the range in the new month
            continue
        m = DAY_RANGE.match(tok)
        if m:
            new_day_token()
            a, b = resolve_date(int(m.group(1))), resolve_date(int(m.group(2)))
            add_dates([a + timedelta(days=i) for i in range((b - a).days + 1)])
            last_date = b
            continue
        if tok.isdigit() and len(tok) <= 2:
            new_day_token()
            cur = resolve_date(int(tok))
            if pending_range and last_date is not None and cur > last_date:
                add_dates([last_date + timedelta(days=i) for i in range(1, (cur - last_date).days + 1)])
            else:
                add_dates([cur])
            last_date, pending_range = cur, False
            continue
        unknown.append(tok)

    if g.times or g.has_days() or g.excl_weekdays or g.excl_dates:
        groups.append(g)
    return groups, unknown, approx
