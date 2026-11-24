"""Runway-condition categories and the classification result attached to a NOTAM."""
from __future__ import annotations

from dataclasses import dataclass, field

AERODROME_CLOSED = "AERODROME_CLOSED"
RUNWAY_CLOSED = "RUNWAY_CLOSED"
LENGTH_REDUCTION = "LENGTH_REDUCTION"
WIDTH_REDUCTION = "WIDTH_REDUCTION"
SURFACE_CONDITION = "SURFACE_CONDITION"
NOT_RUNWAY = "NOT_RUNWAY"         # not a runway restriction (label used by Q-code mapping and ML)

# Order = priority (the first one found becomes the primary category)
CATEGORIES = [AERODROME_CLOSED, RUNWAY_CLOSED, LENGTH_REDUCTION, WIDTH_REDUCTION, SURFACE_CONDITION]

# Display labels for the (Portuguese) report
DESCRIPTION = {
    AERODROME_CLOSED: "Aeródromo fechado",
    RUNWAY_CLOSED: "Pista fechada",
    LENGTH_REDUCTION: "Redução de comprimento / distâncias declaradas",
    WIDTH_REDUCTION: "Largura reduzida",
    SURFACE_CONDITION: "Condição da superfície / obras na pista",
}


@dataclass
class Classification:
    categories: list[str] = field(default_factory=list)
    runways: list[str] = field(default_factory=list)
    declared: dict[str, int] = field(default_factory=dict)  # TORA/TODA/ASDA/LDA -> meters (lowest value)
    available_length: int | None = None
    closed_length: int | None = None
    source: str = "rules"          # rules | ml | rules+ml
    prob: float | None = None      # model probability of being a runway NOTAM
    # Closure details (RUNWAY_CLOSED / AERODROME_CLOSED)
    closed_ops: str = ""           # "LDG" (landing only), "TKOF" (takeoff only) or "" (both)
    conditional: bool = False      # operations may be authorised with prior coordination / PPR
    exception: str = ""            # the EXC ... clause (e.g. "INT RWY 03/21")

    @property
    def relevant(self) -> bool:
        return bool(self.categories)

    @property
    def primary(self) -> str:
        return self.categories[0] if self.categories else ""
