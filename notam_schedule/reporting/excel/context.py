"""Aggregations computed once and shared by every sheet of the workbook."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date

from ...domain.changes import ChangeSet
from ...domain.confidence import Confidence, impact_confidence
from ...domain.impact import AnalysisResult, Impact, RunwayNotam
from ..formatting import airport_map, flight_key, worst_severity, zone_of


@dataclass
class ReportContext:
    res: AnalysisResult
    params: dict
    aps: dict
    by_flight: dict[tuple, list[Impact]]
    by_rn: dict[int, list[Impact]]
    flight_worst: dict[tuple, str]
    flights_per_day: Counter
    ops_per_airport: Counter
    all_rns: list[RunwayNotam]
    rn_airport: dict[int, str]
    dates: list[date]
    by_day_flights: dict[date, set]
    flight_conf: dict[tuple, Confidence]    # lowest-confidence impact of each flight
    changes: ChangeSet | None = None     # vs. the previous recorded run (None = history off)

    def tz_of(self, i: Impact):
        return zone_of(self.aps, i.airport)

    @classmethod
    def build(cls, res: AnalysisResult, params: dict, changes: ChangeSet | None = None) -> ReportContext:
        aps = airport_map(res)
        by_flight: dict[tuple, list[Impact]] = defaultdict(list)
        by_rn: dict[int, list[Impact]] = defaultdict(list)
        for i in res.impacts:
            by_flight[flight_key(i)].append(i)
            by_rn[id(i.rn)].append(i)
        flight_worst = {k: worst_severity(i.severity for i in v) for k, v in by_flight.items()}
        flights_per_day = Counter(f.date for f in res.flights)
        ops_per_airport = Counter()
        for f in res.flights:
            ops_per_airport[f.origin.icao] += 1
            ops_per_airport[f.destination.icao] += 1
        all_rns = [rn for ap in sorted(res.runway_notams) for rn in res.runway_notams[ap]]
        rn_airport = {id(rn): ap for ap in res.runway_notams for rn in res.runway_notams[ap]}
        by_day_flights: dict[date, set] = defaultdict(set)
        for k in by_flight:
            by_day_flights[k[2]].add(k)
        flight_conf = {k: min((impact_confidence(i) for i in v), key=lambda c: c.score) for k, v in by_flight.items()}
        return cls(res, params, aps, by_flight, by_rn, flight_worst, flights_per_day, ops_per_airport, all_rns,
                   rn_airport, sorted(flights_per_day), by_day_flights, flight_conf, changes)
