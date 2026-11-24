"""Confidence degree of runway NOTAMs and flight impacts."""
from datetime import timedelta

from notam_schedule.detection.rules import classify
from notam_schedule.domain.airport import Runway
from notam_schedule.domain.categories import Classification
from notam_schedule.domain.confidence import HIGH, LOW, MEDIUM, impact_confidence, notam_confidence
from notam_schedule.domain.impact import Impact, RunwayNotam
from tests.helpers import U, real_notam

RWYS = [Runway("15/33", 3000, 45)]


def rn_for(e, cod="", d_parsed=True, cls=None, note=""):
    n = real_notam(e)
    n.raw["cod"] = cod
    return RunwayNotam(n, cls or classify(n), [(U(2026, 12, 1, 10), U(2026, 12, 1, 14))], d_parsed, note,
                       RWYS[0], 1, airport_runways=RWYS)


def test_text_and_qcode_agree_is_high():
    c = notam_confidence(rn_for("RWY 15/33 CLSD", cod="QMRLC"))
    assert c.score == 100 and c.level == HIGH and "concordam" in c.explanation


def test_doubts_lower_the_score_with_reasons():
    no_q = notam_confidence(rn_for("RWY 15/33 CLSD"))
    assert no_q.score == 90 and no_q.level == HIGH and "sem código Q" in no_q.explanation
    disagree = notam_confidence(rn_for("RWY 15/33 CLSD", cod="QMXLC"))       # Q-code: taxiway
    assert disagree.level == MEDIUM
    worst = notam_confidence(rn_for("PISTA 15 SEM OPERACAO", d_parsed=False,
                                    cls=Classification(["RUNWAY_CLOSED"], source="ml", prob=0.71)))
    assert worst.level == LOW and "modelo de ML" in worst.explanation and "item D" in worst.explanation


def test_flight_inside_window_vs_margin_only():
    rn = rn_for("RWY 15/33 CLSD", cod="QMRLC")
    inside = Impact(None, "ORIGIN", "SBKP", U(2026, 12, 1, 11), rn, rn.intervals)
    margin = Impact(None, "ORIGIN", "SBKP", U(2026, 12, 1, 14) + timedelta(minutes=40), rn, rn.intervals)
    assert impact_confidence(inside).score == 100
    assert impact_confidence(margin).score == 90 and "margem" in impact_confidence(margin).explanation
