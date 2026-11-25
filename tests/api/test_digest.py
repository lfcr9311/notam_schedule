"""Twice-a-day NOTAM digest: schedule, diff between digests, RSS for Power Automate, page and Excel (offline)."""
import io
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from notam_schedule.api.app import create_app
from notam_schedule.api.digest import DigestScheduler, alert_airports, build_digest, render_rss, slots_between
from notam_schedule.ingestion.aisweb import AiswebClient
from notam_schedule.ingestion.airports import AirportDB
from notam_schedule.ingestion.faa import FaaNmsClient
from notam_schedule.ingestion.routing import RoutedSource
from notam_schedule.storage.alerts import DigestStore
from notam_schedule.storage.notam_db import NotamDB
from tests.helpers import FIX, U

KEY = {"X-API-Key": "segredo"}
NOW = U(2026, 11, 17, 12, 0)       # 09:00 in Brasília


def make_db(tmp_path, *icaos):
    db = NotamDB(f"sqlite:///{tmp_path}/n.db")
    db.seed_airports([(i, "") for i in icaos], NOW)
    return db


@pytest.fixture()
def source():
    return RoutedSource(AiswebClient(None, offline_dir=FIX), FaaNmsClient(None, offline_dir=FIX / "faa"))


def test_schedule_is_09_and_16_brasilia(monkeypatch):
    monkeypatch.delenv("NOTAM_DIGEST_TIMES", raising=False)
    assert slots_between(U(2026, 11, 24), U(2026, 11, 25)) == [U(2026, 11, 24, 12), U(2026, 11, 24, 19)]


def test_airport_list_accepts_iata_and_ignores_junk(monkeypatch):
    monkeypatch.setenv("NOTAM_ALERT_AIRPORTS", "SSA; sbbe, SCEL, XX, SSA")
    assert alert_airports(AirportDB.default()) == ["SBSV", "SBBE", "SCEL"]


def test_digest_diff_and_rss(tmp_path, source):
    store, airports, db = DigestStore(tmp_path / "a.sqlite"), AirportDB.default(), make_db(tmp_path, "SBKP", "SCEL", "LFPG")
    first = store.get(build_digest(store, db, source, airports, NOW, "http://h/notam/", now=NOW))
    rows = first["data"]["rows"]
    assert {r["icao"] for r in rows} == {"SBKP", "SCEL"} and "LFPG" in first["data"]["errors"]   # no data, flagged
    assert not any(r["new"] for r in rows)                                    # first sync = baseline, not news
    assert {"SCEL|A4321/26", "SCEL|A4400/26"} <= {r["key"] for r in rows}    # runway + other NOTAMs
    assert "Sem dados de NOTAM" in first["html"] and "http://h/notam/web/resumos/1" in first["html"]

    db.add_airport("KMCO", "", NOW)                                          # new airport: its baseline, not news
    later = U(2026, 11, 17, 19)
    second = store.get(build_digest(store, db, source, airports, later, "http://h/notam/", now=later))
    assert not any(r["new"] for r in second["data"]["rows"])
    assert second["title"] == "NOTAMs 17/11 16:00 · 0 novos · 8 ativos em 4 aeroportos · 1 sem dados"

    feed = ET.fromstring(render_rss(store, "http://h/notam/"))
    items = feed.findall("channel/item")
    assert [i.findtext("link") for i in items] == ["http://h/notam/web/resumos/2", "http://h/notam/web/resumos/1"]
    assert items[0].findtext("pubDate").endswith("+0000")


class Hiding:
    """Source that hides some NOTAM numbers (as if they had not been issued yet)."""

    def __init__(self, inner, hidden):
        self.inner, self.hidden = inner, set(hidden)

    def covers(self, icao):
        return self.inner.covers(icao)

    def name_for(self, icao):
        return self.inner.name_for(icao)

    def notams(self, icao):
        return [n for n in self.inner.notams(icao) if n.number not in self.hidden]

    def runways(self, icao):
        return self.inner.runways(icao)


def test_new_and_gone_since_previous_sync(tmp_path, source):
    store, airports, db = DigestStore(tmp_path / "a.sqlite"), AirportDB.default(), make_db(tmp_path, "SCEL")
    build_digest(store, db, Hiding(source, {"A4400/26"}), airports, NOW, "", now=NOW)
    later = U(2026, 12, 16)                                  # A4321/26 ended on Dec 15; A4400/26 now published
    d = store.get(build_digest(store, db, source, airports, later, "", now=later))
    assert "SCEL|A4321/26" in {g["key"] for g in d["data"]["gone"]} and "Saíram" in d["html"]
    assert "SCEL|A4400/26" not in {r["key"] for r in d["data"]["rows"]}                    # ended on Nov 30


def test_new_notam_comes_in_unread_and_flagged(tmp_path, source):
    store, airports, db = DigestStore(tmp_path / "a.sqlite"), AirportDB.default(), make_db(tmp_path, "SCEL")
    build_digest(store, db, Hiding(source, {"A4400/26"}), airports, NOW, "", now=NOW)
    db.mark_read(db.notams("all")[0]["id"], "Ana", NOW)
    later = U(2026, 11, 17, 19)
    d = store.get(build_digest(store, db, source, airports, later, "", now=later))
    assert {r["key"] for r in d["data"]["rows"] if r["new"]} == {"SCEL|A4400/26"} and "NOVO" in d["html"]
    unread = {r["number"] for r in db.notams("unread")}
    assert "A4400/26" in unread and len(db.notams("read")) == 1          # the read mark survived the sync


def test_scheduler_builds_each_slot_once(tmp_path, source):
    store, db = DigestStore(tmp_path / "a.sqlite"), make_db(tmp_path, "SCEL")
    sched = DigestScheduler(store, db, source, AirportDB.default(), "")
    assert sched.due(U(2026, 11, 17, 11, 59)) is None                      # 16:00 of the day before is > 3 h ago
    slot = sched.due(U(2026, 11, 17, 12, 1))
    assert slot == NOW
    build_digest(store, db, source, AirportDB.default(), slot, "", now=U(2026, 11, 17, 12, 1))
    assert sched.due(U(2026, 11, 17, 12, 30)) is None
    assert sched.due(U(2026, 11, 17, 21)) == U(2026, 11, 17, 19)          # caught up after a restart
    assert sched.due(U(2026, 11, 18, 0)) is None                           # too late (> 3 h): wait for the next


def test_endpoints(tmp_path, monkeypatch, source):
    monkeypatch.setenv("NOTAM_ALERT_AIRPORTS", "SBKP,SCEL")
    monkeypatch.delenv("NOTAM_PUBLIC_URL", raising=False)
    c = TestClient(create_app(source, "offline", api_key="segredo", alerts_db=tmp_path / "a.sqlite",
                              notam_db=NotamDB(f"sqlite:///{tmp_path}/n.db")))
    assert c.post("/api/resumos").status_code == 401
    info = c.post("/api/resumos", headers=KEY).json()
    assert info["page"].endswith("/web/resumos/1") and info["feed"].endswith("/web/alertas.rss")
    rss = c.get("/web/alertas.rss")
    assert rss.headers["content-type"].startswith("application/rss+xml") and len(ET.fromstring(rss.text).findall("channel/item")) == 1
    assert "Baixar em Excel" in c.get("/web/resumos/1").text
    wb = load_workbook(io.BytesIO(c.get("/web/resumos/1.xlsx").content))
    assert wb["NOTAMs"]["D1"].value == "NOTAM" and wb["NOTAMs"].max_row > 2
    assert c.get("/web/resumos/9").status_code == 404 and c.get("/web/resumos/1.pdf").status_code == 404


def test_big_digest_keeps_runway_notams_and_links_the_rest(tmp_path, monkeypatch, source):
    store, db = DigestStore(tmp_path / "a.sqlite"), make_db(tmp_path, "SCEL")
    monkeypatch.setenv("NOTAM_DIGEST_MAX_KB", "0")
    d = store.get(build_digest(store, db, source, AirportDB.default(), NOW, "", now=NOW))
    assert "A4321/26" in d["html"] and "A4400/26" not in d["html"] and "grande demais" in d["html"]
    assert len(d["data"]["rows"]) == 3                                   # the page/Excel still have everything (ILS on Dec 20)
