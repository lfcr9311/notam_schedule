"""Web page and its public /web/* endpoints, against offline AISWEB fixtures (no network)."""
import pytest
from fastapi.testclient import TestClient

from notam_schedule.api.app import create_app
from notam_schedule.ingestion.aisweb import AiswebClient
from tests.helpers import EX, FIX


@pytest.fixture()
def client(tmp_path):
    app = create_app(AiswebClient(None, offline_dir=FIX), "offline", api_key="segredo",
                     runs_db=tmp_path / "runs.sqlite", reports_dir=tmp_path / "reports")
    return TestClient(app)


def upload(client, path=EX / "schedule_dated.csv", **params):
    return client.post("/web/schedule", params=params, content=path.read_bytes(),
                       headers={"x-filename": path.name, "content-type": "application/octet-stream"})


def test_page_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Identificador de NOTAM" in r.text


def test_web_notams_need_no_key_and_bring_the_time_zone(client):
    r = client.get("/web/notams", params={"icao": "SBKP", "from": "2026-11-17T00:00:00Z", "to": "2026-11-19T00:00:00Z"})
    assert r.status_code == 200
    [a] = r.json()["airports"]
    assert (a["status"], a["timezone"], [n["number"] for n in a["notams"]]) == ("ok", "America/Sao_Paulo", ["C3004/26"])
    assert client.get("/api/notams", params={"icao": "SBKP"}).status_code == 401      # the integration API keeps the key


def test_web_check_one_moment(client):
    leg = {"airport": "SBKP", "operation": "departure"}
    closed = client.post("/web/check", json={"legs": [{**leg, "time": "2026-11-17T02:00:00Z"}], "margin_minutes": 0})
    clear = client.post("/web/check", json={"legs": [{**leg, "time": "2026-11-17T12:00:00Z"}], "margin_minutes": 0})
    assert closed.json()["legs"][0]["no_runway"] is True
    free = clear.json()["legs"][0]
    assert free["restricted"] is False and free["open_runways"] == ["15/33"]       # all runways when nothing is closed
    many = {"legs": [{**leg, "time": "2026-11-17T02:00:00Z"}] * 21}
    assert client.post("/web/check", json=many).status_code == 422


def test_schedule_upload_reports_and_changes(client):
    r = upload(client, categories="all")                                       # every runway category
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["flights"] > 0 and first["impacted_flights"] > 0 and first["impacts"]
    assert first["changes"]["previous"] is None
    assert client.get("/" + first["downloads"]["xlsx"]).status_code == 200
    assert client.get("/" + first["downloads"]["csv"]).status_code == 200

    second = upload(client).json()                                             # closures only: fewer impacts
    assert second["changes"]["previous"]["id"] == first["run_id"]
    assert second["changes"]["counts"]["ended"] > 0
    ch = client.get("/web/changes").json()
    assert ch["run"]["id"] == second["run_id"] and [x["id"] for x in ch["runs"]][:2] == [second["run_id"], first["run_id"]]
    assert client.get("/web/changes", params={"run_id": first["run_id"]}).json()["previous"] is None
    assert client.get("/web/changes", params={"run_id": 999}).status_code == 404


def test_schedule_upload_validation(client, tmp_path):
    bad = tmp_path / "malha.pdf"
    bad.write_bytes(b"%PDF")
    assert upload(client, bad).status_code == 400
    empty = tmp_path / "vazia.csv"
    empty.write_bytes(b"")
    assert upload(client, empty).status_code == 400
    assert client.get("/web/reports/1.exe").status_code == 404
    assert client.get("/web/changes").json()["run"] is None                    # nothing recorded yet
