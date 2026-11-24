"""Severity and impact rules."""
from notam_schedule.detection.rules import classify
from notam_schedule.domain.impact import CRITICAL
from tests.helpers import real_notam


def test_all_runways_closed_is_critical():
    from notam_schedule.domain.airport import Runway
    from notam_schedule.domain.impact import RunwayNotam
    n = real_notam("RWY 11/29 E RWY 15/33 CLSD DEVIDO SER MAINT")
    rws = [Runway("11/29", 1798, 45), Runway("15/33", 1500, 45)]
    assert RunwayNotam(n, classify(n), [], True, "", rws[0], 2, airport_runways=rws).severity == CRITICAL
    n2 = real_notam("RWY 11/29 CLSD")
    assert RunwayNotam(n2, classify(n2), [], True, "", rws[0], 2, airport_runways=rws).severity == "HIGH"


def test_brazilian_prefixes_exclude_neighbours():
    from notam_schedule.domain.airport import Airport
    assert Airport("VCP", "SBKP", "", "America/Sao_Paulo").is_brazilian
    assert not Airport("AGT", "SGES", "", "America/Asuncion").is_brazilian   # Paraguay
    assert not Airport("MVD", "SUMU", "", "America/Montevideo").is_brazilian
