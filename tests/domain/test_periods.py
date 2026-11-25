"""Item D expansion into UTC activity intervals."""
from datetime import datetime
from notam_schedule.domain.periods import expand_periods
from tests.helpers import UTC, U, W0, W1, active_days, mk


def test_d_daily():
    iv, ok, _ = expand_periods(mk(d="DLY 0300-0800", b="2611150000", c="2611170000"), W0, W1)
    assert ok and iv == [(U(2026, 11, 15, 3), U(2026, 11, 15, 8)), (U(2026, 11, 16, 3), U(2026, 11, 16, 8))]

def test_d_crossing_midnight():
    iv, ok, _ = expand_periods(mk(d="2300-0500", b="2611150000", c="2611162359"), W0, W1)
    assert ok and (U(2026, 11, 15, 23), U(2026, 11, 16, 5)) in iv
    assert iv[0] == (U(2026, 11, 15, 0), U(2026, 11, 15, 5))  # tail of the previous night, clipped at B

def test_d_weekdays_pt_and_en():
    for d in ("MON TIL FRI 0900-1100", "SEG A SEX 0900-1100", "MON-FRI 0900-1100"):
        iv, ok, _ = expand_periods(mk(d=d), W0, W1)
        assert ok, d
        assert {s.weekday() for s, _ in iv} == {0, 1, 2, 3, 4}

def test_d_dates_and_months():
    iv, ok, _ = expand_periods(mk(d="NOV 17 18 0100-0500 NOV 20 0200-0300"), W0, W1)
    assert ok
    assert iv == [(U(2026, 11, 17, 1), U(2026, 11, 17, 5)), (U(2026, 11, 18, 1), U(2026, 11, 18, 5)),
                  (U(2026, 11, 20, 2), U(2026, 11, 20, 3))]

def test_d_date_range_and_except():
    iv, ok, _ = expand_periods(mk(d="16 TIL 18 1000-1200"), W0, W1)
    assert ok and len(iv) == 3
    iv, ok, _ = expand_periods(mk(d="DLY EXC SAT SUN 1000-1200"), W0, W1)
    assert ok and all(s.weekday() < 5 for s, _ in iv)

def test_d_unknown_falls_back_to_full_period():
    n = mk(d="CONFORME COORDENACAO PREVIA", b="2611150000", c="2611160000")
    iv, ok, note = expand_periods(n, W0, W1)
    assert not ok and iv == [(U(2026, 11, 15), U(2026, 11, 16))] and "não interpretado" in note

def test_d_cross_month_range():
    iv = active_days("NOV 16-DEC 06 09 0430-0730", datetime(2026, 11, 16, 4, 30, tzinfo=UTC), datetime(2026, 12, 9, 7, 30, tzinfo=UTC))
    assert [s.day for s, _ in iv] == [1, 2, 3, 4, 5, 6, 9]

def test_d_dates_filtered_by_weekday():
    iv = active_days("NOV 09-DEC 29 MON TUE 0500-0759", datetime(2026, 11, 9, 5, tzinfo=UTC), datetime(2026, 12, 29, 7, 59, tzinfo=UTC))
    assert {s.weekday() for s, _ in iv} == {0, 1} and len(iv) == 9

def test_d_weekday_group_inherits_date_range():
    d = ("SEP 15-NOV 30 MON THU FRI 0750-1245 TUE WED 0750-1330 DEC 01-10 MON-FRI 0750-1245")
    iv = active_days(d, datetime(2026, 9, 15, 7, 50, tzinfo=UTC), datetime(2026, 12, 10, 12, 45, tzinfo=UTC))
    assert iv[-1][0].day == 10 and all(e.hour == 12 and e.minute == 45 for _, e in iv)

def test_perm_clipped_to_window():
    iv, ok, _ = expand_periods(mk(c="PERM", b="2601010000"), W0, W1)
    assert iv == [(W0, W1)]


def test_btn_between_is_ignored_in_item_d():
    assert active_days("DLY BTN 1100-2300", U(2026, 12, 1), U(2026, 12, 3, 23, 59)) == active_days(
        "DLY 1100-2300", U(2026, 12, 1), U(2026, 12, 3, 23, 59))
    days = active_days("25 BTN 1800-2000 27 BTN 1100-1300", U(2026, 11, 20), U(2026, 11, 30), month=11)
    assert days == [(U(2026, 11, 25, 18), U(2026, 11, 25, 20)), (U(2026, 11, 27, 11), U(2026, 11, 27, 13))]
