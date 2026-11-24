"""Schedule x NOTAM analysis end to end (offline fixtures)."""
from notam_schedule.domain.impact import CRITICAL
from notam_schedule.ingestion.airports import AirportDB
from notam_schedule.ingestion.aisweb import AiswebClient
from notam_schedule.ingestion.schedule import read_schedule
from notam_schedule.pipelines.analyze import analyze
from tests.helpers import EX, FIX


def test_end_to_end():
    flights = read_schedule(EX / "schedule_dated.csv", AirportDB())
    res = analyze(flights, AiswebClient(None, offline_dir=FIX))
    kp = res.runway_notams["SBKP"]
    assert {n.notam.number for n in kp} == {"C3001/26", "C3004/26"}
    red = next(n for n in kp if n.notam.number == "C3001/26")
    assert red.duplicates == ["A2001/26"] and red.reduction_m == 1040 and red.reduction_pct == 32.1
    crit = [i for i in res.impacts if i.rn.severity == CRITICAL]
    assert [(i.flight.flight, i.side, i.airport) for i in crit] == [("AD4101", "DESTINATION", "SBKP")]
    # VCP 06:00 local = 09:00 UTC: outside the DLY 0300-0800 NOTAM
    assert not any(i.airport == "SBKP" and i.flight.flight == "AD4001" for i in res.impacts)


def test_history_records_runs_and_reports_changes(tmp_path):
    from notam_schedule.pipelines.history import record_run
    from notam_schedule.storage.runs import RunStore
    flights = read_schedule(EX / "schedule_dated.csv", AirportDB())
    res = analyze(flights, AiswebClient(None, offline_dir=FIX))
    store = RunStore(tmp_path / "runs.sqlite")
    first = record_run(store, res, EX / "schedule_dated.csv", "rules", {})
    assert first.previous_run is None and first.changes == []
    res.impacts = res.impacts[1:]                      # one impact disappears in the next run
    second = record_run(store, res, EX / "schedule_dated.csv", "rules", {})
    assert second.previous_run["id"] == 1 and [c.kind for c in second.changes] == ["ENCERRADO"]
