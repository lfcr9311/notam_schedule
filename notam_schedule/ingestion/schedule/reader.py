"""Flight schedule reader (CSV or XLSX).

Two layouts are accepted (detected from the header):

1. Dated flights — one row per flight:
   date;flight;origin;destination;std;sta
2. Period schedule (SSIM-like) — one row per operating period:
   start;end;frequency;flight;origin;destination;std;sta
   (SSIM export headers also work: Flt Desg, Eff Date, Dis Date, Freq, Dept Arp, Dept Time,
   Arvl Arp, Arrv Time, Subfleet, Block Time)
   frequency = operating weekdays, 1=Monday ... 7=Sunday (e.g. "1234567", ".2.4567", "135")

std/sta are optional (without times the analysis is done per day). Time basis (UTC or local)
can be forced or auto-detected from a block-time column. Airports may be IATA or ICAO.
Portuguese headers are also accepted (see ALIASES).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from ...domain.airport import Airport
from ...domain.flight import Flight, Schedule
from ..airports import AirportDB
from .columns import map_columns
from .errors import ScheduleError
from .files import read_rows
from .values import parse_date, parse_frequency, parse_time

log = logging.getLogger(__name__)
UTC = timezone.utc

def _detect_utc(rows: list[list[str]], cols: dict[str, int]) -> bool | None:
    """If STA - STD equals the block time on most rows, times are UTC. None when undecidable."""
    if not {"std", "sta", "block"} <= cols.keys():
        return None
    matches = total = 0
    for row in rows[:500]:
        try:
            std, sta, blk = (parse_time(row[cols[k]]) for k in ("std", "sta", "block"))
        except (ScheduleError, IndexError):
            continue
        if not (std and sta and blk):
            continue
        diff = ((sta[0].hour * 60 + sta[0].minute) - (std[0].hour * 60 + std[0].minute)) % 1440
        total += 1
        matches += diff == blk[0].hour * 60 + blk[0].minute
    if total < 10:
        return None
    return matches / total >= 0.8

def _to_utc(d: date, t: time, extra_days: int, ap: Airport, local: bool) -> datetime:
    naive = datetime.combine(d + timedelta(days=extra_days), t)
    if local:
        return naive.replace(tzinfo=ap.zone).astimezone(UTC)
    return naive.replace(tzinfo=UTC)

def load_schedule(
    path: str | Path,
    airports: AirportDB,
    local_times: bool | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sheet: str | None = None,
) -> Schedule:
    """local_times: True = local, False = UTC, None = auto-detect (block time), defaulting to local."""
    rows = read_rows(Path(path), sheet)
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise ScheduleError("Arquivo da malha vazio.")
    header, body = rows[0], rows[1:]
    cols = map_columns(header)

    for req in ("origin", "destination"):
        if req not in cols:
            raise ScheduleError(f"Coluna obrigatória '{req}' não encontrada. Cabeçalho lido: {header}")
    dated = "date" in cols
    periodic = "start" in cols and "end" in cols
    if not dated and not periodic:
        raise ScheduleError("A malha precisa ter a coluna 'date' ou as colunas 'start' e 'end'.")

    basis = "local" if local_times else "utc"
    if local_times is None:
        detected = _detect_utc(body, cols)
        local_times = detected is not True
        basis = ("utc" if detected else "local") + (" (auto)" if detected is not None else " (padrão)")
        log.info("Schedule time basis: %s", basis)

    def get(row: list[str], field_name: str) -> str:
        i = cols.get(field_name)
        return row[i].strip() if i is not None and i < len(row) else ""

    flights: list[Flight] = []
    errors: list[str] = []
    for lineno, row in enumerate(body, start=2):
        try:
            orig = airports.resolve(get(row, "origin"))
            dest = airports.resolve(get(row, "destination"))
            std_raw, sta_raw = get(row, "std"), get(row, "sta")
            std, sta = parse_time(std_raw), parse_time(sta_raw)
            if dated:
                days = [parse_date(get(row, "date"))]
            else:
                start, end = parse_date(get(row, "start")), parse_date(get(row, "end"))
                freq = parse_frequency(get(row, "frequency"))
                days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
                days = [d for d in days if d.weekday() in freq]
            for d in days:
                if (date_from and d < date_from) or (date_to and d > date_to):
                    continue
                std_utc = _to_utc(d, *std, orig, local_times) if std else None
                sta_utc = _to_utc(d, *sta, dest, local_times) if sta else None
                if std_utc and sta_utc and sta_utc <= std_utc and sta[1] == 0:
                    sta_utc += timedelta(days=1)  # arrival on the next day
                flights.append(Flight(
                    row=lineno, date=d, flight=re.sub(r"\s+", "", get(row, "flight")), origin=orig,
                    destination=dest, std_utc=std_utc, sta_utc=sta_utc, std_raw=std_raw, sta_raw=sta_raw,
                    aircraft=re.sub(r"\s+", " ", get(row, "aircraft")),
                ))
        except (ScheduleError, KeyError, ValueError) as exc:
            msg = exc.args[0] if exc.args else str(exc)
            errors.append(f"linha {lineno}: {msg}")
    if errors:
        raise ScheduleError("Erros na malha:\n  " + "\n  ".join(errors[:30]))
    return Schedule(flights, basis)

def read_schedule(path: str | Path, airports: AirportDB, local_times: bool | None = True,
                  date_from: date | None = None, date_to: date | None = None) -> list[Flight]:
    """Convenience wrapper returning only the flights."""
    return load_schedule(path, airports, local_times, date_from, date_to).flights
