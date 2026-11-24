"""Command line."""
from notam_schedule.cli.analyze import main
from tests.helpers import EX, FIX


def test_cli(tmp_path, capsys):
    out = tmp_path / "r.xlsx"
    rc = main([str(EX / "schedule_period.csv"), "--notam-dir", str(FIX), "-o", str(out), "--csv",
               "--categories", "RUNWAY_CLOSED,LENGTH_REDUCTION"])
    assert rc == 0 and out.exists() and out.with_suffix(".csv").exists()
    assert "MCO" in capsys.readouterr().out
