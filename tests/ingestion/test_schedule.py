"""Schedule files: dated, period/SSIM, time basis."""
from datetime import date, datetime, time
from notam_schedule.ingestion.airports import AirportDB
from notam_schedule.ingestion.schedule import load_schedule, parse_frequency, read_schedule
from tests.helpers import EX, U


def test_frequency():
    assert parse_frequency("1234567") == set(range(7))
    assert parse_frequency("1.3.5..") == {0, 2, 4}
    assert parse_frequency(".2.4567") == {1, 3, 4, 5, 6}
    assert parse_frequency("67") == {5, 6}

def test_read_dated_local_to_utc(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text("data;voo;origem;destino;std;sta\n15/11/2026;AD1;VCP;MAO;23:00;01:30\n", encoding="utf-8")
    [f] = read_schedule(p, AirportDB())
    assert f.std_utc == U(2026, 11, 16, 2, 0)          # -03 (Portuguese headers still accepted)
    assert f.sta_utc == U(2026, 11, 16, 5, 30)         # next-day arrival in Manaus (-04)

def test_read_periodic():
    flights = read_schedule(EX / "schedule_period.csv", AirportDB(), date_from=date(2026, 11, 16),
                            date_to=date(2026, 11, 17))
    assert sorted({f.flight for f in flights}) == ["AD4001", "AD4005"]
    assert len(flights) == 3  # AD4001 x2, AD4005 Monday 16

def test_read_ssim_xlsx_with_utc_autodetect(tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Flt Desg", "Eff Date", "Dis Date", "Freq", "Dept Arp", "Dept Time", "Arvl Arp", "Arrv Time",
               "Subfleet", "Block Time"])
    for i in range(12):
        ws.append([f"AD  {2000 + i}", datetime(2026, 12, 1), datetime(2026, 12, 7), ".2.4567", "VCP", time(0, 0),
                   "MAO", time(3, 40), "AD  32X", time(3, 40)])
    ws.append(["AD  8707", datetime(2026, 12, 1), datetime(2026, 12, 1), 1234567, "JFK", time(0, 0), "VCP",
               time(8, 55), "AD  339", time(8, 55)])
    p = tmp_path / "m.xlsx"
    wb.save(p)
    sched = load_schedule(p, AirportDB(), local_times=None)
    assert sched.time_basis == "utc (auto)"
    f = sched.flights[0]
    assert f.flight == "AD2000" and f.aircraft == "AD 32X"
    assert f.std_utc == U(2026, 12, 1, 0, 0) and f.sta_utc == U(2026, 12, 1, 3, 40)
    unknown = [x for x in sched.flights if x.origin.iata == "JFK"]
    assert unknown and unknown[0].origin.icao == ""   # unknown IATA does not abort the read
