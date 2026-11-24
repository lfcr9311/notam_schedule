"""NOTAM entity: date-time groups and replacement lineage."""
from notam_schedule.domain.notam import drop_replaced, parse_dtg
from tests.helpers import U, mk


def test_parse_dtg():
    assert parse_dtg("2611150300")[0] == U(2026, 11, 15, 3, 0)
    assert parse_dtg("PERM") == (None, True, False)
    dt, perm, est = parse_dtg("2611302359EST")
    assert dt == U(2026, 11, 30, 23, 59) and est and not perm

def test_drop_replaced():
    a = mk(n="C1/26")
    b = mk(n="C2/26", ntype="R", ref="C1/26")
    c = mk(n="C3/26", ntype="C", ref="C9/26")
    assert [n.number for n in drop_replaced([a, b, c])] == ["C2/26"]
