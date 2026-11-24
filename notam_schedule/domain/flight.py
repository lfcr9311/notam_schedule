"""Flight and schedule entities (times already converted to UTC)."""
from __future__ import annotations

from dataclasses import dataclass
import datetime as dt

from .airport import Airport


@dataclass
class Flight:
    row: int
    date: dt.date                  # flight date (as written in the schedule)
    flight: str
    origin: Airport
    destination: Airport
    std_utc: dt.datetime | None
    sta_utc: dt.datetime | None
    std_raw: str
    sta_raw: str
    aircraft: str


@dataclass
class Schedule:
    flights: list[Flight]
    time_basis: str             # "utc" or "local", with " (auto)" when detected
