"""What changed between two analysis runs: new, ended and modified flight impacts."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .impact import Impact

NEW, ENDED, SEVERITY_CHANGED, WINDOW_CHANGED = "NOVO", "ENCERRADO", "SEVERIDADE MUDOU", "JANELA MUDOU"


@dataclass(frozen=True)
class ImpactRecord:
    """Flat, storable form of an Impact. `key` identifies the same flight x NOTAM across runs."""
    flight: str
    date: str                # ISO flight date
    origin: str
    destination: str
    side: str                # ORIGIN / DESTINATION
    airport: str
    notam: str
    category: str
    severity: str
    window_start: str        # ISO UTC, earliest active window touching the flight
    window_end: str

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        return (self.flight, self.date, self.side, self.airport, self.notam)

    @classmethod
    def from_impact(cls, i: Impact) -> ImpactRecord:
        f = i.flight
        return cls(f.flight, f.date.isoformat(), f.origin.icao or f.origin.iata, f.destination.icao or f.destination.iata,
                   i.side, i.airport, i.rn.notam.number, i.rn.cls.primary, i.severity,
                   min(s for s, _ in i.windows).isoformat(), max(e for _, e in i.windows).isoformat())


@dataclass
class Change:
    kind: str                          # NEW / ENDED / SEVERITY_CHANGED / WINDOW_CHANGED
    current: ImpactRecord | None
    previous: ImpactRecord | None

    @property
    def record(self) -> ImpactRecord:
        return self.current or self.previous  # type: ignore[return-value]


@dataclass
class ChangeSet:
    previous_run: dict | None          # metadata of the run compared against (None = first run)
    changes: list[Change] = field(default_factory=list)

    def count(self, kind: str) -> int:
        return sum(c.kind == kind for c in self.changes)


def diff_impacts(previous: list[ImpactRecord], current: list[ImpactRecord], period: tuple[date, date],
                 previous_run: dict | None = None) -> ChangeSet:
    """Compare two runs. Impacts that disappeared only count as ENDED inside the current schedule period,
    so a run over a different date range does not report every other day as ended."""
    start, end = period[0].isoformat(), period[1].isoformat()
    prev = {r.key: r for r in previous}
    cur = {r.key: r for r in current}
    changes: list[Change] = []
    for k, r in cur.items():
        p = prev.get(k)
        if p is None:
            changes.append(Change(NEW, r, None))
        elif p.severity != r.severity:
            changes.append(Change(SEVERITY_CHANGED, r, p))
        elif (p.window_start, p.window_end) != (r.window_start, r.window_end):
            changes.append(Change(WINDOW_CHANGED, r, p))
    for k, p in prev.items():
        if k not in cur and start <= p.date <= end:
            changes.append(Change(ENDED, None, p))
    order = {NEW: 0, SEVERITY_CHANGED: 1, WINDOW_CHANGED: 2, ENDED: 3}
    changes.sort(key=lambda c: (order[c.kind], c.record.date, c.record.flight, c.record.side))
    return ChangeSet(previous_run, changes)
