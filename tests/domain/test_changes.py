"""Comparison between two analysis runs."""
from datetime import date

from notam_schedule.domain.changes import ENDED, NEW, SEVERITY_CHANGED, WINDOW_CHANGED, ImpactRecord, diff_impacts


def rec(flight="AD4001", day="2026-12-10", notam="C1/26", sev="HIGH", ws="2026-12-10T09:00:00+00:00"):
    return ImpactRecord(flight, day, "SBKP", "SBRJ", "ORIGIN", "SBKP", notam, "RUNWAY_CLOSED", sev, ws,
                        "2026-12-10T12:00:00+00:00")


def test_diff_new_ended_and_changed():
    prev = [rec(), rec(flight="AD4002"), rec(flight="AD4003"), rec(flight="AD4004", day="2026-11-01")]
    cur = [rec(sev="CRITICAL"), rec(flight="AD4002", ws="2026-12-10T08:00:00+00:00"), rec(flight="AD4005")]
    cs = diff_impacts(prev, cur, (date(2026, 12, 1), date(2026, 12, 31)), {"id": 1})
    kinds = {(c.kind, c.record.flight) for c in cs.changes}
    assert kinds == {(SEVERITY_CHANGED, "AD4001"), (WINDOW_CHANGED, "AD4002"), (ENDED, "AD4003"), (NEW, "AD4005")}
    # AD4004 (November) is outside the current schedule period: not reported as ended
    assert cs.count(ENDED) == 1
