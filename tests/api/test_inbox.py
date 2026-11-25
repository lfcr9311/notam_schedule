"""Page endpoints of the NOTAM inbox: watched airports, sync, unread / read / all (offline sources)."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from notam_schedule.api.app import create_app
from notam_schedule.ingestion.aisweb import AiswebClient
from notam_schedule.ingestion.faa import FaaNmsClient
from notam_schedule.ingestion.routing import RoutedSource
from notam_schedule.storage.notam_db import NotamDB
from tests.helpers import FIX


@pytest.fixture()
def client(tmp_path, monkeypatch):
    src = RoutedSource(AiswebClient(None, offline_dir=FIX), FaaNmsClient(None, offline_dir=FIX / "faa"))
    return TestClient(create_app(src, "offline", api_key="segredo", alerts_db=tmp_path / "a.sqlite",
                                 notam_db=watching(tmp_path, "SBKP", "SCEL")))


def watching(tmp_path, *icaos):
    db = NotamDB(f"sqlite:///{tmp_path}/n.db")
    db.seed_airports([(i, "") for i in icaos], datetime.now(timezone.utc))
    return db


def test_inbox_flow(client):
    assert [a["icao"] for a in client.get("/web/airports").json()] == ["SBKP", "SCEL"]
    assert client.get("/web/inbox").json()["notams"] == []                                  # nothing synced yet
    sync = client.post("/web/inbox/sync").json()
    assert sync["airports"] == 2 and sync["notams"] > 0 and sync["errors"] == {}
    assert client.post("/web/inbox/sync").status_code == 429                               # public button: throttled

    box = client.get("/web/inbox").json()
    assert box["state"] == "unread" and box["counts"]["read"] == 0 and box["counts"]["unread"] == len(box["notams"])
    order = [(n["icao"], not n["runway"]) for n in box["notams"]]
    assert order == sorted(order)                                       # per airport, runway restrictions first
    first = box["notams"][0]
    marked = client.put(f"/web/inbox/{first['id']}/read", json={"by": "Ana"}).json()
    assert marked["read_by"] == "Ana" and marked["read_at"]
    assert [n["id"] for n in client.get("/web/inbox", params={"state": "read"}).json()["notams"]] == [first["id"]]
    unread = client.get("/web/inbox").json()
    assert first["id"] not in [n["id"] for n in unread["notams"]] and unread["counts"]["read"] == 1
    scel = client.get("/web/inbox", params={"state": "all", "icao": "scel"}).json()
    assert {n["icao"] for n in scel["notams"]} == {"SCEL"}
    client.delete(f"/web/inbox/{first['id']}/read")
    assert client.get("/web/inbox", params={"state": "read"}).json()["notams"] == []
    assert client.put("/web/inbox/999/read", json={"by": "Ana"}).status_code == 404
    assert client.put(f"/web/inbox/{first['id']}/read", json={"by": ""}).status_code == 422


def test_manage_airports(client):
    assert [a["icao"] for a in client.post("/web/airports", json={"icao": "ssa"}).json()] == ["SBKP", "SBSV", "SCEL"]
    assert client.post("/web/airports", json={"icao": "12"}).status_code == 422
    assert [a["icao"] for a in client.delete("/web/airports/SBKP").json()] == ["SBSV", "SCEL"]
    assert client.delete("/web/airports/XXXX").status_code == 404


def test_scopes_split_brazil_and_international(client):
    client.post("/web/inbox/sync")
    br = client.get("/web/inbox", params={"state": "all", "scope": "br"}).json()
    intl = client.get("/web/inbox", params={"state": "all", "scope": "intl"}).json()
    assert [a["icao"] for a in br["airports"]] == ["SBKP"] and [a["icao"] for a in intl["airports"]] == ["SCEL"]
    assert {n["icao"] for n in br["notams"]} == {"SBKP"} and {n["icao"] for n in intl["notams"]} == {"SCEL"}
    both = client.get("/web/inbox", params={"state": "all"}).json()
    assert br["counts"]["all"] + intl["counts"]["all"] == both["counts"]["all"]

    assert [a["icao"] for a in client.post("/web/airports", json={"icao": "kmia", "scope": "intl"}).json()] == ["KMIA", "SCEL"]
    refused = client.post("/web/airports", json={"icao": "SBSV", "scope": "intl"})
    assert refused.status_code == 422 and "NOTAMs Brasil" in refused.json()["detail"]
    assert client.post("/web/airports", json={"icao": "KMIA", "scope": "br"}).status_code == 422
    assert [a["icao"] for a in client.delete("/web/airports/KMIA", params={"scope": "intl"}).json()] == ["SCEL"]
