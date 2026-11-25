"""HTTP API against offline AISWEB fixtures (no network)."""
import json

import pytest
from fastapi.testclient import TestClient

from notam_schedule.api.__main__ import CONTRACT, openapi_document
from notam_schedule.api.app import create_app
from notam_schedule.ingestion.aisweb import AiswebClient
from tests.helpers import FIX

KEY = {"X-API-Key": "segredo"}
WINDOW = {"from": "2026-11-17T00:00:00Z", "to": "2026-11-19T00:00:00Z"}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("api")
    return TestClient(create_app(AiswebClient(None, offline_dir=FIX), "offline", api_key="segredo",
                                 alerts_db=tmp / "alerts.sqlite"))


def test_health_needs_no_key(client):
    assert client.get("/api/health").json() == {"status": "ok", "version": "0.1.0", "source": "offline", "international": False}


def test_key_is_required(client):
    assert client.get("/api/notams", params={"icao": "SBKP"}).status_code == 401
    assert client.get("/api/notams", params={"icao": "SBKP"}, headers={"X-API-Key": "errada"}).status_code == 401


def test_notams_per_airport_with_status(client):
    r = client.get("/api/notams", params={"icao": "SBKP,KMCO,SBXX", **WINDOW}, headers=KEY)
    assert r.status_code == 200
    body = r.json()
    assert body["categories"] == ["AERODROME_CLOSED", "RUNWAY_CLOSED"]
    by = {a["icao"]: a for a in body["airports"]}
    assert by["KMCO"]["status"] == "out_of_coverage"
    assert by["SBXX"]["status"] == "error" and by["SBXX"]["error"]          # no data, never "no restriction"
    sbkp = by["SBKP"]
    assert sbkp["status"] == "ok" and [rw["ident"] for rw in sbkp["runways"]] == ["15/33"]
    [n] = sbkp["notams"]
    assert (n["id"], n["category"], n["runways"], n["severity"]) == ("SBKP|C3004/26", "RUNWAY_CLOSED", ["15/33"], "CRITICAL")
    assert n["closure"] == {"type": "total", "operations": "both", "exception": None}
    assert [p["start"] for p in n["active_periods"]] == ["2026-11-17T01:00:00Z", "2026-11-18T01:00:00Z"]
    assert n["confidence"]["level"] in ("ALTA", "MÉDIA", "BAIXA") and n["confidence"]["reasons"]


def test_notams_validation(client):
    assert client.get("/api/notams", params={"icao": "SB1"}, headers=KEY).status_code == 422
    assert client.get("/api/notams", params={"icao": "SBKP", "categories": "XYZ"}, headers=KEY).status_code == 422
    too_long = {"from": "2026-01-01T00:00:00Z", "to": "2026-06-01T00:00:00Z"}
    assert client.get("/api/notams", params={"icao": "SBKP", **too_long}, headers=KEY).status_code == 422
    everything = client.get("/api/notams", params={"icao": "SBKP", "categories": "all", **WINDOW}, headers=KEY)
    assert everything.json()["categories"] == ["all"]


def test_runway_check_batch(client):
    legs = [
        {"id": "AZU4001-DEP", "airport": "SBKP", "operation": "departure", "time": "2026-11-17T02:00:00Z"},
        {"id": "AZU4003-DEP", "airport": "sbkp", "operation": "departure", "time": "2026-11-17T12:00:00Z"},
        {"id": "AZU8707-ARR", "airport": "KMCO", "operation": "arrival", "time": "2026-11-17T08:55:00Z"},
    ]
    r = client.post("/api/runway-check", json={"legs": legs, "margin_minutes": 60}, headers=KEY)
    assert r.status_code == 200
    closed, clear, abroad = r.json()["legs"]
    assert closed["restricted"] and closed["no_runway"] and closed["open_runways"] == []
    assert closed["severity"] == "CRITICAL" and closed["notams"][0]["number"] == "C3004/26"
    assert closed["notams"][0]["confidence"]["reasons"][-1] == "horário do voo dentro do período ativo"
    assert clear["airport"] == "SBKP" and clear["status"] == "ok" and not clear["restricted"] and clear["notams"] == []
    assert abroad["status"] == "out_of_coverage" and not abroad["restricted"]


def test_runway_check_validation(client):
    assert client.post("/api/runway-check", json={"legs": []}, headers=KEY).status_code == 422
    bad = {"legs": [{"airport": "SBKP", "operation": "taxi", "time": "2026-11-17T02:00:00Z"}]}
    assert client.post("/api/runway-check", json=bad, headers=KEY).status_code == 422


def test_contract_file_matches_the_code():
    """contracts/notam-api.openapi.json is the published contract. If this fails, the API changed:
    confirm the change is intended and run `notam-schedule-api --export-openapi`."""
    assert json.loads(CONTRACT.read_text(encoding="utf-8")) == openapi_document()


def test_international_airports_come_from_the_faa(tmp_path):
    from notam_schedule.ingestion.faa import FaaNmsClient
    from notam_schedule.ingestion.routing import RoutedSource
    src = RoutedSource(AiswebClient(None, offline_dir=FIX), FaaNmsClient(None, offline_dir=FIX / "faa"))
    c = TestClient(create_app(src, "offline", api_key="segredo", alerts_db=tmp_path / "alerts.sqlite"))
    assert c.get("/api/health").json()["international"] is False    # offline FAA does not cover every airport
    r = c.get("/api/notams", headers=KEY, params={"icao": "SBKP,SCEL,KMCO,LFPG", **WINDOW})
    by = {a["icao"]: a for a in r.json()["airports"]}
    assert (by["SBKP"]["source"], by["SCEL"]["source"], by["KMCO"]["source"]) == ("AISWEB", "FAA", "FAA")
    assert [n["number"] for n in by["SCEL"]["notams"]] == ["A4321/26"]           # A4000/26 was replaced
    assert by["SCEL"]["notams"][0]["closure"]["type"] == "total" and by["SCEL"]["other_notams"] == []
    assert [n["number"] for n in by["KMCO"]["notams"]] == ["11/045"]
    assert by["LFPG"]["status"] == "out_of_coverage"

    every = c.get("/api/notams", headers=KEY, params={"icao": "SCEL,KMCO", "categories": "all",
                                                       "include_other": "true", **WINDOW}).json()
    other = {a["icao"]: [n["number"] for n in a["other_notams"]] for a in every["airports"]}
    assert other == {"KMCO": ["11/050"], "SCEL": ["A4400/26"]}                   # ILS (Dec 20) is outside the window

    leg = {"airport": "KMCO", "operation": "arrival", "time": "2026-11-17T03:00:00Z"}
    res = c.post("/api/runway-check", headers=KEY, json={"legs": [leg], "margin_minutes": 0}).json()["legs"][0]
    assert res["status"] == "ok" and res["restricted"] and [n["number"] for n in res["notams"]] == ["11/045"]
