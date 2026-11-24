"""Schedule x runway NOTAM cross-check: severity, de-duplication and flight impacts.

Pure logic. Fetching NOTAMs and running the detector is the job of pipelines.analyze.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from .airport import Runway
from .categories import (
    AERODROME_CLOSED, LENGTH_REDUCTION, RUNWAY_CLOSED, SURFACE_CONDITION, WIDTH_REDUCTION, Classification,
)
from .flight import Flight
from .notam import Notam

UTC = timezone.utc

CRITICAL, HIGH, MEDIUM, LOW = "CRITICAL", "HIGH", "MEDIUM", "LOW"
SEVERITY_ORDER = {CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3}


@dataclass
class RunwayNotam:
    """Relevant NOTAM, already classified and with its activity intervals computed."""
    notam: Notam
    cls: Classification
    intervals: list[tuple[datetime, datetime]]
    d_parsed: bool
    note: str
    base_runway: Runway | None = None          # runway as published in ROTAER
    airport_runway_count: int | None = None
    duplicates: list[str] = field(default_factory=list)
    airport_runways: list[Runway] = field(default_factory=list)  # all runways in ROTAER

    @property
    def closes_all_runways(self) -> bool:
        """Runway closure that covers every runway published for the airport."""
        if self.cls.primary != RUNWAY_CLOSED or not self.cls.runways:
            return False
        if self.airport_runway_count == 1:
            return True
        return bool(self.airport_runways) and all(
            any(r.matches(p) for p in self.cls.runways) for r in self.airport_runways)

    @property
    def reduction_m(self) -> int | None:
        if self.base_runway and self.base_runway.length and self.cls.available_length:
            return self.base_runway.length - self.cls.available_length
        if self.cls.closed_length:
            return self.cls.closed_length
        return None

    @property
    def reduction_pct(self) -> float | None:
        r = self.reduction_m
        if r is not None and self.base_runway and self.base_runway.length:
            return round(100 * r / self.base_runway.length, 1)
        return None

    @property
    def severity(self) -> str:
        cat = self.cls.primary
        if cat == AERODROME_CLOSED:
            return CRITICAL
        if cat == RUNWAY_CLOSED:
            return CRITICAL if self.closes_all_runways else HIGH
        if cat == LENGTH_REDUCTION:
            pct = self.reduction_pct
            return MEDIUM if pct is not None and pct < 10 else HIGH
        if cat == WIDTH_REDUCTION:
            return MEDIUM
        return LOW if cat == SURFACE_CONDITION else MEDIUM

    def active_between(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        """Intervals overlapping [start, end]. start == end checks a single instant (s <= t < e)."""
        if start == end:
            return [(s, e) for s, e in self.intervals if s <= start < e]
        return [(s, e) for s, e in self.intervals if s < end and e > start]


@dataclass
class Impact:
    flight: Flight
    side: str                # ORIGIN / DESTINATION
    airport: str             # ICAO
    reference_utc: datetime | None
    rn: RunwayNotam
    windows: list[tuple[datetime, datetime]]
    open_runways: list[str] | None = None    # runways with no closure in the flight window (None: ROTAER unknown)
    no_runway: bool = False                  # at some moment of the window every published runway is closed

    @property
    def severity(self) -> str:
        """NOTAM severity, raised to CRITICAL when the combined closures leave the airport without a runway."""
        return CRITICAL if self.no_runway else self.rn.severity


@dataclass
class AnalysisResult:
    flights: list[Flight]
    impacts: list[Impact]
    runway_notams: dict[str, list[RunwayNotam]]
    discarded: dict[str, list[Notam]]        # NOTAMs mentioning a runway that did not pass the filter
    errors: dict[str, str]                   # ICAO -> query error
    out_of_coverage: list[str]               # non-Brazilian / unknown airports
    window: tuple[datetime, datetime]


def dedupe(items: list[RunwayNotam]) -> list[RunwayNotam]:
    """Merge national/international versions of the same NOTAM (same validity, category and runway)."""
    out: dict[tuple, RunwayNotam] = {}
    for it in items:
        key = (it.notam.loc, it.notam.start, it.notam.end, it.cls.primary, tuple(it.cls.runways))
        if key in out:
            out[key].duplicates.append(it.notam.number)
            # prefer the national (Portuguese) version as the primary one
            if out[key].notam.international and not it.notam.international:
                it.duplicates = out[key].duplicates[:-1] + [out[key].notam.number]
                out[key] = it
        else:
            out[key] = it
    return list(out.values())


def analysis_window(flights: list[Flight]) -> tuple[datetime, datetime]:
    """UTC window covering the schedule plus one day before and two after (overnight NOTAM periods)."""
    dates = [f.date for f in flights]
    return (datetime.combine(min(dates) - timedelta(days=1), datetime.min.time(), UTC),
            datetime.combine(max(dates) + timedelta(days=2), datetime.min.time(), UTC))


def make_runway_notam(n: Notam, c: Classification, intervals: list[tuple[datetime, datetime]], d_parsed: bool,
                      note: str, runways: list[Runway]) -> RunwayNotam:
    """Attach the published runway (ROTAER) and the operational notes to a classified NOTAM."""
    if c.source == "ml":
        note = (f"Detectado só pelo modelo de ML (prob. {c.prob:.2f}) — revisar. " + note).strip()
    base = None
    for p in c.runways:
        base = next((r for r in runways if r.matches(p)), None)
        if base:
            break
    if base is None and len(runways) == 1:
        base = runways[0]
    if c.conditional:
        note = (note + " Operação possível mediante coordenação prévia/PPR (ver RMK).").strip()
    return RunwayNotam(n, c, intervals, d_parsed, note, base, len(runways) or None, airport_runways=list(runways))


Interval = tuple[datetime, datetime]


def affects_operation(rn: RunwayNotam, side: str) -> bool:
    """A landing-only closure does not affect a departure, and a takeoff-only closure does not affect an arrival."""
    ops = rn.cls.closed_ops
    return not ((ops == "LDG" and side == "ORIGIN") or (ops == "TKOF" and side == "DESTINATION"))


def _union(intervals: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        s, e = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if s < e:
            out.append((s, e))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def runway_availability(rns: list[RunwayNotam], side: str, start: datetime,
                        end: datetime) -> tuple[list[str] | None, bool]:
    """Combine every closure active at the airport in [start, end] for this operation (departure/arrival).

    Returns (runways with no closure in the window, True if at some moment no published runway is open).
    Runways come from ROTAER; without them availability is unknown (None, False).
    """
    published = next((rn.airport_runways for rn in rns if rn.airport_runways), [])
    if not published:
        return None, False
    closed: dict[str, list[Interval]] = {r.ident: [] for r in published}
    for rn in rns:
        if not affects_operation(rn, side):
            continue
        cat = rn.cls.primary
        if cat == AERODROME_CLOSED:
            hit = list(closed)
        elif cat == RUNWAY_CLOSED and rn.cls.runways:
            hit = [r.ident for r in published if any(r.matches(p) for p in rn.cls.runways)]
        else:
            continue
        for ident in hit:
            closed[ident] += [(max(s, start), min(e, end)) for s, e in rn.active_between(start, end)]
    open_runways = [ident for ident, iv in closed.items() if not iv]
    if start == end:  # single instant: every runway closed at that moment = no runway
        return open_runways, not open_runways
    all_closed = [(start, end)]
    for iv in closed.values():
        all_closed = _intersect(all_closed, _union(iv))
        if not all_closed:
            break
    return open_runways, bool(all_closed)


def leg_restrictions(rns: list[RunwayNotam], side: str, start: datetime, end: datetime
                     ) -> tuple[list[tuple[RunwayNotam, list[Interval]]], list[str] | None, bool]:
    """NOTAMs restricting one operation (side ORIGIN = departure, DESTINATION = arrival) in [start, end].

    Returns ([(NOTAM, its active windows)], runways with no closure in the window, airport without a runway?).
    """
    hits = [(rn, w) for rn in rns if affects_operation(rn, side) and (w := rn.active_between(start, end))]
    if not hits:
        return [], None, False
    open_runways, no_runway = runway_availability(rns, side, start, end)
    return hits, open_runways, no_runway


def find_impacts(flights: list[Flight], runway_notams: dict[str, list[RunwayNotam]],
                 margin_before: int = 60, margin_after: int = 60) -> list[Impact]:
    """Flights whose departure (origin) or arrival (destination) window overlaps an active runway NOTAM.

    Each impact also says which runways stay open in that window, combining all closures at the airport,
    so two NOTAMs that each close one of the two runways make the flight CRITICAL (no runway).
    """
    impacts: list[Impact] = []
    before, after = timedelta(minutes=margin_before), timedelta(minutes=margin_after)
    for f in flights:
        for side, ap, ref in (("ORIGIN", f.origin, f.std_utc), ("DESTINATION", f.destination, f.sta_utc)):
            rns = runway_notams.get(ap.icao, [])
            if not rns:
                continue
            if ref is not None:
                start, end = ref - before, ref + after
            else:  # no time: use the whole local day at the airport
                start = datetime.combine(f.date, datetime.min.time(), ap.zone).astimezone(UTC)
                end = start + timedelta(days=1)
            hits, open_runways, no_runway = leg_restrictions(rns, side, start, end)
            impacts += [Impact(f, side, ap.icao, ref, rn, windows, open_runways, no_runway) for rn, windows in hits]
    impacts.sort(key=lambda i: (i.flight.date, i.flight.std_utc or datetime.min.replace(tzinfo=UTC),
                                i.flight.flight, SEVERITY_ORDER[i.severity]))
    return impacts


def daily_summary(res: AnalysisResult) -> list[dict]:
    by_day: dict[date, dict] = {}
    for f in res.flights:
        d = by_day.setdefault(f.date, {"flights": 0, "impacted": set(), "airports": {}, "critical": set()})
        d["flights"] += 1
    for i in res.impacts:
        d = by_day[i.flight.date]
        key = (i.flight.row, i.flight.flight, i.flight.date, i.flight.origin.icao)
        d["impacted"].add(key)
        if i.severity == CRITICAL:
            d["critical"].add(key)
        d["airports"].setdefault(i.airport, set()).add(i.rn.cls.primary)
    return [
        {
            "date": day,
            "flights": by_day[day]["flights"],
            "impacted": len(by_day[day]["impacted"]),
            "critical": len(by_day[day]["critical"]),
            "airports": by_day[day]["airports"],
        }
        for day in sorted(by_day)
    ]
