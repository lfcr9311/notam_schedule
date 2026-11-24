"""Airport and runway entities."""
from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

# Brazilian ICAO prefixes (covered by AISWEB/DECEA)
BR_PREFIXES = ("SB", "SD", "SI", "SJ", "SN", "SS", "SW")  # SG = Paraguay, SR is not used by Brazil


@dataclass(frozen=True)
class Airport:
    iata: str
    icao: str
    name: str
    tz: str

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    @property
    def is_brazilian(self) -> bool:
        return self.icao.startswith(BR_PREFIXES)



@dataclass
class Runway:
    ident: str      # e.g. "15/33"
    length: int | None
    width: int | None

    def matches(self, designator: str) -> bool:
        """'15', '33', '15/33' or '15L' match runway '15/33'."""
        parts = {p.strip() for p in self.ident.upper().split("/")}
        wanted = {p.strip() for p in designator.upper().split("/")}
        return bool(parts & wanted)
