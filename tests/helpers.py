"""Shared test helpers: fixture paths and NOTAM factories."""
from datetime import datetime, timezone
from pathlib import Path

from notam_schedule.domain.notam import Notam, parse_dtg
from notam_schedule.domain.periods import expand_periods

UTC = timezone.utc
TESTS = Path(__file__).parent
FIX = TESTS / "fixtures"
REAL = FIX / "real"
EX = TESTS.parent / "examples"


def mk(e="", d="", b="2611150000", c="2611302359", n="C1/26", ntype="N", ref=""):
    start, _, _ = parse_dtg(b)
    end, perm, est = parse_dtg(c)
    return Notam(n, "SBKP", ntype, ref, start, end, perm, est, d, e)


def U(*a):
    return datetime(*a, tzinfo=UTC)


W0, W1 = U(2026, 11, 14), U(2026, 11, 25)


def real_notam(e="", d="", b=None, c=None):
    return Notam("X", "SBXX", "NOTAMN", "", b, c, False, False, d, e, {})


def active_days(d, b, c, month=12):
    iv, ok, _ = expand_periods(real_notam(d=d, b=b, c=c), datetime(2026, month, 1, tzinfo=UTC),
                               datetime(2027, 1, 1, tzinfo=UTC))
    assert ok, d
    return iv
