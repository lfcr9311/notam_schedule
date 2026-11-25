"""Use case: cross a flight schedule with the runway NOTAMs of its airports."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime

from ..detection.rules import classify
from ..domain.flight import Flight
from ..domain.impact import (
    AnalysisResult, RunwayNotam, analysis_window, dedupe, find_impacts, make_runway_notam,
)
from ..domain.notam import Notam, drop_replaced
from ..domain.periods import expand_periods
from ..domain.ports import BatchDetector, Detector, NotamSource, SourceError, covers
from ..domain.text import normalize

log = logging.getLogger(__name__)
RUNWAY_WORDS = ("RWY", "PISTA", "THR", "CABECEIRA")


def split_coverage(icaos, source: NotamSource | None = None) -> tuple[list[str], list[str]]:
    """(airports the source has data for, airports outside its coverage). Without a source: Brazil only."""
    icaos = sorted(set(icaos))
    return [c for c in icaos if covers(source, c)], [c for c in icaos if not covers(source, c)]


@dataclass
class OtherNotam:
    """A NOTAM that is not a runway restriction (taxiway, navaid, obstacle...), active in the window."""
    notam: Notam
    intervals: list[tuple[datetime, datetime]]
    d_parsed: bool
    note: str


@dataclass
class Collected:
    runway: dict[str, list[RunwayNotam]] = field(default_factory=dict)
    discarded: dict[str, list[Notam]] = field(default_factory=dict)   # mention a runway but were not classified
    errors: dict[str, str] = field(default_factory=dict)
    other: dict[str, list[OtherNotam]] = field(default_factory=dict)  # only with keep_other=True


def collect_runway_notams(
    source: NotamSource,
    icaos: list[str],
    win_start: datetime,
    win_end: datetime,
    categories: set[str] | None = None,
    use_rotaer: bool = True,
    detector: Detector | None = None,
) -> tuple[dict[str, list[RunwayNotam]], dict[str, list[Notam]], dict[str, str]]:
    """Runway NOTAMs active in [win_start, win_end] for each airport.

    Returns (runway NOTAMs per ICAO, discarded runway-mentioning NOTAMs per ICAO, query errors per ICAO).
    An airport with an error has no entry in the first dict: callers must report it as "no data".
    """
    c = collect_notams(source, icaos, win_start, win_end, categories, use_rotaer, detector)
    return c.runway, c.discarded, c.errors


def collect_notams(
    source: NotamSource,
    icaos: list[str],
    win_start: datetime,
    win_end: datetime,
    categories: set[str] | None = None,
    use_rotaer: bool = True,
    detector: Detector | None = None,
    keep_other: bool = False,
) -> Collected:
    """Like collect_runway_notams; with keep_other=True also every other NOTAM active in the window."""
    detect = detector if detector is not None else classify
    out = Collected()
    runway_notams, discarded, errors, other = out.runway, out.discarded, out.errors, out.other
    for icao in icaos:
        try:
            notams = drop_replaced(source.notams(icao))
        except SourceError as exc:
            log.error("%s: %s", icao, exc)
            errors[icao] = str(exc)
            continue
        runways = source.runways(icao) if use_rotaer else []
        if isinstance(detect, BatchDetector):
            detect.prepare(notams)
        items: list[RunwayNotam] = []
        dropped: list[Notam] = []
        others: list[OtherNotam] = []
        for n in notams:
            c = detect(n)
            if categories is not None:
                c = replace(c, categories=[k for k in c.categories if k in categories])
            if not c.relevant:
                if any(w in normalize(n.e) for w in RUNWAY_WORDS):
                    dropped.append(n)
                if keep_other:
                    intervals, ok, note = expand_periods(n, win_start, win_end)
                    if intervals:
                        others.append(OtherNotam(n, intervals, ok, note))
                continue
            intervals, ok, note = expand_periods(n, win_start, win_end)
            if not intervals:
                continue  # outside the window
            items.append(make_runway_notam(n, c, intervals, ok, note, runways))
        runway_notams[icao] = dedupe(items)
        discarded[icao] = dropped
        if keep_other:
            other[icao] = others
        log.info("%s: %d NOTAMs, %d runway NOTAMs in window", icao, len(notams), len(runway_notams[icao]))
    return out


def analyze(
    flights: list[Flight],
    source: NotamSource,
    margin_before: int = 60,
    margin_after: int = 60,
    categories: set[str] | None = None,
    use_rotaer: bool = True,
    detector: Detector | None = None,
) -> AnalysisResult:
    """detector: Notam -> Classification (e.g. ml.detector.HybridDetector). Default: rules only.

    Airports whose query fails go to `errors`; airports the source does not cover go to `out_of_coverage`.
    Neither is ever reported as "no impact".
    """
    if not flights:
        raise ValueError("Nenhum voo na malha para o período informado.")
    win_start, win_end = analysis_window(flights)

    codes = {f.origin for f in flights} | {f.destination for f in flights}
    to_query = sorted({a.icao for a in codes if a.icao and covers(source, a.icao)})
    out_of_coverage = sorted({a.icao or f"{a.iata} (sem ICAO na tabela)" for a in codes
                              if not (a.icao and covers(source, a.icao))})
    runway_notams, discarded, errors = collect_runway_notams(
        source, to_query, win_start, win_end, categories, use_rotaer, detector)
    impacts = find_impacts(flights, runway_notams, margin_before, margin_after)
    return AnalysisResult(flights, impacts, runway_notams, discarded, errors, out_of_coverage, (win_start, win_end))
