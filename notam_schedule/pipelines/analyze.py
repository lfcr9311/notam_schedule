"""Use case: cross a flight schedule with the runway NOTAMs of its airports."""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime

from ..detection.rules import classify
from ..domain.airport import BR_PREFIXES
from ..domain.flight import Flight
from ..domain.impact import (
    AnalysisResult, RunwayNotam, analysis_window, dedupe, find_impacts, make_runway_notam,
)
from ..domain.notam import Notam, drop_replaced
from ..domain.periods import expand_periods
from ..domain.ports import BatchDetector, Detector, NotamSource, SourceError
from ..domain.text import normalize

log = logging.getLogger(__name__)
RUNWAY_WORDS = ("RWY", "PISTA", "THR", "CABECEIRA")


def split_coverage(icaos) -> tuple[list[str], list[str]]:
    """(Brazilian airports to query at the AISWEB, airports outside DECEA coverage)."""
    icaos = sorted(set(icaos))
    return [c for c in icaos if c.startswith(BR_PREFIXES)], [c for c in icaos if not c.startswith(BR_PREFIXES)]


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
    detect = detector if detector is not None else classify
    runway_notams: dict[str, list[RunwayNotam]] = {}
    discarded: dict[str, list[Notam]] = {}
    errors: dict[str, str] = {}
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
        for n in notams:
            c = detect(n)
            if categories is not None:
                c = replace(c, categories=[k for k in c.categories if k in categories])
            if not c.relevant:
                if any(w in normalize(n.e) for w in RUNWAY_WORDS):
                    dropped.append(n)
                continue
            intervals, ok, note = expand_periods(n, win_start, win_end)
            if not intervals:
                continue  # outside the window
            items.append(make_runway_notam(n, c, intervals, ok, note, runways))
        runway_notams[icao] = dedupe(items)
        discarded[icao] = dropped
        log.info("%s: %d NOTAMs, %d runway NOTAMs in window", icao, len(notams), len(runway_notams[icao]))
    return runway_notams, discarded, errors


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

    Airports whose query fails go to `errors`; non-Brazilian airports go to `out_of_coverage`.
    Neither is ever reported as "no impact".
    """
    if not flights:
        raise ValueError("Nenhum voo na malha para o período informado.")
    win_start, win_end = analysis_window(flights)

    codes = {f.origin for f in flights} | {f.destination for f in flights}
    to_query = sorted({a.icao for a in codes if a.icao.startswith(BR_PREFIXES)})
    out_of_coverage = sorted({a.icao or f"{a.iata} (sem ICAO na tabela)" for a in codes
                              if not a.icao.startswith(BR_PREFIXES)})
    runway_notams, discarded, errors = collect_runway_notams(
        source, to_query, win_start, win_end, categories, use_rotaer, detector)
    impacts = find_impacts(flights, runway_notams, margin_before, margin_after)
    return AnalysisResult(flights, impacts, runway_notams, discarded, errors, out_of_coverage, (win_start, win_end))
