"""Watched airports, NOTAM sync and team-wide read marks.

SQLite by default; to run the same tests on PostgreSQL: NOTAM_TEST_PG_URL=postgresql+psycopg://... pytest
"""
import os

from notam_schedule.storage.notam_db import NotamDB, meta
from tests.helpers import U

T0, T1 = U(2026, 11, 17, 12), U(2026, 11, 17, 19)


def make_db(tmp_path):
    url = os.getenv("NOTAM_TEST_PG_URL")
    if not url:
        return NotamDB(f"sqlite:///{tmp_path}/n.db")
    db = NotamDB(url)
    meta.drop_all(db.engine)                 # clean slate on the test server
    meta.create_all(db.engine)
    return db


def row(number, text="RWY 10/28 CLSD", runway=True, icao="SBSV"):
    return dict(key=f"{icao}|{number}", icao=icao, number=number, source="AISWEB", runway=runway, kind="Pista fechada",
                severity="HIGH", runways=["10/28"], valid_from="2026-11-17T00:00:00+00:00", valid_to=None,
                permanent=True, estimated=False, schedule="", text=text)


def test_sync_read_marks_and_replacement(tmp_path):
    db = make_db(tmp_path)
    assert db.seed_airports([("SBSV", "Salvador"), ("SBBE", "Belém")], T0) == 2
    assert db.seed_airports([("SBSV", "x")], T0) == 0                       # seeding never duplicates
    new, ended = db.sync_airport("SBSV", [row("B1/26"), row("B2/26", "TWY A CLSD", runway=False)], T0)
    assert sorted(new) == ["SBSV|B1/26", "SBSV|B2/26"] and ended == []
    b1 = next(r for r in db.notams() if r["number"] == "B1/26")
    assert b1["runways"] == ["10/28"] and b1["first_seen"] == T0 and b1["valid_from"] == U(2026, 11, 17)

    db.mark_read(b1["id"], "Ana", T0)
    assert db.counts() == {"all": 2, "read": 1, "unread": 1}
    assert [r["number"] for r in db.notams("read")] == ["B1/26"] and db.notams("read")[0]["read_by"] == "Ana"
    assert [r["number"] for r in db.notams("all", runway_only=True)] == ["B1/26"]

    # B3/26 replaces B1/26 (NOTAMR): new number -> unread; B1/26 leaves the active list, history kept
    new, ended = db.sync_airport("SBSV", [row("B3/26", "RWY 10/28 CLSD 1000-1200"), row("B2/26", "TWY A CLSD", runway=False)], T1)
    assert new == ["SBSV|B3/26"] and [e["number"] for e in ended] == ["B1/26"]
    assert {r["number"] for r in db.notams("unread")} == {"B2/26", "B3/26"} and db.notams("read") == []
    old = db.get(b1["id"])
    assert old["active"] is False and old["ended_at"] == T1 and old["read_by"] == "Ana"

    db.sync_error("SBSV", "AISWEB fora do ar", T1)                          # a failure never ends NOTAMs
    assert len(db.notams()) == 2 and db.airports()[1]["last_error"] == "AISWEB fora do ar"
    db.mark_unread(db.notams("all")[0]["id"])
    assert db.counts()["read"] == 0


def test_removed_airport_leaves_the_lists(tmp_path):
    db = make_db(tmp_path)
    db.seed_airports([("SBSV", ""), ("SBBE", "")], T0)
    db.sync_airport("SBBE", [row("A1/26", icao="SBBE")], T0)
    assert db.remove_airport("SBBE") and db.notams() == [] and db.counts()["all"] == 0
    assert [a["icao"] for a in db.airports()] == ["SBSV"] and len(db.airports(active_only=False)) == 2
    db.add_airport("SBBE", "Belém", T1)                                     # back, with its history
    assert [r["number"] for r in db.notams()] == ["A1/26"]
