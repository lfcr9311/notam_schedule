"""NOTAM entity, date-time groups (items B/C) and replacement lineage."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .text import normalize

UTC = timezone.utc


def parse_dtg(value: str | None) -> tuple[datetime | None, bool, bool]:
    """Parse a NOTAM date-time group.

    Returns (UTC datetime or None, permanent, estimated).
    Accepts YYMMDDHHMM (ICAO standard), YYYYMMDDHHMM, YYYYMMDDHHMMSS and 'PERM'.
    """
    if not value:
        return None, False, False
    v = value.strip().upper()
    if "PERM" in v:
        return None, True, False
    estimated = "EST" in v
    digits = re.sub(r"\D", "", v)
    try:
        if len(digits) == 10:
            dt = datetime.strptime(digits, "%y%m%d%H%M")
        elif len(digits) == 12:
            dt = datetime.strptime(digits, "%Y%m%d%H%M")
        elif len(digits) == 14:
            dt = datetime.strptime(digits, "%Y%m%d%H%M%S")
        else:
            return None, False, estimated
    except ValueError:
        return None, False, estimated
    return dt.replace(tzinfo=UTC), False, estimated


@dataclass
class Notam:
    number: str               # <n> field, e.g. "C1234/26"
    loc: str                  # location ICAO
    ntype: str                # N (new), R (replace) or C (cancel)
    ref: str                  # replaced/cancelled NOTAM
    start: datetime | None    # item B
    end: datetime | None      # item C (None when PERM)
    permanent: bool
    end_estimated: bool
    d: str
    e: str
    raw: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.raw.get("id") or self.number

    @property
    def international(self) -> bool:
        return self.raw.get("status", "").upper() == "I"



def drop_replaced(notams: list[Notam]) -> list[Notam]:
    """Remove cancel NOTAMs (C) and the ones replaced/cancelled by others."""
    referenced = {normalize(n.ref) for n in notams if n.ntype in ("R", "C") and n.ref}
    return [n for n in notams if n.ntype != "C" and normalize(n.number) not in referenced]
