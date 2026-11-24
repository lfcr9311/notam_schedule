"""Combining closures: open runways per flight and airports left without a runway."""
from notam_schedule.detection.rules import classify
from notam_schedule.domain.airport import Airport, Runway
from notam_schedule.domain.flight import Flight
from notam_schedule.domain.impact import CRITICAL, HIGH, RunwayNotam, find_impacts
from tests.helpers import U, real_notam

SBBE = Airport("BEL", "SBBE", "Belem", "America/Belem")
GRU = Airport("GRU", "SBGR", "Guarulhos", "America/Sao_Paulo")
RWYS = [Runway("06/24", 2800, 45), Runway("02/20", 1830, 45)]


def closure(text, start_h, end_h, runways=RWYS):
    n = real_notam(text)
    return RunwayNotam(n, classify(n), [(U(2026, 12, 1, start_h), U(2026, 12, 1, end_h))], True, "",
                       None, len(runways), airport_runways=runways)


def flight(dep_h=None, arr_h=None, origin=SBBE, dest=GRU):
    return Flight(1, U(2026, 12, 1).date(), "AD4001", origin, dest,
                  U(2026, 12, 1, dep_h) if dep_h is not None else None,
                  U(2026, 12, 1, arr_h) if arr_h is not None else None, "", "", "E195")


def test_one_runway_closed_leaves_the_other_open():
    imp = find_impacts([flight(dep_h=11)], {"SBBE": [closure("RWY 06/24 CLSD", 10, 14)]})
    assert len(imp) == 1 and imp[0].open_runways == ["02/20"] and not imp[0].no_runway
    assert imp[0].severity == HIGH


def test_two_notams_closing_both_runways_make_the_flight_critical():
    rns = [closure("RWY 06/24 CLSD", 10, 14), closure("RWY 02/20 CLSD", 11, 13)]
    imp = find_impacts([flight(dep_h=12)], {"SBBE": rns})
    assert {i.rn.notam.e for i in imp} == {"RWY 06/24 CLSD", "RWY 02/20 CLSD"}
    assert all(i.no_runway and i.open_runways == [] and i.severity == CRITICAL for i in imp)


def test_closures_that_do_not_overlap_in_time_never_close_the_airport():
    rns = [closure("RWY 06/24 CLSD", 10, 12), closure("RWY 02/20 CLSD", 12, 14)]
    imp = find_impacts([flight(dep_h=12)], {"SBBE": rns})       # window 11-13 touches both, never at once
    assert imp and not any(i.no_runway for i in imp)


def test_landing_only_closure_does_not_affect_departures():
    rn = closure("RWY 06/24 CLSD FOR LDG", 10, 14)
    assert find_impacts([flight(dep_h=12)], {"SBBE": [rn]}) == []
    arrival = flight(arr_h=12, origin=GRU, dest=SBBE)
    assert len(find_impacts([arrival], {"SBBE": [rn]})) == 1


def test_instant_check_at_the_flight_time_only():
    """margin 0: only the flight time counts (closure start included, end excluded)."""
    from notam_schedule.domain.impact import leg_restrictions
    rns = [closure("RWY 06/24 CLSD", 10, 14), closure("RWY 02/20 CLSD", 11, 13)]
    at = lambda h, m=0: U(2026, 12, 1, h, m)
    hits, open_rwys, no_runway = leg_restrictions(rns, "ORIGIN", at(12), at(12))
    assert len(hits) == 2 and open_rwys == [] and no_runway
    hits, open_rwys, no_runway = leg_restrictions(rns, "ORIGIN", at(10, 30), at(10, 30))
    assert len(hits) == 1 and open_rwys == ["02/20"] and not no_runway
    assert leg_restrictions(rns, "ORIGIN", at(14), at(14))[0] == []          # closure ended at 14:00
    assert len(leg_restrictions(rns, "ORIGIN", at(11), at(11))[0]) == 2      # 02/20 closes at 11:00
